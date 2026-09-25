"""Shared data models for the FinReq triage agent.

Every layer — tools, agent loop, API, evals — imports its types from here, so the
FastAPI response schema and the eval harness cannot drift apart.

Two deliberate properties of this module:

1. The model never reports its own confidence. `VerdictSubmission` is the exact set
   of fields the LLM is allowed to supply, and it forbids extra keys, so a
   self-reported confidence is rejected rather than quietly trusted. Confidence is
   computed afterwards by `compute_confidence` from facts the harness can check:
   how many sources were cited, how varied they were, whether any quote failed the
   grounding check, and whether the run stopped on its own or was cut off.

2. Evidence quotes are checked against the text the tools actually returned, after
   normalising both sides (JSON unescaping, Unicode and punctuation folding,
   whitespace collapsing). Citation quality is therefore measurable without a
   second LLM judging it.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator


# --------------------------------------------------------------------------- enums


class Verdict(str, Enum):
    BUG = "BUG"
    CR = "CR"
    NEEDS_INFO = "NEEDS_INFO"


class EvidenceKind(str, Enum):
    SON = "son"          # a retrieved SON section
    CONFIG = "config"    # a configuration parameter row (or a confirmed absence)
    CODE = "code"        # a core or client-custom code snippet
    DATA = "data"        # authorisation records


class StopReason(str, Enum):
    VERDICT = "verdict"                      # the agent called submit_verdict and it passed the gate
    GATE_EXHAUSTED = "gate_exhausted"         # it submitted, but never with evidence that held up
    STEP_BUDGET = "step_budget"
    TOOL_ERROR_BUDGET = "tool_error_budget"
    NO_PROGRESS = "no_progress"
    TIMEOUT = "timeout"


class ProductArea(str, Enum):
    STATEMENTS = "statements"
    FEES = "fees"
    AUTHORISATIONS = "authorisations"
    CYCLES = "cycles"
    OTHER = "other"


# ------------------------------------------------------------------------- grounding

_CURLY_PUNCTUATION = {
    ord("‘"): "'",
    ord("’"): "'",
    ord("“"): '"',
    ord("”"): '"',
    ord("–"): "-",
    ord("—"): "-",
    ord(" "): " ",
}


def json_unescape(text: str) -> str:
    """Decode JSON string escapes if the text carries any, otherwise return it as is.

    Tool results reach the model as JSON, so a quote copied back out of one often
    still contains \\n or \\" sequences. Both sides of a grounding comparison are
    passed through this so an escaped copy still matches the original.
    """
    if "\\" not in text:
        return text
    # Normalise quoting, then let the JSON decoder do the unescaping. An invalid
    # escape sequence is not an error here — the text is simply left alone.
    candidate = text.replace('\\"', '"').replace('"', '\\"')
    # Raw control characters are illegal inside a JSON string literal, so a
    # pretty-printed tool result — which is full of real newlines — would fail to
    # decode and silently come back still escaped. Escaping them first means the
    # whole of a serialised tool result normalises the same way a fragment does.
    for raw, escaped in (("\r", "\\r"), ("\n", "\\n"), ("\t", "\\t")):
        candidate = candidate.replace(raw, escaped)
    try:
        decoded = json.loads(f'"{candidate}"')
    except json.JSONDecodeError:
        return text
    return decoded if isinstance(decoded, str) else text


_JSON_PUNCTUATION_SPACING = re.compile(r"\s*([{}\[\],:])\s*")


def normalise_for_match(text: str) -> str:
    """Fold a string to the form used for substring comparison.

    JSON unescape, NFKC normalise, fold curly quotes and dashes to ASCII, collapse
    runs of whitespace, drop whitespace around JSON structural punctuation, strip.
    Case is preserved: a quote is meant to be verbatim, and case-folding would let a
    paraphrase slip through.

    The punctuation-spacing rule earns its place. Tool results are pretty-printed
    JSON, so an aggregate reaches the model as `"by_status": {\\n  "DROPPED": 4` and
    is naturally quoted back compactly as `"by_status": {"DROPPED": 4`. The content
    is identical and the difference is two spaces, but a plain substring test calls
    that an invented quote and rejects a correct verdict. Both sides are folded the
    same way, so this loosens formatting, never content.
    """
    folded = json_unescape(text)
    folded = unicodedata.normalize("NFKC", folded)
    folded = folded.translate(_CURLY_PUNCTUATION)
    folded = re.sub(r"\s+", " ", folded)
    folded = _JSON_PUNCTUATION_SPACING.sub(r"\1", folded)
    return folded.strip()


def is_grounded(quote: str, tool_outputs: Iterable[str]) -> bool:
    """True when the quote appears verbatim in at least one tool output."""
    needle = normalise_for_match(quote)
    if not needle:
        return False
    return any(needle in normalise_for_match(output) for output in tool_outputs)


# -------------------------------------------------------------------------- models


class Ticket(BaseModel):
    """A client ticket arriving for triage."""

    ticket_id: str
    client_id: str
    subject: str
    body: str
    product_area: ProductArea = ProductArea.OTHER
    submitted_at: datetime
    reporter_role: str = "Client Ops Analyst"

    @classmethod
    def from_json_file(cls, path: str | Path) -> "Ticket":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


class Evidence(BaseModel):
    """One citation supporting the verdict."""

    kind: EvidenceKind
    ref: str = Field(
        description="Stable pointer: 'SON-001#3.2', a parameter name, 'path.py:L15-47', "
        "or a description of the record filter used."
    )
    quote: str = Field(
        max_length=600,
        description="Verbatim text copied from the tool result. Checked against what the "
        "tools actually returned.",
    )
    why_it_matters: str = Field(description="One sentence linking this citation to the verdict.")

    @field_validator("ref", "quote", "why_it_matters")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class VerdictSubmission(BaseModel):
    """Exactly what the LLM may supply when it calls `submit_verdict`.

    `extra="forbid"` is load-bearing: the model cannot smuggle in a confidence score,
    a made-up trace id, or any other field the harness is responsible for.
    """

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    rationale: str = Field(description="Up to five sentences, internal-facing.")
    evidence: list[Evidence] = Field(default_factory=list)
    missing_info: list[str] = Field(
        default_factory=list,
        description="Specific questions to put to the client. Required for NEEDS_INFO.",
    )
    draft_reply: str = Field(description="Client-facing reply. No internal file paths.")


class EvidenceGateReport(BaseModel):
    """Result of checking a submission against the evidence rules. All machine-checked."""

    passed: bool
    failures: list[str] = Field(default_factory=list)
    son_citations: int = 0
    corroborating_kinds: list[str] = Field(
        default_factory=list, description="Distinct non-SON evidence kinds cited."
    )
    ungrounded_quotes: list[str] = Field(default_factory=list)
    missing_info_count: int = 0


class TriageResult(BaseModel):
    """What the system returns. Built by the harness, never by the model alone."""

    ticket_id: str
    client_id: str
    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0, description="Computed by compute_confidence.")
    rationale: str
    evidence: list[Evidence]
    missing_info: list[str]
    draft_reply: str
    trace_id: str
    steps_used: int
    stop_reason: StopReason
    gate: EvidenceGateReport

    @classmethod
    def from_submission(
        cls,
        submission: VerdictSubmission,
        *,
        ticket: Ticket,
        trace_id: str,
        steps_used: int,
        stop_reason: StopReason,
        gate: EvidenceGateReport,
        gate_retries: int = 0,
    ) -> "TriageResult":
        return cls(
            ticket_id=ticket.ticket_id,
            client_id=ticket.client_id,
            verdict=submission.verdict,
            confidence=compute_confidence(submission.verdict, gate, stop_reason, gate_retries),
            rationale=submission.rationale,
            evidence=submission.evidence,
            missing_info=submission.missing_info,
            draft_reply=submission.draft_reply,
            trace_id=trace_id,
            steps_used=steps_used,
            stop_reason=stop_reason,
            gate=gate,
        )


# ---------------------------------------------------------------------- the gate

MIN_EVIDENCE_FOR_DECISION = 2


def check_evidence_gate(
    submission: VerdictSubmission, tool_outputs: Iterable[str]
) -> EvidenceGateReport:
    """Validate a submission against the evidence rules.

    A BUG or CR verdict must rest on the spec *and* on something observed: at least
    two citations, at least one of them a SON section and at least one of them not.
    A verdict resting only on the spec is a reading, not a triage. NEEDS_INFO instead
    has to name what is missing. Every quote must be traceable to a tool result.
    """
    outputs = list(tool_outputs)
    failures: list[str] = []

    son_citations = sum(1 for e in submission.evidence if e.kind is EvidenceKind.SON)
    corroborating_kinds = sorted(
        {e.kind.value for e in submission.evidence if e.kind is not EvidenceKind.SON}
    )
    ungrounded = [e.quote for e in submission.evidence if not is_grounded(e.quote, outputs)]

    if submission.verdict in (Verdict.BUG, Verdict.CR):
        if len(submission.evidence) < MIN_EVIDENCE_FOR_DECISION:
            failures.append(
                f"{submission.verdict.value} needs at least {MIN_EVIDENCE_FOR_DECISION} "
                f"evidence items, got {len(submission.evidence)}."
            )
        if son_citations < 1:
            failures.append(
                f"{submission.verdict.value} needs at least one SON citation showing what "
                "was or was not specified."
            )
        if not corroborating_kinds:
            failures.append(
                f"{submission.verdict.value} needs at least one non-SON citation "
                "(config, code or data) showing what the system actually does."
            )
    else:
        if not submission.missing_info:
            failures.append("NEEDS_INFO must list at least one specific question for the client.")

    for quote in ungrounded:
        excerpt = quote[:80] + ("..." if len(quote) > 80 else "")
        failures.append(f"Quote not found in any tool result: {excerpt!r}")

    return EvidenceGateReport(
        passed=not failures,
        failures=failures,
        son_citations=son_citations,
        corroborating_kinds=corroborating_kinds,
        ungrounded_quotes=ungrounded,
        missing_info_count=len(submission.missing_info),
    )


# ---------------------------------------------------------------- confidence

# Deterministic weights. Changing these changes every reported confidence, so they
# live here as named constants and are asserted in tests rather than tuned by feel.
CONF_BASE = 0.30
CONF_SON_WEIGHT = 0.20             # full weight at two or more SON citations
CONF_CORROBORATION_WEIGHT = 0.30   # full weight at three distinct non-SON evidence kinds
CONF_CLEAN_FINISH_BONUS = 0.10     # the gate passed first time, with no retry
CONF_FORCED_STOP_PENALTY = 0.25    # a guard stopped the run rather than the agent
CONF_UNGROUNDED_PENALTY = 0.10     # per quote that could not be traced to a tool result
CONF_FLOOR = 0.05
CONF_CEILING = 0.95


def compute_confidence(
    verdict: Verdict,
    gate: EvidenceGateReport,
    stop_reason: StopReason,
    gate_retries: int = 0,
) -> float:
    """Derive confidence from observable facts about the run.

    Deliberately not asked of the model: a self-reported score is unfalsifiable and
    tends to sit at 0.9 regardless of how thin the evidence was. Everything here can
    be recomputed from the trace, which makes it auditable and makes a low score
    explainable to a client ("one source, and the run was cut short").

    Breadth of evidence is measured in distinct *kinds* rather than count, because
    three quotes from one file are weaker than a spec section, a config value and a
    data observation that agree.
    """
    if not gate.passed:
        return CONF_FLOOR

    score = CONF_BASE
    score += CONF_SON_WEIGHT * min(gate.son_citations, 2) / 2

    if verdict is Verdict.NEEDS_INFO:
        # For NEEDS_INFO the useful signal is how precisely the gap was named.
        score += CONF_CORROBORATION_WEIGHT * min(gate.missing_info_count, 3) / 3
    else:
        score += CONF_CORROBORATION_WEIGHT * min(len(gate.corroborating_kinds), 3) / 3

    if gate_retries == 0:
        score += CONF_CLEAN_FINISH_BONUS
    if stop_reason is not StopReason.VERDICT:
        score -= CONF_FORCED_STOP_PENALTY
    score -= CONF_UNGROUNDED_PENALTY * len(gate.ungrounded_quotes)

    return round(min(max(score, CONF_FLOOR), CONF_CEILING), 2)
