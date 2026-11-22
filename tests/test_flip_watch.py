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


def test_template_hit_early_waits_for_sale_time(frozen, mocker):
    """模板比开售时刻早命中时不能空点：等到开售时刻再放行。"""
    bot = make_bot(
        [
            TARGET - timedelta(seconds=2),  # 进入感知
            TARGET - timedelta(seconds=0.26),  # 剩余时间
            TARGET - timedelta(seconds=0.26),  # 模板提前命中
            TARGET - timedelta(seconds=0.26),  # 补睡前再看一次
            TARGET + timedelta(seconds=0.001),  # 补睡后已到点
            TARGET + timedelta(seconds=0.001),  # released_at
        ]
    )
    watch = Mock(return_value=True)
    factory = mocker.patch(
        "flip_watch.make_scrcpy_watcher", return_value=("Scrcpy 模板", watch)
    )
    u2 = mocker.patch("flip_watch.make_flip_watcher")

    release = bot._wait_for_sale_with_flip_watch()

    assert release.released_at == TARGET + timedelta(seconds=0.001)
    assert release.delay_ms == pytest.approx(1)
    assert factory.call_args.kwargs["focus_point"] == (793, 2617)
    assert factory.call_args.kwargs["screen_size"] == (1220, 2712)
    assert factory.call_args.kwargs["template_dir"] == "/calibrated-templates"
    assert frozen.sleep.call_args_list == [mocker.call(1.0), mocker.call(0.26)]
    bot._ocr.assert_not_called()
    u2.assert_not_called()
    bot._wait_for_sale.assert_not_called()


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


def test_button_is_enabled_rejects_grey_and_accepts_orange():
    import cv2
    import numpy as np

    grey = np.full((120, 120, 3), 70, dtype=np.uint8)
    orange = np.full((120, 120, 3), (0, 90, 255), dtype=np.uint8)

    assert flip_watch._button_is_enabled(grey, (60, 100), (120, 120), cv2) is False
    assert flip_watch._button_is_enabled(orange, (60, 100), (120, 120), cv2) is True
    # 没有按钮坐标时不额外拦截，退回纯模板判断
    assert flip_watch._button_is_enabled(grey, None, None, cv2) is True


def test_grey_bar_does_not_release_even_if_template_matches(mocker, tmp_path):
    """“已预约”灰暗底栏即使字形命中模板也不放行。"""
    import cv2
    import numpy as np

    frame = np.full((120, 120, 3), 70, dtype=np.uint8)
    frame[100:115, 20:80] = 255
    cv2.imwrite(str(tmp_path / "buy.png"), np.full((15, 60), 255, dtype=np.uint8))

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
    mocker.patch.object(flip_watch.time, "monotonic", side_effect=[0.0, 0.0, 2.0])
    _, watch = flip_watch.make_scrcpy_watcher(
        "fake",
        focus_point=(60, 107),
        screen_size=(120, 120),
        template_dir=tmp_path,
    )

    assert watch(1.0) is False


def test_enabled_orange_bar_releases_on_template(mocker, tmp_path):
    import cv2
    import numpy as np

    frame = np.full((120, 120, 3), (0, 90, 255), dtype=np.uint8)
    frame[100:115, 20:80] = 255
    cv2.imwrite(str(tmp_path / "buy.png"), np.full((15, 60), 255, dtype=np.uint8))

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
    _, watch = flip_watch.make_scrcpy_watcher(
        "fake",
        focus_point=(60, 107),
        screen_size=(120, 120),
        template_dir=tmp_path,
    )

    assert watch(1.0) is True


class _FrameStream:
    def __init__(self, frames):
        self.frames = list(frames)

    def start(self):
        pass

    def stop(self):
        pass

    def read_latest(self):
        if len(self.frames) > 1:
            return self.frames.pop(0)
        return self.frames[0]


def _pre_and_post_sale_frames():
    import numpy as np

    pre = np.full((120, 120, 3), (0, 90, 255), dtype=np.uint8)
    pre[99:104, 20:100] = 255
    post = np.full((120, 120, 3), (0, 90, 255), dtype=np.uint8)
    post[99:109, 20:100] = 255
    return pre, post


