import json
from types import SimpleNamespace
from unittest.mock import Mock, mock_open

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
    config_file.assert_called_once_with("config.jsonc", "r", encoding="utf-8")


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
    )
    bot.driver = Mock()
    bot.driver.find_elements.return_value = []
    bot.wait = Mock()
    bot.smart_wait_and_click = Mock(return_value=True)
    bot.select_ticket = Mock(return_value=True)
    bot.ultra_fast_click = Mock(return_value=True)
    bot.ultra_batch_click = Mock()
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
    assert bot.smart_wait_and_click.call_count == 2


def test_commit_order_clicks_submit(mocker):
    bot = make_bot(mocker, if_commit_order=True)

    assert bot.run_ticket_grabbing() is True

    assert bot.smart_wait_and_click.call_count == 3
    submit_call = bot.smart_wait_and_click.call_args_list[2]
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

    bot.driver.execute_script.assert_called_once_with(
        "mobile: clickGesture", {"elementId": "checkbox-id"}
    )
    assert bot.driver.find_element.call_count == 2


def test_unselected_viewer_stops_before_submit(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot.ultra_batch_click.return_value = False

    assert bot.run_ticket_grabbing() is False

    assert bot.smart_wait_and_click.call_count == 2


def test_ticket_selection_failure_stops_before_buying(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot.select_ticket.return_value = False

    assert bot.run_ticket_grabbing() is False

    bot.ultra_fast_click.assert_not_called()
    bot.ultra_batch_click.assert_not_called()


def test_commit_order_fails_when_submit_button_is_not_found(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot.smart_wait_and_click.side_effect = [True, True, False]

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
