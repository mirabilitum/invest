"""Trading calendar provider for US (XNYS), HK (XHKG), and A-share (XSHG) markets.

Since trading-calendars cannot be installed, this module provides a practical
approximation using weekday checks and hardcoded major holiday sets.
"""

import logging
from datetime import date, datetime, timedelta

log = logging.getLogger("invest.calendar")

# ---------------------------------------------------------------------------
# Major holidays per market (2025-2026)
# ---------------------------------------------------------------------------

_US_HOLIDAYS = {
    date(2025, 1, 1),
    date(2025, 1, 20),
    date(2025, 2, 17),
    date(2025, 4, 18),
    date(2025, 5, 26),
    date(2025, 6, 19),
    date(2025, 7, 4),
    date(2025, 9, 1),
    date(2025, 11, 27),
    date(2025, 12, 25),
    date(2026, 1, 1),
    date(2026, 1, 19),
    date(2026, 2, 16),
    date(2026, 4, 3),
    date(2026, 5, 25),
    date(2026, 6, 19),
    date(2026, 7, 3),
    date(2026, 9, 7),
    date(2026, 11, 26),
    date(2026, 12, 25),
}

_HK_HOLIDAYS = {
    date(2025, 1, 1),
    date(2025, 1, 29),
    date(2025, 1, 30),
    date(2025, 1, 31),
    date(2025, 4, 4),
    date(2025, 4, 18),
    date(2025, 4, 19),
    date(2025, 4, 21),
    date(2025, 5, 1),
    date(2025, 5, 5),
    date(2025, 5, 31),
    date(2025, 6, 2),
    date(2025, 7, 1),
    date(2025, 10, 1),
    date(2025, 10, 6),
    date(2025, 10, 7),
    date(2025, 10, 29),
    date(2025, 12, 25),
    date(2025, 12, 26),
    date(2026, 1, 1),
    date(2026, 2, 17),
    date(2026, 2, 18),
    date(2026, 2, 19),
    date(2026, 4, 3),
    date(2026, 4, 4),
    date(2026, 4, 6),
    date(2026, 4, 7),
    date(2026, 5, 1),
    date(2026, 5, 24),
    date(2026, 6, 1),
    date(2026, 7, 1),
    date(2026, 9, 19),
    date(2026, 10, 1),
    date(2026, 10, 6),
    date(2026, 10, 18),
    date(2026, 12, 25),
}

_A_SHARE_HOLIDAYS = {
    date(2025, 1, 1),
    date(2025, 1, 28),
    date(2025, 1, 29),
    date(2025, 1, 30),
    date(2025, 1, 31),
    date(2025, 2, 3),
    date(2025, 2, 4),
    date(2025, 4, 4),
    date(2025, 4, 5),
    date(2025, 5, 1),
    date(2025, 5, 2),
    date(2025, 5, 5),
    date(2025, 5, 31),
    date(2025, 10, 1),
    date(2025, 10, 2),
    date(2025, 10, 3),
    date(2025, 10, 6),
    date(2025, 10, 7),
    date(2025, 10, 8),
    date(2026, 1, 1),
    date(2026, 1, 2),
    date(2026, 2, 16),
    date(2026, 2, 17),
    date(2026, 2, 18),
    date(2026, 2, 19),
    date(2026, 2, 20),
    date(2026, 2, 23),
    date(2026, 3, 8),
    date(2026, 4, 4),
    date(2026, 4, 5),
    date(2026, 5, 1),
    date(2026, 5, 4),
    date(2026, 5, 5),
    date(2026, 5, 31),
    date(2026, 6, 1),
    date(2026, 10, 1),
    date(2026, 10, 2),
    date(2026, 10, 5),
    date(2026, 10, 6),
    date(2026, 10, 7),
    date(2026, 10, 8),
}

_HOLIDAYS = {
    "us": _US_HOLIDAYS,
    "hk": _HK_HOLIDAYS,
    "a_share": _A_SHARE_HOLIDAYS,
}


def is_trading_day(market: str, dt: date = None) -> bool:
    """Check if given date (default: today) is a trading day for the market."""
    if dt is None:
        dt = date.today()
    holidays = _HOLIDAYS.get(market, set())
    if dt.weekday() >= 5:  # Saturday or Sunday
        return False
    if dt in holidays:
        return False
    return True


def next_trading_day(market: str, dt: date = None) -> date:
    """Return the next trading day on or after dt."""
    if dt is None:
        dt = date.today()
    while not is_trading_day(market, dt):
        dt = dt + timedelta(days=1)
    return dt


def all_markets_open() -> bool:
    """True if at least one of US/HK/A-share is trading today."""
    return any(is_trading_day(m) for m in ["us", "hk", "a_share"])
