"""The four evidence tools, plus the terminal `submit_verdict` tool.

Design rules that apply to all of them:

* **A tool never raises into the agent loop.** Every failure comes back as a normal
  tool result with `status: "error"` and a hint. A crash ends a run; a hint lets the
  agent recover, and the trace shows what it did with the information.

* **A miss is a result, not a failure.** `search_son` returning no sections and
  `get_config_param` returning `not_found` are the primary evidence for a CR verdict:
  the client is asking for something the specification never promised. Both therefore
  return a well-formed, quotable answer rather than an empty response.

* **Findings that decide the verdict are computed, not judged.** `reads_params` is a
  deterministic scan of a known parameter list against a known line range. The model
  is asked what a missing parameter *means*, never asked to spot it.

* **Output is JSON, and that JSON is the grounding corpus.** `serialise` is the one
  way a result becomes text. Evidence quotes are checked against exactly these
  strings, so anything the model is allowed to cite must appear here verbatim.
"""

from __future__ import annotations

import difflib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from src import rag

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "db.sqlite"
DEFAULT_CLIENT = "NWB"

MAX_RECORDS = 50
DEFAULT_RECORDS = 20
NEAR_MATCH_LIMIT = 3

AUTH_STATUSES = ["APPROVED", "SETTLED", "DROPPED", "EXPIRED", "REVERSED"]
AUTH_CHANNELS = ["ATM", "POS", "ECOM"]


# --------------------------------------------------------------------- schemas

SEARCH_SON_SCHEMA = {
    "name": "search_son",
    "description": (
        "Search the client's signed-off Statement of Need documents for sections relevant "
        "to a question. Use this first on every ticket: the verdict turns on what was and "
        "was not specified.\n\n"
        "Returns an empty list when no section is relevant. That is a real and useful "
        "answer, not a failure - if the specification is silent on what the client is "
        "asking for, the request is likely a change request rather than a defect."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "What you want the specification to tell you, in natural language. "
                    "Describe the behaviour, not the ticket: 'are dropped authorisations "
                    "excluded from ATM statements' rather than 'customer is complaining'."
                ),
            },
            "client_id": {
                "type": "string",
                "description": "Client whose documents to search. Defaults to NWB.",
            },
            "top_k": {
                "type": "integer",
                "description": "How many sections to return, 1 to 8. Defaults to 4.",
                "minimum": 1,
                "maximum": 8,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

GET_CONFIG_PARAM_SCHEMA = {
    "name": "get_config_param",
    "description": (
        "Look up a configuration parameter for a client: its current value, type, default, "
        "and which layer is expected to read it.\n\n"
        "Accepts an exact name or a fragment. When the name does not exist at all, the "
        "result lists the closest names that do - and the absence itself is evidence: a "
        "parameter that does not exist cannot have been mis-set, so the client is asking "
        "for behaviour that was never built."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Parameter name or a fragment of one, e.g. 'STMT_EXCLUDE_DROPPED_AUTHS' "
                    "or 'EXCLUDE_DROPPED'. Case insensitive."
                ),
            },
            "client_id": {
                "type": "string",
                "description": "Client whose configuration to read. Defaults to NWB.",
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    },
}

COMPARE_CORE_VS_CUSTOM_SCHEMA = {
    "name": "compare_core_vs_custom",
    "description": (
        "Compare the platform's own implementation of a function with the client-custom "
        "override that replaces it, and report which configuration parameters each one "
        "actually reads.\n\n"
        "`params_only_in_core` is the field that matters most: a parameter the platform "
        "honours but the client override ignores means the setting has no effect for that "
        "client, however it is configured. Note that a custom layer differing from core is "
        "normal - overrides exist to differ. What makes a difference a defect is the "
        "specification, so check the Statement of Need before concluding anything."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "function_name": {
                "type": "string",
                "description": (
                    "Function to compare, e.g. 'build_atm_statement_lines'. Call with an "
                    "unknown name to receive the list of functions available."
                ),
            },
            "client_id": {
                "type": "string",
                "description": "Client whose override to compare against core. Defaults to NWB.",
            },
        },
        "required": ["function_name"],
        "additionalProperties": False,
    },
}

