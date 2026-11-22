# -*- coding: UTF-8 -*-
"""开抢时刻必须对齐手机时钟：大麦倒计时以手机本地时间为准。"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import time_sync
from damai_appium import damai_app_v2 as app_v2


def make_bot():
    bot = app_v2.DamaiBot.__new__(app_v2.DamaiBot)
    bot.config = SimpleNamespace(
        device_name="test-device",
        sell_start_time="2026-11-17T15:00:00+08:00",
    )
    bot._adb = Mock(side_effect=AssertionError("不应再走常驻 adb 会话读时钟"))
    return bot


def test_align_clock_uses_phone_offset_when_server_matches(mocker):
    bot = make_bot()
    phone = mocker.patch.object(
        time_sync, "measure_phone_offset", return_value=-0.914
    )
    mocker.patch.object(
        time_sync, "measure_server_offset", return_value=(-0.900, {})
    )

    bot._align_clock_with_phone()

    phone.assert_called_once_with("test-device")
    # 本机比手机慢 0.914 秒 → 本机应在 14:59:59.086 放行
    assert bot.config.sell_start_time == "2026-11-17T14:59:59.086000+08:00"
    assert bot._clock_source == "手机时钟"
    bot._adb.assert_not_called()


def test_align_clock_prefers_server_when_phone_offset_diverges(mocker):
    """手机比服务器快 0.8 秒时，放行必须改用服务器时钟。"""
    bot = make_bot()
    mocker.patch.object(
        time_sync, "measure_phone_offset", return_value=-0.990
    )
    mocker.patch.object(
        time_sync,
        "measure_server_offset",
        return_value=(-0.183, {"ntp:ntp.aliyun.com": {"offset_ms": -183}}),
    )

    bot._align_clock_with_phone()

    # 本机比服务器慢 0.183 秒 → 本机应在 14:59:59.817 放行
    assert bot.config.sell_start_time == "2026-11-17T14:59:59.817000+08:00"
    assert bot._clock_source == "服务器时钟"


def test_align_clock_falls_back_to_ntp_when_phone_fails(mocker):
    bot = make_bot()
    mocker.patch.object(
        time_sync, "measure_phone_offset", side_effect=RuntimeError("adb 不可用")
    )
    mocker.patch.object(
        time_sync, "measure_server_offset", return_value=(-0.1, {})
    )

    bot._align_clock_with_phone()

    assert bot.config.sell_start_time == "2026-11-17T14:59:59.900000+08:00"
    assert bot._clock_source == "服务器时钟"
