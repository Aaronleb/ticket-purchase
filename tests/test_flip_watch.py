# -*- coding: UTF-8 -*-
"""u2 事件监听接入后的翻转感知测试。

不依赖真实设备：u2 watcher 用 mock 代替，时间用脚本化的假时钟，
覆盖 提前命中 / 到点未命中 / 连续异常退回 / agent 不可用退回 OCR /
双感知手段都不可用退回纯定时 五种场景。
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import damai_appium.damai_app_v2 as app_v2
from damai_appium.damai_app_v2 import DamaiBot
from damai_appium.sale_clock import SHANGHAI

from flip_watch import FLIP_KEYWORDS, FlipWatchError, _hit_bottom_bar


TARGET = datetime(2026, 11, 16, 17, 16, 18, tzinfo=SHANGHAI)


class FakeDateTime:
    """now() 按脚本返回时间点的假 datetime。"""

    points = []

    @classmethod
    def now(cls, tz=None):
        if len(cls.points) > 1:
            return cls.points.pop(0)
        return cls.points[0]


def make_bot(now_points, ocr=None):
    bot = DamaiBot.__new__(DamaiBot)
    bot.config = SimpleNamespace(
        sell_start_time="2026-11-16T17:16:18+08:00",
        device_name="test-device",
    )
    bot._ocr = ocr
    bot._adb_size = Mock(return_value=(1220, 2712))
    bot._wait_for_sale = Mock(return_value=SimpleNamespace(released_at=TARGET, delay_ms=0.0))
    FakeDateTime.points = list(now_points)
    return bot


@pytest.fixture
def frozen(mocker):
    """固定 parse_sale_time / datetime / time.sleep / FLIP_WATCH_FROM。"""
    mocker.patch.object(app_v2, "parse_sale_time", return_value=TARGET)
    mocker.patch.object(app_v2, "datetime", FakeDateTime)
    fake_time = SimpleNamespace(sleep=Mock(), monotonic=Mock(return_value=1000.0))
    mocker.patch.object(app_v2, "time", fake_time)
    mocker.patch.object(DamaiBot, "FLIP_WATCH_FROM", 1.0)
    return fake_time


def test_hit_bottom_bar():
    assert _hit_bottom_bar({"top": 2600}, 2712) is True
    assert _hit_bottom_bar({"top": 1000}, 2712) is False
    assert _hit_bottom_bar({}, 0) is True  # 拿不到屏幕高度不过滤
    assert set(FLIP_KEYWORDS) >= {"立即预订", "立即购票"}


def test_u2_watch_flips_early_releases(frozen):
    """u2 监听命中底栏文案：立即放行，不走精准定时。"""
    ocr = Mock(return_value=[])
    bot = make_bot([TARGET - timedelta(seconds=2)], ocr=ocr)
    watch = Mock(return_value=True)
    mocker_watch = Mock(return_value=("u2-agent 事件监听", watch))
    import flip_watch

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(flip_watch, "make_flip_watcher", mocker_watch)
        release = bot._wait_for_sale_with_flip_watch()

    assert release.released_at == FakeDateTime.points[-1]
    assert release.target_time == TARGET
    watch.assert_called_once()
    assert 0 < watch.call_args.args[0] <= 2.0
    bot._wait_for_sale.assert_not_called()
    frozen.sleep.assert_called_once_with(1.0)  # 睡到开抢前 FLIP_WATCH_FROM 秒


def test_u2_watch_timeout_falls_back_to_timer(frozen):
    """到点仍未感知到翻转：交回精准定时，由底栏重试兜底。"""
    ocr = Mock(return_value=[])
    bot = make_bot(
        [TARGET - timedelta(seconds=0.5), TARGET - timedelta(seconds=0.2)],
        ocr=ocr,
    )
    watch = Mock(return_value=False)
    import flip_watch

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(flip_watch, "make_flip_watcher", Mock(return_value=("u2", watch)))
        release = bot._wait_for_sale_with_flip_watch()

    bot._wait_for_sale.assert_called_once_with()
    assert release is bot._wait_for_sale.return_value


def test_u2_watch_repeated_errors_fall_back_to_timer(frozen):
    """u2 watch 连续异常达到上限：放弃感知，改纯定时。"""
    ocr = Mock(return_value=[])
    bot = make_bot([TARGET - timedelta(seconds=2)], ocr=ocr)
    watch = Mock(side_effect=[RuntimeError("agent 掉线")] * 3)
    import flip_watch

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(flip_watch, "make_flip_watcher", Mock(return_value=("u2", watch)))
        release = bot._wait_for_sale_with_flip_watch()

    assert watch.call_count == 3
    bot._wait_for_sale.assert_called_once_with()
    assert release is bot._wait_for_sale.return_value


def test_agent_unavailable_falls_back_to_ocr_poll(frozen):
    """u2 agent 不可用：退回 OCR 轮询，OCR 命中底栏文案同样提前放行。"""
    rows_hit = [(100, 2600, 0.95, "立即预订")]
    ocr = Mock(return_value=[])  # 预热一轮 + 轮询命中
    ocr.side_effect = [[], rows_hit]
    bot = make_bot([TARGET - timedelta(seconds=2)], ocr=ocr)
    import flip_watch

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            flip_watch,
            "make_flip_watcher",
            Mock(side_effect=FlipWatchError("atx-agent 未安装")),
        )
        release = bot._wait_for_sale_with_flip_watch()

    assert release.target_time == TARGET
    bot._wait_for_sale.assert_not_called()
    assert ocr.call_count == 2  # 预热 1 次 + 轮询 1 次命中


def test_no_sensor_at_all_falls_back_to_timer(frozen):
    """u2 不可用且没有 OCR：直接退回纯定时。"""
    bot = make_bot([TARGET - timedelta(seconds=2)], ocr=None)
    import flip_watch

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            flip_watch,
            "make_flip_watcher",
            Mock(side_effect=FlipWatchError("atx-agent 未安装")),
        )
        release = bot._wait_for_sale_with_flip_watch()

    bot._wait_for_sale.assert_called_once_with()
    assert release is bot._wait_for_sale.return_value
