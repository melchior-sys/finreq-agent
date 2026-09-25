"""Calibrate the retrieval score floor, and check what the query prefix is worth.

Why this exists: BGE embeddings sit in a narrow, high cosine band. Two entirely
unrelated sentences score about 0.60 against each other, so an intuitive floor like
0.30 admits every chunk for every query and the retriever loses the ability to say
"the spec does not cover this" — which is precisely what the CR and NEEDS_INFO
verdicts are built on.

The floor is therefore measured, on 12 queries that should hit a known section and
12 that should hit nothing, rather than picked. Run:

    python -m evals.calibrate_retrieval            # prints the report
    python -m evals.calibrate_retrieval --write    # also rewrites evals/retrieval_floor.md
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.rag import DEFAULT_TOP_K, MAX_TOP_K, MIN_SCORE, Retriever, embed_query  # noqa: E402

NOTE_PATH = Path(__file__).resolve().parent / "retrieval_floor.md"

# Phrased the way a client writes a ticket, not the way the spec is written: US
# spellings, everyday vocabulary, and the words a branch employee would use.
RELEVANT: list[tuple[str, str]] = [
    ("dropped authorizations showing on ATM statement", "SON-001#3.2"),
    ("cancelled cash withdrawals appear as duplicate debits", "SON-001#3.2"),
    ("what does it mean when an authorisation is dropped", "SON-001#2"),
    ("pending transactions should show in a separate memo area", "SON-001#3.3"),
    ("how many transaction rows print on each statement page", "SON-001#3.5"),
    ("monthly fee waiver when salary is paid in", "SON-002#3.2"),
    ("senior citizen customers should not be charged the monthly fee", "SON-002#5"),
    ("maximum number of fee waivers allowed in one cycle", "SON-002#3.4"),
    ("charge for using another bank cash machine", "SON-002#3.3"),
    ("when does the statement period close each month", "SON-003#3.1"),
    ("statement close falls on a bank holiday", "SON-003#3.3"),
    ("which timezone is the daily cut off applied in", "SON-003#3.4"),
]

# Nothing in the corpus answers these. The last three are deliberately near-domain:
# real banking language that the SONs still do not cover. An easy negative set would
# flatter the floor.
IRRELEVANT: list[str] = [
    "how do I reset my online banking password",
    "what is the weather forecast for tomorrow",
    "recipe for banana bread",
    "train times from London to Manchester",
    "kubernetes pod keeps restarting after deploy",
    "apply for a mortgage on a second property",
    "what is the interest rate on savings accounts",
    "update my registered home address",
    "open a new business current account",
    "my debit card was stolen, how do I report it",
    "order a replacement PIN for my card",
    "raise a chargeback dispute against a merchant",
]


def score_everything(retriever: Retriever, *, use_prefix: bool) -> dict:
    rows, matrix = retriever._load()  # noqa: SLF001 — calibration needs the raw scores
    index_of = {row["chunk_id"]: i for i, row in enumerate(rows)}

    relevant_rows = []
    for query, expected in RELEVANT:
        scores = matrix @ embed_query(query, use_prefix=use_prefix)
        order = np.argsort(-scores)
        rank = int(np.where(order == index_of[expected])[0][0]) + 1
        relevant_rows.append(
            {
                "query": query,
                "expected": expected,
                "score": float(scores[index_of[expected]]),
                "rank": rank,
                "top1": rows[order[0]]["chunk_id"],
                "top1_score": float(scores[order[0]]),
                "ranked": [(rows[i]["chunk_id"], float(scores[i])) for i in order[:MAX_TOP_K]],
            }
        )

    irrelevant_rows = []
    for query in IRRELEVANT:
        scores = matrix @ embed_query(query, use_prefix=use_prefix)
        best = int(np.argmax(scores))
        irrelevant_rows.append(
            {"query": query, "best": rows[best]["chunk_id"], "score": float(scores[best])}
        )

    return {"relevant": relevant_rows, "irrelevant": irrelevant_rows}


def returned_at(row: dict, floor: float) -> list[str]:
    """The chunk ids a query would actually get back at this floor."""
    return [cid for cid, score in row["ranked"] if score >= floor][:DEFAULT_TOP_K]


def sweep(results: dict) -> list[dict]:
    """Two recall measures and the false-admit rate, across candidate floors.

    Strict recall asks whether the one chunk labelled as the answer came back.
    Document recall asks the weaker, and for this agent more operative, question:
    did anything from the right SON come back at all? A rule section and the
    definitions section above it often both answer a client's question, and the
    agent reads whatever it is handed — so a query that misses 3.3 but returns
    section 2 of the same document has not left the agent empty-handed.
    """
    table = []
    for floor in [round(x, 2) for x in np.arange(0.55, 0.92, 0.01)]:
        strict = 0
        by_document = 0
        for row in results["relevant"]:
            returned = returned_at(row, floor)
            if row["expected"] in returned:
                strict += 1
            expected_doc = row["expected"].split("#")[0]
            if any(cid.startswith(expected_doc) for cid in returned):
                by_document += 1
        admitted = sum(1 for r in results["irrelevant"] if r["score"] >= floor)
        table.append(
            {
                "floor": floor,
                "recall": strict / len(results["relevant"]),
                "doc_recall": by_document / len(results["relevant"]),
                "false_admit": admitted / len(results["irrelevant"]),
            }
        )
    return table


def choose_floor(table: list[dict]) -> dict:
    """No false admits first, then strict recall, then document recall, then lower floor.

    False admits are ranked first deliberately. A chunk that clears the floor is
    presented to the agent as a retrieved requirement, and the evidence gate will
    happily accept a verbatim quote from it — so an off-topic chunk above the floor
    is a route to a confidently wrong, fully "grounded" verdict. A miss is the safer
    failure: the agent sees less, and NEEDS_INFO is a survivable answer.
    """
    clean = [row for row in table if row["false_admit"] == 0.0]
    pool = clean or table
    return max(pool, key=lambda row: (row["recall"], row["doc_recall"], -row["floor"]))


def render(results: dict, table: list[dict], chosen: dict, prefix_delta: dict) -> str:
    rel = results["relevant"]
    irr = results["irrelevant"]
    min_rel = min(r["score"] for r in rel)
    max_irr = max(r["score"] for r in irr)

    lines = [
        "# Retrieval score floor",
        "",
        f"_Measured {date.today().isoformat()} — {len(rel)} relevant and {len(irr)} irrelevant "
        "queries against the 28-chunk SON corpus. Regenerate with "
        "`python -m evals.calibrate_retrieval --write`._",
        "",
        "## Why a floor is needed at all",
        "",
        "BGE cosine scores occupy a narrow high band: unrelated text still scores around "
        "0.60. A floor chosen by intuition (0.3, say) admits everything, and a retriever "
        "that always returns something cannot support a verdict that rests on the spec "
        "being silent. The number below is the separation point actually observed.",
        "",
        "## Result",
        "",
        f"- Lowest score among relevant queries: **{min_rel:.4f}**",
        f"- Highest score among irrelevant queries: **{max_irr:.4f}**",
        f"- Separation: **{min_rel - max_irr:+.4f}** "
        + ("(the two bands overlap)" if min_rel < max_irr else "(cleanly separated)"),
        f"- **Chosen floor: {chosen['floor']:.2f}** — strict recall {chosen['recall']:.0%}, "
        f"document recall {chosen['doc_recall']:.0%}, false admits {chosen['false_admit']:.0%}",
        f"- **Floor margin: {chosen['floor'] - max_irr:+.4f}** — how far the floor sits above the "
        "best-scoring irrelevant query. Thin margins mean one new off-topic query could start "
        "clearing the floor, so this is tracked rather than assumed.",
        "",
        "The bands overlap, so no floor separates relevant from irrelevant perfectly and "
        "the choice is a trade, not an optimum. It is made in favour of zero false "
        "admits: a chunk above the floor reaches the agent as a retrieved requirement, "
        "and the evidence gate will accept a verbatim quote from it, so an off-topic "
        "chunk is a path to a confidently wrong verdict that passes every check. A miss "
        "degrades to NEEDS_INFO, which is recoverable.",
        "",
        "## Relevant queries",
        "",
        "| Query | Expected | Score | Rank | Top hit |",
        "|---|---|---:|---:|---|",
    ]
    for r in sorted(rel, key=lambda r: r["score"]):
        lines.append(
            f"| {r['query']} | `{r['expected']}` | {r['score']:.4f} | {r['rank']} | "
            f"`{r['top1']}` ({r['top1_score']:.4f}) |"
        )

    lines += [
        "",
        "## Irrelevant queries (best score anywhere in the corpus)",
        "",
        "| Query | Closest chunk | Score |",
        "|---|---|---:|",
    ]
    for r in sorted(irr, key=lambda r: -r["score"]):
        lines.append(f"| {r['query']} | `{r['best']}` | {r['score']:.4f} |")

    lines += [
        "",
        "## Floor sweep",
        "",
        "Strict recall @4 = the labelled chunk came back. Document recall @4 = something "
        "from the right SON came back.",
        "",
        "| Floor | Strict recall @4 | Document recall @4 | False admits |",
        "|---:|---:|---:|---:|",
    ]
    for row in table:
        marker = "  **<- chosen**" if row["floor"] == chosen["floor"] else ""
        lines.append(
            f"| {row['floor']:.2f} | {row['recall']:.0%} | {row['doc_recall']:.0%} | "
            f"{row['false_admit']:.0%}{marker} |"
        )

    misses = [
        row
        for row in results["relevant"]
        if row["expected"] not in returned_at(row, chosen["floor"])
    ]
    lines += ["", "## What the strict misses actually return", ""]
    if not misses:
        lines.append("None at the chosen floor.")
    for row in misses:
        returned = returned_at(row, chosen["floor"])
        lines.append(
            f"- `{row['query']}` wanted `{row['expected']}` (rank {row['rank']}, "
            f"{row['score']:.4f}) and got {', '.join(f'`{c}`' for c in returned) or 'nothing'}."
        )
    if misses:
        lines.append("")
        lines.append(
            "Both misses are questions the definitions section of the same document also "
            "answers, which is why document recall is the higher number. The labels are "
            "left strict rather than widened to sets: a label that moves to match the "
            "result measures nothing."
        )

    lines += [
        "",
        "## Is the BGE query prefix worth applying?",
        "",
        "`fastembed.query_embed` does not apply it for this model — it returns a vector "
        "identical to `embed` — so `src.rag.embed_query` prepends it by hand. Measured "
        "both ways:",
        "",
        "| | Mean relevant score | Max irrelevant score | Separation |",
        "|---|---:|---:|---:|",
        f"| With prefix | {prefix_delta['with']['mean_rel']:.4f} | "
        f"{prefix_delta['with']['max_irr']:.4f} | {prefix_delta['with']['separation']:+.4f} |",
        f"| Without prefix | {prefix_delta['without']['mean_rel']:.4f} | "
        f"{prefix_delta['without']['max_irr']:.4f} | {prefix_delta['without']['separation']:+.4f} |",
        "",
        "## Planned for slice 5",
        "",
        "- **Held-out queries.** Add roughly ten relevant and ten irrelevant queries that "
        "were not used to pick this floor, and report their numbers separately. The floor "
        "above is fitted to the set on this page, so those figures are training accuracy: "
        "they say how well the threshold describes the queries it was chosen from, not how "
        "it behaves on a query it has never seen.",
        "- **Floor margin as a tracked metric.** Report "
        f"`floor - max irrelevant score` on every eval run (currently "
        f"{chosen['floor'] - max_irr:+.4f}). It is the early warning: it shrinks silently as "
        "documents are added, and it reaches zero before any recall number moves.",
        "",
    ]
    return "\n".join(lines) + "\n"


def summarise(results: dict) -> dict:
    rel = [r["score"] for r in results["relevant"]]
    irr = [r["score"] for r in results["irrelevant"]]
    return {
        "mean_rel": float(np.mean(rel)),
        "max_irr": max(irr),
        "separation": min(rel) - max(irr),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="rewrite evals/retrieval_floor.md")
    args = parser.parse_args(argv)

    retriever = Retriever()
    with_prefix = score_everything(retriever, use_prefix=True)
    without_prefix = score_everything(retriever, use_prefix=False)

    table = sweep(with_prefix)
    chosen = choose_floor(table)
    prefix_delta = {"with": summarise(with_prefix), "without": summarise(without_prefix)}

    report = render(with_prefix, table, chosen, prefix_delta)
    if args.write:
        NOTE_PATH.write_text(report, encoding="utf-8")
        print(f"wrote {NOTE_PATH}")

    print(
        f"chosen floor {chosen['floor']:.2f} | recall {chosen['recall']:.0%} | "
        f"false admits {chosen['false_admit']:.0%} | src.rag.MIN_SCORE is {MIN_SCORE}"
    )
    if abs(chosen["floor"] - MIN_SCORE) > 1e-9:
        print("MIN_SCORE in src/rag.py does not match the calibrated floor.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
