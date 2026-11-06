"""Provider-neutral LLM tool loop.

One small interface for two wire formats:
- OpenAI-compatible chat completions (Ollama, llama.cpp server, LM Studio, vLLM, OpenRouter, ...)
- Anthropic Messages API

Tools are plain Python functions: `Tool(name, description, parameters, fn)`. `fn(args) -> str` returns the
text the model sees as the tool result. Raise ToolRejected to hand an error back so the model can retry.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from homecoach.config import Settings

log = logging.getLogger(__name__)
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


class LLMError(RuntimeError):
    pass


class ToolRejected(Exception):
    """Raised by a tool to tell the model its call was invalid (the message is shown to the model)."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[[dict[str, Any]], str]


@dataclass
class Result:
    text: str
    calls: list[tuple[str, dict]] = field(default_factory=list)  # (tool name, args) of successful calls


def enabled(s: Settings) -> bool:
    return s.llm_provider != "none" and (s.llm_provider != "anthropic" or bool(s.llm_api_key))


def run(
    s: Settings,
    system: str,
    messages: list[dict[str, str]],
    tools: list[Tool],
    *,
    max_rounds: int = 10,
    client: httpx.Client | None = None,
) -> Result:
    """Run the conversation until the model answers without calling tools (or max_rounds)."""
    if not enabled(s):
        raise LLMError("No LLM is configured: set HC_LLM_PROVIDER (and HC_LLM_BASE_URL / HC_LLM_MODEL) in .env.")
    http = client or httpx.Client(timeout=s.llm_timeout_s)
    by_name = {t.name: t for t in tools}
    fn = _anthropic if s.llm_provider == "anthropic" else _openai
    return fn(s, http, system, messages, tools, by_name, max_rounds)


def _call_tool(by_name: dict[str, Tool], name: str, args: Any, calls: list) -> str:
    t = by_name.get(name)
    if t is None:
        return f"Error: unknown tool {name!r}. Available: {', '.join(by_name)}"
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except json.JSONDecodeError as e:
            return f"Error: arguments were not valid JSON ({e}). Call the tool again with valid JSON."
    try:
        out = t.fn(args or {})
        calls.append((name, args or {}))
        return out
    except ToolRejected as e:
        return f"Rejected: {e}"
    except Exception as e:  # a tool bug should not kill the conversation
        log.exception("tool %s failed", name)
        return f"Error: {e}"


def _check(r: httpx.Response) -> dict:
    if r.status_code >= 400:
        try:
            body = r.json()
            msg = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else body.get("error")
        except ValueError:
            msg = r.text[:300]
        raise LLMError(f"LLM server error {r.status_code}: {msg or r.text[:300]}")
    return r.json()


def tune_system(s: Settings, system: str) -> str:
    """Keep reasoning models' thinking short (see Settings.llm_reasoning), using each family's own switch."""
    model = s.llm_model.lower()
    if s.llm_reasoning != "low":
        return system
    if model.startswith("gpt-oss"):
        return "Reasoning: low\n\n" + system  # gpt-oss reads its reasoning effort from the system message
    if model.startswith("qwen3") and "instruct" not in model:
        return system + "\n\n/no_think"  # Qwen3's soft switch; the instruct-2507 variants never think
    return system


def _openai(s, http, system, messages, tools, by_name, max_rounds) -> Result:
    system = tune_system(s, system)
    url = s.llm_base_url.rstrip("/") + "/chat/completions"
    headers = {"content-type": "application/json"}
    if s.llm_api_key:
        headers["authorization"] = f"Bearer {s.llm_api_key.get_secret_value()}"
    convo: list[dict[str, Any]] = [{"role": "system", "content": system}, *messages]
    spec = [
        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
        for t in tools
    ]
    calls: list = []
    for _ in range(max_rounds):
        try:
            data = _check(
                http.post(
                    url,
                    json={"model": s.llm_model, "messages": convo, "temperature": 0.2, **({"tools": spec} if spec else {})},
                    headers=headers,
                )
            )
        except httpx.HTTPError as e:
            raise LLMError(f"Couldn't reach the LLM at {s.llm_base_url}: {e}") from e
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        tcs = msg.get("tool_calls") or []
        if not tcs:
            # Some small models write the tool call as JSON text instead of using tool_calls.
            parsed = _inline_tool_call(msg.get("content") or "", by_name)
            if parsed:
                tcs = [{"id": "inline", "function": {"name": parsed[0], "arguments": json.dumps(parsed[1])}}]
                msg = {**msg, "content": "", "tool_calls": tcs}
        if not tcs:
            return Result(_clean(msg.get("content") or ""), calls)
        convo.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tcs})
        for tc in tcs:
            f = tc.get("function") or {}
            out = _call_tool(by_name, f.get("name", ""), f.get("arguments") or "{}", calls)
            convo.append({"role": "tool", "tool_call_id": tc.get("id", ""), "name": f.get("name", ""), "content": out})
    return Result("I ran out of steps before finishing — please try again or rephrase.", calls)


def _anthropic(s, http, system, messages, tools, by_name, max_rounds) -> Result:
    headers = {
        "x-api-key": s.llm_api_key.get_secret_value(),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    spec = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
    convo: list[dict[str, Any]] = list(messages)
    calls: list = []
    for _ in range(max_rounds):
        try:
            data = _check(
                http.post(
                    ANTHROPIC_URL,
                    headers=headers,
                    json={
                        "model": s.llm_model,
                        "max_tokens": 2000,
                        "system": system,
                        "messages": convo,
                        **({"tools": spec} if spec else {}),
                    },
                )
            )
        except httpx.HTTPError as e:
            raise LLMError(f"Couldn't reach the Claude API: {e}") from e
        content = data.get("content") or []
        uses = [b for b in content if b.get("type") == "tool_use"]
        if not uses:
            return Result(_clean(" ".join(b.get("text", "") for b in content if b.get("type") == "text")), calls)
        convo.append({"role": "assistant", "content": content})
        convo.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": u["id"],
                        "content": _call_tool(by_name, u["name"], u.get("input") or {}, calls),
                    }
                    for u in uses
                ],
            }
        )
    return Result("I ran out of steps before finishing — please try again or rephrase.", calls)


_THINK = re.compile(r"<think>.*?</think>", re.S)


def _clean(text: str) -> str:
    return _THINK.sub("", text).strip()


def _inline_tool_call(text: str, by_name: dict[str, Tool]) -> tuple[str, dict] | None:
    m = re.search(r"\{.*\}", _clean(text), re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    name = obj.get("name") or obj.get("tool")
    args = obj.get("arguments") or obj.get("parameters") or obj.get("args")
    if name in by_name and isinstance(args, dict):
        return name, args
    return None
