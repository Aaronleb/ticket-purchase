from __future__ import annotations

from pathlib import Path

from appium import webdriver
from appium.options.common.base import AppiumOptions

from damai_appium.layout_cache import CAPTURE_STEPS, capture_step, merge_step


def _with_driver(config: dict, action):
    options = AppiumOptions().load_capabilities(
        {
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
    )
    driver = None
    try:
        driver = webdriver.Remote(config["server_url"], options=options)
        return action(driver)
    finally:
        if driver is not None:
            driver.quit()


def capture_android_layout(config: dict, step_key: str, layout_path: Path) -> dict:
    def run(driver):
        captured = capture_step(
            driver, step_key, config["ticket_count"], config["price_index"]
        )
        snapshot = merge_step(layout_path, captured)
        return {
            "step": step_key,
            "captured": captured["points"],
            "screen": list(snapshot.screen),
            "ticket_count": snapshot.ticket_count,
            "total_points": len(snapshot.points),
        }

    return _with_driver(config, run)


def layout_steps() -> list[dict]:
    return [
        {"key": step["key"], "title": step["title"], "hint": step["hint"]}
        for step in CAPTURE_STEPS
    ]
