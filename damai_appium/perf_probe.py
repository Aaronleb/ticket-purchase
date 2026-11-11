"""Appium 命令级耗时探测。

抢票耗时绝大部分由 WebDriver 往返次数 × 单次往返耗时组成，而单次往返耗时
随设备、系统版本和 UiAutomator2 版本差异很大。优化之前先测量，避免凭经验
估算。本模块提供两件事：

1. ``TimedDriver``：透明包装 webdriver 对象，记录每次命令的类型和耗时。
2. ``report()``：按阶段、按命令类型汇总，输出可直接对比的文本和 CSV。

不改变任何业务行为；未启用时只有极小的判断开销。
"""

from __future__ import annotations

import csv
import time
from collections import defaultdict
from pathlib import Path

COMMAND_KINDS = (
    "find_element",
    "find_elements",
    "get_attribute",
    "execute_script",
    "page_source",
    "screenshot",
    "other",
)


class PerfRecorder:
    """按阶段记录命令耗时，阶段由业务代码显式切换。"""

    def __init__(self):
        self.stage = "startup"
        self._rows: list[dict] = []
        self._stage_entered_at = time.perf_counter()
        self._closed_stages: list[dict] = []

    def set_stage(self, name: str) -> None:
        now = time.perf_counter()
        self._closed_stages.append(
            {"name": self.stage, "duration": now - self._stage_entered_at}
        )
        self.stage = name
        self._stage_entered_at = now

    def record(self, kind: str, duration: float, label: str = "") -> None:
        self._rows.append(
            {
                "stage": self.stage,
                "kind": kind,
                "duration": duration,
                "label": label,
            }
        )

    @property
    def rows(self) -> list[dict]:
        return list(self._rows)

    def stage_durations(self) -> list[dict]:
        now = time.perf_counter()
        closed = list(self._closed_stages)
        closed.append({"name": self.stage, "duration": now - self._stage_entered_at})
        return closed

    def command_count(self, stage: str | None = None) -> int:
        if stage is None:
            return len(self._rows)
        return sum(1 for row in self._rows if row["stage"] == stage)

    def summary(self) -> dict:
        by_stage = defaultdict(lambda: {"count": 0, "duration": 0.0})
        by_kind = defaultdict(lambda: {"count": 0, "duration": 0.0})
        for row in self._rows:
            for bucket, key in ((by_stage, row["stage"]), (by_kind, row["kind"])):
                bucket[key]["count"] += 1
                bucket[key]["duration"] += row["duration"]

        def averages(source):
            return {
                key: {
                    "count": value["count"],
                    "total": round(value["duration"], 3),
                    "average_ms": round(value["duration"] / value["count"] * 1000, 1),
                }
                for key, value in source.items()
            }

        return {
            "commands": len(self._rows),
            "stages": averages(by_stage),
            "kinds": averages(by_kind),
            "timeline": [
                {"name": item["name"], "seconds": round(item["duration"], 3)}
                for item in self.stage_durations()
            ],
        }

    def format_report(self) -> str:
        summary = self.summary()
        lines = [
            f"Appium 命令总数: {summary['commands']}",
            "按命令类型:",
            f"  {'类型':<16}{'次数':>6}{'合计(s)':>10}{'均值(ms)':>12}",
        ]
        for kind, item in sorted(
            summary["kinds"].items(), key=lambda pair: pair[1]["total"], reverse=True
        ):
            lines.append(
                f"  {kind:<16}{item['count']:>6}{item['total']:>10.3f}{item['average_ms']:>12.1f}"
            )
        lines.append("按阶段:")
        lines.append(f"  {'阶段':<20}{'命令数':>8}{'合计(s)':>10}")
        for stage, item in summary["stages"].items():
            lines.append(f"  {stage:<20}{item['count']:>8}{item['total']:>10.3f}")
        lines.append("阶段耗时（含等待）:")
        for item in summary["timeline"]:
            lines.append(f"  {item['name']:<20}{item['seconds']:>10.3f}s")
        return "\n".join(lines)

    def write_csv(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=["stage", "kind", "duration", "label"]
            )
            writer.writeheader()
            for row in self._rows:
                writer.writerow(
                    {
                        "stage": row["stage"],
                        "kind": row["kind"],
                        "duration": f"{row['duration']:.6f}",
                        "label": row["label"],
                    }
                )


class TimedDriver:
    """包装 driver，把每次命令的耗时记到 PerfRecorder。

    只包装抢票关键路径上真正会走的方法；其余属性原样透传，保证兼容性。
    """

    def __init__(self, driver, recorder: PerfRecorder):
        object.__setattr__(self, "_driver", driver)
        object.__setattr__(self, "_recorder", recorder)

    # -- 被测量的命令 ---------------------------------------------------
    def _timed(self, kind, label, call):
        started = time.perf_counter()
        try:
            return call()
        finally:
            self._recorder.record(kind, time.perf_counter() - started, label)

    def find_element(self, by, value):
        return self._timed("find_element", _label(value), lambda: self._driver.find_element(by, value))

    def find_elements(self, by, value):
        return self._timed(
            "find_elements", _label(value), lambda: self._driver.find_elements(by, value)
        )

    def get_attribute(self, name):
        return self._timed("get_attribute", name, lambda: self._driver.get_attribute(name))

    def execute_script(self, script, *args):
        return self._timed(
            "execute_script", str(script), lambda: self._driver.execute_script(script, *args)
        )

    def get_page_source(self):
        return self._timed("page_source", "", lambda: self._driver.page_source)

    def get_screenshot_as_png(self):
        return self._timed("screenshot", "", lambda: self._driver.get_screenshot_as_png())

    def __getattr__(self, name):
        return getattr(self._driver, name)


def _label(value) -> str:
    text = str(value)
    if ":id/" in text:
        return text.split(":id/", 1)[1]
    return text[:40]


def install(driver, recorder: PerfRecorder) -> TimedDriver:
    """用 TimedDriver 包装已建立的会话。"""
    return TimedDriver(driver, recorder)
