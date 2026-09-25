# Session prompts — paste one per Claude Code session

Setup (once):
    mkdir finreq-agent && cd finreq-agent && git init
    (put CLAUDE.md in this folder)
    claude

Press Shift+Tab until you see plan mode before sending Session 1.

---

## Session 1 — Plan + synthetic data
Read CLAUDE.md. Don't write code yet.
1. Propose the full architecture in more detail: data models, the 4 tools' input/output
   schemas, and how the agent loop decides when to stop.
2. For each choice, give one alternative and why you rejected it.
3. List the synthetic data you'll create for slice 1: 3 fictional SON docs for "Northwind Bank"
   (one about excluding dropped/expired authorizations from ATM statements, one about fee
   waivers, one about statement cycle dates), ~6 core vs custom code snippets, and the
   SQLite schema with seed rows. Include one config param that exists in the DB but is
   NOT used in custom code — that is the key scenario.
After I approve, build slice 1 only, then summarise and commit.

## Session 2 — RAG
Build slice 2 (src/rag.py). Explain chunking size, embedding choice and top-k before coding.
Add a small test that a query about "dropped authorizations" retrieves the right SON.

## Session 3 — Tools
Build slice 3 (src/tools.py). Show each tool's JSON schema first. Add unit tests per tool.

## Session 4 — Agent loop + traces
Build slice 4 (src/agent.py): a hand-written tool-calling loop with a max-steps guard.
Every LLM call and tool call is appended to traces/<run_id>.jsonl.
Output must be structured: verdict, evidence (with source file + line/section), draft_reply.

## Session 5 — Evals
Build slice 5: 15 cases in evals/cases.jsonl (mix of BUG, CR, NEEDS_INFO) and
run_evals.py reporting accuracy and % of verdicts with at least one cited SON section.
Show me the failures and propose one prompt or tool fix.

## Session 6 — API + README
Build slice 6: FastAPI POST /triage, and a README with a mermaid architecture diagram,
design trade-offs, eval results table and known limitations.

---

## After every session, ask:
"Explain what we built today as if I'm being interviewed on it. Then quiz me with
3 questions and don't give answers until I reply."
