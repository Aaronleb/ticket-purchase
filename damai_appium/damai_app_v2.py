# -*- coding: UTF-8 -*-
"""大麦 App 原生预约快路。

整条链路不创建 WebDriver 会话：开售瞬间用 adb 点击，票档用本地 OCR 定位，
确认页用 `uiautomator dump` 读取。实测建一次 Appium 会话要 7~9 秒，而建会话
本身与抢票无关，所以这里把它彻底移出关键路径。
"""
from __future__ import annotations

import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    from .config import Config
    from .sale_clock import SaleTimeError, wait_for_sale_time
except ImportError:
    from config import Config
    from sale_clock import SaleTimeError, wait_for_sale_time


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
SKU_MARKERS = ("场次", "票档")
NETWORK_ERROR_MARKERS = ("网络不太顺畅", "努力刷新")
VIEWER_CHECKBOX_ID = "cn.damai:id/checkbox"
# 极速模式的兜底坐标（按 1080x2412 实测，换算成比例以适配分辨率）
CONFIRM_POINT_RATIO = (0.781, 0.960)
SUBMIT_POINT_RATIO = (0.730, 0.961)


class ConfirmationError(RuntimeError):
    """订单确认页不符合提交条件。"""


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
        self._started_at = None
        # 开售前 dump 一次底栏位置并缓存，放行瞬间只剩 1 次 adb tap
        self._buy_point = None

    # ---------- 底层：adb ----------
    def _adb(self, *args, timeout=10):
        return subprocess.run(
            ["adb", "-s", self.config.device_name, *args],
            capture_output=True, text=True, timeout=timeout,
        )

    def _adb_size(self):
        match = re.search(r"(\d+)x(\d+)", self._adb("shell", "wm", "size").stdout)
        return (int(match.group(1)), int(match.group(2))) if match else (1080, 2412)

    def _adb_tap(self, x, y):
        self._adb("shell", "input", "tap", str(int(x)), str(int(y)))

    def _foreground_activity(self):
        output = self._adb("shell", "dumpsys", "activity", "activities").stdout
        match = re.search(r"topResumedActivity=.*?/([\w.$]+)", output)
        return match.group(1) if match else ""

    def _prepare_buy_point(self):
        """开售前定位底栏按钮，避免放行瞬间还要 dump 一次。"""
        activity = self._foreground_activity()
        if "ProjectDetailActivity" not in activity:
            print(f"提示：当前不在演出详情页（{activity or '未知'}），开抢时可能失败")
            return
        root = self._ui_dump()
        if root is None:
            print("提示：未能读取页面结构，开抢时改用默认坐标")
            return
        if self.config.keyword:
            texts = " ".join(
                (node.get("text") or "") for node in root.iter()
            )
            if self.config.keyword not in texts:
                print(
                    f"提示：当前演出页看不到“{self.config.keyword}”，"
                    "确认是不是要抢的那一场"
                )
        for node in root.iter():
            if node.get("resource-id") != BUY_BAR_CONTAINER_ID:
                continue
            bounds = parse_bounds(node.get("bounds"))
            if bounds:
                self._buy_point = ((bounds[0] + bounds[2]) // 2,
                                   (bounds[1] + bounds[3]) // 2)
                print(f"已定位底栏按钮：{self._buy_point}（开售瞬间直接点）")
                return
        print("提示：未找到底栏容器，开抢时改用默认坐标")

    def _tap_buy_now(self):
        """开售瞬间只做一次 adb 点击：不建会话、不查控件。"""
        # 详情页会被全屏视频/社区页顶掉，先退回详情页再点
        for _ in range(3):
            activity = self._foreground_activity()
            if "ProjectDetailActivity" in activity:
                break
            print(f"前台是 {activity or '未知'}，先返回一层")
            self._adb("input", "keyevent", "4")
            time.sleep(1.2)
        else:
            activity = self._foreground_activity()
            raise RuntimeError(
                f"前台不是演出详情页（当前 {activity or '未知'}），请先打开要抢的演出页"
            )
        if self._buy_point:
            point = self._buy_point
        else:
            width, height = self._adb_size()
            point = (int(width * 0.65), height - BUY_BAR_TAP_OFFSET)
        self._adb_tap(*point)
        print(f"已点击底栏坐标 {point}")

    def _wait_any_activity(self, markers, deadline):
        while time.perf_counter() < deadline:
            activity = self._foreground_activity()
            for marker in markers:
                if marker in activity:
                    return marker
            time.sleep(0.05)
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

    def _report_screen(self, label):
        """提交后取一张图 OCR 一下，只作记录，不影响速度。"""
        try:
            self._screenshot("/tmp/damai_after.png")
            rows = self._ocr()
            texts = [text for _x, _y, _c, text in rows][:8]
            print(f"{label}：{' / '.join(texts)}")
        except Exception as exc:
            print(f"{label}：截图识别失败（{exc}）")

    def _wait_activity(self, marker, deadline):
        while time.perf_counter() < deadline:
            if marker in self._foreground_activity():
                return True
            time.sleep(0.1)
        return False

    def _mark(self, label):
        """打印相对“放行”时刻的耗时，便于看出瓶颈。"""
        if self._started_at is None:
            return
        print(f"[耗时] {label}：{time.perf_counter() - self._started_at:.2f} 秒")

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
                [str(OCR_TOOL), self._screenshot()],
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
    def _ui_dump(self):
        """用 adb 抓无障碍树，不经过 Appium。"""
        for _ in range(3):
            self._adb("shell", "uiautomator", "dump", "/sdcard/damai_ui.xml", timeout=30)
            output = self._adb("shell", "cat", "/sdcard/damai_ui.xml", timeout=30).stdout
            xml = output.strip()
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
            refreshed = self._confirm_snapshot()
            if refreshed:
                return refreshed
        return page

    # ---------- 选票档页 ----------
    def _on_sku_page(self, texts):
        return all(marker in texts for marker in SKU_MARKERS)

    def _choose_price(self, rows=None):
        """OCR 找票档卡片：优先配置票档，其次第一个未缺货的。"""
        rows = rows if rows is not None else self._ocr()
        soldout_ys = [y for _x, y, _c, text in rows if "缺货" in text]
        options = []
        for x, y, _c, text in rows:
            if not text.endswith("元"):
                continue
            soldout = any(abs(y - soldout_y) < 60 for soldout_y in soldout_ys)
            options.append((text, (x, y), soldout))
        if not options:
            print("OCR 未识别到票档")
            return False
        wanted = (self.config.price or "").strip()
        pick = next((o for o in options if not o[2] and o[0] == wanted), None)
        if pick is None:
            available = [o for o in options if not o[2]]
            pick = available[0] if available else None
        if pick is None:
            print("所有票档都已缺货登记，停止")
            return False
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
        fast = self._wait_activity(
            "DmOrderActivity", min(time.perf_counter() + 1.2, deadline)
        )
        if not fast:
            rows = self._wait_sku_page(min(time.perf_counter() + SKU_TIMEOUT, deadline))
            if rows is not None:
                if self.config.auto_select and self._choose_price(rows):
                    time.sleep(0.4)
                self._tap_sku_confirm(rows)
            self._wait_activity("DmOrderActivity", deadline)
        while time.perf_counter() < deadline:
            page = self._confirm_snapshot()
            if page:
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

    def _run_fast(self):
        """极速模式：底栏 → 确定 → 立即提交，全程只发点击，不读页面。"""
        print("极速模式：按坐标直点（不 dump、不 OCR）")
        self._tap_buy_now()
        self._mark("点击底栏完成")
        marker = self._wait_any_activity(
            ("NcovSkuActivity", "DmOrderActivity"), time.perf_counter() + 3.0
        )
        if not marker:
            raise TimeoutError("点击底栏后没有离开详情页")
        if marker == "NcovSkuActivity":
            time.sleep(0.45)
            point = self._resolve_point(self.config.confirm_point, CONFIRM_POINT_RATIO)
            self._adb_tap(*point)
            print(f"点击“确定” {point}")
            self._mark("点击确定")
            marker = self._wait_any_activity(("DmOrderActivity",), time.perf_counter() + 3.0)
            if not marker:
                print("“确定”没生效（票档可能没预选），回退到 OCR 选票档")
                rows = self._ocr()
                if self._on_sku_page(self._ocr_texts(rows)):
                    if self.config.auto_select and self._choose_price(rows):
                        time.sleep(0.4)
                    self._tap_sku_confirm(rows)
                    marker = self._wait_any_activity(
                        ("DmOrderActivity",), time.perf_counter() + 4.0
                    )
        if marker != "DmOrderActivity":
            raise TimeoutError("没有进入订单确认页")
        if not self.config.if_commit_order:
            print("已到确认页（极速模式不读页面）；自动提交已关闭")
            return True
        time.sleep(0.6)
        submit = self._resolve_point(self.config.submit_point, SUBMIT_POINT_RATIO)
        for attempt in range(2):
            self._submit_attempted = True
            self._adb_tap(*submit)
            print(f"点击“立即提交”（第 {attempt + 1} 次）{submit}")
            self._mark("点击立即提交")
            if self._wait_activity_gone("DmOrderActivity", time.perf_counter() + 2.0):
                break
        print(f"提交完成，全链路耗时：{time.perf_counter() - self._started_at:.2f} 秒")
        self._report_screen("提交后")
        return True

    def run_ticket_grabbing(self):
        """执行一次预约快路；任何结果不明确都停止，不自动重试。"""
        try:
            if self._purchase_attempted:
                print("本次会话已尝试购买，停止重复操作")
                return False
            print(f"等待程序定时开抢：{self.config.sell_start_time}")
            self._prepare_buy_point()
            release = self._wait_for_sale()
            print(
                f"定时放行：{release.released_at.isoformat()}；"
                f"调度偏差：{release.delay_ms:.3f} 毫秒"
            )
            self._started_at = time.perf_counter()
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

            if not self.config.if_commit_order:
                print("预约信息核对通过，自动提交已关闭")
                return True
            if self._submit_attempted:
                print("本次会话已尝试提交，禁止重复提交")
                return False

            self._submit_attempted = True
            print("提交订单（仅一次）...")
            self._adb_tap(*page["submit"])
            print(
                "提交指令已发出，请在大麦 App 确认订单；"
                f"耗时：{time.perf_counter() - self._started_at:.3f} 秒"
            )
            return True
        except TimeoutError:
            print("预约快路超时：未找到立即购买或完整订单确认页，已停止")
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

    def run_with_retry(self, max_retries=1):
        """兼容旧入口；预约快路始终只执行一次。"""
        return self.run_ticket_grabbing()


if __name__ == "__main__":
    bot = DamaiBot()
    bot.run_with_retry()
