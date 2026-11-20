# -*- coding: UTF-8 -*-
"""极速模式：确定只点一次，提交绝不重复。"""
import json
import time
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from damai_appium.config import Config
from damai_appium.damai_app_v2 import DamaiBot


def make_bot(commit=True):
    bot = DamaiBot.__new__(DamaiBot)
    bot.config = SimpleNamespace(
        confirm_point="950,2562",
        submit_point="941,2563",
        if_commit_order=commit,
        submit_settle_seconds=0,
    )
    bot._started_at = time.perf_counter()
    bot._submit_attempted = False
    bot._buy_bar_point = Mock(return_value=(764, 2557))
    bot._tap_buy_bar_until_next_page = Mock(return_value="NcovSkuActivity")
    bot._wait_drawn = Mock()
    bot._mark = Mock()
    bot._adb_tap = Mock()
    bot._confirm_snapshot = Mock(return_value=None)
    return bot


def tapped(bot):
    return [call.args for call in bot._adb_tap.call_args_list]


def test_normal_path_clicks_confirm_and_submit_once():
    bot = make_bot()
    bot._wait_any_activity = Mock(return_value="DmOrderActivity")
    bot._wait_activity_gone = Mock(return_value=True)

    assert bot._run_fast() is True

    assert tapped(bot) == [(950, 2562), (941, 2563)]
    assert bot._submit_verified is True


def test_confirm_retries_once_when_first_tap_lost():
    """预约模式：首次确定没反应时补点一次，但不做 OCR 选票档。"""
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
    """topResumedActivity 为空/切换中时只等，不补点确定。"""
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
    assert bot._submit_verified is False


def test_submit_disabled_only_clicks_confirm():
    bot = make_bot(commit=False)
    bot._wait_any_activity = Mock(return_value="DmOrderActivity")

    bot._run_fast()

    assert tapped(bot) == [(950, 2562)]


def test_dry_run_stops_at_confirm_page_without_submit():
    """演练模式即使配置开了自动提交，也只能点“确定”，绝不点“立即提交”。"""
    bot = make_bot(commit=True)
    bot.config.dry_run = True
    bot._wait_any_activity = Mock(return_value="DmOrderActivity")

    assert bot._run_fast() is True

    assert tapped(bot) == [(950, 2562)]
    assert bot._submit_attempted is False


class FakeWatcher:
    """只实现 Bot 用到的事件接口，验证事件优先、dumpsys 兜底。"""

    def __init__(self, displayed=(), started=()):
        self.displayed = set(displayed)
        self.started = set(started)

    def wait_any_displayed(self, markers, timeout, since=None):
        for marker in markers:
            if marker in self.displayed:
                return marker
        return ""

    def wait_displayed(self, marker, timeout, since=None):
        return marker in self.displayed

    def seen_displayed(self, marker, since=None):
        return marker in self.displayed

    def wait_any_started(self, markers, timeout, since=None):
        for marker in markers:
            if marker in self.started:
                return marker
        return ""


def test_wait_any_activity_uses_event_without_dumpsys():
    bot = DamaiBot.__new__(DamaiBot)
    bot._activity_watch = FakeWatcher(displayed={"DmOrderActivity"})
    bot._foreground_activity = Mock(side_effect=AssertionError("不应再轮询"))

    assert bot._wait_any_activity(
        ("DmOrderActivity",), time.perf_counter() + 1.0
    ) == "DmOrderActivity"


def test_wait_any_activity_falls_back_to_one_dumpsys():
    bot = DamaiBot.__new__(DamaiBot)
    bot._activity_watch = FakeWatcher()
    bot._foreground_activity = Mock(return_value="cn.damai/.DmOrderActivity")

    assert bot._wait_any_activity(
        ("DmOrderActivity",), time.perf_counter() + 0.01
    ) == "DmOrderActivity"
    assert bot._foreground_activity.call_count == 1


def test_wait_drawn_uses_event_signal():
    bot = DamaiBot.__new__(DamaiBot)
    bot._activity_watch = FakeWatcher(displayed={"DmOrderActivity"})
    bot._activity_drawn = Mock(side_effect=AssertionError("不应再 dumpsys"))

    assert bot._wait_drawn("DmOrderActivity", "确认页") is True


def test_buy_bar_uses_start_event():
    bot = DamaiBot.__new__(DamaiBot)
    bot._activity_watch = FakeWatcher(started={"NcovSkuActivity"})
    bot._foreground_activity = Mock(side_effect=AssertionError("不应再轮询"))
    bot._adb_tap = Mock()
    bot._mark = Mock()

    assert bot._tap_buy_bar_until_next_page((100, 200)) == "NcovSkuActivity"
    assert bot._adb_tap.call_count == 1


def test_extract_xml_strips_uiautomator_noise():
    raw = (
        "UI hierchary dumped to: /dev/tty\n"
        "<?xml version='1.0'?><hierarchy></hierarchy>\n"
    )

    assert DamaiBot._extract_xml(raw) == (
        "<?xml version='1.0'?><hierarchy></hierarchy>"
    )


