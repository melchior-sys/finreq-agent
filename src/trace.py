"""Append-only JSONL tracing for agent runs.

One file per run, one JSON object per line, flushed as it happens rather than
buffered - a run that crashes still leaves everything up to the crash on disk,
which is when a trace is most useful.

JSONL over a table because a trace is meant to be read: `cat` it, `grep` it, diff
two of them, attach one to a pull request. Cost per triage is a jq one-liner.

Everything written goes through `redact()` first. Nothing in this module ever
receives the API key, and the redaction pass is there so that a future change which
accidentally hands it a request object cannot leak one.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm import estimate_cost_usd, redact  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRACE_DIR = PROJECT_ROOT / "traces"


class Tracer:
    """Writes one JSONL trace file for one run."""

    def __init__(self, run_id: str, trace_dir: Path | str = DEFAULT_TRACE_DIR):
        self.run_id = run_id
        self.path = Path(trace_dir) / f"{run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0

    def event(self, event_type: str, **payload: Any) -> None:
        self._seq += 1
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "run_id": self.run_id,
            "seq": self._seq,
            "type": event_type,
            "payload": redact(payload),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def read_events(path: Path | str) -> list[dict[str, Any]]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def summarise(path: Path | str) -> dict[str, Any]:
    """Reduce a trace to the numbers worth reporting after a run.

    Everything here is derived from the trace alone, never from the in-memory run,
    so the summary is exactly what a reviewer reading the file later would compute.
    """
    events = read_events(path)
    by_type: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_type.setdefault(event["type"], []).append(event["payload"])

    started = (by_type.get("run_started") or [{}])[0]
    finished = (by_type.get("run_finished") or [{}])[-1]
    verdict = (by_type.get("verdict") or [{}])[-1]

    llm_calls = by_type.get("llm_response", [])
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    cost = 0.0
    cost_known = bool(llm_calls)
    for call in llm_calls:
        for key in totals:
            totals[key] += (call.get("usage") or {}).get(key, 0)
        if call.get("cost_usd") is None:
            cost_known = False
        else:
            cost += call["cost_usd"]

    return {
        "run_id": (events[0]["run_id"] if events else None),
        "ticket_id": started.get("ticket_id"),
        "backend": started.get("backend"),
        "model": started.get("model"),
        "tool_sequence": [payload["tool"] for payload in by_type.get("tool_call", [])],
        "llm_calls": len(llm_calls),
        "steps_used": finished.get("steps_used"),
        "stop_reason": finished.get("stop_reason"),
        "verdict": verdict.get("verdict"),
        "confidence": verdict.get("confidence"),
        "evidence": verdict.get("evidence", []),
        "gate_retries": len(by_type.get("gate_failed", [])),
        "guards_tripped": [payload.get("guard") for payload in by_type.get("guard_tripped", [])],
        "tool_errors": sum(1 for payload in by_type.get("tool_result", []) if payload.get("is_error")),
        "usage": totals,
        "cost_usd": round(cost, 6) if cost_known else None,
        "latency_ms": finished.get("elapsed_ms"),
    }


def format_summary(summary: dict[str, Any]) -> str:
    lines = [
        f"run        {summary['run_id']}",
        f"ticket     {summary['ticket_id']}",
        f"backend    {summary['backend']} ({summary['model']})",
        "",
        f"tools      {' -> '.join(summary['tool_sequence']) or '(none)'}",
        f"steps      {summary['steps_used']} across {summary['llm_calls']} LLM calls",
        f"stop       {summary['stop_reason']}",
        f"guards     {', '.join(summary['guards_tripped']) or 'none tripped'}",
        f"gate       {summary['gate_retries']} retr{'y' if summary['gate_retries'] == 1 else 'ies'}, "
        f"{summary['tool_errors']} tool error(s)",
        "",
        f"verdict    {summary['verdict']}  (confidence {summary['confidence']})",
    ]
    for item in summary["evidence"]:
        quote = item.get("quote", "")
        if len(quote) > 88:
            quote = quote[:88] + "..."
        lines.append(f"  [{item.get('kind'):>6}] {item.get('ref')}")
        lines.append(f"           \"{quote}\"")

    usage = summary["usage"]
    cost = "unpriced model" if summary["cost_usd"] is None else f"${summary['cost_usd']:.6f}"
    lines += [
        "",
        f"tokens     {usage['input_tokens']} in / {usage['output_tokens']} out"
        + (
            f" ({usage['cache_read_tokens']} cache read, {usage['cache_write_tokens']} cache write)"
            if usage["cache_read_tokens"] or usage["cache_write_tokens"]
            else ""
        ),
        f"cost       {cost}",
        f"wall clock {summary['latency_ms']} ms",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarise an agent trace.")
    parser.add_argument("trace", nargs="?", help="path to a traces/*.jsonl file; defaults to the newest")
    parser.add_argument("--json", action="store_true", help="emit the summary as JSON")
    args = parser.parse_args(argv)

    if args.trace:
        path = Path(args.trace)
    else:
        traces = sorted(DEFAULT_TRACE_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not traces:
            print("no traces found", file=sys.stderr)
            return 1
        path = traces[-1]

    summary = summarise(path)
    print(json.dumps(summary, indent=2) if args.json else format_summary(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
