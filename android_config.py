from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from config_store import ConfigError
from damai_appium.sale_clock import SaleTimeError, normalize_sale_time


DEFAULT_ANDROID_CONFIG = {
    "server_url": "http://127.0.0.1:4723",
    "device_name": "emulator-5554",
    "platform_version": "16",
    "keyword": "",
    "users": [],
    "city": "",
    "date": "",
    "price": "",
    "price_index": 0,
    "ticket_count": 1,
    "sell_start_time": "",
    "if_commit_order": False,
    # 极速模式：开售瞬间只按坐标点“确定/立即提交”，不 OCR、不 dump
    "fast_mode": True,
    # 坐标留空则按屏幕比例换算
    "confirm_point": "",
    "submit_point": "",
}


def _required_string(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field} 不能为空")
    return value.strip()


def _optional_string(value, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ConfigError(f"{field} 必须是字符串")
    return value.strip()


def validate_android_config(payload) -> dict:
    if not isinstance(payload, dict):
        raise ConfigError("安卓配置必须是 JSON 对象")
    config = dict(DEFAULT_ANDROID_CONFIG)
    config.update(payload)

    server_url = _required_string(config["server_url"], "Appium 地址").rstrip("/")
    parsed = urlparse(server_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigError("Appium 地址必须是有效的 http(s) URL")
    if parsed.username or parsed.password:
        raise ConfigError("Appium 地址不能包含账号或密码")
    config["server_url"] = server_url
    config["device_name"] = _required_string(config["device_name"], "设备名")
    config["platform_version"] = _required_string(config["platform_version"], "Android 版本")
    for field in ("keyword", "city", "date", "price"):
        config[field] = _optional_string(config[field], field)

    users = config["users"]
    if not isinstance(users, list):
        raise ConfigError("users 必须是字符串列表")
    normalized_users = []
    for user in users:
        if not isinstance(user, str) or not user.strip():
            raise ConfigError("观演人不能包含空白项")
        name = user.strip()
        if name not in normalized_users:
            normalized_users.append(name)
    config["users"] = normalized_users

    price_index = config["price_index"]
    if isinstance(price_index, bool) or not isinstance(price_index, int) or not 0 <= price_index <= 50:
        raise ConfigError("price_index 必须是 0 到 50 之间的整数")
    ticket_count = config["ticket_count"]
    if isinstance(ticket_count, bool) or not isinstance(ticket_count, int) or not 1 <= ticket_count <= 6:
        raise ConfigError("ticket_count 必须是 1 到 6 之间的整数")
    try:
        config["sell_start_time"] = normalize_sale_time(config["sell_start_time"])
    except SaleTimeError as exc:
        raise ConfigError(str(exc)) from exc
    if not isinstance(config["if_commit_order"], bool):
        raise ConfigError("if_commit_order 必须是布尔值")
    return config


class AndroidConfigStore:
    def __init__(
        self,
        project_root: Path | str,
        config_path: Path | str | None = None,
        managed_server_url: str | None = None,
    ):
        self.project_root = Path(project_root).resolve()
        self.appium_dir = self.project_root / "damai_appium"
        self.config_path = (
            Path(config_path).resolve()
            if config_path is not None
            else self.appium_dir / "config.jsonc"
        )
        self.managed_server_url = managed_server_url.rstrip("/") if managed_server_url else None
        self.entry_path = self.appium_dir / "damai_app_v2.py"

    def ensure_project(self) -> None:
        if not self.entry_path.is_file():
            raise ConfigError(f"未找到安卓 Appium 项目：{self.appium_dir}")

    def load(self) -> dict:
        self.ensure_project()
        if not self.config_path.exists():
            config = dict(DEFAULT_ANDROID_CONFIG)
        else:
            try:
                payload = json.loads(self.config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ConfigError(f"读取安卓配置失败：{exc}") from exc
            if not isinstance(payload, dict):
                raise ConfigError("安卓配置文件必须是 JSON 对象")
            config = dict(DEFAULT_ANDROID_CONFIG)
            config.update(payload)
        if self.managed_server_url:
            config["server_url"] = self.managed_server_url
        return config

    def save(self, payload) -> dict:
        self.ensure_project()
        if self.managed_server_url and isinstance(payload, dict):
            payload = {**payload, "server_url": self.managed_server_url}
        config = validate_android_config(payload)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".android-config-", suffix=".jsonc", dir=self.config_path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(config, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temporary, self.config_path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return config
