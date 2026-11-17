from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from damai_appium.damai_app_v2 import DamaiBot


BUY = (764, 2557)
CONFIRM = (950, 2562)
SUBMIT = (941, 2563)


def make_bot(markers, commit=True):
    bot = DamaiBot.__new__(DamaiBot)
    bot.config = SimpleNamespace(
        confirm_point="950,2562",
        submit_point="941,2563",
        if_commit_order=commit,
    )
    bot._page_watch = Mock()
    bot._ensure_detail_page = Mock()
    bot._buy_bar_point = Mock(return_value=BUY)
    bot._resolve_point = Mock(side_effect=[CONFIRM, SUBMIT])
    bot._adb_tap = Mock()
    bot._mark = Mock()
    bot._wait_purchase_activity = Mock(side_effect=markers)
    bot._wait_button_template = Mock()
    bot._submit_attempted = False
    bot._started_at = 0
    return bot


def test_fast_path_clicks_each_button_exactly_once():
    bot = make_bot(["NcovSkuActivity", "DmOrderActivity"])

    assert bot._run_fast() is True

    assert bot._adb_tap.call_args_list == [call(*BUY), call(*CONFIRM), call(*SUBMIT)]
    assert bot._wait_button_template.call_args_list == [
        call("confirm", "NcovSkuActivity"),
        call("submit", "DmOrderActivity"),
    ]
    assert bot._submit_attempted is True


def test_direct_order_page_skips_confirm_and_clicks_submit_once():
    bot = make_bot(["DmOrderActivity"])

    assert bot._run_fast() is True

    assert bot._adb_tap.call_args_list == [call(*BUY), call(*SUBMIT)]
    bot._wait_button_template.assert_called_once_with("submit", "DmOrderActivity")


def test_auto_submit_disabled_stops_before_submit():
    bot = make_bot(["NcovSkuActivity", "DmOrderActivity"], commit=False)

    assert bot._run_fast() is True

    assert bot._adb_tap.call_args_list == [call(*BUY), call(*CONFIRM)]
    bot._wait_button_template.assert_called_with("submit", "DmOrderActivity")
    assert bot._submit_attempted is False


def test_empty_activity_waits_without_another_tap():
    bot = DamaiBot.__new__(DamaiBot)
    bot._foreground_activity = Mock(side_effect=["", "NcovSkuActivity"])

    marker = bot._wait_purchase_activity(
        ("NcovSkuActivity",), ("ProjectDetailActivity",), timeout=1.0
    )

    assert marker == "NcovSkuActivity"
    assert bot._foreground_activity.call_count == 2


def test_unexpected_activity_stops_immediately():
    bot = DamaiBot.__new__(DamaiBot)
    bot._foreground_activity = Mock(return_value="PaymentActivity")

    with pytest.raises(RuntimeError, match="非预期页面"):
        bot._wait_purchase_activity(
            ("NcovSkuActivity",), ("ProjectDetailActivity",), timeout=1.0
        )


def test_template_hit_on_changed_activity_does_not_click():
    bot = DamaiBot.__new__(DamaiBot)
    bot._page_watch = Mock()
    bot._page_watch.wait_template.return_value = True
    bot._foreground_activity = Mock(return_value="ProjectDetailActivity")

    with pytest.raises(RuntimeError, match="页面已变为"):
        bot._wait_button_template("confirm", "NcovSkuActivity")


def test_close_page_watch_is_idempotent():
    bot = DamaiBot.__new__(DamaiBot)
    bot._page_watch = Mock()
    watch = bot._page_watch

    bot._close_page_watch()
    bot._close_page_watch()

    watch.close.assert_called_once()
    assert bot._page_watch is None
