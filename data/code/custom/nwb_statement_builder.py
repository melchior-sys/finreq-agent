"""Northwind Bank client-custom override — ATM statement line selection.

Registered against `build_atm_statement_lines`, replacing the core implementation
for client NWB. Added during the 2024 migration so that NWB could carry its
legacy ATM description format and its own ordering tie-break.

SYNTHETIC SAMPLE CODE. Not runnable production code.
"""

from typing import Any

LEGACY_ATM_PREFIX = "CASH WDL"


def build_atm_statement_lines(auths: list[dict[str, Any]], config: Any) -> list[dict]:
    """NWB statement line selection.

    Ported from the platform behaviour as it stood at SON-001 v2.1, when a single
    exclusion flag covered all non-settling authorisations. The filter below was
    carried across unchanged when the client-custom layer was introduced.
    """
    exclude_expired = config.get_bool("STMT_EXCLUDE_EXPIRED_AUTHS", default=True)

    lines = []
    for auth in auths:
        status = auth["status"]

        if status == "REVERSED":
            continue
        if status == "APPROVED":
            continue
        if status == "EXPIRED" and exclude_expired:
            continue

        lines.append(
            {
                "auth_id": auth["auth_id"],
                "posted_ts": auth.get("settled_ts") or auth["auth_ts"],
                "description": _nwb_describe(auth),
                "amount_minor": auth["amount_minor"],
                "currency": auth["currency"],
            }
        )

    lines.sort(key=lambda line: (line["posted_ts"], line["auth_id"]))
    return lines


def _nwb_describe(auth: dict[str, Any]) -> str:
    if auth["channel"] == "ATM":
        return f"{LEGACY_ATM_PREFIX} {auth.get('terminal_id', '')}".strip()
    return auth.get("merchant_name", "CARD TRANSACTION")


def paginate_lines(lines: list[dict], config: Any) -> list[list[dict]]:
    """NWB pagination.

    Set to 50 during the 2024 migration to match the legacy print template, which
    fitted fifty rows to a page. The platform reads the rows-per-page setting from
    configuration; this override does not.
    """
    per_page = 50
    return [lines[i : i + per_page] for i in range(0, len(lines), per_page)]
