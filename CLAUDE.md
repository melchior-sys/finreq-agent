# FinReq Agent — Project Guide for Claude Code

## What this is
An agentic AI system that triages finance/card-processing client tickets.
Given a client ticket, it decides: **Bug fix** vs **Change Request (CR)**, with evidence.

It does this by:
1. Retrieving the client's Statement of Need (SON) / spec docs (RAG)
2. Calling tools to inspect config parameters and compare core vs client-custom behaviour
3. Producing a verdict + cited evidence + a draft client reply

Portfolio project for an **Agentic AI Product Owner (Finance)** role. The owner (Samarth)
must be able to explain every design decision in an interview.

## Hard rules
- **Synthetic data only.** Never use real employer, client, or card data. Invent a fictional
  bank ("Northwind Bank"), fictional SONs, fictional config params, fictional code snippets.
- **Explain before you build.** For any new component, first state: what it does, why this
  design, one alternative considered and why rejected. Then code.
- **Small slices.** One component per session. Stop after each slice and summarise.
- **Commit after each working slice** with a clear message.
- Prefer simple, readable code over clever code. No framework magic that hides the agent loop.

## Stack
- Python 3.11+, `uv` or `pip` for deps
- LLM: Anthropic API via the official `anthropic` SDK (key in `.env`, never committed)
- Agent loop: **hand-written** tool-calling loop (no LangChain) — so the orchestration is visible
- RAG: simple chunking + embeddings stored in SQLite or a local vector lib (e.g. `chromadb`)
- Data: SQLite for synthetic config params / auth records
- API: FastAPI (one endpoint: `POST /triage`)
- Tests + evals: `pytest`; eval set as JSONL

## Architecture (target)
```
ticket ──► Orchestrator (agent loop)
              ├─ tool: search_son(query)          → RAG over SON docs
              ├─ tool: get_config_param(name)     → SQLite
              ├─ tool: compare_core_vs_custom(fn) → reads core/ and custom/ code samples
              └─ tool: get_auth_records(filter)   → SQLite
           ──► Verdict {BUG | CR | NEEDS_INFO}, evidence[], draft_reply
           ──► Trace log (every LLM call + tool call, JSON lines)
```

## Folder layout
```
data/sons/          fictional SON markdown docs
data/code/core/     fictional core-platform code snippets
data/code/custom/   fictional client-custom code snippets
data/db.sqlite      config params + auth records
src/rag.py          chunk, embed, retrieve
src/tools.py        tool definitions + implementations
src/agent.py        the tool-calling loop
src/api.py          FastAPI app
evals/cases.jsonl   ticket → expected verdict
evals/run_evals.py  scores accuracy + evidence citation rate
traces/             JSONL traces (gitignored)
```

## Definition of done (for the portfolio)
- `POST /triage` works end to end on 3+ demo tickets
- Eval set of 15+ cases with a reported accuracy number
- Every run writes a trace (observability)
- README: problem, architecture diagram, design trade-offs, eval results, limitations

## Slices (build in order)
1. Synthetic data (SONs, code snippets, SQLite seed)
2. RAG over SONs
3. Tools
4. Agent loop with traces
5. Evals
6. FastAPI + README
