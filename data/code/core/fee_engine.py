"""Core platform — monthly maintenance fee waiver evaluation.

Implements SON-002 section 3.2 and the cap in 3.4.

SYNTHETIC SAMPLE CODE. Not runnable production code.
"""

from typing import Any

FEE_MAINTENANCE_MINOR = 400
ATM_FEE_MINOR = 150
FREE_OUT_OF_NETWORK_WITHDRAWALS = 2


def calculate_monthly_fee_waiver(account: dict[str, Any], cycle: dict[str, Any], config: Any) -> dict:
    """Decide whether the monthly maintenance fee is waived for this cycle.

    SON-002 3.2.1(a)  waive on average balance.
    SON-002 3.4.1     never exceed the per-cycle waiver cap.
    """
    min_avg_balance = config.get_int("FEE_WAIVER_MIN_AVG_BALANCE", default=250000)
    max_per_cycle = config.get_int("FEE_WAIVER_MAX_PER_CYCLE", default=3)

    waivers_used = cycle.get("waivers_granted", 0)
    if waivers_used >= max_per_cycle:
        return {"waived": False, "reason_code": "WAIVER_LIMIT_REACHED", "fee_minor": FEE_MAINTENANCE_MINOR}

    if account["avg_balance_minor"] >= min_avg_balance:
        return {"waived": True, "reason_code": "AVG_BALANCE_MET", "fee_minor": 0}

    return {"waived": False, "reason_code": "NO_WAIVER_CONDITION_MET", "fee_minor": FEE_MAINTENANCE_MINOR}


def calculate_out_of_network_atm_fee(withdrawal: dict[str, Any], cycle: dict[str, Any]) -> dict:
    """Charge the out-of-network ATM fee, waiving the first two of the cycle.

    SON-002 3.3.1  GBP 1.50 per out-of-network ATM withdrawal.
    SON-002 3.3.2  the first two qualifying withdrawals in a cycle are waived.
    """
    already_waived = cycle.get("atm_fee_waivers_used", 0)

    if already_waived < FREE_OUT_OF_NETWORK_WITHDRAWALS:
        return {"waived": True, "reason_code": "FREE_WITHDRAWAL_ALLOWANCE", "fee_minor": 0}

    return {"waived": False, "reason_code": "ALLOWANCE_EXHAUSTED", "fee_minor": ATM_FEE_MINOR}
