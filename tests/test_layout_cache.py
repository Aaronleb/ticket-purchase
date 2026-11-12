import json
from unittest.mock import Mock

import pytest

from damai_appium.layout_cache import (
    LayoutCacheError,
    LayoutSnapshot,
    merge_step,
    prepare_quantity,
    read_quantity,
)


def test_layout_snapshot_round_trip_and_matching(tmp_path):
    snapshot = LayoutSnapshot(
        (1080, 2400),
        {"card_3": {"x": 100, "y": 200}},
        ticket_count=2,
        price_index=3,
    )

    actual = LayoutSnapshot.from_dict(snapshot.to_dict())

    assert actual.matches((1080, 2400), 2, 3)
    assert not actual.matches((1080, 2400), 1, 3)
    assert actual.center("card_3") == (100, 200)


def test_layout_snapshot_rejects_out_of_screen_point():
    with pytest.raises(LayoutCacheError):
        LayoutSnapshot.from_dict(
            {
                "screen": [1080, 2400],
                "points": {"confirm": {"x": 2000, "y": 100}},
                "ticket_count": 1,
                "price_index": 0,
            }
        )


def test_prepare_quantity_clicks_plus_and_verifies():
    container = Mock()
    container.get_attribute.side_effect = ["1张", "3张", "3张"]
    plus = Mock()
    plus.rect = {"x": 10, "y": 20, "width": 30, "height": 40}
    driver = Mock()
    driver.find_elements.return_value = [container]
    driver.find_element.return_value = plus

    prepare_quantity(driver, 3)

    assert read_quantity(driver) == 3
    assert driver.execute_script.call_count == 2


def test_prepare_quantity_rejects_unexpected_start():
    container = Mock()
    container.get_attribute.return_value = "2张"
    driver = Mock()
    driver.find_elements.return_value = [container]

    with pytest.raises(LayoutCacheError, match="手动调整"):
        prepare_quantity(driver, 3)
    driver.execute_script.assert_not_called()


def test_merge_step_rejects_changed_ticket_count(tmp_path):
    path = tmp_path / "layout.json"
    captured = {
        "key": "price_page",
        "screen": [1080, 2400],
        "points": {"confirm": {"x": 10, "y": 20}},
        "ticket_count": 1,
        "price_index": 0,
    }
    merge_step(path, captured)

    with pytest.raises(LayoutCacheError, match="已变化"):
        merge_step(path, {**captured, "ticket_count": 2})

    assert json.loads(path.read_text(encoding="utf-8"))["ticket_count"] == 1
