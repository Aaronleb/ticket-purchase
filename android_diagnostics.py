# -*- coding: UTF-8 -*-
"""安卓网络与设备性能诊断。

只读 adb 命令，不修改大麦或系统状态。所有解析函数都是纯函数，
方便在没有真机时用固定输出做单元测试。
"""
from __future__ import annotations

import re
import shutil
import statistics
import subprocess


PING_HOSTS = ("detail.damai.cn", "mtop.damai.cn")



def _metric(metric_id, label, value, level, target, message):
    return {
        "id": metric_id,
        "label": label,
        "value": value,
        "level": level,
        "target": target,
        "message": message,
    }


def _overall(metrics, advice, prefix):
    fails = [m for m in metrics if m["level"] == "fail"]
    warns = [m for m in metrics if m["level"] == "warn"]
    if fails:
        level = "fail"
        summary = f"{prefix}发现 {len(fails)} 项不达标"
    elif warns:
        level = "warn"
        summary = f"{prefix}基本可用，{len(warns)} 项建议优化"
    else:
        level = "pass"
        summary = f"{prefix}全部达标"
    return {
        "ok": not fails,
        "level": level,
        "summary": summary,
        "metrics": metrics,
        "advice": advice,
    }


# ---------- 纯解析：网络 ----------
def parse_ping(text):
    """解析 ping 输出，返回 RTT/抖动/丢包。"""
    times = [
        float(value)
        for value in re.findall(r"time[=<]\s*([\d.]+)\s*ms", text or "")
    ]
    loss_match = re.search(r"(\d+(?:\.\d+)?)%\s*packet loss", text or "")
    sent_match = re.search(r"(\d+)\s+packets transmitted", text or "")
    received_match = re.search(r"(\d+)\s+(?:packets )?received", text or "")
    stats = re.search(
        r"(?:rtt|round-trip)\s+min/avg/max/(?:mdev|stddev)\s*=\s*"
        r"([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)\s*ms",
        text or "",
    )
    if stats:
        min_ms, avg_ms, max_ms, jitter = (float(v) for v in stats.groups())
    elif times:
        min_ms = min(times)
        avg_ms = statistics.fmean(times)
        max_ms = max(times)
        jitter = statistics.pstdev(times) if len(times) > 1 else 0.0
    else:
        min_ms = avg_ms = max_ms = jitter = None
    loss = float(loss_match.group(1)) if loss_match else None
    if loss is None and times:
        loss = 0.0
    return {
        "min_ms": min_ms,
        "avg_ms": avg_ms,
        "max_ms": max_ms,
        "jitter_ms": jitter,
        "loss": loss,
        "sent": int(sent_match.group(1)) if sent_match else None,
        "received": int(received_match.group(1)) if received_match else None,
    }


def parse_wifi(text):
    """解析 `cmd wifi status` 或 `dumpsys wifi` 里的 Wi-Fi 信息。"""
    text = text or ""
    ssid_match = re.search(r'SSID:\s*"([^"]*)"', text) or re.search(
        r"SSID:\s*([^,\n]+)", text
    )
    rssi_match = re.search(r"RSSI:\s*(-?\d+)", text)
    link_match = re.search(
        r"(?:Link speed|LinkSpeed):\s*(\d+)\s*Mbps", text, re.I
    )
    freq_match = re.search(r"Frequency:\s*(\d+)\s*MHz", text)
    return {
        "enabled": "Wifi is enabled" in text or "Wi-Fi is enabled" in text,
        "ssid": ssid_match.group(1).strip() if ssid_match else "",
        "rssi": int(rssi_match.group(1)) if rssi_match else None,
        "link_mbps": int(link_match.group(1)) if link_match else None,
        "frequency_mhz": int(freq_match.group(1)) if freq_match else None,
    }


def parse_connectivity(text):
    match = re.search(r"Active default network:\s*([^\n]+)", text or "")
    return match.group(1).strip() if match else ""


# ---------- 纯解析：设备 ----------
def parse_properties(text):
    properties = {}
    for line in (text or "").splitlines():
        match = re.match(r"\[([^\]]+)\]:\s*\[(.*)\]", line.strip())
        if match:
            properties[match.group(1)] = match.group(2)
    return properties


