# -*- coding: UTF-8 -*-
"""
__Author__ = "BlueCestbon"
__Version__ = "2.0.0"
__Description__ = "大麦app抢票自动化 - 优化版"
__Created__ = 2025/09/13 19:27
"""

import os
import re
import time
from datetime import datetime

from appium import webdriver
from appium.options.common.base import AppiumOptions
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException


PURCHASE_WORDS = ("购买", "抢购", "预订", "抢票")
UNAVAILABLE_WORDS = ("预约", "缺货", "售罄", "无票", "缺票")
PURCHASE_SELECTORS = (
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*购买.*|.*抢购.*|.*预订.*|.*抢票.*")'),
    (By.XPATH, '//*[contains(@text,"购买") or contains(@text,"抢购") or contains(@text,"预订") or contains(@text,"抢票")]'),
)
SALE_TIME_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

try:
    from .config import Config
    from .fast_path import (
        FastPathResult,
        is_usable,
        load_snapshot,
        select_ticket_by_coords,
        tap_at,
        tap_viewers_by_coords,
    )
    from .layout_cache import LAYOUT_CACHE_NAME, LayoutCacheError
    from .perf_probe import PerfRecorder, install as install_perf_probe
    from .ticket_selection import TicketSelectionError, load_prepared_tickets, select_prepared_ticket
except ImportError:
    from config import Config
    from fast_path import (
        FastPathResult,
        is_usable,
        load_snapshot,
        select_ticket_by_coords,
        tap_at,
        tap_viewers_by_coords,
    )
    from layout_cache import LAYOUT_CACHE_NAME, LayoutCacheError
    from perf_probe import PerfRecorder, install as install_perf_probe
    from ticket_selection import TicketSelectionError, load_prepared_tickets, select_prepared_ticket


def validate_sale_time(sale_time):
    if not sale_time:
        return ""
    if not isinstance(sale_time, str) or SALE_TIME_PATTERN.fullmatch(sale_time.strip()) is None:
        raise ValueError("开售时间必须是不带时区的本机 ISO 日期时间，例如 2026-12-01T20:30:00")
    text = sale_time.strip()
    try:
        datetime.strptime(text, "%Y-%m-%dT%H:%M:%S")
    except ValueError as exc:
        raise ValueError("开售时间必须是不带时区的本机 ISO 日期时间，例如 2026-12-01T20:30:00") from exc
    return text


def seconds_until_sale(sale_time: str, now: datetime) -> float:
    text = validate_sale_time(sale_time)
    if not text:
        return 0.0
    target = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S")
    return max(0.0, (target - now).total_seconds())


def wait_for_deadline(deadline, *, clock, sleep, keepalive):
    next_keepalive = clock() + 60
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            return
        sleep(min(1.0, remaining))
        now = clock()
        if now >= next_keepalive:
            keepalive()
            next_keepalive = now + 60


def profile_enabled() -> bool:
    """通过 DAMAI_PROFILE=1 开启命令级耗时探测，默认关闭。"""
    return os.environ.get("DAMAI_PROFILE", "").strip() == "1"