def test_ui_dump_uses_single_dev_tty_round_trip():
    bot = DamaiBot.__new__(DamaiBot)
    calls = []

    def fake_adb(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(
            stdout="<?xml version='1.0'?><hierarchy><node/></hierarchy>"
        )

    bot._adb = fake_adb

    root = bot._ui_dump()

    assert root is not None
    assert calls == [
        ("shell", "uiautomator", "dump", "--compressed", "/dev/tty")
    ]


def test_adb_size_is_cached():
    bot = DamaiBot.__new__(DamaiBot)
    bot._adb = Mock(return_value=SimpleNamespace(stdout="Physical size: 1220x2712"))

    assert bot._adb_size() == (1220, 2712)
    assert bot._adb_size() == (1220, 2712)
    assert bot._adb.call_count == 1


def test_config_dry_run_forces_commit_off():
    config = Config("http://127.0.0.1:4723", True, dry_run=True)
    assert config.dry_run is True
    assert config.if_commit_order is False


def test_config_run_now_implies_dry_run():
    config = Config("http://127.0.0.1:4723", True, run_now=True)
    assert config.run_now is True
    assert config.dry_run is True
    assert config.if_commit_order is False


def test_prepare_buy_point_detects_presale_page():
    bot = DamaiBot.__new__(DamaiBot)
    bot.config = SimpleNamespace(buy_point="", keyword="陆虎")
    bot._buy_point = None
    bot._presale_detected = False
    bot._adb_size = Mock(return_value=(1220, 2712))
    bot._foreground_activity = Mock(return_value="cn.damai/.homepage.MainActivity")
    bot._ui_dump = Mock(
        return_value=ET.fromstring(
            """
            <hierarchy>
              <node resource-id="cn.damai:id/info_v2_title_tv1"
                    text="北京·2026陆虎巡演" />
              <node resource-id="cn.damai:id/tour_city_name_state_desc"
                    text="预约" />
              <node resource-id="cn.damai:id/project_item_bottom_want_to_see_fl"
                    text="想看" />
            </hierarchy>
            """
        )
    )
    bot._locate_buy_point_by_ocr = Mock(return_value=None)

    bot._prepare_buy_point()

    assert bot._presale_detected is True
    assert bot._buy_point is None


def test_prepare_buy_point_trusts_project_detail_activity_without_markers():
    bot = DamaiBot.__new__(DamaiBot)
    bot.config = SimpleNamespace(buy_point="", keyword="陆虎")
    bot._buy_point = None
    bot._presale_detected = False
    bot._adb_size = Mock(return_value=(1220, 2712))
    bot._foreground_activity = Mock(
        return_value="cn.damai/.trade.newtradeorder.ui.projectdetail.ProjectDetailActivity"
    )
    bot._ui_dump = Mock(
        return_value=ET.fromstring(
            "<hierarchy><node text='陆虎'/></hierarchy>"
        )
    )
    bot._locate_buy_point_by_ocr = Mock(return_value=(765, 2556))

    bot._prepare_buy_point()

    assert bot._buy_point == (765, 2556)
    assert bot._presale_detected is False


def test_prepare_buy_point_uses_ocr_for_canvas_bottom_bar():
    bot = DamaiBot.__new__(DamaiBot)
    bot.config = SimpleNamespace(buy_point="", keyword="陆虎")
    bot._buy_point = None
    bot._presale_detected = False
    bot._adb_size = Mock(return_value=(1220, 2712))
    bot._foreground_activity = Mock(return_value="cn.damai/.homepage.MainActivity")
    bot._ui_dump = Mock(
        return_value=ET.fromstring(
            """
            <hierarchy>
              <node resource-id="cn.damai:id/info_v2_title_tv1"
                    text="北京·2026陆虎巡演" />
              <node resource-id="cn.damai:id/project_item_bottom_want_to_see_fl"
                    text="想看" />
            </hierarchy>
            """
        )
    )
    bot._locate_buy_point_by_ocr = Mock(return_value=(765, 2556))

    bot._prepare_buy_point()

    assert bot._buy_point == (765, 2556)
    assert bot._presale_detected is False


def test_run_now_skips_sale_wait_and_runs_immediately():
    bot = DamaiBot.__new__(DamaiBot)
    bot.config = SimpleNamespace(
        run_now=True,
        dry_run=True,
        fast_mode=True,
        sell_start_time="2026-11-16T13:52:21+08:00",
    )
    bot._purchase_attempted = False
    bot._activity_watch = None
    bot._buy_point = (765, 2556)
    bot._presale_detected = False
    bot._foreground_activity = Mock(
        return_value="cn.damai/.trade.newtradeorder.ui.projectdetail.ProjectDetailActivity"
    )
    bot._prepare_buy_point = Mock()
    bot._start_activity_watch = Mock()
    bot._align_clock_with_phone = Mock(
        side_effect=AssertionError("立即演练不应对时")
    )
    bot._wait_for_sale_with_flip_watch = Mock(
        side_effect=AssertionError("立即演练不应等待")
    )
    bot._wait_for_sale = Mock(side_effect=AssertionError("立即演练不应等待"))
    bot._run_fast = Mock(return_value=True)
    bot._print_timing_summary = Mock()
    bot._stop_activity_watch = Mock()

    assert bot.run_ticket_grabbing() is True

    bot._run_fast.assert_called_once_with()
    bot._wait_for_sale_with_flip_watch.assert_not_called()
    bot._wait_for_sale.assert_not_called()


def test_timing_summary_emits_stage_table_and_json(capsys):
    bot = DamaiBot.__new__(DamaiBot)
    bot._timings = [
        ("点击底栏完成", 0.02),
        ("票档页可点", 0.45),
        ("点击确定", 0.47),
        ("确认页可点", 1.85),
        ("点击立即提交", 1.88),
    ]
    bot._flow = "normal"
    bot._submit_attempted = True

    bot._print_timing_summary()

    out = capsys.readouterr().out
    assert "耗时评估（普通模式）" in out
    line = next(
        line for line in out.splitlines() if line.startswith("[TIMING] ")
    )
    payload = json.loads(line[len("[TIMING] "):])
    assert payload["flow"] == "normal"
    assert payload["submitted"] is True
    assert payload["total_ms"] == 1880
    assert payload["stages"][1] == {"label": "票档页可点", "ms": 430}
