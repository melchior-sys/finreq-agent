"""Northwind Bank client-custom override — statement cycle date derivation.

Registered against `resolve_statement_cycle_dates` for client NWB. Adds the
England and Wales bank holiday calendar to the non-working-day test, which the
core implementation treats as weekends only.

SYNTHETIC SAMPLE CODE. Not runnable production code.
"""

import calendar
from datetime import date, timedelta
from typing import Any

NWB_BANK_HOLIDAYS_2026 = {
    date(2026, 1, 1),
    date(2026, 4, 3),
    date(2026, 4, 6),
    date(2026, 5, 4),
    date(2026, 5, 25),
    date(2026, 8, 31),
    date(2026, 12, 25),
    date(2026, 12, 28),
}


def resolve_statement_cycle_dates(account: dict[str, Any], as_of: date, config: Any) -> dict:
    """NWB cycle derivation, with bank holidays honoured in the roll."""
    anchor_day = config.get_int("STMT_CYCLE_ANCHOR_DAY", default=15)
    roll_rule = config.get_str("STMT_CYCLE_ROLL_RULE", default="FORWARD")
    tz_name = config.get_str("STMT_TIMEZONE", default="Europe/London")

    close = _anchor_in_month(as_of.year, as_of.month, anchor_day)
    close = _apply_roll(close, roll_rule, anchor_day)

    previous_close = account.get("last_cycle_close")
    if previous_close is None:
        raise ValueError("no previous cycle close on file for account")

    return {
        "cycle_start": previous_close + timedelta(days=1),
        "cycle_end": close,
        "timezone": tz_name,
        "cutoff": "23:59:59.999",
    }


def _anchor_in_month(year: int, month: int, anchor_day: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(anchor_day, last_day))


def _is_non_working_day(day: date) -> bool:
    return day.weekday() >= 5 or day in NWB_BANK_HOLIDAYS_2026


def _apply_roll(close: date, roll_rule: str, anchor_day: int) -> date:
    """SON-003 3.3.1 with 3.3.3 — a roll never crosses the following anchor."""
    if roll_rule == "NONE":
        return close
    step = 1 if roll_rule == "FORWARD" else -1
    rolled = close
    while _is_non_working_day(rolled):
        candidate = rolled + timedelta(days=step)
        if step == 1 and candidate.day >= anchor_day and candidate.month != close.month:
            return close
        rolled = candidate
    return rolled
