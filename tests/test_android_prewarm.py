# -*- coding: UTF-8 -*-
"""票档页预热只导航、不点确定。"""
from types import SimpleNamespace

import android_prewarm


def test_prewarm_opens_and_returns(mocker):
    state = {"activity": "cn.damai/.ProjectDetailActivity", "backs": 0}
    taps = []

    def activity(_device):
        return state["activity"]

    def tap(_device, point):
        taps.append(point)
        state["activity"] = "cn.damai/.NcovSkuActivity"

    def adb(_device, *args, timeout=15):
        if args[:3] == ("shell", "input", "keyevent"):
            state["backs"] += 1
            state["activity"] = "cn.damai/.ProjectDetailActivity"
        elif args[:3] == ("shell", "wm", "size"):
            return SimpleNamespace(stdout="Physical size: 1220x2712", returncode=0)
        return SimpleNamespace(stdout="", returncode=0)

    mocker.patch.object(android_prewarm, "_activity", side_effect=activity)
    mocker.patch.object(android_prewarm, "_tap", side_effect=tap)
    mocker.patch.object(android_prewarm, "_adb", side_effect=adb)
    mocker.patch.object(
        android_prewarm,
        "_ocr_rows",
        return_value=[(760, 2556, "立即预订")],
    )
    mocker.patch.object(android_prewarm.time, "sleep")

    result = android_prewarm.prewarm_ticket_page(
        "fake", buy_point="765,2556", runs=2
    )

    assert result["ok"] is True
    assert [run["open_ms"] for run in result["runs"]] == [0, 0]
    assert taps == [(765, 2556), (765, 2556)]
    assert state["backs"] == 3
    assert result["returned_to_detail"] is True


def test_prewarm_refuses_reserved_target_page(mocker):
    mocker.patch.object(
        android_prewarm,
        "_activity",
        return_value="cn.damai/.ProjectDetailActivity",
    )
    mocker.patch.object(
        android_prewarm,
        "_ocr_rows",
        return_value=[(400, 2557, "已预约"), (600, 2600, "即将开抢")],
    )
    mocker.patch.object(android_prewarm, "_screen_height", return_value=2712)
    tap = mocker.patch.object(android_prewarm, "_tap")

    result = android_prewarm.prewarm_ticket_page("fake", buy_point="765,2556")

    assert result["ok"] is False
    assert "已预约" in result["message"]
    tap.assert_not_called()


def test_prewarm_requires_detail_page(mocker):
    mocker.patch.object(
        android_prewarm, "_activity", return_value="cn.damai/.homepage.MainActivity"
    )

    result = android_prewarm.prewarm_ticket_page("fake")

    assert result["ok"] is False
    assert "详情页" in result["message"]


def test_prewarm_stops_when_native_sku_does_not_open(mocker):
    mocker.patch.object(
        android_prewarm,
        "_activity",
        return_value="cn.damai/.ProjectDetailActivity",
    )
    mocker.patch.object(
        android_prewarm,
        "_ocr_rows",
        return_value=[(760, 2556, "立即预订")],
    )
    mocker.patch.object(android_prewarm, "_screen_height", return_value=2712)
    mocker.patch.object(android_prewarm, "_tap")
    mocker.patch.object(
        android_prewarm,
        "_wait_activity",
        return_value="cn.damai/.h5container.DMH5Activity",
    )
    mocker.patch.object(android_prewarm.time, "sleep")

    result = android_prewarm.prewarm_ticket_page("fake", buy_point="765,2556")

    assert result["ok"] is False
    assert "原生票档页" in result["message"]