GET_AUTH_RECORDS_SCHEMA = {
    "name": "get_auth_records",
    "description": (
        "Query authorisation records to confirm what actually happened on the account. "
        "Use it to establish that the behaviour the client reports is real and to quantify "
        "it, rather than reasoning only from documents and code.\n\n"
        "Counts and per-status totals are always computed over the whole matching set, even "
        "when the returned rows are truncated, so the aggregates can be cited safely."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "client_id": {"type": "string", "description": "Defaults to NWB."},
            "card_last4": {"type": "string", "description": "Last four digits of the card."},
            "channel": {
                "type": "string",
                "description": "Transaction channel.",
                "enum": [*AUTH_CHANNELS, "any"],
            },
            "status": {
                "type": "string",
                "description": (
                    "Authorisation lifecycle state. DROPPED means no clearing advice arrived "
                    "and the hold was released; EXPIRED means it aged out unpresented."
                ),
                "enum": [*AUTH_STATUSES, "any"],
            },
            "date_from": {"type": "string", "description": "Inclusive, YYYY-MM-DD."},
            "date_to": {"type": "string", "description": "Inclusive, YYYY-MM-DD."},
            "limit": {
                "type": "integer",
                "description": f"Rows to return, 1 to {MAX_RECORDS}. Defaults to {DEFAULT_RECORDS}.",
                "minimum": 1,
                "maximum": MAX_RECORDS,
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}

SUBMIT_VERDICT_SCHEMA = {
    "name": "submit_verdict",
    "description": (
        "Submit the triage decision. This is the only way to finish: the loop does not read "
        "prose conclusions.\n\n"
        "BUG - the specification requires a behaviour and the implementation does not deliver "
        "it.\n"
        "CR - the client is asking for behaviour the specification does not require, or "
        "explicitly places out of scope. New work, not a defect.\n"
        "NEEDS_INFO - the specification is genuinely silent or ambiguous and no amount of "
        "further tool calls will settle it. Say what you need answered and by whom.\n\n"
        "A BUG or CR is rejected unless it cites at least two pieces of evidence, at least "
        "one from a Statement of Need and at least one from config, code or data: the "
        "specification says what should happen, the other sources say what does. Every quote "
        "must be copied verbatim from a tool result - invented quotes are detected and the "
        "submission is sent back. Do not include a confidence score; it is computed from the "
        "evidence you cite."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["BUG", "CR", "NEEDS_INFO"]},
            "rationale": {
                "type": "string",
                "description": "Up to five sentences for the internal reviewer, not the client.",
            },
            "evidence": {
                "type": "array",
                "description": "The citations the verdict rests on.",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["son", "config", "code", "data"],
                        },
                        "ref": {
                            "type": "string",
                            "description": (
                                "Stable pointer: 'SON-001#3.2', a parameter name, "
                                "'data/code/custom/x.py:L15-47', or the record filter used."
                            ),
                        },
                        "quote": {
                            "type": "string",
                            "description": "Verbatim text from the tool result. Checked.",
                        },
                        "why_it_matters": {
                            "type": "string",
                            "description": "One sentence tying this citation to the verdict.",
                        },
                    },
                    "required": ["kind", "ref", "quote", "why_it_matters"],
                    "additionalProperties": False,
                },
            },
            "missing_info": {
                "type": "array",
                "description": "Specific questions to put to the client. Required for NEEDS_INFO.",
                "items": {"type": "string"},
            },
            "draft_reply": {
                "type": "string",
                "description": (
                    "Reply the client will read. Plain language, no file paths, no internal "
                    "system names. State the finding and the next step."
                ),
            },
        },
        "required": ["verdict", "rationale", "evidence", "draft_reply"],
        "additionalProperties": False,
    },
}

