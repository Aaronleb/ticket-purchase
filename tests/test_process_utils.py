import subprocess
from unittest.mock import MagicMock, patch

import process_utils


def test_background_popen_kwargs_uses_posix_process_group():
    with patch("process_utils.os.name", "posix"):
        assert process_utils.background_popen_kwargs() == {"start_new_session": True}


def test_background_popen_kwargs_hides_windows_console():
    with patch("process_utils.os.name", "nt"):
        options = process_utils.background_popen_kwargs()
    assert "start_new_session" not in options
    assert options["creationflags"] & getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    assert options["creationflags"] & getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def test_terminate_process_tree_uses_windows_taskkill():
    proc = MagicMock()
    proc.pid = 123
    proc.poll.return_value = None
    with (
        patch("process_utils.os.name", "nt"),
        patch("process_utils.subprocess.run") as run,
    ):
        process_utils.terminate_process_tree(proc)
    assert run.call_args.args[0][:2] == ["taskkill", "/PID"]
    assert run.call_args.args[0][2:] == ["123", "/T", "/F"]
    proc.wait.assert_called_once_with(timeout=4)


def test_terminate_process_tree_uses_posix_process_group():
    proc = MagicMock()
    proc.pid = 123
    proc.poll.return_value = None
    with (
        patch("process_utils.os.name", "posix"),
        patch("process_utils.os.getpgid", return_value=123, create=True),
        patch("process_utils.os.killpg", create=True) as killpg,
    ):
        process_utils.terminate_process_tree(proc)
    killpg.assert_called_once_with(123, process_utils.signal.SIGTERM)
    proc.wait.assert_called_once_with(timeout=4)
