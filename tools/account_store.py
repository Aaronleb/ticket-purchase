#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账号库：多账号的本地存储。

目录结构（全部在仓库内，已被 .gitignore 忽略）：
    .data/accounts.json                 # 账号索引（不含敏感信息）
    .data/accounts/<id>/damai_cookies.pkl   # 该账号的登录态
    .runs/<id>/                         # 该账号抢票时的运行目录

每个账号各自持有：名称、观演人清单、是否启用、登录态。
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_HOME = Path(os.environ.get("DAMAI_DATA") or (Path.home() / "Library" / "Application Support" / "DamaiHelper"))
DATA_DIR = DATA_HOME
ACCOUNTS_DIR = DATA_DIR / "accounts"
INDEX_PATH = DATA_DIR / "accounts.json"


def _ensure_dirs() -> None:
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)


def load() -> list[dict]:
    _ensure_dirs()
    if not INDEX_PATH.exists():
        return []
    try:
        payload = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    accounts = payload.get("accounts") if isinstance(payload, dict) else payload
    if not isinstance(accounts, list):
        return []
    cleaned = []
    for item in accounts:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        cleaned.append(
            {
                "id": str(item["id"]),
                "name": str(item.get("name") or "未命名账号"),
                "enabled": bool(item.get("enabled", True)),
                "users": [str(u) for u in (item.get("users") or [])],
                "selected": [str(u) for u in (item.get("selected") or [])],
                "last_login": item.get("last_login") or "",
            }
        )
    return cleaned


def save(accounts: list[dict]) -> None:
    _ensure_dirs()
    payload = {"version": 1, "accounts": accounts}
    INDEX_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def account_dir(account: dict) -> Path:
    path = ACCOUNTS_DIR / str(account["id"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def cookie_path(account: dict) -> Path:
    return account_dir(account) / "damai_cookies.pkl"


def has_cookie(account: dict) -> bool:
    path = cookie_path(account)
    try:
        return path.exists() and path.stat().st_size > 0
    except OSError:
        return False


def add(name: str, users: list[str] | None = None) -> dict:
    name = (name or "").strip()[:24] or "未命名账号"
    account = {
        "id": "acc_" + uuid.uuid4().hex[:10],
        "name": name,
        "enabled": True,
        "users": list(users or []),
        "selected": list(users or []),
        "last_login": "",
    }
    accounts = load()
    accounts.append(account)
    save(accounts)
    account_dir(account)
    return account


def remove(account_id: str) -> None:
    accounts = [a for a in load() if a["id"] != account_id]
    save(accounts)
    target = ACCOUNTS_DIR / account_id
    if target.exists():
        for child in sorted(target.rglob("*"), reverse=True):
            try:
                child.unlink() if child.is_file() else child.rmdir()
            except OSError:
                pass
        try:
            target.rmdir()
        except OSError:
            pass


def update(account: dict) -> None:
    """就地更新一条记录（按 id 匹配）。"""
    accounts = load()
    for index, item in enumerate(accounts):
        if item["id"] == account["id"]:
            accounts[index] = account
            break
    else:
        accounts.append(account)
    save(accounts)


def mark_logged_in(account: dict) -> dict:
    account["last_login"] = time.strftime("%Y-%m-%d %H:%M")
    update(account)
    return account


def clear_cookie(account: dict) -> None:
    path = cookie_path(account)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def migrate_from_config(users: list[str]) -> list[dict]:
    """首次使用：把旧配置里的观演人迁成一个「默认账号」。"""
    accounts = load()
    if accounts:
        return accounts
    if not users:
        return []
    account = add("默认账号", [str(u) for u in users])
    return [account]


def safe_slug(text: str) -> str:
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", text).strip("_")
    return slug[:24] or "account"
