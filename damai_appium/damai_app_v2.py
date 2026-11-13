# -*- coding: UTF-8 -*-
"""大麦 App 原生预约快路。"""
from __future__ import annotations

import re
import time

from appium import webdriver
from appium.options.common.base import AppiumOptions
from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

try:
    from .config import Config
    from .sale_clock import SaleTimeError, wait_for_sale_time
except ImportError:
    from config import Config
    from sale_clock import SaleTimeError, wait_for_sale_time


BUY_NOW_SELECTOR = (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即购买")')
SUBMIT_SELECTORS = (
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("提交订单")'),
)
VIEWER_CHECKBOXES = (
    '//*[@resource-id="cn.damai:id/layout_main"]'
    '//*[@resource-id="cn.damai:id/checkbox"]'
)


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


class DamaiBot:
    def __init__(self):
        self.config = Config.load_config()
        self.driver = None
        self.wait = None
        self._purchase_attempted = False
        self._submit_attempted = False
        self._setup_driver()

    def _setup_driver(self):
        capabilities = {
            "platformName": "Android",
            "platformVersion": self.config.platform_version,
            "deviceName": self.config.device_name,
            "appPackage": "cn.damai",
            "appActivity": ".launcher.splash.SplashMainActivity",
            "unicodeKeyboard": True,
            "resetKeyboard": True,
            "noReset": True,
            "newCommandTimeout": 86400,
            "automationName": "UiAutomator2",
            "skipServerInstallation": False,
            "ignoreHiddenApiPolicyError": True,
            "disableWindowAnimation": True,
            "mjpegServerFramerate": 1,
            "shouldTerminateApp": False,
            "adbExecTimeout": 20000,
        }
        options = AppiumOptions().load_capabilities(capabilities)
        self.driver = webdriver.Remote(self.config.server_url, options=options)
        self.driver.update_settings({
            "waitForIdleTimeout": 0,
            "actionAcknowledgmentTimeout": 0,
            "keyInjectionDelay": 0,
            "waitForSelectorTimeout": 300,
            "ignoreUnimportantViews": False,
            "allowInvisibleElements": True,
            "enableNotificationListener": False,
        })
        self.wait = WebDriverWait(self.driver, 2, poll_frequency=0.05)

    @staticmethod
    def _click_element(driver, element):
        rect = element.rect
        driver.execute_script("mobile: clickGesture", {
            "x": rect["x"] + rect["width"] // 2,
            "y": rect["y"] + rect["height"] // 2,
            "duration": 20,
        })

    def _find_submit(self):
        for by, value in SUBMIT_SELECTORS:
            elements = self.driver.find_elements(by, value)
            if elements:
                return elements[0]
        return None

    def _locate_confirmation(self, _driver):
        """一次轮询确认页的三个必要区域，不修改任何预约选择。"""
        prices = self.driver.find_elements(By.ID, "cn.damai:id/tv_price")
        checkboxes = self.driver.find_elements(By.XPATH, VIEWER_CHECKBOXES)
        if not prices or not checkboxes:
            return False
        submit = self._find_submit()
        if submit is None:
            return False
        return {"price": prices[0], "checkboxes": checkboxes, "submit": submit}

    @staticmethod
    def _validate_confirmation(page, ticket_count):
        price_element = page["price"]
        price_text = price_element.get_attribute("text") or price_element.text
        amount = parse_amount(price_text)
        if amount is None:
            raise ConfirmationError(f"确认页金额无效：{price_text!r}")
        selected = sum(
            checkbox.get_attribute("checked") == "true"
            for checkbox in page["checkboxes"]
        )
        if selected != ticket_count:
            raise ConfirmationError(
                f"预约观演人数不符：需要 {ticket_count} 位，实际已选 {selected} 位"
            )
        return amount, selected

    def _wait_for_sale(self):
        return wait_for_sale_time(self.config.sell_start_time)

    def run_ticket_grabbing(self):
        """执行一次预约快路；任何结果不明确都停止，不自动重试。"""
        started = None
        try:
            if self._purchase_attempted:
                print("本次会话已尝试购买，停止重复操作")
                return False
            print(f"等待程序定时开抢：{self.config.sell_start_time}")
            release = self._wait_for_sale()
            print(
                f"定时放行：{release.released_at.isoformat()}；"
                f"调度偏差：{release.delay_ms:.3f} 毫秒"
            )
            started = time.perf_counter()
            self._purchase_attempted = True
            print("预约快路：点击立即购买...")
            buy_buttons = self.wait.until(
                lambda _driver: self.driver.find_elements(*BUY_NOW_SELECTOR) or False
            )
            self._click_element(self.driver, buy_buttons[0])

            print("等待订单确认页并核对预约结果...")
            page = self.wait.until(self._locate_confirmation)
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
            self._click_element(self.driver, page["submit"])
            print(f"提交指令已发出，请在大麦 App 确认订单；耗时：{time.perf_counter() - started:.3f} 秒")
            return True
        except TimeoutException:
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
        finally:
            if self.driver is not None:
                self.driver.quit()

    def run_with_retry(self, max_retries=1):
        """兼容旧入口；预约快路始终只执行一次。"""
        return self.run_ticket_grabbing()


if __name__ == "__main__":
    bot = DamaiBot()
    bot.run_with_retry()