def test_auto_capture_releases_after_sale_when_template_misses(mocker, tmp_path):
    """开售后模板没补上时，底栏稳定翻转就补采并立即放行。"""
    write_template(tmp_path)
    pre, post = _pre_and_post_sale_frames()
    mocker.patch(
        "scrcpy_stream.ScrcpyStream",
        lambda _device: _FrameStream([pre, pre, post, post]),
    )
    mocker.patch.object(flip_watch, "_scrcpy_template_hit", return_value=False)
    save = mocker.patch.object(flip_watch, "_save_auto_template_async")
    mocker.patch.object(
        flip_watch.time,
        "monotonic",
        side_effect=[0.0, 0.4, 0.8, 1.05, 1.10],
    )
    _, watch = flip_watch.make_scrcpy_watcher(
        "fake",
        focus_point=(60, 107),
        screen_size=(120, 120),
        template_dir=tmp_path,
    )

    assert watch(1.0, post_sale_timeout=0.5) is True
    save.assert_called_once()


def test_auto_capture_ignores_change_before_sale_time(mocker, tmp_path):
    """开售前的页面变化不能被当成翻转，到点也不能补采放行。"""
    write_template(tmp_path)
    pre, post = _pre_and_post_sale_frames()
    mocker.patch(
        "scrcpy_stream.ScrcpyStream",
        lambda _device: _FrameStream([pre, post, post, post, post]),
    )
    mocker.patch.object(flip_watch, "_scrcpy_template_hit", return_value=False)
    save = mocker.patch.object(flip_watch, "_save_auto_template_async")
    mocker.patch.object(
        flip_watch.time,
        "monotonic",
        side_effect=[0.0, 0.5, 0.6, 1.2, 1.3, 1.6],
    )
    _, watch = flip_watch.make_scrcpy_watcher(
        "fake",
        focus_point=(60, 107),
        screen_size=(120, 120),
        template_dir=tmp_path,
    )

    assert watch(1.0, post_sale_timeout=0.5) is False
    save.assert_not_called()


def test_buy_button_crop_matches_calibration_ratio():
    import cv2
    import numpy as np

    frame = np.zeros((1000, 500, 3), dtype=np.uint8)
    crop = flip_watch._buy_button_crop(frame, (250, 900), (500, 1000), cv2)

    assert crop is not None
    assert crop.shape == (70, 180)


def test_negative_templates_are_loaded_separately(tmp_path):
    import cv2
    import numpy as np

    write_template(tmp_path)
    negative = np.zeros((15, 60), dtype=np.uint8)
    negative[3:12, 5:55] = 255
    cv2.imwrite(str(tmp_path / "negative-yuyue.png"), negative)

    positives = flip_watch._load_scrcpy_templates(cv2, tmp_path)
    negatives = flip_watch._load_scrcpy_negatives(cv2, tmp_path)

    assert [name for name, _ in positives] == ["buy"]
    assert [name for name, _ in negatives] == ["negative-yuyue"]


def test_missing_template_file_rejects_before_starting_stream(mocker, tmp_path):
    stream = mocker.patch("scrcpy_stream.ScrcpyStream")

    with pytest.raises(FlipWatchError, match="未找到可购买按钮模板"):
        flip_watch.make_scrcpy_watcher("fake", template_dir=tmp_path)
    stream.assert_not_called()


def test_empty_activity_waits_without_back_or_extra_tap(mocker):
    """前台 Activity 暂空时只等待过渡，不返回、不重复点击。"""
    bot = DamaiBot.__new__(DamaiBot)
    bot.config = SimpleNamespace(device_name="test-device")
    activities = iter(["", "NcovSkuActivity"])
    taps = []
    back = Mock()
    bot._foreground_activity = lambda: next(activities)
    bot._adb_tap = lambda *point: taps.append(point)
    bot._adb = back
    bot._mark = Mock()
    mocker.patch.object(app_v2.time, "sleep", Mock())

    assert bot._tap_buy_bar_until_next_page((100, 200)) == "NcovSkuActivity"
    assert taps == [(100, 200)]
    back.assert_not_called()


def test_ensure_detail_page_does_not_press_back_when_activity_empty(mocker):
    """开售瞬间 Activity 暂空时等过渡，按返回键会把 App 退掉。"""
    bot = DamaiBot.__new__(DamaiBot)
    bot.config = SimpleNamespace(device_name="test-device")
    activities = iter(["", "cn.damai/.ProjectDetailActivity"])
    bot._foreground_activity = lambda: next(activities)
    bot._adb = Mock()
    mocker.patch.object(app_v2.time, "sleep", Mock())

    bot._ensure_detail_page()

    bot._adb.assert_not_called()


