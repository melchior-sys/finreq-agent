"""Core platform — statement cycle date derivation.

Implements SON-003 section 3.

SYNTHETIC SAMPLE CODE. Not runnable production code.
"""

import calendar
from datetime import date, timedelta
from typing import Any


def resolve_statement_cycle_dates(account: dict[str, Any], as_of: date, config: Any) -> dict:
    """Return the start and end dates of the cycle containing `as_of`.

    SON-003 3.1.1  anchor day comes from configuration.
    SON-003 3.2.1  an anchor that does not exist in the month falls to month end.
    SON-003 3.3.1  a close on a non-working day rolls per the configured rule.
    SON-003 3.4.1  boundaries are evaluated in the configured timezone.
    """
    anchor_day = config.get_int("STMT_CYCLE_ANCHOR_DAY", default=15)
    roll_rule = config.get_str("STMT_CYCLE_ROLL_RULE", default="FORWARD")
    tz_name = config.get_str("STMT_TIMEZONE", default="Europe/London")

    close = _anchor_in_month(as_of.year, as_of.month, anchor_day)
    close = _apply_roll(close, roll_rule)

    previous_close = account.get("last_cycle_close")
    if previous_close is None:
        # Newly opened account, no prior close on file. SON-003 3.1.2 bounds a cycle
        # against the previous close and says nothing about how the first cycle of a
        # new account is bounded, so the platform falls back to the opening date.
        # An account opened shortly before the anchor therefore gets a very short
        # first statement. Raised with Product 2025-04; unanswered.
        cycle_start = account["opened_on"]
    else:
        cycle_start = previous_close + timedelta(days=1)

    return {
        "cycle_start": cycle_start,
        "cycle_end": close,
        "timezone": tz_name,
        "cutoff": "23:59:59.999",
    }


def _anchor_in_month(year: int, month: int, anchor_day: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(anchor_day, last_day))


def _apply_roll(close: date, roll_rule: str) -> date:
    if roll_rule == "NONE":
        return close
    step = 1 if roll_rule == "FORWARD" else -1
    rolled = close
    while rolled.weekday() >= 5:
        rolled += timedelta(days=step)
    return rolled
