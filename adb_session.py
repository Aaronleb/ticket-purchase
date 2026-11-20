# -*- coding: UTF-8 -*-
"""常驻 adb shell 会话。

每次 ``adb shell xxx`` 都要新建进程，仅客户端握手就约 34ms；
抢票关键路径上有 8~10 次 adb 操作（点击、dumpsys 轮询等）。
这里保持一个 ``adb -s <device> shell`` 长连接，命令直接下发、
输出按标记帧界定，单次操作降到 ~10-15ms。

两个实现要点（都是实测踩出来的）：
- adb shell 的 stdin/stdout 不是 TTY 时会直接退出，所以用 PTY 把三者
  都绑到伪终端，并在 master 上关掉本地回显（ECHO）和换行转换（OPOST）；
- 不能用带缓冲的 readline + select（缓冲层会把标记行预读进用户态，
  导致 select 永远等不到），必须底层 os.read 自行按行切分。

会话异常自动重建一次，仍失败抛 AdbSessionError，调用方退回单次调用。
"""
from __future__ import annotations

import os
import pty
import selectors
import shlex
import subprocess
import time
import uuid


class AdbSessionError(RuntimeError):
    """常驻 adb 会话不可用。"""


class PtyTransport:
    """真实传输层：PTY 伪终端 + adb shell 子进程。

    adb shell 要求 stdin/stdout 都是 TTY 才保持交互式，且输入回显是
    本地 PTY 线路做的——所以三者都绑到 PTY slave，并在 master 上关
    掉 ECHO（命令行不混进输出）和 OPOST（\\n 不被转成 \\r\\n）。
    读写共用 master fd，shell 的 stderr 也合并进来。
    """

    def __init__(self, device: str, adb: str = "adb"):
        import termios

        master, slave = pty.openpty()
        attrs = termios.tcgetattr(master)
        attrs[1] &= ~termios.OPOST  # oflag：输出不做 \n -> \r\n 转换
        attrs[3] &= ~termios.ECHO  # lflag：输入不本地回显
        termios.tcsetattr(master, termios.TCSANOW, attrs)
        try:
            self._proc = subprocess.Popen(
                [adb, "-s", device, "shell"],
                stdin=slave, stdout=slave, stderr=slave,
                start_new_session=True,
            )
        finally:
            os.close(slave)
        self._fd = master

    def write(self, data: bytes) -> None:
        os.write(self._fd, data)

    @property
    def stdout_fd(self) -> int:
        return self._fd

    def poll(self):
        return self._proc.poll()

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass
        try:
            self._proc.kill()
        except OSError:
            pass


class AdbShellSession:
    def __init__(self, device: str, adb: str = "adb", transport_factory=None):
        self._factory = transport_factory or (
            lambda: PtyTransport(device, adb)
        )
        self._t = None

    def _ensure(self):
        if self._t is None or self._t.poll() is not None:
            self._t = self._factory()
            self._wait_shell_ready()

    def _wait_shell_ready(self, timeout: float = 5.0):
        """等设备 shell 起来（首条提示符出现），丢弃启动噪音。"""
        fd = self._t.stdout_fd
        sel = selectors.DefaultSelector()
        try:
            sel.register(fd, selectors.EVENT_READ)
            deadline = time.monotonic() + timeout
            seen = bytearray()
            while time.monotonic() < deadline:
                if not sel.select(0.2):
                    if seen and b"$ " in seen:
                        return
                    continue
                chunk = os.read(fd, 65536)
                if not chunk:
                    raise AdbSessionError("adb shell 会话已退出")
                seen += chunk
                if b"$ " in seen or b"# " in seen:
                    return
            raise AdbSessionError("等待设备 shell 就绪超时")
        finally:
            sel.close()

    def close(self):
        t, self._t = self._t, None
        if t is not None:
            t.close()

    def run(self, args, timeout: float = 10.0, replay: bool = True) -> str:
        """在设备 shell 里执行命令，返回 stdout 文本。

        args 与 ``adb -s <device>`` 的用法一致（允许以 "shell" 开头，
        会剥掉），例如 ("shell", "input", "tap", "100", "100")。

        replay=False 用于点击/返回这类非幂等命令：超时说明命令可能已经
        送达设备，再发一次就会变成双击（提交按钮尤其不能重放），所以
        只抛错不重试，交给调用方按页面状态判断。
        """
        cmd = [str(a) for a in args]
        if cmd and cmd[0] == "shell":
            cmd = cmd[1:]
        line = shlex.join(cmd)
        last_exc = None
        for _attempt in range(2):  # 失败重建一次会话再试
            try:
                self._ensure()
                return self._transact(
                    f"{line}\necho {self._marker()}\n".encode(), timeout
                )
            except (AdbSessionError, TimeoutError, OSError, ValueError) as exc:
                last_exc = exc
                self.close()
                if not replay:
                    break
        raise AdbSessionError(f"adb shell 连续失败：{last_exc}")

    @staticmethod
    def _marker() -> str:
        return f"__DAMAI_{uuid.uuid4().hex}__"

    def _transact(self, payload: bytes, timeout: float) -> str:
        """发一命令 + 标记，读到标记行返回之前的输出。"""
        fd = self._t.stdout_fd
        sel = selectors.DefaultSelector()
        try:
            sel.register(fd, selectors.EVENT_READ)
            self._t.write(payload)
            deadline = time.monotonic() + timeout
            buf = bytearray()
            out_lines = []
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("adb shell 超时")
                if not sel.select(remaining):
                    raise TimeoutError("adb shell 超时")
                chunk = os.read(fd, 65536)
                if not chunk:  # EOF：shell 已退出
                    raise AdbSessionError("adb shell 会话已退出")
                buf += chunk
                while b"\n" in buf:
                    raw, _, buf = buf.partition(b"\n")
                    text = raw.rstrip(b"\r").decode(errors="replace")
                    if text.startswith("__DAMAI_") and text.endswith("__"):
                        return "".join(out_lines)
                    out_lines.append(text + "\n")
        finally:
            sel.close()
