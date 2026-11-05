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

## Deferred features

Matched routes, segment rankings, heatmaps, grade-adjusted pace and precise in-run best efforts need GPS or sample data,
plus separate matching/analysis work. Activity totals alone cannot establish them. They are not implemented by guessing
from total distance and time. Weather, social features and extra provider integrations also need explicit dependencies
and a separate implementation scope.

## API and schema

Interactive API documentation remains at `/docs`. New endpoints are `GET/POST /api/goals`, `PUT/DELETE /api/goals/{id}`,
`GET /api/training/log` and `GET /api/training/monthly`. Goals live in `training_goals`; coach strength durations live in
`coach_suggestions.duration_min`. Migrations 0004 and 0005 add these assets without replacing existing data. Web startup
runs migrations as before. Back up the database before upgrading and retain a compatible image tag for recovery.
