"""Authoritative usage-period calculation.

Policy (documented in docs/billing-and-plans.md):

* All periods are computed on the server in UTC.
* Accounts without a paid subscription use calendar months (UTC): quotas reset at
  00:00 UTC on the first day of each month.
* Paid subscriptions anchor monthly quota windows to the subscription's current billing
  period start reported by Paddle. Monthly plans therefore reset on renewal; annual plans
  reset every month on the anniversary day of the annual period (clamped to month end,
  e.g. an anchor on the 31st resets on Feb 28/29).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class UsagePeriod:
    start: datetime
    end: datetime
    key: str
    kind: str  # "calendar_month" | "subscription"

    def contains(self, ts: datetime) -> bool:
        return self.start <= ts < self.end


def _add_months(anchor: datetime, months: int) -> datetime:
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return anchor.replace(year=year, month=month, day=day)


def calendar_month(now: datetime) -> UsagePeriod:
    now = now.astimezone(UTC)
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    end = _add_months(start, 1)
    return UsagePeriod(start, end, f"m:{start:%Y-%m}", "calendar_month")


def subscription_month(anchor: datetime, now: datetime, period_end: datetime | None, sub_key: str) -> UsagePeriod:
    """Monthly window anchored at ``anchor`` that contains ``now``.

    ``anchor`` is the start of the current Paddle billing period. If ``now`` falls outside
    the reported period (stale data), we fall back to a calendar month rather than guess.
    """
    anchor = anchor.astimezone(UTC)
    now = now.astimezone(UTC)
    if now < anchor or (period_end is not None and now >= period_end.astimezone(UTC)):
        return calendar_month(now)
    i = 0
    start = anchor
    while True:
        nxt = _add_months(anchor, i + 1)
        if now < nxt:
            break
        i += 1
        start = nxt
        if i > 240:  # defensive bound (20 years)
            return calendar_month(now)
    end = _add_months(anchor, i + 1)
    if period_end is not None:
        end = min(end, period_end.astimezone(UTC))
    return UsagePeriod(start, end, f"s:{sub_key}:{start:%Y%m%dT%H%M%S}", "subscription")


def utc_day(now: datetime) -> UsagePeriod:
    now = now.astimezone(UTC)
    start = datetime(now.year, now.month, now.day, tzinfo=UTC)
    return UsagePeriod(start, start + timedelta(days=1), f"d:{start:%Y-%m-%d}", "day")


LIFETIME = UsagePeriod(datetime(1970, 1, 1, tzinfo=UTC), datetime(9999, 1, 1, tzinfo=UTC), "lifetime", "lifetime")
LIVE = UsagePeriod(datetime(1970, 1, 1, tzinfo=UTC), datetime(9999, 1, 1, tzinfo=UTC), "live", "live")
