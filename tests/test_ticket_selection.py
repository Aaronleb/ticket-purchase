import json
from unittest.mock import Mock

import pytest

from damai_appium.ticket_selection import (
    TicketPreparation,
    TicketSelectionError,
    extract_price,
    load_prepared_tickets,
    prioritize_tickets,
    select_prepared_ticket,
)


def make_card(index, sold_out=False):
    card = Mock()
    card.id = f"card-{index}"
    card.rect = {"x": (index % 2) * 100, "y": (index // 2) * 100, "width": 100, "height": 100}
    card.get_attribute.side_effect = lambda name: "false" if sold_out and name == "enabled" else "true"
    tag = Mock()
    tag.text = "缺货登记" if sold_out else "预售"
    card.find_elements.return_value = [tag]
    return card


def test_extract_price_from_ocr_text():
    assert extract_price("内场 1,599 元") == 1599
    assert extract_price("内场1199") == 1199
    assert extract_price("内场") is None


def test_prepare_runs_ocr_once_and_caches_price_order():
    cards = [make_card(index) for index in range(3)]
    container = Mock()
    container.find_elements.return_value = cards
    driver = Mock()
    driver.find_element.return_value = container
    driver.get_screenshot_as_png.return_value = b"screenshot"
    ocr = Mock(return_value=([
        ([[100, 0], [200, 0], [200, 100], [100, 100]], "内场 1199元", "0.99"),
        ([[0, 0], [100, 0], [100, 100], [0, 100]], "看台 399元", "0.99"),
        ([[0, 100], [100, 100], [100, 200], [0, 200]], "内场 1599元", "0.99"),
    ], None))

    candidates = TicketPreparation(driver, ocr).prepare("内场")

    assert ocr.call_count == 1
    assert [candidate["price"] for candidate in candidates] == [1599, 1199]
    assert [candidate["index"] for candidate in candidates] == [2, 1]


def test_prioritize_rejects_ambiguous_highest_price():
    tickets = [
        {"text": "内场A 1599元", "price": 1599},
        {"text": "内场B 1599元", "price": 1599},
    ]
    with pytest.raises(TicketSelectionError):
        prioritize_tickets(tickets, "内场")


def test_cached_selection_skips_sold_out_ticket_without_ocr():
    sold_out = make_card(0, sold_out=True)
    available = make_card(1)
    cards = [sold_out, available]
    container = Mock()
    container.find_elements.return_value = cards
    price = Mock()
    price.text = "1599"
    driver = Mock()

    def find_element(by, value):
        if value == "cn.damai:id/project_detail_perform_price_flowlayout":
            return container
        if value == "cn.damai:id/tv_price":
            return price
        raise AssertionError(value)

    driver.find_element.side_effect = find_element
    candidates = [
        {"index": 0, "price": 1999},
        {"index": 1, "price": 1599},
    ]

    assert select_prepared_ticket(driver, candidates) is True
    driver.execute_script.assert_called_once_with("mobile: clickGesture", {"elementId": "card-1"})


def test_cached_selection_stops_when_price_does_not_match():
    card = make_card(0)
    container = Mock()
    container.find_elements.return_value = [card]
    price = Mock()
    price.text = "0"
    driver = Mock()

    def find_element(by, value):
        return container if "flowlayout" in value else price

    driver.find_element.side_effect = find_element

    assert select_prepared_ticket(driver, [{"index": 0, "price": 1999}]) is False
    driver.execute_script.assert_called_once()


def test_load_prepared_tickets_validates_area_and_preserves_order(tmp_path):
    cache_path = tmp_path / "ticket_candidates.json"
    candidates = [
        {"index": 2, "price": 1599, "card_count": 3},
        {"index": 1, "price": 1199, "card_count": 3},
    ]
    cache_path.write_text(
        json.dumps({"ticket_area": "内场", "candidates": candidates}),
        encoding="utf-8",
    )

    assert load_prepared_tickets(cache_path, "内场") == candidates
    with pytest.raises(TicketSelectionError, match="区域不匹配"):
        load_prepared_tickets(cache_path, "看台")


def test_load_prepared_tickets_requires_existing_cache(tmp_path):
    with pytest.raises(TicketSelectionError, match="尚未缓存票档"):
        load_prepared_tickets(tmp_path / "missing.json", "内场")


@pytest.mark.parametrize("candidates", [
    [],
    [{"index": -1, "price": 1599}],
    [{"index": 0, "price": 1199}, {"index": 1, "price": 1599}],
    [{"index": 0, "price": 1599}, {"index": 0, "price": 1199}],
])
def test_load_prepared_tickets_rejects_invalid_cache(tmp_path, candidates):
    cache_path = tmp_path / "ticket_candidates.json"
    cache_path.write_text(
        json.dumps({"ticket_area": "内场", "candidates": candidates}),
        encoding="utf-8",
    )

    with pytest.raises(TicketSelectionError, match="缓存"):
        load_prepared_tickets(cache_path, "内场")


def test_cached_selection_stops_when_page_structure_changes():
    container = Mock()
    container.find_elements.return_value = [make_card(0)]
    driver = Mock()
    driver.find_element.return_value = container

    with pytest.raises(TicketSelectionError, match="页面结构已变化"):
        select_prepared_ticket(
            driver,
            [{"index": 0, "price": 1599, "card_count": 2}],
        )
    driver.execute_script.assert_not_called()
