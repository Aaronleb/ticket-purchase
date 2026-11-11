#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import queue
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from android_config import AndroidConfigStore
from android_environment import AndroidEnvironment
from android_tickets import capture_android_layout, layout_steps, prepare_android_tickets
from config_store import ConfigError, ConfigStore, default_project_root
from damai_appium.layout_cache import LAYOUT_CACHE_NAME, LayoutCacheError
from damai_appium.ticket_selection import TicketSelectionError
from process_manager import ProcessManager, TaskBusy, TaskError, TaskNotRunning


APP_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = APP_ROOT / "static"
RUNTIME_ROOT = APP_ROOT / ".runtime"


class WebApplication:
    def __init__(
        self,
        project_root: Path | str,
        android_only: bool = False,
        android_config_path: Path | str | None = None,
        android_command: list[str] | None = None,
        managed_server_url: str | None = None,
        runtime_status=None,
    ):
        self.store = ConfigStore(project_root)
        self.android_store = AndroidConfigStore(
            project_root,
            config_path=android_config_path,
            managed_server_url=managed_server_url,
        )
        self.android_environment = AndroidEnvironment()
        self.manager = ProcessManager()
        self.android_only = android_only
        self.android_command = android_command
        self.runtime_status = runtime_status or (lambda: {"state": "external"})
        self.login_signal = RUNTIME_ROOT / "login.signal"

    def status(self) -> dict:
        status = self.manager.snapshot()
        status.update(
            {
                "logged_in": self.store.cookie_path.is_file(),
                "project_root": str(self.store.project_root),
                "enabled_platforms": ["android"] if self.android_only else ["pc", "android"],
                "managed_appium": bool(self.android_store.managed_server_url),
                "runtime": self.runtime_status(),
            }
        )
        return status

    def require_pc(self) -> None:
        if self.android_only:
            raise ConfigError("此应用仅包含安卓抢票功能")

    def start_check(self):
        self.require_pc()
        self.store.ensure_project()
        return self.manager.start(
            "check", [sys.executable, "check_environment.py"], self.store.damai_dir
        )

    def start_login(self):
        self.require_pc()
        self.store.ensure_project()
        config = self.store.load()
        self.login_signal.unlink(missing_ok=True)
        command = [
            sys.executable,
            str(self.store.project_root / "tools" / "account_login.py"),
            "--cookie",
            str(self.store.cookie_path),
            "--signal",
            str(self.login_signal),
            "--url",
            config["index_url"],
        ]
        driver = self.store.damai_dir / "chromedriver"
        if driver.exists():
            command.extend(["--driver", str(driver)])
        return self.manager.start("login", command, self.store.project_root)

    def start_grab(self, payload):
        self.require_pc()
        self.store.save(payload)
        return self.manager.start("grab", [sys.executable, "damai.py"], self.store.damai_dir)

    def check_android(self):
        return self.android_environment.check(self.android_store.load())

    def _check_android_config(self, config):
        report = self.android_environment.check(config)
        if not report["ok"]:
            failures = "；".join(
                f"{check['label']}：{check['message']}"
                for check in report["checks"]
                if not check["ok"]
            )
            raise ConfigError(f"安卓环境检查未通过：{failures}")

    def list_android_devices(self):
        return self.android_environment.list_devices()

    def prepare_android(self, payload):
        if self.manager.snapshot()["running"]:
            raise TaskBusy("抢票任务运行中，请先停止后再识别票档")
        config = self.android_store.save(payload)
        self._check_android_config(config)
        cache_path = self.android_store.config_path.with_name("ticket_candidates.json")
        layout_path = self.android_store.config_path.with_name(LAYOUT_CACHE_NAME)
        return prepare_android_tickets(config, cache_path, layout_path)

    def android_layout_steps(self):
        return {"steps": layout_steps()}

    def capture_android_layout(self, payload):
        if self.manager.snapshot()["running"]:
            raise TaskBusy("抢票任务运行中，请先停止后再采集页面坐标")
        step = (payload or {}).get("step")
        if not step:
            raise ConfigError("请指定要采集的页面步骤")
        config = self.android_store.load()
        self._check_android_config(config)
        layout_path = self.android_store.config_path.with_name(LAYOUT_CACHE_NAME)
        return capture_android_layout(config, step, layout_path)

    def start_android(self, payload):
        config = self.android_store.save(payload)
        self._check_android_config(config)
        command = self.android_command or [sys.executable, "damai_app_v2.py"]
        return self.manager.start(
            "android",
            command,
            self.android_store.config_path.parent,
        )


class LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, application: WebApplication):
        self.application = application
        super().__init__(address, handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "DamaiLocal/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def application(self) -> WebApplication:
        return self.server.application

    def log_message(self, format, *args):
        return

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _require_same_origin(self) -> None:
        if self.headers.get("X-Requested-With") != "fetch":
            raise ConfigError("缺少本机页面请求标识")

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ConfigError("无效的请求长度") from exc
        if length <= 0 or length > 65536:
            raise ConfigError("请求体为空或过大")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigError("请求不是有效的 JSON") from exc

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            self._api(method, path)
            return
        if method != "GET":
            self._error(405, "不支持的请求方法")
            return
        self._static(path)

    def _api(self, method: str, path: str) -> None:
        try:
            if method in {"POST", "PUT", "DELETE"}:
                self._require_same_origin()
            app = self.application
            if method == "GET" and path == "/api/status":
                self._json(200, app.status())
            elif method == "GET" and path == "/api/config":
                app.require_pc()
                self._json(200, app.store.load())
            elif method == "PUT" and path == "/api/config":
                app.require_pc()
                self._json(200, app.store.save(self._read_json()))
            elif method == "GET" and path == "/api/android/config":
                self._json(200, app.android_store.load())
            elif method == "PUT" and path == "/api/android/config":
                self._json(200, app.android_store.save(self._read_json()))
            elif method == "GET" and path == "/api/android/devices":
                self._json(200, app.list_android_devices())
            elif method == "GET" and path == "/api/logs":
                self._json(200, {"logs": app.manager.logs()})
            elif method == "GET" and path == "/api/events":
                self._events()
            elif method == "POST" and path == "/api/check":
                self._json(200, app.start_check())
            elif method == "POST" and path == "/api/android/check":
                self._json(200, app.check_android())
            elif method == "POST" and path == "/api/android/tickets/prepare":
                self._json(200, app.prepare_android(self._read_json()))
            elif method == "GET" and path == "/api/android/layout/steps":
                self._json(200, app.android_layout_steps())
            elif method == "POST" and path == "/api/android/layout/capture":
                self._json(200, app.capture_android_layout(self._read_json()))
            elif method == "POST" and path == "/api/login":
                self._json(200, app.start_login())
            elif method == "POST" and path == "/api/login/complete":
                self._json(200, app.manager.complete_login(app.login_signal))
            elif method == "POST" and path == "/api/start":
                self._json(200, app.start_grab(self._read_json()))
            elif method == "POST" and path == "/api/android/start":
                self._json(200, app.start_android(self._read_json()))
            elif method == "POST" and path == "/api/stop":
                self._json(200, app.manager.stop())
            else:
                self._error(404, "接口不存在")
        except (ConfigError, TicketSelectionError, LayoutCacheError) as exc:
            self._error(400, str(exc))
        except TaskBusy as exc:
            self._error(409, str(exc))
        except TaskNotRunning as exc:
            self._error(409, str(exc))
        except TaskError as exc:
            self._error(500, str(exc))
        except Exception as exc:
            self._error(500, f"服务异常：{type(exc).__name__}: {exc}")

    def _events(self) -> None:
        manager = self.application.manager
        channel = manager.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            initial = {
                "status": self.application.status(),
                "logs": manager.logs(),
            }
            self._send_event("initial", initial)
            while True:
                try:
                    event = channel.get(timeout=15)
                    self._send_event(event["type"], event["data"])
                except queue.Empty:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            manager.unsubscribe(channel)

    def _send_event(self, name: str, data: dict) -> None:
        payload = json.dumps(data, ensure_ascii=False)
        self.wfile.write(f"event: {name}\ndata: {payload}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _static(self, path: str) -> None:
        files = {
            "/": STATIC_ROOT / "index.html",
            "/index.html": STATIC_ROOT / "index.html",
            "/styles.css": STATIC_ROOT / "styles.css",
            "/app.js": STATIC_ROOT / "app.js",
        }
        file_path = files.get(path)
        if file_path is None or not file_path.is_file():
            self._error(404, "页面不存在")
            return
        body = file_path.read_bytes()
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")


def create_server(
    project_root: Path | str, port: int = 8765, **application_options
) -> LocalHTTPServer:
    application = WebApplication(project_root, **application_options)
    return LocalHTTPServer(("127.0.0.1", port), Handler, application)


def main() -> int:
    parser = argparse.ArgumentParser(description="大麦 PC / 安卓抢票本机 Web 控制台")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1", help="仅允许本机地址")
    parser.add_argument("--project-root", type=Path, default=default_project_root())
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("为避免远程触发抢票，只允许监听本机地址")
    if not 0 <= args.port <= 65535:
        parser.error("端口必须在 0 到 65535 之间")

    try:
        server = create_server(args.project_root, args.port)
    except OSError as exc:
        print(f"无法启动 Web 服务：{exc}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"大麦 Web 控制台：{url}", flush=True)
    print(f"抢票项目：{args.project_root.resolve()}", flush=True)
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.application.manager.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
