# -*- coding: UTF-8 -*-
"""开抢前采集按钮坐标。

识别手机当前停在哪个页面（详情页 / 选票档页 / 确认页），截图 OCR 找到
对应按钮文字的实测中心点，写回配置对应字段。三个页面各点一次，
即可在开抢前完成坐标核对与预热（首次 OCR 会建立模型缓存，约 5 秒）。
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

from config_store import ConfigError


OCR_TOOL = Path(__file__).resolve().parent / "tools" / "damai_ocr"
# 详情页底栏容器：OCR 找不到按钮文字时的后备定位手段
BUY_BAR_CONTAINER_ID = "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"

# (Activity 关键字, 配置字段, 页面名称, 按钮描述, 候选文字)
PAGES = (
    ("ProjectDetailActivity", "buy_point", "详情页", "立即购票", ("立即购", "立即预订", "立即购买")),
    ("NcovSkuActivity", "confirm_point", "选票档页", "确定", ("确定",)),
    ("DmOrderActivity", "submit_point", "确认页", "立即提交", ("立即提交", "提交订单")),
)


def _adb(device: str, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["adb", "-s", device, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _foreground_activity(device: str) -> str:
    output = _adb(device, "shell", "dumpsys", "activity", "activities", timeout=30).stdout
    match = re.search(r"topResumedActivity=.*?/([\w.$]+)", output)
    return match.group(1) if match else ""


def _ocr_rows(image_path: str) -> list[tuple[int, int, str]]:
    """返回 [(x, y, 文本)]；工具缺失或失败返回空列表。"""
    try:
        result = subprocess.run(
            [sys.executable, str(OCR_TOOL), image_path],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConfigError(f"OCR 工具不可用：{exc}") from exc
    rows = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        try:
            rows.append((int(parts[0]), int(parts[1]), parts[3].strip()))
        except ValueError:
            continue
    return rows


def _find_button(rows, keywords) -> tuple[int, int] | None:
    """按候选文字找按钮中心，命中多个时取置信度最高的一条（列表即按行输出）。"""
    for keyword in keywords:
        for x, y, text in rows:
            if text == keyword or (len(keyword) > 2 and keyword in text):
                return x, y
    return None


def _dump_buy_bar_center(device: str) -> tuple[int, int] | None:
    """详情页底栏是无障碍容器，OCR 失败时可从页面结构里取中心。"""
    _adb(device, "shell", "uiautomator", "dump", "/sdcard/damai_calibrate.xml", timeout=30)
    xml = _adb(device, "shell", "cat", "/sdcard/damai_calibrate.xml", timeout=30).stdout
    match = re.search(
        rf'resource-id="{re.escape(BUY_BAR_CONTAINER_ID)}"[^>]*'
        r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        xml,
    )
    if not match:
        return None
    x1, y1, x2, y2 = (int(value) for value in match.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


def calibrate_android_point(device: str) -> dict:
    """识别当前页面并采集对应按钮坐标，返回结果 dict（不含写配置）。"""
    if not device:
        raise ConfigError("请先在配置里选择并保存设备名")
    activity = _foreground_activity(device)
    page = next((item for item in PAGES if item[0] in activity), None)
    if page is None:
        raise ConfigError(
            f"当前页面（{activity or '未知'}）不在可采集范围，"
            "请把大麦依次停在：演出详情页 → 选票档页 → 确认页"
        )
    _marker, field, page_name, button_name, keywords = page

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        image_path = handle.name
    with open(image_path, "wb") as output:
        capture = subprocess.run(
            ["adb", "-s", device, "exec-out", "screencap", "-p"],
            stdout=output,
            timeout=30,
        )
    if capture.returncode != 0:
        raise ConfigError("截图失败，请确认手机亮屏且 USB 调试正常")

    point = _find_button(_ocr_rows(image_path), keywords)
    if point is None and field == "buy_point":
        point = _dump_buy_bar_center(device)
    if point is None:
        raise ConfigError(
            f"在{page_name}上没有识别到「{button_name}」按钮，"
            "请确认页面已完全加载后重试"
        )
    return {
        "field": field,
        "page": page_name,
        "button": button_name,
        "point": f"{point[0]},{point[1]}",
        "activity": activity,
    }
