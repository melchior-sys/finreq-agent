"""Run the eval set and report what actually matters.

Accuracy alone is close to uninformative here: three verdict classes, sixteen cases,
and a constant "always CR" predictor already scores 37.5%. One flipped case moves the
headline by six points. So the report leads with four numbers — accuracy against the
majority-class baseline, evidence-anchor recall, grounded-citation rate, and the
distribution of stop reasons — and prices the run as cost per *correct* verdict
rather than cost per call, because a cheap wrong answer is not cheap.

    python -m evals.run_evals --backend anthropic --mode retrieval
    python -m evals.run_evals --backend anthropic --mode stuffed
    python -m evals.run_evals --backend ollama    --mode retrieval
    python -m evals.run_evals --retrieval-only          # no LLM calls, no cost

The harness calls `src.agent.TriageAgent` — the same code path the API will use.
Nothing about the loop is reimplemented here, or the evals would be measuring a
second implementation rather than the one that ships.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.held_out_queries import IRRELEVANT_HELD_OUT, RELEVANT_HELD_OUT  # noqa: E402
from src import rag, tools as tools_module  # noqa: E402
from src.agent import TriageAgent  # noqa: E402
from src.llm import get_client  # noqa: E402
from src.models import Ticket, Verdict  # noqa: E402
from src.trace import DEFAULT_TRACE_DIR, summarise  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CASES_PATH = PROJECT_ROOT / "evals" / "cases.jsonl"
RESULTS_DIR = PROJECT_ROOT / "evals" / "results"
REPORT_PATH = PROJECT_ROOT / "evals" / "results.md"


# ------------------------------------------------------------ stuffed baseline


class StuffedToolbox(tools_module.Toolbox):
    """Control arm: `search_son` returns the whole corpus instead of retrieving.

    The entire SON corpus is about 2,200 tokens, so it fits in a prompt with room to
    spare. That makes "why retrieve at all?" a fair question, and the honest way to
    answer it is to run the same agent both ways rather than to argue.

    The chunks keep their ids. Without them every SON citation would fail the gate's
    ref check and the comparison would be measuring the gate rather than retrieval.
    """

    def search_son(self, query: str, client_id: str = tools_module.DEFAULT_CLIENT,
                   top_k: int = rag.DEFAULT_TOP_K) -> dict:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT c.chunk_id, c.son_id, c.section, c.heading, c.text, d.title, d.version
                FROM son_chunks c JOIN son_docs d ON d.son_id = c.son_id
                WHERE d.client_id = ? ORDER BY c.chunk_id
                """,
                (client_id,),
            ).fetchall()
        finally:
            conn.close()

        return {
            "status": "ok",
            "query": query,
            "client_id": client_id,
            "mode": "stuffed",
            "note": (
                "Every section of this client's Statements of Need is included below, "
                "unfiltered. Nothing has been selected for relevance to the query."
            ),
            "chunks": [
                {
                    "chunk_id": row["chunk_id"],
                    "son_id": row["son_id"],
                    "title": row["title"],
                    "version": row["version"],
                    "section": row["section"],
                    "heading": row["heading"],
                    "text": row["text"],
                    "score": None,
                }
                for row in rows
            ],
        }


# ------------------------------------------------------------------- scoring


def anchor_hit(kind: str, anchor: str, refs: list[str]) -> bool:
    """Did the verdict cite the thing this case turns on?

    Matching mirrors the gate: a SON ref below the expected section counts (citing
    3.2.1 satisfies an anchor of 3.2), a code ref carries a line range after the
    path, and parameter names are compared case-insensitively.
    """
    for ref in refs:
        ref = ref.strip()
        if kind == "son" and (ref == anchor or ref.startswith(f"{anchor}.")):
            return True
        if kind == "config" and ref.upper() == anchor.upper():
            return True
        if kind == "code" and ref.split(":", 1)[0].replace("\\", "/") == anchor:
            return True
        if kind == "data" and anchor.lower() in ref.lower():
            return True
    return False


