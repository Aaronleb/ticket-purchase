# -*- coding: UTF-8 -*-
"""u2 事件式按钮翻转监听。

通过 uiautomator2 让设备端的 atx-agent 直接等待无障碍树里出现
「立即预订/立即购票」文案——等待发生在设备端，出现时立即返回，
感知延迟百毫秒级，远快于截图 OCR（约 1.8 秒/轮）。

设备需要先执行过一次 ``python -m uiautomator2 init``（装 atx-agent）。
agent 不可用时报 FlipWatchError，调用方退回 OCR 轮询。
"""
from __future__ import annotations

import time


FLIP_KEYWORDS = ("立即预订", "立即购票", "立即购买")
# 命中文案的 top 需超过屏幕高度的 75% 才算底栏翻转（排除页面中部文案）
BOTTOM_BAR_MIN_RATIO = 0.75


class FlipWatchError(RuntimeError):
    """u2 agent 不可用，无法启用事件监听。"""


def _hit_bottom_bar(bounds: dict, display_height: int) -> bool:
    if not display_height:
        return True  # 拿不到屏幕高度时不做区域过滤
    top = (bounds or {}).get("top", 0)
    return top >= display_height * BOTTOM_BAR_MIN_RATIO


def make_flip_watcher(device: str):
    """连接设备端 u2 agent，返回 (名称, watch)。

    watch(remaining_seconds) -> bool：在 remaining 秒内等到底栏出现
    可购买文案返回 True，超时返回 False。
    """
    try:
        import uiautomator2 as u2
    except ImportError as exc:
        raise FlipWatchError(f"uiautomator2 未安装：{exc}") from exc
    try:
        d = u2.connect(device)
        d.info  # 探活：agent 不在会在这里抛异常
    except Exception as exc:
        raise FlipWatchError(
            f"无法连接设备端 u2 agent（{type(exc).__name__}: {exc}）；"
            "手机连上后执行 .venv/bin/python -m uiautomator2 init 安装"
        ) from exc

    def watch(remaining: float) -> bool:
        deadline = time.monotonic() + remaining
        for keyword in FLIP_KEYWORDS:
            left = deadline - time.monotonic()
            if left <= 0.05:
                return False
            obj = d(textContains=keyword)
            try:
                if not obj.wait(timeout=min(left, 1.5)):
                    continue
                display_height = (d.info or {}).get("displayHeight", 0)
                if _hit_bottom_bar(obj.info.get("bounds") or {}, display_height):
                    return True
                # 命中在屏幕中上部：不是底栏翻转，继续等下一个关键词
            except Exception:
                continue
        return False

    return "u2-agent 事件监听", watch
