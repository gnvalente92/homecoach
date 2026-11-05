# Design notes

What homecoach borrows from established training and recovery apps, and why.

| Idea | Taken from | Where it lives in homecoach |
|---|---|---|
| One question per screen; big primary number, small supporting text; colour only for meaning (green / yellow / red) on a dark background | [WHOOP design breakdown](https://www.925studios.co/blog/whoop-design-breakdown) | Dark-first theme (light toggle in the header); Today answers "how ready am I and what do I do today?" |
| Readiness as a 0–100 score built from named contributors, shown in zones | [Garmin Training Readiness](https://www8.garmin.com/manuals/webhelp/GUID-C144B465-A0C8-4FE9-AFE6-41A3FE3F1D9A/EN-US/GUID-C21BE0C8-A08E-4DA1-B6C6-2E0E2DDDB372.html) | Today: score ring plus bars for training load, overnight HRV, resting HR and sleep |
| Every metric against *your* baseline range | [Oura app redesign](https://ouraring.com/blog/pt/new-oura-app-experience/) | "Your normal" under each contributor; HRV normal band; 28-day resting HR baseline line |
| Fitness / fatigue / form (CTL, ATL, TSB) with form zones | [intervals.icu](https://www.intervals.icu/features/track/), [TrainingPeaks PMC](https://tritrainingharder.squarespace.com/blog/2013/07/understanding-performance-management.html) | Trends → Performance chart; Today → Form tile (with ramp rate) |
| Totals compared with the previous period | intervals.icu totals tables | Overview tiles show ▲/▼ vs the previous period, coloured by whether the change is good |
| "Is my volume enough for this race distance?" | [Runalyze marathon shape](https://blog.runalyze.com/features/new-marathon-shape-for-other-distances) | Today → Endurance shape for the target race |

## Definitions

- **Readiness score**: each contributor is scored 0–100 and then combined with weights (load 30, HRV 30, resting HR 20, sleep 20). Missing contributors are left out and the remaining weights rescaled. Zones: High 80+, Moderate 60–79, Low 40–59, Poor under 40. The plan's volume scaling still uses the simpler flag count (Ready / Hold / Back off), so the plan behaves as before.
- **Fitness / fatigue / form**: daily load is the provider's training load per activity, or an HR-weighted estimate when that's missing. Fitness is the 42-day exponentially weighted average, fatigue the 7-day one, and form is yesterday's fitness minus fatigue. Zones: Transition > 25, Fresh 5…25, Neutral −10…5, Optimal training −30…−10, High risk < −30.
- **Endurance shape**: 67% weight on the average weekly running km over 6 weeks against a target, and 33% on the longest run over 8 weeks against a target. Targets: 5k 30 km/10 km, 10k 40/14, half 50/20, marathon 65/30. This is a heuristic in the spirit of Runalyze's model, not their formula.

None of this is medical or coaching advice. The rules are deliberately simple and transparent.
