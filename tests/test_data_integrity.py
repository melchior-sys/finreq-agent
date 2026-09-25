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
    ToolObservation,
    Verdict,
    VerdictSubmission,
    check_citation,
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


def test_first_cycle_period_is_unspecified_but_does_not_crash(conn):
    """Ticket NWB-4502 is a NEEDS_INFO, and it has to earn that.

    The code makes a choice the spec never settles: with no previous close on file it
    falls back to the account opening date, which hands a customer opened on the 13th a
    two-day first statement. Nothing errors, so there is no defect to point at, and
    SON-003 says nothing about the first cycle, so there is no requirement to point at
    either. The only honest verdict is to ask.
    """
    son = (PROJECT_ROOT / "data/sons/SON-003-statement-cycle-dates.md").read_text(encoding="utf-8").lower()
    for phrase in ["first cycle", "first statement", "newly opened", "account opening"]:
        assert phrase not in son, f"SON-003 now covers {phrase!r}; NWB-4502 is no longer NEEDS_INFO"

    rows = conn.execute(
        "SELECT * FROM code_snippets WHERE function_name = 'resolve_statement_cycle_dates'"
    ).fetchall()
    assert len(rows) == 2
    for row in rows:
        lines = (PROJECT_ROOT / row["path"]).read_text(encoding="utf-8").splitlines()
        window = "\n".join(lines[row["start_line"] - 1 : row["end_line"]])
        assert "opened_on" in window, f"{row['layer']} must fall back to the opening date"
        assert "raise" not in window, (
            f"{row['layer']} must not error on a first cycle — a job that produces nothing "
            "is a defect regardless of what the spec says, which would make the ticket a BUG"
        )


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


def test_grounding_works_against_a_pretty_printed_tool_result():
    """Regression.

    Tool results reach the model as pretty-printed JSON, which is full of real
    newlines. A raw newline is illegal inside a JSON string literal, so the attempt
    to unescape a whole result used to raise, get swallowed, and return the text
    still escaped — at which case every genuine quote from a code snippet looked
    invented and the evidence gate rejected correct verdicts.
    """
    import json as _json

    payload = _json.dumps(
        {"snippet": 'exclude = config.get_bool("FLAG", default=True)\nreturn exclude'},
        ensure_ascii=False,
        indent=2,
    )
    assert "\\n" in payload and '\\"' in payload  # the shape that used to break it
    assert is_grounded('config.get_bool("FLAG", default=True)', [payload])


def test_grounding_tolerates_json_reformatting_but_not_rewording():
    """Regression from the first live run.

    A pretty-printed aggregate reaches the model across several lines; the model
    quotes it back compactly. Identical content, two spaces of difference — and the
    strict substring test rejected a correct BUG verdict twice over it. Rewording is
    still caught: the check loosens formatting, never content.
    """
    pretty = '''  "aggregates": {
    "by_status": {
      "APPROVED": 1,
      "DROPPED": 4,
      "SETTLED": 2
    }
  }'''
    assert is_grounded('"by_status": {"APPROVED": 1, "DROPPED": 4, "SETTLED": 2}', [pretty])
    assert is_grounded('"DROPPED": 4', [pretty])
    assert not is_grounded('"DROPPED": 5', [pretty])
    assert not is_grounded('"by_status": {"APPROVED": 1, "DROPPED": 40}', [pretty])


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


