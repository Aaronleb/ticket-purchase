import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, mock_open

import pytest
from selenium.common.exceptions import TimeoutException

from damai_appium.config import Config
from damai_appium.damai_app_v2 import BUY_NOW_SELECTOR, DamaiBot, parse_amount
from damai_appium.sale_clock import SHANGHAI, SaleTimePast


BASE_CONFIG = {
    "server_url": "http://127.0.0.1:4723",
    "keyword": "旧配置",
    "users": ["旧观演人"],
    "city": "旧城市",
    "date": "12.06",
    "price": "旧票档",
    "price_index": 5,
    "sell_start_time": "2026-11-13T20:00:00+08:00",
    "if_commit_order": True,
}


def element(x=10, y=20, width=100, height=40, text=""):
    item = Mock()
    item.rect = {"x": x, "y": y, "width": width, "height": height}
    item.get_attribute.side_effect = lambda name: text if name == "text" else None
    item.text = text
    return item


def checkbox(checked):
    item = Mock()
    item.get_attribute.return_value = "true" if checked else "false"
    return item


def confirmation(states=(True,), price="¥680.00"):
    return {
        "price": element(text=price),
        "checkboxes": [checkbox(state) for state in states],
        "submit": element(x=300, y=1800),
    }


def make_bot(if_commit_order=False, ticket_count=1, page=None):
    bot = DamaiBot.__new__(DamaiBot)
    bot.config = SimpleNamespace(
        ticket_count=ticket_count,
        sell_start_time="2026-11-13T20:00:00+08:00",
        if_commit_order=if_commit_order,
    )
    bot.driver = Mock()
    bot.wait = Mock()
    bot.wait.until.side_effect = [
        [element(x=200, y=2000)],
        page if page is not None else confirmation(),
    ]
    bot._wait_for_sale = Mock(return_value=SimpleNamespace(
        released_at=datetime(2026, 11, 13, 20, 0, tzinfo=SHANGHAI),
        delay_ms=0.0,
    ))
    bot._purchase_attempted = False
    bot._submit_attempted = False
    return bot


def test_load_config_uses_legacy_defaults():
    config_file = mock_open(read_data=json.dumps(BASE_CONFIG))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("builtins.open", config_file)
        config = Config.load_config()

    assert config.device_name == "emulator-5554"
    assert config.platform_version == "16"
    assert config.ticket_count == 1
    assert config.sell_start_time == "2026-11-13T20:00:00+08:00"
    config_file.assert_called_once_with("config.jsonc", "r", encoding="utf-8")


def test_load_config_reads_ticket_count(mocker):
    payload = {**BASE_CONFIG, "ticket_count": 4}
    mocker.patch("builtins.open", mock_open(read_data=json.dumps(payload)))

    assert Config.load_config().ticket_count == 4


def test_capabilities_use_configured_device(mocker):
    config = SimpleNamespace(
        server_url="http://127.0.0.1:4723",
        device_name="configured-device",
        platform_version="15",
    )
    mocker.patch.object(Config, "load_config", return_value=config)
    remote = mocker.patch("damai_appium.damai_app_v2.webdriver.Remote")

    DamaiBot()

    options = remote.call_args.kwargs["options"]
    assert options.capabilities["appium:deviceName"] == "configured-device"
    assert options.capabilities["appium:platformVersion"] == "15"
    assert options.capabilities["appium:newCommandTimeout"] == 86400


@pytest.mark.parametrize(
    ("text", "expected"),
    [("¥680.00", 680.0), ("合计：1,199 元", 1199.0), ("¥0", None), ("无金额", None), ("680 + 10", None)],
)
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


def test_buy_selector_is_exact():
    assert 'text("立即购买")' in BUY_NOW_SELECTOR[1]
    assert "预约" not in BUY_NOW_SELECTOR[1]


def test_reservation_flow_stops_before_submit_when_disabled():
    bot = make_bot(if_commit_order=False)

    assert bot.run_ticket_grabbing() is True

    assert bot.driver.execute_script.call_count == 1
    assert bot.driver.execute_script.call_args.args[1]["x"] == 250
    assert bot._submit_attempted is False
    bot.driver.quit.assert_called_once_with()


