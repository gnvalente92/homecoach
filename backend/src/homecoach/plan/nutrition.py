"""Daily nutrition targets from training (pure functions, no database).

The rules follow common sports-nutrition guidance for endurance athletes (e.g. the IOC and ACSM position
statements): carbohydrate is periodised to the day's training load in g per kg of body weight, protein stays
roughly constant, fat fills the rest of the energy with a floor, and fluids scale with time spent training.
These are estimates for healthy adults in training, not medical or dietetic advice.

Energy for a day = resting metabolism (Mifflin-St Jeor) x a daily-life factor from your usual steps
+ the energy of that day's training (what the watch measured on past days, an estimate on planned days).
Walks are left out of training energy because your steps already count them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

Sex = str  # "male" | "female"


@dataclass
class Profile:
    weight_kg: float
    height_cm: float
    birth_date: date
    sex: Sex
    goal: str = "maintain"  # maintain | lose | gain
    protein_g_per_kg: float = 1.8

    def age(self, on: date) -> int:
        b = self.birth_date
        return on.year - b.year - ((on.month, on.day) < (b.month, b.day))


@dataclass
class Workout:
    """One session on a day: done (with the watch's calories) or planned."""

    kind: str  # run | strength | ride | swim | walk | other
    minutes: float = 0
    km: float = 0
    kcal: float | None = None  # measured; None for planned sessions
    hard: bool = False  # key session, tempo, intervals, race pace
    long: bool = False  # the week's long run
    planned: bool = False
    name: str = ""


@dataclass
class RaceDay:
    day: date
    distance_km: float
    priority: str
    name: str


@dataclass
class DayTargets:
    day: date
    load: str  # Rest | Light | Moderate | High | Carb loading | Race
    kcal: int
    carbs_g: int
    protein_g: int
    fat_g: int
    fluid_l: float
    training_kcal: int
    carbs_g_per_kg: float
    reasons: list[str] = field(default_factory=list)
    fuelling: list[str] = field(default_factory=list)


def bmr(p: Profile, on: date) -> float:
    """Resting energy (Mifflin-St Jeor), kcal per day."""
    base = 10 * p.weight_kg + 6.25 * p.height_cm - 5 * p.age(on)
    return base + (5 if p.sex == "male" else -161)


def life_factor(avg_steps: float | None) -> float:
    """Daily life outside training, from your usual steps: 1.2 (desk-bound) to 1.5 (on your feet all day)."""
    if not avg_steps:
        return 1.3
    return round(min(1.5, max(1.2, 1.2 + (avg_steps - 4000) / 40000)), 3)


def workout_kcal(w: Workout, weight_kg: float) -> float:
    if w.kind == "walk":
        return 0.0  # already in the steps-based daily-life factor
    if w.kcal is not None and not w.planned:
        return float(w.kcal)
    if w.kind == "run" and w.km:
        return 1.0 * weight_kg * w.km  # running costs ~1 kcal per kg per km
    met = {"strength": 5.0, "ride": 7.0, "swim": 7.0, "run": 9.0}.get(w.kind, 5.0)
    return met * weight_kg * (w.minutes or 45) / 60


def _minutes(w: Workout) -> float:
    if w.minutes:
        return w.minutes
    if w.kind == "run" and w.km:
        return w.km * 6  # ~6 min/km when no duration is known
    return 45 if w.kind == "strength" else 0


CARB_LOAD = {  # race distance (km) -> (days before, g/kg) for A and B races
    "half": (1, 8.0),
    "marathon": (2, 10.0),
}


def classify(workouts: list[Workout], race: RaceDay | None, loading: RaceDay | None) -> tuple[str, float, list[str]]:
    """Training load category for the day and its carbohydrate target in g/kg."""
    if race:
        g = 8.0 if race.distance_km >= 30 else 7.0 if race.distance_km >= 15 else 6.0
        return "Race", g, [f"Race day: {race.name} ({race.distance_km:g} km)"]
    if loading:
        days, g = CARB_LOAD["marathon" if loading.distance_km >= 30 else "half"]
        return "Carb loading", g, [f"Carb loading for {loading.name}: topping up glycogen"]
    runs = [w for w in workouts if w.kind != "walk"]
    if not runs:
        return "Rest", 3.5, ["Rest day: lower carbs, protein stays the same for recovery"]
    minutes = sum(_minutes(w) for w in runs)
    if any(w.long for w in runs) or minutes >= 90:
        return "High", 7.0, [f"Long or big day (~{minutes:.0f} min): carbs up to refuel"]
    if any(w.hard for w in runs) or minutes >= 60:
        return "Moderate", 6.0, ["Key session: carbs up to train well and recover"]
    if all(w.kind == "strength" for w in runs):
        return "Light", 4.5, ["Strength day: moderate carbs, protein matters most"]
    return "Light", 5.0, ["Easy training: moderate carbs"]


def fuelling(workouts: list[Workout], race: RaceDay | None, weight_kg: float) -> list[str]:
    notes = []
    long_ones = [w for w in workouts if w.kind != "walk" and _minutes(w) >= 75]
    if race and race.distance_km >= 15:
        notes.append("During the race: 60–90 g carbs per hour (gels, drink), starting early")
    elif race:
        notes.append("Before the race: a light, carb-rich meal 2–3 h ahead; no need to fuel during")
    elif long_ones:
        notes.append(f"During the {_minutes(long_ones[0]):.0f}-min session: 30–60 g carbs per hour after the first 45 min")
    if any(w.hard or w.long for w in workouts) or race:
        notes.append(f"Within 2 h after: ~{round(0.3 * weight_kg)} g protein with carbs")
    return notes


def day_targets(
    p: Profile,
    day: date,
    workouts: list[Workout],
    races: list[RaceDay],
    avg_steps: float | None = None,
) -> DayTargets:
    kg = p.weight_kg
    race = next((r for r in races if r.day == day), None)
    loading = None
    if not race:
        for r in races:
            if r.priority in ("A", "B") and r.distance_km >= 15:
                days, _ = CARB_LOAD["marathon" if r.distance_km >= 30 else "half"]
                if r.day - timedelta(days=days) <= day < r.day:
                    loading = r
                    break
    load, carbs_per_kg, reasons = classify(workouts, race, loading)

    rest_kcal = bmr(p, day) * life_factor(avg_steps)
    train_kcal = sum(workout_kcal(w, kg) for w in workouts)
    if race:
        train_kcal += 1.0 * kg * race.distance_km
    kcal = rest_kcal + train_kcal

    hard_day = load in ("Moderate", "High", "Carb loading", "Race")
    if p.goal == "lose":
        if hard_day:
            reasons.append("No deficit on a hard day: fuel the session")
        else:
            kcal -= 400
            reasons.append("Weight loss: 400 kcal less, only on rest and easy days")
    elif p.goal == "gain":
        kcal += 300
        reasons.append("Gaining: 300 kcal extra")
    kcal = max(kcal, bmr(p, day) * 1.2)  # never plan below resting needs plus daily life

    protein_per_kg = max(p.protein_g_per_kg, 2.0) if p.goal == "lose" else p.protein_g_per_kg
    protein = protein_per_kg * kg
    carbs = carbs_per_kg * kg
    fat = (kcal - 4 * (carbs + protein)) / 9
    fat_floor = 0.8 * kg
    if fat < fat_floor:
        fat = fat_floor
        if not hard_day:  # rest and easy days: carbs give way first (never below 3 g/kg) so a deficit holds
            carbs = max(3.0 * kg, (kcal - 4 * protein - 9 * fat) / 4)
            carbs_per_kg = round(carbs / kg, 1)
        kcal = 4 * (carbs + protein) + 9 * fat  # carb-heavy days: keep a minimum of fat and let energy rise
    exercise_h = sum(_minutes(w) for w in workouts if w.kind != "walk") / 60 + (race.distance_km * 6 / 60 if race else 0)
    fluid = 0.035 * kg + 0.6 * exercise_h
    return DayTargets(
        day=day,
        load=load,
        kcal=round(kcal / 10) * 10,
        carbs_g=round(carbs),
        protein_g=round(protein),
        fat_g=round(fat),
        fluid_l=round(fluid, 1),
        training_kcal=round(train_kcal),
        carbs_g_per_kg=carbs_per_kg,
        reasons=reasons,
        fuelling=fuelling(workouts, race, kg),
    )
