#!/usr/bin/env python3
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
DMG = ROOT / "dist" / "DamaiAndroid-arm64.dmg"
VERIFY = ROOT / "build" / "verify"
MOUNT = VERIFY / "mounted image"
DATA = VERIFY / "user data"
STDOUT = VERIFY / "stdout.log"
CONSOLE_LOG = DATA / "logs" / "console.log"
APPIUM_LOG = DATA / "logs" / "appium.log"
EARLY_EXIT = "--early-exit" in sys.argv
WINDOW_MODE = "--window" in sys.argv or EARLY_EXIT
WINDOW_SCREENSHOT = VERIFY / "window.png"


def wait_for_native_window(process: subprocess.Popen) -> dict:
    from Quartz import (
        CGWindowListCopyWindowInfo,
        kCGNullWindowID,
        kCGWindowBounds,
        kCGWindowLayer,
        kCGWindowListOptionOnScreenOnly,
        kCGWindowNumber,
        kCGWindowOwnerPID,
    )

    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"窗口应用提前退出，代码 {process.returncode}")
        windows = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID) or []
        for window in windows:
            if int(window.get(kCGWindowOwnerPID, -1)) != process.pid:
                continue
            if int(window.get(kCGWindowLayer, -1)) != 0:
                continue
            bounds = window.get(kCGWindowBounds, {})
            if bounds.get("Width", 0) >= 900 and bounds.get("Height", 0) >= 600:
                return {"number": int(window[kCGWindowNumber])}
        time.sleep(0.25)
    raise TimeoutError("等待大麦安卓原生窗口超时")


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
    if not DMG.is_file():
        raise FileNotFoundError(f"未找到 {DMG}，请先运行 packaging/build_dmg.sh")
    shutil.rmtree(VERIFY, ignore_errors=True)
    MOUNT.mkdir(parents=True)
    DATA.mkdir(parents=True)
    environment = os.environ.copy()
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        environment.pop(variable, None)
    environment.update(
        {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "ANDROID_USER_HOME": str(DATA / "android"),
            "ANDROID_SDK_HOME": str(DATA),
            "ADB_SERVER_SOCKET": "tcp:127.0.0.1:5038",
        }
    )
    process = None
    mounted = False
    adb = MOUNT / "DamaiAndroid.app" / "Contents" / "Resources" / "runtime" / "android-platform-tools" / "adb"
    try:
        subprocess.run(
            ["hdiutil", "attach", str(DMG), "-readonly", "-nobrowse", "-mountpoint", str(MOUNT)],
            check=True,
        )
        mounted = True
        bundled_app = MOUNT / "DamaiAndroid.app"
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(bundled_app)], check=True)
        executable = bundled_app / "Contents" / "MacOS" / "DamaiAndroid"
        command = [
            str(executable),
            "--data-dir",
            str(DATA),
            "--startup-timeout",
            "45",
        ]
        if not WINDOW_MODE:
            command.append("--no-browser")
        with STDOUT.open("w", encoding="utf-8") as output:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        url = console_url(process)
        status = wait_until_ready(process, url)
        assert status["enabled_platforms"] == ["android"], status
        assert status["managed_appium"] is True, status
        appium_pid = status["runtime"]["pid"]
        assert appium_pid, status
        appium_log = APPIUM_LOG.read_text(encoding="utf-8", errors="replace")
        assert str(DATA / "appium-home" / "node_modules" / "appium-uiautomator2-driver") in appium_log
        assert str(ROOT / "build" / "runtime" / "appium-cli") not in appium_log
        if WINDOW_MODE:
            window = wait_for_native_window(process)
            if EARLY_EXIT:
                from AppKit import NSRunningApplication

                application = NSRunningApplication.runningApplicationWithProcessIdentifier_(process.pid)
                assert application is not None, "未找到待退出的原生应用"
                application.terminate()
                process.wait(timeout=20)
                try:
                    os.kill(appium_pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    raise AssertionError("提前退出应用后 Appium 仍在运行")
                print("DMG 页面初始化期间的提前退出验证通过。")
                return 0
            from Quartz import CGPreflightScreenCaptureAccess

            if CGPreflightScreenCaptureAccess():
                subprocess.run(
                    ["/usr/sbin/screencapture", "-x", "-l", str(window["number"]), str(WINDOW_SCREENSHOT)],
                    check=True,
                )
            else:
                print("已检测到原生窗口；没有屏幕录制权限，跳过窗口截图。")

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

        version = subprocess.run([str(adb), "--version"], env=environment, capture_output=True, text=True)
        assert version.returncode == 0, version.stderr
        assert "Android Debug Bridge" in version.stdout, version.stdout

        if WINDOW_MODE:
            from AppKit import NSRunningApplication

            application = NSRunningApplication.runningApplicationWithProcessIdentifier_(process.pid)
            assert application is not None, "未找到待退出的原生应用"
            application.terminate()
        else:
            process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            subprocess.run(["ps", "-o", "pid,ppid,pgid,stat,command", "-p", str(process.pid)], check=False)
            children = subprocess.run(["pgrep", "-P", str(process.pid)], capture_output=True, text=True, check=False)
            if children.stdout.strip():
                subprocess.run(["ps", "-o", "pid,ppid,pgid,stat,command", "-p", children.stdout.strip().replace("\n", ",")], check=False)
            raise
        try:
            os.kill(appium_pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError("退出应用后 Appium 仍在运行")
        print(f"DMG 验证通过：{url}")
        print(f"ADB：{version.stdout.splitlines()[0]}")
        print("内嵌 Appium、客户端、服务、配置写入和进程清理均通过；未执行真实抢票。")
        return 0
    except Exception:
        print_logs()
        raise
    finally:
        if process is not None and process.poll() is None:
            if WINDOW_MODE:
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        appium_pid = locals().get("appium_pid")
        if appium_pid:
            try:
                os.killpg(os.getpgid(appium_pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        if adb.is_file():
            subprocess.run([str(adb), "kill-server"], env=environment, capture_output=True)
        if mounted:
            subprocess.run(["hdiutil", "detach", str(MOUNT)], check=False)


if __name__ == "__main__":
    raise SystemExit(main())
