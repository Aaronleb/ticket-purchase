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
    bot.ultra_fast_click = Mock(return_value=True)
    bot.ultra_batch_click = Mock()
    mocker.patch("damai_appium.damai_app_v2.time.sleep")
    return bot


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


def test_commit_order_fails_when_submit_button_is_not_found(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot.smart_wait_and_click.side_effect = [True, True, False]

    assert bot.run_ticket_grabbing() is False
