import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, mock_open

import pytest
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException

from damai_appium import damai_app_v2 as worker
from damai_appium.config import Config
from damai_appium.damai_app_v2 import DamaiBot


BASE_CONFIG = {
    "server_url": "http://127.0.0.1:4723",
    "keyword": "测试演出",
    "users": ["观演人"],
    "city": "深圳",
    "date": "12.06",
    "price": "内场1199元",
    "price_index": 5,
    "if_commit_order": True,
}


def test_load_config_uses_legacy_device_defaults(mocker):
    config_file = mock_open(read_data=json.dumps(BASE_CONFIG))
    mocker.patch("builtins.open", config_file)

    config = Config.load_config()

    assert config.device_name == "emulator-5554"
    assert config.platform_version == "16"
    assert config.sale_time == ""
    config_file.assert_called_once_with("config.jsonc", "r", encoding="utf-8")


def test_load_config_reads_sale_time(mocker):
    payload = dict(BASE_CONFIG, sale_time="2026-12-01T20:30:00")
    mocker.patch("builtins.open", mock_open(read_data=json.dumps(payload)))

    config = Config.load_config()

    assert config.sale_time == "2026-12-01T20:30:00"


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


def make_bot(mocker, if_commit_order):
    bot = DamaiBot.__new__(DamaiBot)
    bot.config = SimpleNamespace(
        city="深圳",
        users=["观演人"],
        price_index=5,
        if_commit_order=if_commit_order,
        sale_time="",
        ticket_area="内场",
    )
    bot.driver = Mock()
    bot.driver.find_elements.return_value = []
    bot.wait = Mock()
    bot.ticket_candidates = []
    bot._submit_attempted = False
    bot._load_ticket_candidates = Mock(return_value=[])
    bot._select_city = Mock(return_value=True)
    bot.wait_until_sale = Mock()
    bot._wait_for_purchase_button = Mock(return_value=Mock())
    bot._wait_for_price_page = Mock(return_value=True)
    bot._click_element = Mock(return_value=True)
    bot._set_quantity = Mock(return_value=True)
    bot.smart_wait_and_click = Mock(return_value=True)
    bot.select_ticket = Mock(return_value=True)
    bot.ultra_fast_click = Mock(return_value=True)
    bot.ultra_batch_click = Mock(return_value=True)
    mocker.patch("damai_appium.damai_app_v2.time.sleep")
    return bot


def test_existing_price_page_skips_city_and_booking(mocker):
    bot = make_bot(mocker, if_commit_order=False)

    def find_elements(by, value):
        if value == "cn.damai:id/project_detail_perform_price_flowlayout":
            return [Mock()]
        return []

    bot.driver.find_elements.side_effect = find_elements

    assert bot.run_ticket_grabbing() is True

    bot.smart_wait_and_click.assert_not_called()
    bot.ultra_fast_click.assert_called_once()
    bot.ultra_batch_click.assert_called_once()


def test_skip_submit_stops_after_selecting_users(mocker):
    bot = make_bot(mocker, if_commit_order=False)

    assert bot.run_ticket_grabbing() is True

    bot.ultra_batch_click.assert_called_once()
    assert bot.smart_wait_and_click.call_count == 0


def test_commit_order_clicks_submit(mocker):
    bot = make_bot(mocker, if_commit_order=True)

    assert bot.run_ticket_grabbing() is True

    assert bot.smart_wait_and_click.call_count == 1
    submit_call = bot.smart_wait_and_click.call_args_list[0]
    assert submit_call.args[1] == 'new UiSelector().text("立即提交")'


def test_viewer_checkbox_is_clicked_and_verified(mocker):
    bot = make_bot(mocker, if_commit_order=False)
    name = Mock()
    row = Mock()
    checkbox = Mock()
    checkbox.id = "checkbox-id"
    checkbox.get_attribute.side_effect = ["false", "true"]
    name.get_attribute.return_value = "观演人"
    bot.driver.find_element.return_value = row
    row.find_element.return_value = checkbox
    wait = Mock()
    wait.until.return_value = name
    mocker.patch("damai_appium.damai_app_v2.WebDriverWait", return_value=wait)

    assert DamaiBot.ultra_batch_click(bot, [("uiautomator", "观演人")]) is True

    bot._click_element.assert_called_once_with(checkbox)
    assert bot.driver.find_element.call_count == 2


