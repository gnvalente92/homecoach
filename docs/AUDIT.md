# Homecoach review — 5 November 2026

Homecoach is an active, personal training application: a FastAPI service and collector, a Vite frontend, Postgres and
Docker Compose deployment. Its operator is the owner of the host. The existing provider-neutral model, pure planning
engine and explicit acceptance of AI suggestions are useful foundations; no architecture rewrite was needed.

The review used the workbench's project-framing, quality-audit, quality, service, frontend, infrastructure, architecture,
delivery, operations, documentation and writing skills. Local repository assessment: **4/5** after remediation. This is
not a sign-off of the running NAS installation or a validation of training advice.

## Confirmed defects and changes

| Severity | Finding | Implemented change and evidence |
|---|---|---|
| High | The SPA fallback served files outside the web directory through encoded traversal. | Resolve and check path containment, including symlinks. TestClient regression requests return 404. |
| High | A failed `pg_dump` could publish an empty archive because the pipeline returned gzip's success status; retention still ran. | Dump and compress in checked steps, publish atomically and rotate only on success. Shell tests inject dump/compression failures and verify old archives survive. |
| High | The race page tool could fetch private network addresses, follow unchecked redirects and read an unbounded body. | Validate public addresses on every hop, pin the connection's address while preserving TLS/Host identity, disable environment proxies and limit downloaded content. Mock HTTP/DNS tests cover private hosts, redirects and oversized pages. |
| High | An old Vite version had npm advisories; the build used Node 20, which is EOL. | Update Vite and use Node 24 LTS in CI/Docker. A fresh npm install and production build pass; npm audit reports no remaining advisories in the frontend dependency tree. [Node release status](https://nodejs.org/en/about/previous-releases), [Vite requirements](https://vite.dev/guide/). |
| Medium | Separate coach proposals could each pass validation but exceed the combined weekly limit when accepted. Accepted proposals were also applied after their assumptions became stale. | Recheck on acceptance, serialise Postgres decisions and revalidate while calculating the current plan. Inactive changes are explained in the UI. Regression tests cover combined proposals, invalid distances and completed workouts. |
| Medium | A failed sync rolled back rotated COROS tokens together with activity writes. Concurrent sync jobs could overlap. | Preserve refreshed tokens after activity rollback, without restoring a disconnected account. Use a shared file lock for sync. Tests cover transaction failure, disconnect and competing processes. |
| Medium | Missing or old health metrics produced “Ready” and a normal-range explanation. | Use current signals only and show Unknown when none are usable. State missing signals explicitly. Tests cover missing and stale metrics. |
| Medium | Partial auth configuration silently left the service unauthenticated, health ignored the database and sync failures were not visible in the header. | Reject partial credentials, check DB connectivity and expose non-secret sync status. Add operational instructions. |
| Medium | Python dependencies were unpinned and npm installation could silently bypass its lockfile. | Add Python constraints to local/CI/image installation and require npm ci. CI now tests feature-branch pushes; image publication remains restricted to main/master. |

## Requested features

Full resync now syncs data, calculates the plan, reviews coach recommendations and refreshes AI race outlooks. It reports
each stage separately. Successful reviews replace pending suggestions; failed reviews retain them. Accepted decisions,
check-ins and review history remain. Tests exercise successful, failed and AI-disabled runs and transactional replacement.

The coach can propose strength sessions with duration, exercises and a reason. Acceptance adds strength alongside a run,
without changing running kilometres. Duration, weekly count, completed sessions and race-day additions have explicit
guards. Tests exercise the model tool call through acceptance and plan output.

Recurring goals and searchable history with monthly comparisons were added after reviewing comparable training apps.
See [Feature choices](FEATURES.md) for sources, boundaries and the API. Both features use local recorded data and work
without an AI model or a Strava subscription.

## Verification and remaining limits

The backend suite grew from 46 to 92 passing tests. Ruff, formatting, shell syntax, Compose configuration and frontend
production build checks pass. A non-editable package installs and runs migrations 0001–0005 outside the checkout. Browser
checks with synthetic data covered goal creation/editing, history/search and desktop/phone layouts without console errors.

Docker's daemon was unavailable locally. A live Postgres migration/restore, the new Postgres advisory lock, container image
builds, real COROS access, model behaviour and TrueNAS deployment remain unverified here. The CI image build covers Linux
amd64/arm64 after merging to a publishing branch; that is separate from the local checks above. The training heuristics
and model's exercise choices have not received clinical validation. A Starlette TestClient deprecation warning remains;
it does not fail the tests.

Before updating the NAS, keep a database and `/data` backup. Verify health, an actual provider sync, a full resync with the
configured model, and acceptance of a strength recommendation. Periodically restore a backup into an isolated database.
The commands and recovery boundaries are in [Operations](OPERATIONS.md).
