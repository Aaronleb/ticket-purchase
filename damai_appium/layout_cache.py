"""开售前的布局坐标缓存。

抢票关键路径上最大的开销不是点击本身，而是"为了点击而必须的若干次元素查询"。
同一场演出、同一台手机、同一档页面结构下，票档卡片、数量加号、确定购买、观演人
勾选框和提交按钮的坐标是稳定的，因此可以：

1. 开售前（识别票档阶段）一次性把关键控件的 ``bounds`` 存进缓存；
2. 开售后按坐标直接点击，跳过 ``find_element`` / ``get_attribute``；
3. 用布局指纹（屏幕尺寸 + 关键字节点位置）校验缓存是否仍然有效；
4. 指纹不匹配时整体降级回现有慢路径，绝不盲目点击。

缓存文件与票档缓存放在同一目录，格式为 ``layout_cache.json``。
"""

from __future__ import annotations

import json
from pathlib import Path

from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

from appium.webdriver.common.appiumby import AppiumBy

LAYOUT_CACHE_NAME = "layout_cache.json"


class LayoutCacheError(RuntimeError):
    pass


class LayoutSnapshot:
    """一次布局快照：屏幕尺寸 + 关键控件中心坐标 + 指纹。"""

    def __init__(self, screen, points, fingerprint=None):
        self.screen = tuple(screen)
        self.points = dict(points)
        self.fingerprint = fingerprint

    def center(self, key):
        point = self.points.get(key)
        if not point:
            raise LayoutCacheError(f"布局缓存缺少控件坐标: {key}")
        return int(point["x"]), int(point["y"])

    def has(self, key):
        return key in self.points

    def matches(self, screen):
        return tuple(screen) == self.screen

    def to_dict(self):
        return {
            "screen": list(self.screen),
            "points": self.points,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict):
            raise LayoutCacheError("布局缓存格式无效，请重新识别")
        screen = payload.get("screen")
        points = payload.get("points")
        if (
            not isinstance(screen, (list, tuple))
            or len(screen) != 2
            or not all(isinstance(item, int) and item > 0 for item in screen)
        ):
            raise LayoutCacheError("布局缓存缺少有效的屏幕尺寸，请重新识别")
        if not isinstance(points, dict):
            raise LayoutCacheError("布局缓存缺少控件坐标，请重新识别")
        validated = {}
        for key, point in points.items():
            if not isinstance(point, dict):
                raise LayoutCacheError("布局缓存控件坐标格式无效，请重新识别")
            x, y = point.get("x"), point.get("y")
            if (
                isinstance(x, bool)
                or isinstance(y, bool)
                or not isinstance(x, int)
                or not isinstance(y, int)
            ):
                raise LayoutCacheError("布局缓存控件坐标必须是整数，请重新识别")
            if not 0 <= x < screen[0] or not 0 <= y < screen[1]:
                raise LayoutCacheError("布局缓存控件坐标超出屏幕范围，请重新识别")
            validated[key] = {"x": x, "y": y}
        return cls(screen, validated, payload.get("fingerprint"))


def save_layout(path: str | Path, snapshot: LayoutSnapshot) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_layout(path: str | Path) -> LayoutSnapshot:
    path = Path(path)
    if not path.is_file():
        raise LayoutCacheError("尚未缓存页面布局，请先在票档页点击“识别并缓存票档”")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LayoutCacheError(f"布局缓存读取失败，请重新识别：{exc}") from exc
    return LayoutSnapshot.from_dict(payload)


