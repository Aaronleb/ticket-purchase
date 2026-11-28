# -*- coding: UTF-8 -*-
"""开抢前采集按钮坐标和可购买按钮正面模板。

识别手机当前停在哪个页面（详情页 / 选票档页 / 确认页），截图 OCR 找到
对应按钮文字的实测中心点，写回配置对应字段。详情页只有正面识别到可购买
文案才保存无 OCR 模板；通过容器坐标兜底时不会把“已预约”保存为放行模板。
"""
from __future__ import annotations

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


# 负向模板：这些文案表示当前不可购买，命中后必须拒绝放行
NEGATIVE_KEYWORDS = ("已预约", "未开售", "即将开抢", "即将开售", "已售罄")
NEGATIVE_TEMPLATE_SLUGS = {
    "已预约": "yuyue",
    "未开售": "weikaisou",
    "即将开抢": "jiangkaqiang",
    "即将开售": "jiangkaishou",
    "已售罄": "yishouqing",
}


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


def _find_button_keyword(rows, keywords) -> str:
    """返回实际命中的候选文字，用于给模板命名。"""
    for keyword in keywords:
        for _x, _y, text in rows:
            if text == keyword or (len(keyword) > 2 and keyword in text):
                return keyword
    return ""


def _capture_screen(device: str) -> str:
    """截取手机当前屏幕，返回临时文件路径。"""
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
    return image_path


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


def _save_button_template(image_path: str, point, template_dir, filename: str) -> str:
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
    destination = directory / filename
    crop.save(destination, format="PNG")
    return str(destination)


def _save_buy_template(image_path: str, point, template_dir) -> str:
    return _save_button_template(image_path, point, template_dir, "buy.png")


def _save_sku_confirm_template(
    image_path: str, point, template_dir, filename: str = "sku-confirm.png"
) -> str:
    """票档页「确定」/确认页「立即提交」按钮模板：只裁固定按钮框。

    票档页价格块宽度会随金额变化，必须排除；确认页同理只用按钮本体。
    以 OCR 识别到的按钮文字中心为锚点裁固定大小。
    """
    from PIL import Image

    from flip_watch import SKU_TEMPLATE_HALF_HEIGHT, SKU_TEMPLATE_HALF_WIDTH

    directory = Path(template_dir)
    directory.mkdir(parents=True, exist_ok=True)
    image = Image.open(image_path).convert("L")
    half_width = max(60, int(image.width * SKU_TEMPLATE_HALF_WIDTH))
    half_height = max(20, int(image.height * SKU_TEMPLATE_HALF_HEIGHT))
    crop = image.crop(
        (
            max(0, point[0] - half_width),
            max(0, point[1] - half_height),
            min(image.width, point[0] + half_width),
            min(image.height, point[1] + half_height),
        )
    )
    destination = directory / filename
    crop.save(destination, format="PNG")
    return str(destination)


def _save_submit_template(image_path: str, point, template_dir) -> str:
    return _save_sku_confirm_template(
        image_path, point, template_dir, filename="sku-submit.png"
    )


def _template_warning(template_path: str) -> str:
    """模板是灰暗的未开售按钮时提示风险（灰度模板只能确认文字形状）。"""
    try:
        from PIL import Image

        with Image.open(template_path) as image:
            values = image.convert("L").tobytes()
    except Exception:
        return ""
    if not values:
        return ""
    bright = sum(1 for value in values if value >= 170) / len(values)
    if bright < 0.08:
        return (
            "模板看起来是未开售的灰色按钮：模板只认字形，"
            "程序还会检查底栏是否已变成可购买的着色状态才放行；"
            "如条件允许，请在按钮真正可购买时重新采集一次模板。"
        )
    return ""


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

    image_path = _capture_screen(device)

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
    if field == "buy_point" and text_confirmed and template_dir:
        result["template_path"] = _save_buy_template(image_path, point, template_dir)
        warning = _template_warning(result["template_path"])
        if warning:
            result["warning"] = warning
    elif field == "confirm_point" and text_confirmed and template_dir:
        # 顺带生成票档页「确定」按钮模板（固定区域，不含金额）
        result["template_path"] = _save_sku_confirm_template(
            image_path, point, template_dir
        )
    elif field == "submit_point" and text_confirmed and template_dir:
        # 确认页「立即提交」模板：首帧后命中即可点，替代固定 0.4 秒等待
        result["template_path"] = _save_submit_template(
            image_path, point, template_dir
        )
    return result


def calibrate_negative_point(device: str, template_dir=None) -> dict:
    """在“已预约/未开售”的详情页采集负向模板。

    负向模板只用于拒绝放行：开售后底栏命中这些模板时继续等待，
    避免把仍不可购买的底栏当成可购买按钮点击。
    """
    if not device:
        raise ConfigError("请先在配置里选择并保存设备名")
    if not template_dir:
        raise ConfigError("模板目录未配置，请先采集一次「立即购票」坐标")
    activity = _foreground_activity(device)
    if "ProjectDetailActivity" not in activity:
        raise ConfigError(
            f"当前页面（{activity or '未知'}）不是演出详情页，"
            "请在大麦打开显示「已预约」或「未开售」的详情页后重试"
        )

    image_path = _capture_screen(device)
    rows = _ocr_rows(image_path)
    point = _find_button(rows, NEGATIVE_KEYWORDS)
    if point is None:
        raise ConfigError(
            "没有识别到「已预约 / 未开售 / 即将开抢」等不可购买文案，"
            "请确认详情页显示已预约状态后重试"
        )
    keyword = _find_button_keyword(rows, NEGATIVE_KEYWORDS)
    slug = NEGATIVE_TEMPLATE_SLUGS.get(keyword, "template")
    template_path = _save_button_template(
        image_path,
        point,
        template_dir,
        f"negative-{slug}.png",
    )
    return {
        "field": "negative_template",
        "page": "详情页",
        "button": keyword,
        "point": f"{point[0]},{point[1]}",
        "activity": activity,
        "template_path": template_path,
    }
