"""Food chat: the local AI estimates what was eaten and proposes food-log entries.

One tool, propose_food. Like the race assistant it never saves anything: proposals go back to the UI as a
card and the user clicks Add, which goes through the normal food-log API. Estimates are checked for
consistency (energy has to match the macros) and implausible numbers are sent back to the model to fix.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from homecoach import llm
from homecoach.config import Settings
from homecoach.llm import Tool, ToolRejected

MEALS = ("breakfast", "lunch", "dinner", "snack")
ALCOHOL = ("beer", "wine", "cerveja", "vinho", "cider", "gin", "vodka", "whisky", "rum", "cocktail", "sangria")

ITEM = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Food, e.g. 'Scrambled eggs'"},
        "quantity": {"type": "string", "description": "Amount, e.g. '2 eggs', '150 g', '1 slice', '330 ml'"},
        "kcal": {"type": "number"},
        "protein_g": {"type": "number"},
        "carbs_g": {"type": "number"},
        "fat_g": {"type": "number"},
    },
    "required": ["name", "quantity", "kcal", "protein_g", "carbs_g", "fat_g"],
}
PROPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "meal": {"type": "string", "enum": list(MEALS)},
        "day": {"type": "string", "description": "YYYY-MM-DD; today unless the user said otherwise"},
        "items": {"type": "array", "items": ITEM, "minItems": 1},
        "assumptions": {"type": "string", "description": "Portions you assumed, in a few words"},
    },
    "required": ["meal", "items"],
}

# A few anchors keep a small model's estimates in a sensible range.
REFERENCE = """Typical values (portion: kcal, protein/carbs/fat g):
- 1 large egg: 75, 6/0.5/5 · 1 slice wholemeal bread (40 g): 95, 4/16/1.5 · 1 medium banana: 105, 1.3/27/0.4
- 100 g cooked rice: 130, 2.7/28/0.3 · 100 g cooked pasta: 155, 5.5/30/1 · 100 g potatoes (boiled): 85, 2/19/0.1
- 100 g chicken breast (cooked): 165, 31/0/3.6 · 100 g salmon (cooked): 205, 22/0/13 · 100 g lean beef (cooked): 220, 27/0/12
- 150 g Greek yoghurt 2%: 110, 15/6/3 · 250 ml milk (semi-skimmed): 125, 8.5/12/4.5 · 30 g oats: 115, 4/20/2
- 1 tbsp olive oil (13 g): 120, 0/0/13 · 30 g cheese: 120, 7.5/0.5/10 · 1 apple: 95, 0.5/25/0.3
- 1 energy gel: 100, 0/25/0 · 30 g whey protein: 120, 24/3/1.5 · 1 pastel de nata: 220, 3/26/11
- 330 ml beer: 145, 1.5/13/0 (alcohol: 7 kcal/g, not counted in the macros)"""


def _totals(x: dict | None) -> str:
    if not x:
        return "nothing logged yet"
    return f"{x['kcal']} kcal, protein {x['protein_g']} g, carbs {x['carbs_g']} g, fat {x['fat_g']} g"


def system_prompt(athlete: str, today: date, day: date, target: dict | None, eaten: dict | None) -> str:
    goal = (
        f"{target['kcal']} kcal, protein {target['protein_g']} g, carbs {target['carbs_g']} g, fat {target['fat_g']} g "
        f"({target['load']} day: {'; '.join(target['reasons'])})"
        if target
        else "unknown (no profile yet)"
    )
    return f"""You help {athlete}, a runner, log food. Today is {today.isoformat()}; the day being logged is {day.isoformat()}.

Target for that day: {goal}.
Logged so far: {_totals(eaten)}.

When the user says what they ate:
1. List each food with an amount. If no amount was given, assume a normal adult portion and say so.
2. Call propose_food ONCE with all items of that meal. Nothing is saved: the user sees a card and confirms.
3. Reply in one or two short sentences: the meal's total, and what is left for the day.

If the user asks what to eat (no food eaten), answer from what is left for the day, without calling the tool.
Rules: kcal must be close to 4 x protein + 4 x carbs + 9 x fat. Round to whole numbers. Use cooked weights when
the user gives them. Don't moralise about food choices. Never mention or estimate body weight.

{REFERENCE}"""


def check_item(i: dict) -> dict:
    try:
        item = {
            "name": str(i["name"]).strip()[:120],
            "quantity": str(i.get("quantity") or "").strip()[:60] or None,
            **{k: round(float(i[k]), 1) for k in ("kcal", "protein_g", "carbs_g", "fat_g")},
        }
    except (KeyError, TypeError, ValueError) as e:
        raise ToolRejected("each item needs name, quantity and numeric kcal, protein_g, carbs_g, fat_g") from e
    if not item["name"]:
        raise ToolRejected("an item has no name")
    if min(item["kcal"], item["protein_g"], item["carbs_g"], item["fat_g"]) < 0:
        raise ToolRejected(f"{item['name']}: values can't be negative")
    if item["kcal"] > 3000 or item["protein_g"] > 250 or item["carbs_g"] > 500 or item["fat_g"] > 250:
        raise ToolRejected(f"{item['name']}: that's more than one sitting; check the portion")
    macros = 4 * (item["protein_g"] + item["carbs_g"]) + 9 * item["fat_g"]
    alcohol = any(w in item["name"].lower() for w in ALCOHOL)
    off = item["kcal"] - macros
    if abs(off) > max(40, 0.25 * macros) and not (alcohol and off > 0):
        raise ToolRejected(
            f"{item['name']}: {item['kcal']:g} kcal doesn't match the macros "
            f"(4×{item['protein_g']:g} + 4×{item['carbs_g']:g} + 9×{item['fat_g']:g} = {macros:.0f}); fix the numbers"
        )
    return item


def chat(s: Settings, messages: list[dict], today: date, day: date, target: dict | None, eaten: dict | None, client=None) -> dict:
    proposals: list[dict[str, Any]] = []

    def propose_food(args: dict) -> str:
        meal = str(args.get("meal", "")).lower()
        if meal not in MEALS:
            raise ToolRejected(f"meal must be one of {', '.join(MEALS)}")
        d = day
        if args.get("day"):
            try:
                d = date.fromisoformat(str(args["day"]))
            except ValueError as e:
                raise ToolRejected("day must be YYYY-MM-DD") from e
            if not today - timedelta(days=60) <= d <= today:
                raise ToolRejected(f"day must be between {today - timedelta(days=60)} and today ({today})")
        items = args.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 30:
            raise ToolRejected("items must be a list of 1 to 30 foods")
        checked = [check_item(i) for i in items]
        total = {k: round(sum(i[k] for i in checked)) for k in ("kcal", "protein_g", "carbs_g", "fat_g")}
        proposals.append({"day": d, "meal": meal, "items": checked, "total": total, "assumptions": args.get("assumptions")})
        return f"Shown to the user as a card to confirm (total {total['kcal']} kcal). Now reply briefly."

    tool = Tool(
        "propose_food",
        "Show the user food-log entries to confirm. Nothing is saved until they click Add.",
        PROPOSE_SCHEMA,
        propose_food,
    )
    res = llm.run(s, system_prompt(s.athlete_name, today, day, target, eaten), messages, [tool], max_rounds=5, client=client)
    return {
        "reply": res.text or ("Here's my estimate." if proposals else "Sorry, I couldn't estimate that."),
        "proposals": proposals,
    }
