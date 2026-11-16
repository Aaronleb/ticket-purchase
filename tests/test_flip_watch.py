# -*- coding: UTF-8 -*-
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import damai_appium.damai_app_v2 as app_v2
import flip_watch
from damai_appium.damai_app_v2 import DamaiBot
from damai_appium.sale_clock import SHANGHAI, SaleTimeError
from flip_watch import FLIP_KEYWORDS, FlipWatchError


TARGET = datetime(2026, 11, 16, 17, 16, 18, tzinfo=SHANGHAI)


class FakeDateTime:
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
    bot._buy_point = (793, 2617)
    bot._adb_size = Mock(return_value=(1220, 2712))
    bot._wait_for_sale = Mock()
    FakeDateTime.points = list(now_points)
    return bot


@pytest.fixture
def frozen(mocker):
    mocker.patch.object(app_v2, "parse_sale_time", return_value=TARGET)
    mocker.patch.object(app_v2, "datetime", FakeDateTime)
    fake_time = SimpleNamespace(sleep=Mock(), monotonic=Mock(return_value=1000.0))
    mocker.patch.object(app_v2, "time", fake_time)
    mocker.patch.object(DamaiBot, "FLIP_WATCH_FROM", 1.0)
    mocker.patch(
        "flip_watch.make_scrcpy_watcher",
        side_effect=FlipWatchError("测试默认关闭 Scrcpy"),
    )
    return fake_time


def test_oneshot_texts_parses_tool_output(mocker):
    done = mocker.patch("flip_watch.subprocess.run")
    done.return_value.stdout = "10\t20\t0.98\t立即预订\n30\t40\t0.90\t已预约\n坏行\n"
    assert flip_watch._oneshot_texts("/tmp/x.png", flip_watch._DEFAULT_OCR_TOOL) == [
        "立即预订",
        "已预约",
    ]

    fail = mocker.patch("flip_watch.subprocess.run", side_effect=OSError("工具不存在"))
    assert flip_watch._oneshot_texts("/tmp/x.png", flip_watch._DEFAULT_OCR_TOOL) == []


def test_daemon_ocr_falls_back_to_oneshot(mocker):
    mocker.patch.object(flip_watch, "_spawn_daemon", return_value=None)
    done = mocker.patch("flip_watch.subprocess.run")
    done.return_value.stdout = "10\t20\t0.98\t立即预订\n"
    try:
        assert flip_watch._DaemonOcr.texts("/tmp/x.png", flip_watch._DEFAULT_OCR_TOOL) == [
            "立即预订"
        ]
    finally:
        flip_watch._DaemonOcr._proc = None


def test_strip_stable_waiter_detects_stable(mocker):
    from PIL import Image

    fake_u2 = mocker.MagicMock()
    shots = [
        Image.new("RGB", (1220, 2712), (10, 10, 10)),
        Image.new("RGB", (1220, 2712), (10, 10, 10)),
        Image.new("RGB", (1220, 2712), (80, 80, 80)),
        Image.new("RGB", (1220, 2712), (80, 80, 80)),
        Image.new("RGB", (1220, 2712), (80, 80, 80)),
    ]
    fake_u2.connect.return_value.screenshot.side_effect = shots
    mocker.patch.dict("sys.modules", {"uiautomator2": fake_u2})

    name, wait = flip_watch.make_strip_stable_waiter("fake-device")
    assert "稳定检测" in name
    assert wait(5.0) is True
    assert fake_u2.connect.return_value.screenshot.call_count == 5


def test_strip_stable_waiter_timeout_returns_false(mocker):
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


def test_scrcpy_watch_flips_early_releases(frozen, mocker):
    bot = make_bot([TARGET - timedelta(seconds=2)], ocr=Mock(return_value=[]))
    watch = Mock(return_value=True)
    factory = mocker.patch(
        "flip_watch.make_scrcpy_watcher", return_value=("Scrcpy", watch)
    )
    u2 = mocker.patch("flip_watch.make_flip_watcher")

    release = bot._wait_for_sale_with_flip_watch()

    assert release.delay_ms == pytest.approx(-2000)
    assert factory.call_args.kwargs["focus_point"] == (793, 2617)
    assert factory.call_args.kwargs["screen_size"] == (1220, 2712)
    u2.assert_not_called()
    bot._wait_for_sale.assert_not_called()


def test_u2_watch_flips_early_releases(frozen, mocker):
    bot = make_bot([TARGET - timedelta(seconds=2)], ocr=Mock(return_value=[]))
    watch = Mock(return_value=True)
    mocker.patch("flip_watch.make_flip_watcher", return_value=("u2", watch))

    release = bot._wait_for_sale_with_flip_watch()

    assert release.delay_ms == pytest.approx(-2000)
    watch.assert_called_once()
    bot._wait_for_sale.assert_not_called()
    frozen.sleep.assert_called_once_with(1.0)


def test_watch_timeout_stops_instead_of_blind_timer(frozen, mocker):
    bot = make_bot([TARGET - timedelta(seconds=0.5)], ocr=Mock(return_value=[]))
    mocker.patch("flip_watch.make_flip_watcher", return_value=("u2", Mock(return_value=False)))

    with pytest.raises(SaleTimeError, match="未点击"):
        bot._wait_for_sale_with_flip_watch()
    bot._wait_for_sale.assert_not_called()


