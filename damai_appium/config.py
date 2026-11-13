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
    ):
        self.server_url = server_url
        self.if_commit_order = if_commit_order
        self.device_name = device_name
        self.platform_version = platform_version
        self.ticket_count = ticket_count
        self.sell_start_time = sell_start_time

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
        )