def test_activity_drawn_uses_first_frame_flag(mocker):
    bot = DamaiBot.__new__(DamaiBot)
    bot.config = SimpleNamespace(device_name="test-device")
    raw = (
        "  topResumedActivity=ActivityRecord{1 u0 cn.damai/.sku.NcovSkuActivity t1}\n"
        "    * Hist  #1: ActivityRecord{1 u0 cn.damai/.sku.NcovSkuActivity t1}\n"
        "      state=RESUMED\n"
        "      mVisible=true reportedDrawn=true reportedVisible=true\n"
        "      nowVisible=true\n"
        "    * Hist  #0: ActivityRecord{2 u0 cn.damai/.homepage.MainActivity t1}\n"
        "      reportedDrawn=false nowVisible=false\n"
    )
    bot._adb = Mock(return_value=SimpleNamespace(stdout=raw))

    assert bot._activity_drawn("NcovSkuActivity") is True
    assert bot._activity_drawn("DmOrderActivity") is False

    drawn_false = raw.replace("reportedDrawn=true", "reportedDrawn=false").replace(
        "nowVisible=true", "nowVisible=false"
    )
    bot._adb = Mock(return_value=SimpleNamespace(stdout=drawn_false))
    assert bot._activity_drawn("NcovSkuActivity") is False


def test_fast_mode_retaps_confirm_when_first_tap_lost(mocker):
    """预约模式：首次确定没反应时补点一次，不做 OCR 选票档。"""
    bot = DamaiBot.__new__(DamaiBot)
    bot.config = SimpleNamespace(
        confirm_point="950,2562",
        submit_point="941,2563",
        if_commit_order=False,
    )
    bot._tap_buy_bar_until_next_page = Mock(return_value="NcovSkuActivity")
    bot._buy_bar_point = Mock(return_value=(764, 2557))
    bot._wait_drawn = Mock(return_value=True)
    bot._resolve_point = Mock(return_value=(950, 2562))
    taps = []
    bot._adb_tap = lambda *point: taps.append(point)
    bot._wait_any_activity = Mock(side_effect=["", "DmOrderActivity"])
    bot._foreground_activity = Mock(return_value="cn.damai/.sku.NcovSkuActivity")
    bot._mark = Mock()

    assert bot._run_fast() is True
    assert taps == [(950, 2562), (950, 2562)]
    assert bot._wait_any_activity.call_count == 2


def test_sku_confirm_template_is_loaded_separately(tmp_path):
    import cv2
    import numpy as np

    cv2.imwrite(str(tmp_path / "buy.png"), np.zeros((15, 60), dtype=np.uint8))
    cv2.imwrite(str(tmp_path / "sku-confirm.png"), np.zeros((30, 90), dtype=np.uint8))

    buy_names = [name for name, _ in flip_watch._load_scrcpy_templates(cv2, tmp_path)]
    sku_names = [name for name, _ in flip_watch._load_sku_confirm_templates(cv2, tmp_path)]

    assert buy_names == ["buy"]
    assert sku_names == ["sku-confirm"]


def _sku_frame(price_right=200):
    import numpy as np

    frame = np.zeros((1600, 720, 3), dtype=np.uint8)
    # 左侧价格块：宽度变化不应影响右侧按钮区域
    frame[1500:1580, 20:price_right] = (40, 40, 220)
    # 右侧确定按钮（橙色）
    frame[1505:1575, 430:702] = (30, 90, 240)
    frame[1530:1555, 470:660] = 255
    return frame


def test_sku_search_crop_ignores_price_width():
    import cv2

    narrow = flip_watch._sku_search_crop(
        _sku_frame(200), (960, 2613), (1220, 2712), cv2
    )
    wide = flip_watch._sku_search_crop(
        _sku_frame(320), (960, 2613), (1220, 2712), cv2
    )

    assert narrow is not None and wide is not None
    assert narrow.shape == wide.shape
    assert (narrow == wide).all()


def test_watch_sku_confirm_hits_fixed_button(tmp_path):
    import cv2

    frame = _sku_frame()
    cv2.imwrite(
        str(tmp_path / "sku-confirm.png"),
        cv2.cvtColor(frame[1505:1575, 430:702], cv2.COLOR_BGR2GRAY),
    )

    class FakeStream:
        def start(self):
            pass

        def stop(self):
            pass

        def read_latest(self):
            return frame

    assert flip_watch.watch_sku_confirm(
        FakeStream(), (960, 2613), (1220, 2712), 0.2, template_dir=tmp_path
    ) is True


