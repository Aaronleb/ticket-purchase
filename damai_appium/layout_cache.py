"""开售前采集下单坐标，并预置、校验购票数量。"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

LAYOUT_CACHE_NAME = "layout_cache.json"
VIEWER_ROWS = (
    '//*[@resource-id="cn.damai:id/layout_main" '
    'and .//*[@resource-id="cn.damai:id/text_name"]]'
)
VIEWER_CHECKBOXES = (
    '//*[@resource-id="cn.damai:id/layout_main"]'
    '//*[@resource-id="cn.damai:id/checkbox"]'
)


class LayoutCacheError(RuntimeError):
    pass


CAPTURE_STEPS = (
    {
        "key": "price_page",
        "title": "票档页并预置数量",
        "hint": "停在票档页后采集；此步会按配置点击数量加号并校验，不会点击确定购买。",
    },
    {
        "key": "confirm_page",
        "title": "观演人页",
        "hint": "手动进入确认购买页后采集，只读取前 N 位观演人及复选框坐标。",
    },
    {
        "key": "submit_page",
        "title": "提交按钮",
        "hint": "停在确认购买页采集提交按钮；脚本只读取坐标，不会提交订单。",
    },
)


def center_of_rect(rect):
    return {
        "x": int(rect["x"] + rect["width"] // 2),
        "y": int(rect["y"] + rect["height"] // 2),
    }


def screen_size(driver):
    size = driver.get_window_size()
    return int(size["width"]), int(size["height"])


def read_quantity(driver) -> int:
    containers = driver.find_elements(By.ID, "layout_num")
    if not containers:
        raise LayoutCacheError("未找到数量区域")
    text = containers[0].get_attribute("text") or ""
    match = re.fullmatch(r"\D*(\d+)\D*", text)
    if not match:
        raise LayoutCacheError(f"无法从数量区域确认唯一数字: {text!r}")
    return int(match.group(1))


def prepare_quantity(driver, expected: int) -> None:
    current = read_quantity(driver)
    if current == expected:
        return
    if current != 1:
        raise LayoutCacheError(
            f"当前数量为 {current}，请先手动调整为 1 或目标数量 {expected}"
        )
    plus = driver.find_element(By.ID, "img_jia")
    point = center_of_rect(plus.rect)
    for _ in range(expected - 1):
        driver.execute_script("mobile: clickGesture", {**point, "duration": 20})
        time.sleep(0.02)
    actual = read_quantity(driver)
    if actual != expected:
        raise LayoutCacheError(f"数量预置失败：期望 {expected}，实际 {actual}")


class LayoutSnapshot:
    def __init__(self, screen, points, ticket_count, price_index):
        self.screen = tuple(screen)
        self.points = dict(points)
        self.ticket_count = ticket_count
        self.price_index = price_index

    def center(self, key):
        point = self.points.get(key)
        if not point:
            raise LayoutCacheError(f"布局缓存缺少控件坐标: {key}")
        return int(point["x"]), int(point["y"])

    def has(self, key):
        return key in self.points

    def matches(self, screen, ticket_count, price_index):
        return (
            tuple(screen) == self.screen
            and ticket_count == self.ticket_count
            and price_index == self.price_index
        )

    def to_dict(self):
        return {
            "screen": list(self.screen),
            "points": self.points,
            "ticket_count": self.ticket_count,
            "price_index": self.price_index,
        }

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict):
            raise LayoutCacheError("布局缓存格式无效，请重新采集")
        screen = payload.get("screen")
        points = payload.get("points")
        ticket_count = payload.get("ticket_count")
        price_index = payload.get("price_index")
        if (
            not isinstance(screen, (list, tuple))
            or len(screen) != 2
            or not all(isinstance(item, int) and not isinstance(item, bool) and item > 0 for item in screen)
            or not isinstance(ticket_count, int)
            or isinstance(ticket_count, bool)
            or not 1 <= ticket_count <= 6
            or not isinstance(price_index, int)
            or isinstance(price_index, bool)
            or not 0 <= price_index <= 50
            or not isinstance(points, dict)
        ):
            raise LayoutCacheError("布局缓存缺少有效配置，请重新采集")
        validated = {}
        for key, point in points.items():
            if not isinstance(point, dict):
                raise LayoutCacheError("布局缓存控件格式无效，请重新采集")
            x, y = point.get("x"), point.get("y")
            if (
                isinstance(x, bool)
                or isinstance(y, bool)
                or not isinstance(x, int)
                or not isinstance(y, int)
                or not 0 <= x < screen[0]
                or not 0 <= y < screen[1]
            ):
                raise LayoutCacheError("布局缓存坐标超出屏幕范围，请重新采集")
            validated[key] = {"x": x, "y": y}
        return cls(screen, validated, ticket_count, price_index)


def save_layout(path, snapshot):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_layout(path):
    path = Path(path)
    if not path.is_file():
        raise LayoutCacheError("尚未采集下单坐标")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LayoutCacheError(f"布局缓存读取失败：{exc}") from exc
    return LayoutSnapshot.from_dict(payload)


def _capture_price_page(driver, ticket_count, price_index):
    prepare_quantity(driver, ticket_count)
    points = {}
    confirm = driver.find_element(By.ID, "cn.damai:id/btn_buy_view")
    points["confirm"] = center_of_rect(confirm.rect)
    container = driver.find_element(
        By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"
    )
    cards = container.find_elements(By.XPATH, "./android.widget.FrameLayout")
    for index, card in enumerate(cards):
        points[f"card_{index}"] = center_of_rect(card.rect)
    if ticket_count > 1:
        plus = driver.find_element(By.ID, "img_jia")
        points["quantity_plus"] = center_of_rect(plus.rect)
    return points


def _capture_viewers(driver, ticket_count):
    driver.find_element(By.ID, "cn.damai:id/tv_price")
    rows = driver.find_elements(By.XPATH, VIEWER_ROWS)
    if len(rows) < ticket_count:
        raise LayoutCacheError(
            f"观演人不足：需要 {ticket_count} 位，当前仅找到 {len(rows)} 位"
        )
    points = {}
    for index, row in enumerate(rows):
        checkbox = row.find_element(By.ID, "cn.damai:id/checkbox")
        points[f"viewer_{index}"] = center_of_rect(checkbox.rect)
    return points


def _capture_submit(driver):
    for text in ("立即提交", "提交订单"):
        try:
            element = driver.find_element(
                AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{text}")'
            )
        except NoSuchElementException:
            continue
        return {"submit": center_of_rect(element.rect)}
    raise LayoutCacheError("未找到立即提交按钮")


def capture_step(driver, step_key, ticket_count, price_index):
    if step_key == "price_page":
        points = _capture_price_page(driver, ticket_count, price_index)
    elif step_key == "confirm_page":
        points = _capture_viewers(driver, ticket_count)
    elif step_key == "submit_page":
        points = _capture_submit(driver)
    else:
        raise LayoutCacheError(f"未知的采集步骤: {step_key}")
    required = [f"card_{price_index}"] if step_key == "price_page" else []
    if step_key == "price_page":
        required.append("confirm")
        if ticket_count > 1:
            required.append("quantity_plus")
    elif step_key == "confirm_page":
        required.append(f"viewer_{ticket_count - 1}")
    elif step_key == "submit_page":
        required.append("submit")
    missing = [key for key in required if key not in points]
    if missing:
        raise LayoutCacheError(f"采集缺少控件: {', '.join(missing)}")
    return {
        "key": step_key,
        "screen": list(screen_size(driver)),
        "points": points,
        "ticket_count": ticket_count,
        "price_index": price_index,
    }


def merge_step(cache_path, captured):
    path = Path(cache_path)
    snapshot = load_layout(path) if path.is_file() else None
    screen = tuple(captured["screen"])
    ticket_count = captured["ticket_count"]
    price_index = captured["price_index"]
    if snapshot is not None and not snapshot.matches(screen, ticket_count, price_index):
        raise LayoutCacheError("屏幕、购票数量或票档序号已变化，请重新采集全部步骤")
    points = dict(snapshot.points) if snapshot is not None else {}
    points.update(captured["points"])
    merged = LayoutSnapshot(screen, points, ticket_count, price_index)
    save_layout(path, merged)
    return merged
