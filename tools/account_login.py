#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为单个账号完成扫码登录，并把登录态写进该账号的 cookie 文件。

流程：打开可见的 Chrome → 用户在窗口里扫码 → 界面点「已完成」→ 写入 cookie。
只做登录，不进详情页、不抢票。

命令行：
  python3 account_login.py --cookie <path> --signal <flag> [--timeout 600]
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
import time

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)

LOGIN_COOKIE_NAMES = ("cookie2", "sgcookie", "_tb_token_", "unb", "cna", "_m_h5_tk")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cookie", required=True)
    parser.add_argument("--signal", required=True)
    parser.add_argument("--url", default="https://www.damai.cn/")
    parser.add_argument("--driver", default="")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--headless", action="store_true", help="仅用于自测")
    args = parser.parse_args()

    from pathlib import Path

    signal = Path(args.signal)
    cookie_file = Path(args.cookie)
    cookie_file.parent.mkdir(parents=True, exist_ok=True)

    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

    options = webdriver.ChromeOptions()
    for arg in ("--no-sandbox", "--disable-gpu", "--window-size=1200,900", f"--user-agent={USER_AGENT}"):
        options.add_argument(arg)
    if args.headless:
        options.add_argument("--headless=new")

    driver = webdriver.Chrome(
        service=Service(args.driver) if args.driver else Service(), options=options
    )
    status = {"ok": False, "count": 0, "login_like": False, "reason": ""}
    try:
        driver.get(args.url)
        print("已打开大麦首页，请扫码登录；完成后在界面点「已完成登录」", flush=True)
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            if signal.exists():
                break
            time.sleep(0.4)
        else:
            status["reason"] = "等待超时"
            print("等待登录超时", flush=True)

        cookies = driver.get_cookies() or []
        if signal.exists():
            signal.unlink()
        if not cookies:
            status["reason"] = status["reason"] or "未取到任何 Cookie"
            print("未取到 Cookie", flush=True)
        else:
            with cookie_file.open("wb") as fh:
                pickle.dump(cookies, fh)
            names = {c.get("name") for c in cookies}
            status.update(
                {
                    "ok": True,
                    "count": len(cookies),
                    "login_like": bool(names & set(LOGIN_COOKIE_NAMES)),
                }
            )
            print(f"已保存 {len(cookies)} 条 Cookie 到 {cookie_file}", flush=True)
    except Exception as exc:  # noqa: BLE001
        status["reason"] = f"{type(exc).__name__}: {exc}"
        print(f"登录流程异常：{status['reason']}", flush=True)
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        print("RESULT " + json.dumps(status, ensure_ascii=False), flush=True)
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