def score_case(case: dict, result, summary: dict, wall_ms: int) -> dict:
    expected = case["expected_verdict"]
    predicted = result.verdict.value
    refs_by_kind: dict[str, list[str]] = {}
    for item in result.evidence:
        refs_by_kind.setdefault(item.kind.value, []).append(item.ref)

    anchors = case.get("expected_anchors") or {}
    expected_anchors = [(kind, a) for kind, values in anchors.items() for a in values]
    hits = [anchor_hit(kind, a, refs_by_kind.get(kind, [])) for kind, a in expected_anchors]

    return {
        "case_id": case["case_id"],
        "tags": case.get("tags", []),
        "expected": expected,
        "predicted": predicted,
        "correct": expected == predicted,
        "anchors_expected": len(expected_anchors),
        "anchors_hit": sum(hits),
        "missed_anchors": [f"{k}:{a}" for (k, a), hit in zip(expected_anchors, hits) if not hit],
        "citations": len(result.evidence),
        "gate_passed": result.gate.passed,
        "gate_retries": summary["gate_retries"],
        "ungrounded": len(result.gate.ungrounded_quotes),
        "stop_reason": result.stop_reason.value,
        "confidence": result.confidence,
        "steps": result.steps_used,
        "llm_calls": summary["llm_calls"],
        "tool_sequence": summary["tool_sequence"],
        "tokens_in": summary["usage"]["input_tokens"],
        "tokens_out": summary["usage"]["output_tokens"],
        "cost_usd": summary["cost_usd"],
        "latency_ms": wall_ms,
        "trace_id": result.trace_id,
    }


def aggregate(rows: list[dict], cases: list[dict]) -> dict:
    n = len(rows)
    correct = sum(1 for r in rows if r["correct"])
    costs = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
    total_cost = sum(costs) if costs else None
    latencies = sorted(r["latency_ms"] for r in rows)

    anchors_expected = sum(r["anchors_expected"] for r in rows)
    anchors_hit = sum(r["anchors_hit"] for r in rows)

    # The number to beat: always predicting the commonest class in the case set.
    majority = Counter(c["expected_verdict"] for c in cases).most_common(1)[0]

    confusion: dict[str, Counter] = {v.value: Counter() for v in Verdict}
    for row in rows:
        confusion[row["expected"]][row["predicted"]] += 1

    return {
        "cases": n,
        "accuracy": correct / n if n else 0.0,
        "correct": correct,
        "majority_baseline": majority[1] / len(cases),
        "majority_class": majority[0],
        "anchor_recall": (anchors_hit / anchors_expected) if anchors_expected else None,
        "anchors_hit": anchors_hit,
        "anchors_expected": anchors_expected,
        "grounded_citation_rate": (
            1 - sum(r["ungrounded"] for r in rows) / max(sum(r["citations"] for r in rows), 1)
        ),
        "gate_retry_rate": sum(1 for r in rows if r["gate_retries"]) / n if n else 0.0,
        "stop_reasons": dict(Counter(r["stop_reason"] for r in rows)),
        "mean_latency_ms": int(statistics.mean(latencies)) if latencies else 0,
        "p95_latency_ms": latencies[max(0, int(len(latencies) * 0.95) - 1)] if latencies else 0,
        "total_cost_usd": round(total_cost, 4) if total_cost is not None else None,
        "cost_per_case_usd": round(total_cost / n, 4) if total_cost is not None and n else None,
        # The figure that matters operationally: a wrong verdict has to be redone by
        # a human, so its cost is sunk, not saved.
        "cost_per_correct_usd": (
            round(total_cost / correct, 4) if total_cost is not None and correct else None
        ),
        "confusion": {k: dict(v) for k, v in confusion.items()},
    }


# ---------------------------------------------------------- retrieval report


def retrieval_report() -> dict:
    """Score the held-out queries at the calibrated floor. No LLM calls."""
    retriever = rag.Retriever()
    rows, matrix = retriever._load()  # noqa: SLF001 — the report needs raw scores
    index_of = {row["chunk_id"]: i for i, row in enumerate(rows)}
    floor = rag.MIN_SCORE

    strict = doc = 0
    relevant_scores = []
    for query, expected in RELEVANT_HELD_OUT:
        scores = matrix @ rag.embed_query(query)
        order = np.argsort(-scores)
        returned = [rows[i]["chunk_id"] for i in order if scores[i] >= floor][: rag.DEFAULT_TOP_K]
        strict += expected in returned
        doc += any(cid.startswith(expected.split("#")[0]) for cid in returned)
        relevant_scores.append(float(scores[index_of[expected]]))

    irrelevant_best = []
    admitted = 0
    for query in IRRELEVANT_HELD_OUT:
        best = float(np.max(matrix @ rag.embed_query(query)))
        irrelevant_best.append(best)
        admitted += best >= floor

    return {
        "floor": floor,
        "relevant": len(RELEVANT_HELD_OUT),
        "irrelevant": len(IRRELEVANT_HELD_OUT),
        "strict_recall": strict / len(RELEVANT_HELD_OUT),
        "doc_recall": doc / len(RELEVANT_HELD_OUT),
        "false_admit_rate": admitted / len(IRRELEVANT_HELD_OUT),
        "min_relevant_score": round(min(relevant_scores), 4),
        "max_irrelevant_score": round(max(irrelevant_best), 4),
        # The early-warning metric: it shrinks as documents are added and reaches
        # zero before any recall number moves.
        "floor_margin": round(floor - max(irrelevant_best), 4),
    }


