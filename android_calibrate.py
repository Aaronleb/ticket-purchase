# -*- coding: UTF-8 -*-
"""开抢前采集按钮坐标和三页正面模板。

识别手机当前停在哪个页面（详情页 / 选票档页 / 确认页），截图 OCR 找到
对应按钮文字的实测中心点，写回配置对应字段。只有正面识别到当前页目标
按钮才保存对应模板；容器坐标兜底不会把“已预约”保存为放行模板。
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from config_store import ConfigError


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


_OCR_ENGINE = None


def _ocr_rows(image_path: str) -> list[tuple[int, int, str]]:
    """校准专用应用内 OCR，返回 [(x, y, 文本)]。"""
    global _OCR_ENGINE
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise ConfigError(f"校准 OCR 组件不可用：{exc}") from exc
    if _OCR_ENGINE is None:
        _OCR_ENGINE = RapidOCR()
    try:
        result, _elapsed = _OCR_ENGINE(image_path)
    except Exception as exc:
        raise ConfigError(f"校准截图识别失败：{exc}") from exc
    rows = []
    for box, text, _score in result or []:
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        normalized = " ".join(str(text).split())
        if normalized:
            rows.append((int(sum(xs) / len(xs)), int(sum(ys) / len(ys)), normalized))
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


TEMPLATE_FIELDS = {
    "buy_point": "buy",
    "confirm_point": "confirm",
    "submit_point": "submit",
}


def _save_button_template(image_path: str, point, template_dir, name: str) -> str:
    from PIL import Image

    directory = Path(template_dir)
    directory.mkdir(parents=True, exist_ok=True)
    image = Image.open(image_path).convert("L")
    half_width = int(image.width * 0.18)
    half_height = int(image.height * 0.035)
    crop = image.crop(
        (
            max(0, point[0] - half_width),
            max(0, point[1] - half_height),
            min(image.width, point[0] + half_width),
            min(image.height, point[1] + half_height),
        )
    )
    destination = directory / f"{name}.png"
    crop.save(destination, format="PNG")
    metadata_path = directory / "screens.json"
    try:
        screens = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        screens = {}
    screens[name] = [image.width, image.height]
    metadata_path.write_text(
        json.dumps(screens, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return str(destination)


def calibrate_android_point(device: str, template_dir=None) -> dict:
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

    rows = _ocr_rows(image_path)
    point = _find_button(rows, keywords)
    text_confirmed = point is not None
    if point is None and field == "buy_point":
        point = _dump_buy_bar_center(device)
    if point is None:
        raise ConfigError(
            f"在{page_name}上没有识别到「{button_name}」按钮，"
            "请确认页面已完全加载后重试"
        )
    result = {
        "field": field,
        "page": page_name,
        "button": button_name,
        "point": f"{point[0]},{point[1]}",
        "activity": activity,
    }
    if text_confirmed and template_dir:
        result["template_path"] = _save_button_template(
            image_path, point, template_dir, TEMPLATE_FIELDS[field]
        )
    return result
