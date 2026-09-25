"""LLM backends behind one small interface.

Three backends, selected by the `FINREQ_LLM` environment variable:

* `fake`     - scripted responses. The default under pytest, so the unit suite
               never touches a network or an API key.
* `anthropic`- the official SDK, reading ANTHROPIC_API_KEY from .env.
* `ollama`   - a local OpenAI-compatible server on localhost:11434, if one is
               running. Optional; the agent never depends on it.

The interface is deliberately thin: one `complete()` call returning a normalised
`LLMResponse`. The agent loop in src/agent.py owns the orchestration, which is the
point of the project - a backend that hid the loop would defeat it.

**The API key is never printed, logged or traced.** It is read from the environment
into the SDK client and nowhere else. `redact()` below is the belt-and-braces pass
applied to everything on its way into a trace, and a test asserts a run's trace file
does not contain the key.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
OLLAMA_BASE_URL = "http://localhost:11434/v1"

# Triage turns emit either a tool call or a short structured verdict, never long
# prose, but max_tokens costs nothing unless it is used - only generated tokens are
# billed - so it is set well clear of the ceiling rather than tuned down.
MAX_TOKENS = 16000

# USD per million tokens. Cache reads bill at ~0.1x input, writes at ~1.25x.
# Used only to report what a run cost; a model missing here reports cost as None
# rather than guessing.
PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-sonnet-5": {"input": 3.00, "output": 15.00},
    "claude-opus-5": {"input": 5.00, "output": 25.00},
}
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25


# ------------------------------------------------------------------ env loading


def load_env(path: Path | None = None) -> None:
    """Read .env into os.environ without overwriting anything already set.

    A hand-rolled six-line parser rather than a dependency: this is the only file
    that needs it, and the value it reads is a secret, so it is worth being able to
    see exactly what happens to it.
    """
    env_path = path or PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


# -------------------------------------------------------------------- redaction

_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")
_SECRET_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def redact(value: Any) -> Any:
    """Strip anything that looks like a credential, recursively.

    Applied to every payload on its way into a trace. Two passes: the literal value
    of any secret environment variable, and anything shaped like an API key. Keys
    should never reach here in the first place - this is the guard for when a future
    change accidentally puts a request object into the trace.
    """
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        cleaned = value
        for var in _SECRET_ENV_VARS:
            secret = os.environ.get(var)
            if secret and len(secret) > 8 and secret in cleaned:
                cleaned = cleaned.replace(secret, "[redacted]")
        return _SECRET_PATTERN.sub("[redacted]", cleaned)
    return value


# ----------------------------------------------------------------------- models


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }


@dataclass(frozen=True)
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str
    model: str
    usage: Usage
    latency_ms: int
    raw_content: list[dict[str, Any]] = field(default_factory=list)
    """The assistant turn exactly as the provider returned it, for replay in the
    next request. Anthropic requires tool_use blocks be echoed back unchanged."""


def resolve_pricing(model: str) -> dict[str, float] | None:
    """Find the rate card for a model id.

    The API answers with the dated snapshot it actually served — ask for
    `claude-haiku-4-5` and the response says `claude-haiku-4-5-20251001`. Matching on
    the longest table key the id starts with means a run is priced rather than
    silently reported as free, without pinning a date in the table.
    """
    if model in PRICING:
        return PRICING[model]
    candidates = [key for key in PRICING if model.startswith(key)]
    return PRICING[max(candidates, key=len)] if candidates else None


def estimate_cost_usd(model: str, usage: Usage) -> float | None:
    """Cost of one call at list prices, or None for a model with no price on file."""
    rates = resolve_pricing(model)
    if rates is None:
        return None
    million = 1_000_000
    return round(
        (usage.input_tokens / million) * rates["input"]
        + (usage.output_tokens / million) * rates["output"]
        + (usage.cache_read_tokens / million) * rates["input"] * CACHE_READ_MULTIPLIER
        + (usage.cache_write_tokens / million) * rates["input"] * CACHE_WRITE_MULTIPLIER,
        6,
    )


# --------------------------------------------------------------------- backends


class LLMError(RuntimeError):
    """A backend failed in a way the agent loop cannot recover from."""


class FakeLLM:
    """A scripted backend for tests.

    Give it a list of responses; each `complete()` returns the next one and records
    the request. Deterministic, offline, and instant - which is what lets the agent
    loop's guards (step budget, duplicate detection, gate retries) be tested at all.
    A real model cannot be asked to reliably loop, stall, or cite a fabricated quote.
    """

    name = "fake"

    def __init__(self, responses: Sequence[LLMResponse] | None = None, model: str = "fake-model"):
        self.model = model
        self.scripted: list[LLMResponse] = list(responses or [])
        self.requests: list[dict[str, Any]] = []

    def queue(self, *responses: LLMResponse) -> "FakeLLM":
        self.scripted.extend(responses)
        return self

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | None = None,
    ) -> LLMResponse:
        self.requests.append(
            {
                "system": system,
                # A copy: the agent appends to the live list as the run continues, so
                # holding the reference would make every recorded request look like
                # the last one.
                "messages": list(messages),
                "tools": [t["name"] for t in tools],
                "tool_choice": tool_choice,
            }
        )
        if not self.scripted:
            raise LLMError("FakeLLM ran out of scripted responses")
        return self.scripted.pop(0)


class AnthropicLLM:
    """The Anthropic Messages API through the official SDK.

    Note the deliberate absence of `output_config.effort` and adaptive thinking:
    both are rejected by claude-haiku-4-5, which is a pre-4.6 model. Pointing this
    backend at a newer model would be the moment to add them.
    """

    name = "anthropic"

    def __init__(self, model: str = DEFAULT_ANTHROPIC_MODEL, max_tokens: int = MAX_TOKENS):
        load_env()
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise LLMError(
                "ANTHROPIC_API_KEY is not set. Put it in .env (which is gitignored) "
                "or export it before running."
            )
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise LLMError("the anthropic package is not installed: pip install anthropic") from exc

        self._anthropic = anthropic
        # The SDK reads ANTHROPIC_API_KEY from the environment itself. Passing it
        # explicitly would mean holding the secret in a local variable for no gain.
        self._client = anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
            "tools": tools,
        }
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice

        started = time.perf_counter()
        try:
            response = self._client.messages.create(**kwargs)
        except self._anthropic.NotFoundError as exc:
            raise LLMError(f"model {self.model!r} not found: {exc}") from exc
        except self._anthropic.RateLimitError as exc:
            raise LLMError(f"rate limited: {exc}") from exc
        except self._anthropic.APIStatusError as exc:
            raise LLMError(f"API error {exc.status_code}: {exc.message}") from exc
        except self._anthropic.APIConnectionError as exc:
            raise LLMError(f"could not reach the API: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.stop_reason == "refusal":
            raise LLMError("the model declined this request (stop_reason=refusal)")

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))

        usage = Usage(
            input_tokens=response.usage.input_tokens or 0,
            output_tokens=response.usage.output_tokens or 0,
            cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        )
        return LLMResponse(
            text="\n".join(text_parts).strip(),
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "end_turn",
            model=response.model,
            usage=usage,
            latency_ms=latency_ms,
            raw_content=[block.model_dump(exclude_none=True) for block in response.content],
        )


class OllamaLLM:
    """A local OpenAI-compatible server, for running the loop without an API key.

    Translates the Anthropic-shaped conversation this project uses into OpenAI chat
    format and back. Optional by design: it exists so the agent loop can be
    exercised offline, not because the triage quality is expected to match.
    """

    name = "ollama"

    def __init__(self, model: str = DEFAULT_OLLAMA_MODEL, base_url: str = OLLAMA_BASE_URL):
        self.model = model
        self.base_url = base_url.rstrip("/")

    @staticmethod
    def is_running(base_url: str = OLLAMA_BASE_URL, timeout: float = 1.0) -> bool:
        try:
            with urllib.request.urlopen(f"{base_url}/models", timeout=timeout) as response:
                return response.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *_to_openai_messages(messages)],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["input_schema"],
                    },
                }
                for tool in tools
            ],
        }
        if tool_choice is not None and tool_choice.get("type") == "tool":
            payload["tool_choice"] = {"type": "function", "function": {"name": tool_choice["name"]}}

        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise LLMError(f"ollama request failed: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        choice = body["choices"][0]
        message = choice["message"]
        tool_calls = [
            ToolCall(
                id=call.get("id") or f"call_{index}",
                name=call["function"]["name"],
                arguments=json.loads(call["function"].get("arguments") or "{}"),
            )
            for index, call in enumerate(message.get("tool_calls") or [])
        ]
        usage_body = body.get("usage") or {}
        usage = Usage(
            input_tokens=usage_body.get("prompt_tokens", 0),
            output_tokens=usage_body.get("completion_tokens", 0),
        )
        return LLMResponse(
            text=(message.get("content") or "").strip(),
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn",
            model=body.get("model", self.model),
            usage=usage,
            latency_ms=latency_ms,
            raw_content=_anthropic_blocks_from_openai(message, tool_calls),
        )


def _to_openai_messages(messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic content blocks -> OpenAI chat messages.

    The shapes differ in one structural way: Anthropic returns every tool result of
    a turn inside one user message, while OpenAI wants one `role: "tool"` message
    per result. That fan-out is the whole of this function.
    """
    converted: list[dict[str, Any]] = []
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            converted.append({"role": message["role"], "content": content})
            continue

        text_parts = [b["text"] for b in content if b.get("type") == "text"]
        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        tool_results = [b for b in content if b.get("type") == "tool_result"]

        if message["role"] == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts)}
            if tool_uses:
                entry["tool_calls"] = [
                    {
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block.get("input", {})),
                        },
                    }
                    for block in tool_uses
                ]
            converted.append(entry)
            continue

        if text_parts:
            converted.append({"role": "user", "content": "\n".join(text_parts)})
        for block in tool_results:
            converted.append(
                {
                    "role": "tool",
                    "tool_call_id": block["tool_use_id"],
                    "content": block["content"],
                }
            )
    return converted


def _anthropic_blocks_from_openai(message: dict[str, Any], tool_calls: list[ToolCall]) -> list[dict]:
    blocks: list[dict[str, Any]] = []
    if message.get("content"):
        blocks.append({"type": "text", "text": message["content"]})
    for call in tool_calls:
        blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments})
    return blocks


# ------------------------------------------------------------------- selection


def get_client(backend: str | None = None, *, model: str | None = None):
    """Build the backend named by FINREQ_LLM, defaulting to the fake one.

    Defaulting to `fake` means a missing environment variable can never turn a test
    run into a billed API call.
    """
    choice = (backend or os.environ.get("FINREQ_LLM") or "fake").strip().lower()
    if choice == "fake":
        return FakeLLM(model=model or "fake-model")
    if choice == "anthropic":
        return AnthropicLLM(model=model or DEFAULT_ANTHROPIC_MODEL)
    if choice == "ollama":
        return OllamaLLM(model=model or DEFAULT_OLLAMA_MODEL)
    raise LLMError(f"unknown FINREQ_LLM backend {choice!r}; expected fake, anthropic or ollama")
