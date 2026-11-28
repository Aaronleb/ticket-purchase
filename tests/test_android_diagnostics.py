# -*- coding: UTF-8 -*-
"""网络/设备诊断：纯解析 + 报告结构，不依赖真机。"""
from unittest.mock import patch

import pytest

from android_diagnostics import (
    AndroidDiagnostics,
    parse_app_meminfo,
    parse_battery,
    parse_cpuinfo,
    parse_df,
    parse_gfxinfo,
    parse_meminfo,
    parse_ping,
    parse_properties,
    parse_refresh_rate,
    parse_thermal,
    parse_wifi,
    parse_wm_size,
)


PING_OUTPUT = """
PING detail.damai.cn (203.0.113.10) 56(84) bytes of data.
64 bytes from 203.0.113.10: icmp_seq=1 ttl=54 time=41.2 ms
64 bytes from 203.0.113.10: icmp_seq=2 ttl=54 time=45.6 ms
64 bytes from 203.0.113.10: icmp_seq=3 ttl=54 time=43.1 ms
64 bytes from 203.0.113.10: icmp_seq=4 ttl=54 time=44.8 ms

--- detail.damai.cn ping statistics ---
4 packets transmitted, 4 received, 0% packet loss, time 800ms
rtt min/avg/max/mdev = 41.200/43.675/45.600/1.800 ms
"""

WIFI_OUTPUT = """
Wifi is enabled
WifiInfo: SSID: "damai-5g", BSSID: 00:11:22:33:44:55, Supplicant state: COMPLETED, Wi-Fi standard: 5, RSSI: -48, Link speed: 866Mbps, Tx Link speed: 866Mbps, Rx Link speed: 866Mbps, Frequency: 5180MHz, ...
"""

GFXINFO_OUTPUT = """
Total frames rendered: 1200
Janky frames: 36 (3.00%)
50th percentile: 5ms
90th percentile: 11ms
95th percentile: 18ms
99th percentile: 40ms
"""


def test_parse_ping_extracts_rtt_jitter_and_loss():
    data = parse_ping(PING_OUTPUT)

    assert data["avg_ms"] == pytest.approx(43.675)
    assert data["jitter_ms"] == pytest.approx(1.8)
    assert data["loss"] == 0
    assert data["sent"] == 4
    assert data["received"] == 4


def test_parse_ping_handles_unreachable():
    data = parse_ping("ping: unknown host detail.damai.cn")

    assert data["avg_ms"] is None
    assert data["loss"] is None


def test_parse_wifi_extracts_signal_and_band():
    data = parse_wifi(WIFI_OUTPUT)

    assert data["enabled"] is True
    assert data["ssid"] == "damai-5g"
    assert data["rssi"] == -48
    assert data["link_mbps"] == 866
    assert data["frequency_mhz"] == 5180


def test_parse_gfxinfo_extracts_jank_and_percentiles():
    data = parse_gfxinfo(GFXINFO_OUTPUT)

    assert data["janky_percent"] == 3.0
    assert data["p90_ms"] == 11
    assert data["p99_ms"] == 40


def test_parse_device_metrics():
    assert parse_meminfo("MemTotal: 8000000 kB\nMemAvailable: 3000000 kB\n") == {
        "total_mb": pytest.approx(7812.5),
        "available_mb": pytest.approx(2929.6875),
    }
    assert parse_df(
        "Filesystem 1K-blocks Used Available Use% Mounted on\n"
        "/dev/block/dm-1 100000000 50000000 50000000 50% /data\n"
    )["available_gb"] == pytest.approx(47.68, abs=0.01)
    assert parse_battery("level: 80\ntemperature: 365\nstatus: 2\n") == {
        "temperature_c": 36.5,
        "level": 80,
        "status": 2,
    }
    assert parse_thermal("Thermal Status: 2\n") == 2
    assert parse_refresh_rate("mRefreshRate=120.000004\n") == pytest.approx(120.000004)
    assert parse_wm_size("Physical size: 1220x2712\n") == (1220, 2712)
    assert parse_app_meminfo("TOTAL PSS: 409600\n") == 409600
    assert parse_cpuinfo("  1.5% 1234/cn.damai: 1.0% user + 0.5% kernel\n") == 1.5
    assert parse_properties("[ro.product.model]: [Test Phone]\n") == {
        "ro.product.model": "Test Phone"
    }


def make_diag():
    diag = AndroidDiagnostics(timeout=1)
    return diag


