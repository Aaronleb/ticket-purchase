"""开售前建立常驻 ADB Shell，关键路径不重复创建 ADB 进程。"""
from __future__ import annotations

import itertools
import select
import shutil
import subprocess
import time


class AdbTapError(RuntimeError):
    pass


def build_tap_script(points, delay=0.06):
    commands = []
    for index, (x, y) in enumerate(points):
        if index:
            commands.append(f"sleep {delay:.2f}")
        commands.append(f"input tap {int(x)} {int(y)}")
    return "; ".join(commands)


class PersistentAdbShell:
    def __init__(self, device=None, adb_path=None, timeout=1.5):
        self.device = device
        self.adb_path = adb_path or shutil.which("adb")
        self.timeout = timeout
        self.process = None
        self._tokens = itertools.count()

    def start(self):
        if not self.adb_path:
            raise AdbTapError("未找到 adb")
        command = [self.adb_path]
        if self.device:
            command.extend(["-s", self.device])
        command.append("shell")
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            self.run("true")
        except (OSError, AdbTapError) as exc:
            self.close()
            raise AdbTapError(f"常驻 ADB Shell 启动失败：{exc}") from exc
        return self

    def run(self, script):
        if self.process is None or self.process.poll() is not None:
            raise AdbTapError("常驻 ADB Shell 不可用")
        token = f"__DAMAI_DONE_{next(self._tokens)}__"
        try:
            self.process.stdin.write(f"{script}; echo {token}\n")
            self.process.stdin.flush()
        except (OSError, BrokenPipeError) as exc:
            raise AdbTapError(f"ADB 命令写入失败：{exc}") from exc
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AdbTapError("ADB 命令执行超时")
            readable, _, _ = select.select([self.process.stdout], [], [], remaining)
            if not readable:
                continue
            line = self.process.stdout.readline()
            if line == "":
                raise AdbTapError("常驻 ADB Shell 已退出")
            if token in line:
                return
            if line.startswith("error:") or "Permission denied" in line:
                raise AdbTapError(line.strip())

    def tap(self, point):
        self.run(build_tap_script([point]))

    def batch_tap(self, points, delay=0.06):
        if points:
            self.run(build_tap_script(points, delay))

    def close(self):
        process = self.process
        self.process = None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
