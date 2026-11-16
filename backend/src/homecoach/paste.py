"""Claude hand-off: prompts the athlete copies into Claude, and checks on the JSON they paste back.

The app never calls a model. It writes a prompt with what Claude needs to know (today's date, the race calendar,
the day's nutrition target), the athlete pastes Claude's answer, and these functions turn it into proposals.
Nothing is saved here: proposals go back to the UI as cards and the athlete clicks Add, which goes through the
normal races and food-log APIs.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any


class PasteError(ValueError):
    """The pasted answer can't be used; the message says what to fix."""


def extract_json(text: str) -> Any:
    """The JSON in Claude's answer: a ```json block if there is one, otherwise the outermost {...}."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    raw = fenced.group(1) if fenced else text
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise PasteError("no JSON found: paste Claude's whole answer, including the {...} block")
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError as e:
        raise PasteError(f"the JSON is incomplete or malformed ({e.msg} at character {e.pos})") from e


def _str(x: Any, limit: int) -> str | None:
    if x is None:
        return None
    return str(x).strip()[:limit] or None


# ---- races --------------------------------------------------------------------------------------

RACE_EXAMPLE = {
    "races": [
        {
            "race_id": None,
            "name": "Example City 10K 2027",
            "date": "2027-03-14",
            "distance_km": 10,
            "goal_time": "49:00",
            "priority": "A",
            "location": "Example City",
            "start_time": "10:00",
            "source_url": "https://example.org/10k",
            "notes": None,
        }
    ]
}


def race_prompt(request: str, today: date, races: list[dict]) -> str:
    cal = (
        "\n".join(
            f"- race_id {r['id']}: {r['name']} on {r['date']}, {r['distance_km']:g} km, "
            f"goal {r['goal_time'] or 'none'}, priority {r['priority']}"
            for r in races
        )
        or "- (no races yet)"
    )
    default = "B" if any(r["priority"] == "A" for r in races) else "A"
    return f"""I'm adding a race to my running training app. Today is {today.isoformat()}.

The race: {request.strip() or "<race name, and my goal time if I have one>"}

Search the web for the organiser's official page and find the NEXT edition on or after {today.isoformat()}.
Reply with only one JSON code block in exactly this format, and no other text:

{json.dumps(RACE_EXAMPLE, indent=2)}

Rules:
- Never guess a date. If the next edition's date isn't published, reply {{"races": [], "error": "<why>"}}.
- distance_km is a number: 21.0975 for a half marathon, 42.195 for a marathon.
- If the race has several distances and I didn't say which, add one entry per distance; I'll pick one.
- goal_time (h:mm:ss or mm:ss) only if I gave one above; otherwise null. Never invent one.
- priority: A = main race, B = tune-up, C = training race. Use what I said; otherwise "{default}".
- race_id: the id from my calendar below if this updates a race already in it; otherwise null.
- start_time is the local start time if published (HH:MM), otherwise null. source_url is the page the date came from.

My calendar:
{cal}"""


def parse_races(text: str, today: date, races: list[dict]) -> list[dict]:
    data = extract_json(text)
    if not isinstance(data, dict) or not isinstance(data.get("races"), list):
        raise PasteError('expected {"races": [...]}')
    if not data["races"]:
        raise PasteError(f"Claude found no race: {_str(data.get('error'), 300) or 'no reason given'}")
    ids = {r["id"] for r in races}
    out = []
    for i, r in enumerate(data["races"][:10], 1):
        if not isinstance(r, dict):
            raise PasteError(f"race {i} is not an object")
        name = _str(r.get("name"), 200)
        if not name:
            raise PasteError(f"race {i} has no name")
        try:
            day = date.fromisoformat(str(r.get("date")))
        except ValueError as e:
            raise PasteError(f"{name}: date must be YYYY-MM-DD") from e
        if day < today:
            raise PasteError(f"{name}: {day} is in the past; ask Claude for the next edition")
        try:
            km = float(r["distance_km"])
        except (KeyError, TypeError, ValueError) as e:
            raise PasteError(f"{name}: distance_km must be a number") from e
        if not 0.5 <= km <= 400:
            raise PasteError(f"{name}: {km:g} km doesn't look like a race distance")
        goal = _str(r.get("goal_time"), 10)
        if goal and not re.fullmatch(r"\d{1,2}(:\d{2}){1,2}", goal):
            raise PasteError(f"{name}: goal_time must be h:mm:ss or mm:ss")
        url = _str(r.get("source_url"), 500)
        out.append(
            {
                "race_id": r.get("race_id") if r.get("race_id") in ids else None,
                "name": name,
                "date": day,
                "distance_km": km,
                "goal_time": goal,
                "priority": r.get("priority") if r.get("priority") in ("A", "B", "C") else "A",
                "location": _str(r.get("location"), 120),
                "start_time": _str(r.get("start_time"), 10),
                "source_url": url if url and url.startswith(("https://", "http://")) else None,
                "notes": _str(r.get("notes"), 500),
            }
        )
    return out


# ---- food ---------------------------------------------------------------------------------------

MEALS = ("breakfast", "lunch", "dinner", "snack")
ALCOHOL = ("beer", "wine", "cerveja", "vinho", "cider", "gin", "vodka", "whisky", "rum", "cocktail", "sangria")
FOOD_EXAMPLE = {
    "meals": [
        {
            "meal": "breakfast",
            "day": "YYYY-MM-DD",
            "items": [
                {
                    "name": "Porridge with semi-skimmed milk",
                    "quantity": "40 g oats",
                    "kcal": 280,
                    "protein_g": 12,
                    "carbs_g": 40,
                    "fat_g": 7,
                },
                {"name": "Banana", "quantity": "1 medium", "kcal": 105, "protein_g": 1.3, "carbs_g": 27, "fat_g": 0.4},
            ],
            "assumptions": "250 ml milk",
        }
    ]
}


def _totals(x: dict | None) -> str:
    if not x:
        return "nothing logged yet"
    return f"{x['kcal']} kcal, protein {x['protein_g']} g, carbs {x['carbs_g']} g, fat {x['fat_g']} g"


def food_prompt(request: str, day: date, target: dict | None, eaten: dict | None) -> str:
    goal = (
        f"{target['kcal']} kcal, protein {target['protein_g']} g, carbs {target['carbs_g']} g, fat {target['fat_g']} g "
        f"({target['load']} day)"
        if target
        else "unknown"
    )
    return f"""I'm a runner logging food in my training app. Estimate the energy and macros of what I ate on {day.isoformat()}:

