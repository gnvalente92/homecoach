# homecoach

Self-hosted training dashboard and plan engine for wearable data. It runs on your own server
(built for TrueNAS SCALE, works on any Docker host), keeps a local copy of all your data, and turns
your race calendar into a week-by-week plan.

- **Today**: readiness from load ratio, overnight HRV and resting HR; today's session and what's next; progress through the week.
- **Training plan**: this week (what you did plus suggestions for the remaining days) and next week, built around your race calendar,
  then a road-to-race-day chart that shows your actual weeks followed by the planned phases.
- **Races**: a form to add or edit races with A/B/C priority and a goal time.
- **Trends** and **Recovery**: distance, training time, pace vs HR, efficiency, training load, steps, sleep stages, HRV, resting HR, stress.
- **Grafana** (optional), pointed at the same Postgres for ad-hoc analysis.

The dashboard reads only from the local database, so pages load instantly. A collector keeps that database current.

## How it works

```
 COROS (or FIT files)  ──►  collector  ──►  Postgres  ◄──  web (FastAPI + UI)  ◄── you
                         every 10 min                 ◄──  Grafana (optional)
                         + nightly full re-sync
```

| Service     | What it does                                                                                      |
|-------------|---------------------------------------------------------------------------------------------------|
| `db`        | Postgres 16. Your data lives here and in the FIT archive.                                         |
| `web`       | API and UI on port 8080. Runs database migrations on start.                                       |
| `collector` | Incremental sync every `HC_SYNC_INTERVAL_MINUTES`, full re-sync nightly, imports `/data/fit-inbox`. |
| `backup`    | Daily gzipped `pg_dump` into your backup dataset, keeping 14 days.                                |
| `grafana`   | Optional, in `docker-compose.grafana.yml`.                                                         |

### Data sources (providers)

Providers live in `backend/src/homecoach/providers/` and map vendor data into one neutral model
(`activities`, `daily_metrics`, `fitness_snapshots`). To add Garmin, Strava or anything else, implement the
`Provider` protocol in `providers/base.py` and register it in `providers/__init__.py`.

- **`coros`**: the COROS Training Hub web API.
  > ⚠️ This API is **unofficial and undocumented**. It's what `training.coros.com` uses, reverse-engineered by
  > community projects, and it can change or break without notice. The client reads fields defensively and stores raw
  > payloads, so a change degrades the data instead of crashing. If it breaks, use the FIT fallback below.
- **FIT inbox** (always on): drop `.fit` files into `/data/fit-inbox` and they're imported and archived. It skips
  duplicates of activities that are already synced.

### Plan engine

`backend/src/homecoach/plan/engine.py` holds pure, tested functions with no database access.

- **Readiness** counts warning flags: load ratio over 1.3 (over 1.5 counts as two), HRV below its normal range, and a
  7-day resting HR at least 3 bpm above the 28-day average. No flags means *Ready* at 100% of planned volume, one flag
  *Hold* at 90%, and two or more *Back off* at 75%.
- **Race plan** targets your nearest A race (or your nearest race if none is marked A). It runs through base, build,
  peak and taper phases. Volume grows about 8% a week up to a peak set by distance, every fourth week is 20% easier, and
  the taper takes 1 to 3 weeks depending on distance.
  - **B races** get a mini-taper week at 80% volume: the race replaces both the key session and the long run, and the
    following week starts easy.
  - **C races** take the place of that week's hard session.
- Paces come from your goal time when one is set, otherwise from the threshold pace (taken from the provider, set with
  `HC_THRESHOLD_PACE`, or estimated from your recent runs).

The rules are simple and transparent. This is not a coach.

## Install on TrueNAS SCALE (24.10 or newer)

The repo is cloned onto the NAS, your settings live in a `.env` file **outside** the repo, and a TrueNAS custom app
*includes* the compose file. Updating then comes down to `git pull` plus restarting the app.

1. **Datasets.** Create `tank/apps/homecoach` (preset **Apps**) with child datasets `postgres`, `data`, `backups`
   and `grafana`. Replace `tank` with your pool name throughout.
2. **Files and permissions** (System → Shell):
   ```sh
   cd /mnt/tank/apps/homecoach
   sudo git clone https://github.com/gnvalente92/homecoach src
   sudo cp src/.env.example .env && sudo chmod 600 .env && sudo nano .env
   sudo chown -R 10001:10001 data      # app user inside the container
   sudo chown -R 472:472 grafana       # grafana user
   ```
3. **App.** Go to **Apps → Discover Apps → ⋮ → Install via YAML**, name it `homecoach`, and paste:
   ```yaml
   include:
     - path:
         - /mnt/tank/apps/homecoach/src/docker-compose.yml
         - /mnt/tank/apps/homecoach/src/docker-compose.grafana.yml   # remove this line if you don't want Grafana
       env_file: /mnt/tank/apps/homecoach/.env
   ```
4. Open `http://<nas>:8742`, and Grafana at `http://<nas>:3742` if you included it. The first sync backfills your history.

