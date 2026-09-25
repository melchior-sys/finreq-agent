"""Tests for the four evidence tools and the submit_verdict schema.

Only `search_son` needs the embedding model, so it carries the integration marker.
Everything else runs offline against a freshly seeded database.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.build_db import DEFAULT_SEED, build  # noqa: E402
from src import rag, tools  # noqa: E402
from src.models import VerdictSubmission, is_grounded  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent

needs_model = pytest.mark.skipif(
    not rag.model_is_cached(),
    reason=f"embedding model not cached in {rag.model_cache_dir()}",
)


@pytest.fixture(scope="module")
def db_path(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("tools") / "db.sqlite"
    build(DEFAULT_SEED, path).close()
    return path


@pytest.fixture(scope="module")
def box(db_path) -> tools.Toolbox:
    return tools.Toolbox(db_path)


# ------------------------------------------------------------------- schemas


def test_every_schema_is_well_formed():
    for schema in tools.ALL_TOOL_SCHEMAS:
        assert schema["name"] and schema["description"]
        properties = schema["input_schema"]["properties"]
        assert schema["input_schema"]["type"] == "object"
        assert schema["input_schema"]["additionalProperties"] is False
        for required in schema["input_schema"].get("required", []):
            assert required in properties, f"{schema['name']} requires undeclared {required}"


def test_tool_names_are_unique():
    names = [schema["name"] for schema in tools.ALL_TOOL_SCHEMAS]
    assert len(names) == len(set(names)) == 5


def test_submit_verdict_schema_matches_the_model():
    """The wire schema and VerdictSubmission must not drift apart."""
    schema_fields = set(tools.SUBMIT_VERDICT_SCHEMA["input_schema"]["properties"])
    assert schema_fields == set(VerdictSubmission.model_fields)

    evidence_fields = set(
        tools.SUBMIT_VERDICT_SCHEMA["input_schema"]["properties"]["evidence"]["items"]["properties"]
    )
    assert evidence_fields == {"kind", "ref", "quote", "why_it_matters"}


def test_submit_verdict_schema_forbids_a_self_reported_confidence():
    """Closed at the wire schema as well as in the model, so the API rejects it first."""
    schema = tools.SUBMIT_VERDICT_SCHEMA["input_schema"]
    assert "confidence" not in schema["properties"]
    assert schema["additionalProperties"] is False


# ----------------------------------------------------------- get_config_param


def test_exact_lookup_is_case_insensitive(box):
    result = box.get_config_param("stmt_exclude_dropped_auths")
    assert result["status"] == "ok"
    assert result["matched_on"] == "exact"
    assert result["param"]["name"] == "STMT_EXCLUDE_DROPPED_AUTHS"
    assert result["param"]["value"] == "true"


def test_unique_fragment_resolves_to_one_parameter(box):
    result = box.get_config_param("SALARY_CREDIT")
    assert result["status"] == "ok"
    assert result["matched_on"] == "partial"
    assert result["param"]["name"] == "FEE_SALARY_CREDIT_MIN"


def test_ambiguous_fragment_lists_candidates_rather_than_guessing(box):
    result = box.get_config_param("STMT_EXCLUDE")
    assert result["status"] == "ambiguous"
    names = [c["name"] for c in result["candidates"]]
    assert names == ["STMT_EXCLUDE_DROPPED_AUTHS", "STMT_EXCLUDE_EXPIRED_AUTHS"]


def test_missing_parameter_returns_near_matches_and_says_what_absence_means(box):
    result = box.get_config_param("FEE_WAIVER_SENIOR_TIER_ENABLED")
    assert result["status"] == "not_found"
    assert "FEE_WAIVER_MIN_AVG_BALANCE" in result["near_matches"]
    assert "never built" in result["note"]


def test_underscore_is_not_treated_as_a_wildcard(box, db_path):
    """The SQL LIKE trap, pinned.

    In LIKE, `_` matches any single character, so the pattern '%_AGE%' matches
    STMT_MAX_LINES_PER_PAGE via the P of PAGE. Parameter names are full of
    underscores, so a LIKE-based fragment search quietly returns parameters the
    caller never asked about. get_config_param uses instr, which is literal.
    """
    conn = sqlite3.connect(db_path)
    like_hits = conn.execute(
        "SELECT name FROM config_params WHERE client_id = 'NWB' AND name LIKE '%_AGE%'"
    ).fetchall()
    conn.close()
    assert ("STMT_MAX_LINES_PER_PAGE",) in like_hits, "LIKE really does over-match here"

    result = box.get_config_param("_AGE")
    assert result["status"] == "not_found"


def test_client_scoping_is_real(box):
    nwb = box.get_config_param("STMT_EXCLUDE_DROPPED_AUTHS", client_id="NWB")
    svb = box.get_config_param("STMT_EXCLUDE_DROPPED_AUTHS", client_id="SVB")
    assert nwb["param"]["value"] == "true"
    assert svb["param"]["value"] == "false"
    assert box.get_config_param("FEE_SALARY_CREDIT_MIN", client_id="HRB")["status"] == "not_found"


# ---------------------------------------------------- compare_core_vs_custom


def test_the_override_that_drops_a_parameter_is_reported(box):
    """The finding the whole demo turns on, and it is computed, not judged."""
    result = box.compare_core_vs_custom("build_atm_statement_lines")
    assert result["status"] == "ok"
    assert result["custom_overrides_core"] is True
    assert result["params_only_in_core"] == ["STMT_EXCLUDE_DROPPED_AUTHS"]
    assert result["params_only_in_custom"] == []
    assert "STMT_EXCLUDE_DROPPED_AUTHS" in result["core"]["reads_params"]
    assert "STMT_EXCLUDE_DROPPED_AUTHS" not in result["custom"]["reads_params"]
    assert "no effect on this function" in result["note"]


def test_a_legitimate_override_produces_no_missing_parameters(box):
    """Custom differing from core is normal; the decoy has to look innocent."""
    result = box.compare_core_vs_custom("calculate_monthly_fee_waiver")
    assert result["params_only_in_core"] == []
    assert result["params_only_in_custom"] == ["FEE_SALARY_CREDIT_MIN"]
    assert "no effect" not in result["note"]


def test_snippets_are_bounded_to_the_function(box):
    result = box.compare_core_vs_custom("build_atm_statement_lines")
    for layer in ("core", "custom"):
        snippet = result[layer]["snippet"]
        assert snippet.startswith("def build_atm_statement_lines(")
        assert "def build_memo_block" not in snippet, "range leaked into the next function"
    assert result["core"]["lines"] == "L14-51"


def test_unknown_function_lists_what_is_available(box):
    result = box.compare_core_vs_custom("calculate_interest")
    assert result["status"] == "not_found"
    assert "build_atm_statement_lines" in result["available_functions"]


def test_client_without_an_override_gets_core(box):
    result = box.compare_core_vs_custom("build_atm_statement_lines", client_id="SVB")
    assert result["custom_overrides_core"] is False
    assert result["custom"] is None
    assert result["params_only_in_core"] == []


def test_param_scan_matches_whole_words_only():
    known = ["STMT_EXCLUDE_DROPPED_AUTHS", "AUTH_DROP_WINDOW_DAYS"]
    source = 'x = config.get_bool("STMT_EXCLUDE_DROPPED_AUTHS_V2")\ny = AUTH_DROP_WINDOW_DAYS'
    assert tools.params_read_in(source, known) == ["AUTH_DROP_WINDOW_DAYS"]


# ---------------------------------------------------------- get_auth_records


def test_the_smoking_gun_is_findable(box):
    result = box.get_auth_records(
        card_last4="4417", channel="ATM", status="DROPPED",
        date_from="2026-02-15", date_to="2026-03-15",
    )
    assert result["count"] == 4
    assert result["aggregates"]["by_status"] == {"DROPPED": 4}
    assert {r["auth_id"] for r in result["records"]} == {"A-10231", "A-10244", "A-10267", "A-10289"}


def test_aggregates_cover_the_whole_match_even_when_rows_are_truncated(box):
    result = box.get_auth_records(limit=3)
    assert result["returned"] == 3
    assert result["truncated"] is True
    assert sum(result["aggregates"]["by_status"].values()) == result["count"] > 3
    assert "quote those rather than counting rows" in result["note"]


def test_date_filter_excludes_the_previous_cycle(box):
    everything = box.get_auth_records(card_last4="4417", status="DROPPED", limit=50)
    in_cycle = box.get_auth_records(
        card_last4="4417", status="DROPPED", date_from="2026-02-15", date_to="2026-03-15", limit=50
    )
    assert everything["count"] == 5  # includes A-10126 from the January cycle
    assert in_cycle["count"] == 4


def test_no_matches_says_so_rather_than_implying_nothing_happened(box):
    result = box.get_auth_records(card_last4="0000")
    assert result["count"] == 0
    assert result["records"] == []
    assert "Widen the date range" in result["note"]


def test_limit_is_capped(box):
    assert box.get_auth_records(limit=999)["returned"] <= tools.MAX_RECORDS


# ------------------------------------------------------------------ dispatch


def test_execute_routes_to_the_right_tool(box):
    result = box.execute("get_config_param", {"name": "STMT_TIMEZONE"})
    assert result["param"]["value"] == "Europe/London"


def test_unknown_tool_is_a_result_not_an_exception(box):
    result = box.execute("delete_everything", {})
    assert result["status"] == "error"
    assert "get_config_param" in result["hint"]


def test_bad_arguments_come_back_as_a_recoverable_error(box):
    result = box.execute("get_config_param", {"parameter": "STMT_TIMEZONE"})
    assert result["status"] == "error"
    assert "input schema" in result["hint"]


def test_a_broken_database_does_not_end_the_run(tmp_path):
    broken = tools.Toolbox(tmp_path / "empty.sqlite")
    result = broken.execute("get_auth_records", {})
    assert result["status"] == "error"
    assert "continue with the other tools" in result["hint"]


# ------------------------------------------------------- output and grounding


def test_serialised_output_keeps_specification_text_quotable(box):
    """Evidence quotes are checked against serialise() output, so it must stay verbatim."""
    result = box.compare_core_vs_custom("build_atm_statement_lines")
    text = tools.serialise(result)
    assert is_grounded("STMT_EXCLUDE_DROPPED_AUTHS", [text])
    assert is_grounded('exclude_expired = config.get_bool("STMT_EXCLUDE_EXPIRED_AUTHS"', [text])
    assert not is_grounded("the custom layer reads both flags", [text])


def test_serialise_does_not_escape_non_ascii(box):
    result = {"note": "cardholder's hold was released - see section 3.2"}
    assert "\\u" not in tools.serialise(result)


# ------------------------------------------------------- search_son (integration)


@pytest.fixture(scope="module")
def indexed_box(db_path) -> tools.Toolbox:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rag.index(conn, force=True)
    conn.close()
    return tools.Toolbox(db_path)


@pytest.mark.integration
@needs_model
def test_search_son_returns_citable_sections(indexed_box):
    result = indexed_box.search_son("are dropped authorisations excluded from ATM statements")
    assert result["status"] == "ok"
    top = result["chunks"][0]
    assert top["chunk_id"] == "SON-001#3.2"
    assert top["version"] == "2.3"
    assert is_grounded(
        "MUST NOT appear as statement", [tools.serialise(result)]
    )


@pytest.mark.integration
@needs_model
def test_search_son_reports_silence_as_a_usable_answer(indexed_box):
    result = indexed_box.search_son("how do I reset my online banking password")
    assert result["status"] == "no_match"
    assert result["chunks"] == []
    assert "treat the silence as evidence" in result["note"]


@pytest.mark.integration
@needs_model
def test_search_son_respects_top_k(indexed_box):
    result = indexed_box.search_son("fee waiver conditions", top_k=2)
    assert len(result["chunks"]) <= 2