def test_network_report_marks_good_network_as_pass():
    diag = make_diag()

    def fake_run(adb, device, args, timeout=None):
        command = " ".join(args)
        if "ping" in command:
            return PING_OUTPUT, ""
        if "wifi" in command:
            return WIFI_OUTPUT, ""
        if "private_dns_mode" in command:
            return "off\n", ""
        return "", ""

    with patch("android_diagnostics.shutil.which", return_value="/usr/bin/adb"), \
         patch.object(diag, "_safe_run", side_effect=fake_run):
        report = diag.network_report("device-1")

    assert report["ok"] is True
    assert report["level"] == "pass"
    assert any(m["id"] == "rtt-0" and m["level"] == "pass" for m in report["metrics"])
    assert any(m["id"] == "wifi-band" for m in report["metrics"])


def test_network_report_flags_bad_rtt_and_loss():
    diag = make_diag()

    def fake_run(adb, device, args, timeout=None):
        if "ping" in " ".join(args):
            return (
                "4 packets transmitted, 3 received, 25% packet loss\n"
                "rtt min/avg/max/mdev = 180.0/220.0/260.0/40.0 ms\n",
                "",
            )
        return "", ""

    with patch("android_diagnostics.shutil.which", return_value="/usr/bin/adb"), \
         patch.object(diag, "_safe_run", side_effect=fake_run):
        report = diag.network_report("device-1")

    assert report["ok"] is False
    assert any(m["id"] == "rtt-0" and m["level"] == "fail" for m in report["metrics"])


def test_performance_report_summarizes_device_metrics():
    diag = make_diag()

    def fake_run(adb, device, args, timeout=None):
        command = " ".join(args)
        if command.endswith("getprop"):
            return (
                "[ro.product.manufacturer]: [Test]\n"
                "[ro.product.model]: [Phone]\n"
                "[ro.build.version.release]: [14]\n"
                "[ro.build.version.sdk]: [34]\n",
                "",
            )
        if command.endswith("wm size"):
            return "Physical size: 1220x2712\n", ""
        if command.endswith("cat /proc/meminfo"):
            return "MemTotal: 8000000 kB\nMemAvailable: 3000000 kB\n", ""
        if command.endswith("df -k /data"):
            return (
                "Filesystem 1K-blocks Used Available Use% Mounted on\n"
                "/dev/block/dm-1 100000000 50000000 50000000 50% /data\n",
                "",
            )
        if command.endswith("dumpsys battery"):
            return "level: 80\ntemperature: 365\n", ""
        if command.endswith("dumpsys thermalservice"):
            return "Thermal Status: 0\n", ""
        if "settings get global" in command:
            return "0\n", ""
        if command.endswith("dumpsys display"):
            return "mRefreshRate=120.000004\n", ""
        if command.endswith("pidof cn.damai"):
            return "1234\n", ""
        if "gfxinfo" in command:
            return GFXINFO_OUTPUT, ""
        if "meminfo cn.damai" in command:
            return "TOTAL PSS: 409600\n", ""
        if "cpuinfo" in command:
            return "  1.5% 1234/cn.damai: 1.0% user + 0.5% kernel\n", ""
        return "", ""

    with patch("android_diagnostics.shutil.which", return_value="/usr/bin/adb"), \
         patch.object(diag, "_safe_run", side_effect=fake_run):
        report = diag.performance_report("device-1")

    assert report["ok"] is True
    assert report["level"] == "pass"
    ids = {metric["id"] for metric in report["metrics"]}
    assert {"device", "ram", "storage", "temperature", "thermal", "animations", "jank", "p90"} <= ids


def test_performance_report_flags_hot_and_low_memory():
    diag = make_diag()

    def fake_run(adb, device, args, timeout=None):
        command = " ".join(args)
        if command.endswith("getprop"):
            return "[ro.product.model]: [Phone]\n", ""
        if command.endswith("cat /proc/meminfo"):
            return "MemTotal: 8000000 kB\nMemAvailable: 500000 kB\n", ""
        if command.endswith("dumpsys battery"):
            return "temperature: 450\n", ""
        if command.endswith("dumpsys thermalservice"):
            return "Thermal Status: 3\n", ""
        return "", ""

    with patch("android_diagnostics.shutil.which", return_value="/usr/bin/adb"), \
         patch.object(diag, "_safe_run", side_effect=fake_run):
        report = diag.performance_report("device-1")

    assert report["ok"] is False
    assert any(m["id"] == "ram" and m["level"] == "fail" for m in report["metrics"])
    assert any(m["id"] == "temperature" and m["level"] == "fail" for m in report["metrics"])
