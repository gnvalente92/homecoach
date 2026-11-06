# Feature choices

Reviewed against official product descriptions on 5 November 2026. This comparison informed Homecoach's local features;
it does not imply compatibility with another service's algorithms or APIs.

| Reference | Relevant capability | Homecoach decision |
|---|---|---|
| [Strava subscription](https://support.strava.com/en-us/articles/15402044-what-features-are-included-in-a-strava-subscription) | Custom goals, training log, cumulative monthly statistics, fitness/fatigue trends | Added recurring goals and searchable history with monthly comparisons. Existing fitness/fatigue and planning remain the source of training guidance. |
| [Strava goals](https://support.strava.com/en-us/articles/15401694-goals-on-the-strava-app) | Weekly, monthly and annual distance, time and activity-count targets | Goals use recorded activities in the current calendar period. Homecoach supports distance, hours and sessions, by sport or all sports. Earlier activities in the period count too. |
| [Intervals.icu](https://www.intervals.icu/) | Multisport analysis, fitness charts, training calendar and structured workouts | Keep the provider-neutral activity model and sport filters. Reuse existing planning and fitness charts. No extra integration is required for local analysis. |
| [TrainingPeaks strength builder](https://help.trainingpeaks.com/hc/en-us/articles/21397126893581-Using-the-Strength-Workout-Builder) | Strength workouts as a distinct part of a training calendar | Added coach proposals with strength duration and exercise descriptions. They coexist with running sessions and require acceptance. This is not a full sets/reps/load editor or device workout export. |

## Data and behaviour

Goals are independent of race goals and plan volume. Weekly goals start on Monday; monthly and yearly goals use calendar
boundaries in `HC_TIMEZONE`. Each goal renews at the next boundary. A period/sport/metric combination has one target.
Distance uses kilometres, duration uses hours and session counts use whole numbers. Missing distance or duration does not
become a fabricated value; the goal displays how many activities lack the selected measure.

The log paginates in descending activity-start order, with the activity ID as a stable tie-breaker. It uses literal,
case-insensitive name/sport search and inclusive date filters. Future-dated activities do not count. Monthly comparisons
show elapsed days for the current month against the equivalent days last year, including leap-year boundaries. Empty
months mean no recorded activity; they do not prove that no training occurred.

Strength recommendations use the same propose/accept flow as running changes. The `strength` action accepts a duration
of 1–60 minutes, an exercise description and a reason; zero minutes removes a planned strength session. It leaves running
sessions intact and is limited to two planned/recorded strength sessions per week. Completed strength sessions cannot be
edited through suggestions and race-day additions are rejected. These are product constraints, not a validation of the
exercise technique, resistance or its suitability for an individual.

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
is on. Editing it by hand keeps your values. The food chat proposes entries for a meal with estimated macros; energy must
match 4/4/9 kcal per gram of protein/carbs/fat within 25% (alcohol excepted), otherwise the model is asked to fix the
numbers. Nothing is logged until you click Add, and entries can be removed or added by hand.

## Deferred features

Matched routes, segment rankings, heatmaps, grade-adjusted pace and precise in-run best efforts need GPS or sample data,
plus separate matching/analysis work. Activity totals alone cannot establish them. They are not implemented by guessing
from total distance and time. Weather, social features and extra provider integrations also need explicit dependencies
and a separate implementation scope.

## API and schema

Interactive API documentation remains at `/docs`. New endpoints are `GET/POST /api/goals`, `PUT/DELETE /api/goals/{id}`,
`GET /api/training/log` and `GET /api/training/monthly`. Goals live in `training_goals`; coach strength durations live in
`coach_suggestions.duration_min`. Migrations 0004 and 0005 add these assets without replacing existing data. Nutrition adds `GET /api/nutrition`,
`PUT /api/nutrition/profile`, `GET/POST /api/nutrition/food`, `DELETE /api/nutrition/food/{id}` and
`POST /api/nutrition/chat`, with the `nutrition_profile` and `food_entries` tables (migration 0006). Web startup
runs migrations as before. Back up the database before upgrading and retain a compatible image tag for recovery.
