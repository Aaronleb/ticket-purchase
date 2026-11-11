"""adb 批量点击注入。

关键路径上每个 ``mobile: clickGesture`` 都是一次 Appium HTTP 往返（通常 40–100ms）。
当多个点击发生在同一页面（主要是观演人勾选、同一时刻的多档尝试）时，可以合并成
一条 ``adb shell`` 命令一次下发，耗时接近单次 adb 调用。

约束：

- 只在同一页面内的点击做合并，跨页面跳转必须分开下发；
- adb 不可用时返回失败，调用方回退到 Appium 逐次点击；
- 每次下发前等待的时间由调用方给出，失败不重试、不补点。
"""

from __future__ import annotations

import shutil
import subprocess


class AdbTapError(RuntimeError):
    pass


def find_adb():
    """返回可用的 adb 路径；打包环境里由 desktop_app 注入 PATH。"""
    return shutil.which("adb")


def build_tap_script(points, delay=0.15):
    """把一组坐标编译成一条 shell 脚本，坐标之间 sleep 固定间隔。"""
    commands = []
    for index, (x, y) in enumerate(points):
        if index:
            commands.append(f"sleep {delay:.2f}")
        commands.append(f"input tap {int(x)} {int(y)}")
    return "; ".join(commands)


def batch_tap(points, delay=0.15, device=None, adb_path=None, timeout=8):
    """一条 adb 命令完成多次坐标点击。

    返回实际下发的命令条数；失败抛出 :class:`AdbTapError`，由调用方决定降级。
    """
    if not points:
        return 0
    adb = adb_path or find_adb()
    if not adb:
        raise AdbTapError("未找到 adb，无法批量点击")

    script = build_tap_script(points, delay)
    command = [adb]
    if device:
        command.extend(["-s", device])
    command.extend(["shell", script])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdbTapError(f"adb 批量点击执行失败：{exc}") from exc
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "adb 返回非零退出码"
        raise AdbTapError(f"adb 批量点击失败：{message}")
    return len(points)
