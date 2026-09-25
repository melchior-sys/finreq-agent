"""Northwind Bank client-custom override — monthly maintenance fee waiver.

Registered against `calculate_monthly_fee_waiver` for client NWB. Extends the
core rule with the salary-credit condition introduced in SON-002 v1.6, which NWB
required ahead of the platform release train.

SYNTHETIC SAMPLE CODE. Not runnable production code.
"""

from typing import Any

FEE_MAINTENANCE_MINOR = 400


def calculate_monthly_fee_waiver(account: dict[str, Any], cycle: dict[str, Any], config: Any) -> dict:
    """NWB waiver evaluation.

    SON-002 3.2.1(a)  waive on average balance.
    SON-002 3.2.1(b)  waive on a qualifying salary credit.
    SON-002 3.2.2     the two conditions are not cumulative — one waiver only.
    SON-002 3.4.1     never exceed the per-cycle waiver cap.
    """
    min_avg_balance = config.get_int("FEE_WAIVER_MIN_AVG_BALANCE", default=250000)
    min_salary_credit = config.get_int("FEE_SALARY_CREDIT_MIN", default=150000)
    max_per_cycle = config.get_int("FEE_WAIVER_MAX_PER_CYCLE", default=3)

    waivers_used = cycle.get("waivers_granted", 0)
    if waivers_used >= max_per_cycle:
        return {"waived": False, "reason_code": "WAIVER_LIMIT_REACHED", "fee_minor": FEE_MAINTENANCE_MINOR}

    if account["avg_balance_minor"] >= min_avg_balance:
        return {"waived": True, "reason_code": "AVG_BALANCE_MET", "fee_minor": 0}

    if _best_salary_credit(cycle) >= min_salary_credit:
        return {"waived": True, "reason_code": "SALARY_CREDIT_MET", "fee_minor": 0}

    return {"waived": False, "reason_code": "NO_WAIVER_CONDITION_MET", "fee_minor": FEE_MAINTENANCE_MINOR}


def _best_salary_credit(cycle: dict[str, Any]) -> int:
    credits = [
        c["amount_minor"]
        for c in cycle.get("inbound_credits", [])
        if c.get("originator_category") in {"SALARY", "PENSION"}
    ]
    return max(credits, default=0)
