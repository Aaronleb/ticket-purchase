from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from urllib.request import Request, urlopen


class AndroidEnvironment:
    def __init__(self, timeout: float = 3):
        self.timeout = timeout

    def check(self, config: dict) -> dict:
        checks = []
        client_ok = importlib.util.find_spec("appium") is not None
        checks.append(
            self._result(
                "client",
                "Python Appium 客户端",
                client_ok,
                "已安装" if client_ok else "请安装项目 requirements.txt 中的依赖",
            )
        )
        checks.append(self._check_server(config["server_url"]))

        adb = shutil.which("adb")
        checks.append(
            self._result(
                "adb",
                "Android ADB",
                bool(adb),
                adb or "未找到 adb，请安装 Android SDK Platform-Tools 并加入 PATH",
            )
        )
        device_ok = False
        if adb:
            device_check, device_ok = self._check_device(adb, config["device_name"])
        else:
            device_check = self._result("device", "安卓设备", False, "adb 不可用，无法检测设备")
        checks.append(device_check)

        if device_ok:
            checks.append(self._check_app(adb, config["device_name"]))
        else:
            checks.append(self._result("app", "大麦 App", False, "设备未就绪，无法检查大麦 App"))
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    def list_devices(self) -> dict:
        adb = shutil.which("adb")
        if not adb:
            return {"ok": False, "devices": [], "message": "未找到内置 ADB，无法检测设备"}
        try:
            result = self._run_adb(adb, ["devices"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "devices": [], "message": f"adb devices 执行失败：{exc}"}
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "adb devices 执行失败"
            return {"ok": False, "devices": [], "message": message}

        devices = []
        for line in result.stdout.splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            name, state = parts[:2]
            device = {
                "name": name,
                "state": state,
                "online": state == "device",
                "platform_version": "",
                "app_installed": False,
            }
            if device["online"]:
                try:
                    version = self._run_adb(
                        adb, ["-s", name, "shell", "getprop", "ro.build.version.release"]
                    )
                    if version.returncode == 0:
                        device["platform_version"] = version.stdout.strip().splitlines()[0]
                except (OSError, subprocess.TimeoutExpired, IndexError):
                    pass
                device["app_installed"] = self._check_app(adb, name)["ok"]
            devices.append(device)

        online = [device for device in devices if device["online"]]
        unavailable = [f"{device['name']} ({device['state']})" for device in devices if not device["online"]]
        if online:
            message = f"检测到 {len(online)} 台在线设备"
        else:
            message = "未检测到在线设备，请连接手机并允许 USB 调试授权"
        if unavailable:
            message += f"；不可用设备：{', '.join(unavailable)}"
        return {"ok": bool(online), "devices": devices, "message": message}

    def _check_server(self, server_url: str) -> dict:
        url = f"{server_url.rstrip('/')}/status"
        try:
            request = Request(url, headers={"Accept": "application/json"})
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            ready = isinstance(payload, dict) and payload.get("value", {}).get("ready") is True
            if ready:
                return self._result("server", "Appium 服务", True, "服务已就绪")
            message = "服务已响应，但尚未就绪"
        except Exception as exc:
            message = f"无法连接 {url}：{type(exc).__name__}: {exc}"
        return self._result("server", "Appium 服务", False, message)

    def _run_adb(self, adb: str, arguments: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [adb, *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout,
            check=False,
        )

    def _check_device(self, adb: str, device_name: str) -> tuple[dict, bool]:
        try:
            result = self._run_adb(adb, ["devices"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return self._result("device", "安卓设备", False, f"adb devices 执行失败：{exc}"), False
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "adb devices 执行失败"
            return self._result("device", "安卓设备", False, message), False
        online = []
        unavailable = []
        for line in result.stdout.splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2:
                if parts[1] == "device":
                    online.append(parts[0])
                else:
                    unavailable.append(f"{parts[0]} ({parts[1]})")
        if device_name in online:
            return self._result("device", "安卓设备", True, f"{device_name} 在线"), True
        details = f"；检测到在线设备：{', '.join(online)}" if online else ""
        if unavailable:
            details += f"；不可用设备：{', '.join(unavailable)}"
        return self._result("device", "安卓设备", False, f"设备 {device_name} 不在线{details}"), False

    def _check_app(self, adb: str, device_name: str) -> dict:
        try:
            result = self._run_adb(adb, ["-s", device_name, "shell", "pm", "list", "packages", "cn.damai"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return self._result("app", "大麦 App", False, f"检查应用失败：{exc}")
        installed = result.returncode == 0 and any(
            line.strip() == "package:cn.damai" for line in result.stdout.splitlines()
        )
        return self._result(
            "app",
            "大麦 App",
            installed,
            "已安装" if installed else "设备上未安装 cn.damai，或系统不允许查询应用",
        )

    @staticmethod
    def _result(check_id: str, label: str, ok: bool, message: str) -> dict:
        return {"id": check_id, "label": label, "ok": ok, "message": message}