def parse_meminfo(text):
    total = re.search(r"MemTotal:\s*(\d+)\s*kB", text or "")
    available = re.search(r"MemAvailable:\s*(\d+)\s*kB", text or "")
    free = re.search(r"MemFree:\s*(\d+)\s*kB", text or "")
    return {
        "total_mb": int(total.group(1)) / 1024 if total else None,
        "available_mb": (
            int(available.group(1)) / 1024
            if available
            else (int(free.group(1)) / 1024 if free else None)
        ),
    }


def parse_df(text):
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if len(lines) < 2:
        return {}
    parts = lines[-1].split()
    if len(parts) < 5:
        return {}
    try:
        total_kb = int(parts[1])
        available_kb = int(parts[3])
    except ValueError:
        return {}
    return {
        "total_gb": total_kb / 1024 / 1024,
        "available_gb": available_kb / 1024 / 1024,
        "use_percent": parts[4],
    }


def parse_battery(text):
    temperature = re.search(r"temperature:\s*(\d+)", text or "")
    level = re.search(r"level:\s*(\d+)", text or "")
    status = re.search(r"status:\s*(\d+)", text or "")
    return {
        "temperature_c": (
            int(temperature.group(1)) / 10 if temperature else None
        ),
        "level": int(level.group(1)) if level else None,
        "status": int(status.group(1)) if status else None,
    }


def parse_thermal(text):
    match = re.search(
        r"(?:Thermal Status|mStatus)[:=]\s*(\d+)", text or ""
    )
    return int(match.group(1)) if match else None


def _percentile(text, name):
    match = re.search(
        rf"{name}th percentile:\s*(\d+(?:\.\d+)?)\s*(ms|s)?", text or ""
    )
    if not match:
        return None
    value = float(match.group(1))
    return value * 1000 if match.group(2) == "s" else value


def parse_gfxinfo(text):
    text = text or ""
    total = re.search(r"Total frames rendered:\s*(\d+)", text)
    janky = re.search(r"Janky frames:\s*(\d+)", text)
    janky_percent = re.search(
        r"Janky frames:\s*\d+\s*\(([\d.]+)%\)", text
    )
    return {
        "total_frames": int(total.group(1)) if total else None,
        "janky_frames": int(janky.group(1)) if janky else None,
        "janky_percent": (
            float(janky_percent.group(1)) if janky_percent else None
        ),
        "p50_ms": _percentile(text, 50),
        "p90_ms": _percentile(text, 90),
        "p95_ms": _percentile(text, 95),
        "p99_ms": _percentile(text, 99),
    }


def parse_app_meminfo(text):
    match = re.search(r"TOTAL PSS:\s*(\d+)", text or "") or re.search(
        r"^\s*TOTAL\s+(\d+)", text or "", re.M
    )
    return int(match.group(1)) if match else None


def parse_cpuinfo(text):
    for line in (text or "").splitlines():
        if "cn.damai" in line:
            match = re.search(r"([\d.]+)%", line)
            if match:
                return float(match.group(1))
    return None


def parse_refresh_rate(text):
    values = [
        float(value)
        for value in re.findall(
            r"(?:mRefreshRate|refreshRate)=([\d.]+)", text or ""
        )
    ]
    return max(values) if values else None


