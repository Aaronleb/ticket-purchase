"""把开售墙上时间映射到单调时钟，并执行两段精准等待。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


SHANGHAI = timezone(timedelta(hours=8))
COARSE_REMAINING_SECONDS = 0.25
FINAL_SLEEP_SECONDS = 0.005


class SaleTimeError(ValueError):
    """抢购时间配置或等待失败。"""


class SaleTimePast(SaleTimeError):
    """抢购时间已经过期。"""


def parse_sale_time(value) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SaleTimeError("请填写抢购时间")
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SaleTimeError(f"抢购时间格式无效：{raw}") from exc
    if "T" not in raw and "t" not in raw:
        raise SaleTimeError("抢购时间必须包含日期和时间")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI)


def normalize_sale_time(value) -> str:
    return parse_sale_time(value).isoformat()


@dataclass(frozen=True)
class SaleRelease:
    target_time: datetime
    released_at: datetime
    target_monotonic: float
    released_monotonic: float

    @property
    def delay_ms(self) -> float:
        return (self.released_monotonic - self.target_monotonic) * 1000


def wait_for_sale_time(value, *, monotonic=None, sleep=None, wall_now=None) -> SaleRelease:
    """等待到目标时间；时钟函数可注入，以便无真实等待地测试。"""
    monotonic = monotonic or time.monotonic
    sleep = sleep or time.sleep
    wall_now = wall_now or (lambda: datetime.now(SHANGHAI))
    target = parse_sale_time(value)

    anchor_before = monotonic()
    anchor_wall = wall_now()
    anchor_after = monotonic()
    if anchor_wall.tzinfo is None:
        anchor_wall = anchor_wall.replace(tzinfo=SHANGHAI)
    else:
        anchor_wall = anchor_wall.astimezone(SHANGHAI)
    anchor_monotonic = (anchor_before + anchor_after) / 2.0
    remaining = (target - anchor_wall).total_seconds()
    if remaining <= 0:
        raise SaleTimePast(
            f"抢购时间 {target.isoformat()} 已过，请修改后重新启动"
        )

    target_monotonic = anchor_monotonic + remaining
    coarse_sleep = target_monotonic - COARSE_REMAINING_SECONDS - monotonic()
    if coarse_sleep > 0:
        sleep(coarse_sleep)

    while True:
        remaining = target_monotonic - monotonic()
        if remaining <= 0:
            break
        sleep(min(FINAL_SLEEP_SECONDS, remaining))

    released_monotonic = monotonic()
    released_at = wall_now()
    if released_at.tzinfo is None:
        released_at = released_at.replace(tzinfo=SHANGHAI)
    else:
        released_at = released_at.astimezone(SHANGHAI)
    return SaleRelease(
        target_time=target,
        released_at=released_at,
        target_monotonic=target_monotonic,
        released_monotonic=released_monotonic,
    )
