import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from appium_service import AppiumService


class AppiumServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.node = self.root / "node"
        self.entry = self.root / "appium.js"
        self.node.write_text("", encoding="utf-8")
        self.entry.write_text("", encoding="utf-8")
        self.service = AppiumService(
            self.node,
            self.entry,
            self.root / "appium-home",
            self.root / "logs" / "appium.log",
            startup_timeout=0.1,
        )

    def tearDown(self):
        self.service._close_log()
        self.temporary.cleanup()

    def process(self):
        proc = MagicMock()
        proc.pid = 12345
        proc.poll.return_value = None
        return proc

    def test_start_uses_embedded_runtime_and_reports_ready(self):
        proc = self.process()
        with (
            patch.dict(
                "appium_service.os.environ",
                {"ANDROID_HOME": "/bundled/android", "ANDROID_SDK_ROOT": "/bundled/android"},
            ),
            patch("appium_service.subprocess.Popen", return_value=proc) as popen,
            patch.object(self.service, "_ready", return_value=True),
        ):
            status = self.service.start()
        self.assertEqual(status["state"], "ready")
        command = popen.call_args.args[0]
        self.assertEqual(command[:2], [str(self.node), str(self.entry)])
        self.assertIn(str(self.service.port), command)
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(environment["APPIUM_HOME"], str(self.root / "appium-home"))
        self.assertEqual(environment["ANDROID_HOME"], "/bundled/android")
        self.assertEqual(environment["ANDROID_SDK_ROOT"], "/bundled/android")

    def test_early_process_exit_is_an_error(self):
        proc = self.process()
        proc.poll.return_value = 2
        proc.returncode = 2
        with (
            patch("appium_service.subprocess.Popen", return_value=proc),
            patch.object(self.service, "_ready", return_value=False),
        ):
            status = self.service.start()
        self.assertEqual(status["state"], "error")
        self.assertIn("代码 2", status["message"])

    def test_snapshot_detects_later_process_exit(self):
        proc = self.process()
        with (
            patch("appium_service.subprocess.Popen", return_value=proc),
            patch.object(self.service, "_ready", return_value=True),
        ):
            self.service.start()
        proc.poll.return_value = 9
        proc.returncode = 9
        status = self.service.snapshot()
        self.assertEqual(status["state"], "error")
        self.assertIn("代码 9", status["message"])

    def test_missing_embedded_node_fails_without_launching(self):
        self.node.unlink()
        with patch("appium_service.subprocess.Popen") as popen:
            status = self.service.start()
        self.assertEqual(status["state"], "error")
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
