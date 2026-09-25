"""Core platform — ATM statement line selection.

Implements SON-001 section 3. This is the platform default used by every client
unless a client-custom builder is registered for the same function name.

SYNTHETIC SAMPLE CODE. Not runnable production code.
"""

from typing import Any

TERMINAL_STATES = {"SETTLED", "DROPPED", "EXPIRED", "REVERSED"}


def build_atm_statement_lines(auths: list[dict[str, Any]], config: Any) -> list[dict]:
    """Select the authorisations that become printed statement lines.

    SON-001 3.1.1  SETTLED activity in period becomes a statement line.
    SON-001 3.2.1  DROPPED and EXPIRED activity must not, subject to the two
                   independent exclusion flags in 3.2.2.
    SON-001 3.3.1  APPROVED activity is not a line (it goes to the memo block).
    SON-001 3.4.1  REVERSED activity is never presented.
    """
    exclude_dropped = config.get_bool("STMT_EXCLUDE_DROPPED_AUTHS", default=True)
    exclude_expired = config.get_bool("STMT_EXCLUDE_EXPIRED_AUTHS", default=True)

    lines = []
    for auth in auths:
        status = auth["status"]

        if status == "REVERSED":
            continue
        if status == "APPROVED":
            continue
        if status == "DROPPED" and exclude_dropped:
            continue
        if status == "EXPIRED" and exclude_expired:
            continue

        lines.append(
            {
                "auth_id": auth["auth_id"],
                "posted_ts": auth.get("settled_ts") or auth["auth_ts"],
                "description": _describe(auth),
                "amount_minor": auth["amount_minor"],
                "currency": auth["currency"],
            }
        )

    lines.sort(key=lambda line: line["posted_ts"])
    return lines


def build_memo_block(auths: list[dict[str, Any]], config: Any) -> list[dict]:
    """SON-001 3.3.2 — pending authorisations listed below the transaction table."""
    if not config.get_bool("STMT_MEMO_BLOCK_ENABLED", default=True):
        return []
    return [
        {"auth_id": a["auth_id"], "amount_minor": a["amount_minor"], "auth_ts": a["auth_ts"]}
        for a in auths
        if a["status"] == "APPROVED"
    ]


def paginate_lines(lines: list[dict], config: Any) -> list[list[dict]]:
    """SON-001 3.5.1 — fixed rows per page."""
    per_page = config.get_int("STMT_MAX_LINES_PER_PAGE", default=45)
    return [lines[i : i + per_page] for i in range(0, len(lines), per_page)]


def _describe(auth: dict[str, Any]) -> str:
    if auth["channel"] == "ATM":
        return f"ATM WITHDRAWAL {auth.get('terminal_id', '')}".strip()
    return auth.get("merchant_name", "CARD TRANSACTION")
