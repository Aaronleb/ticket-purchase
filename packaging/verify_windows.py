from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "dist" / "DamaiAndroid-windows-x64.zip"
VERIFY = ROOT / "build" / "verify-windows"
APP = VERIFY / "DamaiAndroid"
DATA = VERIFY / "user data"
STDOUT = VERIFY / "stdout.log"
CONSOLE_LOG = DATA / "logs" / "console.log"
APPIUM_LOG = DATA / "logs" / "appium.log"


def request(url: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict | str]:
    parsed = urlparse(url)
    body = json.dumps(payload) if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    if method in {"POST", "PUT", "DELETE"}:
        headers["X-Requested-With"] = "fetch"
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    connection.request(method, parsed.path, body=body, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    content_type = response.getheader("Content-Type", "")
    connection.close()
    text = raw.decode("utf-8")
    return status, json.loads(text) if "application/json" in content_type else text


def console_url(process: subprocess.Popen) -> str:
    deadline = time.monotonic() + 60
    pattern = re.compile(r"控制台地址：(http://127\.0\.0\.1:\d+)")
    while time.monotonic() < deadline:
        if CONSOLE_LOG.is_file():
            match = pattern.search(CONSOLE_LOG.read_text(encoding="utf-8", errors="replace"))
            if match:
                return match.group(1)
        if process.poll() is not None:
            raise RuntimeError(f"应用提前退出，代码 {process.returncode}")
        time.sleep(0.2)
    raise TimeoutError("等待控制台地址超时")


def wait_until_ready(process: subprocess.Popen, url: str) -> dict:
    deadline = time.monotonic() + 60
    latest = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"应用提前退出，代码 {process.returncode}")
        try:
            status, latest = request(f"{url}/api/status")
            if status == 200 and latest["runtime"]["state"] == "ready":
                return latest
            if status == 200 and latest["runtime"]["state"] == "error":
                raise RuntimeError(latest["runtime"]["message"])
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.25)
    raise TimeoutError(f"等待内置 Appium 就绪超时：{latest}")


def print_logs() -> None:
    for path in (STDOUT, CONSOLE_LOG, APPIUM_LOG):
        if path.is_file():
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            print(f"\n--- {path} ---", file=sys.stderr)
            print("\n".join(lines[-80:]), file=sys.stderr)


def main() -> int:
    if os.name != "nt":
        raise RuntimeError("Windows zip 验证必须在 Windows 上运行")
    if not ARCHIVE.is_file():
        raise FileNotFoundError(f"未找到 {ARCHIVE}，请先运行 packaging/build_windows.ps1")
    shutil.rmtree(VERIFY, ignore_errors=True)
    VERIFY.mkdir(parents=True)
    with zipfile.ZipFile(ARCHIVE) as bundle:
        bundle.extractall(VERIFY)
    DATA.mkdir(parents=True)

    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    environment = os.environ.copy()
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        environment.pop(variable, None)
    environment.update(
        {
            "PATH": os.pathsep.join([str(system_root / "System32"), str(system_root)]),
            "ANDROID_USER_HOME": str(DATA / "android"),
            "ANDROID_SDK_HOME": str(DATA),
            "ADB_SERVER_SOCKET": "tcp:127.0.0.1:5038",
        }
    )
    executable = APP / "DamaiAndroid.exe"
    adb = APP / "runtime" / "android-platform-tools" / "adb.exe"
    command = [
        str(executable),
        "--data-dir",
        str(DATA),
        "--startup-timeout",
        "45",
        "--no-browser",
    ]
    process = None
    appium_pid = None
    try:
        with STDOUT.open("w", encoding="utf-8") as output:
            process = subprocess.Popen(
                command,
                cwd=VERIFY,
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        url = console_url(process)
        status = wait_until_ready(process, url)
        assert status["enabled_platforms"] == ["android"], status
        assert status["managed_appium"] is True, status
        appium_pid = status["runtime"]["pid"]
        assert appium_pid, status
        appium_log = APPIUM_LOG.read_text(encoding="utf-8", errors="replace")
        assert str(DATA / "appium-home") in appium_log

        http_status, page = request(url)
        assert http_status == 200
        assert "大麦" in page
        http_status, devices = request(f"{url}/api/android/devices")
        assert http_status == 200, devices
        assert "未找到内置 ADB" not in devices["message"], devices

        config = {
            "server_url": "https://external.example.com",
            "device_name": "verification-device",
            "platform_version": "16",
            "users": ["测试观演人"],
            "city": "测试城市",
            "price_index": 0,
            "sell_start_time": "2099-01-01T20:00:00+08:00",
            "if_commit_order": False,
        }
        http_status, saved = request(f"{url}/api/android/config", "PUT", config)
        assert http_status == 200, saved
        assert saved["server_url"] == status["runtime"]["server_url"], saved
        http_status, report = request(f"{url}/api/android/check", "POST")
        assert http_status == 200, report
        checks = {item["id"]: item["ok"] for item in report["checks"]}
        assert checks["client"] is True, report
        assert checks["server"] is True, report
        assert checks["adb"] is True, report
        assert checks["device"] is False, report
        assert (DATA / "config.jsonc").is_file()

        version = subprocess.run(
            [str(adb), "--version"], env=environment, capture_output=True, text=True
        )
        assert version.returncode == 0, version.stderr
        assert "Android Debug Bridge" in version.stdout, version.stdout
        print(f"Windows zip 验证通过：{url}")
        print(f"ADB：{version.stdout.splitlines()[0]}")
        print("内嵌 Appium、客户端、服务、配置写入均通过；未执行真实抢票。")
        return 0
    except Exception:
        print_logs()
        raise
    finally:
        taskkill = system_root / "System32" / "taskkill.exe"
        for candidate in (process, appium_pid):
            pid = candidate.pid if hasattr(candidate, "pid") else candidate
            if not pid:
                continue
            subprocess.run(
                [str(taskkill), "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        if process is not None:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if adb.is_file():
            subprocess.run([str(adb), "kill-server"], env=environment, capture_output=True)


if __name__ == "__main__":
    raise SystemExit(main())
