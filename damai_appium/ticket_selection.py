from __future__ import annotations

import json
import re
from pathlib import Path

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


SOLD_OUT_WORDS = ("缺货", "售罄", "无票", "缺票")


class TicketSelectionError(RuntimeError):
    pass


def extract_price(text: str) -> float | None:
    normalized = re.sub(r"[\s,，]", "", text)
    matches = re.findall(r"(?<!\d)(\d+(?:\.\d+)?)元", normalized)
    if not matches:
        matches = re.findall(r"(?:¥|￥)(\d+(?:\.\d+)?)", normalized)
    if not matches:
        matches = re.findall(r"(?<!\d)(\d+(?:\.\d+)?)(?!\d)", normalized)
    return float(matches[0]) if matches else None


def prioritize_tickets(tickets: list[dict], area: str) -> list[dict]:
    candidates = [
        ticket for ticket in tickets
        if area in ticket["text"] and ticket["price"] is not None
    ]
    candidates.sort(key=lambda ticket: ticket["price"], reverse=True)
    if len(candidates) > 1 and candidates[0]["price"] == candidates[1]["price"]:
        raise TicketSelectionError(f"{area}最高价位存在多个相同候选，无法确定目标")
    return candidates


def load_prepared_tickets(cache_path: str | Path, area: str) -> list[dict]:
    path = Path(cache_path)
    if not path.is_file():
        raise TicketSelectionError("尚未缓存票档，请先在票档页面点击“识别并缓存票档”")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TicketSelectionError(f"票档缓存读取失败，请重新识别：{exc}") from exc
    if not isinstance(payload, dict) or payload.get("ticket_area") != area:
        raise TicketSelectionError("票档缓存与当前区域不匹配，请重新识别")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise TicketSelectionError("票档缓存为空，请重新识别")

    validated = []
    indexes = set()
    previous_price = None
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise TicketSelectionError("票档缓存格式无效，请重新识别")
        index = candidate.get("index")
        price = candidate.get("price")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or index in indexes
            or isinstance(price, bool)
            or not isinstance(price, (int, float))
            or price <= 0
            or (previous_price is not None and price > previous_price)
        ):
            raise TicketSelectionError("票档缓存内容无效，请重新识别")
        indexes.add(index)
        validated.append(candidate)
        previous_price = price
    return validated


def card_is_available(card, ocr_text: str = "") -> bool:
    if card.get_attribute("clickable") != "true" or card.get_attribute("enabled") != "true":
        return False
    tags = [element.text for element in card.find_elements(By.ID, "cn.damai:id/tv_tag")]
    text = " ".join([ocr_text, *tags])
    return not any(word in text for word in SOLD_OUT_WORDS)


def ocr_center(box) -> tuple[float, float]:
    return (
        sum(point[0] for point in box) / 4,
        sum(point[1] for point in box) / 4,
    )


class TicketPreparation:
    def __init__(self, driver, ocr_engine=None):
        self.driver = driver
        self.ocr_engine = ocr_engine

    def prepare(self, area: str) -> list[dict]:
        container = self.driver.find_element(By.ID, "cn.damai:id/project_detail_perform_price_flowlayout")
        cards = container.find_elements(By.XPATH, "./android.widget.FrameLayout")
        if not cards:
            raise TicketSelectionError("未找到票档卡片")
        if self.ocr_engine is None:
            from rapidocr_onnxruntime import RapidOCR

            self.ocr_engine = RapidOCR()
        results, _ = self.ocr_engine(self.driver.get_screenshot_as_png())
        results = results or []
        tickets = []
        for index, card in enumerate(cards):
            rect = card.rect
            entries = []
            for box, text, score in results:
                x, y = ocr_center(box)
                if rect["x"] <= x <= rect["x"] + rect["width"] and rect["y"] <= y <= rect["y"] + rect["height"]:
                    entries.append((y, x, text, float(score)))
            entries.sort()
            text = " ".join(entry[2] for entry in entries)
            ticket = {
                "index": index,
                "text": text,
                "price": extract_price(text),
                "available": card_is_available(card, text),
                "card_count": len(cards),
            }
            tickets.append(ticket)
            print(f"票档识别 {index}: {text or '未识别'}；价格={ticket['price']}；可购买={ticket['available']}")
        candidates = prioritize_tickets(tickets, area)
        if not candidates:
            raise TicketSelectionError(f"未识别到{area}票档")
        print(f"已缓存{area}候选顺序: " + " > ".join(f"{ticket['price']:g}元" for ticket in candidates))
        return candidates


def select_prepared_ticket(driver, candidates: list[dict]) -> bool:
    container = driver.find_element(By.ID, "cn.damai:id/project_detail_perform_price_flowlayout")
    cards = container.find_elements(By.XPATH, "./android.widget.FrameLayout")
    expected_card_count = candidates[0].get("card_count") if candidates else None
    if expected_card_count is not None and len(cards) != expected_card_count:
        raise TicketSelectionError("票档页面结构已变化，请重新识别并缓存")
    for candidate in candidates:
        index = candidate["index"]
        if index >= len(cards):
            raise TicketSelectionError("票档位置已变化，请重新识别并缓存")
        card = cards[index]
        if not card_is_available(card):
            print(f"跳过已缺货票档: {candidate['price']:g}元")
            continue
        driver.execute_script("mobile: clickGesture", {"elementId": card.id})
        expected = candidate["price"]
        try:
            WebDriverWait(driver, 1.5).until(
                lambda current: extract_price(
                    current.find_element(By.ID, "cn.damai:id/tv_price").text
                ) == expected
            )
        except TimeoutException:
            print(f"票档价格核对失败，目标为 {expected:g}元")
            return False
        print(f"已选择目标票档: {expected:g}元")
        return True
    print("缓存中的票档均不可购买")
    return False
