# -*- coding: UTF-8 -*-
"""常驻 adb shell 会话测试（管道 + 线程模拟交互式 shell，不碰真机）。"""
import os
import threading

import pytest

from adb_session import AdbSessionError, AdbShellSession


class FakeShell:
    """行为像一个交互式 `adb shell`：从 stdin 读命令行，按脚本产出输出。

    script: list[(命令行包含此子串时, 产出的输出行列表)]，取首个匹配项。
    每个命令后跟的 ``echo __DAMAI_xxx__`` 由本 fake 直接回显标记行。
    """

    def __init__(self, script=None, hang_after=None):
        self._script = list(script or [])
        # hang_after=N：前 N 条命令正常响应，之后一律不响应（模拟设备挂起）
        self._hang_after = hang_after
        self._cmd_index = 0
        self.commands = []  # serve 线程收到的所有命令行
        self._stdin_r, self._stdin_w = os.pipe()
        self._stdout_r, self._stdout_w = os.pipe()
        self.stdin = os.fdopen(self._stdin_w, "w", buffering=1)
        self.stdout = os.fdopen(self._stdout_r, "r", buffering=1)
        self._writer = os.fdopen(self._stdout_w, "w", buffering=1)
        self.dead = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        # 模拟交互式 shell 的启动提示符
        self._writer.write("fake:/ $ \n")
        self._writer.flush()
        pending = []
        with os.fdopen(self._stdin_r, "r") as stdin:
            for line in stdin:
                line = line.strip()
                if line.startswith("echo __DAMAI_"):
                    if self._hang_after is not None and self._cmd_index > self._hang_after:
                        continue  # 挂起：标记也不回
                    marker = line.split()[1]
                    for out_line in pending:
                        self._writer.write(out_line + "\n")
                    pending = []
                    self._writer.write(marker + "\n")
                    self._writer.flush()
                else:
                    self._cmd_index += 1
                    if self._hang_after is not None and self._cmd_index > self._hang_after:
                        continue  # 挂起：命令照常消费但不产出
                    self.commands.append(line)
                    for key, out_lines in self._script:
                        if key in line:
                            pending.extend(out_lines)
                            break

    def poll(self):
        return 1 if self.dead else None

    def kill(self):
        self.dead = True
        for f in (self.stdin, self.stdout, self._writer):
            try:
                f.close()
            except OSError:
                pass


def make_session(mocker, shell):
    class ShellTransport:
        def write(self, data):
            shell.stdin.write(data.decode())
            shell.stdin.flush()

        @property
        def stdout_fd(self):
            return shell.stdout.fileno()

        def poll(self):
            return shell.poll()

        def close(self):
            shell.kill()

    return AdbShellSession(
        "fake-device", transport_factory=ShellTransport
    ), ShellTransport


def test_run_returns_output(mocker):
    shell = FakeShell(script=[("input tap", ["ok-line"])])
    session, _ = make_session(mocker, shell)

    out = session.run(("shell", "input", "tap", "100", "200"))

    assert out == "ok-line\n"
    # 命令确实经 stdin 下发（shell 前缀已剥掉；首条是 stty bootstrap）
    assert shell.commands[-1] == "input tap 100 200"


def test_shell_prefix_is_stripped(mocker):
    shell = FakeShell()
    session, _ = make_session(mocker, shell)

    session.run(("shell", "wm", "size"))

    assert shell.commands[-1] == "wm size"


@pytest.mark.skipif(os.name == "nt", reason="PTY 仅 POSIX 平台可用")
def test_pty_transport_spawns_adb_shell(mocker):
    import os
    import termios

    master, slave = os.pipe()
    mocker.patch("pty.openpty", return_value=(master, slave))
    mocker.patch.object(termios, "tcgetattr", return_value=[0, 1, 2, 3, 4, 5, 6])
    mocker.patch.object(termios, "tcsetattr")
    popen = mocker.patch("adb_session.subprocess.Popen")
    import adb_session

    adb_session.PtyTransport("dev-1", adb="/x/adb")

    assert popen.call_args.args[0] == ["/x/adb", "-s", "dev-1", "shell"]
    os.close(master)


def test_session_respawns_after_eof(mocker):
    dead = FakeShell()
    dead.kill()  # 首次 spawn 就已退出
    alive = FakeShell(script=[("dumpsys", ["topResumedActivity=xxx/.Main"])])
    transports = iter([transport_for(dead), transport_for(alive)])
    session = AdbShellSession(
        "fake-device", transport_factory=lambda: next(transports)
    )

    out = session.run(("dumpsys", "activity"))

    assert "Main" in out


def transport_for(shell):
    class T:
        def write(self, data):
            shell.stdin.write(data.decode())
            shell.stdin.flush()

        @property
        def stdout_fd(self):
            return shell.stdout.fileno()

        def poll(self):
            return shell.poll()

        def close(self):
            shell.kill()

    return T()


def test_persistent_failure_raises(mocker):
    shell = FakeShell()
    shell.kill()
    session = AdbShellSession(
        "fake-device", transport_factory=lambda: transport_for(shell)
    )

    with pytest.raises(AdbSessionError):
        session.run(("wm", "size"))


def test_multiline_output_framed(mocker):
    shell = FakeShell(script=[("dumpsys", ["line1", "line2", "line3"])])
    session, _ = make_session(mocker, shell)

    out = session.run(("dumpsys",))

    assert out == "line1\nline2\nline3\n"


def test_timeout_raises(mocker):
    shell = FakeShell(hang_after=0)  # 第一条命令就挂起
    session, _ = make_session(mocker, shell)

    with pytest.raises(AdbSessionError, match="连续失败"):
        session.run(("dumpsys",), timeout=0.2)
    shell.kill()


def test_no_replay_on_timeout_for_taps(mocker):
    """点击超时可能已经生效：replay=False 时只能发一次。"""
    session = AdbShellSession("fake-device")
    calls = []

    def fake_transact(payload, timeout):
        calls.append(payload)
        raise TimeoutError("adb shell 超时")

    mocker.patch.object(session, "_ensure")
    mocker.patch.object(session, "_transact", side_effect=fake_transact)

    with pytest.raises(AdbSessionError, match="连续失败"):
        session.run(("shell", "input", "tap", "1", "2"), replay=False)

    assert len(calls) == 1


def test_query_commands_still_replay_on_timeout(mocker):
    """查询类命令超时重放是安全的，保留一次重建重试。"""
    session = AdbShellSession("fake-device")
    calls = []

    def fake_transact(payload, timeout):
        calls.append(payload)
        raise TimeoutError("adb shell 超时")

    mocker.patch.object(session, "_ensure")
    mocker.patch.object(session, "_transact", side_effect=fake_transact)

    with pytest.raises(AdbSessionError, match="连续失败"):
        session.run(("shell", "dumpsys", "activity"))

    assert len(calls) == 2


def test_repeated_runs_reuse_session(mocker):
    shell = FakeShell(
        script=[(f"echo-test-{i}", [f"r{i}"]) for i in range(3)]
    )
    spawned = {"n": 0}

    class CountingTransport:
        def __init__(self):
            spawned["n"] += 1
            self._inner = transport_for(shell)

        def write(self, data):
            self._inner.write(data)

        @property
        def stdout_fd(self):
            return self._inner.stdout_fd

        def poll(self):
            return self._inner.poll()

        def close(self):
            self._inner.close()

    session = AdbShellSession(
        "fake-device", transport_factory=CountingTransport
    )

    for i in range(3):
        assert f"r{i}" in session.run((f"echo-test-{i}",))
    assert spawned["n"] == 1  # 只 spawn 一次
    shell.kill()
