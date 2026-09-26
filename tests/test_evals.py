"""Tests for the eval set and the harness.

The case file is the thing the reported accuracy is measured against, so a silent
defect in it — a missing ticket, a duplicate id, an anchor naming a section that no
longer exists — corrupts every number downstream without failing anything. These
tests are cheap insurance on that.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.run_evals import (  # noqa: E402
    CASES_PATH,
    StuffedToolbox,
    aggregate,
    anchor_hit,
    case_ticket,
    load_cases,
)
from src import rag  # noqa: E402
from src.models import EvidenceKind, Ticket, Verdict  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent

needs_index = pytest.mark.skipif(
    not rag.model_is_cached(), reason="needs the cached model and an indexed database"
)


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    return load_cases(CASES_PATH)


# ------------------------------------------------------------- the case file


def test_every_case_resolves_to_a_ticket(cases):
    """Either an inline ticket or a ticket_file that exists on disk. Never neither."""
    for case in cases:
        has_inline = "ticket" in case
        has_file = "ticket_file" in case
        assert has_inline or has_file, f"{case['case_id']} has no ticket"
        assert not (has_inline and has_file), f"{case['case_id']} has two ticket sources"

        if has_file:
            path = PROJECT_ROOT / case["ticket_file"]
            assert path.is_file(), f"{case['case_id']} points at a missing file: {path}"

        ticket = case_ticket(case)
        assert isinstance(ticket, Ticket)
        assert ticket.ticket_id and ticket.body.strip()


def test_case_ids_and_ticket_ids_are_unique(cases):
    ids = [case["case_id"] for case in cases]
    assert len(ids) == len(set(ids))
    ticket_ids = [case_ticket(case).ticket_id for case in cases]
    assert len(ticket_ids) == len(set(ticket_ids))


def test_every_case_declares_a_valid_verdict(cases):
    for case in cases:
        assert case["expected_verdict"] in {v.value for v in Verdict}


def test_the_case_set_is_big_enough_and_covers_every_verdict(cases):
    assert len(cases) >= 15
    present = {case["expected_verdict"] for case in cases}
    assert present == {v.value for v in Verdict}

    # No class may dominate: the majority-class baseline is the number accuracy has
    # to beat, and a lopsided set makes a weak agent look strong.
    counts = {v: sum(1 for c in cases if c["expected_verdict"] == v) for v in present}
    assert max(counts.values()) / len(cases) <= 0.45, counts


def test_anchors_name_real_kinds_and_real_sources(cases):
    valid_kinds = {k.value for k in EvidenceKind}
    conn = sqlite3.connect(PROJECT_ROOT / "data" / "db.sqlite")
    try:
        known_params = {row[0] for row in conn.execute("SELECT name FROM config_params")}
        known_paths = {row[0] for row in conn.execute("SELECT path FROM code_snippets")}
        known_sons = {row[0] for row in conn.execute("SELECT son_id FROM son_docs")}
    finally:
        conn.close()

    for case in cases:
        for kind, anchors in (case.get("expected_anchors") or {}).items():
            assert kind in valid_kinds, f"{case['case_id']}: unknown anchor kind {kind}"
            for anchor in anchors:
                if kind == "son":
                    assert anchor.split("#")[0] in known_sons, f"{case['case_id']}: {anchor}"
                elif kind == "code":
                    assert anchor in known_paths, f"{case['case_id']}: {anchor}"
                elif kind == "config":
                    # A deliberately absent parameter is a legitimate anchor: the
                    # CR case turns on get_config_param reporting not_found.
                    assert anchor.isupper(), f"{case['case_id']}: {anchor} is not a parameter name"


def test_the_three_defect_scenarios_are_each_represented(cases):
    """One defect worded three ways would not be an eval of defect detection."""
    tags = [set(case.get("tags", [])) for case in cases]
    for defect in ("defect-dropped-auths", "defect-pagination", "defect-atm-fee"):
        assert any(defect in t for t in tags), f"no case exercises {defect}"

    distinct = {t for tagset in tags for t in tagset if t.startswith("defect-")}
    assert len(distinct) >= 3, "the BUG arm must cover more than one underlying defect"


def test_paraphrase_variants_are_labelled(cases):
    """They test retrieval across a wording gap, not breadth of defects, and the
    report has to be able to separate the two."""
    dropped = [c for c in cases if "defect-dropped-auths" in c.get("tags", [])]
    assert len(dropped) >= 2
    assert sum(1 for c in dropped if "paraphrase" in c.get("tags", [])) == len(dropped) - 1


# ------------------------------------------------------------ anchor matching


def test_anchor_matching_mirrors_the_gate():
    assert anchor_hit("son", "SON-001#3.2", ["SON-001#3.2"])
    assert anchor_hit("son", "SON-001#3.2", ["SON-001#3.2.1"]), "a sub-clause counts"
    assert not anchor_hit("son", "SON-001#3.2", ["SON-001#3.3"])
    assert not anchor_hit("son", "SON-001#3.2", ["SON-001#3"]), "the parent does not count"

    assert anchor_hit("config", "STMT_EXCLUDE_DROPPED_AUTHS", ["stmt_exclude_dropped_auths"])
    assert not anchor_hit("config", "STMT_EXCLUDE_DROPPED_AUTHS", ["STMT_EXCLUDE_EXPIRED_AUTHS"])

    path = "data/code/custom/nwb_statement_builder.py"
    assert anchor_hit("code", path, [f"{path}:L15-47"])
    assert not anchor_hit("code", path, ["data/code/core/statement_builder.py:L14-51"])


# -------------------------------------------------------------- aggregation


def _row(**kwargs) -> dict:
    base = {
        "expected": "BUG", "predicted": "BUG",
        "correct": True, "anchors_expected": 1, "anchors_hit": 1, "citations": 2,
        "ungrounded": 0, "gate_retries": 0, "stop_reason": "verdict",
        "cost_usd": 0.05, "latency_ms": 1000,
    }
    row = {**base, **kwargs}
    if not row["correct"] and row["predicted"] == row["expected"]:
        row["predicted"] = "CR"
    return row


def test_cost_is_reported_per_correct_verdict():
    """A wrong verdict has to be redone by a human, so its cost is sunk, not saved."""
    rows = [_row(), _row(), _row(correct=False), _row(correct=False)]
    cases = [{"expected_verdict": "BUG"} for _ in rows]
    summary = aggregate(rows, cases)

    assert summary["cost_per_case_usd"] == pytest.approx(0.05)
    assert summary["cost_per_correct_usd"] == pytest.approx(0.10)


def test_accuracy_is_reported_against_the_majority_baseline():
    cases = [{"expected_verdict": v} for v in ["BUG", "BUG", "CR", "NEEDS_INFO"]]
    summary = aggregate([_row() for _ in cases], cases)
    assert summary["majority_class"] == "BUG"
    assert summary["majority_baseline"] == pytest.approx(0.5)


def test_an_unpriced_arm_reports_no_cost_rather_than_zero():
    """Local inference is not free; it is unpriced. Reporting $0.00 would be a lie."""
    rows = [_row(cost_usd=None), _row(cost_usd=None)]
    summary = aggregate(rows, [{"expected_verdict": "BUG"}] * 2)
    assert summary["total_cost_usd"] is None
    assert summary["cost_per_correct_usd"] is None


def test_zero_correct_does_not_divide_by_zero():
    rows = [_row(correct=False)]
    assert aggregate(rows, [{"expected_verdict": "BUG"}])["cost_per_correct_usd"] is None


# ---------------------------------------------------- the stuffed control arm


@pytest.mark.integration
@needs_index
def test_the_stuffed_arm_returns_the_whole_corpus_with_chunk_ids():
    """Without ids every SON citation fails the gate and the comparison measures
    the gate rather than retrieval."""
    result = StuffedToolbox().search_son("anything at all")
    assert result["status"] == "ok"
    assert result["mode"] == "stuffed"
    assert len(result["chunks"]) == 28
    assert all(chunk["chunk_id"] for chunk in result["chunks"])
    assert {c["chunk_id"] for c in result["chunks"]} >= {"SON-001#3.2", "SON-002#5", "SON-003#3.1"}


@pytest.mark.integration
@needs_index
def test_the_stuffed_arm_ignores_the_query():
    a = StuffedToolbox().search_son("dropped authorisations")
    b = StuffedToolbox().search_son("recipe for banana bread")
    assert [c["chunk_id"] for c in a["chunks"]] == [c["chunk_id"] for c in b["chunks"]]
