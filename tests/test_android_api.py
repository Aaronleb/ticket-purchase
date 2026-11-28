import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_server
from config_store import ConfigError
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

    def test_android_network_test_returns_report(self):
        report = {
            "ok": True,
            "level": "pass",
            "summary": "网络检查全部达标",
            "metrics": [],
            "advice": [],
        }
        with patch.object(
            self.server.application.android_diagnostics,
            "network_report",
            return_value=report,
        ) as network:
            status, actual = self.request(
                "POST", "/api/android/network-test", valid_android_config()
            )
        self.assertEqual(status, 200)
        self.assertEqual(actual, report)
        self.assertEqual(network.call_args.args[0], valid_android_config()["device_name"])

    def test_android_performance_test_returns_report(self):
        report = {
            "ok": False,
            "level": "warn",
            "summary": "设备检查基本可用",
            "metrics": [],
            "advice": ["关闭系统动画"],
        }
        with patch.object(
            self.server.application.android_diagnostics,
            "performance_report",
            return_value=report,
        ) as performance:
            status, actual = self.request(
                "POST", "/api/android/performance-test", valid_android_config()
            )
        self.assertEqual(status, 200)
        self.assertEqual(actual, report)
        self.assertEqual(
            performance.call_args.args[0], valid_android_config()["device_name"]
        )

    def test_android_prewarm_returns_runs(self):
        result = {
            "ok": True,
            "runs": [{"run": 1, "ok": True, "open_ms": 120}],
            "message": "票档页预热完成",
        }
        with patch("app.prewarm_ticket_page", return_value=result) as prewarm:
            status, actual = self.request(
                "POST", "/api/android/prewarm", valid_android_config()
            )
        self.assertEqual(status, 200)
        self.assertEqual(actual, result)
        self.assertEqual(
            prewarm.call_args.args[0], valid_android_config()["device_name"]
        )

    def test_android_dry_run_passes_env_without_persisting_flag(self):
        report = {"ok": True, "checks": []}
        with patch.object(
            self.server.application.android_environment, "check", return_value=report
        ), patch.object(
            self.server.application.manager,
            "start",
            return_value={"phase": "grabbing", "task": "android"},
        ) as start:
            status, started = self.request(
                "POST",
                "/api/android/start",
                {**valid_android_config(), "dry_run": True},
            )

        self.assertEqual(status, 200)
        self.assertEqual(started["phase"], "grabbing")
        self.assertEqual(start.call_args.kwargs["env"], {"DAMAI_DRY_RUN": "1"})
        _, loaded = self.request("GET", "/api/android/config")
        self.assertNotIn("dry_run", loaded)

    def test_android_run_now_passes_env_without_persisting_flag(self):
        report = {"ok": True, "checks": []}
        with patch.object(
            self.server.application.android_environment, "check", return_value=report
        ), patch.object(
            self.server.application.manager,
            "start",
            return_value={"phase": "grabbing", "task": "android"},
        ) as start:
            status, started = self.request(
                "POST",
                "/api/android/start",
                {**valid_android_config(), "run_now": True},
            )

        self.assertEqual(status, 200)
        self.assertEqual(started["phase"], "grabbing")
        self.assertEqual(
            start.call_args.kwargs["env"],
            {"DAMAI_DRY_RUN": "1", "DAMAI_RUN_NOW": "1"},
        )
        _, loaded = self.request("GET", "/api/android/config")
        self.assertNotIn("run_now", loaded)
        self.assertNotIn("dry_run", loaded)

    def test_android_diagnostics_require_device_name(self):
        payload = {**valid_android_config(), "device_name": ""}
        status, error = self.request("POST", "/api/android/network-test", payload)
        self.assertEqual(status, 400)
        self.assertIn("设备名", error["error"])

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

    def test_android_calibration_saves_template_directory(self):
        self.request("PUT", "/api/android/config", valid_android_config())
        expected_dir = (self.appium_dir / "flip-templates").resolve()
        result = {
            "field": "buy_point",
            "page": "详情页",
            "button": "立即预订",
            "point": "500,1800",
            "activity": "ProjectDetailActivity",
            "template_path": str(expected_dir / "buy.png"),
        }
        with patch("app.calibrate_android_point", return_value=result) as calibrate:
            status, saved = self.request("POST", "/api/android/calibrate")

        self.assertEqual(status, 200)
        self.assertEqual(saved["config"]["flip_template_dir"], str(expected_dir))
        self.assertEqual(calibrate.call_args.kwargs["template_dir"], expected_dir)
        _, loaded = self.request("GET", "/api/android/config")
        self.assertEqual(loaded["flip_template_dir"], str(expected_dir))

    def test_android_calibration_merges_unsaved_form_fields(self):
        self.request(
            "PUT", "/api/android/config", {**valid_android_config(), "ticket_count": 1}
        )
        result = {
            "field": "submit_point",
            "page": "确认页",
            "button": "立即提交",
            "point": "941,2562",
            "activity": "DmOrderActivity",
        }
        payload = {**valid_android_config(), "ticket_count": 2}
        with patch("app.calibrate_android_point", return_value=result):
            status, saved = self.request(
                "POST", "/api/android/calibrate", payload
            )

        self.assertEqual(status, 200)
        self.assertEqual(saved["config"]["ticket_count"], 2)
        self.assertEqual(saved["config"]["submit_point"], "941,2562")
        _, loaded = self.request("GET", "/api/android/config")
        self.assertEqual(loaded["ticket_count"], 2)

    def test_android_negative_calibration_saves_template_without_touching_points(self):
        self.request("PUT", "/api/android/config", valid_android_config())
        expected_dir = (self.appium_dir / "flip-templates").resolve()
        result = {
            "field": "negative_template",
            "page": "详情页",
            "button": "已预约",
            "point": "500,1800",
            "activity": "ProjectDetailActivity",
            "template_path": str(expected_dir / "negative-yuyue.png"),
        }
        payload = {
            **valid_android_config(),
            "ticket_count": 2,
            "buy_point": "765,2556",
        }
        with patch(
            "app.calibrate_negative_point", return_value=result
        ) as calibrate:
            status, saved = self.request(
                "POST", "/api/android/calibrate-negative", payload
            )

        self.assertEqual(status, 200)
        self.assertEqual(saved["config"]["flip_template_dir"], str(expected_dir))
        self.assertEqual(saved["config"]["ticket_count"], 2)
        self.assertEqual(saved["config"]["buy_point"], "765,2556")
        self.assertEqual(calibrate.call_args.args[0], payload["device_name"])
        self.assertEqual(calibrate.call_args.kwargs["template_dir"], expected_dir)

    def test_android_negative_calibration_requires_detail_page(self):
        self.request("PUT", "/api/android/config", valid_android_config())
        with patch(
            "app.calibrate_negative_point",
            side_effect=ConfigError("当前页面不是演出详情页"),
        ):
            status, error = self.request(
                "POST",
                "/api/android/calibrate-negative",
                valid_android_config(),
            )
        self.assertEqual(status, 400)
        self.assertIn("详情页", error["error"])

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


    def test_check_android_surfaces_managed_appium_error(self):
        packaged = create_server(
            self.root,
            port=0,
            android_config_path=self.root / "data" / "config.jsonc",
            managed_server_url="http://127.0.0.1:1",
            runtime_status=lambda: {
                "state": "error",
                "message": "node exited 1: EACCES",
            },
        )
        try:
            report = {
                "ok": False,
                "checks": [
                    {
                        "id": "server",
                        "label": "Appium 服务",
                        "ok": False,
                        "message": "connection refused",
                    }
                ],
            }
            with patch.object(
                packaged.application.android_environment,
                "check",
                return_value=report,
            ):
                result = packaged.application.check_android()
            self.assertFalse(result["ok"])
            self.assertEqual(result["checks"][0]["id"], "runtime")
            self.assertIn("EACCES", result["checks"][0]["message"])
        finally:
            packaged.server_close()

    def test_pc_only_application_hides_android_and_rejects_android_api(self):
        packaged = create_server(self.root, port=0, pc_only=True)
        port = packaged.server_address[1]
        thread = threading.Thread(target=packaged.serve_forever, daemon=True)
        thread.start()
        try:
            status = packaged.application.status()
            self.assertEqual(status["enabled_platforms"], ["pc"])
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request("GET", "/api/android/config")
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
            self.assertEqual(response.status, 400)
            self.assertIn("仅包含 PC", payload["error"])
        finally:
            packaged.shutdown()
            packaged.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
