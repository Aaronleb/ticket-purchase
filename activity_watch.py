# -*- coding: UTF-8 -*-
"""事件驱动的 Activity 前台/首帧感知。

原实现用 `dumpsys activity activities` 轮询判断“页面出来没有”：
单次约 40ms，关键路径上每 20~30ms 跑一次，确认页加载的 1~2 秒里
要跑几十上百次，既慢又会和 App 抢 CPU。

这里常驻一条 ``adb logcat``，只解析 ActivityTaskManager 的
``START`` / ``Displayed`` / ``Fully drawn`` 事件：

- ``START``：页面开始启动（约等于 topResumedActivity 变化）
- ``Displayed`` / ``Fully drawn``：首帧已绘制（约等于 reportedDrawn=true）

等待变成 Condition 事件，事件一到立即返回；logcat 不可用时调用方
自动退回原来的 dumpsys 轮询。
"""
from __future__ import annotations

import re
import subprocess
import threading
import time

# Displayed cn.damai/.trade.order.DmOrderActivity: +1s234ms
_DISPLAYED_RE = re.compile(
    r"(?:Displayed|Fully drawn)\s+([\w.$]+)/([\w.$]+)"
)
# START u0 {act=... cmp=cn.damai/.trade.order.DmOrderActivity ...}
_START_RE = re.compile(
    r"\bSTART\s+u\d+\s+\{[^}]*cmp=([\w.$]+)/([\w.$]+)"
)
# 最多保留多少条事件，避免长时间运行时无限增长
_MAX_EVENTS = 256
# 事件名统一取类名最后一段（DmOrderActivity / NcovSkuActivity）
_SHORT_NAME_RE = re.compile(r"([\w$]+)$")


def _short_name(component_class: str) -> str:
    match = _SHORT_NAME_RE.search(component_class or "")
    return match.group(1) if match else (component_class or "")


def parse_activity_line(line: str):
    """从一行 logcat 里解析出 (事件类型, Activity 短类名)。

    事件类型：``started`` / ``displayed``；不是 Activity 事件时返回 None。
    """
    if not line:
        return None
    match = _DISPLAYED_RE.search(line)
    if match:
        return "displayed", _short_name(match.group(2))
    match = _START_RE.search(line)
    if match:
        return "started", _short_name(match.group(2))
    return None


class ActivityWatcher:
    """常驻 logcat，提供毫秒级的页面启动/首帧事件等待。"""

    def __init__(self, device: str, adb: str = "adb", popen_factory=None):
        self._device = device
        self._adb = adb
        self._popen_factory = popen_factory or self._spawn
        self._proc = None
        self._thread = None
        self._cond = threading.Condition()
        self._events = []  # [(monotonic, kind, short_name, raw_line)]
        self._running = False
        self._started_at = 0.0
        self._error = ""

    # ---------- 生命周期 ----------
    def _spawn(self):
        return subprocess.Popen(
            [
                self._adb, "-s", self._device,
                "logcat", "-T", "1", "-v", "brief",
                "-s", "ActivityTaskManager:I", "ActivityManager:I",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

    def start(self) -> bool:
        """启动 logcat 读取线程；失败返回 False，由调用方退回 dumpsys。"""
        if self._proc is not None:
            return self._running
        try:
            proc = self._popen_factory()
        except Exception as exc:  # noqa: BLE001 - 任何启动失败都只降级
            self._error = f"{type(exc).__name__}: {exc}"
            return False
        if proc is None or getattr(proc, "stdout", None) is None:
            self._error = "logcat 未返回输出"
            return False
        self._proc = proc
        self._running = True
        self._started_at = time.monotonic()
        self._thread = threading.Thread(
            target=self._read_loop, name="activity-watch", daemon=True
        )
        self._thread.start()
        return True

    def _read_loop(self):
        proc = self._proc
        # logcat -T 1 会把启动前的最后一条事件也带回来；它没有可靠时间戳，
        # 直接丢弃第一条能解析的 Activity 事件，避免把上一轮的 DmOrderActivity
        # 误当成这一轮的确认页（感知线程在开售前 30 秒就启动，丢一条无影响）。
        primed = False
        try:
            for line in proc.stdout:
                if not primed:
                    if parse_activity_line(line) is None:
                        continue
                    primed = True
                    continue
                self.feed_line(line)
        except (OSError, ValueError):
            pass
        finally:
            with self._cond:
                self._running = False
                self._cond.notify_all()

    def stop(self):
        proc, self._proc = self._proc, None
        with self._cond:
            self._running = False
            self._cond.notify_all()
        if proc is None:
            return
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001 - 清理失败不影响主流程
            pass
        try:
            proc.wait(timeout=1)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    # ---------- 事件 ----------
    def feed_line(self, line: str) -> bool:
        """喂入一行 logcat；测试也直接调用它，不依赖真实 adb。"""
        parsed = parse_activity_line(line)
        if parsed is None:
            return False
        kind, name = parsed
        now = time.monotonic()
        with self._cond:
            self._events.append((now, kind, name, line.rstrip()))
            if len(self._events) > _MAX_EVENTS:
                del self._events[: _MAX_EVENTS // 2]
            self._cond.notify_all()
        return True

    def _wait(self, kinds, markers, timeout: float, since=None) -> str:
        deadline = time.monotonic() + max(timeout, 0.0)
        markers = tuple(markers)
        with self._cond:
            while True:
                for ts, kind, name, _raw in reversed(self._events):
                    if since is not None and ts < since:
                        break
                    if kind in kinds:
                        for marker in markers:
                            if marker in name:
                                return marker
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._running:
                    return ""
                self._cond.wait(remaining)

    def wait_any_started(self, markers, timeout: float, since=None) -> str:
        """等任一 Activity 启动事件，返回命中的 marker。"""
        return self._wait(("started",), markers, timeout, since)

    def wait_any_displayed(self, markers, timeout: float, since=None) -> str:
        """等任一 Activity 首帧事件，返回命中的 marker。"""
        return self._wait(("displayed",), markers, timeout, since)

    def wait_displayed(self, marker: str, timeout: float, since=None) -> bool:
        return bool(self.wait_any_displayed((marker,), timeout, since))

    def seen_displayed(self, marker: str, since=None) -> bool:
        """不等待，只查事件里是否已经有该页首帧。"""
        with self._cond:
            for ts, kind, name, _raw in reversed(self._events):
                if since is not None and ts < since:
                    break
                if kind == "displayed" and marker in name:
                    return True
        return False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def error(self) -> str:
        return self._error
