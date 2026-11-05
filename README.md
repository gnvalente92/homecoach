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
| `grafana`   | Optional, started with the `grafana` profile.                                                     |

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

## Install on TrueNAS SCALE

1. **Create datasets** (adjust the pool name), for example:
   `tank/apps/homecoach/{postgres,data,backups}`.
   Postgres runs as uid 999 in its container and the app as uid 10001, so give those users write access to their datasets,
   or set ACLs.
2. **Get the files** onto the NAS:
   ```sh
   cd /mnt/tank/apps/homecoach && git clone https://github.com/gnvalente92/homecoach src && cd src
   cp .env.example .env && chmod 600 .env && nano .env
   ```
   Fill in your COROS login, the passwords and the dataset paths. **`.env` never goes into git.**
3. **Start it**:
   - Shell: `docker compose --env-file .env up -d`. Add `--profile grafana` for Grafana.
   - Or in the UI: **Apps → Discover Apps → ⋮ → Install via YAML**. Paste `docker-compose.yml` with your values filled
     in, or point it at the cloned file.
4. Open `http://<nas>:8080`. The first sync backfills `HC_BACKFILL_DAYS` of history, which can take a few minutes.
   Grafana, if enabled, is at `http://<nas>:3000`.

Updating: `docker compose pull && docker compose up -d`. Images are built for amd64 and arm64 on every push to `master`
and published at `ghcr.io/gnvalente92/homecoach`.

Restoring a backup: `gunzip -c backups/homecoach-YYYY-MM-DD.sql.gz | docker compose exec -T db psql -U homecoach homecoach`.

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
