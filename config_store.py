from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_CONFIG = {
    "index_url": "https://www.damai.cn/",
    "login_url": "https://passport.damai.cn/login",
    "target_url": "",
    "users": [],
    "city": "",
    "dates": [],
    "prices": [],
    "if_listen": True,
    "if_commit_order": False,
    "max_retries": 1000,
    "fast_mode": True,
    "page_load_delay": 2,
}


class ConfigError(ValueError):
    pass


def default_project_root() -> Path:
    configured = os.environ.get("DAMAI_PROJECT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parent


def _string_list(value, field: str, required: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise ConfigError(f"{field} 必须是字符串列表")
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{field} 不能包含空白项")
        text = item.strip()
        if text not in result:
            result.append(text)
    if required and not result:
        raise ConfigError("请至少填写一位观演人")
    return result


def _validate_damai_url(value, field: str, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{field} 必须是 URL 字符串")
    value = value.strip()
    if not value:
        if required:
            raise ConfigError("请填写演出详情页链接")
        return ""
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not (
        hostname == "damai.cn" or hostname.endswith(".damai.cn")
    ):
        raise ConfigError(f"{field} 必须是大麦网 http(s) 链接")
    return value


def validate_config(payload) -> dict:
    if not isinstance(payload, dict):
        raise ConfigError("配置必须是 JSON 对象")

    config = dict(DEFAULT_CONFIG)
    config.update(payload)
    config["index_url"] = _validate_damai_url(config["index_url"], "首页链接", True)
    config["login_url"] = _validate_damai_url(config["login_url"], "登录链接", True)
    config["target_url"] = _validate_damai_url(config["target_url"], "演出详情页链接", True)
    config["users"] = _string_list(config["users"], "users", True)
    config["dates"] = _string_list(config["dates"], "dates")
    config["prices"] = _string_list(config["prices"], "prices")

    city = config["city"]
    if city is None:
        city = ""
    if not isinstance(city, str):
        raise ConfigError("city 必须是字符串")
    config["city"] = city.strip()

    for field in ("if_listen", "if_commit_order", "fast_mode"):
        if not isinstance(config[field], bool):
            raise ConfigError(f"{field} 必须是布尔值")

    retries = config["max_retries"]
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 1:
        raise ConfigError("max_retries 必须是正整数")

    delay = config["page_load_delay"]
    if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not 0 <= delay <= 60:
        raise ConfigError("page_load_delay 必须在 0 到 60 秒之间")
    return config


class ConfigStore:
    def __init__(self, project_root: Path | str):
        self.project_root = Path(project_root).resolve()
        self.damai_dir = self.project_root / "damai"
        self.config_path = self.damai_dir / "config.json"
        self.cookie_path = self.damai_dir / "damai_cookies.pkl"

    def ensure_project(self) -> None:
        required = [self.damai_dir / "damai.py", self.damai_dir / "check_environment.py"]
        if not all(path.is_file() for path in required):
            raise ConfigError(f"未找到有效的大麦项目：{self.project_root}")

    def load(self) -> dict:
        self.ensure_project()
        if not self.config_path.exists():
            return dict(DEFAULT_CONFIG)
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"读取配置失败：{exc}") from exc
        if not isinstance(payload, dict):
            raise ConfigError("配置文件必须是 JSON 对象")
        config = dict(DEFAULT_CONFIG)
        config.update(payload)
        return config

    def save(self, payload) -> dict:
        self.ensure_project()
        config = validate_config(payload)
        self.damai_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".config-", suffix=".json", dir=self.damai_dir
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
