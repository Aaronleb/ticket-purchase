# -*- coding: UTF-8 -*-
"""大麦 App 原生预约快路。

整条链路不创建 WebDriver 会话：开售瞬间用 adb 点击，票档用本地 OCR 定位，
确认页用 `uiautomator dump` 读取。实测建一次 Appium 会话要 7~9 秒，而建会话
本身与抢票无关，所以这里把它彻底移出关键路径。

极速模式的页面首帧不再用 20~30ms 一次的 `dumpsys` 轮询，而是常驻
`adb logcat` 解析 `START`/`Displayed` 事件；点击类 adb 命令超时不重放，
避免确认页提交被重复注入。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

# 项目根目录的 flip_watch / time_sync / adb_session 需要可导入；
# 控制台启动 worker 时 cwd 是 damai_appium，默认不在 sys.path。
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from .config import Config
    from .sale_clock import (
        SHANGHAI,
        SaleRelease,
        SaleTimeError,
        parse_sale_time,
        wait_for_sale_time,
    )
except ImportError:
    from config import Config
    from sale_clock import (
        SHANGHAI,
        SaleRelease,
        SaleTimeError,
        parse_sale_time,
        wait_for_sale_time,
    )


# 从点“确定”到确认页出现的等待窗口
CONFIRM_TIMEOUT = 15
# 等选票档页的窗口：超时就认为已经直接到了确认页
SKU_TIMEOUT = 5
# 详情页底栏“立即购买/立即预订”的点击位置（距屏幕底部像素）
BUY_BAR_TAP_OFFSET = 107
# 详情页底栏容器（“立即预订/立即购买”所在整条栏），提前 dump 一次即可拿到坐标
BUY_BAR_CONTAINER_ID = "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
# 票档卡片与“确定”按钮都是自绘控件，无障碍树里没有文本，只能截图 + OCR 定位
OCR_TOOL = Path(__file__).resolve().parents[1] / "tools" / "damai_ocr"
SUBMIT_TEXTS = ("立即提交", "提交订单")
BUY_TEXTS = ("立即购票", "立即购买", "立即预订")
SKU_MARKERS = ("场次", "票档")
NETWORK_ERROR_MARKERS = ("网络不太顺畅", "努力刷新")
VIEWER_CHECKBOX_ID = "cn.damai:id/checkbox"
# 极速模式的兜底坐标（按 1220x2712 实测：确定/立即提交都在底部栏中央，
# 旧比例 0.96 按 1080x2412 推算，在本机会偏出按钮约 40px 导致直点失效；
# 配置里写了 confirm_point/submit_point 精确坐标时优先用配置值）
CONFIRM_POINT_RATIO = (0.779, 0.945)
SUBMIT_POINT_RATIO = (0.789, 0.945)
# 极速模式点底栏：首轮等翻页的窗口（覆盖正常页面跳转时长），
# 未翻页则按重试节奏再点，覆盖大麦按钮延迟翻转；总预算 BUY_RETRY_TIMEOUT 秒
BUY_FIRST_WAIT = 0.9
BUY_RETRY_WAIT = 0.3
BUY_RETRY_TIMEOUT = 3.5
# 确认页首帧到“立即提交”可点之间给一点稳定时间，避免点到加载中的禁用按钮
SUBMIT_SETTLE_SECONDS = 0.4
# 提交后只做一次轻量校验，不补点
SUBMIT_VERIFY_TIMEOUT = 1.0


class ConfirmationError(RuntimeError):
    """订单确认页不符合提交条件。"""


class TicketUnavailableError(RuntimeError):
    """配置的票档不存在或已缺货：直接结束，不再尝试其他票档。"""


def parse_amount(text):
    """从确认页金额控件读取唯一、大于零的金额。"""
    if not isinstance(text, str):
        return None
    matches = re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))
    if len(matches) != 1:
        return None
    amount = float(matches[0])
    return amount if amount > 0 else None


def parse_bounds(text):
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", text or "")
    if not match:
        return None
    return tuple(int(value) for value in match.groups())


class DamaiBot:
    def __init__(self):
        self.config = Config.load_config()
        self._purchase_attempted = False
        self._submit_attempted = False
        self._submit_verified = None
        self._started_at = None
        # 开售前 dump 一次底栏位置并缓存，放行瞬间只剩 1 次 adb tap
        self._buy_point = None
        # 常驻 adb shell 会话：None=未尝试，False=不可用，对象=可用
        self._adb_session = None
        # 屏幕尺寸只读一次，关键路径不再为“确定/提交”坐标跑 wm size
        self._screen_size = None
        # logcat 事件感知：None=未启动，False=不可用，对象=可用
        self._activity_watch = None
        # 分段耗时：[(阶段名, 相对放行的秒数)]
        self._timings = []
        self._flow = "normal"
        # 当前详情页是否还是“预约/想看”的未开售状态
        self._presale_detected = False

    # ---------- 底层：adb ----------
    def _adb(self, *args, timeout=10):
        # 优先走常驻 shell 会话（省每次 34ms 进程创建）；失败退回单次调用
        if self._adb_session is None:
            try:
                from adb_session import AdbShellSession

                self._adb_session = AdbShellSession(self.config.device_name)
            except Exception:
                self._adb_session = False
        # 点击/返回这类非幂等命令超时后可能已经生效，绝不重放
        replay = not (
            len(args) >= 2 and args[0] == "shell" and args[1] == "input"
        )
        if self._adb_session:
            try:
                stdout = self._adb_session.run(
                    args, timeout=timeout, replay=replay
                )
                return SimpleNamespace(stdout=stdout, returncode=0)
            except Exception as exc:
                try:
                    self._adb_session.close()
                except Exception:
                    pass
                self._adb_session = None
                if not replay:
                    print(f"常驻 adb 点击超时（{exc}），不重放，按页面状态判断")
                    return SimpleNamespace(stdout="", returncode=1)
                # 查询类命令可以安全重放：一次抖动不能把整场抢票
                # 降级成每次 34ms 的新进程，先重建会话再试一次。
                print(f"常驻 adb 会话失败（{exc}），重建后重试")
                try:
                    from adb_session import AdbShellSession

                    self._adb_session = AdbShellSession(self.config.device_name)
                    stdout = self._adb_session.run(
                        args, timeout=timeout, replay=replay
                    )
                    return SimpleNamespace(stdout=stdout, returncode=0)
                except Exception as retry_exc:
                    print(f"常驻 adb 会话重建失败（{retry_exc}），退回单次调用")
                    self._adb_session = False
        return subprocess.run(
            ["adb", "-s", self.config.device_name, *args],
            capture_output=True, text=True, timeout=timeout,
        )

    def _adb_size(self, refresh=False):
        """屏幕尺寸只读一次；关键路径上的坐标换算不再走 adb。"""
        cached = getattr(self, "_screen_size", None)
        if cached and not refresh:
            return cached
        match = re.search(r"(\d+)x(\d+)", self._adb("shell", "wm", "size").stdout)
        if not match:
            # 读取失败不要缓存默认值，否则后面的坐标会全部按错误分辨率换算
            return (1080, 2412)
        self._screen_size = (int(match.group(1)), int(match.group(2)))
        return self._screen_size

    def _adb_tap(self, x, y):
        self._adb("shell", "input", "tap", str(int(x)), str(int(y)))

    def _foreground_activity(self):
        output = self._adb("shell", "dumpsys", "activity", "activities").stdout
        match = re.search(r"topResumedActivity=.*?/([\w.$]+)", output)
        return match.group(1) if match else ""

    # ---------- logcat 事件感知 ----------
    def _start_activity_watch(self):
        """开售前启动常驻 logcat，把页面首帧变成事件而不是轮询。"""
        watcher = getattr(self, "_activity_watch", None)
        if watcher:
            return watcher
        if watcher is False:
            return None
        try:
            from activity_watch import ActivityWatcher

            watcher = ActivityWatcher(self.config.device_name)
            if watcher.start():
                self._activity_watch = watcher
                print("页面事件感知已启动（logcat START/Displayed）")
                return watcher
            print(f"页面事件感知不可用（{watcher.error}），退回 dumpsys 轮询")
        except Exception as exc:  # noqa: BLE001 - 感知失败不阻断抢票
            print(f"页面事件感知启动失败（{exc}），退回 dumpsys 轮询")
        self._activity_watch = False
        return None

    def _stop_activity_watch(self):
        watcher = getattr(self, "_activity_watch", None)
        if watcher:
            watcher.stop()
        self._activity_watch = None

    def _wait_started(self, markers, timeout, since=None):
        """等任一页面启动；logcat 不可用时退回 20ms dumpsys 轮询。

        事件等待按 0.3 秒切片，每片用一次 dumpsys 兜底：logcat 万一丢行，
        最多多等 0.3 秒，而不是整个窗口都白等。
        """
        watcher = getattr(self, "_activity_watch", None)
        deadline = time.perf_counter() + max(timeout, 0.0)
        if watcher and getattr(watcher, "running", True):
            while time.perf_counter() < deadline:
                now = time.perf_counter()
                found = watcher.wait_any_started(
                    markers, min(0.3, deadline - now), since=since
                )
                if found:
                    return found
                activity = self._foreground_activity()
                for marker in markers:
                    if marker in activity:
                        return marker
                if not getattr(watcher, "running", True):
                    break
        while time.perf_counter() < deadline:
            activity = self._foreground_activity()
            for marker in markers:
                if marker in activity:
                    return marker
            time.sleep(0.02)
        return ""

    def _wait_displayed(self, markers, timeout, since=None):
        """等任一页面首帧；logcat 不可用时返回空串，由调用方走 dumpsys。"""
        watcher = getattr(self, "_activity_watch", None)
        if not watcher or not getattr(watcher, "running", True):
            return ""
        return watcher.wait_any_displayed(markers, timeout, since=since)

    def _locate_buy_point_by_ocr(self):
        """自绘底栏不在无障碍树里时，用 OCR 找底部的购买按钮。

        只取最靠下的命中，避免把推荐流里的“立即购票”当成底栏。
        """
        try:
            rows = self._ocr()
        except Exception as exc:  # noqa: BLE001 - OCR 失败只降级
            print(f"OCR 定位底栏失败：{exc}")
            return None
        candidates = []
        for x, y, _confidence, text in rows:
            normalized = (text or "").replace(" ", "")
            if "立即购" in normalized or "立即预订" in normalized:
                candidates.append((x, y))
        if not candidates:
            return None
        return max(candidates, key=lambda point: point[1])

    def _prepare_buy_point(self):
        """开售前定位底栏按钮，避免放行瞬间还要 dump 一次。"""
        # 屏幕尺寸在这里读一次，后面“确定/提交”坐标换算直接复用
        self._adb_size()
        activity = self._foreground_activity()
        # 新版大麦可能用 MainActivity + Fragment 承载详情页，
        # 不能只认 ProjectDetailActivity；下面再用详情页控件确认。
        if "ProjectDetailActivity" not in activity and "MainActivity" not in activity:
            print(f"提示：当前不在演出详情页（{activity or '未知'}），开抢时可能失败")
            return
        if self.config.buy_point:
            match = re.match(r"^\s*(\d+)\s*[,x]\s*(\d+)\s*$", self.config.buy_point)
            if match:
                self._buy_point = (int(match.group(1)), int(match.group(2)))
                print(f"使用配置的底栏坐标 {self._buy_point}（跳过页面读取）")
                return
            print(f"底栏坐标格式不正确（{self.config.buy_point}），改用页面读取")
        root = self._ui_dump()
        if root is None:
            print("提示：未能读取页面结构，开抢时改用默认坐标")
            return
        texts = " ".join(
            (node.get("text") or "") + " " + (node.get("content-desc") or "")
            for node in root.iter()
        )
        if "ProjectDetailActivity" not in activity:
            # MainActivity + Fragment 架构下没有 ProjectDetailActivity，
            # 这时才需要用详情页控件确认；ProjectDetailActivity 本身已足够。
            detail_markers = any(
                "info_v2_title_tv1" in (node.get("resource-id") or "")
                or "tour_city_name_state_desc" in (node.get("resource-id") or "")
                for node in root.iter()
            )
            if not detail_markers:
                print(
                    f"提示：当前页面不是演出详情页（{activity or '未知'}），"
                    "开抢时可能失败"
                )
                return
        if self.config.keyword and self.config.keyword not in texts:
            print(
                f"提示：当前演出页看不到“{self.config.keyword}”，"
                "确认页面已加载且是不是要抢的那一场"
            )
        for node in root.iter():
            if node.get("resource-id") != BUY_BAR_CONTAINER_ID:
                continue
            bounds = parse_bounds(node.get("bounds"))
            if bounds:
                self._buy_point = ((bounds[0] + bounds[2]) // 2,
                                   (bounds[1] + bounds[3]) // 2)
                self._presale_detected = False
                print(f"已定位底栏按钮：{self._buy_point}（开售瞬间直接点）")
                return
        ocr_point = self._locate_buy_point_by_ocr()
        if ocr_point:
            self._buy_point = ocr_point
            self._presale_detected = False
            print(f"OCR 定位到底栏按钮：{ocr_point}（开售瞬间直接点）")
            return
        self._presale_detected = any(
            marker in texts for marker in ("预约", "想看", "开售提醒", "缺货登记")
        )
        if self._presale_detected:
            if not getattr(self.config, "run_now", False):
                print(
                    "提示：当前演出尚未开售（底栏是“预约/想看”）；"
                    "正式模式会等到开售后底栏翻转为“立即购买/立即购票”再点击"
                    "（未定位到可购买按钮时使用默认底栏坐标）"
                )
        else:
            print("提示：未找到底栏容器，开抢时改用默认坐标")

    def _ensure_detail_page(self):
        """详情页会被全屏视频/社区页顶掉，先退回详情页。"""
        for _ in range(5):
            activity = self._foreground_activity()
            if "ProjectDetailActivity" in activity:
                return
            if not activity:
                # 开售瞬间大麦会重建详情页，topResumedActivity 短暂为空：
                # 这里返回一层会直接把 App 退掉，只能等过渡结束再看。
                time.sleep(0.15)
                continue
            print(f"前台是 {activity or '未知'}，先返回一层")
            self._adb("shell", "input", "keyevent", "4")
            time.sleep(1.2)
        activity = self._foreground_activity()
        raise RuntimeError(
            f"前台不是演出详情页（当前 {activity or '未知'}），请先打开要抢的演出页"
        )

    def _buy_bar_point(self):
        """底栏点击位置：优先开售前缓存/配置坐标，否则按比例兜底。"""
        if self._buy_point:
            return self._buy_point
        width, height = self._adb_size()
        return (int(width * 0.65), height - BUY_BAR_TAP_OFFSET)

    def _tap_buy_now(self):
        """普通模式：退回详情页后只做一次 adb 点击。"""
        self._ensure_detail_page()
        point = self._buy_bar_point()
        self._adb_tap(*point)
        print(f"已点击底栏坐标 {point}")

    def _wait_any_activity(self, markers, deadline, since=None):
        """等任一页面首帧；事件感知可用时不再每 20ms 跑一次 dumpsys。

        事件按 0.4 秒切片，每片用一次 dumpsys 兜底：正常路径由事件立即
        唤醒，只有 logcat 丢行时才会走兜底，最多多等 0.4 秒。
        """
        watcher = getattr(self, "_activity_watch", None)
        if watcher and getattr(watcher, "running", True):
            while True:
                now = time.perf_counter()
                if now >= deadline:
                    return ""
                found = watcher.wait_any_displayed(
                    markers, min(0.4, deadline - now), since=since
                )
                if found:
                    return found
                # 0.4 秒一次 dumpsys 兜底：logcat 丢行时最多多等 0.4 秒
                activity = self._foreground_activity()
                for marker in markers:
                    if marker in activity:
                        return marker
                if not getattr(watcher, "running", True):
                    break
        while time.perf_counter() < deadline:
            activity = self._foreground_activity()
            for marker in markers:
                if marker in activity:
                    return marker
            time.sleep(0.02)
        return ""

    def _wait_activity_gone(self, marker, deadline):
        while time.perf_counter() < deadline:
            if marker not in self._foreground_activity():
                return True
            time.sleep(0.05)
        return False

    def _resolve_point(self, raw, ratio):
        """配置里写了 x,y 就用配置，否则按屏幕比例换算。"""
        if raw:
            match = re.match(r"^\s*(\d+)\s*[,x]\s*(\d+)\s*$", raw)
            if match:
                return int(match.group(1)), int(match.group(2))
            print(f"坐标格式不正确（{raw}），改用默认位置")
        width, height = self._adb_size()
        return int(width * ratio[0]), int(height * ratio[1])

    def _wait_activity(self, marker, deadline):
        """等页面首帧；事件感知可用时立即返回，否则退回 dumpsys。"""
        watcher = getattr(self, "_activity_watch", None)
        if watcher and getattr(watcher, "running", True):
            timeout = max(deadline - time.perf_counter(), 0.0)
            if watcher.wait_displayed(marker, timeout):
                return True
            return marker in self._foreground_activity()
        while time.perf_counter() < deadline:
            if marker in self._foreground_activity():
                return True
            time.sleep(0.1)
        return False

    def _mark(self, label):
        """记录并打印相对“放行”时刻的耗时，便于看出瓶颈。"""
        if self._started_at is None:
            return
        elapsed = time.perf_counter() - self._started_at
        getattr(self, "_timings", []).append((label, elapsed))
        print(f"[耗时] {label}：{elapsed:.2f} 秒")

    def _print_timing_summary(self):
        """输出分段耗时表和 [TIMING] JSON，供控制台渲染耗时评估。"""
        timings = getattr(self, "_timings", [])
        if not timings:
            return
        stages = []
        previous = 0.0
        for label, elapsed in timings:
            stages.append(
                {"label": label, "ms": max(0, round((elapsed - previous) * 1000))}
            )
            previous = max(previous, elapsed)
        total_ms = max(0, round(timings[-1][1] * 1000))
        flow = getattr(self, "_flow", "normal")
        flow_label = "极速模式" if flow == "fast" else "普通模式"
        print(f"── 耗时评估（{flow_label}）──")
        for stage in stages:
            print(f"  {stage['label']}：{stage['ms'] / 1000:.3f} 秒")
        print(f"  合计：{total_ms / 1000:.3f} 秒")
        config = getattr(self, "config", None)
        payload = {
            "flow": flow,
            "flow_label": flow_label,
            "total_ms": total_ms,
            "stages": stages,
            "submitted": bool(getattr(self, "_submit_attempted", False)),
            "submit_verified": getattr(self, "_submit_verified", None),
            "dry_run": bool(getattr(config, "dry_run", False)),
            "run_now": bool(getattr(config, "run_now", False)),
            "at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        }
        print("[TIMING] " + json.dumps(payload, ensure_ascii=False))

    # ---------- 屏幕识别：截图 + OCR ----------
    def _screenshot(self, path="/tmp/damai_screen.png"):
        with open(path, "wb") as handle:
            subprocess.run(
                ["adb", "-s", self.config.device_name, "exec-out", "screencap", "-p"],
                stdout=handle, timeout=30,
            )
        return path

    def _ocr(self):
        """返回 [(x, y, 置信度, 文本)]；失败时返回空列表。"""
        try:
            result = subprocess.run(
                [sys.executable, str(OCR_TOOL), self._screenshot()],
                capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"OCR 不可用：{exc}")
            return []
        rows = []
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 4:
                continue
            try:
                rows.append((int(parts[0]), int(parts[1]), float(parts[2]), parts[3]))
            except ValueError:
                continue
        return rows

    def _ocr_texts(self, rows=None):
        return [row[3] for row in (rows if rows is not None else self._ocr())]

    # ---------- 无障碍 dump：读确认页 ----------
    @staticmethod
    def _extract_xml(output):
        """从 uiautomator 输出里截出完整 XML（/dev/tty 前后会有提示行）。"""
        start = output.find("<?xml")
        end = output.find("</hierarchy>")
        if start < 0:
            return ""
        if end < start:
            return output[start:].strip()
        return output[start:end + len("</hierarchy>")]

    def _ui_dump(self):
        """用 adb 抓无障碍树，不经过 Appium。

        先用 ``uiautomator dump --compressed /dev/tty`` 一次往返拿回 XML；
        部分机型不支持 /dev/tty 时才退回“写文件 + cat”两次往返。
        """
        for _ in range(3):
            output = self._adb(
                "shell", "uiautomator", "dump", "--compressed", "/dev/tty",
                timeout=30,
            ).stdout
            xml = self._extract_xml(output)
            if xml.startswith("<?xml"):
                try:
                    return ET.fromstring(xml)
                except ET.ParseError:
                    pass
            self._adb(
                "shell", "uiautomator", "dump", "--compressed",
                "/sdcard/damai_ui.xml", timeout=30,
            )
            output = self._adb(
                "shell", "cat", "/sdcard/damai_ui.xml", timeout=30
            ).stdout
            xml = self._extract_xml(output)
            if xml.startswith("<?xml"):
                try:
                    return ET.fromstring(xml)
                except ET.ParseError:
                    pass
            time.sleep(0.3)
        return None

    def _confirm_snapshot(self):
        """确认页快照：金额、已选观演人数、提交按钮坐标；缺一项就返回 None。"""
        root = self._ui_dump()
        if root is None:
            return None
        amount = None
        amount_y = -1
        selected = 0
        checkbox_count = 0
        submit = None
        boxes, names = [], []
        for node in root.iter():
            text = (node.get("text") or "").strip()
            resource_id = node.get("resource-id") or ""
            bounds = parse_bounds(node.get("bounds"))
            if resource_id == VIEWER_CHECKBOX_ID:
                checkbox_count += 1
                if node.get("checked") == "true":
                    selected += 1
                if bounds:
                    boxes.append((bounds, node.get("checked") == "true"))
            elif bounds and text and ("text_name" in resource_id or not resource_id):
                names.append(((bounds[1] + bounds[3]) // 2, text))
            if bounds and text and ("¥" in text or "￥" in text):
                value = parse_amount(text)
                if value is not None and bounds[1] > amount_y:
                    amount, amount_y = value, bounds[1]
            if bounds and text in SUBMIT_TEXTS:
                submit = (
                    (bounds[0] + bounds[2]) // 2,
                    (bounds[1] + bounds[3]) // 2,
                )
        if amount is None or checkbox_count == 0 or submit is None:
            return None
        viewers = []
        for bounds, checked in boxes:
            center_y = (bounds[1] + bounds[3]) // 2
            near = [t for y, t in names if abs(y - center_y) <= 45]
            viewers.append((bounds, checked, near[0] if near else ""))
        return {
            "amount": amount,
            "selected": selected,
            "checkboxes": checkbox_count,
            "submit": submit,
            "viewers": viewers,
        }

    # ---------- 观演人 ----------
    def _viewer_rows(self):
        """返回 [(bounds, checked, name)]，name 取勾选框同一行的姓名。"""
        root = self._ui_dump()
        if root is None:
            return []
        boxes, names = [], []
        for node in root.iter():
            bounds = parse_bounds(node.get("bounds"))
            if not bounds:
                continue
            resource_id = node.get("resource-id") or ""
            text = (node.get("text") or "").strip()
            if resource_id == VIEWER_CHECKBOX_ID:
                boxes.append((bounds, node.get("checked") == "true"))
            elif text and ("text_name" in resource_id or not resource_id):
                names.append(((bounds[1] + bounds[3]) // 2, text))
        rows = []
        for bounds, checked in boxes:
            center_y = (bounds[1] + bounds[3]) // 2
            nearby = [t for y, t in names if abs(y - center_y) <= 45]
            rows.append((bounds, checked, nearby[0] if nearby else ""))
        return rows

    def _select_viewers(self, viewers, ticket_count):
        """按配置姓名优先勾选；名字对不上时退回勾第一个未选的。"""
        if not viewers:
            return False
        wanted = [u.strip() for u in (self.config.users or []) if u.strip()]
        selected = sum(1 for _b, checked, _n in viewers if checked)
        if selected >= ticket_count:
            return True
        unchecked = [v for v in viewers if not v[1]]

        def matched(row):
            name = row[2]
            return not wanted or not name or any(w in name or name in w for w in wanted)

        ordered = [v for v in unchecked if matched(v)] + [v for v in unchecked if not matched(v)]
        for bounds, _checked, _name in ordered:
            if selected >= ticket_count:
                break
            self._adb_tap((bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2)
            time.sleep(0.35)
            selected += 1
        return selected >= ticket_count

    def _ensure_viewers(self, page):
        # 没配观演人 = 相信大麦已预选，直接进提交环节
        if not self.config.auto_select or not (self.config.users or []):
            return page
        if page["selected"] >= self.config.ticket_count:
            return page
        print(f"自动勾选观演人（当前已选 {page['selected']}/{self.config.ticket_count} 位）")
        if self._select_viewers(page["viewers"], self.config.ticket_count):
            self._mark("勾选观演人")
            refreshed = self._confirm_snapshot()
            if refreshed:
                return refreshed
        return page

    # ---------- 选票档页 ----------
    def _on_sku_page(self, texts):
        return all(marker in texts for marker in SKU_MARKERS)

    def _choose_price(self, rows=None):
        """OCR 找票档卡片：配置了票档就必须命中，缺货或不在列表都直接结束。

        缺货标签按“同一行内最近的票档”配对——大麦票档是双列布局，
        只看纵向距离会把同行右侧的可用票档误判成缺货。
        """
        rows = rows if rows is not None else self._ocr()
        soldout_marks = [(x, y) for x, y, _c, text in rows if "缺货" in text]
        options = []
        for x, y, _c, text in rows:
            if "元" in text and any(ch.isdigit() for ch in text):
                options.append((text, (x, y)))
        # 每个“缺货”标签配对同一行内横向最近的票档
        soldout_points = set()
        for mark_x, mark_y in soldout_marks:
            same_row = [
                (abs(mark_x - x), index)
                for index, (_text, (x, y)) in enumerate(options)
                if abs(mark_y - y) < 60
            ]
            if same_row:
                _dist, nearest = min(same_row)
                soldout_points.add(options[nearest][1])
        options = [
            (text, point, point in soldout_points) for text, point in options
        ]
        if not options:
            raise TicketUnavailableError("选票档页上没有识别到任何票档（OCR 结果为空）")
        wanted = (self.config.price or "").strip()
        if wanted:
            hit = next((o for o in options if o[0] == wanted), None)
            if hit is None:
                seen = "、".join(o[0] for o in options)
                raise TicketUnavailableError(
                    f"配置的票档「{wanted}」不在当前列表（页面识别到：{seen}）"
                )
            if hit[2]:
                raise TicketUnavailableError(f"配置的票档「{wanted}」已显示缺货")
            pick = hit
        else:
            available = [o for o in options if not o[2]]
            if not available:
                raise TicketUnavailableError("所有票档都已缺货登记")
            pick = available[0]
        print(f"选择票档：{pick[0]}，点击 {pick[1]}")
        self._adb_tap(*pick[1])
        self._mark("识别并选择票档")
        return True

    def _tap_sku_confirm(self, rows=None):
        """“确定”是自绘控件：优先 OCR 定位，失败退回右下角坐标。"""
        rows = rows if rows is not None else self._ocr()
        target = next(((x, y) for x, y, _c, text in rows if text.strip() == "确定"), None)
        if target is None:
            width, height = self._adb_size()
            target = (int(width * 0.79), height - 87)
            print(f"OCR 未找到“确定”，退回坐标 {target}")
        else:
            print(f"点击“确定” {target}")
        self._adb_tap(*target)
        self._mark("点击确定")
        return True

    def _wait_sku_page(self, deadline):
        """等选票档页；返回该页的 OCR 结果，超时返回 None。"""
        while time.perf_counter() < deadline:
            if "DmOrderActivity" in self._foreground_activity():
                return None
            rows = self._ocr()
            texts = self._ocr_texts(rows)
            joined = "".join(texts)
            if any(marker in joined for marker in NETWORK_ERROR_MARKERS):
                raise RuntimeError(
                    "选票档页加载失败（大麦提示网络不顺畅）：手机没有可用网络，请先恢复联网"
                )
            if self._on_sku_page(texts):
                return rows
            time.sleep(0.2)
        return None

    def _wait_confirmation(self):
        """预选好的场次会一步进确认页；否则走选票档页兜底。"""
        deadline = time.perf_counter() + CONFIRM_TIMEOUT
        # 同时等确认页和票档页：票档页先出现就立刻走 OCR 兜底，
        # 不再固定等满 1.2 秒。
        first = self._wait_any_activity(
            ("DmOrderActivity", "NcovSkuActivity"),
            min(time.perf_counter() + 1.2, deadline),
        )
        if first != "DmOrderActivity":
            rows = self._wait_sku_page(min(time.perf_counter() + SKU_TIMEOUT, deadline))
            if rows is not None:
                self._mark("票档页可读")
                if self.config.auto_select:
                    self._choose_price(rows)
                    time.sleep(0.4)
                    self._mark("选择票档")
                self._tap_sku_confirm(rows)
            self._wait_activity("DmOrderActivity", deadline)
        else:
            self._mark("确认页出现")
        while time.perf_counter() < deadline:
            page = self._confirm_snapshot()
            if page:
                self._mark("确认页可读")
                return self._ensure_viewers(page)
            time.sleep(0.1)
        raise TimeoutError("订单确认页未出现")

    @staticmethod
    def _validate_confirmation(page, ticket_count):
        if page["selected"] != ticket_count:
            raise ConfirmationError(
                f"预约观演人数不符：需要 {ticket_count} 位，"
                f"实际已选 {page['selected']} 位"
            )
        return page["amount"], page["selected"]

    # ---------- 主流程 ----------
    def _wait_for_sale(self):
        return wait_for_sale_time(self.config.sell_start_time)

    def _align_clock_with_phone(self):
        """把放行目标对齐到手机时钟（大麦倒计时以手机本地时钟渲染）。

        偏差 = 本机 - 对方（正数 = 本机偏快）；手机读不到时降级为
        NTP 服务器偏差（手机与服务器通常都接近标准时间，精度较差）。
        """
        offset = None
        source = ""
        try:
            # 不能走常驻 adb 会话：它会用 shlex 把 $EPOCHREALTIME 加上单引号，
            # 设备端只回显字面量，读不到真实时钟。
            from time_sync import measure_phone_offset

            offset = measure_phone_offset(self.config.device_name)
            source = "手机时钟"
        except Exception as exc:  # noqa: BLE001 - 读不到就降级，不阻断抢票
            print(f"手机时钟读取失败（{exc}），改用 NTP 对齐")
            try:
                from time_sync import measure_server_offset

                server_offset, _detail = measure_server_offset()
                if server_offset is not None:
                    offset = server_offset
                    source = "NTP 服务器（精度降级）"
            except Exception as exc:  # noqa: BLE001 - 对时失败不阻断抢票
                print(f"NTP 对齐也不可用：{exc}")
        if offset is None:
            print("时钟对齐跳过：手机与 NTP 均不可用，按本机时间放行")
            return
        if abs(offset) < 0.002:
            print(f"本机与对方时钟偏差 {offset * 1000:+.1f} 毫秒，无需修正")
            return
        adjusted = parse_sale_time(self.config.sell_start_time) + timedelta(seconds=offset)
        self.config.sell_start_time = adjusted.isoformat()
        print(
            f"以{source}为准：本机{'快' if offset > 0 else '慢'} "
            f"{abs(offset) * 1000:.1f} 毫秒，"
            f"放行时刻已对齐：{self.config.sell_start_time}"
        )

    # ---------- 开抢前：按钮翻转感知 ----------
    FLIP_WATCH_FROM = 30.0
    # 到点仍未匹配到正面模板时继续感知；超时停止，禁止 OCR 或纯定时盲点
    FLIP_POST_SALE_TIMEOUT = 15.0

    @staticmethod
    def _wait_until_target(target, attempts=3):
        """等到目标墙上时间；补睡几次防止系统 sleep 提前返回。"""
        for _ in range(attempts):
            remaining = (target - datetime.now(SHANGHAI)).total_seconds()
            if remaining <= 0:
                return
            time.sleep(remaining)

    def _wait_for_sale_with_flip_watch(self):
        """只在 Scrcpy 画面确认底栏翻转后放行：模板命中，或开售后稳定翻转时补采。"""
        target = parse_sale_time(self.config.sell_start_time)
        remaining = (target - datetime.now(SHANGHAI)).total_seconds()
        if remaining <= 0:
            raise SaleTimeError("抢购时间已过，未执行购买")
        if remaining > self.FLIP_WATCH_FROM:
            time.sleep(remaining - self.FLIP_WATCH_FROM)

        width, height = self._adb_size()
        try:
            from flip_watch import make_scrcpy_watcher

            watch_name, watch = make_scrcpy_watcher(
                self.config.device_name,
                focus_point=self._buy_point,
                screen_size=(width, height),
                template_dir=getattr(self.config, "flip_template_dir", "") or None,
            )
        except Exception as exc:
            raise SaleTimeError(f"Scrcpy 模板感知不可用：{exc}；未点击") from exc

        print(f"按钮翻转感知：{watch_name}")
        remaining = (target - datetime.now(SHANGHAI)).total_seconds()
        try:
            flipped = watch(
                max(remaining, 0.0),
                post_sale_timeout=self.FLIP_POST_SALE_TIMEOUT,
            )
        except Exception as exc:
            raise SaleTimeError(f"按钮模板感知异常：{exc}；未点击") from exc
        if not flipped:
            raise SaleTimeError("等待可购买按钮模板超时，已停止且未点击")

        released_at = datetime.now(SHANGHAI)
        remaining = (target - released_at).total_seconds()
        if remaining > 0:
            # 模板可能比真实开售早命中（倒计时结束前的刷新、模板本身是未开售画面）。
            # 大麦在开售时刻前会忽略底栏点击，此时点下去只是白点一次，
            # 还会让“是否翻页”的等待窗口白白吃掉几百毫秒，所以等到点再点。
            print(
                f"模板命中但比开售时刻早 {remaining:.3f} 秒："
                "等到开售时刻再点（开售前的点击会被大麦忽略）"
            )
            self._wait_until_target(target)
            released_at = datetime.now(SHANGHAI)
            remaining = (target - released_at).total_seconds()
        released_monotonic = time.monotonic()
        if remaining >= 0:
            print("感知到底栏可购买状态，按开售时刻准点放行")
        else:
            print(f"感知到底栏可购买状态，开售后 {-remaining:.3f} 秒放行")
        return SaleRelease(
            target_time=target,
            released_at=released_at,
            target_monotonic=released_monotonic + remaining,
            released_monotonic=released_monotonic,
        )

    def _tap_buy_bar_until_next_page(self, point):
        """正面确认可购买后点击底栏，直到离开详情页。

        重试覆盖点击后页面未响应；若仍误入“已预约”等页面，先退回详情页。
        返回翻页后的 Activity 名。
        """
        deadline = time.perf_counter() + BUY_RETRY_TIMEOUT
        attempts = 0
        targets = ("NcovSkuActivity", "DmOrderActivity")
        while time.perf_counter() < deadline:
            attempts += 1
            tap_at = time.perf_counter()
            self._adb_tap(*point)
            if attempts == 1:
                print(f"已点击底栏坐标 {point}")
                self._mark("点击底栏完成")
            else:
                print(f"底栏未翻页，第 {attempts} 次点击")
            wait_until = time.perf_counter() + (
                BUY_FIRST_WAIT if attempts == 1 else BUY_RETRY_WAIT
            )
            empty_seen = False
            while True:
                # 先等 logcat 的 START 事件；不再每 20ms 跑一次 dumpsys
                remaining = max(wait_until - time.perf_counter(), 0.0)
                found = self._wait_started(targets, remaining, since=tap_at)
                if found:
                    elapsed_ms = (time.perf_counter() - tap_at) * 1000
                    if attempts > 1:
                        print(
                            f"底栏点击 {attempts} 次后翻页"
                            f"（末次点击后 {elapsed_ms:.0f} 毫秒）"
                        )
                    else:
                        print(f"底栏点击后 {elapsed_ms:.0f} 毫秒翻页")
                    return found
                # 事件没来：只做一次 dumpsys 兜底，避免 logcat 丢行误判
                activity = self._foreground_activity()
                for marker in targets:
                    if marker in activity:
                        elapsed_ms = (time.perf_counter() - tap_at) * 1000
                        if attempts > 1:
                            print(
                                f"底栏点击 {attempts} 次后翻页"
                                f"（末次点击后 {elapsed_ms:.0f} 毫秒）"
                            )
                        else:
                            print(f"底栏点击后 {elapsed_ms:.0f} 毫秒翻页")
                        return marker
                if not activity:
                    # 页面切换瞬间 topResumedActivity 可能为空：
                    # 只等待过渡，不点击、不返回。
                    if not empty_seen:
                        print(
                            "前台 Activity 暂空（点击后 "
                            f"{(time.perf_counter() - tap_at) * 1000:.0f} 毫秒），"
                            "等待页面过渡（不返回、不点击）"
                        )
                        empty_seen = True
                    wait_until = min(
                        deadline, max(wait_until, time.perf_counter() + 0.3)
                    )
                    if time.perf_counter() < deadline:
                        continue
                    break
                empty_seen = False
                if "ProjectDetailActivity" not in activity:
                    print(f"前台是 {activity}，先返回详情页")
                    self._adb("shell", "input", "keyevent", "4")
                    time.sleep(0.4)
                    break
                # 还在详情页：走外层重试点击
                break
        raise TimeoutError(
            f"点击底栏 {attempts} 次仍未进入下单流程"
            "（大麦按钮可能延迟翻转，或场次已售罄）"
        )

    def _activity_drawn(self, marker):
        """前台是该页且已经画出首帧（reportedDrawn/nowVisible）。

        比 u2 截图稳定判定便宜一个量级（一次 dumpsys 约 40ms），
        而且“首帧已绘制”正好是能安全盲点坐标的时刻。
        """
        output = self._adb("shell", "dumpsys", "activity", "activities").stdout
        match = re.search(r"topResumedActivity=.*?/([\w.$]+)", output)
        if not match or marker not in match.group(1):
            return False
        blocks = output[match.end():].split("* Hist")
        block = blocks[1] if len(blocks) > 1 else blocks[0]
        return "reportedDrawn=true" in block or "nowVisible=true" in block

    def _wait_drawn(self, marker, label, timeout=1.2):
        """等页面首帧绘制完成；事件感知可用时不再轮询 dumpsys。"""
        started = time.perf_counter()
        watcher = getattr(self, "_activity_watch", None)
        if watcher and getattr(watcher, "running", True):
            if watcher.seen_displayed(marker):
                print(f"{label}首帧已绘制（事件已到），开始点击")
                return True
            if watcher.wait_displayed(marker, timeout):
                elapsed_ms = (time.perf_counter() - started) * 1000
                print(f"{label}首帧已绘制（事件感知 {elapsed_ms:.0f} 毫秒），开始点击")
                return True
            # logcat 可能丢行：超时后只做一次 dumpsys 兜底
            if self._activity_drawn(marker):
                elapsed_ms = (time.perf_counter() - started) * 1000
                print(
                    f"{label}首帧已绘制（dumpsys 兜底 {elapsed_ms:.0f} 毫秒），"
                    "开始点击"
                )
                return True
            print(f"{label}首帧等待超时，按固定余量继续点击")
            time.sleep(0.1)
            return False
        while time.perf_counter() - started < timeout:
            if self._activity_drawn(marker):
                elapsed_ms = (time.perf_counter() - started) * 1000
                print(f"{label}首帧已绘制（等待 {elapsed_ms:.0f} 毫秒），开始点击")
                return True
            time.sleep(0.03)
        print(f"{label}首帧等待超时，按固定余量继续点击")
        time.sleep(0.1)
        return False

    def _run_fast(self):
        """预约模式：底栏 → 确定（首次 + 补点一次）→ 立即提交。

        预约模式不自动选票档、不做 OCR；票档应由大麦预约带出。
        普通模式才用 OCR 选票档。确认页出现后只提交一次。
        """
        print("极速模式：按坐标直点（不 dump、不 OCR）")
        # 翻转感知刚在详情页命中底栏按钮，这里不再 dump 一次，放行后直接发点击。
        marker = self._tap_buy_bar_until_next_page(self._buy_bar_point())
        if "NcovSkuActivity" in marker:
            self._wait_drawn("NcovSkuActivity", "票档页")
            self._mark("票档页可点")
            point = self._resolve_point(self.config.confirm_point, CONFIRM_POINT_RATIO)
            confirm_tap_at = time.perf_counter()
            self._adb_tap(*point)
            print(f"点击“确定” {point}")
            self._mark("点击确定")
            # 预约模式：确定首次 + 补点一次，不做 OCR 自动选票档。
            marker = self._wait_any_activity(
                ("DmOrderActivity",), time.perf_counter() + 2.0,
                since=confirm_tap_at,
            )
            if not marker:
                activity = self._foreground_activity()
                if "DmOrderActivity" in activity:
                    marker = "DmOrderActivity"
                elif "NcovSkuActivity" in activity:
                    # 只有还停在票档页才补点；确认页或页面切换中绝不能再点。
                    retry_at = time.perf_counter()
                    self._adb_tap(*point)
                    print(f"票档页仍在，补点“确定” {point}")
                    self._mark("补点确定")
                    marker = self._wait_any_activity(
                        ("DmOrderActivity",), time.perf_counter() + 1.0,
                        since=retry_at,
                    )
                else:
                    # 页面切换中（topResumedActivity 为空或未知）：只等，不点击
                    marker = self._wait_any_activity(
                        ("DmOrderActivity",), time.perf_counter() + 0.8,
                        since=confirm_tap_at,
                    )
            if not marker:
                raise RuntimeError(
                    "“确定”已点一次并补点一次，仍未进入确认页："
                    "请确认大麦预约已预选场次/票档/观演人，"
                    "或核对「确定」坐标配置；预约模式不自动选票档，直接结束"
                )
        if marker != "DmOrderActivity":
            raise TimeoutError("没有进入订单确认页")
        if getattr(self.config, "dry_run", False):
            print("演练模式：已到确认页，不点击“立即提交”")
            return True
        if not self.config.if_commit_order:
            print("已到确认页（极速模式不读页面）；自动提交已关闭")
            return True
        self._wait_drawn("DmOrderActivity", "确认页")
        self._mark("确认页可点")
        settle = getattr(self.config, "submit_settle_seconds", SUBMIT_SETTLE_SECONDS)
        if settle and settle > 0:
            time.sleep(settle)
            self._mark("确认页稳定")
        submit = self._resolve_point(self.config.submit_point, SUBMIT_POINT_RATIO)
        self._submit_attempted = True
        self._adb_tap(*submit)
        print(f"点击“立即提交” {submit}")
        self._mark("点击立即提交")
        # 只提交一次，不补点；提交后做一次轻量校验并明确反馈结果
        left = self._wait_activity_gone(
            "DmOrderActivity", time.perf_counter() + SUBMIT_VERIFY_TIMEOUT
        )
        self._submit_verified = left
        if left:
            print("提交后已离开确认页，请在大麦 App 订单记录确认")
        else:
            print(
                "警告：提交后仍停在确认页，点击可能未生效；"
                "不再补点，请检查「立即提交」坐标或按钮是否可点"
            )
            # 只在失败后读一次页面，给出具体原因；不影响正常提交速度
            page = self._confirm_snapshot()
            if page:
                print(
                    f"诊断：确认页已选观演人 {page['selected']}/"
                    f"{self.config.ticket_count}，金额 {page['amount']:g} 元；"
                    f"页面「立即提交」中心 {page['submit']}，本次点击 {submit}"
                )
                if page["selected"] != self.config.ticket_count:
                    print(
                        "诊断原因：观演人未选或数量不符，提交可能被大麦拒绝；"
                        "请先在预约中选好观演人"
                    )
                elif page["submit"] != submit:
                    print(
                        "诊断原因：提交按钮实际坐标与本次点击不一致；"
                        "建议在确认页采集一次提交坐标"
                    )
            else:
                print(
                    "诊断：无法读取确认页，可能按钮未启用、页面有弹窗"
                    "或订单数据尚未加载"
                )
        print(f"提交完成，全链路耗时：{time.perf_counter() - self._started_at:.3f} 秒")
        return True

    def run_ticket_grabbing(self):
        """执行一次预约快路；任何结果不明确都停止，不自动重试。"""
        try:
            if self._purchase_attempted:
                print("本次会话已尝试购买，停止重复操作")
                return False
            run_now = bool(getattr(self.config, "run_now", False))
            if run_now:
                print("立即演练：跳过倒计时和定时等待")
            else:
                print(f"等待程序定时开抢：{self.config.sell_start_time}")
            if getattr(self.config, "dry_run", False):
                print("演练模式：只到订单确认页，绝不点击“立即提交”")
            print(
                f"配置：购票数量 {getattr(self.config, 'ticket_count', '?')} 张；"
                f"极速模式 {'开' if self.config.fast_mode else '关'}"
            )
            self._start_activity_watch()
            if run_now:
                activity = self._foreground_activity()
                # 新版大麦可能用 MainActivity + Fragment 承载详情页
                if (
                    "ProjectDetailActivity" not in activity
                    and "MainActivity" not in activity
                ):
                    print(
                        "立即演练：当前不在演出详情页"
                        f"（{activity or '未知'}），请先打开一个正在售票的演出"
                    )
                    return False
                print("立即演练：跳过倒计时，直接从当前详情页执行到确认页")
            self._prepare_buy_point()
            if run_now and getattr(self, "_presale_detected", False):
                print(
                    "立即演练：当前演出尚未开售，请打开一个正在售票的演出详情页"
                )
                return False
            if run_now and getattr(self, "_buy_point", None) is None:
                print(
                    "立即演练：没有定位到“立即购票”按钮，"
                    "请确认当前是正在售票的演出详情页"
                )
                return False
            if run_now:
                print("立即演练：开始点击底栏")
            else:
                self._align_clock_with_phone()
                if self.config.fast_mode:
                    release = self._wait_for_sale_with_flip_watch()
                else:
                    release = self._wait_for_sale()
                print(
                    f"定时放行：{release.released_at.isoformat()}；"
                    f"调度偏差：{release.delay_ms:.3f} 毫秒"
                )
            self._started_at = time.perf_counter()
            self._timings = []
            self._flow = "fast" if self.config.fast_mode else "normal"
            self._purchase_attempted = True
            if self.config.fast_mode:
                return self._run_fast()
            print("预约快路：点击立即购买...")
            self._tap_buy_now()
            self._mark("点击底栏完成")

            print("等待订单确认页并核对预约结果...")
            page = self._wait_confirmation()
            self._mark("确认页读取完成")
            amount, selected = self._validate_confirmation(
                page, self.config.ticket_count
            )
            print(f"确认页金额：{amount:g} 元；已选观演人：{selected} 位")

            if getattr(self.config, "dry_run", False):
                print("演练模式：已到确认页，不点击“立即提交”")
                return True
            if not self.config.if_commit_order:
                print("预约信息核对通过，自动提交已关闭")
                return True
            if self._submit_attempted:
                print("本次会话已尝试提交，禁止重复提交")
                return False

            self._submit_attempted = True
            print("提交订单（仅一次）...")
            self._adb_tap(*page["submit"])
            self._mark("点击立即提交")
            print(
                "提交指令已发出，请在大麦 App 确认订单；"
                f"耗时：{time.perf_counter() - self._started_at:.3f} 秒"
            )
            return True
        except TimeoutError:
            print("预约快路超时：未找到立即购买或完整订单确认页，已停止")
            return False
        except TicketUnavailableError as exc:
            print(f"票档不可用，直接结束，不再尝试下单：{exc}")
            return False
        except ConfirmationError as exc:
            print(f"{exc}，禁止提交")
            return False
        except SaleTimeError as exc:
            print(f"定时抢购未执行：{exc}")
            return False
        except Exception as exc:
            print(f"预约快路中止：{exc}；不会自动重试或重复提交")
            return False
        finally:
            self._print_timing_summary()
            self._stop_activity_watch()

    def run_with_retry(self, max_retries=1):
        """兼容旧入口；预约快路始终只执行一次。"""
        return self.run_ticket_grabbing()


if __name__ == "__main__":
    bot = DamaiBot()
    bot.run_with_retry()
