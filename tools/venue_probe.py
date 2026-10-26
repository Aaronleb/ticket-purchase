#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读取大麦演出详情页的「演出名 / 时间 / 场馆 / 城市」，用于界面自动带出信息。

只读：打开详情页、读文本、立即关闭；不点击、不登录、不下单。
读不到就返回空值，界面自动退回手工填写，不阻塞流程。
"""

from __future__ import annotations

import json
import re
import sys
import time

# 默认 Selenium 的 HeadlessChrome UA 会被大麦直接返回 404，必须伪装成普通 Chrome
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)

JS_SUMMARY = r"""
const body = document.body.innerText || "";
const grab = (re) => { const m = body.match(re); return m ? String(m[1]).trim() : ""; };
const lines = body.split("\n").map(s => s.trim()).filter(Boolean);
const skip = /^(首页|分类|登录|下载|搜索|时间[:：]|场馆[:：]|预售|选择|￥|¥)/;
const title = lines.find(l => l.length > 6 && !skip.test(l) && !/^\d/.test(l)) || "";
return {
  title,
  docTitle: document.title || "",
  datetime: grab(/时间[:：]\s*([^\n]{4,40})/),
  venue: grab(/场馆[:：]\s*([^\n]{2,60})/),
  is404: /404/.test(document.title || "")
};
"""


def probe_summary(url: str, timeout: int = 30, driver_path: str | None = None) -> dict:
    """返回 {title, datetime, venue, city, is404, error}。"""
    out = {"title": "", "datetime": "", "venue": "", "city": "", "is404": False, "error": ""}
    try:
        from selenium import webdriver
    except ImportError:
        out["error"] = "缺少 selenium"
        return out

    options = webdriver.ChromeOptions()
    for arg in (
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-blink-features=AutomationControlled",
        "--window-size=1440,1600",
        f"--user-agent={USER_AGENT}",
    ):
        options.add_argument(arg)

    driver = None
    try:
        if driver_path:
            from selenium.webdriver.chrome.service import Service

            driver = webdriver.Chrome(service=Service(driver_path), options=options)
        else:
            driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(timeout)
        driver.get(url)
        time.sleep(2.0)  # 等首屏渲染
        data = driver.execute_script(JS_SUMMARY) or {}
        title = (data.get("title") or "").strip()
        doc_title = (data.get("docTitle") or "").strip()
        if not title and doc_title:
            title = re.sub(r"【网上订票】.*$", "", doc_title).strip()
        venue = (data.get("venue") or "").strip().rstrip("|").strip()
        city = venue.split("|")[0].strip() if "|" in venue else ""
        out.update(
            {
                "title": title[:120],
                "datetime": (data.get("datetime") or "").strip()[:40],
                "venue": venue[:80],
                "city": city[:20],
                "is404": bool(data.get("is404")),
            }
        )
        if out["is404"]:
            out["error"] = "演出页面不存在（404），请检查链接"
        elif not out["title"] and not out["datetime"]:
            out["error"] = "未识别到演出信息，可直接手工填写"
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"读取失败：{type(exc).__name__}: {exc}"
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
    return out


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else ""
    if not target:
        print("用法: python3 tools/venue_probe.py <详情页URL>")
        raise SystemExit(2)
    print(json.dumps(probe_summary(target), ensure_ascii=False, indent=2))