def test_watch_sku_confirm_without_template_does_not_block(tmp_path):
    class FakeStream:
        def start(self):
            pass

        def stop(self):
            pass

        def read_latest(self):
            return _sku_frame()

    assert flip_watch.watch_sku_confirm(
        FakeStream(), (960, 2613), (1220, 2712), 0.5, template_dir=tmp_path
    ) is False


def test_keep_alive_stream_survives_watch_and_closes_on_demand(mocker, tmp_path):
    import cv2
    import numpy as np

    # 纯白模板：与 frame 底栏里的白色按钮条完全一致
    cv2.imwrite(str(tmp_path / "buy.png"), np.full((15, 60), 255, dtype=np.uint8))
    frame = np.zeros((120, 120, 3), dtype=np.uint8)
    frame[100:115, 20:80] = 255
    streams = []

    class FakeStream:
        def __init__(self, _device):
            self.stopped = False
            streams.append(self)

        def start(self):
            pass

        def read_latest(self):
            return frame

        def stop(self):
            self.stopped = True

    mocker.patch("scrcpy_stream.ScrcpyStream", FakeStream)
    _, watch = flip_watch.make_scrcpy_watcher(
        "fake", template_dir=tmp_path, keep_alive=True
    )

    assert watch(1.0) is True
    assert streams[0].stopped is False
    watch.close()
    assert streams[0].stopped is True


def test_template_hit_reports_best_score(tmp_path):
    import cv2
    import numpy as np

    template = np.full((15, 60), 255, dtype=np.uint8)
    frame = np.zeros((120, 120, 3), dtype=np.uint8)
    frame[100:115, 20:80] = 255
    crop = flip_watch._scrcpy_crop(frame, None, None)
    stats = {}

    assert flip_watch._scrcpy_template_hit(
        crop, [("buy", template)], cv2, stats=stats
    ) is True
    assert stats["score"] >= flip_watch.SCRCPY_TEMPLATE_THRESHOLD
    assert stats["template"] == "buy"
    assert stats["scale"] in (0.5, 0.59, 0.67, 0.8, 1.0, 1.25, 1.5, 2.0)


def test_live_template_is_saved_only_once(tmp_path):
    import cv2
    import numpy as np
    import time

    image = np.zeros((20, 60), dtype=np.uint8)

    flip_watch._save_live_template_async(image, tmp_path, cv2)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not (tmp_path / "buy-live.png").exists():
        time.sleep(0.01)
    assert (tmp_path / "buy-live.png").is_file()
    first = (tmp_path / "buy-live.png").read_bytes()

    flip_watch._save_live_template_async(image, tmp_path, cv2)
    time.sleep(0.1)
    assert (tmp_path / "buy-live.png").read_bytes() == first


def test_scrcpy_stream_defaults_to_30_fps():
    from scrcpy_stream import ScrcpyStream

    assert ScrcpyStream("fake").max_fps == 30


def test_submit_template_is_loaded_separately(tmp_path):
    import cv2
    import numpy as np

    cv2.imwrite(str(tmp_path / "buy.png"), np.zeros((15, 60), dtype=np.uint8))
    cv2.imwrite(str(tmp_path / "sku-confirm.png"), np.zeros((30, 90), dtype=np.uint8))
    cv2.imwrite(str(tmp_path / "sku-submit.png"), np.zeros((30, 90), dtype=np.uint8))

    buy_names = [name for name, _ in flip_watch._load_scrcpy_templates(cv2, tmp_path)]
    confirm_names = [
        name for name, _ in flip_watch._load_sku_confirm_templates(cv2, tmp_path)
    ]
    submit_names = [
        name for name, _ in flip_watch._load_submit_templates(cv2, tmp_path)
    ]

    assert buy_names == ["buy"]
    assert confirm_names == ["sku-confirm"]
    assert submit_names == ["sku-submit"]


def test_watch_submit_ready_hits_fixed_button(tmp_path):
    import cv2

    frame = _sku_frame()
    cv2.imwrite(
        str(tmp_path / "sku-submit.png"),
        cv2.cvtColor(frame[1505:1575, 430:702], cv2.COLOR_BGR2GRAY),
    )

    class FakeStream:
        def start(self):
            pass

        def stop(self):
            pass

        def read_latest(self):
            return frame

    assert flip_watch.watch_submit_ready(
        FakeStream(), (960, 2613), (1220, 2712), 0.2, template_dir=tmp_path
    ) is True
