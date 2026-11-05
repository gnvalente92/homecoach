"""Race assistant: a small chat that researches races on the web and proposes calendar entries.

It calls the Claude API directly (no SDK) with two tools:
- web_search: Anthropic's server-side web search, which finds the official race page
- propose_race: a client tool. The backend never saves anything from it. Proposals go back to the
  UI as cards, and the user clicks "Add" (or "Update") to save them through the normal races API.

Conversation history is plain text (user messages and the assistant's final text), so no web-search
blocks have to be kept between turns.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx

from homecoach.config import Settings

log = logging.getLogger(__name__)
API_URL = "https://api.anthropic.com/v1/messages"
MAX_ROUNDS = 8

PROPOSE_TOOL = {
    "name": "propose_race",
    "description": (
        "Show the user a race to add to (or update in) their calendar. Nothing is saved until the user confirms. "
        "Call once per race. Only call it when the date and distance come from a source you found or from the user."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "race_id": {"type": "integer", "description": "Existing race id when this updates a race already in the calendar"},
            "name": {"type": "string", "description": "Official race name including the year, e.g. 'Corrida Sporting 2026'"},
            "date": {"type": "string", "description": "Race day, YYYY-MM-DD"},
            "distance_km": {"type": "number", "description": "Race distance in km, e.g. 10, 21.0975, 42.195"},
            "goal_time": {"type": "string", "description": "User's goal as h:mm:ss or mm:ss, only if the user gave one"},
            "priority": {"type": "string", "enum": ["A", "B", "C"], "description": "A main race, B tune-up, C training race"},
            "location": {"type": "string"},
            "start_time": {"type": "string", "description": "Local start time if published, e.g. 10:00"},
            "source_url": {"type": "string", "description": "Official page you got the date and distance from"},
            "notes": {
                "type": "string",
                "description": "Short useful facts: course profile, other distances on the day, registration",
            },
        },
        "required": ["name", "date", "distance_km", "priority"],
    },
}


def system_prompt(today: date, timezone: str, athlete: str, races: list[dict]) -> str:
    cal = (
        "\n".join(
            f"- id {r['id']}: {r['name']}, {r['date']}, {r['distance_km']} km, "
            f"goal {r['goal_time'] or 'none'}, priority {r['priority']}"
            for r in races
        )
        or "(empty)"
    )
    return f"""You help {athlete} keep a race calendar in a self-hosted training app. Today is {today.isoformat()} ({timezone}).

When they mention a race:
1. Use web search to find the official page for the NEXT edition on or after today: exact date,
   distances on offer, location, start time.
   Prefer the organiser's site over aggregators. If the next edition's date isn't published yet, say so and don't guess.
2. If the race has several distances and they didn't say which, ask.
3. Priority: A = main race the plan builds towards, B = tune-up, C = training race. Use what they said. If they didn't
   say and the calendar has no A race in the future, use A. Otherwise ask.
4. A goal time is only included if they gave one. Never invent one. You may comment on how realistic it is.
5. Call propose_race (with race_id when changing an existing race). Then reply in 1–3 short sentences:
   what you found, with the source, and that they can click Add (or Update) to save it.
To remove a race, tell them to use Delete in the table. You can't delete.

Current calendar:
{cal}"""


class AssistantError(RuntimeError):
    pass


def chat(
    settings: Settings, messages: list[dict[str, str]], today: date, races: list[dict], client: httpx.Client | None = None
) -> dict[str, Any]:
    """messages: [{"role": "user"|"assistant", "content": "text"}, ...] ending with a user message."""
    if not settings.anthropic_api_key:
        raise AssistantError("The race assistant isn't set up: add HC_ANTHROPIC_API_KEY to .env and restart the app.")
    convo: list[dict[str, Any]] = [{"role": m["role"], "content": m["content"]} for m in messages if m.get("content")]
    tools = [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": settings.assistant_max_searches},
        PROPOSE_TOOL,
    ]
    body = {
        "model": settings.assistant_model,
        "max_tokens": 1500,
        "system": system_prompt(today, settings.timezone, settings.athlete_name, races),
        "tools": tools,
    }
    headers = {
        "x-api-key": settings.anthropic_api_key.get_secret_value(),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    http = client or httpx.Client(timeout=120)
    proposals: list[dict] = []
    sources: dict[str, str] = {}
    texts: list[str] = []
    searches = 0
    for _ in range(MAX_ROUNDS):
        r = http.post(API_URL, json={**body, "messages": convo}, headers=headers)
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message", r.text)
            except ValueError:
                msg = r.text
            raise AssistantError(f"Claude API error {r.status_code}: {msg}")
        data = r.json()
        content = data.get("content", [])
        tool_results = []
        for block in content:
            t = block.get("type")
            if t == "text" and block.get("text", "").strip():
                texts.append(block["text"].strip())
                for c in block.get("citations") or []:
                    if c.get("url"):
                        sources[c["url"]] = c.get("title") or c["url"]
            elif t == "server_tool_use":
                searches += 1
            elif t == "web_search_tool_result" and isinstance(block.get("content"), list):
                for res in block["content"]:
                    if res.get("url"):
                        sources.setdefault(res["url"], res.get("title") or res["url"])
            elif t == "tool_use" and block.get("name") == "propose_race":
                proposals.append(block.get("input") or {})
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block["id"], "content": "Shown to the user as a card to confirm."}
                )
        stop = data.get("stop_reason")
        convo.append({"role": "assistant", "content": content})
        if stop == "pause_turn":
            continue  # long search turn: send it back unchanged and let it continue
        if stop == "tool_use" and tool_results:
            convo.append({"role": "user", "content": tool_results})
            texts.clear()  # keep only the reply that follows the tool call
            continue
        break
    cited = [{"url": u, "title": t} for u, t in sources.items() if any(u == p.get("source_url") for p in proposals)]
    return {
        "reply": "\n\n".join(texts) or ("Here's what I found." if proposals else "Sorry, I couldn't work that out."),
        "proposals": proposals,
        "sources": cited or [{"url": u, "title": t} for u, t in list(sources.items())[:3]],
        "searches": searches,
    }