def test_reservation_flow_submits_once_after_validation():
    page = confirmation(states=(True, False, True), price="合计：1,199 元")
    bot = make_bot(if_commit_order=True, ticket_count=2, page=page)

    assert bot.run_ticket_grabbing() is True

    assert bot.driver.execute_script.call_count == 2
    submit_click = bot.driver.execute_script.call_args_list[1].args[1]
    assert submit_click["x"] == 350
    assert submit_click["y"] == 1820
    assert bot._submit_attempted is True


@pytest.mark.parametrize(
    ("page", "ticket_count", "message"),
    [
        (confirmation(price="待计算"), 1, "金额无效"),
        (confirmation(price="¥0"), 1, "金额无效"),
        (confirmation(states=(False,)), 1, "实际已选 0 位"),
        (confirmation(states=(True, True)), 1, "实际已选 2 位"),
    ],
)
def test_invalid_confirmation_stops_before_submit(page, ticket_count, message):
    bot = make_bot(if_commit_order=True, ticket_count=ticket_count, page=page)

    assert bot.run_ticket_grabbing() is False

    assert bot.driver.execute_script.call_count == 1
    assert bot._submit_attempted is False


def test_validate_confirmation_accepts_any_reserved_viewers():
    page = confirmation(states=(False, True, True), price="¥900")

    amount, selected = DamaiBot._validate_confirmation(page, 2)

    assert amount == 900
    assert selected == 2


def test_scheduled_wait_finishes_before_purchase_click():
    bot = make_bot(if_commit_order=False)
    events = []
    release = bot._wait_for_sale.return_value
    bot._wait_for_sale.side_effect = lambda: events.append("released") or release
    bot.driver.execute_script.side_effect = lambda *_args: events.append("clicked")

    assert bot.run_ticket_grabbing() is True

    assert events == ["released", "clicked"]
    bot._wait_for_sale.assert_called_once_with()


def test_past_sale_time_stops_before_purchase():
    bot = make_bot(if_commit_order=True)
    bot._wait_for_sale.side_effect = SaleTimePast("抢购时间已过")

    assert bot.run_ticket_grabbing() is False

    bot.wait.until.assert_not_called()
    bot.driver.execute_script.assert_not_called()
    assert bot._purchase_attempted is False
    assert bot._submit_attempted is False


def test_missing_buy_button_does_not_click_or_fall_back():
    bot = make_bot(if_commit_order=True)
    bot.wait.until.side_effect = TimeoutException()

    assert bot.run_ticket_grabbing() is False

    bot.driver.execute_script.assert_not_called()
    bot.driver.find_elements.assert_not_called()


def test_missing_confirmation_page_stops_after_purchase():
    bot = make_bot(if_commit_order=True)
    bot.wait.until.side_effect = [[element()], TimeoutException()]

    assert bot.run_ticket_grabbing() is False

    assert bot.driver.execute_script.call_count == 1
    assert bot._submit_attempted is False


def test_purchase_cannot_be_reentered():
    bot = make_bot(if_commit_order=False)
    assert bot.run_ticket_grabbing() is True
    bot.driver.execute_script.reset_mock()

    assert bot.run_ticket_grabbing() is False

    bot.driver.execute_script.assert_not_called()


def test_submit_click_exception_cannot_be_retried():
    bot = make_bot(if_commit_order=True)
    bot.driver.execute_script.side_effect = [None, RuntimeError("结果未知")]

    assert bot.run_ticket_grabbing() is False
    assert bot._submit_attempted is True
    assert bot.run_ticket_grabbing() is False
    assert bot.driver.execute_script.call_count == 2


def test_legacy_retry_entry_runs_only_once(mocker):
    bot = DamaiBot.__new__(DamaiBot)
    run = mocker.patch.object(bot, "run_ticket_grabbing", return_value=False)

    assert bot.run_with_retry(max_retries=3) is False
    run.assert_called_once_with()
