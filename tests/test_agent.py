"""Tests for the agent loop, its guards, and the trace.

Every test here runs on the scripted `fake` backend against a stub toolbox. That is
deliberate: the interesting behaviour of this slice is what the loop does when the
model misbehaves - loops, stalls, cites something that was never returned, never
stops - and a real model cannot be asked to do those things on demand. Tool
correctness is covered separately in test_tools.py.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import tools as tools_module  # noqa: E402
from src.agent import AgentConfig, TriageAgent  # noqa: E402
from src.llm import (  # noqa: E402
    FakeLLM,
    LLMResponse,
    ToolCall,
    Usage,
    estimate_cost_usd,
    redact,
)
from src.models import ProductArea, StopReason, Ticket, Verdict  # noqa: E402
from src.trace import read_events, summarise  # noqa: E402


# ------------------------------------------------------------------ fixtures

SON_RESULT = {
    "status": "ok",
    "chunks": [{
        "chunk_id": "SON-001#3.2",
        "heading": "3.2 Exclusion of non-settling authorisations",
        "text": "Authorisations in DROPPED or EXPIRED state MUST NOT appear as statement lines.",
    }],
}
CONFIG_RESULT = {
    "status": "ok",
    "param": {"name": "STMT_EXCLUDE_DROPPED_AUTHS", "value": "true", "owner_layer": "both"},
}
CODE_RESULT = {
    "status": "ok",
    "function": "build_atm_statement_lines",
    "params_only_in_core": ["STMT_EXCLUDE_DROPPED_AUTHS"],
}
DATA_RESULT = {"status": "ok", "count": 4, "aggregates": {"by_status": {"DROPPED": 4}}}
ERROR_RESULT = {"status": "error", "error": "boom", "hint": "continue with the other tools"}


class StubToolbox:
    """Returns canned results and records what it was asked for."""

    def __init__(self, results: dict[str, Any] | None = None):
        self.results = results or {
            "search_son": SON_RESULT,
            "get_config_param": CONFIG_RESULT,
            "compare_core_vs_custom": CODE_RESULT,
            "get_auth_records": DATA_RESULT,
        }
        self.calls: list[tuple[str, dict]] = []

    def execute(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, arguments))
        return self.results.get(name, {"status": "error", "error": f"no stub for {name}"})


@pytest.fixture
def ticket() -> Ticket:
    return Ticket(
        ticket_id="NWB-TEST",
        client_id="NWB",
        subject="Dropped authorisations on statements",
        body="Four cancelled ATM withdrawals appear on the statement.",
        product_area=ProductArea.STATEMENTS,
        submitted_at=datetime(2026, 3, 19, tzinfo=timezone.utc),
    )


def tool_turn(*calls: tuple[str, dict]) -> LLMResponse:
    return LLMResponse(
        text="",
        tool_calls=[ToolCall(id=f"call_{i}", name=n, arguments=a) for i, (n, a) in enumerate(calls)],
        stop_reason="tool_use",
        model="fake-model",
        usage=Usage(input_tokens=100, output_tokens=20),
        latency_ms=5,
    )


def text_turn(text: str) -> LLMResponse:
    return LLMResponse(
        text=text, tool_calls=[], stop_reason="end_turn", model="fake-model",
        usage=Usage(input_tokens=100, output_tokens=20), latency_ms=5,
    )


def verdict_turn(verdict: str, evidence: list[dict], **extra) -> LLMResponse:
    payload = {
        "verdict": verdict,
        "rationale": extra.get("rationale", "Because the evidence says so."),
        "evidence": evidence,
        "draft_reply": extra.get("draft_reply", "Thanks for raising this."),
    }
    if "missing_info" in extra:
        payload["missing_info"] = extra["missing_info"]
    return tool_turn(("submit_verdict", payload))


def cite(kind: str, ref: str, quote: str) -> dict:
    return {"kind": kind, "ref": ref, "quote": quote, "why_it_matters": "it decides the verdict"}


SON_CITE = cite("son", "SON-001#3.2", "MUST NOT appear as statement lines")
CONFIG_CITE = cite("config", "STMT_EXCLUDE_DROPPED_AUTHS", '"value": "true"')
DATA_CITE = cite("data", "auth_records", '"DROPPED": 4')

# Quotes are checked against tool output, so a test citing these must also have
# scripted the tool call that produces them.
GOOD_EVIDENCE = [SON_CITE, CONFIG_CITE, DATA_CITE]
SON_AND_CONFIG = [SON_CITE, CONFIG_CITE]


def build(llm: FakeLLM, tmp_path: Path, toolbox=None, **config) -> TriageAgent:
    return TriageAgent(llm, toolbox or StubToolbox(), config=AgentConfig(**config), trace_dir=tmp_path)


# ------------------------------------------------------------- the happy path


def test_a_well_evidenced_bug_is_returned(ticket, tmp_path):
    llm = FakeLLM([
        tool_turn(("search_son", {"query": "dropped authorisations"})),
        tool_turn(("get_config_param", {"name": "STMT_EXCLUDE_DROPPED_AUTHS"})),
        tool_turn(("compare_core_vs_custom", {"function_name": "build_atm_statement_lines"})),
        tool_turn(("get_auth_records", {"status": "DROPPED"})),
        verdict_turn("BUG", GOOD_EVIDENCE),
    ])
    toolbox = StubToolbox()
    result = build(llm, tmp_path, toolbox).run(ticket)

    assert result.verdict is Verdict.BUG
    assert result.stop_reason is StopReason.VERDICT
    assert result.steps_used == 5
    assert result.gate.passed
    assert result.confidence >= 0.7
    assert [name for name, _ in toolbox.calls] == [
        "search_son", "get_config_param", "compare_core_vs_custom", "get_auth_records",
    ]


def test_parallel_tool_calls_in_one_turn_are_all_executed(ticket, tmp_path):
    llm = FakeLLM([
        tool_turn(("search_son", {"query": "x"}), ("get_config_param", {"name": "y"})),
        verdict_turn("BUG", SON_AND_CONFIG),
    ])
    toolbox = StubToolbox()
    result = build(llm, tmp_path, toolbox).run(ticket)
    assert [name for name, _ in toolbox.calls] == ["search_son", "get_config_param"]
    assert result.verdict is Verdict.BUG


# ------------------------------------------------------------- evidence gate


def test_a_thin_verdict_is_sent_back_once_and_can_be_fixed(ticket, tmp_path):
    """A BUG citing only the spec is a reading, not a triage."""
    llm = FakeLLM([
        tool_turn(("search_son", {"query": "dropped"})),
        tool_turn(("get_config_param", {"name": "STMT_EXCLUDE_DROPPED_AUTHS"})),
        verdict_turn("BUG", [SON_CITE]),
        verdict_turn("BUG", SON_AND_CONFIG),
    ])
    result = build(llm, tmp_path).run(ticket)

    assert result.verdict is Verdict.BUG
    assert result.stop_reason is StopReason.VERDICT
    events = read_events(tmp_path / f"{result.trace_id}.jsonl")
    gate_failures = [e for e in events if e["type"] == "gate_failed"]
    assert len(gate_failures) == 1
    assert any("non-SON" in f for f in gate_failures[0]["payload"]["failures"])


def test_a_retry_costs_confidence(ticket, tmp_path):
    clean = FakeLLM([
        tool_turn(("search_son", {"query": "a"})),
        tool_turn(("get_config_param", {"name": "b"})),
        tool_turn(("get_auth_records", {})),
        verdict_turn("BUG", GOOD_EVIDENCE),
    ])
    retried = FakeLLM([
        tool_turn(("search_son", {"query": "a"})),
        tool_turn(("get_config_param", {"name": "b"})),
        tool_turn(("get_auth_records", {})),
        verdict_turn("BUG", [SON_CITE]),
        verdict_turn("BUG", GOOD_EVIDENCE),
    ])
    assert build(clean, tmp_path).run(ticket).confidence > build(retried, tmp_path).run(ticket).confidence


def test_a_verdict_that_never_holds_up_is_downgraded(ticket, tmp_path):
    thin = [SON_CITE]
    llm = FakeLLM([
        tool_turn(("search_son", {"query": "dropped"})),
        verdict_turn("BUG", thin),
        verdict_turn("BUG", thin),
        verdict_turn("BUG", thin),  # the forced finish tries once more
    ])
    result = build(llm, tmp_path).run(ticket)

    assert result.verdict is Verdict.NEEDS_INFO
    assert result.stop_reason is StopReason.GATE_EXHAUSTED
    assert result.missing_info
    assert any("BUG" in question for question in result.missing_info)


def test_an_invented_quote_is_dropped_not_published(ticket, tmp_path):
    fabricated = "the platform team confirmed this is a known defect"
    invented = [*GOOD_EVIDENCE, cite("code", "somewhere", fabricated)]
    llm = FakeLLM([
        tool_turn(("search_son", {"query": "dropped"}), ("get_config_param", {"name": "x"}),
                  ("get_auth_records", {})),
        verdict_turn("BUG", invented),
        verdict_turn("BUG", invented),
        verdict_turn("BUG", invented),
    ])
    result = build(llm, tmp_path).run(ticket)

    quotes = [e.quote for e in result.evidence]
    assert fabricated not in quotes
    assert SON_CITE["quote"] in quotes, "the grounded citations survive the downgrade"
    assert result.verdict is Verdict.NEEDS_INFO


def test_a_malformed_submission_is_rejected_on_schema(ticket, tmp_path):
    """extra='forbid' means a self-reported confidence never even reaches the gate."""
    llm = FakeLLM([
        tool_turn(("submit_verdict", {
            "verdict": "BUG", "rationale": "r", "evidence": GOOD_EVIDENCE,
            "draft_reply": "d", "confidence": 0.99,
        })),
        tool_turn(("search_son", {"query": "dropped"}), ("get_config_param", {"name": "x"})),
        verdict_turn("BUG", SON_AND_CONFIG),
    ])
    result = build(llm, tmp_path).run(ticket)

    events = read_events(tmp_path / f"{result.trace_id}.jsonl")
    schema_failures = [e for e in events if e["type"] == "gate_failed"
                       and e["payload"]["reason"] == "schema"]
    assert schema_failures
    assert any("confidence" in f for f in schema_failures[0]["payload"]["failures"])


# ------------------------------------------------------------------- guards


def test_step_budget_forces_a_structured_finish(ticket, tmp_path):
    llm = FakeLLM(
        [tool_turn(("search_son", {"query": f"attempt {i}"})) for i in range(4)]
        + [verdict_turn("NEEDS_INFO", [], missing_info=["Which behaviour was intended?"])]
    )
    result = build(llm, tmp_path, max_steps=4).run(ticket)

    assert result.stop_reason is StopReason.STEP_BUDGET
    assert result.verdict is Verdict.NEEDS_INFO
    assert result.confidence < 0.7, "a forced stop must cost confidence"


def test_repeated_identical_calls_are_caught(ticket, tmp_path):
    same = ("search_son", {"query": "dropped authorisations"})
    llm = FakeLLM([
        tool_turn(same), tool_turn(same), tool_turn(same),
        verdict_turn("NEEDS_INFO", [], missing_info=["What did you intend?"]),
    ])
    toolbox = StubToolbox()
    result = build(llm, tmp_path, toolbox).run(ticket)

    assert len(toolbox.calls) == 1, "the repeat must be served from cache, not re-run"
    assert result.stop_reason is StopReason.NO_PROGRESS
    guards = [e["payload"]["guard"] for e in read_events(tmp_path / f"{result.trace_id}.jsonl")
              if e["type"] == "guard_tripped"]
    assert "duplicate_call" in guards


def test_tool_errors_are_survivable_until_the_budget_runs_out(ticket, tmp_path):
    broken = StubToolbox({name: ERROR_RESULT for name in
                          ["search_son", "get_config_param", "get_auth_records"]})
    llm = FakeLLM([
        tool_turn(("search_son", {"query": "a"})),
        tool_turn(("get_config_param", {"name": "b"})),
        tool_turn(("get_auth_records", {})),
        verdict_turn("NEEDS_INFO", [], missing_info=["The tools were unavailable."]),
    ])
    result = build(llm, tmp_path, broken).run(ticket)

    assert result.stop_reason is StopReason.TOOL_ERROR_BUDGET
    assert result.verdict is Verdict.NEEDS_INFO


def test_prose_instead_of_a_tool_call_is_nudged_then_stopped(ticket, tmp_path):
    llm = FakeLLM([
        text_turn("I think this is probably a bug, based on my general knowledge."),
        text_turn("Still just talking."),
        verdict_turn("NEEDS_INFO", [], missing_info=["Nothing was actually checked."]),
    ])
    result = build(llm, tmp_path).run(ticket)

    assert result.stop_reason is StopReason.NO_PROGRESS
    assert llm.requests[1]["messages"][-1]["content"][0]["text"].startswith("Prose does not reach me")


def test_every_run_returns_a_verdict_even_when_the_model_will_not(ticket, tmp_path):
    """The floor: guards must never produce an empty result."""
    llm = FakeLLM([text_turn("no"), text_turn("still no"), text_turn("no tool call here either")])
    result = build(llm, tmp_path).run(ticket)

    assert result.verdict is Verdict.NEEDS_INFO
    assert result.missing_info
    assert result.confidence < 0.4, "nothing was checked, so the score must be low"


# -------------------------------------------------------------------- traces


def test_the_trace_records_the_whole_run(ticket, tmp_path):
    llm = FakeLLM([
        tool_turn(("search_son", {"query": "dropped"}), ("get_config_param", {"name": "x"})),
        verdict_turn("BUG", SON_AND_CONFIG),
    ])
    result = build(llm, tmp_path).run(ticket)
    events = read_events(tmp_path / f"{result.trace_id}.jsonl")

    types = [e["type"] for e in events]
    assert types[0] == "run_started"
    assert types[-1] == "run_finished"
    for expected in ["llm_request", "llm_response", "tool_call", "tool_result", "verdict"]:
        assert expected in types
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert all(e["run_id"] == result.trace_id for e in events)


def test_the_trace_carries_tokens_latency_and_cost(ticket, tmp_path):
    llm = FakeLLM([verdict_turn("NEEDS_INFO", [], missing_info=["q"])])
    result = build(llm, tmp_path).run(ticket)
    responses = [e["payload"] for e in read_events(tmp_path / f"{result.trace_id}.jsonl")
                 if e["type"] == "llm_response"]

    assert responses[0]["usage"]["input_tokens"] == 100
    assert responses[0]["latency_ms"] == 5
    assert responses[0]["cost_usd"] is None, "the fake model has no price on file"


def test_the_api_key_never_reaches_the_trace(ticket, tmp_path, monkeypatch):
    secret = "sk-ant-thisisnotarealkey-0123456789abcdef"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)

    llm = FakeLLM([
        tool_turn(("search_son", {"query": f"a query mentioning {secret}"}),
                  ("get_config_param", {"name": "x"})),
        verdict_turn("BUG", SON_AND_CONFIG),
    ])
    result = build(llm, tmp_path).run(ticket)

    raw = (tmp_path / f"{result.trace_id}.jsonl").read_text(encoding="utf-8")
    assert secret not in raw
    assert "[redacted]" in raw


@pytest.mark.parametrize(
    "value, expected",
    [
        ("sk-ant-api03-abcdefghijklmnop", "[redacted]"),
        ("nothing secret here", "nothing secret here"),
    ],
)
def test_redaction_of_key_shaped_strings(value, expected):
    assert redact(value) == expected


def test_redaction_reaches_inside_nested_payloads():
    payload = {"headers": {"x-api-key": "sk-ant-api03-abcdefghijklmnop"}, "n": [1, 2]}
    assert redact(payload)["headers"]["x-api-key"] == "[redacted]"
    assert redact(payload)["n"] == [1, 2]


def test_summary_reports_what_a_reviewer_needs(ticket, tmp_path):
    llm = FakeLLM([
        tool_turn(("search_son", {"query": "dropped"})),
        tool_turn(("get_config_param", {"name": "STMT_EXCLUDE_DROPPED_AUTHS"})),
        verdict_turn("BUG", SON_AND_CONFIG),
    ])
    result = build(llm, tmp_path).run(ticket)
    summary = summarise(tmp_path / f"{result.trace_id}.jsonl")

    assert summary["verdict"] == "BUG"
    assert summary["tool_sequence"] == ["search_son", "get_config_param", "submit_verdict"]
    assert summary["stop_reason"] == "verdict"
    assert summary["gate_retries"] == 0
    assert summary["usage"]["input_tokens"] == 300
    assert len(summary["evidence"]) == 2


# --------------------------------------------------------------------- cost


def test_cost_uses_published_rates():
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert estimate_cost_usd("claude-haiku-4-5", usage) == pytest.approx(6.00)


def test_cache_reads_are_cheaper_than_fresh_input():
    fresh = estimate_cost_usd("claude-haiku-4-5", Usage(input_tokens=1_000_000))
    cached = estimate_cost_usd("claude-haiku-4-5", Usage(cache_read_tokens=1_000_000))
    assert cached == pytest.approx(fresh * 0.1)


def test_a_dated_snapshot_id_is_still_priced():
    """The API answers with the snapshot it served, not the alias that was asked for."""
    usage = Usage(input_tokens=1_000_000)
    assert estimate_cost_usd("claude-haiku-4-5-20251001", usage) == pytest.approx(1.00)


def test_an_unpriced_model_reports_no_cost_rather_than_guessing():
    assert estimate_cost_usd("some-local-model", Usage(input_tokens=1000)) is None


# ------------------------------------------------------------------ backends


def test_the_default_backend_is_the_fake_one(monkeypatch):
    """A missing FINREQ_LLM must never turn a test run into a billed API call."""
    monkeypatch.delenv("FINREQ_LLM", raising=False)
    from src.llm import get_client

    assert get_client().name == "fake"


def test_an_unknown_backend_is_refused(monkeypatch):
    from src.llm import LLMError, get_client

    monkeypatch.setenv("FINREQ_LLM", "gpt")
    with pytest.raises(LLMError, match="unknown FINREQ_LLM backend"):
        get_client()


def test_tool_schemas_translate_to_openai_shape():
    from src.llm import _to_openai_messages

    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "search_son", "input": {"query": "x"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "{}"},
        ]},
    ]
    converted = _to_openai_messages(messages)
    assert [m["role"] for m in converted] == ["user", "assistant", "tool"]
    assert converted[1]["tool_calls"][0]["function"]["name"] == "search_son"
    assert json.loads(converted[1]["tool_calls"][0]["function"]["arguments"]) == {"query": "x"}
    assert converted[2]["tool_call_id"] == "t1"


def test_the_real_tool_schemas_are_what_the_loop_sends(ticket, tmp_path):
    llm = FakeLLM([verdict_turn("NEEDS_INFO", [], missing_info=["q"])])
    build(llm, tmp_path).run(ticket)
    assert llm.requests[0]["tools"] == [s["name"] for s in tools_module.ALL_TOOL_SCHEMAS]
    assert "submit_verdict" in llm.requests[0]["tools"]