SON_OBS = ToolObservation.of(
    "search_son",
    {"chunks": [{"chunk_id": "SON-001#3.2", "text": "MUST NOT appear as statement lines"}]},
    '{"chunks": [{"chunk_id": "SON-001#3.2", "text": "MUST NOT appear as statement lines"}]}',
)
DROPPED_OBS = ToolObservation.of(
    "get_config_param",
    {"param": {"name": "STMT_EXCLUDE_DROPPED_AUTHS", "value": "true"}},
    '{"param": {"name": "STMT_EXCLUDE_DROPPED_AUTHS", "value": "true"}}',
)
ANCHOR_OBS = ToolObservation.of(
    "get_config_param",
    {"param": {"name": "STMT_CYCLE_ANCHOR_DAY", "value": "15"}},
    '{"param": {"name": "STMT_CYCLE_ANCHOR_DAY", "value": "15"}}',
)
CODE_OBS = ToolObservation.of(
    "compare_core_vs_custom",
    {
        "core": {"path": "data/code/core/statement_builder.py", "snippet": "exclude_dropped = x"},
        "custom": {"path": "data/code/custom/nwb_statement_builder.py",
                   "snippet": "exclude_expired = y"},
    },
    '{"core": {"path": "data/code/core/statement_builder.py", "snippet": "exclude_dropped = x"}, '
    '"custom": {"path": "data/code/custom/nwb_statement_builder.py", '
    '"snippet": "exclude_expired = y"}}',
)
DATA_OBS = ToolObservation.of(
    "get_auth_records",
    {"count": 4, "aggregates": {"by_status": {"DROPPED": 4}}},
    '{"count": 4, "aggregates": {"by_status": {"DROPPED": 4}}}',
)
ALL_OBS = [SON_OBS, DROPPED_OBS, ANCHOR_OBS, CODE_OBS, DATA_OBS]


def _evidence(kind: EvidenceKind, ref: str, quote: str) -> Evidence:
    return Evidence(kind=kind, ref=ref, quote=quote, why_it_matters="because")


SON_EV = _evidence(EvidenceKind.SON, "SON-001#3.2", "MUST NOT appear as statement lines")
CONFIG_EV = _evidence(EvidenceKind.CONFIG, "STMT_EXCLUDE_DROPPED_AUTHS", '"value": "true"')
DATA_EV = _evidence(EvidenceKind.DATA, "card 4417 ATM", '"DROPPED": 4')


def test_gate_rejects_a_bug_verdict_with_only_a_son_citation():
    submission = VerdictSubmission(
        verdict=Verdict.BUG, rationale="r", evidence=[SON_EV], draft_reply="d"
    )
    gate = check_evidence_gate(submission, ALL_OBS)
    assert not gate.passed
    assert any("non-SON" in f for f in gate.failures)


def test_gate_flags_an_invented_quote():
    invented = _evidence(
        EvidenceKind.CODE,
        "data/code/core/statement_builder.py",
        "this text was never returned by a tool",
    )
    submission = VerdictSubmission(
        verdict=Verdict.BUG, rationale="r", evidence=[SON_EV, invented], draft_reply="d"
    )
    gate = check_evidence_gate(submission, ALL_OBS)
    assert not gate.passed
    assert gate.ungrounded_quotes == ["this text was never returned by a tool"]


def test_gate_passes_a_well_supported_bug():
    submission = VerdictSubmission(
        verdict=Verdict.BUG, rationale="r", evidence=[SON_EV, CONFIG_EV, DATA_EV], draft_reply="d"
    )
    gate = check_evidence_gate(submission, ALL_OBS)
    assert gate.passed, gate.failures
    assert gate.son_citations == 1
    assert gate.corroborating_kinds == ["config", "data"]


# ------------------------------------------- citations must match their source


def test_a_config_quote_from_the_wrong_parameter_is_rejected():
    """The hole this closes.

    Both lookups are real and both quotes are real: every word of the citation
    appeared in some tool result during the run. But the text is from the
    dropped-auth lookup, while the citation attributes it to the cycle anchor day,
    whose value is 15. Checking a quote against every result the run has seen calls
    that grounded. Checking it against the result its ref names does not.
    """
    misattributed = _evidence(EvidenceKind.CONFIG, "STMT_CYCLE_ANCHOR_DAY", '"value": "true"')
    submission = VerdictSubmission(
        verdict=Verdict.BUG, rationale="r", evidence=[SON_EV, misattributed], draft_reply="d"
    )
    gate = check_evidence_gate(submission, ALL_OBS)

    assert not gate.passed
    assert any("STMT_CYCLE_ANCHOR_DAY" in failure for failure in gate.failures)
    assert is_grounded('"value": "true"', [obs.text for obs in ALL_OBS]), (
        "the quote really is somewhere in the run, which is why the old check passed it"
    )


