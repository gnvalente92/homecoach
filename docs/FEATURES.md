# Feature choices

Reviewed against official product descriptions on 5 November 2026. This comparison informed Homecoach's local features;
it does not imply compatibility with another service's algorithms or APIs.

| Reference | Relevant capability | Homecoach decision |
|---|---|---|
| [Strava subscription](https://support.strava.com/en-us/articles/15402044-what-features-are-included-in-a-strava-subscription) | Custom goals, training log, cumulative monthly statistics, fitness/fatigue trends | Added recurring goals and searchable history with monthly comparisons. Existing fitness/fatigue and planning remain the source of training guidance. |
| [Strava goals](https://support.strava.com/en-us/articles/15401694-goals-on-the-strava-app) | Weekly, monthly and annual distance, time and activity-count targets | Goals use recorded activities in the current calendar period. Homecoach supports distance, hours and sessions, by sport or all sports. Earlier activities in the period count too. |
| [Intervals.icu](https://www.intervals.icu/) | Multisport analysis, fitness charts, training calendar and structured workouts | Keep the provider-neutral activity model and sport filters. Reuse existing planning and fitness charts. No extra integration is required for local analysis. |
| [TrainingPeaks strength builder](https://help.trainingpeaks.com/hc/en-us/articles/21397126893581-Using-the-Strength-Workout-Builder) | Strength workouts as a distinct part of a training calendar | The rule plan includes a weekly strength session alongside running. Check-ins shorten, replace or drop it. This is not a full sets/reps/load editor or device workout export. |

## Data and behaviour

Goals are independent of race goals and plan volume. Weekly goals start on Monday; monthly and yearly goals use calendar
boundaries in `HC_TIMEZONE`. Each goal renews at the next boundary. A period/sport/metric combination has one target.
Distance uses kilometres, duration uses hours and session counts use whole numbers. Missing distance or duration does not
become a fabricated value; the goal displays how many activities lack the selected measure.

The log paginates in descending activity-start order, with the activity ID as a stable tie-breaker. It uses literal,
case-insensitive name/sport search and inclusive date filters. Future-dated activities do not count. Monthly comparisons
show elapsed days for the current month against the equivalent days last year, including leap-year boundaries. Empty
months mean no recorded activity; they do not prove that no training occurred.

## Rules instead of a model

Homecoach used a local model (Ollama) for a coach chat, a race assistant, race-outlook text and a food chat. It was too
slow and unreliable on a CPU-only NAS, so each was replaced on 16 November 2026:

- **Coach chat and Sunday review** → a structured check-in (good, tired, sore, ill, travelling) with fixed rules in
  `engine.apply_checkin`. The README lists the rules. The plan page lists the next 7 days as a to-do list and the last
  7 days as done, without weekdays, so sessions can be fitted around the week.
- **Race outlook text** → `outlook.summary`, which writes the reading and up to three focus points from the computed
  facts.
- **Race assistant and food chat** → a Claude hand-off (`paste.py`). The app writes a prompt with what Claude needs
  (today's date, the race calendar, or the day's nutrition target and what's logged). You run it in Claude and paste the
  JSON answer back. The app validates it the same way the old tools did and shows cards to confirm.

## Nutrition

Targets are rule-based and follow common sports-nutrition guidance for endurance athletes (IOC and ACSM position
statements). They are estimates for healthy adults in training, not medical or dietetic advice.

- **Energy**: resting metabolism (Mifflin-St Jeor) × a daily-life factor from your usual steps (1.2–1.5, last 28 days)
  + that day's training. Past days and today use the watch's calories. Planned runs are estimated at ~1 kcal per kg per km,
  and other sessions use MET values. Walks are left out of training energy because the steps already count them.
- **Carbohydrate** (g per kg of body weight): rest 3.5 · easy or strength 4.5–5 · key session or 60+ min 6 · long run or
  90+ min 7 · race day 6–8 by distance. A and B races of 15 km or more get carb loading: 8 g/kg the day before a half,
  10 g/kg for the two days before a marathon. Long sessions and races get fuelling notes (g of carbs per hour).
- **Protein**: 1.8 g/kg every day by default, adjustable from 1.2 to 2.4, and 2.0 g/kg minimum while losing weight.
- **Fat**: the rest of the energy, never below 0.8 g/kg. On carb-heavy days energy rises rather than fat dropping.
- **Fluids**: 35 ml/kg plus about 0.6 L per hour of training.
- **Weight goals**: "lose" takes 400 kcal off rest and easy days only, never off key, long, race or carb-loading days. It
  never plans below resting metabolism × 1.2; carbs give way (down to 3 g/kg) before fat goes under its floor. "Gain" adds
  300 kcal a day.

The profile (weight, height, birth date, sex) comes from COROS's user info on each sync while "keep in sync with my watch"
is on. Editing it by hand keeps your values. Food estimates come from Claude via the copy-and-paste prompt. Energy must
match 4/4/9 kcal per gram of protein/carbs/fat within 25% (alcohol excepted); otherwise the app rejects the answer and
says which item to ask Claude to fix. Nothing is logged until you click Add, and entries can be removed or added by hand.

## Deferred features

Matched routes, segment rankings, heatmaps, grade-adjusted pace and precise in-run best efforts need GPS or sample data,
plus separate matching/analysis work. Activity totals alone cannot establish them. They are not implemented by guessing
from total distance and time. Weather, social features and extra provider integrations also need explicit dependencies
and a separate implementation scope.

## API and schema

Interactive API documentation remains at `/docs`. New endpoints are `GET/POST /api/goals`, `PUT/DELETE /api/goals/{id}`,
`GET /api/training/log` and `GET /api/training/monthly`. Goals live in `training_goals` (migration 0005). Nutrition adds `GET /api/nutrition`,
`PUT /api/nutrition/profile`, `GET/POST /api/nutrition/food`, `DELETE /api/nutrition/food/{id}`,
`GET /api/nutrition/prompt` and `POST /api/nutrition/import`, with the `nutrition_profile` and `food_entries` tables
(migration 0006). Races add `GET /api/races/prompt` and `POST /api/races/import`; check-ins use
`GET/POST /api/checkins` and `DELETE /api/checkins/{id}`. `GET /api/plan` returns `next_7_days` and `last_7_days`.
Migration 0007 adds `checkins.feeling` and `checkins.until` and drops `coach_suggestions`, `coach_notes` and
`race_insights`; downgrading recreates them empty. Web startup runs migrations as before. Back up the database before upgrading and retain a compatible image tag for recovery.
