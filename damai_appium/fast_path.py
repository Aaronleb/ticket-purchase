"""开售后的坐标快路。

慢路径每走一步都要先 ``find_element`` 再点，一次操作 2–5 个往返。快路把开售前
采集好的坐标直接用来点击，一个操作只剩 1 个往返，且可以整串批量下发。

安全原则（比速度优先）：

- 缓存必须存在，且屏幕尺寸与采集时一致，否则整体返回不可用，调用方走慢路径；
- 任何一次坐标点击后如果页面没有按预期变化，立即停止，不补点；
- 提交按钮单独处理，一旦发出就记录，绝不在重试路径上再发一次。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from appium.webdriver.common.appiumby import AppiumBy

try:
    from .layout_cache import LayoutCacheError, load_layout, screen_size
except ImportError:
    from layout_cache import LayoutCacheError, load_layout, screen_size


@dataclass
class FastPathResult:
    ok: bool
    reason: str = ""


def tap_at(driver, point):
    """按坐标点击一次，走 UiAutomator2 的手势接口。"""
    driver.execute_script("mobile: clickGesture", {"x": point[0], "y": point[1]})
    return True


def is_usable(driver, snapshot) -> tuple[bool, str]:
    """判断缓存坐标能否用于当前屏幕。"""
    if snapshot is None:
        return False, "没有可用的布局缓存"
    try:
        current = screen_size(driver)
    except Exception as exc:  # 读取屏幕失败时按不可用处理
        return False, f"无法读取屏幕尺寸：{exc}"
    if not snapshot.matches(current):
        return False, (
            f"屏幕尺寸已变化（缓存 {snapshot.screen[0]}x{snapshot.screen[1]}，"
            f"当前 {current[0]}x{current[1]}），请重新采集"
        )
    return True, ""


def page_changed(driver, marker_id, polls=3, interval=0.08) -> bool:
    """用少量元素查询确认页面是否已经切换。

    快路要的是"确认跳转成功"，不是"等到元素出现"；没出现就由调用方降级。
    """
    for _ in range(polls):
        if driver.find_elements(AppiumBy.ID, marker_id):
            return True
        time.sleep(interval)
    return False


def select_ticket_by_coords(driver, snapshot, candidates) -> FastPathResult:
    """按缓存坐标点票档卡片，跳过元素查找与价格复核。"""
    for candidate in candidates:
        key = f"card_{candidate['index']}"
        if not snapshot.has(key):
            return FastPathResult(False, f"缓存缺少票档 {candidate['price']:g}元 的坐标")
        tap_at(driver, snapshot.center(key))
        return FastPathResult(True)
    return FastPathResult(False, "缓存中的票档均不可购买")


def tap_viewers_by_coords(driver, snapshot, users, device=None) -> FastPathResult:
    """按缓存坐标点每位观演人的勾选框。

    多个观演人时优先合并成一条 adb 命令；adb 不可用或失败时回退为逐个 Appium 点击。
    """
    missing = [user for user in users if not snapshot.has(f"viewer_{user}")]
    if missing:
        return FastPathResult(False, f"缓存缺少观演人坐标: {', '.join(missing)}")

    points = [snapshot.center(f"viewer_{user}") for user in users]
    if len(points) > 1:
        try:
            from .adb_tap import batch_tap
        except ImportError:
            from adb_tap import batch_tap

        try:
            batch_tap(points, device=device)
            return FastPathResult(True)
        except Exception as exc:
            print(f"adb 批量点击不可用，改为逐个点击：{exc}")

    for point in points:
        tap_at(driver, point)
    return FastPathResult(True)


def load_snapshot(snapshot_path):
    """读取布局缓存；失败时返回 None，由调用方降级。"""
    try:
        return load_layout(Path(snapshot_path))
    except (LayoutCacheError, OSError):
        return None
