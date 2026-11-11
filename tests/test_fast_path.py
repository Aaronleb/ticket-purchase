import json
from unittest.mock import Mock

import pytest

from damai_appium.adb_tap import AdbTapError, batch_tap, build_tap_script
from damai_appium.fast_path import (
    FastPathResult,
    is_usable,
    load_snapshot,
    select_ticket_by_coords,
    tap_viewers_by_coords,
)
from damai_appium.layout_cache import (
    LayoutCacheError,
    LayoutSnapshot,
    load_layout,
    merge_step,
    save_layout,
)


def snapshot_with(points, screen=(1080, 2400)):
    return LayoutSnapshot(screen, points)


def test_layout_snapshot_round_trip(tmp_path):
    path = tmp_path / "layout_cache.json"
    original = snapshot_with({"confirm": {"x": 540, "y": 2200}, "submit": {"x": 900, "y": 2300}})
    save_layout(path, original)

    loaded = load_layout(path)

    assert loaded.screen == (1080, 2400)
    assert loaded.center("confirm") == (540, 2200)
    assert loaded.has("submit")
    assert json.loads(path.read_text(encoding="utf-8"))["fingerprint"] is None


def test_layout_rejects_out_of_screen_coordinates(tmp_path):
    path = tmp_path / "layout_cache.json"
    path.write_text(
        json.dumps({"screen": [1080, 2400], "points": {"confirm": {"x": 1080, "y": 10}}}),
        encoding="utf-8",
    )
    with pytest.raises(LayoutCacheError, match="超出屏幕范围"):
        load_layout(path)


def test_layout_rejects_missing_file(tmp_path):
    with pytest.raises(LayoutCacheError, match="尚未缓存页面布局"):
        load_layout(tmp_path / "none.json")


@pytest.mark.parametrize(
    "payload",
    [
        {"points": {}},
        {"screen": [0, 2400], "points": {}},
        {"screen": [1080, 2400], "points": {"a": {"x": "1", "y": 2}}},
        {"screen": [1080, 2400], "points": {"a": {"x": True, "y": 2}}},
    ],
)
def test_layout_rejects_invalid_payload(tmp_path, payload):
    path = tmp_path / "layout_cache.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LayoutCacheError):
        load_layout(path)


def test_merge_step_keeps_previous_points(tmp_path):
    path = tmp_path / "layout_cache.json"
    merge_step(path, {"key": "price_page", "screen": [1080, 2400], "points": {"buy_bar": {"x": 540, "y": 1800}}})
    merged = merge_step(
        path, {"key": "confirm_page", "screen": [1080, 2400], "points": {"confirm_price": {"x": 300, "y": 900}}}
    )

    assert merged.has("buy_bar") and merged.has("confirm_price")


def test_merge_step_rejects_screen_change(tmp_path):
    path = tmp_path / "layout_cache.json"
    merge_step(path, {"key": "price_page", "screen": [1080, 2400], "points": {"buy_bar": {"x": 540, "y": 1800}}})
    with pytest.raises(LayoutCacheError, match="屏幕尺寸与已有缓存不一致"):
        merge_step(path, {"key": "confirm_page", "screen": [720, 1600], "points": {}})


def test_is_usable_detects_screen_mismatch():
    driver = Mock()
    driver.get_window_size.return_value = {"width": 720, "height": 1600}
    usable, reason = is_usable(driver, snapshot_with({}, screen=(1080, 2400)))

    assert usable is False
    assert "屏幕尺寸已变化" in reason


def test_is_usable_accepts_matching_screen():
    driver = Mock()
    driver.get_window_size.return_value = {"width": 1080, "height": 2400}
    usable, reason = is_usable(driver, snapshot_with({}, screen=(1080, 2400)))

    assert usable is True
    assert reason == ""


def test_is_usable_without_cache():
    usable, reason = is_usable(Mock(), None)
    assert usable is False and "没有可用的布局缓存" in reason