def test_a_quote_attributed_to_the_wrong_kind_of_source_is_rejected():
    """An authorisation count dressed up as a specification requirement."""
    dressed_up = _evidence(EvidenceKind.SON, "SON-001#3.2", '"DROPPED": 4')
    submission = VerdictSubmission(
        verdict=Verdict.BUG, rationale="r", evidence=[dressed_up, CONFIG_EV], draft_reply="d"
    )
    assert not check_evidence_gate(submission, ALL_OBS).passed


def test_a_citation_from_a_tool_the_run_never_called_is_rejected():
    submission = VerdictSubmission(
        verdict=Verdict.BUG, rationale="r", evidence=[SON_EV, CONFIG_EV], draft_reply="d"
    )
    gate = check_evidence_gate(submission, [SON_OBS])
    assert not gate.passed
    assert any("never called get_config_param" in failure for failure in gate.failures)


def test_a_sub_clause_of_the_retrieved_chunk_counts():
    """Retrieval returns 3.2; citing the 3.2.1 it rested on is a better citation."""
    precise = _evidence(EvidenceKind.SON, "SON-001#3.2.1", "MUST NOT appear as statement lines")
    assert check_citation(precise, [SON_OBS]) is None

    elsewhere = _evidence(EvidenceKind.SON, "SON-002#5", "MUST NOT appear as statement lines")
    assert check_citation(elsewhere, [SON_OBS]) is not None


def test_a_code_citation_must_name_a_file_that_was_actually_read():
    real = _evidence(
        EvidenceKind.CODE, "data/code/custom/nwb_statement_builder.py:L15-47", "exclude_expired = y"
    )
    assert check_citation(real, [CODE_OBS]) is None

    wrong_file = _evidence(
        EvidenceKind.CODE, "data/code/core/fee_engine.py:L13-29", "exclude_expired = y"
    )
    assert check_citation(wrong_file, [CODE_OBS]) is not None


def test_an_absent_parameter_can_still_be_cited():
    """A parameter that does not exist is the evidence for CR, so it must be citable."""
    absent = ToolObservation.of(
        "get_config_param",
        {
            "status": "not_found",
            "query": "FEE_WAIVER_SENIOR_TIER_ENABLED",
            "note": "No configuration parameter of that name exists for NWB.",
        },
        '{"status": "not_found", "query": "FEE_WAIVER_SENIOR_TIER_ENABLED", '
        '"note": "No configuration parameter of that name exists for NWB."}',
    )
    citation = _evidence(
        EvidenceKind.CONFIG, "FEE_WAIVER_SENIOR_TIER_ENABLED", '"status": "not_found"'
    )
    assert check_citation(citation, [absent]) is None


def test_needs_info_requires_a_question():
    submission = VerdictSubmission(verdict=Verdict.NEEDS_INFO, rationale="r", draft_reply="d")
    assert not check_evidence_gate(submission, []).passed


def test_the_model_cannot_report_its_own_confidence():
    with pytest.raises(ValueError):
        VerdictSubmission(verdict=Verdict.BUG, rationale="r", draft_reply="d", confidence=0.99)


def test_confidence_rewards_breadth_and_punishes_a_forced_stop():
    broad = VerdictSubmission(
        verdict=Verdict.BUG, rationale="r", evidence=[SON_EV, CONFIG_EV, DATA_EV], draft_reply="d"
    )
    narrow = VerdictSubmission(
        verdict=Verdict.BUG, rationale="r", evidence=[SON_EV, CONFIG_EV], draft_reply="d"
    )

    broad_gate = check_evidence_gate(broad, ALL_OBS)
    narrow_gate = check_evidence_gate(narrow, ALL_OBS)
    assert broad_gate.passed and narrow_gate.passed

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
