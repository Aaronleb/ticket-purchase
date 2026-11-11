from __future__ import annotations

import json
from pathlib import Path

from appium import webdriver
from appium.options.common.base import AppiumOptions

from damai_appium.layout_cache import CAPTURE_STEPS, capture_step, merge_step
from damai_appium.ticket_selection import TicketPreparation


def _capabilities(config: dict) -> dict:
    return {
        "platformName": "Android",
        "platformVersion": config["platform_version"],
        "deviceName": config["device_name"],
        "udid": config["device_name"],
        "appPackage": "cn.damai",
        "appActivity": ".launcher.splash.SplashMainActivity",
        "automationName": "UiAutomator2",
        "noReset": True,
        "shouldTerminateApp": False,
        "ignoreHiddenApiPolicyError": True,
        "adbExecTimeout": 60000,
        "newCommandTimeout": 300,
    }


def _with_driver(config: dict, action):
    options = AppiumOptions().load_capabilities(_capabilities(config))
    driver = None
    try:
        driver = webdriver.Remote(config["server_url"], options=options)
        return action(driver)
    finally:
        if driver is not None:
            driver.quit()


def prepare_android_tickets(config: dict, cache_path: Path, layout_path: Path | None = None) -> dict:
    """识别票档并把票档页的控件坐标一起缓存。"""

    def run(driver):
        candidates = TicketPreparation(driver).prepare(config["ticket_area"])
        payload = {"ticket_area": config["ticket_area"], "candidates": candidates}
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if layout_path is not None:
            try:
                merge_step(layout_path, capture_step(driver, "price_page"))
            except Exception as exc:  # 坐标采集失败不阻断票档缓存
                print(f"票档页坐标采集失败（不影响抢票，可单独补采）：{exc}")
        return payload

    return _with_driver(config, run)


def capture_android_layout(config: dict, step_key: str, layout_path: Path) -> dict:
    """采集当前页面某一阶段的控件坐标并写入布局缓存。"""

    def run(driver):
        captured = capture_step(driver, step_key)
        snapshot = merge_step(layout_path, captured)
        return {
            "step": step_key,
            "captured": captured["points"],
            "screen": list(snapshot.screen),
            "total_points": len(snapshot.points),
        }

    return _with_driver(config, run)


def layout_steps() -> list[dict]:
    return [
        {"key": step["key"], "title": step["title"], "hint": step["hint"]}
        for step in CAPTURE_STEPS
    ]