{request.strip() or "<what I ate, with amounts if I know them>"}

My target that day: {goal}. Logged so far: {_totals(eaten)}.

Reply with one JSON code block in exactly this format:

{json.dumps({"meals": [{**FOOD_EXAMPLE["meals"][0], "day": day.isoformat()}]}, indent=2)}

Rules:
- meal is one of {", ".join(MEALS)}; use one entry per meal if I described several.
- day is {day.isoformat()} unless I said otherwise.
- One item per food, each with an amount. If I gave no amount, assume a normal adult portion and say so in assumptions.
- kcal must be close to 4 x protein_g + 4 x carbs_g + 9 x fat_g (alcohol adds 7 kcal/g on top).
- Use cooked weights when I give them.
- After the JSON block you may add one sentence on what's left for the day."""


def check_item(i: Any) -> dict:
    if not isinstance(i, dict):
        raise PasteError("each item must be an object")
    try:
        name = str(i["name"]).strip()[:120]
        quantity = _str(i.get("quantity"), 60)
        kcal, protein, carbs, fat = (round(float(i[k]), 1) for k in ("kcal", "protein_g", "carbs_g", "fat_g"))
    except (KeyError, TypeError, ValueError) as e:
        raise PasteError("each item needs name, quantity and numeric kcal, protein_g, carbs_g, fat_g") from e
    if not name:
        raise PasteError("an item has no name")
    if min(kcal, protein, carbs, fat) < 0:
        raise PasteError(f"{name}: values can't be negative")
    if kcal > 3000 or protein > 250 or carbs > 500 or fat > 250:
        raise PasteError(f"{name}: that's more than one sitting; check the portion")
    macros = 4 * (protein + carbs) + 9 * fat
    alcohol = any(w in name.lower() for w in ALCOHOL)
    off = kcal - macros
    if abs(off) > max(40, 0.25 * macros) and not (alcohol and off > 0):
        raise PasteError(
            f"{name}: {kcal:g} kcal doesn't match the macros "
            f"(4×{protein:g} + 4×{carbs:g} + 9×{fat:g} = {macros:.0f}); ask Claude to fix the numbers"
        )
    return {"name": name, "quantity": quantity, "kcal": kcal, "protein_g": protein, "carbs_g": carbs, "fat_g": fat}


def parse_food(text: str, today: date, day: date) -> list[dict]:
    data = extract_json(text)
    if not isinstance(data, dict) or not isinstance(data.get("meals"), list) or not data["meals"]:
        raise PasteError('expected {"meals": [...]} with at least one meal')
    out = []
    for m in data["meals"][:6]:
        if not isinstance(m, dict):
            raise PasteError("each meal must be an object")
        meal = str(m.get("meal", "")).lower()
        if meal not in MEALS:
            raise PasteError(f"meal must be one of {', '.join(MEALS)}")
        d = day
        if m.get("day"):
            try:
                d = date.fromisoformat(str(m["day"]))
            except ValueError as e:
                raise PasteError("day must be YYYY-MM-DD") from e
            if not today - timedelta(days=60) <= d <= today:
                raise PasteError(f"day must be between {today - timedelta(days=60)} and today ({today})")
        items = m.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 30:
            raise PasteError("items must be a list of 1 to 30 foods")
        checked = [check_item(i) for i in items]
        total = {k: round(sum(i[k] for i in checked)) for k in ("kcal", "protein_g", "carbs_g", "fat_g")}
        out.append({"day": d, "meal": meal, "items": checked, "total": total, "assumptions": _str(m.get("assumptions"), 300)})
    return out