def test_scrcpy_failure_falls_back_to_u2(frozen, mocker):
    bot = make_bot([TARGET - timedelta(seconds=2)], ocr=Mock(return_value=[]))
    mocker.patch(
        "flip_watch.make_scrcpy_watcher",
        return_value=("Scrcpy", Mock(side_effect=RuntimeError("视频断开"))),
    )
    u2_watch = Mock(return_value=True)
    mocker.patch("flip_watch.make_flip_watcher", return_value=("u2", u2_watch))

    release = bot._wait_for_sale_with_flip_watch()

    assert release.delay_ms == pytest.approx(-2000)
    u2_watch.assert_called_once()


def test_u2_repeated_errors_fall_back_to_ocr(frozen, mocker):
    rows_hit = [(100, 2600, 0.95, "立即预订")]
    ocr = Mock(side_effect=[[], rows_hit])
    bot = make_bot([TARGET - timedelta(seconds=2)], ocr=ocr)
    watch = Mock(side_effect=[RuntimeError("agent 掉线")] * 3)
    mocker.patch("flip_watch.make_flip_watcher", return_value=("u2", watch))

    release = bot._wait_for_sale_with_flip_watch()

    assert release.target_time == TARGET
    assert watch.call_count == 3
    assert ocr.call_count == 2
    bot._wait_for_sale.assert_not_called()


def test_agent_unavailable_falls_back_to_ocr_poll(frozen, mocker):
    rows_hit = [(100, 2600, 0.95, "立即预订")]
    ocr = Mock(side_effect=[[], rows_hit])
    bot = make_bot([TARGET - timedelta(seconds=2)], ocr=ocr)
    mocker.patch(
        "flip_watch.make_flip_watcher",
        side_effect=FlipWatchError("atx-agent 未安装"),
    )

    release = bot._wait_for_sale_with_flip_watch()

    assert release.target_time == TARGET
    assert ocr.call_count == 2
    bot._wait_for_sale.assert_not_called()


def test_no_sensor_at_all_stops_without_blind_timer(frozen, mocker):
    bot = make_bot([TARGET - timedelta(seconds=2)], ocr=None)
    mocker.patch(
        "flip_watch.make_flip_watcher",
        side_effect=FlipWatchError("atx-agent 未安装"),
    )

    with pytest.raises(SaleTimeError, match="未点击"):
        bot._wait_for_sale_with_flip_watch()
    bot._wait_for_sale.assert_not_called()


def test_after_sale_hit_reports_positive_delay(frozen, mocker):
    bot = make_bot(
        [
            TARGET - timedelta(seconds=2),
            TARGET - timedelta(seconds=2),
            TARGET + timedelta(seconds=1.2),
        ],
        ocr=Mock(return_value=[]),
    )
    mocker.patch("flip_watch.make_flip_watcher", return_value=("u2", Mock(return_value=True)))

    release = bot._wait_for_sale_with_flip_watch()

    assert release.delay_ms == pytest.approx(1200)


def test_expired_sale_time_does_not_start_sensor(frozen, mocker):
    bot = make_bot([TARGET + timedelta(seconds=1)], ocr=Mock(return_value=[]))
    factory = mocker.patch("flip_watch.make_flip_watcher")

    with pytest.raises(SaleTimeError, match="已过"):
        bot._wait_for_sale_with_flip_watch()
    factory.assert_not_called()
    bot._wait_for_sale.assert_not_called()


def test_scrcpy_watch_skips_ocr_for_unchanged_frames(mocker):
    import numpy as np

    first = np.zeros((120, 120, 3), dtype=np.uint8)
    changed = first.copy()
    changed[100:, :] = 255

    class FakeStream:
        def __init__(self, _device):
            self.frames = [first, first.copy(), changed]
            self.stopped = False

        def start(self):
            pass

        def read_latest(self):
            return self.frames.pop(0) if self.frames else None

        def stop(self):
            self.stopped = True

    mocker.patch("scrcpy_stream.ScrcpyStream", FakeStream)
    ocr = mocker.patch.object(
        flip_watch._DaemonOcr,
        "texts",
        side_effect=[["已预约"], ["立即预订"]],
    )
    name, watch = flip_watch.make_scrcpy_watcher("fake")

    assert watch(1.0) is True
    assert ocr.call_count == 2
    assert "Scrcpy" in name


def test_scrcpy_template_match_skips_ocr(mocker, tmp_path):
    import cv2
    import numpy as np

    frame = np.zeros((120, 120, 3), dtype=np.uint8)
    frame[100:115, 20:80] = 255
    template = np.full((15, 60), 255, dtype=np.uint8)
    cv2.imwrite(str(tmp_path / "buy.png"), template)

    class FakeStream:
        def __init__(self, _device):
            pass

        def start(self):
            pass

        def read_latest(self):
            return frame

        def stop(self):
            pass

    mocker.patch("scrcpy_stream.ScrcpyStream", FakeStream)
    ocr = mocker.patch.object(flip_watch._DaemonOcr, "texts")
    name, watch = flip_watch.make_scrcpy_watcher("fake", template_dir=tmp_path)

    assert watch(1.0) is True
    assert "模板" in name
    ocr.assert_not_called()


def test_scrcpy_blocked_text_never_releases(mocker):
    import numpy as np

    class FakeStream:
        def __init__(self, _device):
            self.stopped = False

        def start(self):
            pass

        def read_latest(self):
            return np.zeros((120, 120, 3), dtype=np.uint8)

        def stop(self):
            self.stopped = True

    mocker.patch("scrcpy_stream.ScrcpyStream", FakeStream)
    mocker.patch.object(flip_watch._DaemonOcr, "texts", return_value=["已预约"])
    mocker.patch.object(flip_watch.time, "monotonic", side_effect=[0.0, 0.0, 2.0])
    _, watch = flip_watch.make_scrcpy_watcher("fake")

    assert watch(1.0) is False
