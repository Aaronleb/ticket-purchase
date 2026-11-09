# -*- coding: UTF-8 -*-
"""
__Author__ = "WECENG"
__Version__ = "1.0.0"
__Description__ = "配置类"
__Created__ = 2023/10/27 09:54
"""
import json


class Config:
    def __init__(self, server_url, keyword, users, city, date, price, price_index, if_commit_order,
                 device_name='emulator-5554', platform_version='16', ticket_area='内场',
                 price_strategy='highest_available', sale_time=''):
        self.server_url = server_url
        self.keyword = keyword
        self.users = users
        self.city = city
        self.date = date
        self.price = price
        self.price_index = price_index
        self.if_commit_order = if_commit_order
        self.device_name = device_name
        self.platform_version = platform_version
        self.ticket_area = ticket_area
        self.price_strategy = price_strategy
        self.sale_time = sale_time

    @staticmethod
    def load_config():
        with open('config.jsonc', 'r', encoding='utf-8') as config_file:
            config = json.load(config_file)
        return Config(config['server_url'],
                      config['keyword'],
                      config['users'],
                      config['city'],
                      config['date'],
                      config['price'],
                      config['price_index'],
                      config['if_commit_order'],
                      config.get('device_name', 'emulator-5554'),
                      config.get('platform_version', '16'),
                      config.get('ticket_area', '内场'),
                      config.get('price_strategy', 'highest_available'),
                      config.get('sale_time', ''))
