# -*- coding: UTF-8 -*-
"""极速模式：确定只点一次，提交绝不重复。"""
import time
from types import SimpleNamespace
from unittest.mock import Mock

from damai_appium.damai_app_v2 import DamaiBot


def make_bot(commit=True):
    bot = DamaiBot.__new__(DamaiBot)
    bot.config = SimpleNamespace(
        confirm_point="950,2562",
        submit_point="941,2563",
        if_commit_order=commit,
    )
    bot._started_at = time.perf_counter()
    bot._submit_attempted = False
    bot._buy_bar_point = Mock(return_value=(764, 2557))
    bot._tap_buy_bar_until_next_page = Mock(return_value="NcovSkuActivity")
    bot._wait_drawn = Mock()
    bot._mark = Mock()
    bot._adb_tap = Mock()
    return bot


def tapped(bot):
    return [call.args for call in bot._adb_tap.call_args_list]


def test_normal_path_clicks_confirm_and_submit_once():
    bot = make_bot()
    bot._wait_any_activity = Mock(return_value="DmOrderActivity")
    bot._wait_activity_gone = Mock(return_value=True)

    assert bot._run_fast() is True

    assert tapped(bot) == [(950, 2562), (941, 2563)]


def test_confirm_retries_only_when_still_on_sku_page():
    """第一次确定真被吞掉（仍停在票档页）时才允许补点一次。"""
    bot = make_bot()
    bot._wait_any_activity = Mock(side_effect=["", "DmOrderActivity"])
    bot._foreground_activity = Mock(return_value="cn.damai/.sku.NcovSkuActivity")
    bot._wait_activity_gone = Mock(return_value=True)

    bot._run_fast()

    assert tapped(bot) == [(950, 2562), (950, 2562), (941, 2563)]


def test_confirm_does_not_retry_when_order_page_already_visible():
    """超时时确认页已经出现，只是 Activity 轮询没赶上：绝不能再点一次确定。"""
    bot = make_bot()
    bot._wait_any_activity = Mock(return_value="")
    bot._foreground_activity = Mock(return_value="cn.damai/.DmOrderActivity")
    bot._wait_activity_gone = Mock(return_value=True)

    bot._run_fast()

    assert tapped(bot) == [(950, 2562), (941, 2563)]


def test_confirm_does_not_tap_during_page_transition():
    """topResumedActivity 为空/切换中时只等，不点确定。"""
    bot = make_bot()
    bot._wait_any_activity = Mock(side_effect=["", "DmOrderActivity"])
    bot._foreground_activity = Mock(return_value="")
    bot._wait_activity_gone = Mock(return_value=True)

    bot._run_fast()

    assert tapped(bot) == [(950, 2562), (941, 2563)]
    assert bot._wait_any_activity.call_count == 2


def test_submit_is_clicked_once_even_when_order_page_stays():
    """提交后页面不离开也不能补点，否则就是重复提交。"""
    bot = make_bot()
    bot._wait_any_activity = Mock(return_value="DmOrderActivity")
    bot._wait_activity_gone = Mock(return_value=False)

    bot._run_fast()

    assert tapped(bot) == [(950, 2562), (941, 2563)]
    assert bot._submit_attempted is True


def test_submit_disabled_only_clicks_confirm():
    bot = make_bot(commit=False)
    bot._wait_any_activity = Mock(return_value="DmOrderActivity")

    bot._run_fast()

    assert tapped(bot) == [(950, 2562)]
