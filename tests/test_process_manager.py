import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import process_manager
from process_manager import ProcessManager, TaskBusy


class ProcessManagerTests(unittest.TestCase):
    def setUp(self):
        self.manager = ProcessManager()
        self.temporary = tempfile.TemporaryDirectory()
        self.cwd = Path(self.temporary.name)

    def tearDown(self):
        self.manager.shutdown()
        self.temporary.cleanup()

    def wait_for_phase(self, phase, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.manager.snapshot()["phase"] == phase:
                return self.manager.snapshot()
            time.sleep(0.02)
        self.fail(f"等待状态 {phase} 超时，当前状态：{self.manager.snapshot()}")

    def start_script(self, source, task="check"):
        return self.manager.start(task, [sys.executable, "-c", source], self.cwd)

    def test_successful_process_streams_logs_and_exits(self):
        self.start_script("print('hello from child', flush=True)")
        status = self.wait_for_phase("idle")
        self.assertEqual(status["exit_code"], 0)
        self.assertFalse(status["running"])
        self.assertIn("hello from child", [entry["text"] for entry in self.manager.logs()])

    def test_nonzero_exit_is_error(self):
        self.start_script("raise SystemExit(3)")
        status = self.wait_for_phase("error")
        self.assertEqual(status["exit_code"], 3)
        self.assertIn("异常退出", status["message"])

    def test_duplicate_start_is_rejected_and_stop_terminates_process_group(self):
        self.start_script("import time; time.sleep(30)")
        with self.assertRaises(TaskBusy):
            self.start_script("print('duplicate')")
        self.manager.stop()
        status = self.wait_for_phase("stopped")
        self.assertFalse(status["running"])
        self.assertEqual(status["message"], "任务已停止")

    def test_login_completion_writes_signal(self):
        self.start_script("import time; time.sleep(30)", task="login")
        signal = self.cwd / "login.signal"
        self.manager.complete_login(signal)
        self.assertEqual(signal.read_text(encoding="utf-8"), "done\n")

    def test_windows_stop_uses_taskkill_tree(self):
        proc = SimpleNamespace(pid=4321)
        with patch.object(process_manager.os, "name", "nt"), patch.object(
            process_manager.subprocess, "run"
        ) as run:
            self.manager._terminate_process_group(proc)

        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["taskkill", "/PID"])
        self.assertIn("/T", command)
        self.assertIn("/F", command)


if __name__ == "__main__":
    unittest.main()
