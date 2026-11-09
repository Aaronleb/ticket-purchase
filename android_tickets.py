from __future__ import annotations

import json
from pathlib import Path

from appium import webdriver
from appium.options.common.base import AppiumOptions

from damai_appium.ticket_selection import TicketPreparation


def prepare_android_tickets(config: dict, cache_path: Path) -> dict:
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
        candidates = TicketPreparation(driver).prepare(config["ticket_area"])
        payload = {"ticket_area": config["ticket_area"], "candidates": candidates}
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload
    finally:
        if driver is not None:
            driver.quit()
