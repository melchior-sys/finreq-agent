"""The triage agent loop.

Hand-written on purpose. The orchestration - when to call a tool, when to stop,
what to do when the model produces something unusable - is the substance of this
project, and a framework that owned the loop would hide exactly the decisions worth
being able to defend.

The shape of it:

    for step in 1..MAX_STEPS:
        ask the model
        if it called submit_verdict and the evidence gate passes -> done
        if it called submit_verdict and the gate fails          -> hand back the
                                                                    failures, once
        otherwise run the tools it asked for and go round again
    budget exhausted -> one final call with tool_choice forced to submit_verdict

The only sanctioned exit is `submit_verdict`. Prose is never parsed into a verdict.
Every other way out of the loop is a guard, and each one is named in the result and
in the trace, so "why did it stop?" always has an answer.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402

from src import tools as tools_module  # noqa: E402
from src.llm import LLMError, LLMResponse, ToolCall, estimate_cost_usd  # noqa: E402
from src.models import (  # noqa: E402
    EvidenceGateReport,
    StopReason,
    Ticket,
    TriageResult,
    Verdict,
    VerdictSubmission,
    check_evidence_gate,
)
from src.trace import DEFAULT_TRACE_DIR, Tracer  # noqa: E402

SUBMIT_VERDICT = "submit_verdict"


SYSTEM_PROMPT = """\
You are a triage analyst on the platform team of a card-processing provider. Client \
operations teams raise tickets; you decide whether each one is a defect in what was \
built, or a request for something that was never agreed.

Your verdict is one of three:

BUG - the client's signed-off Statement of Need requires a behaviour and the \
implementation does not deliver it.
CR - the client is asking for behaviour the Statement of Need does not require, or \
explicitly places out of scope. This is new work, not a defect.
NEEDS_INFO - the Statement of Need is genuinely silent or ambiguous on the point at \
issue, and no further tool call will settle it. Somebody has to decide what the \
behaviour should be.

How to work a ticket:

1. Start with search_son. What was specified is the question every verdict turns on. \
If several searches return nothing, the silence is itself a finding.
2. Look up any configuration parameter the ticket mentions or implies, with \
get_config_param. A parameter that does not exist for this client cannot have been \
mis-set, which points at CR.
3. Where the ticket is about how the platform behaves, use compare_core_vs_custom on \
the function involved. Read params_only_in_core carefully: a parameter the platform \
honours but the client's own override never reads has no effect for that client, \
whatever it is set to.
4. Use get_auth_records to confirm the reported activity actually happened and to put \
a number on it. Quote the aggregate counts rather than counting rows yourself.
5. Call submit_verdict.

Things that will catch you out:

- A client-custom override differing from the platform implementation is normal. \
Overrides exist in order to differ. What makes a difference a defect is the \
specification, never the difference on its own.
- Never state a configuration value you have not looked up.
- Quote evidence verbatim from the tool results. Quotes are checked against what the \
tools actually returned, and an invented one sends the submission back to you.
- A BUG or a CR needs at least two pieces of evidence: at least one Statement of Need \
section saying what should happen, and at least one piece of config, code or data \
showing what does happen. One without the other is not triage.
- Do not pad the evidence list. Three citations that each carry weight beat six that \
repeat each other.

