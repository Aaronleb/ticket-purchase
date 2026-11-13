# -*- coding: UTF-8 -*-
"""安卓原生预约快路配置。"""
import json


class Config:
    def __init__(
        self,
        server_url,
        if_commit_order,
        device_name="emulator-5554",
        platform_version="16",
        ticket_count=1,
        sell_start_time="",
        price="",
        auto_select=True,
        users=None,
        keyword="",
        fast_mode=True,
        confirm_point="",
        submit_point="",
    ):
        self.server_url = server_url
        self.if_commit_order = if_commit_order
        self.device_name = device_name
        self.platform_version = platform_version
        self.ticket_count = ticket_count
        self.sell_start_time = sell_start_time
        self.price = price
        # 进页面只管提交：False 时不选票档、不勾观演人，直接点“确定/立即提交”
        self.auto_select = auto_select
        # 观演人姓名：自动勾选时按它匹配
        self.users = [u for u in (users or []) if isinstance(u, str) and u.strip()]
        # 极速模式：开售瞬间只按坐标点“确定”和“立即提交”，不做 OCR/dump
        self.keyword = (keyword or "").strip()
        self.fast_mode = fast_mode
        self.confirm_point = (confirm_point or "").strip()
        self.submit_point = (submit_point or "").strip()

    @staticmethod
    def load_config():
        with open("config.jsonc", "r", encoding="utf-8") as config_file:
            config = json.load(config_file)
        return Config(
            config["server_url"],
            config["if_commit_order"],
            config.get("device_name", "emulator-5554"),
            config.get("platform_version", "16"),
            config.get("ticket_count", 1),
            config.get("sell_start_time", ""),
            config.get("price", ""),
            config.get("auto_select", True),
            config.get("users") or [],
            config.get("keyword", ""),
            config.get("fast_mode", True),
            config.get("confirm_point", ""),
            config.get("submit_point", ""),
        )