def test_unselected_viewer_stops_before_submit(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot.ultra_batch_click.return_value = False

    assert bot.run_ticket_grabbing() is False

    assert bot.smart_wait_and_click.call_count == 0


def test_ticket_selection_failure_stops_before_buying(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot.select_ticket.return_value = False

    assert bot.run_ticket_grabbing() is False

    bot.ultra_fast_click.assert_not_called()
    bot.ultra_batch_click.assert_not_called()


def test_commit_order_fails_when_submit_button_is_not_found(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot.smart_wait_and_click.side_effect = [False]

    assert bot.run_ticket_grabbing() is False


def test_select_ticket_loads_cached_candidates_once(mocker):
    bot = DamaiBot.__new__(DamaiBot)
    bot.config = SimpleNamespace(ticket_area="内场")
    bot.driver = Mock()
    bot.ticket_candidates = None
    candidates = [{"index": 0, "price": 1599}]
    load = mocker.patch(
        "damai_appium.damai_app_v2.load_prepared_tickets",
        return_value=candidates,
    )
    select = mocker.patch(
        "damai_appium.damai_app_v2.select_prepared_ticket",
        return_value=True,
    )

    assert bot.select_ticket() is True
    assert bot.select_ticket() is True
    load.assert_called_once_with("ticket_candidates.json", "内场")
    assert select.call_count == 2
    select.assert_called_with(bot.driver, candidates)


def test_fast_click_uses_element_id_without_reading_rect(mocker):
    bot = DamaiBot.__new__(DamaiBot)
    bot.driver = Mock()
    element = Mock()
    element.id = "buy-element"
    wait = Mock()
    wait.until.return_value = element
    wait_class = mocker.patch("damai_appium.damai_app_v2.WebDriverWait", return_value=wait)

    assert bot.ultra_fast_click("id", "btn_buy_view") is True

    bot.driver.execute_script.assert_called_once_with(
        "mobile: clickGesture", {"elementId": "buy-element"}
    )
    assert not element.rect.called
    assert wait_class.call_args.kwargs["poll_frequency"] == 0.1


def make_purchase_element(text, enabled="true", clickable="true"):
    element = Mock()
    element.get_attribute.side_effect = lambda name: {
        "enabled": enabled,
        "clickable": clickable,
        "text": text,
    }[name]
    return element


@pytest.mark.parametrize("text", ["预约", "立即预约", "缺货登记", "售罄", "无票"])
def test_purchase_state_rejects_unavailable_text(text):
    bot = DamaiBot.__new__(DamaiBot)
    assert bot._element_is_purchasable(make_purchase_element(text)) is False


@pytest.mark.parametrize("attributes", [{"enabled": "false"}, {"clickable": "false"}])
def test_purchase_state_requires_enabled_and_clickable(attributes):
    bot = DamaiBot.__new__(DamaiBot)
    assert bot._element_is_purchasable(make_purchase_element("立即购买", **attributes)) is False


def test_purchase_state_accepts_explicit_purchase_text():
    bot = DamaiBot.__new__(DamaiBot)
    assert bot._element_is_purchasable(make_purchase_element("立即购买")) is True


def test_purchase_button_polls_until_available_without_clicking(mocker):
    bot = DamaiBot.__new__(DamaiBot)
    bot.driver = Mock()
    reservation = make_purchase_element("立即预约")
    purchase = make_purchase_element("立即购买")
    bot.driver.find_elements.side_effect = [[reservation], [], [purchase], []]
    mocker.patch("damai_appium.damai_app_v2.time.monotonic", side_effect=[0, 0.05])
    sleep = mocker.patch("damai_appium.damai_app_v2.time.sleep")

    assert bot._wait_for_purchase_button(timeout=1, poll_interval=0.1) is purchase

    sleep.assert_called_once_with(0.1)
    bot.driver.execute_script.assert_not_called()


def test_purchase_button_timeout_returns_none_without_clicking(mocker):
    bot = DamaiBot.__new__(DamaiBot)
    bot.driver = Mock()
    bot.driver.find_elements.return_value = []
    mocker.patch("damai_appium.damai_app_v2.time.monotonic", side_effect=[0, 0.05, 0.2])
    mocker.patch("damai_appium.damai_app_v2.time.sleep")

    assert bot._wait_for_purchase_button(timeout=0.1, poll_interval=0.1) is None

    bot.driver.execute_script.assert_not_called()


def test_seconds_until_sale_handles_immediate_modes():
    now = datetime(2026, 12, 1, 20, 29, 50)
    assert worker.seconds_until_sale("", now) == 0
    assert worker.seconds_until_sale("2026-12-01T20:29:50", now) == 0
    assert worker.seconds_until_sale("2026-12-01T20:30:00", now) == 10


def test_wait_until_sale_rejects_invalid_legacy_value():
    bot = DamaiBot.__new__(DamaiBot)
    bot.config = SimpleNamespace(sale_time="not-a-date")
    with pytest.raises(ValueError, match="开售时间"):
        bot.wait_until_sale()


@pytest.mark.parametrize("sale_time", ["", "2026-12-01T20:29:50"])
def test_wait_until_sale_skips_immediate_modes(mocker, sale_time):
    bot = DamaiBot.__new__(DamaiBot)
    bot.config = SimpleNamespace(sale_time=sale_time)
    wait = mocker.patch("damai_appium.damai_app_v2.wait_for_deadline")

    bot.wait_until_sale(now=datetime(2026, 12, 1, 20, 29, 50))

    wait.assert_not_called()


def test_wait_until_sale_uses_monotonic_deadline_and_keepalive(mocker):
    bot = DamaiBot.__new__(DamaiBot)
    bot.config = SimpleNamespace(sale_time="2026-12-01T20:30:00")
    bot.driver = Mock()
    wait = mocker.patch("damai_appium.damai_app_v2.wait_for_deadline")
    mocker.patch("damai_appium.damai_app_v2.time.monotonic", return_value=100)

    bot.wait_until_sale(now=datetime(2026, 12, 1, 20, 29, 50))

    assert wait.call_args.args[0] == 110
    assert wait.call_args.kwargs["keepalive"] == bot._session_keepalive


class FakeClock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


def test_long_wait_keeps_session_alive_every_sixty_seconds():
    clock = FakeClock()
    keepalive = Mock()

    worker.wait_for_deadline(125, clock=clock, sleep=clock.sleep, keepalive=keepalive)

    assert clock.value == 125
    assert keepalive.call_count == 2
    assert max(clock.sleeps) <= 1


def test_preload_sale_wait_and_purchase_run_in_order(mocker):
    bot = make_bot(mocker, if_commit_order=False)
    calls = []
    bot._load_ticket_candidates.side_effect = lambda: calls.append("preload") or []
    bot.wait_until_sale.side_effect = lambda: calls.append("sale")
    purchase = Mock()
    bot._wait_for_purchase_button.side_effect = lambda **kwargs: calls.append("purchase") or purchase
    bot._wait_for_price_page.side_effect = lambda: calls.append("price") or True
    bot.select_ticket.side_effect = lambda: calls.append("ticket") or True

    assert bot.run_ticket_grabbing() is True

    assert calls == ["preload", "sale", "purchase", "price", "ticket"]
    bot._wait_for_purchase_button.assert_called_once_with(timeout=1.5, poll_interval=0.1)


def test_scheduled_mode_waits_thirty_seconds_for_purchase(mocker):
    bot = make_bot(mocker, if_commit_order=False)
    bot.config.sale_time = "2026-12-01T20:30:00"

    assert bot.run_ticket_grabbing() is True

    bot._wait_for_purchase_button.assert_called_once_with(timeout=30, poll_interval=0.1)


def test_unreadable_quantity_stops_before_buy_and_submit(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot._set_quantity.return_value = False

    assert bot.run_ticket_grabbing() is False

    bot.ultra_fast_click.assert_not_called()
    bot.ultra_batch_click.assert_not_called()


def test_sale_button_timeout_does_not_click_or_recreate_session(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot._wait_for_purchase_button.return_value = None

    assert bot.run_ticket_grabbing() is False

    bot._click_element.assert_not_called()
    bot.driver.quit.assert_not_called()


def test_set_quantity_clicks_plus_and_verifies_unique_number(mocker):
    bot = make_bot(mocker, if_commit_order=False)
    bot.config.users = ["观演人甲", "观演人乙"]
    container = Mock()
    container.get_attribute.return_value = "2张"
    plus = Mock()
    bot.driver.find_elements.return_value = [container]
    bot.driver.find_element.return_value = plus

    assert DamaiBot._set_quantity(bot) is True

    bot._click_element.assert_called_once_with(plus)


@pytest.mark.parametrize("text", ["", "1张", "1张共2件"])
def test_set_quantity_rejects_unreadable_or_wrong_number(mocker, text):
    bot = make_bot(mocker, if_commit_order=True)
    bot.config.users = ["观演人甲", "观演人乙"]
    container = Mock()
    container.get_attribute.return_value = text
    bot.driver.find_elements.return_value = [container]
    bot.driver.find_element.return_value = Mock()

    assert DamaiBot._set_quantity(bot) is False


def test_set_quantity_requires_selector_for_multiple_viewers(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot.config.users = ["观演人甲", "观演人乙"]
    bot.driver.find_elements.return_value = []

    assert DamaiBot._set_quantity(bot) is False


def test_set_quantity_allows_single_viewer_without_selector(mocker):
    bot = make_bot(mocker, if_commit_order=False)
    bot.driver.find_elements.return_value = []

    assert DamaiBot._set_quantity(bot) is True


def test_business_failure_does_not_recreate_session(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot.run_ticket_grabbing = Mock(return_value=False)
    bot._setup_driver = Mock()

    assert bot.run_with_retry() is False

    assert bot.run_ticket_grabbing.call_count == 1
    bot._setup_driver.assert_not_called()
    bot.driver.quit.assert_called_once_with()


def test_webdriver_failure_recreates_session_before_submit(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot.run_ticket_grabbing = Mock(side_effect=[WebDriverException("lost"), True])
    bot._setup_driver = Mock()

    assert bot.run_with_retry() is True

    assert bot.run_ticket_grabbing.call_count == 2
    bot._setup_driver.assert_called_once_with()
    bot.driver.quit.assert_called()


def test_webdriver_failure_after_submit_does_not_retry(mocker, capsys):
    bot = make_bot(mocker, if_commit_order=True)
    bot._submit_attempted = True
    bot.run_ticket_grabbing = Mock(side_effect=WebDriverException("lost after submit"))
    bot._setup_driver = Mock()

    assert bot.run_with_retry() is False

    bot._setup_driver.assert_not_called()
    assert bot.run_ticket_grabbing.call_count == 1
    assert "订单状态未知" in capsys.readouterr().out


@pytest.mark.parametrize(
    "value",
    ["2026-12-01", "2026-12-01T20", "2026-12-01 20:30:00", 123],
)
def test_seconds_until_sale_rejects_nonstandard_values(value):
    with pytest.raises(ValueError, match="开售时间"):
        worker.seconds_until_sale(value, datetime(2026, 12, 1, 20, 0, 0))


def test_invalid_sale_time_stops_before_cache_or_city(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot.config.sale_time = "2026-12-01"

    assert bot.run_ticket_grabbing() is False

    bot._load_ticket_candidates.assert_not_called()
    bot._select_city.assert_not_called()


def test_purchase_target_uses_clickable_ancestor_for_text_node():
    bot = DamaiBot.__new__(DamaiBot)
    text_node = make_purchase_element("立即购买", clickable="false")
    target = Mock()
    target.get_attribute.side_effect = lambda name: {
        "enabled": "true",
        "clickable": "true",
    }[name]
    text_node.find_element.return_value = target

    assert bot._purchase_target(text_node) is target


def test_purchase_state_accepts_immediate_grabbing():
    bot = DamaiBot.__new__(DamaiBot)
    assert bot._element_is_purchasable(make_purchase_element("立即抢票")) is True


def test_purchase_click_waits_for_price_page(mocker):
    bot = make_bot(mocker, if_commit_order=False)
    bot._wait_for_price_page = Mock(return_value=False)

    assert bot.run_ticket_grabbing() is False

    bot._wait_for_price_page.assert_called_once_with()
    bot.select_ticket.assert_not_called()


def test_wait_for_price_page_times_out_with_short_poll(mocker):
    bot = DamaiBot.__new__(DamaiBot)
    bot.driver = Mock()
    wait = Mock()
    wait.until.side_effect = TimeoutException
    wait_class = mocker.patch("damai_appium.damai_app_v2.WebDriverWait", return_value=wait)

    assert bot._wait_for_price_page() is False

    assert wait_class.call_args.kwargs["poll_frequency"] == 0.1


def test_missing_quantity_plus_is_business_failure(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot.config.users = ["观演人甲", "观演人乙"]
    bot.driver.find_elements.return_value = [Mock()]
    bot.driver.find_element.side_effect = NoSuchElementException("missing plus")

    assert DamaiBot._set_quantity(bot) is False


def test_quantity_reloads_plus_before_each_click(mocker):
    bot = make_bot(mocker, if_commit_order=False)
    bot.config.users = ["甲", "乙", "丙"]
    container = Mock()
    container.get_attribute.return_value = "3张"
    bot.driver.find_elements.return_value = [container]
    bot.driver.find_element.side_effect = [Mock(), Mock()]

    assert DamaiBot._set_quantity(bot) is True

    assert bot.driver.find_element.call_count == 2
    assert bot._click_element.call_count == 2


def test_missing_ticket_page_is_business_failure(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot.select_ticket.side_effect = NoSuchElementException("price page gone")

    assert bot.run_ticket_grabbing() is False


def test_submit_webdriver_exception_marks_attempt_and_prevents_reconnect(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot.smart_wait_and_click.side_effect = WebDriverException("submit connection lost")
    bot._setup_driver = Mock()

    assert bot.run_with_retry() is False

    assert bot._submit_attempted is True
    bot._setup_driver.assert_not_called()


def test_webdriver_exception_before_submit_keeps_retry_eligible(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot.select_ticket.side_effect = WebDriverException("ticket page connection lost")

    with pytest.raises(WebDriverException):
        bot.run_ticket_grabbing()

    assert bot._submit_attempted is False


def test_session_reconnect_stops_after_three_attempts(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot.run_ticket_grabbing = Mock(side_effect=WebDriverException("still lost"))
    bot._setup_driver = Mock()

    assert bot.run_with_retry() is False

    assert bot.run_ticket_grabbing.call_count == 3
    assert bot._setup_driver.call_count == 2


def make_fast_bot(mocker, layout, if_commit_order=True):
    bot = make_bot(mocker, if_commit_order=if_commit_order)
    bot.layout = layout
    bot.config.fast_mode = True
    bot.config.device_name = "device-1"
    return bot


def test_fast_path_uses_cached_ticket_coordinates(mocker):
    from damai_appium.layout_cache import LayoutSnapshot

    layout = LayoutSnapshot((1080, 2400), {"card_0": {"x": 200, "y": 300}})
    bot = make_fast_bot(mocker, layout)
    bot.ticket_candidates = [{"index": 0, "price": 999}]
    bot.driver.find_elements.return_value = []

    assert bot._select_ticket_step() is True

    bot.driver.execute_script.assert_called_once_with(
        "mobile: clickGesture", {"x": 200, "y": 300}
    )
    bot.select_ticket.assert_not_called()


def test_fast_path_falls_back_to_slow_ticket_selection(mocker):
    from damai_appium.layout_cache import LayoutSnapshot

    layout = LayoutSnapshot((1080, 2400), {})
    bot = make_fast_bot(mocker, layout)
    bot.ticket_candidates = [{"index": 0, "price": 999}]

    assert bot._select_ticket_step() is True

    bot.select_ticket.assert_called_once()
    bot.driver.execute_script.assert_not_called()


def test_fast_path_skips_when_disabled(mocker):
    from damai_appium.layout_cache import LayoutSnapshot

    bot = make_fast_bot(mocker, None)
    bot.config.fast_mode = False
    snapshot = LayoutSnapshot((1080, 2400), {"card_0": {"x": 1, "y": 2}})
    mocker.patch("damai_appium.damai_app_v2.load_snapshot", return_value=snapshot)

    bot._prepare_fast_path()

    assert bot.layout is None


def test_fast_path_degrades_on_screen_mismatch(mocker):
    from damai_appium.layout_cache import LayoutSnapshot

    snapshot = LayoutSnapshot((1080, 2400), {"card_0": {"x": 1, "y": 2}})
    mocker.patch("damai_appium.damai_app_v2.load_snapshot", return_value=snapshot)
    bot = make_bot(mocker, if_commit_order=True)
    bot.config.fast_mode = True
    bot.driver.get_window_size.return_value = {"width": 720, "height": 1600}

    bot._prepare_fast_path()

    assert bot.layout is None


def test_confirm_purchase_uses_cached_coordinates(mocker):
    from damai_appium.layout_cache import LayoutSnapshot

    layout = LayoutSnapshot((1080, 2400), {"confirm": {"x": 540, "y": 2200}})
    bot = make_fast_bot(mocker, layout)

    assert bot._confirm_purchase_step() is True

    bot.driver.execute_script.assert_called_once_with(
        "mobile: clickGesture", {"x": 540, "y": 2200}
    )
    bot.ultra_fast_click.assert_not_called()


def test_confirm_purchase_falls_back_without_cache(mocker):
    bot = make_fast_bot(mocker, None)

    assert bot._confirm_purchase_step() is True

    bot.ultra_fast_click.assert_called_once_with("id", "btn_buy_view")


def test_viewers_prefer_cached_coordinates(mocker):
    from damai_appium.layout_cache import LayoutSnapshot

    layout = LayoutSnapshot((1080, 2400), {"viewer_观演人": {"x": 90, "y": 800}})
    bot = make_fast_bot(mocker, layout)

    assert bot._select_viewers_step() is True

    bot.driver.execute_script.assert_called_once_with(
        "mobile: clickGesture", {"x": 90, "y": 800}
    )
    bot.ultra_batch_click.assert_not_called()


def test_viewers_fall_back_when_coordinates_missing(mocker):
    from damai_appium.layout_cache import LayoutSnapshot

    layout = LayoutSnapshot((1080, 2400), {})
    bot = make_fast_bot(mocker, layout)

    assert bot._select_viewers_step() is True

    bot.ultra_batch_click.assert_called_once()


def test_quantity_uses_cached_plus_coordinates(mocker):
    from damai_appium.layout_cache import LayoutSnapshot

    bot = make_bot(mocker, if_commit_order=False)
    bot.config.users = ["甲", "乙"]
    bot.layout = LayoutSnapshot((1080, 2400), {"quantity_plus": {"x": 700, "y": 1200}})
    container = Mock()
    container.get_attribute.return_value = "2张"
    bot.driver.find_elements.return_value = [container]

    assert DamaiBot._set_quantity(bot) is True

    bot.driver.find_element.assert_not_called()
    bot.driver.execute_script.assert_called_once_with(
        "mobile: clickGesture", {"x": 700, "y": 1200}
    )


def test_quantity_without_cache_still_finds_plus(mocker):
    bot = make_bot(mocker, if_commit_order=False)
    bot.config.users = ["甲", "乙"]
    bot.layout = None
    container = Mock()
    container.get_attribute.return_value = "2张"
    bot.driver.find_elements.return_value = [container]
    bot.driver.find_element.return_value = Mock()

    assert DamaiBot._set_quantity(bot) is True

    bot._click_element.assert_called_once()
