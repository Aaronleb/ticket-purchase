import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_server
from test_android_config import valid_android_config


class AndroidApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "damai").mkdir()
        (self.root / "damai" / "damai.py").write_text("print('pc')\n", encoding="utf-8")
        (self.root / "damai" / "check_environment.py").write_text("print('check')\n", encoding="utf-8")
        self.appium_dir = self.root / "damai_appium"
        self.appium_dir.mkdir()
        (self.appium_dir / "damai_app_v2.py").write_text(
            "import os\nprint('android fake started: ' + os.getcwd(), flush=True)\n",
            encoding="utf-8",
        )
        self.server = create_server(self.root, port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.application.manager.shutdown()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def request(self, method, path, payload=None, same_origin=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload)
            headers["Content-Type"] = "application/json"
        if same_origin:
            headers["X-Requested-With"] = "fetch"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read().decode("utf-8"))
        status = response.status
        connection.close()
        return status, data

    def test_android_config_round_trip(self):
        status, saved = self.request("PUT", "/api/android/config", valid_android_config())
        self.assertEqual(status, 200)
        self.assertEqual(saved["price_index"], 1)
        status, loaded = self.request("GET", "/api/android/config")
        self.assertEqual(status, 200)
        self.assertEqual(loaded["users"], ["测试观演人"])

    def test_android_check_returns_structured_results(self):
        report = {"ok": True, "checks": [{"id": "server", "label": "Appium 服务", "ok": True, "message": "就绪"}]}
        self.request("PUT", "/api/android/config", valid_android_config())
        with patch.object(self.server.application.android_environment, "check", return_value=report):
            status, actual = self.request("POST", "/api/android/check")
        self.assertEqual(status, 200)
        self.assertEqual(actual, report)

    def test_android_devices_route_returns_detection_report(self):
        report = {"ok": False, "devices": [], "message": "未连接设备"}
        with patch.object(self.server.application.android_environment, "list_devices", return_value=report):
            status, actual = self.request("GET", "/api/android/devices")
        self.assertEqual(status, 200)
        self.assertEqual(actual, report)

    def test_failed_environment_check_prevents_start(self):
        report = {
            "ok": False,
            "checks": [{"id": "server", "label": "Appium 服务", "ok": False, "message": "未启动"}],
        }
        with patch.object(self.server.application.android_environment, "check", return_value=report):
            status, error = self.request("POST", "/api/android/start", valid_android_config())
        self.assertEqual(status, 400)
        self.assertIn("Appium 服务", error["error"])
        self.assertFalse(self.server.application.manager.snapshot()["running"])

    def test_android_start_executes_entry_in_appium_directory(self):
        report = {"ok": True, "checks": []}
        with patch.object(self.server.application.android_environment, "check", return_value=report):
            status, started = self.request("POST", "/api/android/start", valid_android_config())
        self.assertEqual(status, 200)
        self.assertEqual(started["phase"], "grabbing")
        self.assertEqual(started["task"], "android")
        deadline = time.time() + 5
        current = started
        while time.time() < deadline:
            _, current = self.request("GET", "/api/status")
            if current["phase"] == "idle":
                break
            time.sleep(0.03)
        self.assertEqual(current["phase"], "idle")
        self.assertEqual(current["exit_code"], 0)
        _, logs = self.request("GET", "/api/logs")
        lines = [entry["text"] for entry in logs["logs"]]
        self.assertIn(f"android fake started: {self.appium_dir.resolve()}", lines)
        self.assertIn("大麦 App 确认订单状态", current["message"])

    def test_android_only_application_disables_pc_and_reports_runtime(self):
        packaged = create_server(
            self.root,
            port=0,
            android_only=True,
            android_config_path=self.root / "user-data" / "config.jsonc",
            android_command=["frozen-app", "--android-worker"],
            managed_server_url="http://127.0.0.1:49152",
            runtime_status=lambda: {"state": "ready"},
        )
        try:
            status = packaged.application.status()
            self.assertEqual(status["enabled_platforms"], ["android"])
            self.assertTrue(status["managed_appium"])
            self.assertEqual(status["runtime"]["state"], "ready")
            with self.assertRaisesRegex(ValueError, "仅包含安卓"):
                packaged.application.start_check()
        finally:
            packaged.server_close()


if __name__ == "__main__":
    unittest.main()