class DamaiBot:
    def __init__(self):
        self.config = Config.load_config()
        validate_sale_time(getattr(self.config, "sale_time", ""))
        self.driver = None
        self.wait = None
        self.ticket_candidates = None
        self.layout = None
        self._submit_attempted = False
        self.profiler = PerfRecorder() if profile_enabled() else None
        self._setup_driver()

    def _setup_driver(self):
        """初始化驱动配置"""
        capabilities = {
            "platformName": "Android",  # 操作系统
            "platformVersion": self.config.platform_version,  # 系统版本
            "deviceName": self.config.device_name,  # 设备名称
            "udid": self.config.device_name,  # 与票档识别保持一致，避免多设备误连
            "appPackage": "cn.damai",  # app 包名
            "appActivity": ".launcher.splash.SplashMainActivity",  # app 启动 Activity
            "unicodeKeyboard": True,  # 支持 Unicode 输入
            "resetKeyboard": True,  # 隐藏键盘
            "noReset": True,  # 不重置 app
            "newCommandTimeout": 6000,  # 超时时间
            "automationName": "UiAutomator2",  # 使用 uiautomator2
            "skipServerInstallation": False,  # 跳过服务器安装
            "ignoreHiddenApiPolicyError": True,  # 忽略隐藏 API 策略错误
            "disableWindowAnimation": True,  # 禁用窗口动画
            # 优化性能配置
            "mjpegServerFramerate": 1,  # 降低截图帧率
            "shouldTerminateApp": False,
            "adbExecTimeout": 20000,
        }

        device_app_info = AppiumOptions()
        device_app_info.load_capabilities(capabilities)
        self.driver = webdriver.Remote(self.config.server_url, options=device_app_info)

        # 更激进的性能优化设置
        self.driver.update_settings({
            "waitForIdleTimeout": 0,  # 空闲时间，0 表示不等待，让 UIAutomator2 不等页面“空闲”再返回
            "actionAcknowledgmentTimeout": 0,  # 禁止等待动作确认
            "keyInjectionDelay": 0,  # 禁止输入延迟
            "waitForSelectorTimeout": 300,  # 从500减少到300ms
            "ignoreUnimportantViews": False,  # 保持false避免元素丢失
            "allowInvisibleElements": True,
            "enableNotificationListener": False,  # 禁用通知监听
        })

        # 极短的显式等待，抢票场景下速度优先
        self.wait = WebDriverWait(self.driver, 2)  # 从5秒减少到2秒

        if self.profiler is not None:
            self.driver = install_perf_probe(self.driver, self.profiler)

    def _click_element(self, element):
        self.driver.execute_script("mobile: clickGesture", {"elementId": element.id})
        return True

    def ultra_fast_click(self, by, value, timeout=1.5):
        try:
            element = WebDriverWait(
                self.driver, timeout, poll_frequency=0.1
            ).until(EC.presence_of_element_located((by, value)))
            return self._click_element(element)
        except TimeoutException:
            return False

    def batch_click(self, elements_info, delay=0.1):
        """批量点击操作"""
        for by, value in elements_info:
            if self.ultra_fast_click(by, value):
                if delay > 0:
                    time.sleep(delay)
            else:
                print(f"点击失败: {value}")

    def ultra_batch_click(self, elements_info, timeout=2):
        """选择观演人并确认复选框状态"""
        selected_count = 0
        for by, value in elements_info:
            try:
                # 轮询间隔与其它关键路径保持一致，默认 0.5s 会让每个观演人多等最多半秒
                name_element = WebDriverWait(self.driver, timeout, poll_frequency=0.1).until(
                    EC.presence_of_element_located((by, value))
                )
                viewer_name = name_element.get_attribute("text")
                viewer_selector = f'//*[@resource-id="cn.damai:id/layout_main" and .//*[@resource-id="cn.damai:id/text_name" and @text="{viewer_name}"]]'
                checkbox = self._viewer_checkbox(viewer_selector)
                if checkbox.get_attribute("checked") != "true":
                    self._click_element(checkbox)
                    checkbox = self._viewer_checkbox(viewer_selector)
                if checkbox.get_attribute("checked") != "true":
                    print(f"观演人未选中: {value}")
                    return False
                selected_count += 1
                print(f"已选择观演人: {value}")
            except TimeoutException:
                print(f"超时未找到用户: {value}")
                return False
            except Exception as e:
                print(f"选择用户失败 {value}: {e}")
                return False
        print(f"成功选择 {selected_count} 个用户")
        return True

    def _viewer_checkbox(self, viewer_selector):
        viewer_row = self.driver.find_element(By.XPATH, viewer_selector)
        return viewer_row.find_element(By.ID, "cn.damai:id/checkbox")

    def smart_wait_and_click(self, by, value, backup_selectors=None, timeout=1.5):
        selectors = [(by, value)]
        if backup_selectors:
            selectors.extend(backup_selectors)
        for selector_by, selector_value in selectors:
            if self.ultra_fast_click(selector_by, selector_value, timeout):
                return True
        return False

    def _purchase_target(self, element):
        text = element.get_attribute("text") or ""
        if any(word in text for word in UNAVAILABLE_WORDS):
            return None
        if not any(word in text for word in PURCHASE_WORDS):
            return None
        if (
            element.get_attribute("enabled") == "true"
            and element.get_attribute("clickable") == "true"
        ):
            return element
        try:
            target = element.find_element(
                By.XPATH, 'ancestor-or-self::*[@clickable="true"][1]'
            )
        except NoSuchElementException:
            return None
        if target.get_attribute("enabled") == "true":
            return target
        return None

    def _element_is_purchasable(self, element):
        return self._purchase_target(element) is not None

    def _wait_for_purchase_button(self, timeout=30, poll_interval=0.1):
        deadline = time.monotonic() + timeout
        while True:
            for by, value in PURCHASE_SELECTORS:
                for element in self.driver.find_elements(by, value):
                    target = self._purchase_target(element)
                    if target is not None:
                        return target
            if time.monotonic() >= deadline:
                return None
            time.sleep(poll_interval)

    def _wait_for_price_page(self, timeout=2):
        try:
            WebDriverWait(self.driver, timeout, poll_frequency=0.1).until(
                EC.presence_of_element_located(
                    (By.ID, "cn.damai:id/project_detail_perform_price_flowlayout")
                )
            )
            return True
        except TimeoutException:
            print("购买入口点击后未出现票档页面")
            return False

    def _session_keepalive(self):
        return self.driver.current_activity

    def wait_until_sale(self, now=None):
        sale_time = getattr(self.config, "sale_time", "")
        remaining = seconds_until_sale(sale_time, now or datetime.now())
        if remaining <= 0:
            return
        print(f"等待开售，剩余 {remaining:.1f} 秒")
        wait_for_deadline(
            time.monotonic() + remaining,
            clock=time.monotonic,
            sleep=time.sleep,
            keepalive=self._session_keepalive,
        )

    def _load_ticket_candidates(self):
        if self.ticket_candidates is None:
            self.ticket_candidates = load_prepared_tickets(
                "ticket_candidates.json", self.config.ticket_area
            )
        return self.ticket_candidates

    def select_ticket(self):
        return select_prepared_ticket(self.driver, self._load_ticket_candidates())

    def _select_city(self):
        print("选择城市...")
        city_selectors = [
            (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{self.config.city}")'),
            (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().textContains("{self.config.city}")'),
            (By.XPATH, f'//*[@text="{self.config.city}"]'),
        ]
        if self.smart_wait_and_click(*city_selectors[0], city_selectors[1:]):
            return True
        print("城市选择失败")
        return False

    def _set_quantity(self):
        expected = len(self.config.users)
        containers = self.driver.find_elements(By.ID, "layout_num")
        if not containers:
            if expected == 1:
                return True
            print("多位观演人需要数量选择，但当前页面没有数量区域")
            return False

        # 单人多票时不再逐次查找加号：有缓存坐标就直接点，否则退回一次查找 + 逐次点击
        clicks_needed = expected - 1
        if clicks_needed:
            cached = self._cached_quantity_point()
            if cached is not None:
                for _ in range(clicks_needed):
                    tap_at(self.driver, cached)
                    time.sleep(0.02)
            else:
                for _ in range(clicks_needed):
                    try:
                        plus_button = self.driver.find_element(By.ID, "img_jia")
                    except NoSuchElementException:
                        print("需要增加票数，但未找到数量加号")
                        return False
                    self._click_element(plus_button)
                    time.sleep(0.02)

        containers = self.driver.find_elements(By.ID, "layout_num")
        if not containers:
            print("数量区域在设置后消失，无法确认数量")
            return False
        text = containers[0].get_attribute("text") or ""
        match = re.fullmatch(r"\D*(\d+)\D*", text)
        if not match:
            print(f"无法从数量区域确认唯一数字: {text!r}")
            return False
        actual = int(match.group(1))
        if actual != expected:
            print(f"数量校验失败: 期望 {expected}，实际 {actual}")
            return False
        print(f"数量已确认: {actual}")
        return True

    def _cached_quantity_point(self):
        """只返回缓存里的数量加号坐标；没有缓存时返回 None，由调用方走慢路径。"""
        layout = getattr(self, "layout", None)
        if layout is None or not layout.has("quantity_plus"):
            return None
        return layout.center("quantity_plus")

    def _log_stage(self, name, started):
        finished = time.perf_counter()
        print(f"{name}耗时: {finished - started:.3f}秒")
        return finished

    # -- 坐标快路 -------------------------------------------------------
    def _prepare_fast_path(self):
        """开售前加载布局缓存并判断是否可用；不可用则静默降级到慢路径。"""
        self.layout = None
        if not getattr(self.config, "fast_mode", True):
            print("已关闭坐标快路，使用元素查找路径")
            return
        snapshot = load_snapshot(LAYOUT_CACHE_NAME)
        if snapshot is None:
            print("没有布局缓存，使用元素查找路径（可先采集页面坐标提速）")
            return
        usable, reason = is_usable(self.driver, snapshot)
        if not usable:
            print(f"布局缓存不可用，使用元素查找路径：{reason}")
            return
        self.layout = snapshot
        print(f"已启用坐标快路，缓存控件 {len(snapshot.points)} 个")

    def _select_ticket_step(self):
        """优先按缓存坐标点票档；坐标缺失或无效时回退慢路径。"""
        layout = getattr(self, "layout", None)
        if layout is not None:
            result = select_ticket_by_coords(self.driver, layout, self.ticket_candidates)
            if result.ok:
                return True
            print(f"坐标快路选票档不可用，降级：{result.reason}")
        return self.select_ticket()

    def _confirm_purchase_step(self):
        """点确定购买：有缓存坐标就直接点，否则回退元素查找。"""
        layout = getattr(self, "layout", None)
        if layout is not None and layout.has("confirm"):
            tap_at(self.driver, layout.center("confirm"))
            print("已按缓存坐标点击确定购买")
            return True
        if not self.ultra_fast_click(By.ID, "btn_buy_view"):
            fallback = (
                AppiumBy.ANDROID_UIAUTOMATOR,
                'new UiSelector().textMatches(".*确定.*|.*购买.*")',
            )
            if not self.ultra_fast_click(*fallback):
                print("确定购买按钮点击失败")
                return False
        return True

    def _select_viewers_step(self):
        """优先按缓存坐标点观演人；缺坐标时回退元素查找。"""
        layout = getattr(self, "layout", None)
        if layout is not None:
            result = tap_viewers_by_coords(
                self.driver,
                layout,
                self.config.users,
                device=getattr(self.config, "device_name", None),
            )
            if result.ok:
                print(f"已按缓存坐标选择观演人: {', '.join(self.config.users)}")
                return True
            print(f"坐标快路选观演人不可用，降级：{result.reason}")
        user_clicks = [
            (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{user}")')
            for user in self.config.users
        ]
        return self.ultra_batch_click(user_clicks)

    def run_ticket_grabbing(self):
        try:
            print("开始抢票流程...")
            started = time.perf_counter()
            stage_started = started
            self._submit_attempted = False
            validate_sale_time(getattr(self.config, "sale_time", ""))

            self._load_ticket_candidates()
            self._prepare_fast_path()
            stage_started = self._log_stage("票档与布局缓存准备", stage_started)

            on_price_page = bool(self.driver.find_elements(
                By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"
            ))
            if not on_price_page and not self._select_city():
                return False
            stage_started = self._log_stage("城市与页面准备", stage_started)

            self.wait_until_sale()
            stage_started = self._log_stage("开售等待", stage_started)

            if not on_price_page:
                timeout = 30 if getattr(self.config, "sale_time", "") else 1.5
                purchase = self._wait_for_purchase_button(
                    timeout=timeout, poll_interval=0.1
                )
                if purchase is None:
                    print("等待可购买按钮超时，未点击预约或不可购买按钮")
                    return False
                self._click_element(purchase)
                if not self._wait_for_price_page():
                    return False
            stage_started = self._log_stage("购买入口", stage_started)

            try:
                if not self._select_ticket_step():
                    return False
            except (NoSuchElementException, TicketSelectionError) as exc:
                print(f"票档选择失败: {exc}")
                return False
            stage_started = self._log_stage("票档选择", stage_started)

            if not self._set_quantity():
                return False
            stage_started = self._log_stage("数量设置与校验", stage_started)

            if not self._confirm_purchase_step():
                return False
            stage_started = self._log_stage("确定购买", stage_started)

            if not self._select_viewers_step():
                print("观演人选择失败，停止提交")
                return False
            stage_started = self._log_stage("观演人选择与校验", stage_started)

            if self.config.if_commit_order:
                print("提交订单...")
                submit_selectors = [
                    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
                    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*提交.*|.*确认.*")'),
                    (By.XPATH, '//*[contains(@text,"提交")]'),
                ]
                self._submit_attempted = True
                if not self.smart_wait_and_click(*submit_selectors[0], submit_selectors[1:]):
                    print("提交订单按钮点击失败，请在大麦 App 核对订单状态")
                    return False
                self._log_stage("首次提交", started)
            else:
                print("观演人选择完成，未提交订单")
                self._log_stage("到达订单确认页", started)

            print(f"流程结束，请在大麦 App 确认订单状态，总耗时: {time.perf_counter() - started:.3f}秒")
            return True
        except WebDriverException:
            print("Appium/WebDriver 会话异常")
            raise
        except Exception as exc:
            print(f"抢票过程发生错误: {exc}")
            return False

    def run_with_retry(self, max_retries=3):
        try:
            for attempt in range(max_retries):
                print(f"第 {attempt + 1} 次尝试...")
                try:
                    if self.run_ticket_grabbing():
                        print("流程结束，请在大麦 App 确认订单状态")
                        return True
                    print("抢票流程失败，保留会话并停止自动重试")
                    return False
                except WebDriverException as exc:
                    if getattr(self, "_submit_attempted", False):
                        print("提交后发生会话异常，订单状态未知，请在大麦 App 核对；不自动重试")
                        return False
                    if attempt == max_retries - 1:
                        print(f"会话重连次数已用尽: {exc}")
                        return False
                    print("提交前会话异常，重新初始化驱动")
                    try:
                        self.driver.quit()
                    except Exception:
                        pass
                    self._setup_driver()
            return False
        finally:
            try:
                self.driver.quit()
            except Exception:
                pass


# 使用示例
if __name__ == "__main__":
    bot = DamaiBot()
    bot.run_with_retry(max_retries=3)
