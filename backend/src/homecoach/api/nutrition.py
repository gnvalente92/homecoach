"""Nutrition API: daily targets from training, the body profile and the food log."""

from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from homecoach import nutrition
from homecoach.config import get_settings
from homecoach.db import get_session
from homecoach.models import FoodEntry, NutritionProfile

router = APIRouter(prefix="/api/nutrition")
Db = Depends(get_session)
Meal = Literal["breakfast", "lunch", "dinner", "snack"]


def today() -> date:
    return datetime.now(ZoneInfo(get_settings().timezone)).date()


@router.get("")
def overview(db: Session = Db) -> dict:
    """Profile plus targets for this week and next week (Monday to Sunday), with what was logged."""
    from homecoach.api.main import compute_plan, jsonable
    from homecoach.plan import engine as E

    t = today()
    start = E.monday(t)
    end = start + timedelta(days=13)
    row = nutrition.get_profile(db)
    days = nutrition.week_targets(db, compute_plan(db), start, end, t) if not nutrition.missing(row) else []
    return jsonable({"today": t, "profile": nutrition.profile_out(row), "days": days})


class ProfileBody(BaseModel):
    weight_kg: float = Field(ge=25, le=300)
    height_cm: float = Field(ge=100, le=250)
    birth_date: date
    sex: Literal["male", "female"]
    goal: Literal["maintain", "lose", "gain"] = "maintain"
    protein_g_per_kg: float = Field(1.8, ge=1.2, le=2.4)
    follow_provider: bool = True

    @field_validator("birth_date")
    @classmethod
    def plausible_age(cls, v: date) -> date:
        age = (today() - v).days / 365.25
        if not 14 <= age <= 100:
            raise ValueError("birth date must give an age between 14 and 100")
        return v


@router.put("/profile")
def save_profile(body: ProfileBody, db: Session = Db) -> dict:
    row = nutrition.get_profile(db) or NutritionProfile(id=1)
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    row.source = "manual"
    db.add(row)
    db.flush()
    return nutrition.profile_out(row)


class FoodItem(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    quantity: str | None = Field(None, max_length=60)
    kcal: float = Field(ge=0, le=5000)
    protein_g: float = Field(ge=0, le=500)
    carbs_g: float = Field(ge=0, le=1000)
    fat_g: float = Field(ge=0, le=500)


class FoodBody(BaseModel):
    day: date
    meal: Meal
    items: list[FoodItem] = Field(min_length=1, max_length=30)
    source: Literal["ai", "manual"] = "manual"


def food_out(x: FoodEntry) -> dict:
    return {
        "id": x.id,
        "day": x.day,
        "meal": x.meal,
        "name": x.name,
        "quantity": x.quantity,
        "kcal": round(x.kcal),
        "protein_g": round(x.protein_g, 1),
        "carbs_g": round(x.carbs_g, 1),
        "fat_g": round(x.fat_g, 1),
        "source": x.source,
    }


@router.get("/food")
def food(day: date | None = None, db: Session = Db) -> list[dict]:
    d = day or today()
    rows = db.scalars(select(FoodEntry).where(FoodEntry.day == d).order_by(FoodEntry.created_at, FoodEntry.id))
    return [food_out(x) for x in rows]


@router.post("/food", status_code=201)
def add_food(body: FoodBody, db: Session = Db) -> list[dict]:
    t = today()
    if not t - timedelta(days=60) <= body.day <= t:
        raise HTTPException(422, "food can be logged for today and the last 60 days")
    rows = [FoodEntry(day=body.day, meal=body.meal, source=body.source, **i.model_dump()) for i in body.items]
    db.add_all(rows)
    db.flush()
    return [food_out(x) for x in rows]


@router.delete("/food/{fid}", status_code=204)
def delete_food(fid: int, db: Session = Db) -> None:
    x = db.get(FoodEntry, fid)
    if not x:
        raise HTTPException(404, "entry not found")
    db.delete(x)


class ChatBody(BaseModel):
    messages: list[dict] = Field(min_length=1, max_length=20)
    day: date | None = None


@router.post("/chat")
def chat(body: ChatBody, db: Session = Db) -> dict:
    """The food chat: the local AI estimates what was eaten and proposes entries; nothing is saved here."""
    from homecoach import food_assistant, llm

    s = get_settings()
    if not llm.enabled(s):
        raise HTTPException(409, "the local AI is off (set HC_LLM_PROVIDER); add food with the form instead")
    t = today()
    d = body.day or t
    msgs = [
        {"role": m.get("role"), "content": str(m.get("content", ""))[:2000]}
        for m in body.messages
        if m.get("role") in ("user", "assistant")
    ]
    if not msgs or msgs[-1]["role"] != "user":
        raise HTTPException(422, "the last message must be from the user")
    target = next((x for x in overview(db)["days"] if x["day"] == d), None)
    try:
        return food_assistant.chat(s, msgs, t, d, target, nutrition.eaten(db, d, d).get(d))
    except llm.LLMError as e:
        raise HTTPException(502, str(e)) from e
