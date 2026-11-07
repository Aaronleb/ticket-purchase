import subprocess
import unittest
from unittest.mock import MagicMock, patch

from android_environment import AndroidEnvironment
from test_android_config import valid_android_config


class AndroidEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.environment = AndroidEnvironment(timeout=0.1)
        self.config = valid_android_config()

    def appium_response(self):
        response = MagicMock()
        response.read.return_value = b'{"value": {"ready": true}}'
        response.__enter__.return_value = response
        return response

    def test_all_checks_pass(self):
        processes = [
            subprocess.CompletedProcess([], 0, "List of devices attached\nemulator-5554\tdevice\n", ""),
            subprocess.CompletedProcess([], 0, "package:cn.damai\n", ""),
        ]
        with (
            patch("android_environment.importlib.util.find_spec", return_value=object()),
            patch("android_environment.shutil.which", return_value="/usr/bin/adb"),
            patch("android_environment.urlopen", return_value=self.appium_response()),
            patch("android_environment.subprocess.run", side_effect=processes) as run,
        ):
            report = self.environment.check(self.config)
        self.assertTrue(report["ok"])
        self.assertEqual(len(report["checks"]), 5)
        self.assertEqual(run.call_args_list[1].args[0][1:4], ["-s", "emulator-5554", "shell"])

    def test_missing_adb_fails_dependent_checks(self):
        with (
            patch("android_environment.importlib.util.find_spec", return_value=object()),
            patch("android_environment.shutil.which", return_value=None),
            patch("android_environment.urlopen", return_value=self.appium_response()),
        ):
            report = self.environment.check(self.config)
        self.assertFalse(report["ok"])
        self.assertEqual([check["ok"] for check in report["checks"]], [True, True, False, False, False])

    def test_offline_or_mismatched_device_is_reported(self):
        result = subprocess.CompletedProcess([], 0, "List of devices attached\nother-device\tdevice\n", "")
        with (
            patch("android_environment.importlib.util.find_spec", return_value=object()),
            patch("android_environment.shutil.which", return_value="/usr/bin/adb"),
            patch("android_environment.urlopen", return_value=self.appium_response()),
            patch("android_environment.subprocess.run", return_value=result),
        ):
            report = self.environment.check(self.config)
        device = next(check for check in report["checks"] if check["id"] == "device")
        self.assertFalse(device["ok"])
        self.assertIn("other-device", device["message"])

    def test_appium_connection_failure_is_actionable(self):
        with (
            patch("android_environment.importlib.util.find_spec", return_value=None),
            patch("android_environment.shutil.which", return_value=None),
            patch("android_environment.urlopen", side_effect=OSError("offline")),
        ):
            report = self.environment.check(self.config)
        server = next(check for check in report["checks"] if check["id"] == "server")
        self.assertFalse(server["ok"])
        self.assertIn("/status", server["message"])

    def test_list_devices_reads_version_and_reports_unauthorized(self):
        processes = [
            subprocess.CompletedProcess(
                [], 0, "List of devices attached\nphone-1\tdevice\nphone-2\tunauthorized\n", ""
            ),
            subprocess.CompletedProcess([], 0, "15\n", ""),
            subprocess.CompletedProcess([], 0, "package:cn.damai\n", ""),
        ]
        with (
            patch("android_environment.shutil.which", return_value="/runtime/adb"),
            patch("android_environment.subprocess.run", side_effect=processes),
        ):
            report = self.environment.list_devices()
        self.assertTrue(report["ok"])
        self.assertEqual(report["devices"][0]["platform_version"], "15")
        self.assertTrue(report["devices"][0]["app_installed"])
        self.assertFalse(report["devices"][1]["online"])
        self.assertIn("unauthorized", report["message"])

    def test_list_devices_reports_missing_adb(self):
        with patch("android_environment.shutil.which", return_value=None):
            report = self.environment.list_devices()
        self.assertFalse(report["ok"])
        self.assertIn("ADB", report["message"])


if __name__ == "__main__":
    unittest.main()
