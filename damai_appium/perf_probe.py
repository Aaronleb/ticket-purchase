"""Appium 命令与业务阶段耗时记录；通过 DAMAI_PROFILE=1 启用。"""
from __future__ import annotations

import os
import time
from collections import defaultdict


def profile_enabled():
    return os.environ.get("DAMAI_PROFILE") == "1"


class PerfRecorder:
    def __init__(self):
        self.stage = "startup"
        self.rows = []
        self._entered = time.perf_counter()
        self.timeline = []

    def set_stage(self, name):
        now = time.perf_counter()
        self.timeline.append({"name": self.stage, "seconds": now - self._entered})
        self.stage = name
        self._entered = now

    def record(self, kind, duration, label=""):
        self.rows.append(
            {"stage": self.stage, "kind": kind, "duration": duration, "label": label}
        )

    def summary(self):
        stages = defaultdict(lambda: {"count": 0, "duration": 0.0})
        kinds = defaultdict(lambda: {"count": 0, "duration": 0.0})
        for row in self.rows:
            for bucket, key in ((stages, row["stage"]), (kinds, row["kind"])):
                bucket[key]["count"] += 1
                bucket[key]["duration"] += row["duration"]
        timeline = list(self.timeline)
        timeline.append(
            {"name": self.stage, "seconds": time.perf_counter() - self._entered}
        )
        return {"commands": len(self.rows), "stages": stages, "kinds": kinds, "timeline": timeline}

    def format_report(self):
        summary = self.summary()
        lines = [f"Appium 命令总数: {summary['commands']}", "按命令类型:"]
        for kind, item in sorted(
            summary["kinds"].items(), key=lambda pair: pair[1]["duration"], reverse=True
        ):
            average = item["duration"] / item["count"] * 1000
            lines.append(
                f"  {kind}: {item['count']} 次，合计 {item['duration']:.3f}s，均值 {average:.1f}ms"
            )
        lines.append("阶段耗时（含等待）:")
        for item in summary["timeline"]:
            lines.append(f"  {item['name']}: {item['seconds']:.3f}s")
        return "\n".join(lines)


class TimedDriver:
    def __init__(self, driver, recorder):
        object.__setattr__(self, "_driver", driver)
        object.__setattr__(self, "_recorder", recorder)

    def _timed(self, kind, label, call):
        started = time.perf_counter()
        try:
            return call()
        finally:
            self._recorder.record(kind, time.perf_counter() - started, label)

    def find_element(self, by, value):
        return self._timed("find_element", str(value), lambda: self._driver.find_element(by, value))

    def find_elements(self, by, value=None, **kwargs):
        value = value or kwargs.get("value")
        return self._timed(
            "find_elements", str(value), lambda: self._driver.find_elements(by, value)
        )

    def execute_script(self, script, *args):
        return self._timed(
            "execute_script", str(script), lambda: self._driver.execute_script(script, *args)
        )

    def __getattr__(self, name):
        return getattr(self._driver, name)


def install(driver, recorder):
    return TimedDriver(driver, recorder)
