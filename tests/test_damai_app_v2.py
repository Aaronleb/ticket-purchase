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


def test_load_config_uses_legacy_defaults(mocker):
    config_file = mock_open(read_data=json.dumps(BASE_CONFIG))
    mocker.patch("builtins.open", config_file)

    config = Config.load_config()

    assert config.device_name == "emulator-5554"
    assert config.platform_version == "16"
    assert config.ticket_count == 1
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


def make_bot(mocker, if_commit_order):
    bot = DamaiBot.__new__(DamaiBot)
    bot.config = SimpleNamespace(
        city="深圳",
        users=["观演人"],
        price_index=5,
        ticket_count=1,
        if_commit_order=if_commit_order,
    )
    bot.driver = Mock()
    bot.driver.find_elements.return_value = []
    bot.wait = Mock()
    bot.smart_wait_and_click = Mock(return_value=True)
    bot.ultra_fast_click = Mock(return_value=True)
    bot.select_viewers = Mock(return_value=True)
    mocker.patch("damai_appium.damai_app_v2.time.sleep")
    return bot


def test_existing_price_page_skips_city_and_booking(mocker):
    bot = make_bot(mocker, if_commit_order=False)

    def find_elements(by=None, value=None, **kwargs):
        value = value or kwargs.get("value")
        if value == "cn.damai:id/project_detail_perform_price_flowlayout":
            return [Mock()]
        return []

    bot.driver.find_elements.side_effect = find_elements

    assert bot.run_ticket_grabbing() is True

    bot.smart_wait_and_click.assert_not_called()
    bot.ultra_fast_click.assert_called_once()
    bot.select_viewers.assert_called_once_with(1)


def test_skip_submit_stops_after_selecting_users(mocker):
    bot = make_bot(mocker, if_commit_order=False)

    assert bot.run_ticket_grabbing() is True

    bot.select_viewers.assert_called_once_with(1)
    assert bot.smart_wait_and_click.call_count == 2


def test_commit_order_clicks_submit(mocker):
    bot = make_bot(mocker, if_commit_order=True)

    assert bot.run_ticket_grabbing() is True

    assert bot.smart_wait_and_click.call_count == 3
    submit_call = bot.smart_wait_and_click.call_args_list[2]
    assert submit_call.args[1] == 'new UiSelector().text("立即提交")'


def test_ticket_count_clicks_plus_n_minus_one(mocker):
    bot = make_bot(mocker, if_commit_order=False)
    bot.config.ticket_count = 3
    plus_button = Mock()
    plus_button.rect = {"x": 10, "y": 20, "width": 30, "height": 40}

    def find_elements(by=None, value=None, **kwargs):
        value = value or kwargs.get("value")
        if value in {"cn.damai:id/project_detail_perform_price_flowlayout", "layout_num"}:
            return [Mock()]
        return []

    def find_element(by, value):
        return plus_button if value == "img_jia" else Mock()

    bot.driver.find_elements.side_effect = find_elements
    bot.driver.find_element.side_effect = find_element

    assert bot.run_ticket_grabbing() is True

    plus_clicks = [
        call for call in bot.driver.execute_script.call_args_list
        if "x" in call.args[1]
    ]
    assert len(plus_clicks) == 2
    bot.select_viewers.assert_called_once_with(3)


def test_missing_quantity_control_stops_before_buying(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot.config.ticket_count = 2

    assert bot.run_ticket_grabbing() is False

    bot.ultra_fast_click.assert_not_called()
    bot.select_viewers.assert_not_called()


def make_viewer_rows(states):
    rows = []
    for index in range(len(states)):
        row = Mock()
        checkbox = Mock()
        checkbox.id = f"checkbox-{index}"
        checkbox.get_attribute.side_effect = (
            lambda name, index=index: "true" if states[index] else "false"
        )
        row.find_element.return_value = checkbox
        rows.append(row)
    return rows


def make_viewer_bot(mocker, states):
    bot = DamaiBot.__new__(DamaiBot)
    bot.driver = Mock()
    rows = make_viewer_rows(states)
    bot.driver.find_elements.return_value = rows
    wait = Mock()
    wait.until.return_value = rows
    mocker.patch("damai_appium.damai_app_v2.WebDriverWait", return_value=wait)

    def toggle_checkbox(script, payload):
        index = int(payload["elementId"].rsplit("-", 1)[1])
        states[index] = not states[index]

    bot.driver.execute_script.side_effect = toggle_checkbox
    return bot


def test_select_viewers_selects_first_n_and_unselects_extras(mocker):
    states = [True, False, True]
    bot = make_viewer_bot(mocker, states)

    assert bot.select_viewers(2) is True

    assert states == [True, True, False]
    clicked = [call.args[1]["elementId"] for call in bot.driver.execute_script.call_args_list]
    assert clicked == ["checkbox-1", "checkbox-2"]


def test_select_viewers_rejects_insufficient_viewers(mocker):
    bot = make_viewer_bot(mocker, [True])

    assert bot.select_viewers(2) is False
    bot.driver.execute_script.assert_not_called()


def test_select_viewers_rejects_checkbox_verification_failure(mocker):
    bot = make_viewer_bot(mocker, [False])
    bot.driver.execute_script.side_effect = None

    assert bot.select_viewers(1) is False


def test_unselected_viewer_stops_before_submit(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot.select_viewers.return_value = False

    assert bot.run_ticket_grabbing() is False

    assert bot.smart_wait_and_click.call_count == 2


def test_commit_order_fails_when_submit_button_is_not_found(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot.smart_wait_and_click.side_effect = [True, True, False]

    assert bot.run_ticket_grabbing() is False
