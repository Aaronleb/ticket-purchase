"""一秒候选快速通道：坐标点击、常驻 ADB、提交前复核。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

try:
    from .layout_cache import VIEWER_CHECKBOXES, screen_size
except ImportError:
    from layout_cache import VIEWER_CHECKBOXES, screen_size
from selenium.webdriver.common.by import By


@dataclass
class FastOrderResult:
    ok: bool
    reason: str = ""
    seconds: float = 0.0
    stages: dict = field(default_factory=dict)


def validate_snapshot(driver, snapshot, ticket_count, price_index, if_commit_order):
    if snapshot is None:
        return False, "没有布局缓存"
    if not snapshot.matches(screen_size(driver), ticket_count, price_index):
        return False, "屏幕、购票数量或票档序号与缓存不一致"
    required = [f"card_{price_index}", "confirm"]
    required.extend(f"viewer_{index}" for index in range(ticket_count))
    if if_commit_order:
        required.append("submit")
    missing = [key for key in required if not snapshot.has(key)]
    if missing:
        return False, f"缓存缺少控件: {', '.join(missing)}"
    return True, ""


def _checkboxes(driver):
    return driver.find_elements(By.XPATH, VIEWER_CHECKBOXES)


def _states(checkboxes):
    return [checkbox.get_attribute("checked") == "true" for checkbox in checkboxes]


def wait_for_viewers(driver, ticket_count, attempts=5, interval=0.04):
    for attempt in range(attempts):
        checkboxes = _checkboxes(driver)
        if len(checkboxes) >= ticket_count:
            return checkboxes
        if attempt + 1 < attempts:
            time.sleep(interval)
    return None


def select_first_n(driver, snapshot, shell, ticket_count, checkboxes):
    expected = [index < ticket_count for index in range(len(checkboxes))]
    initial = _states(checkboxes)
    points = []
    for index, should_select in enumerate(expected):
        if initial[index] != should_select:
            key = f"viewer_{index}"
            if not snapshot.has(key):
                return False, f"缺少第 {index + 1} 位观演人坐标"
            points.append(snapshot.center(key))
    shell.batch_tap(points, delay=0.04)
    final_states = _states(_checkboxes(driver))
    if final_states != expected:
        return False, (
            f"观演人复核失败：需要前 {ticket_count} 位，"
            f"实际选中 {sum(final_states)} 位"
        )
    return True, ""


def run_fast_order(driver, snapshot, shell, ticket_count, if_commit_order, before_submit):
    """执行快速通道；任何不明确结果都停止，不在本函数内重试。"""
    started = time.perf_counter()
    stages = {}

    def mark(name):
        nonlocal started
        now = time.perf_counter()
        stages[name] = now - started
        started = now

    try:
        shell.batch_tap(
            [snapshot.center("card_" + str(snapshot.price_index)), snapshot.center("confirm")],
            delay=0.08,
        )
        mark("票档与确定")
        checkboxes = wait_for_viewers(driver, ticket_count)
        if checkboxes is None:
            return FastOrderResult(False, "确认购买页未及时出现", stages["票档与确定"], stages)
        selected, reason = select_first_n(
            driver, snapshot, shell, ticket_count, checkboxes
        )
        mark("观演人选择与复核")
        if not selected:
            return FastOrderResult(False, reason, sum(stages.values()), stages)
        if if_commit_order:
            before_submit()
            shell.tap(snapshot.center("submit"))
            mark("提交指令")
        return FastOrderResult(True, seconds=sum(stages.values()), stages=stages)
    except Exception as exc:
        return FastOrderResult(False, f"快速通道中止：{exc}", sum(stages.values()), stages)
