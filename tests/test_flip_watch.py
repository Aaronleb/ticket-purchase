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
import flip_watch
from damai_appium.damai_app_v2 import DamaiBot
from damai_appium.sale_clock import SHANGHAI

from flip_watch import FLIP_KEYWORDS, FlipWatchError


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


def test_oneshot_texts_parses_tool_output(mocker):
    from flip_watch import _DEFAULT_OCR_TOOL, _oneshot_texts

    done = mocker.patch("flip_watch.subprocess.run")
    done.return_value.stdout = "10\t20\t0.98\t立即预订\n30\t40\t0.90\t已预约\n坏行\n"
    assert _oneshot_texts("/tmp/x.png", _DEFAULT_OCR_TOOL) == ["立即预订", "已预约"]

    fail = mocker.patch("flip_watch.subprocess.run")
    fail.side_effect = OSError("工具不存在")
    assert _oneshot_texts("/tmp/x.png", _DEFAULT_OCR_TOOL) == []


def test_daemon_ocr_falls_back_to_oneshot(mocker):
    """daemon 起不来时退回一次性调用，仍返回识别文本。"""
    from flip_watch import _DEFAULT_OCR_TOOL, _DaemonOcr

    mocker.patch.object(flip_watch, "_spawn_daemon", return_value=None)
    done = mocker.patch("flip_watch.subprocess.run")
    done.return_value.stdout = "10\t20\t0.98\t立即预订\n"
    try:
        assert _DaemonOcr.texts("/tmp/x.png", _DEFAULT_OCR_TOOL) == ["立即预订"]
    finally:
        _DaemonOcr._proc = None


def test_strip_stable_waiter_detects_stable(mocker):
    """伪造 u2：底栏画面变化后稳定，wait 应返回 True。"""
    from PIL import Image

    fake_u2 = mocker.MagicMock()
    shots = [
        Image.new("RGB", (1220, 2712), (10, 10, 10)),
        Image.new("RGB", (1220, 2712), (10, 10, 10)),
        Image.new("RGB", (1220, 2712), (80, 80, 80)),  # 渲染突变
        Image.new("RGB", (1220, 2712), (80, 80, 80)),
        Image.new("RGB", (1220, 2712), (80, 80, 80)),
    ]
    fake_u2.connect.return_value.screenshot.side_effect = shots
    mocker.patch.dict("sys.modules", {"uiautomator2": fake_u2})

    name, wait = flip_watch.make_strip_stable_waiter("fake-device")
    assert "稳定检测" in name
    assert wait(5.0) is True
    # 变化后需要连续 2 轮稳定：第 3 张是新画面，第 4、5 张稳定才放行
    assert fake_u2.connect.return_value.screenshot.call_count == 5


def test_strip_stable_waiter_timeout_returns_false(mocker):
    """画面一直变化时超时返回 False。"""
    from PIL import Image

    counter = {"n": 0}

    def jitter(_size):
        counter["n"] += 17
        return Image.new("RGB", (1220, 2712), (counter["n"] % 255,) * 3)

    fake_u2 = mocker.MagicMock()
    fake_u2.connect.return_value.screenshot.side_effect = lambda: jitter(None)
    mocker.patch.dict("sys.modules", {"uiautomator2": fake_u2})

    _, wait = flip_watch.make_strip_stable_waiter("fake-device")
    assert wait(0.8) is False


def test_flip_keywords_cover_buyable_texts():
    assert set(FLIP_KEYWORDS) >= {"立即预订", "立即购票", "立即购买"}


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
