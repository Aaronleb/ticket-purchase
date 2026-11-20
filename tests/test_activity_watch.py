# -*- coding: UTF-8 -*-
"""logcat 事件感知：解析与等待不依赖真实 adb。"""
import time

import pytest

from activity_watch import ActivityWatcher, parse_activity_line


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "11-18 21:54:26.123  1234  1250 I ActivityTaskManager: "
            "Displayed cn.damai/.trade.order.DmOrderActivity: +1s234ms",
            ("displayed", "DmOrderActivity"),
        ),
        (
            "11-18 21:54:26.123  1234  1250 I ActivityTaskManager: "
            "Fully drawn cn.damai/cn.damai.sku.NcovSkuActivity: +456ms",
            ("displayed", "NcovSkuActivity"),
        ),
        (
            "11-18 21:54:25.000  1234  1250 I ActivityTaskManager: "
            "START u0 {act=android.intent.action.MAIN cmp=cn.damai/.ProjectDetailActivity}",
            ("started", "ProjectDetailActivity"),
        ),
        ("11-18 21:54:26.123  1234  1250 I ActivityTaskManager: some noise", None),
        ("", None),
    ],
)
def test_parse_activity_line(line, expected):
    assert parse_activity_line(line) == expected


def test_watcher_returns_displayed_event_interactively():
    watcher = ActivityWatcher("fake")
    watcher._running = True

    assert watcher.wait_displayed("DmOrderActivity", 0.05) is False

    watcher.feed_line(
        "I ActivityTaskManager: Displayed cn.damai/.trade.order.DmOrderActivity: +1s"
    )
    assert watcher.wait_displayed("DmOrderActivity", 0.05) is True
    assert watcher.seen_displayed("DmOrderActivity") is True


def test_watcher_since_ignores_old_events():
    watcher = ActivityWatcher("fake")
    watcher._running = True
    watcher.feed_line(
        "I ActivityTaskManager: Displayed cn.damai/.trade.order.DmOrderActivity: +1s"
    )
    since = time.monotonic()
    time.sleep(0.002)

    assert watcher.wait_any_displayed(("DmOrderActivity",), 0.05, since=since) == ""
    watcher.feed_line(
        "I ActivityTaskManager: Displayed cn.damai/.trade.order.DmOrderActivity: +2s"
    )
    assert watcher.wait_any_displayed(("DmOrderActivity",), 0.05, since=since) == (
        "DmOrderActivity"
    )


def test_watcher_started_and_displayed_are_separate():
    watcher = ActivityWatcher("fake")
    watcher._running = True
    watcher.feed_line(
        "I ActivityTaskManager: START u0 {cmp=cn.damai/.sku.NcovSkuActivity}"
    )

    assert watcher.wait_any_started(("NcovSkuActivity",), 0.01) == "NcovSkuActivity"
    assert watcher.wait_any_displayed(("NcovSkuActivity",), 0.01) == ""


def test_watcher_stop_wakes_waiters():
    watcher = ActivityWatcher("fake")
    watcher._running = True
    started = time.monotonic()

    watcher.stop()

    assert watcher.wait_displayed("DmOrderActivity", 1.0) is False
    assert time.monotonic() - started < 0.5
