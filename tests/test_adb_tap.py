from unittest.mock import Mock

from damai_appium.adb_tap import PersistentAdbShell, build_tap_script


def test_build_tap_script_preserves_order_and_delay():
    assert build_tap_script([(10, 20), (30, 40)], delay=0.05) == (
        "input tap 10 20; sleep 0.05; input tap 30 40"
    )


def test_persistent_shell_reuses_one_process(mocker):
    process = Mock()
    process.poll.return_value = None
    process.stdout.readline.side_effect = [
        "__DAMAI_DONE_0__\n",
        "__DAMAI_DONE_1__\n",
    ]
    popen = mocker.patch(
        "damai_appium.adb_tap.subprocess.Popen", return_value=process
    )
    mocker.patch(
        "damai_appium.adb_tap.select.select",
        side_effect=lambda streams, *_: (streams, [], []),
    )

    shell = PersistentAdbShell(device="device-1", adb_path="/adb").start()
    shell.batch_tap([(10, 20), (30, 40)], delay=0.04)

    popen.assert_called_once()
    writes = [call.args[0] for call in process.stdin.write.call_args_list]
    assert writes[0] == "true; echo __DAMAI_DONE_0__\n"
    assert writes[1] == (
        "input tap 10 20; sleep 0.04; input tap 30 40; "
        "echo __DAMAI_DONE_1__\n"
    )


def test_empty_batch_does_not_run_command():
    shell = PersistentAdbShell(adb_path="/adb")
    shell.run = Mock()

    shell.batch_tap([])

    shell.run.assert_not_called()
