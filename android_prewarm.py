# -*- coding: UTF-8 -*-
"""票档页预热：在非目标演出详情页上打开/返回原生票档页若干次。

只做导航：不选票档、不点「确定」、不下单。目的只是让 NcovSkuActivity
及其布局、字体、Canvas 首次加载完成，正式开抢时第一次打开票档页更快。
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from config_store import ConfigError

OCR_TOOL = Path(__file__).resolve().parent / "tools" / "damai_ocr"
SKU_MARKER = "NcovSkuActivity"
DETAIL_MARKERS = ("ProjectDetailActivity", "DMH5Activity")
BUY_KEYWORDS = ("立即购买", "立即预订", "立即购票", "立即预约", "去抢票", "预约")
TARGET_MARKERS = ("已预约", "票档已选好")


def _adb(device: str, *args: str, timeout: float = 15.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["adb", "-s", device, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _activity(device: str) -> str:
    output = _adb(device, "shell", "dumpsys", "activity", "activities").stdout
    match = re.search(r"topResumedActivity=.*?/([\w.$]+)", output)
    return match.group(1) if match else ""


def _wait_activity(device: str, needle: str, timeout: float) -> str:
    deadline = time.monotonic() + max(timeout, 0.0)
    while time.monotonic() < deadline:
        activity = _activity(device)
        if needle in activity:
            return activity
        time.sleep(0.05)
    return _activity(device)


def _wait_not_activity(device: str, needle: str, timeout: float) -> str:
    deadline = time.monotonic() + max(timeout, 0.0)
    while time.monotonic() < deadline:
        activity = _activity(device)
        if needle not in activity:
            return activity
        time.sleep(0.05)
    return _activity(device)


def _tap(device: str, point) -> None:
    _adb(device, "shell", "input", "tap", str(int(point[0])), str(int(point[1])))


def _screen_height(device: str) -> int:
    match = re.search(r"(\d+)x(\d+)", _adb(device, "shell", "wm", "size").stdout)
    return int(match.group(2)) if match else 2712


def _ocr_rows(device: str) -> list[tuple[int, int, str]]:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        path = Path(handle.name)
    try:
        with open(path, "wb") as output:
            subprocess.run(
                ["adb", "-s", device, "exec-out", "screencap", "-p"],
                stdout=output,
                timeout=30,
            )
        result = subprocess.run(
            [sys.executable, str(OCR_TOOL), str(path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        rows = []
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 4:
                continue
            try:
                rows.append((int(parts[0]), int(parts[1]), parts[3]))
            except ValueError:
                continue
        return rows
    finally:
        path.unlink(missing_ok=True)


def _parse_point(raw: str):
    match = re.match(r"^\s*(\d+)\s*[,x]\s*(\d+)\s*$", raw or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def _bar_point(rows, height: int):
    candidates = [
        (x, y)
        for x, y, text in rows
        if y > height * 0.85 and any(keyword in text for keyword in BUY_KEYWORDS)
    ]
    return max(candidates, key=lambda point: point[1]) if candidates else None


def prewarm_ticket_page(device: str, buy_point: str = "", runs: int = 2) -> dict:
    """打开/返回原生票档页 runs 次，返回每次耗时和最终页面。"""
    if not device:
        raise ConfigError("请先在安卓配置里填写设备名")
    activity = _activity(device)
    if not any(marker in activity for marker in DETAIL_MARKERS):
        return {
            "ok": False,
            "runs": [],
            "message": (
                f"当前页面（{activity or '未知'}）不是演出详情页；"
                "请先打开一个非目标演出的详情页"
            ),
        }

    rows = _ocr_rows(device)
    height = _screen_height(device)
    # 只看底部栏，避免推荐位里的“已预约”误伤
    bar_texts = " ".join(
        text for _x, y, text in rows if y > height * 0.85
    )
    if any(marker in bar_texts for marker in TARGET_MARKERS):
        return {
            "ok": False,
            "runs": [],
            "message": (
                "当前是已预约的场次（底栏“已预约/票档已选好”）；"
                "请换一个未预约的非目标演出预热，避免重置票档"
            ),
        }

    point = _parse_point(buy_point) or _bar_point(rows, height)
    if point is None:
        return {
            "ok": False,
            "runs": [],
            "message": "没有找到「立即购买/立即预订」按钮，请确认详情页已完全加载",
        }

    results = []
    runs = max(1, min(int(runs or 2), 4))
    for index in range(1, runs + 1):
        started = None
        current = ""
        for _attempt in range(2):
            started = time.perf_counter()
            _tap(device, point)
            current = _wait_activity(device, SKU_MARKER, 4.0)
            if SKU_MARKER in current:
                break
            # 从票档页返回后详情页可能还在过渡/重绘，等一下再点一次
            time.sleep(0.6)
        if SKU_MARKER not in current:
            results.append(
                {"run": index, "ok": False, "activity": current, "open_ms": None}
            )
            return {
                "ok": False,
                "runs": results,
                "message": (
                    f"第 {index} 次没有进入原生票档页（当前 {current or '未知'}）；"
                    "请确认详情页已加载，或换一个原生详情页演出预热"
                ),
            }
        open_ms = round((time.perf_counter() - started) * 1000)
        results.append({"run": index, "ok": True, "open_ms": open_ms})
        _adb(device, "shell", "input", "keyevent", "4")
        _wait_not_activity(device, SKU_MARKER, 3.0)
        time.sleep(0.6)

    # 预热完成后退回上一页；用户应把目标详情页留在上一页
    _adb(device, "shell", "input", "keyevent", "4")
    time.sleep(1.0)
    final = _activity(device)
    returned = "ProjectDetailActivity" in final
    return {
        "ok": True,
        "runs": results,
        "final_activity": final,
        "returned_to_detail": returned,
        "message": (
            "票档页预热完成，已退回上一页；请确认当前是目标详情页"
            if returned
            else f"票档页预热完成，但当前退回到（{final or '未知'}），请手动回到目标详情页"
        ),
    }
