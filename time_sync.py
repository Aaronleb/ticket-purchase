# -*- coding: UTF-8 -*-
"""测量本机与手机、NTP 服务器的时钟偏差，用于对齐开抢放行时刻。

符号约定：偏差为正 = 本机比对方快（本机 - 对方）。
- 服务器偏差走 SNTP（UDP 123，stdlib 实现），多个源取中位数；
  HTTP Date 头只在 SNTP 全灭时兜底（CDN 缓存可能给过期 Date，不可靠）。
- 手机偏差通过 adb 读取 $EPOCHREALTIME 计算，大麦倒计时以手机时钟为准。
"""
from __future__ import annotations

import shutil
import socket
import statistics
import struct
import subprocess
import time
from email.utils import parsedate_to_datetime
from urllib.request import Request, urlopen


NTP_SOURCES = ("ntp.aliyun.com", "time.apple.com", "pool.ntp.org")
HTTP_FALLBACK_SOURCES = ("https://www.aliyun.com/",)
NTP_EPOCH_DELTA = 2208988800  # 1900-01-01 到 1970-01-01 的秒数


def _sntp_offset(host: str, samples: int = 3):
    """最小 SNTP 客户端，返回 (offset_seconds, rtt_seconds)，失败 None。"""
    packet = b"\x1b" + 47 * b"\0"
    best = None
    for _ in range(samples):
        t0 = time.time()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(3)
                sock.sendto(packet, (host, 123))
                data, _ = sock.recvfrom(1024)
        except OSError:
            continue
        t3 = time.time()
        if len(data) < 48:
            continue

        def stamp(offset: int) -> float:
            seconds, fraction = struct.unpack("!II", data[offset : offset + 8])
            return seconds - NTP_EPOCH_DELTA + fraction / 2**32

        originate, receive = stamp(32), stamp(40)
        rtt = t3 - t0
        # offset = 本机 - 服务器
        offset = -((receive - t0) + (originate - t3)) / 2
        if best is None or rtt < best[1]:
            best = (offset, rtt)
    return best


def _http_date_offset(url: str, samples: int = 3):
    """HTTP Date 头兜底，返回 (offset_seconds, rtt_seconds)，失败 None。"""
    best = None
    for _ in range(samples):
        t0 = time.time()
        try:
            with urlopen(Request(url, method="GET"), timeout=3) as response:
                date_header = response.headers.get("Date")
                response.read(64)
        except Exception:
            continue
        t1 = time.time()
        if not date_header:
            continue
        try:
            server_dt = parsedate_to_datetime(date_header)
        except (TypeError, ValueError):
            continue
        rtt = t1 - t0
        # offset = 本机 - 服务器（假设去回延迟对称）
        offset = (t0 + rtt / 2) - server_dt.timestamp()
        if best is None or rtt < best[1]:
            best = (offset, rtt)
    return best


def measure_server_offset() -> tuple[float | None, dict]:
    """返回 (offset_seconds, 明细)。先 SNTP，全灭再退回 HTTP Date。"""
    details: dict = {}
    offsets = []
    for host in NTP_SOURCES:
        got = _sntp_offset(host)
        if got:
            details[f"ntp:{host}"] = {"offset_ms": got[0] * 1000, "rtt_ms": got[1] * 1000}
            offsets.append(got[0])
    if offsets:
        return statistics.median(offsets), details
    details["ntp_error"] = "全部 NTP 源不可达，退回 HTTP Date"
    for url in HTTP_FALLBACK_SOURCES:
        got = _http_date_offset(url)
        if got:
            details[f"http:{url}"] = {"offset_ms": got[0] * 1000, "rtt_ms": got[1] * 1000}
            offsets.append(got[0])
    return (statistics.median(offsets) if offsets else None), details


def resolve_adb() -> str:
    adb = shutil.which("adb")
    if adb:
        return adb
    # desktop_app 会把内置 adb 目录注入 PATH；直接调用时再兜底一次
    from pathlib import Path

    bundled = (
        Path(__file__).resolve().parent
        / "build" / "runtime" / "android-platform-tools" / "adb"
    )
    if bundled.is_file():
        return str(bundled)
    raise RuntimeError("未找到 adb")


def measure_phone_offset(device: str, timeout: int = 10) -> float:
    """本机 - 手机的时钟偏差（秒），正数 = 本机偏快。"""
    result = subprocess.run(
        [resolve_adb(), "-s", device, "shell", "echo", "$EPOCHREALTIME"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    result.check_returncode()
    phone_epoch = float(result.stdout.strip())
    return time.time() - phone_epoch


def measure_time_sync(device: str | None = None) -> dict:
    """返回各偏差（毫秒）与明细；offset 为正表示本机偏快。"""
    server_offset, sources = measure_server_offset()
    phone_offset = None
    if device:
        try:
            phone_offset = measure_phone_offset(device)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            sources["phone_error"] = str(exc)
    return {
        "ok": server_offset is not None or phone_offset is not None,
        "phone_offset_ms": phone_offset * 1000 if phone_offset is not None else None,
        "server_offset_ms": server_offset * 1000 if server_offset is not None else None,
        "sources": sources,
    }
