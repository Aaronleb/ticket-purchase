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


def make_bot(now_points):
    bot = DamaiBot.__new__(DamaiBot)
    bot.config = SimpleNamespace(
        sell_start_time="2026-11-16T17:16:18+08:00",
        device_name="test-device",
        flip_template_dir="/calibrated-templates",
    )
    bot._ocr = Mock()
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
    return fake_time


def write_template(path):
    import cv2
    import numpy as np

    template = np.zeros((15, 60), dtype=np.uint8)
    template[3:12, 5:55] = 255
    cv2.imwrite(str(path / "buy.png"), template)


def test_oneshot_texts_parses_tool_output(mocker):
    done = mocker.patch("flip_watch.subprocess.run")
    done.return_value.stdout = "10\t20\t0.98\t立即预订\n30\t40\t0.90\t已预约\n坏行\n"
    assert flip_watch._oneshot_texts("/tmp/x.png", flip_watch._DEFAULT_OCR_TOOL) == [
        "立即预订",
        "已预约",
    ]

    mocker.patch("flip_watch.subprocess.run", side_effect=OSError("工具不存在"))
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


def test_template_hit_releases_without_ocr(frozen, mocker):
    bot = make_bot([TARGET - timedelta(seconds=2)])
    watch = Mock(return_value=True)
    factory = mocker.patch(
        "flip_watch.make_scrcpy_watcher", return_value=("Scrcpy 模板", watch)
    )
    u2 = mocker.patch("flip_watch.make_flip_watcher")

    release = bot._wait_for_sale_with_flip_watch()

    assert release.delay_ms == pytest.approx(-2000)
    assert factory.call_args.kwargs["focus_point"] == (793, 2617)
    assert factory.call_args.kwargs["screen_size"] == (1220, 2712)
    assert factory.call_args.kwargs["template_dir"] == "/calibrated-templates"
    bot._ocr.assert_not_called()
    u2.assert_not_called()
    bot._wait_for_sale.assert_not_called()
    frozen.sleep.assert_called_once_with(1.0)


def test_template_timeout_stops_without_ocr_or_blind_timer(frozen, mocker):
    bot = make_bot([TARGET - timedelta(seconds=0.5)])
    mocker.patch(
        "flip_watch.make_scrcpy_watcher",
        return_value=("Scrcpy 模板", Mock(return_value=False)),
    )

    with pytest.raises(SaleTimeError, match="未点击"):
        bot._wait_for_sale_with_flip_watch()
    bot._ocr.assert_not_called()
    bot._wait_for_sale.assert_not_called()


def test_missing_template_stops_before_sale(frozen, mocker):
    bot = make_bot([TARGET - timedelta(seconds=2)])
    mocker.patch(
        "flip_watch.make_scrcpy_watcher",
        side_effect=FlipWatchError("未找到可购买按钮模板"),
    )

    with pytest.raises(SaleTimeError, match="未点击"):
        bot._wait_for_sale_with_flip_watch()
    bot._ocr.assert_not_called()
    bot._wait_for_sale.assert_not_called()


def test_video_failure_does_not_fall_back_to_ocr(frozen, mocker):
    bot = make_bot([TARGET - timedelta(seconds=2)])
    mocker.patch(
        "flip_watch.make_scrcpy_watcher",
        return_value=("Scrcpy 模板", Mock(side_effect=RuntimeError("视频断开"))),
    )
    u2 = mocker.patch("flip_watch.make_flip_watcher")

    with pytest.raises(SaleTimeError, match="未点击"):
        bot._wait_for_sale_with_flip_watch()
    bot._ocr.assert_not_called()
    u2.assert_not_called()


def test_after_sale_hit_reports_positive_delay(frozen, mocker):
    bot = make_bot(
        [
            TARGET - timedelta(seconds=2),
            TARGET - timedelta(seconds=2),
            TARGET + timedelta(seconds=1.2),
        ]
    )
    mocker.patch(
        "flip_watch.make_scrcpy_watcher",
        return_value=("Scrcpy 模板", Mock(return_value=True)),
    )

    release = bot._wait_for_sale_with_flip_watch()

    assert release.delay_ms == pytest.approx(1200)


def test_expired_sale_time_does_not_start_sensor(frozen, mocker):
    bot = make_bot([TARGET + timedelta(seconds=1)])
    factory = mocker.patch("flip_watch.make_scrcpy_watcher")

    with pytest.raises(SaleTimeError, match="已过"):
        bot._wait_for_sale_with_flip_watch()
    factory.assert_not_called()
    bot._wait_for_sale.assert_not_called()


def test_scrcpy_unchanged_frame_skips_template_matching(mocker, tmp_path):
    import numpy as np

    write_template(tmp_path)
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
    match = mocker.patch.object(
        flip_watch, "_scrcpy_template_hit", side_effect=[False, True]
    )
    ocr = mocker.patch.object(flip_watch._DaemonOcr, "texts")
    name, watch = flip_watch.make_scrcpy_watcher("fake", template_dir=tmp_path)

    assert watch(1.0) is True
    assert match.call_count == 2
    assert "无 OCR" in name
    ocr.assert_not_called()


def test_scrcpy_real_template_match_uses_no_ocr(mocker, tmp_path):
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
    _, watch = flip_watch.make_scrcpy_watcher("fake", template_dir=tmp_path)

    assert watch(1.0) is True
    ocr.assert_not_called()


def test_pixel_change_without_positive_template_never_releases(mocker, tmp_path):
    import numpy as np

    write_template(tmp_path)

    class FakeStream:
        def __init__(self, _device):
            self.stopped = False

        def start(self):
            pass

        def read_latest(self):
            return np.random.default_rng(3).integers(
                0, 255, (120, 120, 3), dtype=np.uint8
            )

        def stop(self):
            self.stopped = True

    mocker.patch("scrcpy_stream.ScrcpyStream", FakeStream)
    ocr = mocker.patch.object(flip_watch._DaemonOcr, "texts")
    mocker.patch.object(flip_watch.time, "monotonic", side_effect=[0.0, 0.0, 2.0])
    _, watch = flip_watch.make_scrcpy_watcher("fake", template_dir=tmp_path)

    assert watch(1.0) is False
    ocr.assert_not_called()


def test_missing_template_file_rejects_before_starting_stream(mocker, tmp_path):
    stream = mocker.patch("scrcpy_stream.ScrcpyStream")

    with pytest.raises(FlipWatchError, match="未找到可购买按钮模板"):
        flip_watch.make_scrcpy_watcher("fake", template_dir=tmp_path)
    stream.assert_not_called()