# ------------------------------------------------------------------ the run


def load_cases(path: Path, only: list[str] | None = None) -> list[dict]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [c for c in cases if not only or c["case_id"] in only]


def case_ticket(case: dict) -> Ticket:
    if "ticket_file" in case:
        return Ticket.from_json_file(PROJECT_ROOT / case["ticket_file"])
    return Ticket.model_validate(case["ticket"])


def run_arm(cases: list[dict], backend: str, mode: str, model: str | None) -> tuple[list[dict], dict]:
    toolbox = StuffedToolbox() if mode == "stuffed" else tools_module.Toolbox()
    rows: list[dict] = []

    for index, case in enumerate(cases, start=1):
        ticket = case_ticket(case)
        agent = TriageAgent(get_client(backend, model=model), toolbox)
        started = time.perf_counter()
        try:
            result = agent.run(ticket)
        except Exception as exc:  # a failed case is a data point, not a crashed run
            print(f"  {index:>2}/{len(cases)} {case['case_id']}  ERROR {type(exc).__name__}: {exc}")
            rows.append({
                "case_id": case["case_id"], "tags": case.get("tags", []),
                "expected": case["expected_verdict"], "predicted": "ERROR", "correct": False,
                "anchors_expected": 0, "anchors_hit": 0, "missed_anchors": [], "citations": 0,
                "gate_passed": False, "gate_retries": 0, "ungrounded": 0, "stop_reason": "error",
                "confidence": 0.0, "steps": 0, "llm_calls": 0, "tool_sequence": [],
                "tokens_in": 0, "tokens_out": 0, "cost_usd": None,
                "latency_ms": int((time.perf_counter() - started) * 1000), "trace_id": None,
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        wall_ms = int((time.perf_counter() - started) * 1000)
        summary = summarise(Path(DEFAULT_TRACE_DIR) / f"{result.trace_id}.jsonl")
        row = score_case(case, result, summary, wall_ms)
        rows.append(row)

        mark = "ok " if row["correct"] else "XX "
        anchors = f"{row['anchors_hit']}/{row['anchors_expected']}"
        print(
            f"  {index:>2}/{len(cases)} {mark}{case['case_id']}  "
            f"{row['expected']:>10} -> {row['predicted']:<10} anchors {anchors}  "
            f"{row['stop_reason']:<15} {wall_ms/1000:5.1f}s"
        )

    return rows, aggregate(rows, cases)


# ------------------------------------------------------------------ reporting


def format_report(meta: dict, summary: dict, rows: list[dict], retrieval: dict | None) -> str:
    lines = [
        f"# Eval run — {meta['backend']} / {meta['mode']}",
        "",
        f"_{meta['timestamp']} · model `{meta['model']}` · {summary['cases']} cases_",
        "",
        "## Headline",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Verdict accuracy | **{summary['accuracy']:.0%}** ({summary['correct']}/{summary['cases']}) |",
        f"| Majority-class baseline | {summary['majority_baseline']:.0%} (always {summary['majority_class']}) |",
    ]
    if summary["anchor_recall"] is not None:
        lines.append(
            f"| Evidence-anchor recall | **{summary['anchor_recall']:.0%}** "
            f"({summary['anchors_hit']}/{summary['anchors_expected']}) |"
        )
    lines += [
        f"| Grounded-citation rate | {summary['grounded_citation_rate']:.0%} |",
        f"| Runs needing a gate retry | {summary['gate_retry_rate']:.0%} |",
        f"| Mean latency | {summary['mean_latency_ms'] / 1000:.1f}s |",
        f"| p95 latency | {summary['p95_latency_ms'] / 1000:.1f}s |",
    ]
    if summary["total_cost_usd"] is not None:
        lines += [
            f"| Cost per case | ${summary['cost_per_case_usd']:.4f} |",
            f"| **Cost per correct verdict** | **${summary['cost_per_correct_usd']:.4f}** |",
            f"| Total | ${summary['total_cost_usd']:.4f} |",
        ]
    else:
        lines.append("| Cost | not priced (local model — compute is not free, the API call is) |")

    lines += ["", "## Where it stopped", "", "| Stop reason | Cases |", "|---|---:|"]
    for reason, count in sorted(summary["stop_reasons"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{reason}` | {count} |")

    lines += ["", "## Confusion", "", "| expected \\ predicted | BUG | CR | NEEDS_INFO |", "|---|---:|---:|---:|"]
    for expected in ("BUG", "CR", "NEEDS_INFO"):
        row = summary["confusion"].get(expected, {})
        lines.append(
            f"| **{expected}** | {row.get('BUG', 0)} | {row.get('CR', 0)} | {row.get('NEEDS_INFO', 0)} |"
        )

    lines += [
        "", "## Per case", "",
        "| Case | Expected | Predicted | Anchors | Stop | Conf | Steps | Latency | Cost |",
        "|---|---|---|---:|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        mark = "" if row["correct"] else " ⚠"
        cost = f"${row['cost_usd']:.4f}" if row["cost_usd"] is not None else "—"
        lines.append(
            f"| {row['case_id']}{mark} | {row['expected']} | {row['predicted']} | "
            f"{row['anchors_hit']}/{row['anchors_expected']} | `{row['stop_reason']}` | "
            f"{row['confidence']:.2f} | {row['steps']} | {row['latency_ms'] / 1000:.1f}s | {cost} |"
        )

    failures = [r for r in rows if not r["correct"]]
    lines += ["", "## Failures", ""]
    if not failures:
        lines.append("None.")
    for row in failures:
        lines.append(
            f"- **{row['case_id']}** expected {row['expected']}, got {row['predicted']} "
            f"(stop `{row['stop_reason']}`, anchors {row['anchors_hit']}/{row['anchors_expected']}"
            + (f", missed {', '.join(row['missed_anchors'])}" if row["missed_anchors"] else "")
            + f"). Trace `{row['trace_id']}`."
        )

    missed_only = [r for r in rows if r["correct"] and r["missed_anchors"]]
    if missed_only:
        lines += ["", "### Right verdict, wrong reasons", ""]
        for row in missed_only:
            lines.append(f"- **{row['case_id']}** missed {', '.join(row['missed_anchors'])}.")

    if retrieval:
        lines += [
            "", "## Retrieval, held-out queries", "",
            "Written without reference to the calibration set or the floor it produced. "
            "The numbers in `retrieval_floor.md` are training accuracy; these are not.",
            "",
            "| Metric | Held out |",
            "|---|---|",
            f"| Strict recall @4 | {retrieval['strict_recall']:.0%} |",
            f"| Document recall @4 | {retrieval['doc_recall']:.0%} |",
            f"| False admits | {retrieval['false_admit_rate']:.0%} |",
            f"| Lowest relevant score | {retrieval['min_relevant_score']:.4f} |",
            f"| Highest irrelevant score | {retrieval['max_irrelevant_score']:.4f} |",
            f"| **Floor margin** | **{retrieval['floor_margin']:+.4f}** |",
        ]

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="fake", help="fake | anthropic | ollama")
    parser.add_argument("--model", default=None)
    parser.add_argument("--mode", default="retrieval", choices=["retrieval", "stuffed"])
    parser.add_argument("--cases", nargs="*", help="run only these case ids")
    parser.add_argument("--retrieval-only", action="store_true", help="retrieval report, no LLM calls")
    parser.add_argument("--no-retrieval-report", action="store_true")
    args = parser.parse_args(argv)

    if args.retrieval_only:
        print(json.dumps(retrieval_report(), indent=2))
        return 0

    cases = load_cases(CASES_PATH, args.cases)
    print(f"{len(cases)} cases · backend {args.backend} · mode {args.mode}\n")

    rows, summary = run_arm(cases, args.backend, args.mode, args.model)
    retrieval = None if args.no_retrieval_report or args.mode == "stuffed" else retrieval_report()

    meta = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "backend": args.backend,
        "mode": args.mode,
        "model": args.model or "(backend default)",
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    results_path = RESULTS_DIR / f"{stamp}-{args.backend}-{args.mode}.jsonl"
    with results_path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"meta": meta, "summary": summary, "retrieval": retrieval}) + "\n")
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    report = format_report(meta, summary, rows, retrieval)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"rows: {results_path.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