def parse_wm_size(text):
    match = re.search(r"(\d+)x(\d+)", text or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


# ---------- 诊断 ----------
class AndroidDiagnostics:
    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    def _safe_run(self, adb, device, args, timeout=None):
        try:
            result = subprocess.run(
                [adb, "-s", device, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout or self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "", "命令超时"
        except OSError as exc:
            return "", f"{type(exc).__name__}: {exc}"
        error = (result.stderr or "").strip() if result.returncode else ""
        return result.stdout or "", error

    # ---------- 网络 ----------
    def network_report(self, device: str) -> dict:
        adb = shutil.which("adb")
        if not adb:
            return _overall(
                [
                    _metric(
                        "adb", "ADB", "不可用", "fail",
                        "需要 adb", "未找到 adb，请安装 Platform-Tools",
                    )
                ],
                ["安装 adb 后重试"],
                "网络检查",
            )
        metrics = []
        advice = []
        for index, host in enumerate(PING_HOSTS):
            data, error = self._ping(adb, device, host)
            label = f"到 {host} 的 RTT"
            if data.get("avg_ms") is None:
                metrics.append(
                    _metric(
                        f"rtt-{index}", label, "不可达", "warn",
                        "平均 < 60 ms",
                        f"ping 失败：{error or 'ICMP 可能被拦截'}",
                    )
                )
                advice.append(f"{host} 无法 ping 通，先用浏览器确认手机能打开大麦")
                continue
            avg = data["avg_ms"]
            level = "pass" if avg < 60 else "warn" if avg < 100 else "fail"
            metrics.append(
                _metric(
                    f"rtt-{index}", label, f"{avg:.0f} ms", level,
                    "平均 < 60 ms",
                    f"最小 {data['min_ms']:.0f} / 最大 {data['max_ms']:.0f} ms",
                )
            )
            jitter = data.get("jitter_ms")
            if jitter is not None:
                jlevel = "pass" if jitter < 10 else "warn" if jitter < 30 else "fail"
                metrics.append(
                    _metric(
                        f"jitter-{index}", f"{host} 抖动",
                        f"{jitter:.1f} ms", jlevel, "< 10 ms",
                        "抖动越大，页面请求耗时越不稳定",
                    )
                )
            loss = data.get("loss")
            if loss is not None:
                llevel = "pass" if loss == 0 else "warn" if loss < 1 else "fail"
                metrics.append(
                    _metric(
                        f"loss-{index}", f"{host} 丢包",
                        f"{loss:.1f}%", llevel, "0%",
                        f"发送 {data.get('sent') or '-'}，接收 {data.get('received') or '-'}",
                    )
                )

        wifi_text, _ = self._safe_run(adb, device, ["shell", "cmd", "wifi", "status"], timeout=6)
        if not wifi_text.strip():
            wifi_text, _ = self._safe_run(adb, device, ["shell", "dumpsys", "wifi"], timeout=8)
        wifi = parse_wifi(wifi_text)
        if wifi["rssi"] is not None:
            rssi = wifi["rssi"]
            level = "pass" if rssi > -60 else "warn" if rssi > -75 else "fail"
            metrics.append(
                _metric(
                    "wifi-rssi", "Wi-Fi 信号", f"{rssi} dBm", level,
                    "> -60 dBm", f"SSID：{wifi['ssid'] or '未知'}",
                )
            )
            if wifi["link_mbps"]:
                link = wifi["link_mbps"]
                llevel = "pass" if link >= 300 else "warn" if link >= 100 else "fail"
                metrics.append(
                    _metric(
                        "wifi-link", "Wi-Fi 协商速率", f"{link} Mbps",
                        llevel, "> 300 Mbps",
                        "协商速率低通常说明距离远或走的是 2.4GHz",
                    )
                )
            if wifi["frequency_mhz"]:
                freq = wifi["frequency_mhz"]
                flevel = "pass" if freq >= 5000 else "warn"
                metrics.append(
                    _metric(
                        "wifi-band", "Wi-Fi 频段",
                        f"{freq} MHz", flevel, "5GHz（≥ 5000 MHz）",
                        "5GHz 通常比 2.4GHz 延迟更低、干扰更少",
                    )
                )
        else:
            connectivity, _ = self._safe_run(
                adb, device, ["shell", "dumpsys", "connectivity"], timeout=8
            )
            active = parse_connectivity(connectivity) or "非 Wi-Fi"
            metrics.append(
                _metric(
                    "wifi", "Wi-Fi", "未连接", "info",
                    "建议 5GHz Wi-Fi",
                    f"当前默认网络：{active}；蜂窝网络请靠近窗边或改用 5GHz Wi-Fi",
                )
            )
            advice.append("当前未检测到 Wi-Fi，抢票建议使用 5GHz Wi-Fi 或稳定 5G")

        dns_mode, _ = self._safe_run(
            adb, device, ["shell", "settings", "get", "global", "private_dns_mode"], timeout=5
        )
        dns_mode = dns_mode.strip() or "未知"
        dlevel = "pass" if dns_mode in {"off", "opportunistic"} else "warn"
        metrics.append(
            _metric(
                "dns", "私人 DNS", dns_mode, dlevel,
                "off 或 opportunistic",
                "hostname 模式多一次解析链路，可能增加首包延迟",
            )
        )

        if any(m["level"] == "fail" for m in metrics):
            advice.append("网络存在不达标项：优先切 5GHz Wi-Fi、关闭 VPN/代理，再复测")
        elif any(m["level"] == "warn" for m in metrics):
            advice.append("网络基本可用；如果订单页仍慢，瓶颈更可能在服务端或设备")
        return _overall(metrics, _dedupe(advice), "网络检查")

    def _ping(self, adb, device, host, count=15):
        stdout, error = self._safe_run(
            adb, device,
            ["shell", "ping", "-c", str(count), "-i", "0.2", host],
            timeout=20,
        )
        if not stdout.strip():
            stdout, error = self._safe_run(
                adb, device, ["shell", "ping", "-c", str(count), host],
                timeout=20,
            )
        return parse_ping(stdout), error

    # ---------- 设备 ----------
    def performance_report(self, device: str) -> dict:
        adb = shutil.which("adb")
        if not adb:
            return _overall(
                [
                    _metric(
                        "adb", "ADB", "不可用", "fail",
                        "需要 adb", "未找到 adb，请安装 Platform-Tools",
                    )
                ],
                ["安装 adb 后重试"],
                "设备检查",
            )
        metrics = []
        advice = []

        props_text, _ = self._safe_run(adb, device, ["shell", "getprop"], timeout=8)
        props = parse_properties(props_text)
        model = " ".join(
            value for value in (
                props.get("ro.product.manufacturer"),
                props.get("ro.product.model"),
            ) if value
        ) or "未知机型"
        version = props.get("ro.build.version.release", "未知")
        sdk = props.get("ro.build.version.sdk", "?")
        soc = props.get("ro.soc.model") or props.get("ro.board.platform") or "未知"
        metrics.append(
            _metric(
                "device", "设备", model, "info", "近 2~3 年主流机型",
                f"Android {version}（API {sdk}），SoC：{soc}",
            )
        )

        size_text, _ = self._safe_run(adb, device, ["shell", "wm", "size"], timeout=5)
        size = parse_wm_size(size_text)
        if size:
            metrics.append(
                _metric(
                    "screen", "屏幕分辨率", f"{size[0]}×{size[1]}", "info",
                    "不影响成败", "坐标会按实际分辨率换算",
                )
            )

        mem_text, _ = self._safe_run(adb, device, ["shell", "cat", "/proc/meminfo"], timeout=6)
        mem = parse_meminfo(mem_text)
        if mem.get("available_mb") is not None:
            available_gb = mem["available_mb"] / 1024
            level = "pass" if available_gb >= 2 else "warn" if available_gb >= 1 else "fail"
            metrics.append(
                _metric(
                    "ram", "可用内存", f"{available_gb:.1f} GB", level,
                    "> 2 GB",
                    f"总内存 {mem['total_mb'] / 1024:.1f} GB"
                    if mem.get("total_mb") else "",
                )
            )
            if level != "pass":
                advice.append("可用内存偏低：重启手机并关闭后台应用，避免大麦被回收")

        df_text, _ = self._safe_run(adb, device, ["shell", "df", "-k", "/data"], timeout=5)
        disk = parse_df(df_text)
        if disk:
            available = disk["available_gb"]
            level = "pass" if available >= 10 else "warn" if available >= 5 else "fail"
            metrics.append(
                _metric(
                    "storage", "可用存储", f"{available:.1f} GB", level,
                    "> 10 GB",
                    f"总容量 {disk['total_gb']:.0f} GB，已用 {disk['use_percent']}",
                )
            )
            if level != "pass":
                advice.append("存储空间不足：清理缓存和视频，至少留 10GB")

        battery_text, _ = self._safe_run(adb, device, ["shell", "dumpsys", "battery"], timeout=5)
        battery = parse_battery(battery_text)
        if battery.get("temperature_c") is not None:
            temp = battery["temperature_c"]
            level = "pass" if temp < 38 else "warn" if temp < 42 else "fail"
            metrics.append(
                _metric(
                    "temperature", "电池温度", f"{temp:.1f} °C", level,
                    "< 38 °C",
                    "温度过高会触发降频；抓票前取下手机壳、避免边充边抢",
                )
            )
            if level != "pass":
                advice.append("手机偏热：停止充电、取下手机壳或加散热背夹")

        thermal_text, _ = self._safe_run(adb, device, ["shell", "dumpsys", "thermalservice"], timeout=6)
        thermal = parse_thermal(thermal_text)
        if thermal is not None:
            level = "pass" if thermal == 0 else "warn" if thermal <= 2 else "fail"
            metrics.append(
                _metric(
                    "thermal", "系统热状态", str(thermal), level,
                    "0（无限制）",
                    "0 无限制，1 轻度，2 中度，3 严重，4 以上紧急",
                )
            )
            if level != "pass":
                advice.append("系统已进入热限制：先降温，否则页面跳转会明显变慢")

        scales = {}
        for key in ("window_animation_scale", "transition_animation_scale", "animator_duration_scale"):
            value, _ = self._safe_run(
                adb, device, ["shell", "settings", "get", "global", key], timeout=5
            )
            scales[key] = value.strip()
        animations_off = all(value in {"0", "0.0", "0.00"} for value in scales.values())
        metrics.append(
            _metric(
                "animations", "系统动画",
                "已关闭" if animations_off else "未关闭",
                "pass" if animations_off else "warn",
                "三项均为 0",
                "窗口 / 过渡 / 动画时长："
                + " / ".join(f"{value or '未知'}" for value in scales.values()),
            )
        )
        if not animations_off:
            advice.append("关闭系统动画：设置里把窗口、过渡、动画时长都调到 0")

        display_text, _ = self._safe_run(adb, device, ["shell", "dumpsys", "display"], timeout=8)
        refresh = parse_refresh_rate(display_text)
        if refresh:
            level = "pass" if refresh >= 90 else "warn"
            metrics.append(
                _metric(
                    "refresh", "屏幕刷新率", f"{refresh:.0f} Hz", level,
                    "≥ 90 Hz", "高刷能让页面转场更跟手",
                )
            )

        pid, _ = self._safe_run(adb, device, ["shell", "pidof", "cn.damai"], timeout=5)
        running = bool(pid.strip())
        metrics.append(
            _metric(
                "app", "大麦进程", "运行中" if running else "未运行", "info",
                "测试帧率时保持运行",
                "未运行时无法读取帧率和内存数据" if not running else "可以读取帧率与内存",
            )
        )

        if running:
            gfx_text, _ = self._safe_run(
                adb, device, ["shell", "dumpsys", "gfxinfo", "cn.damai"], timeout=10
            )
            gfx = parse_gfxinfo(gfx_text)
            if gfx.get("janky_percent") is not None:
                jank = gfx["janky_percent"]
                level = "pass" if jank < 5 else "warn" if jank < 10 else "fail"
                metrics.append(
                    _metric(
                        "jank", "Janky frames", f"{jank:.1f}%", level,
                        "< 5%",
                        f"共渲染 {gfx.get('total_frames') or '-'} 帧"
                        "（App 启动以来的累计值）",
                    )
                )
                if level != "pass":
                    advice.append("掉帧偏高：开启性能模式、关闭后台，避免边充边抢")
            if gfx.get("p90_ms") is not None:
                p90 = gfx["p90_ms"]
                level = "pass" if p90 <= 16.7 else "warn" if p90 <= 33 else "fail"
                metrics.append(
                    _metric(
                        "p90", "90th 帧时间", f"{p90:.1f} ms", level,
                        "≤ 16.7 ms",
                        "累计值；先在详情页重置帧统计再测更准。"
                        "60fps 预算是 16.7ms，90Hz 约 11ms",
                    )
                )
            app_mem_text, _ = self._safe_run(
                adb, device, ["shell", "dumpsys", "meminfo", "cn.damai"], timeout=8
            )
            app_pss = parse_app_meminfo(app_mem_text)
            if app_pss is not None:
                pss_mb = app_pss / 1024
                level = "pass" if pss_mb < 800 else "warn" if pss_mb < 1200 else "fail"
                metrics.append(
                    _metric(
                        "app-pss", "大麦内存占用", f"{pss_mb:.0f} MB", level,
                        "< 800 MB", "内存过高会增加被系统回收的概率",
                    )
                )
            cpu_text, _ = self._safe_run(
                adb, device, ["shell", "dumpsys", "cpuinfo"], timeout=8
            )
            cpu = parse_cpuinfo(cpu_text)
            if cpu is not None:
                # CPU 是瞬时值，诊断命令本身也会占一点；只做提示，不判失败
                level = "pass" if cpu < 30 else "warn"
                metrics.append(
                    _metric(
                        "cpu", "大麦 CPU", f"{cpu:.0f}%", level,
                        "< 30%", "瞬时值，抓票前应处于低负载",
                    )
                )
        else:
            advice.append("先打开大麦并停在详情页，再点一次设备性能测试，才能读到帧率")

        if not any(m["level"] == "fail" for m in metrics):
            if not advice:
                advice.append("设备指标全部达标；如果页面仍慢，优先查网络和页面跳转")
        return _overall(metrics, _dedupe(advice), "设备检查")


def _dedupe(items):
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
