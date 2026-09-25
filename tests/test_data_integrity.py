"""Guards on the synthetic data and the model layer.

These are not tests of the agent — there is no agent yet. They protect the two things
slice 1 is responsible for getting right:

* the seed data still encodes the scenarios the evals depend on, in particular that
  `STMT_EXCLUDE_DROPPED_AUTHS` exists in the database and is read by the core code but
  not by the Northwind custom override;
* the code snippet registry's line ranges still bracket the functions they claim to,
  so `compare_core_vs_custom` will read the right lines in slice 3.

The parameter scan below is a deliberately minimal stand-in for the real one that
ships with the tool in slice 3. It states the invariant now so that an innocent edit
to the sample code cannot silently dissolve the key scenario.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.build_db import DEFAULT_SEED, build  # noqa: E402
from src.models import (  # noqa: E402
    Evidence,
    EvidenceKind,
    StopReason,
    Ticket,
    Verdict,
    VerdictSubmission,
    check_evidence_gate,
    compute_confidence,
    is_grounded,
    normalise_for_match,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def conn(tmp_path_factory) -> sqlite3.Connection:
    db_path = tmp_path_factory.mktemp("db") / "db.sqlite"
    connection = build(DEFAULT_SEED, db_path)
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


def params_read_in_range(path: str, start_line: int, end_line: int, known_params: set[str]) -> set[str]:
    """Which known parameter names appear inside the given line range of a file."""
    lines = (PROJECT_ROOT / path).read_text(encoding="utf-8").splitlines()
    window = "\n".join(lines[start_line - 1 : end_line])
    return {name for name in known_params if name in window}


@pytest.fixture(scope="module")
def known_params(conn) -> set[str]:
    return {row["name"] for row in conn.execute("SELECT DISTINCT name FROM config_params")}


# ------------------------------------------------------------------ seed sanity


def test_seed_builds_with_expected_row_counts(conn):
    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ["clients", "config_params", "auth_records", "son_docs", "code_snippets"]
    }
    assert counts["clients"] == 3
    assert counts["config_params"] >= 11
    assert counts["auth_records"] >= 40
    assert counts["son_docs"] == 3
    assert counts["code_snippets"] == 6


def test_son_documents_referenced_by_the_database_exist_on_disk(conn):
    for row in conn.execute("SELECT son_id, path FROM son_docs"):
        assert (PROJECT_ROOT / row["path"]).is_file(), f"{row['son_id']} points at a missing file"


def test_demo_tickets_validate(conn):
    ticket_files = sorted((PROJECT_ROOT / "data" / "tickets").glob("*.json"))
    assert len(ticket_files) == 3
    client_ids = {row["client_id"] for row in conn.execute("SELECT client_id FROM clients")}
    for path in ticket_files:
        ticket = Ticket.from_json_file(path)
        assert ticket.client_id in client_ids


# ----------------------------------------------------------- the key scenario


def test_registry_line_ranges_bracket_their_functions(conn):
    for row in conn.execute("SELECT * FROM code_snippets"):
        lines = (PROJECT_ROOT / row["path"]).read_text(encoding="utf-8").splitlines()
        assert row["end_line"] <= len(lines), f"{row['path']} is shorter than its registered range"
        window = lines[row["start_line"] - 1 : row["end_line"]]
        assert window[0].startswith(f"def {row['function_name']}("), (
            f"{row['path']}:{row['start_line']} does not start {row['function_name']}"
        )


def test_dropped_auth_parameter_exists_for_northwind(conn):
    row = conn.execute(
        "SELECT * FROM config_params WHERE client_id = 'NWB' AND name = 'STMT_EXCLUDE_DROPPED_AUTHS'"
    ).fetchone()
    assert row is not None, "the key scenario needs this parameter present in the database"
    assert row["value"] == "true"


def test_custom_statement_builder_ignores_the_dropped_auth_parameter(conn, known_params):
    """The scenario the whole demo turns on."""
    rows = {
        row["layer"]: row
        for row in conn.execute(
            "SELECT * FROM code_snippets WHERE function_name = 'build_atm_statement_lines'"
        )
    }
    core = params_read_in_range(
        rows["core"]["path"], rows["core"]["start_line"], rows["core"]["end_line"], known_params
    )
    custom = params_read_in_range(
        rows["custom"]["path"], rows["custom"]["start_line"], rows["custom"]["end_line"], known_params
    )

    assert "STMT_EXCLUDE_DROPPED_AUTHS" in core
    assert "STMT_EXCLUDE_EXPIRED_AUTHS" in core
    assert "STMT_EXCLUDE_EXPIRED_AUTHS" in custom
    assert core - custom == {"STMT_EXCLUDE_DROPPED_AUTHS"}

    # It must not appear anywhere else in the custom file either, or the tool's scan
    # would report it as read when it is only mentioned.
    custom_file = (PROJECT_ROOT / rows["custom"]["path"]).read_text(encoding="utf-8")
    assert "STMT_EXCLUDE_DROPPED_AUTHS" not in custom_file


def test_fee_engine_is_a_decoy_not_a_defect(conn, known_params):
    """Custom diverges from core here legitimately, so divergence alone cannot mean BUG."""
    rows = {
        row["layer"]: row
        for row in conn.execute(
            "SELECT * FROM code_snippets WHERE function_name = 'calculate_monthly_fee_waiver'"
        )
    }
    core = params_read_in_range(
        rows["core"]["path"], rows["core"]["start_line"], rows["core"]["end_line"], known_params
    )
    custom = params_read_in_range(
        rows["custom"]["path"], rows["custom"]["start_line"], rows["custom"]["end_line"], known_params
    )
    assert core - custom == set(), "custom should be a superset here"
    assert "FEE_SALARY_CREDIT_MIN" in custom - core


def test_no_tier_or_age_waiver_parameter_exists(conn):
    """SON-002 5.1 and 5.2 put these out of scope, so the CR case needs them absent."""
    # Note: underscore is a single-character wildcard in LIKE, so patterns here are kept
    # to whole words that cannot collide with e.g. STMT_MAX_LINES_PER_PAGE.
    rows = conn.execute(
        "SELECT name FROM config_params WHERE name LIKE '%TIER%' OR name LIKE '%SENIOR%'"
    ).fetchall()
    assert [row["name"] for row in rows] == []


def test_dropped_atm_authorisations_exist_for_the_complaining_card(conn):
    rows = conn.execute(
        """
        SELECT * FROM auth_records
        WHERE client_id = 'NWB' AND card_last4 = '4417' AND channel = 'ATM'
          AND status = 'DROPPED' AND auth_ts BETWEEN '2026-02-15' AND '2026-03-15'
        """
    ).fetchall()
    assert len(rows) == 4


# ------------------------------------------------------------------- grounding


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("line one\\nline two", "line one line two"),
        ('he said \\"exclude\\"', 'he said "exclude"'),
        ("  spaced   out \n text ", "spaced out text"),
        ("curly ‘quotes’ and — dashes", "curly 'quotes' and - dashes"),
        ("no escapes here", "no escapes here"),
        ("a windows\\path\\that is not json", "a windows\\path\\that is not json"),
    ],
)
def test_normalisation(raw, expected):
    assert normalise_for_match(raw) == expected


def test_grounding_survives_json_escaping_and_rewrapping():
    tool_output = (
        "3.2.1 Authorisations in DROPPED or EXPIRED state MUST NOT appear as statement\n"
        "lines on ATM statements."
    )
    quote_as_the_model_echoes_it = (
        "Authorisations in DROPPED or EXPIRED state MUST NOT appear as statement\\nlines"
    )
    assert is_grounded(quote_as_the_model_echoes_it, [tool_output])
    assert not is_grounded("Authorisations in DROPPED state may appear", [tool_output])


# ------------------------------------------------------- gate and confidence


def _evidence(kind: EvidenceKind, quote: str) -> Evidence:
    return Evidence(kind=kind, ref="ref", quote=quote, why_it_matters="because")


def test_gate_rejects_a_bug_verdict_with_only_a_son_citation():
    submission = VerdictSubmission(
        verdict=Verdict.BUG,
        rationale="r",
        evidence=[_evidence(EvidenceKind.SON, "MUST NOT appear")],
        draft_reply="d",
    )
    gate = check_evidence_gate(submission, ["MUST NOT appear"])
    assert not gate.passed
    assert any("non-SON" in f for f in gate.failures)


def test_gate_flags_an_invented_quote():
    submission = VerdictSubmission(
        verdict=Verdict.BUG,
        rationale="r",
        evidence=[
            _evidence(EvidenceKind.SON, "MUST NOT appear"),
            _evidence(EvidenceKind.CODE, "this text was never returned by a tool"),
        ],
        draft_reply="d",
    )
    gate = check_evidence_gate(submission, ["MUST NOT appear"])
    assert not gate.passed
    assert gate.ungrounded_quotes == ["this text was never returned by a tool"]


def test_gate_passes_a_well_supported_bug():
    outputs = ["MUST NOT appear", "STMT_EXCLUDE_DROPPED_AUTHS = true", "4 DROPPED ATM records"]
    submission = VerdictSubmission(
        verdict=Verdict.BUG,
        rationale="r",
        evidence=[
            _evidence(EvidenceKind.SON, "MUST NOT appear"),
            _evidence(EvidenceKind.CONFIG, "STMT_EXCLUDE_DROPPED_AUTHS = true"),
            _evidence(EvidenceKind.DATA, "4 DROPPED ATM records"),
        ],
        draft_reply="d",
    )
    gate = check_evidence_gate(submission, outputs)
    assert gate.passed
    assert gate.son_citations == 1
    assert gate.corroborating_kinds == ["config", "data"]


def test_needs_info_requires_a_question():
    submission = VerdictSubmission(verdict=Verdict.NEEDS_INFO, rationale="r", draft_reply="d")
    assert not check_evidence_gate(submission, []).passed


def test_the_model_cannot_report_its_own_confidence():
    with pytest.raises(ValueError):
        VerdictSubmission(
            verdict=Verdict.BUG, rationale="r", draft_reply="d", confidence=0.99
        )


def test_confidence_rewards_breadth_and_punishes_a_forced_stop():
    outputs = ["a", "b", "c"]
    broad = VerdictSubmission(
        verdict=Verdict.BUG,
        rationale="r",
        evidence=[
            _evidence(EvidenceKind.SON, "a"),
            _evidence(EvidenceKind.CONFIG, "b"),
            _evidence(EvidenceKind.DATA, "c"),
        ],
        draft_reply="d",
    )
    narrow = VerdictSubmission(
        verdict=Verdict.BUG,
        rationale="r",
        evidence=[_evidence(EvidenceKind.SON, "a"), _evidence(EvidenceKind.CONFIG, "b")],
        draft_reply="d",
    )

    broad_gate = check_evidence_gate(broad, outputs)
    narrow_gate = check_evidence_gate(narrow, outputs)

    clean = compute_confidence(Verdict.BUG, broad_gate, StopReason.VERDICT)
    thinner = compute_confidence(Verdict.BUG, narrow_gate, StopReason.VERDICT)
    cut_short = compute_confidence(Verdict.BUG, broad_gate, StopReason.STEP_BUDGET)
    retried = compute_confidence(Verdict.BUG, broad_gate, StopReason.VERDICT, gate_retries=1)

    assert clean > thinner > 0
    assert clean > cut_short
    assert clean > retried
    assert 0.0 <= cut_short <= 1.0


def test_a_failed_gate_floors_confidence():
    submission = VerdictSubmission(verdict=Verdict.NEEDS_INFO, rationale="r", draft_reply="d")
    gate = check_evidence_gate(submission, [])
    assert compute_confidence(Verdict.NEEDS_INFO, gate, StopReason.VERDICT) == pytest.approx(0.05)
