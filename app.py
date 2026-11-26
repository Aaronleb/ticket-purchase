#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import queue
import sys
import threading
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from android_calibrate import calibrate_android_point, calibrate_negative_point
from android_config import AndroidConfigStore
from android_diagnostics import AndroidDiagnostics
from android_prewarm import prewarm_ticket_page
from time_sync import measure_time_sync
from android_environment import AndroidEnvironment
from config_store import ConfigError, ConfigStore, default_project_root
from damai_appium.sale_clock import SHANGHAI
from process_manager import ProcessManager, TaskBusy, TaskError, TaskNotRunning


APP_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = APP_ROOT / "static"
RUNTIME_ROOT = APP_ROOT / ".runtime"


class WebApplication:
    def __init__(
        self,
        project_root: Path | str,
        android_only: bool = False,
        pc_only: bool = False,
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
        self.android_diagnostics = AndroidDiagnostics()
        self.manager = ProcessManager()
        self.android_only = android_only
        self.pc_only = pc_only
        self.android_command = android_command
        self.runtime_status = runtime_status or (lambda: {"state": "external"})
        self.login_signal = RUNTIME_ROOT / "login.signal"

    def status(self) -> dict:
        status = self.manager.snapshot()
        status.update(
            {
                "logged_in": self.store.cookie_path.is_file(),
                "project_root": str(self.store.project_root),
                "enabled_platforms": (
                    ["android"]
                    if self.android_only
                    else (["pc"] if self.pc_only else ["pc", "android"])
                ),
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
        if not driver.is_file():
            # Windows 免安装包通过 CHROMEDRIVER_PATH 指向内置驱动
            configured = os.environ.get("CHROMEDRIVER_PATH", "").strip()
            if configured:
                driver = Path(configured)
        if driver.is_file():
            command.extend(["--driver", str(driver)])
        return self.manager.start("login", command, self.store.project_root)

    def start_grab(self, payload):
        self.require_pc()
        self.store.save(payload)
        return self.manager.start("grab", [sys.executable, "damai.py"], self.store.damai_dir)

    def check_android(self):
        return self.android_environment.check(self.android_store.load())

    def _android_config_with_form(self, payload=None):
        """页面表单可能还没保存；把表单里的业务字段合并进配置。

        避免校准/采集模板时把未保存的购票数量、观演人等覆盖成旧值。
        """
        config = self.android_store.load()
        if isinstance(payload, dict):
            for field in (
                "device_name",
                "platform_version",
                "ticket_count",
                "users",
                "price",
                "keyword",
                "fast_mode",
                "auto_select",
                "buy_point",
                "confirm_point",
                "submit_point",
                "flip_template_dir",
            ):
                if field in payload:
                    config[field] = payload[field]
        return config

    def calibrate_android(self, payload=None):
        """识别手机当前页面，把对应按钮坐标写回配置。"""
        config = self._android_config_with_form(payload)
        template_dir = self.android_store.config_path.parent / "flip-templates"
        result = calibrate_android_point(
            config.get("device_name", ""), template_dir=template_dir
        )
        config[result["field"]] = result["point"]
        if result.get("template_path"):
            config["flip_template_dir"] = str(template_dir)
        result["config"] = self.android_store.save(config)
        return result

    def calibrate_negative(self, payload=None):
        """采集“已预约/未开售”按钮的负向模板，命中时拒绝放行。"""
        config = self._android_config_with_form(payload)
        template_dir = self.android_store.config_path.parent / "flip-templates"
        result = calibrate_negative_point(
            config.get("device_name", ""), template_dir=template_dir
        )
        config["flip_template_dir"] = str(template_dir)
        result["config"] = self.android_store.save(config)
        return result

    def prewarm_android(self, payload=None):
        """在非目标演出详情页上预热原生票档页（只导航，不点确定/不下单）。"""
        config = self._android_config_with_form(payload)
        device = str(config.get("device_name") or "").strip()
        if not device:
            raise ConfigError("请先在安卓配置里填写设备名")
        return prewarm_ticket_page(
            device,
            buy_point=str(config.get("buy_point") or ""),
            runs=2,
        )

    def time_sync_android(self):
        """测量本机与手机、阿里系服务器的时钟偏差。"""
        config = self.android_store.load()
        return measure_time_sync(config.get("device_name") or None)

    def _diagnostic_device(self, payload) -> str:
        device = str((payload or {}).get("device_name") or "").strip()
        if not device:
            raise ConfigError("请先在安卓配置里填写设备名")
        return device

    def network_test_android(self, payload):
        """测试手机到 detail/mtop 的 RTT、抖动、丢包与 Wi-Fi 信号。"""
        return self.android_diagnostics.network_report(
            self._diagnostic_device(payload)
        )

    def performance_test_android(self, payload):
        """读取手机内存、温度、动画、帧率与大麦进程状态。"""
        return self.android_diagnostics.performance_report(
            self._diagnostic_device(payload)
        )

    def list_android_devices(self):
        return self.android_environment.list_devices()

    def start_android(self, payload, dry_run=False, run_now=False):
        payload = dict(payload or {})
        # dry_run / run_now 不写入配置文件：只通过环境变量传给本次 worker，
        # 避免下一次正常启动意外继承演练模式。
        dry_run = bool(payload.pop("dry_run", False) or dry_run)
        run_now = bool(payload.pop("run_now", False) or run_now)
        dry_run = dry_run or run_now
        if run_now and not str(payload.get("sell_start_time") or "").strip():
            payload["sell_start_time"] = (
                datetime.now(SHANGHAI) + timedelta(minutes=1)
            ).isoformat()
        config = self.android_store.save(payload)
        report = self.android_environment.check(config)
        if not report["ok"]:
            failures = "；".join(
                f"{check['label']}：{check['message']}"
                for check in report["checks"]
                if not check["ok"]
            )
            raise ConfigError(f"安卓环境检查未通过：{failures}")
        command = self.android_command or [sys.executable, "damai_app_v2.py"]
        env = None
        if dry_run:
            env = {"DAMAI_DRY_RUN": "1"}
        if run_now:
            env = {**(env or {}), "DAMAI_RUN_NOW": "1"}
        return self.manager.start(
            "android",
            command,
            self.android_store.config_path.parent,
            env=env,
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

    def _optional_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = 0
        return self._read_json() if length > 0 else None

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
            if app.pc_only and path.startswith("/api/android"):
                raise ConfigError("此安装包仅包含 PC 抢票功能，安卓入口不可用")
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
            elif method == "POST" and path == "/api/android/calibrate":
                self._json(200, app.calibrate_android(self._optional_json()))
            elif method == "POST" and path == "/api/android/calibrate-negative":
                self._json(200, app.calibrate_negative(self._optional_json()))
            elif method == "POST" and path == "/api/android/prewarm":
                self._json(200, app.prewarm_android(self._optional_json()))
            elif method == "GET" and path == "/api/android/time-sync":
                self._json(200, app.time_sync_android())
            elif method == "POST" and path == "/api/android/network-test":
                self._json(200, app.network_test_android(self._read_json()))
            elif method == "POST" and path == "/api/android/performance-test":
                self._json(200, app.performance_test_android(self._read_json()))
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
        except ConfigError as exc:
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
    parser.add_argument("--android-only", action="store_true", help="只启用安卓端，隐藏 PC 入口")
    parser.add_argument("--pc-only", action="store_true", help="只启用 PC 端，隐藏安卓入口")
    args = parser.parse_args()
    if args.android_only and args.pc_only:
        parser.error("--android-only 与 --pc-only 不能同时使用")
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("为避免远程触发抢票，只允许监听本机地址")
    if not 0 <= args.port <= 65535:
        parser.error("端口必须在 0 到 65535 之间")

    try:
        server = create_server(
            args.project_root,
            args.port,
            android_only=args.android_only,
            pc_only=args.pc_only,
        )
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