**Update:** run `cd /mnt/tank/apps/homecoach/src && sudo git pull && sudo docker pull ghcr.io/gnvalente92/homecoach:latest`,
then **Stop** and **Start** the app.

**Restore a backup:** run `gunzip -c backups/homecoach-YYYY-MM-DD.sql.gz | sudo docker exec -i homecoach-db-1 psql -U homecoach homecoach`.
Run `sudo docker ps` to check the container name.

On any other Docker host: `docker compose --env-file .env -f docker-compose.yml [-f docker-compose.grafana.yml] up -d`.

## Optional: local AI (race assistant and coach)

Both features run fully on your own server, with no API costs, using **Ollama** for the model and **SearXNG**
for web search. They're off until you enable them.

- **Race assistant** (Races page): type something like "add Corrida do Sporting, I want 49 minutes". It searches the web,
  reads the official page and shows a race card. **Nothing is saved until you click Add.**
- **Coach** (Training plan page): the rule engine still builds the plan. The coach reads how training is actually going
  (the last 8 weeks, this week so far, readiness, and your check-ins such as "calf is sore" or "travelling Thu–Sat")
  and suggests day-by-day changes: replace, shorten, skip, or move within the week. Each suggestion has a reason, and you
  **Accept** or **Dismiss** it. Guardrails reject anything that pushes a week more than 10% over the plan, or a run more
  than 15% over your longest recent run; the model is told why and asked to revise. A review runs automatically every
  Sunday at 19:00, and there's also a "Review my week now" button.

**Enable on TrueNAS**

1. Create a dataset `…/homecoach/ollama` (preset Apps) for the model files.
2. In `.env`, set:
   ```ini
   HC_LLM_PROVIDER=openai
   HC_LLM_MODEL=qwen2.5:3b
   HC_OLLAMA_PATH=/mnt/<pool>/<path>/homecoach/ollama
   SEARXNG_SECRET=<a random string>
   ```
3. Add `docker-compose.llm.yml` to the app's `include` list (see `deploy/truenas-app.yaml`), then Stop and Start the app.
   The `ollama-pull` container downloads the model (about 2 GB) once and then exits.
4. Check everything works: `sudo docker exec ix-homecoach-web-1 homecoach llm-check`

**Model choice** (CPU-only works; Ollama can't use Intel iGPUs):

| Model | RAM | Speed on a 6-core desktop CPU | Notes |
|---|---|---|---|
| `qwen2.5:3b` | ~2.5 GB | race lookup ~1–2 min | Default; reliable tool calling |
| `qwen2.5:7b` | ~5.5 GB | ~2× slower | Better judgement for the coach |

Any OpenAI-compatible server works (llama.cpp, LM Studio, vLLM, a remote box with a GPU): point `HC_LLM_BASE_URL` at it.
`HC_LLM_PROVIDER=anthropic` with `HC_LLM_API_KEY` uses the Claude API instead, which is paid.

Small local models make mistakes. That's why every race and plan change needs your confirmation, and why the rules,
not the model, set your training load.

## Configuration

Everything is set through environment variables prefixed `HC_`. See [`.env.example`](.env.example) for the full list.
The most common ones:

| Variable | Default | Notes |
|---|---|---|
| `HC_PROVIDER` | `coros` | `coros` or `none` (FIT inbox only) |
| `HC_COROS_EMAIL` / `HC_COROS_PASSWORD` | – | Stored only in `.env` on your server |
| `HC_COROS_REGION` | `eu` | `eu`, `en` (Americas) or `cn` |
| `HC_SYNC_INTERVAL_MINUTES` | `10` | Incremental sync frequency |
| `HC_THRESHOLD_PACE` | – | Override, e.g. `4:50` |
| `HC_ATHLETE_NAME` | `Athlete` | Used in the greeting and headlines |
| `HC_LLM_PROVIDER` / `HC_LLM_MODEL` | `none` / `qwen2.5:3b` | Local AI, see above |
| `HC_AUTH_USER` / `HC_AUTH_PASSWORD` | – | Optional basic-auth login for the web app. The app is otherwise open to your network, so set this, or put it behind a reverse proxy, if it's reachable beyond your LAN |

## Development

```sh
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
pytest && ruff check .
HC_DATABASE_URL=sqlite:///demo.db python scripts/seed_demo.py     # synthetic data
HC_DATABASE_URL=sqlite:///demo.db HC_PROVIDER=none HC_WEB_DIR=../web/dist uvicorn homecoach.api.main:app --port 8080
cd ../web && npm install && npm run dev                            # UI with hot reload, proxies /api to :8080
```

API docs are served at `/docs`. Secrets are scanned on every push with gitleaks. Run `pre-commit install` to check them
locally as well.

## Privacy

The repository contains no personal data. Your credentials, races, activities and health metrics stay in `.env`, Postgres
and the FIT archive on your own server. The app makes outbound calls only to your configured provider.

## License

See [LICENSE](LICENSE).