def center_of_rect(rect):
    """把 WebDriver 的 rect 转成中心点坐标。"""
    return {
        "x": int(rect["x"] + rect["width"] // 2),
        "y": int(rect["y"] + rect["height"] // 2),
    }


def screen_size(driver):
    """读取当前屏幕像素尺寸，用于校验缓存是否仍然适用。"""
    size = driver.get_window_size()
    return int(size["width"]), int(size["height"])


# ---------------------------------------------------------------------------
# 预演采集
#
# 开售前无法进入观演人页和订单确认页，所以这三个页面阶段的坐标只能在"有票"的
# 场次上走一遍采下来。每步只读取当前页面上的控件位置，不点击、不提交。
# ---------------------------------------------------------------------------

CAPTURE_STEPS = (
    {
        "key": "price_page",
        "title": "票档页",
        "hint": "保持手机停在票档页（已显示各价位卡片），点击本步采集。",
        "ids": {
            "buy_bar": "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl",
        },
    },
    {
        "key": "confirm_page",
        "title": "订单确认页",
        "hint": "手动在手机上点“确定购买”，进入订单确认页（观演人列表所在页）后点击本步采集。",
        "ids": {
            "confirm_price": "cn.damai:id/tv_price",
        },
    },
    {
        "key": "submit_page",
        "title": "提交按钮",
        "hint": "确认观演人已显示后点击本步采集；脚本不会提交订单，请采集完自行退出。",
        "ids": {
            "submit": None,
        },
    },
)

CONFIRM_BUTTON_ID = "cn.damai:id/btn_buy_view"
SUBMIT_TEXTS = ("立即提交", "提交订单")


def capture_step(driver, step_key):
    """采集当前页面上该步骤需要的控件坐标。

    返回 ``{"key": ..., "screen": [...], "points": {...}}``；找不到必须的控件时
    抛出 :class:`LayoutCacheError`，提示用户先把手机停在正确的页面。
    """
    step = next((item for item in CAPTURE_STEPS if item["key"] == step_key), None)
    if step is None:
        raise LayoutCacheError(f"未知的采集步骤: {step_key}")

    screen = screen_size(driver)
    points = {}

    for name, resource_id in step["ids"].items():
        if resource_id is None:
            continue
        try:
            element = driver.find_element(By.ID, resource_id)
        except NoSuchElementException:
            continue
        rect = element.rect
        if rect["width"] <= 0 or rect["height"] <= 0:
            continue
        points[name] = center_of_rect(rect)

    if step_key == "price_page":
        points.update(_capture_ticket_cards(driver))
        if "confirm" not in points:
            try:
                confirm = driver.find_element(By.ID, CONFIRM_BUTTON_ID)
                points["confirm"] = center_of_rect(confirm.rect)
            except NoSuchElementException:
                pass
    elif step_key == "confirm_page":
        points.update(_capture_viewers(driver))
    elif step_key == "submit_page":
        points.update(_capture_submit(driver))

    required = {
        "price_page": ("buy_bar",),
        "confirm_page": ("confirm_price",),
        "submit_page": ("submit",),
    }[step_key]
    missing = [name for name in required if name not in points]
    if missing:
        raise LayoutCacheError(
            f"未在“{step['title']}”页面找到控件 {', '.join(missing)}，请确认手机停在正确页面"
        )
    return {"key": step_key, "screen": list(screen), "points": points}


def _capture_ticket_cards(driver):
    """采集票档卡片坐标，键名与票档缓存里的 index 对齐。"""
    points = {}
    try:
        container = driver.find_element(
            By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"
        )
    except NoSuchElementException:
        return points
    cards = container.find_elements(By.XPATH, "./android.widget.FrameLayout")
    for index, card in enumerate(cards):
        rect = card.rect
        if rect["width"] <= 0 or rect["height"] <= 0:
            continue
        points[f"card_{index}"] = center_of_rect(rect)
    return points


def _capture_viewers(driver):
    """采集每个观演人行的勾选框坐标，键名使用观演人姓名。"""
    points = {}
    rows = driver.find_elements(By.ID, "cn.damai:id/layout_main")
    for row in rows:
        try:
            name_element = row.find_element(By.ID, "cn.damai:id/text_name")
            checkbox = row.find_element(By.ID, "cn.damai:id/checkbox")
        except NoSuchElementException:
            continue
        name = (name_element.get_attribute("text") or "").strip()
        if not name:
            continue
        points[f"viewer_{name}"] = center_of_rect(checkbox.rect)
    return points


def _capture_submit(driver):
    points = {}
    for text in SUBMIT_TEXTS:
        try:
            element = driver.find_element(
                AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{text}")'
            )
        except NoSuchElementException:
            continue
        rect = element.rect
        if rect["width"] <= 0 or rect["height"] <= 0:
            continue
        points["submit"] = center_of_rect(rect)
        break
    return points


def merge_step(cache_path, captured):
    """把一次采集结果并入已有缓存，返回合并后的快照。"""
    path = Path(cache_path)
    if path.is_file():
        try:
            snapshot = load_layout(path)
        except LayoutCacheError:
            snapshot = None
    else:
        snapshot = None

    screen = tuple(captured["screen"])
    if snapshot is not None and not snapshot.matches(screen):
        raise LayoutCacheError(
            "屏幕尺寸与已有缓存不一致，请重新采集全部步骤"
        )
    points = dict(snapshot.points) if snapshot is not None else {}
    points.update(captured["points"])
    merged = LayoutSnapshot(screen, points, snapshot.fingerprint if snapshot else None)
    save_layout(path, merged)
    return merged