You have a limited number of steps. Gather what you need, then decide. If you run out \
of road, NEEDS_INFO with a clear question is a respectable answer; a confident guess \
is not.\
"""


@dataclass(frozen=True)
class AgentConfig:
    """Guard thresholds. Every one of these is a stop condition with a name."""

    max_steps: int = 8
    wall_clock_seconds: float = 90.0
    tool_error_budget: int = 3
    duplicate_strikes: int = 2
    no_progress_strikes: int = 2
    gate_retries: int = 1


def render_ticket(ticket: Ticket) -> str:
    return (
        f"Ticket {ticket.ticket_id} from {ticket.client_id}, raised "
        f"{ticket.submitted_at:%Y-%m-%d} by a {ticket.reporter_role}.\n"
        f"Product area: {ticket.product_area.value}\n\n"
        f"Subject: {ticket.subject}\n\n{ticket.body}"
    )


class TriageAgent:
    def __init__(
        self,
        llm,
        toolbox: tools_module.Toolbox | None = None,
        *,
        config: AgentConfig | None = None,
        trace_dir: Path | str = DEFAULT_TRACE_DIR,
    ):
        self.llm = llm
        self.toolbox = toolbox or tools_module.Toolbox()
        self.config = config or AgentConfig()
        self.trace_dir = trace_dir

    # -- the loop ---------------------------------------------------------

    def run(self, ticket: Ticket) -> TriageResult:
        run_id = f"{ticket.ticket_id}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
        tracer = Tracer(run_id, self.trace_dir)
        started = time.perf_counter()

        tracer.event(
            "run_started",
            ticket_id=ticket.ticket_id,
            client_id=ticket.client_id,
            subject=ticket.subject,
            backend=getattr(self.llm, "name", "unknown"),
            model=getattr(self.llm, "model", "unknown"),
            config=self.config.__dict__,
        )

        state = _RunState()
        state.ticket = ticket
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": [{"type": "text", "text": render_ticket(ticket)}]}
        ]

        for step in range(1, self.config.max_steps + 1):
            state.steps = step
            elapsed = time.perf_counter() - started
            if elapsed > self.config.wall_clock_seconds:
                return self._forced_finish(
                    ticket, messages, tracer, state, StopReason.TIMEOUT, started,
                    detail=f"{elapsed:.1f}s elapsed",
                )

            try:
                response = self._call_llm(messages, tracer, step)
            except LLMError as exc:
                tracer.event("guard_tripped", guard="llm_error", detail=str(exc))
                tracer.event("run_finished", steps_used=step, stop_reason="llm_error",
                             elapsed_ms=int((time.perf_counter() - started) * 1000))
                raise

            messages.append({"role": "assistant", "content": _assistant_blocks(response)})

            if not response.tool_calls:
                state.no_progress += 1
                tracer.event("guard_tripped", guard="no_tool_call", strikes=state.no_progress,
                             text=response.text[:400])
                if state.no_progress >= self.config.no_progress_strikes:
                    return self._forced_finish(
                        ticket, messages, tracer, state, StopReason.NO_PROGRESS, started,
                        detail="two turns produced no tool call",
                    )
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "text",
                        "text": "Prose does not reach me. Call one of the evidence tools, or "
                                "call submit_verdict if you have what you need.",
                    }],
                })
                continue

            results: list[dict[str, Any]] = []
            for call in response.tool_calls:
                if call.name == SUBMIT_VERDICT:
                    outcome = self._handle_submission(call, state, tracer)
                    if isinstance(outcome, TriageResult):
                        tracer.event("run_finished", steps_used=step, stop_reason=StopReason.VERDICT.value,
                                     elapsed_ms=int((time.perf_counter() - started) * 1000))
                        return outcome
                    results.append(outcome)
                    if state.gate_failures > self.config.gate_retries:
                        return self._forced_finish(
                            ticket, messages, tracer, state, StopReason.GATE_EXHAUSTED, started,
                            detail="evidence gate exhausted its retry", pending=results,
                        )
                else:
                    results.append(self._run_tool(call, state, tracer))

            messages.append({"role": "user", "content": results})

            if state.tool_errors >= self.config.tool_error_budget:
                return self._forced_finish(
                    ticket, messages, tracer, state, StopReason.TOOL_ERROR_BUDGET, started,
                    detail=f"{state.tool_errors} tool errors",
                )
            if state.duplicates >= self.config.duplicate_strikes:
                return self._forced_finish(
                    ticket, messages, tracer, state, StopReason.NO_PROGRESS, started,
                    detail=f"{state.duplicates} repeated tool calls",
                )

        return self._forced_finish(
            ticket, messages, tracer, state, StopReason.STEP_BUDGET, started,
            detail=f"{self.config.max_steps} steps used",
        )

    # -- pieces -----------------------------------------------------------

    def _call_llm(
        self, messages: list[dict[str, Any]], tracer: Tracer, step: int,
        tool_choice: dict[str, Any] | None = None,
    ) -> LLMResponse:
        tracer.event("llm_request", step=step, messages=len(messages), tool_choice=tool_choice)
        response = self.llm.complete(
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=tools_module.ALL_TOOL_SCHEMAS,
            tool_choice=tool_choice,
        )
        tracer.event(
            "llm_response",
            step=step,
            stop_reason=response.stop_reason,
            model=response.model,
            latency_ms=response.latency_ms,
            usage=response.usage.as_dict(),
            cost_usd=estimate_cost_usd(response.model, response.usage),
            tool_calls=[call.name for call in response.tool_calls],
            text=response.text[:2000],
        )
        return response

    def _run_tool(self, call: ToolCall, state: "_RunState", tracer: Tracer) -> dict[str, Any]:
        key = (call.name, json.dumps(call.arguments, sort_keys=True, default=str))
        tracer.event("tool_call", tool=call.name, arguments=call.arguments,
                     repeat=key in state.seen_calls)

        if key in state.seen_calls:
            state.duplicates += 1
            cached = state.seen_calls[key]
            note = (
                "You have already called this tool with these exact arguments. The earlier "
                "result is repeated below unchanged. Ask something different, or decide."
            )
            payload = tools_module.serialise({"status": "repeat", "note": note, "result": cached})
            tracer.event("guard_tripped", guard="duplicate_call", tool=call.name,
                         strikes=state.duplicates)
            return _tool_result_block(call.id, payload)

        result = self.toolbox.execute(call.name, call.arguments)
        is_error = result.get("status") == "error"
        if is_error:
            state.tool_errors += 1

        state.seen_calls[key] = result
        payload = tools_module.serialise(result)
        state.tool_outputs.append(payload)
        tracer.event("tool_result", tool=call.name, status=result.get("status"),
                     is_error=is_error, chars=len(payload), result=result)
        return _tool_result_block(call.id, payload, is_error=is_error)

    def _handle_submission(
        self, call: ToolCall, state: "_RunState", tracer: Tracer
    ) -> TriageResult | dict[str, Any]:
        """Validate a submit_verdict call. Returns a result, or a tool_result to retry."""
        tracer.event("tool_call", tool=SUBMIT_VERDICT, arguments=call.arguments)

        try:
            submission = VerdictSubmission.model_validate(call.arguments)
        except ValidationError as exc:
            state.gate_failures += 1
            failures = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
            tracer.event("gate_failed", reason="schema", failures=failures)
            return _tool_result_block(
                call.id,
                tools_module.serialise({"status": "rejected", "failures": failures}),
                is_error=True,
            )

        gate = check_evidence_gate(submission, state.tool_outputs)
        if gate.passed:
            state.submission, state.gate = submission, gate
            result = TriageResult.from_submission(
                submission,
                ticket=state.ticket,
                trace_id=tracer.run_id,
                steps_used=state.steps,
                stop_reason=StopReason.VERDICT,
                gate=gate,
                gate_retries=state.gate_failures,
            )
            tracer.event("verdict", **result.model_dump(mode="json"))
            return result

        state.gate_failures += 1
        tracer.event("gate_failed", reason="evidence", failures=gate.failures,
                     attempt=state.gate_failures)
        state.submission, state.gate = submission, gate
        return _tool_result_block(
            call.id,
            tools_module.serialise({
                "status": "rejected",
                "failures": gate.failures,
                "note": "Your verdict was not accepted. Fix the points above and call "
                        "submit_verdict again. Quotes must be copied verbatim from a tool "
                        "result you actually received.",
            }),
            is_error=True,
        )

    def _forced_finish(
        self,
        ticket: Ticket,
        messages: list[dict[str, Any]],
        tracer: Tracer,
        state: "_RunState",
        stop_reason: StopReason,
        started: float,
        *,
        detail: str,
        pending: list[dict[str, Any]] | None = None,
    ) -> TriageResult:
        """Make one last call with submit_verdict forced, so every run returns a verdict.

        A guard firing must not mean returning nothing. Forcing the tool guarantees a
        schema-valid answer - usually NEEDS_INFO - instead of an empty timeout, and the
        stop_reason on the result says plainly that the agent did not choose to stop.
        """
        tracer.event("guard_tripped", guard=stop_reason.value, detail=detail)

        if pending:
            messages.append({"role": "user", "content": pending})
        messages.append({
            "role": "user",
            "content": [{
                "type": "text",
                "text": f"Stop gathering evidence: {detail}. Submit your verdict now using "
                        "only what you already have. If it does not support a BUG or a CR, "
                        "return NEEDS_INFO and say exactly what you would need to settle it.",
            }],
        })

        try:
            response = self._call_llm(
                messages, tracer, state.steps,
                tool_choice={"type": "tool", "name": SUBMIT_VERDICT},
            )
            call = next((c for c in response.tool_calls if c.name == SUBMIT_VERDICT), None)
            submission = VerdictSubmission.model_validate(call.arguments) if call else None
        except (LLMError, ValidationError, AttributeError) as exc:
            tracer.event("guard_tripped", guard="forced_finish_failed", detail=str(exc))
            submission = None

        if submission is None:
            submission = state.submission
        if submission is None:
            submission = VerdictSubmission(
                verdict=Verdict.NEEDS_INFO,
                rationale=f"The run stopped before a verdict was reached ({detail}).",
                missing_info=["Re-run this ticket, or have an analyst review it by hand."],
                draft_reply="We are still looking into this and will come back to you shortly.",
            )

        gate = check_evidence_gate(submission, state.tool_outputs)
        if not gate.passed:
            submission = _downgrade(submission, gate, detail)
            gate = check_evidence_gate(submission, state.tool_outputs)
            tracer.event("guard_tripped", guard="downgraded_to_needs_info",
                         detail="; ".join(gate.failures) or detail)

        result = TriageResult.from_submission(
            submission,
            ticket=ticket,
            trace_id=tracer.run_id,
            steps_used=state.steps,
            stop_reason=stop_reason,
            gate=gate,
            gate_retries=state.gate_failures,
        )
        tracer.event("verdict", **result.model_dump(mode="json"))
        tracer.event("run_finished", steps_used=state.steps, stop_reason=stop_reason.value,
                     elapsed_ms=int((time.perf_counter() - started) * 1000))
        return result


@dataclass
class _RunState:
    steps: int = 0
    tool_errors: int = 0
    duplicates: int = 0
    no_progress: int = 0
    gate_failures: int = 0
    seen_calls: dict[tuple[str, str], dict] = None  # type: ignore[assignment]
    tool_outputs: list[str] = None  # type: ignore[assignment]
    submission: VerdictSubmission | None = None
    gate: EvidenceGateReport | None = None
    ticket: Ticket | None = None

    def __post_init__(self) -> None:
        self.seen_calls = {}
        self.tool_outputs = []


def _assistant_blocks(response: LLMResponse) -> list[dict[str, Any]]:
    """The assistant turn to replay. Never empty - the API rejects empty content."""
    if response.raw_content:
        return response.raw_content
    blocks: list[dict[str, Any]] = []
    if response.text:
        blocks.append({"type": "text", "text": response.text})
    for call in response.tool_calls:
        blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments})
    return blocks or [{"type": "text", "text": "(no content)"}]


def _tool_result_block(tool_use_id: str, content: str, *, is_error: bool = False) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        block["is_error"] = True
    return block


def _downgrade(submission: VerdictSubmission, gate: EvidenceGateReport, detail: str) -> VerdictSubmission:
    """Turn a submission that cannot pass the gate into an honest NEEDS_INFO.

    Ungrounded quotes are dropped rather than carried across: the point of the gate is
    that nothing reaches the client that cannot be traced to a tool result. What the
    verdict was going to be, and why it was not accepted, go into missing_info, so the
    reviewer sees the downgrade rather than a mysteriously cautious answer.
    """
    grounded = [e for e in submission.evidence if e.quote not in gate.ungrounded_quotes]
    questions = list(submission.missing_info) or [
        f"The evidence did not support a {submission.verdict.value} verdict: "
        f"{'; '.join(gate.failures)}"
    ]
    if submission.verdict is not Verdict.NEEDS_INFO:
        questions.append(
            f"This was submitted as {submission.verdict.value} but did not clear the evidence "
            f"check ({detail}). An analyst should confirm before it goes to the client."
        )
    return VerdictSubmission(
        verdict=Verdict.NEEDS_INFO,
        rationale=submission.rationale,
        evidence=grounded,
        missing_info=questions,
        draft_reply=submission.draft_reply,
    )


def triage(ticket: Ticket, *, llm=None, toolbox=None, config: AgentConfig | None = None) -> TriageResult:
    """Convenience entry point used by the CLI, the evals and the API."""
    from src.llm import get_client

    agent = TriageAgent(llm or get_client(), toolbox, config=config)
    return agent.run(ticket)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from src.trace import format_summary, summarise

    parser = argparse.ArgumentParser(description="Triage one ticket.")
    parser.add_argument("ticket", help="path to a ticket JSON file")
    parser.add_argument("--backend", default=None, help="fake | anthropic | ollama")
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)

    from src.llm import get_client

    ticket = Ticket.from_json_file(args.ticket)
    agent = TriageAgent(get_client(args.backend, model=args.model))
    result = agent.run(ticket)
    print(format_summary(summarise(Path(DEFAULT_TRACE_DIR) / f"{result.trace_id}.jsonl")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
