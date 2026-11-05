"""Race assistant: researches a race on the web and proposes a calendar entry.

Tools: web_search (SearXNG), fetch_page (reads a page, schema.org Event data first) and propose_race.
propose_race never saves anything: proposals go back to the UI as cards, and the user clicks
Add/Update, which goes through the normal races API.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from homecoach import llm, websearch
from homecoach.config import Settings
from homecoach.llm import Tool, ToolRejected

PROPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "race_id": {"type": "integer", "description": "Existing race id, only when changing a race already in the calendar"},
        "name": {"type": "string", "description": "Official name with year, e.g. 'Corrida Sporting 2026'"},
        "date": {"type": "string", "description": "Race day as YYYY-MM-DD"},
        "distance_km": {"type": "number", "description": "e.g. 5, 10, 21.0975, 42.195"},
        "goal_time": {"type": "string", "description": "User's goal as h:mm:ss or mm:ss, only if the user gave one"},
        "priority": {"type": "string", "enum": ["A", "B", "C"]},
        "location": {"type": "string"},
        "start_time": {"type": "string", "description": "Local start time if published, e.g. 10:00"},
        "source_url": {"type": "string", "description": "Page the date and distance came from"},
        "notes": {"type": "string"},
    },
    "required": ["name", "date", "distance_km", "priority"],
}


def system_prompt(today: date, athlete: str, races: list[dict]) -> str:
    cal = (
        "\n".join(
            f"- id {r['id']}: {r['name']} on {r['date']}, {r['distance_km']} km, "
            f"goal {r['goal_time'] or 'none'}, priority {r['priority']}"
            for r in races
        )
        or "(empty)"
    )
    return f"""You add races to {athlete}'s training calendar. Today is {today.isoformat()}.

Steps:
1. Call web_search to find the race's official page (prefer the organiser's site).
2. Call fetch_page on the best result to read the exact date of the NEXT edition on or after today,
   the distances and the location.
3. Call propose_race with what you found. The user sees it as a card and confirms; you do not save anything.
4. Reply in one or two short sentences: date, distance, place, and the source site.

Rules:
- Never guess a date. If the next edition's date isn't published, say so and don't call propose_race.
- If the race has several distances and the user didn't say which, ask which one.
- Include goal_time only if the user gave a goal. Never invent one.
- Priority: what the user said. If unsaid and no future A race exists, use A, otherwise ask.
- To change an existing race, pass its race_id. You cannot delete races: tell the user to use Delete in the table.

Current calendar:
{cal}"""


def chat(s: Settings, messages: list[dict[str, str]], today: date, races: list[dict], client=None) -> dict[str, Any]:
    proposals: list[dict] = []
    sources: dict[str, str] = {}

    def web_search(args: dict) -> str:
        q = str(args.get("query", "")).strip()
        if not q:
            raise ToolRejected("query is required")
        results = websearch.search(s, q)
        for r in results:
            sources.setdefault(r["url"], r["title"])
        return json.dumps(results, ensure_ascii=False) if results else "No results. Try different words."

    def fetch_page(args: dict) -> str:
        url = str(args.get("url", ""))
        try:
            return websearch.fetch_page(url)
        except Exception as e:  # report to the model, it can try another result
            raise ToolRejected(f"couldn't read {url}: {e}") from e

    def propose_race(args: dict) -> str:
        d = str(args.get("date", ""))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            raise ToolRejected("date must be YYYY-MM-DD")
        if date.fromisoformat(d) < today:
            raise ToolRejected(f"{d} is in the past; find the next edition on or after {today.isoformat()}")
        try:
            km = float(args.get("distance_km"))
        except (TypeError, ValueError) as e:
            raise ToolRejected("distance_km must be a number") from e
        if not 0.5 <= km <= 400:
            raise ToolRejected("distance_km looks wrong")
        if args.get("priority") not in ("A", "B", "C"):
            args["priority"] = "A"
        if args.get("race_id") is not None and not any(r["id"] == args["race_id"] for r in races):
            args.pop("race_id")
        proposals.append({**args, "date": d, "distance_km": km})
        return "Shown to the user as a card to confirm. Now reply briefly."

    tools = [
        Tool(
            "web_search",
            "Search the web. Returns titles, urls and snippets.",
            {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            web_search,
        ),
        Tool(
            "fetch_page",
            "Read the text of a web page (structured event data first).",
            {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            fetch_page,
        ),
        Tool(
            "propose_race",
            "Show the user a race card to confirm. Nothing is saved until they click Add.",
            PROPOSE_SCHEMA,
            propose_race,
        ),
    ]
    res = llm.run(s, system_prompt(today, s.athlete_name, races), messages, tools, max_rounds=8, client=client)
    used = [p.get("source_url") for p in proposals if p.get("source_url")]
    src = [{"url": u, "title": sources.get(u, u)} for u in used] or [{"url": u, "title": t} for u, t in list(sources.items())[:2]]
    return {
        "reply": res.text or ("Here's what I found." if proposals else "Sorry, I couldn't find that."),
        "proposals": proposals,
        "sources": src,
    }
