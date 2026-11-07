from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class AppiumService:
    def __init__(
        self,
        node_path: Path | str,
        appium_entry: Path | str,
        appium_home: Path | str,
        log_path: Path | str,
        startup_timeout: float = 30,
    ):
        self.node_path = Path(node_path)
        self.appium_entry = Path(appium_entry)
        self.appium_home = Path(appium_home)
        self.log_path = Path(log_path)
        self.startup_timeout = startup_timeout
        self.port = available_port()
        self.server_url = f"http://127.0.0.1:{self.port}"
        self._proc = None
        self._log_handle = None
        self._state = "idle"
        self._message = "Appium 尚未启动"
        self._lock = threading.RLock()

    def snapshot(self) -> dict:
        with self._lock:
            if self._proc is not None and self._state in {"starting", "ready"}:
                code = self._proc.poll()
                if code is not None:
                    self._state = "error"
                    self._message = f"Appium 异常退出（代码 {code}），请查看应用日志"
            return {
                "state": self._state,
                "message": self._message,
                "server_url": self.server_url,
                "pid": self._proc.pid if self._proc and self._proc.poll() is None else None,
                "log_path": str(self.log_path),
            }

    def start(self) -> dict:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return self.snapshot()
            if not self.node_path.is_file():
                return self._fail(f"未找到内置 Node.js：{self.node_path}")
            if not self.appium_entry.is_file():
                return self._fail(f"未找到内置 Appium：{self.appium_entry}")
            try:
                self.appium_home.mkdir(parents=True, exist_ok=True)
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                self._log_handle = self.log_path.open("a", encoding="utf-8")
                environment = os.environ.copy()
                environment["APPIUM_HOME"] = str(self.appium_home)
                self._proc = subprocess.Popen(
                    [
                        str(self.node_path),
                        str(self.appium_entry),
                        "--address",
                        "127.0.0.1",
                        "--port",
                        str(self.port),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=self._log_handle,
                    stderr=subprocess.STDOUT,
                    env=environment,
                    start_new_session=True,
                )
            except OSError as exc:
                self._close_log()
                return self._fail(f"Appium 启动失败：{exc}")
            self._state = "starting"
            self._message = "正在启动内置 Appium"

        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._proc is not None and self._proc.poll() is not None:
                    code = self._proc.returncode
                    self._close_log()
                    return self._fail(f"Appium 启动失败（代码 {code}），请查看应用日志")
            if self._ready():
                with self._lock:
                    self._state = "ready"
                    self._message = "内置 Appium 已就绪"
                return self.snapshot()
            time.sleep(0.1)

        self.stop()
        return self._fail("Appium 启动超时，请查看应用日志")

    def stop(self) -> dict:
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=2)
        with self._lock:
            self._close_log()
            if self._state != "error":
                self._state = "stopped"
                self._message = "Appium 已停止"
        return self.snapshot()

    def _ready(self) -> bool:
        try:
            request = Request(f"{self.server_url}/status", headers={"Accept": "application/json"})
            with urlopen(request, timeout=0.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return isinstance(payload, dict) and payload.get("value", {}).get("ready") is True
        except Exception:
            return False

    def _fail(self, message: str) -> dict:
        with self._lock:
            self._state = "error"
            self._message = message
        return self.snapshot()

    def _close_log(self) -> None:
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