EVIDENCE_TOOL_SCHEMAS = [
    SEARCH_SON_SCHEMA,
    GET_CONFIG_PARAM_SCHEMA,
    COMPARE_CORE_VS_CUSTOM_SCHEMA,
    GET_AUTH_RECORDS_SCHEMA,
]
ALL_TOOL_SCHEMAS = [*EVIDENCE_TOOL_SCHEMAS, SUBMIT_VERDICT_SCHEMA]


# ----------------------------------------------------------------- the toolbox


def serialise(result: dict) -> str:
    """The single rendering of a tool result into text.

    Evidence quotes are grounded against this exact string, so every tool must go
    through it. `ensure_ascii=False` keeps the SON's own punctuation intact instead
    of turning it into escapes the model would have to reproduce.
    """
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


class Toolbox:
    """Executes the evidence tools against the database and the retriever."""

    def __init__(self, db_path: Path | str = DEFAULT_DB, *, retriever: rag.Retriever | None = None):
        self.db_path = Path(db_path)
        self._retriever = retriever

    # -- plumbing ----------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @property
    def retriever(self) -> rag.Retriever:
        if self._retriever is None:
            self._retriever = rag.Retriever(self.db_path)
        return self._retriever

    def _known_params(self, conn: sqlite3.Connection, client_id: str) -> list[str]:
        rows = conn.execute(
            "SELECT name FROM config_params WHERE client_id = ? ORDER BY name", (client_id,)
        ).fetchall()
        return [row["name"] for row in rows]

    def execute(self, name: str, arguments: dict[str, Any]) -> dict:
        """Dispatch by tool name. Never raises; unknown names and bad input become results."""
        handlers = {
            "search_son": self.search_son,
            "get_config_param": self.get_config_param,
            "compare_core_vs_custom": self.compare_core_vs_custom,
            "get_auth_records": self.get_auth_records,
        }
        handler = handlers.get(name)
        if handler is None:
            return {
                "status": "error",
                "error": f"unknown tool {name!r}",
                "hint": f"available tools: {', '.join(sorted(handlers))}",
            }
        try:
            return handler(**arguments)
        except TypeError as exc:
            return {
                "status": "error",
                "error": f"bad arguments for {name}: {exc}",
                "hint": "check the tool's input schema and try again",
            }
        except Exception as exc:  # a tool fault must not end the run
            return {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "hint": "this tool is unavailable; continue with the other tools",
            }

    # -- search_son --------------------------------------------------------

    def search_son(
        self, query: str, client_id: str = DEFAULT_CLIENT, top_k: int = rag.DEFAULT_TOP_K
    ) -> dict:
        hits = self.retriever.retrieve(query, client_id=client_id, top_k=top_k)
        if not hits:
            return {
                "status": "no_match",
                "query": query,
                "client_id": client_id,
                "chunks": [],
                "note": (
                    f"No section of {client_id}'s Statements of Need scored above the relevance "
                    f"floor of {self.retriever.min_score} for this query. Either the wording needs "
                    "to be closer to how a specification would phrase it, or the documents do not "
                    "cover this subject at all. If a differently worded search also returns "
                    "nothing, treat the silence as evidence."
                ),
            }
        return {
            "status": "ok",
            "query": query,
            "client_id": client_id,
            "chunks": [
                {
                    "chunk_id": hit.chunk_id,
                    "son_id": hit.son_id,
                    "title": hit.title,
                    "version": hit.version,
                    "section": hit.section,
                    "heading": hit.heading,
                    "text": hit.text,
                    "score": hit.score,
                }
                for hit in hits
            ],
        }

    # -- get_config_param --------------------------------------------------

    def get_config_param(self, name: str, client_id: str = DEFAULT_CLIENT) -> dict:
        """Look up a parameter by exact name, then by fragment, then by near miss.

        Matching deliberately avoids SQL `LIKE`. In LIKE, `_` is a single-character
        wildcard, so a fragment like `MAX_LINES` silently also matches `MAXXLINES`,
        and a pattern like `%_AGE%` matches `..._PER_PAGE`. Parameter names here are
        full of underscores, so LIKE is precisely the wrong operator: `instr` does a
        literal substring test with no wildcard semantics at all.
        """
        wanted = name.strip().upper()
        with self._connect() as conn:
            exact = conn.execute(
                "SELECT * FROM config_params WHERE client_id = ? AND UPPER(name) = ?",
                (client_id, wanted),
            ).fetchone()
            if exact:
                return {"status": "ok", "matched_on": "exact", "param": _param_row(exact)}

            partial = conn.execute(
                "SELECT * FROM config_params WHERE client_id = ? AND instr(UPPER(name), ?) > 0 "
                "ORDER BY name",
                (client_id, wanted),
            ).fetchall()
            if len(partial) == 1:
                return {
                    "status": "ok",
                    "matched_on": "partial",
                    "note": f"{name!r} matched exactly one parameter by substring.",
                    "param": _param_row(partial[0]),
                }
            if len(partial) > 1:
                return {
                    "status": "ambiguous",
                    "query": name,
                    "client_id": client_id,
                    "candidates": [_param_row(row) for row in partial],
                    "note": f"{len(partial)} parameters contain {wanted!r}. Ask for one by exact name.",
                }

            known = self._known_params(conn, client_id)

        return {
            "status": "not_found",
            "query": name,
            "client_id": client_id,
            "near_matches": difflib.get_close_matches(wanted, known, n=NEAR_MATCH_LIMIT, cutoff=0.4),
            "note": (
                f"No configuration parameter named {name!r} exists for {client_id}. A parameter "
                "that does not exist cannot be switched on or mis-set, so if the client is asking "
                "for it to be enabled, the behaviour was never built. Confirm against the "
                "Statement of Need before concluding."
            ),
        }

    # -- compare_core_vs_custom -------------------------------------------

    def compare_core_vs_custom(
        self, function_name: str, client_id: str = DEFAULT_CLIENT
    ) -> dict:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM code_snippets WHERE function_name = ? "
                "AND (client_id IS NULL OR client_id = ?)",
                (function_name, client_id),
            ).fetchall()
            if not rows:
                available = conn.execute(
                    "SELECT DISTINCT function_name FROM code_snippets ORDER BY function_name"
                ).fetchall()
                return {
                    "status": "not_found",
                    "query": function_name,
                    "available_functions": [row["function_name"] for row in available],
                    "note": f"No registered code for {function_name!r}.",
                }
            known = self._known_params(conn, client_id)

        layers = {row["layer"]: _read_snippet(row, known) for row in rows}
        core, custom = layers.get("core"), layers.get("custom")

        if core is None:
            return {
                "status": "error",
                "error": f"{function_name!r} has a custom override but no core implementation",
                "hint": "the registry is inconsistent; rely on other evidence",
            }

        if custom is None:
            return {
                "status": "ok",
                "function": function_name,
                "client_id": client_id,
                "custom_overrides_core": False,
                "core": core,
                "custom": None,
                "params_only_in_core": [],
                "params_only_in_custom": [],
                "note": (
                    f"{client_id} has no override for {function_name!r}; the platform "
                    "implementation runs unmodified."
                ),
            }

        only_core = sorted(set(core["reads_params"]) - set(custom["reads_params"]))
        only_custom = sorted(set(custom["reads_params"]) - set(core["reads_params"]))

        note = (
            f"{client_id} overrides {function_name!r}. Parameter reads were found by scanning "
            "each function's own line range for the names of parameters configured for this "
            "client, so this is a literal scan of the code rather than an interpretation of it."
        )
        if only_core:
            note += (
                f" The override does not read {', '.join(only_core)}, which the platform "
                "implementation does read. For this client those settings have no effect on "
                "this function whatever they are set to."
            )

        return {
            "status": "ok",
            "function": function_name,
            "client_id": client_id,
            "custom_overrides_core": True,
            "core": core,
            "custom": custom,
            "params_only_in_core": only_core,
            "params_only_in_custom": only_custom,
            "note": note,
        }

    # -- get_auth_records --------------------------------------------------

    def get_auth_records(
        self,
        client_id: str = DEFAULT_CLIENT,
        card_last4: str | None = None,
        channel: str = "any",
        status: str = "any",
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = DEFAULT_RECORDS,
    ) -> dict:
        limit = max(1, min(int(limit), MAX_RECORDS))

        clauses = ["client_id = ?"]
        params: list[Any] = [client_id]
        if card_last4:
            clauses.append("card_last4 = ?")
            params.append(card_last4)
        if channel and channel != "any":
            clauses.append("channel = ?")
            params.append(channel.upper())
        if status and status != "any":
            clauses.append("status = ?")
            params.append(status.upper())
        if date_from:
            clauses.append("auth_ts >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("auth_ts <= ?")
            params.append(f"{date_to}T23:59:59Z")

        where = " AND ".join(clauses)
        with self._connect() as conn:
            matching = conn.execute(
                f"SELECT * FROM auth_records WHERE {where} ORDER BY auth_ts", params
            ).fetchall()

        by_status: dict[str, int] = {}
        by_channel: dict[str, int] = {}
        for row in matching:
            by_status[row["status"]] = by_status.get(row["status"], 0) + 1
            by_channel[row["channel"]] = by_channel.get(row["channel"], 0) + 1

        page = matching[:limit]
        result = {
            "status": "ok",
            "filters": {
                "client_id": client_id,
                "card_last4": card_last4,
                "channel": channel,
                "status": status,
                "date_from": date_from,
                "date_to": date_to,
            },
            "count": len(matching),
            "returned": len(page),
            "truncated": len(page) < len(matching),
            "aggregates": {
                "by_status": dict(sorted(by_status.items())),
                "by_channel": dict(sorted(by_channel.items())),
            },
            "records": [dict(row) for row in page],
        }
        if not matching:
            result["note"] = (
                "No authorisation records match these filters. Widen the date range or drop a "
                "filter before concluding that the reported activity did not happen."
            )
        elif result["truncated"]:
            result["note"] = (
                f"Showing {len(page)} of {len(matching)} matching records. The counts in "
                "`aggregates` cover all of them, so quote those rather than counting rows."
            )
        return result


# -------------------------------------------------------------------- helpers


def _param_row(row: sqlite3.Row) -> dict:
    return {
        "name": row["name"],
        "value": row["value"],
        "data_type": row["data_type"],
        "default_value": row["default_value"],
        "scope": row["scope"],
        "owner_layer": row["owner_layer"],
        "description": row["description"],
        "effective_from": row["effective_from"],
        "updated_at": row["updated_at"],
    }


def params_read_in(source: str, known_params: list[str]) -> list[str]:
    """Which of the known parameter names appear in this source text.

    Whole-word matching against a closed list, not a general identifier parse: the
    question is only ever "does this function read a parameter we know exists", and a
    closed list keeps the scan deterministic and free of false positives from local
    variable names.
    """
    return sorted(
        name for name in known_params if re.search(rf"\b{re.escape(name)}\b", source)
    )


def _read_snippet(row: sqlite3.Row, known_params: list[str]) -> dict:
    lines = (PROJECT_ROOT / row["path"]).read_text(encoding="utf-8").splitlines()
    window = lines[row["start_line"] - 1 : row["end_line"]]
    source = "\n".join(window)
    return {
        "layer": row["layer"],
        "path": row["path"],
        "lines": f"L{row['start_line']}-{row['end_line']}",
        "description": row["description"],
        "reads_params": params_read_in(source, known_params),
        "snippet": source,
    }