def test_select_ticket_by_coords_taps_cached_card():
    driver = Mock()
    snapshot = snapshot_with({"card_1": {"x": 300, "y": 400}})

    result = select_ticket_by_coords(driver, snapshot, [{"index": 1, "price": 999}])

    assert result.ok is True
    driver.execute_script.assert_called_once_with("mobile: clickGesture", {"x": 300, "y": 400})
    driver.find_element.assert_not_called()


def test_select_ticket_by_coords_reports_missing_card():
    driver = Mock()
    snapshot = snapshot_with({})
    result = select_ticket_by_coords(driver, snapshot, [{"index": 0, "price": 999}])

    assert result.ok is False
    assert "缺少票档" in result.reason
    driver.execute_script.assert_not_called()


def test_tap_viewers_by_coords_uses_adb_batch_for_multiple_users(mocker):
    driver = Mock()
    snapshot = snapshot_with(
        {"viewer_甲": {"x": 100, "y": 100}, "viewer_乙": {"x": 100, "y": 200}}
    )
    batch = mocker.patch("damai_appium.adb_tap.batch_tap")

    result = tap_viewers_by_coords(driver, snapshot, ["甲", "乙"], device="device-1")

    assert result.ok is True
    batch.assert_called_once_with([(100, 100), (100, 200)], device="device-1")
    driver.execute_script.assert_not_called()


def test_tap_viewers_by_coords_falls_back_when_adb_fails(mocker):
    driver = Mock()
    snapshot = snapshot_with(
        {"viewer_甲": {"x": 100, "y": 100}, "viewer_乙": {"x": 100, "y": 200}}
    )
    mocker.patch("damai_appium.adb_tap.batch_tap", side_effect=AdbTapError("no adb"))

    result = tap_viewers_by_coords(driver, snapshot, ["甲", "乙"])

    assert result.ok is True
    assert driver.execute_script.call_count == 2


def test_tap_viewers_by_coords_reports_missing_viewer():
    result = tap_viewers_by_coords(Mock(), snapshot_with({}), ["甲"])
    assert result.ok is False and "观演人坐标" in result.reason


def test_single_viewer_taps_without_adb(mocker):
    driver = Mock()
    snapshot = snapshot_with({"viewer_甲": {"x": 100, "y": 100}})
    batch = mocker.patch("damai_appium.adb_tap.batch_tap")

    result = tap_viewers_by_coords(driver, snapshot, ["甲"])

    assert result.ok is True
    batch.assert_not_called()
    driver.execute_script.assert_called_once_with("mobile: clickGesture", {"x": 100, "y": 100})


def test_load_snapshot_returns_none_without_file(tmp_path):
    assert load_snapshot(tmp_path / "missing.json") is None


def test_build_tap_script_inserts_delays():
    assert build_tap_script([(10, 20), (30, 40)], delay=0.15) == (
        "input tap 10 20; sleep 0.15; input tap 30 40"
    )


def test_build_tap_script_is_empty_for_no_points():
    assert build_tap_script([]) == ""


def test_batch_tap_reports_missing_adb(mocker):
    mocker.patch("damai_appium.adb_tap.find_adb", return_value=None)
    with pytest.raises(AdbTapError, match="未找到 adb"):
        batch_tap([(1, 2)])


def test_batch_tap_issues_single_command(mocker):
    run = mocker.patch("damai_appium.adb_tap.subprocess.run")
    run.return_value.returncode = 0
    run.return_value.stdout = ""
    run.return_value.stderr = ""

    assert batch_tap([(10, 20), (30, 40)], device="dev", adb_path="/usr/bin/adb") == 2

    command = run.call_args.args[0]
    assert command[:5] == ["/usr/bin/adb", "-s", "dev", "shell", "input tap 10 20; sleep 0.15; input tap 30 40"]


def test_batch_tap_raises_on_nonzero_exit(mocker):
    run = mocker.patch("damai_appium.adb_tap.subprocess.run")
    run.return_value.returncode = 1
    run.return_value.stdout = "INJECT_EVENTS permission denied"
    run.return_value.stderr = ""

    with pytest.raises(AdbTapError, match="INJECT_EVENTS"):
        batch_tap([(10, 20)], adb_path="/usr/bin/adb")


def test_batch_tap_with_no_points_returns_zero():
    assert batch_tap([]) == 0
