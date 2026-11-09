import json
from unittest.mock import Mock

import pytest

from android_tickets import prepare_android_tickets
from damai_appium.ticket_selection import TicketSelectionError


def android_config():
    return {
        "server_url": "http://127.0.0.1:4723",
        "device_name": "test-device",
        "platform_version": "16",
        "ticket_area": "内场",
    }


def test_prepare_android_tickets_writes_cache_and_closes_driver(mocker, tmp_path):
    driver = Mock()
    remote = mocker.patch("android_tickets.webdriver.Remote", return_value=driver)
    candidates = [{"index": 0, "price": 1599, "card_count": 1}]
    preparation = Mock()
    preparation.prepare.return_value = candidates
    preparation_class = mocker.patch(
        "android_tickets.TicketPreparation", return_value=preparation
    )
    cache_path = tmp_path / "data" / "ticket_candidates.json"

    result = prepare_android_tickets(android_config(), cache_path)

    assert result == {"ticket_area": "内场", "candidates": candidates}
    assert json.loads(cache_path.read_text(encoding="utf-8")) == result
    remote.assert_called_once()
    preparation_class.assert_called_once_with(driver)
    preparation.prepare.assert_called_once_with("内场")
    driver.quit.assert_called_once_with()


def test_prepare_android_tickets_closes_driver_after_failure(mocker, tmp_path):
    driver = Mock()
    mocker.patch("android_tickets.webdriver.Remote", return_value=driver)
    preparation = Mock()
    preparation.prepare.side_effect = TicketSelectionError("未识别到内场票档")
    mocker.patch("android_tickets.TicketPreparation", return_value=preparation)
    cache_path = tmp_path / "ticket_candidates.json"

    with pytest.raises(TicketSelectionError, match="未识别到内场票档"):
        prepare_android_tickets(android_config(), cache_path)

    assert not cache_path.exists()
    driver.quit.assert_called_once_with()
