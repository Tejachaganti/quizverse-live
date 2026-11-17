# Phase 16 — Free Deployment Preparation Report

**Status:** prepared locally; not deployed or connected to external accounts.
**Targets:** Vercel Hobby static frontend, Render Free FastAPI/WebSocket service, Supabase Free PostgreSQL.
**Scope:** Python project only. The separate Node.js project is untouched.

## Architecture findings

- The browser client is plain HTML, CSS, and vanilla JavaScript in `quizverse/static`; it does not require a frontend framework or a Node build. The backend currently serves those files from FastAPI, but a copy can also be built as a static site.
- The browser had same-origin API calls and derived WebSocket URLs from `window.location`. All REST calls in `app.js` and `quiz-library.js`, and both `/ws` and `/ws/presence`, now use one API-origin helper. Local development keeps same-origin behavior. The Vercel build reads the public `QUIZVERSE_API_BASE_URL` environment variable and emits a production API origin plus a matching HTTPS/WSS Content Security Policy. It accepts only an HTTPS origin and refuses to overwrite an existing output directory.
- FastAPI runs with `python -m quizverse`; `quizverse.__main__` reads `HOST` and `PORT`. Render must bind to `0.0.0.0` and its assigned `PORT` (normally `10000`). The existing server is a single Uvicorn process; do not add worker processes.
- `/health` is a process/liveness check. `/ready` checks the database and game-history persistence health and is configured for the Render service health check.
- The app has exact-origin CORS and WebSocket origin checks using `ALLOWED_ORIGINS`. Set it to the exact Vercel production origin(s), comma-separated. Do not use `*`. Vercel preview URLs are separate origins; use a separately configured preview build/backend allowlist if previews must call the backend.
- `quizverse.persistence.db` already rejects SQLite when `ENVIRONMENT=production`. A driver-neutral PostgreSQL URL (`postgresql://`) is now normalized to the installed Psycopg 3 driver. Supabase's connection string must retain `sslmode=require`; the session pooler is the recommended connection option for this persistent SQLAlchemy service. Use the Supabase pooler host, region, username, and password exactly as provisioned; URL-encode reserved characters in the password. Never place that URL in Vercel or browser configuration.
- `alembic.ini` has a SQLite local default, but `quizverse/migrations/env.py` loads `DATABASE_URL` through the same production-enforcing environment helper. Production migrations are explicit, not automatic.
- No live PostgreSQL instance was configured or contacted. PostgreSQL compatibility can be checked locally through the existing dialect compilation tests and Alembic offline verification, but that is not a live Supabase test.

## Files inspected and prepared

Reviewed the FastAPI startup and routes, static browser clients, database and Alembic configuration, AI provider configuration, existing production-readiness/PostgreSQL tests, Docker/Cloud Run files, and current deployment/backup documentation.

Prepared or changed:

- `quizverse/static/deployment-config.js` and `quizverse/static/index.html`: shared API and WebSocket origin helper, defaulting to same-origin locally.
- `quizverse/static/app.js` and `quizverse/static/quiz-library.js`: route browser REST and WebSocket traffic through the shared helper.
- `scripts/build_vercel.py` and `vercel.json`: static build, safe API-origin validation, origin-specific CSP, static output and response headers. No framework was added.
- `render.yaml`: single-instance free Python web-service blueprint, `/ready`, production mode, explicit CORS/database settings, low PostgreSQL pool defaults. Secrets remain dashboard-supplied. AI generation is disabled by default.
- `.env.example` and `quizverse/persistence/db.py`: document Supabase's Psycopg-compatible URL shape and choose Psycopg 3 for standard `postgresql://` URLs.
- `quizverse/realtime.py`: an explicitly empty CORS allowlist no longer falls back to wildcard origins.
- `scripts/browser_verification.py` and `scripts/quiz_library_browser_verification.py`: use OS temporary storage rather than the ACL-restricted repository `tmp` path.
- `scripts/verify_postgresql_integration.py`: UTF-8-safe offline migration verification and no longer prints connection URLs or overstates live verification.
- `tests/test_deployment_config.py` and `tests/test_production_readiness.py`: build, origin validation/routing, manifest, overwrite-safety, empty-CORS, and PostgreSQL URL coverage.
- This report.

No database migration, dependency, account, paid service, credential, or deployment was created. No application feature or game-mode behavior was intentionally changed.

## Configuration and manual setup sequence

1. Decide that this use qualifies for Vercel Hobby's **personal/non-commercial** restriction. If the project is used for commercial or financial-gain purposes, Hobby is not an eligible plan; choose an appropriate plan before proceeding.
2. Create the Supabase project manually. Select its PostgreSQL session-pooler URI and convert only the scheme if necessary to `postgresql+psycopg://` (the app also handles the standard `postgresql://` scheme). Include `sslmode=require`. Store it only as Render `DATABASE_URL`.
3. Before serving traffic, run the migration from a trusted local environment using the real URL in a protected shell environment, not a committed file:

   ```powershell
   $env:ENVIRONMENT = 'production'
   $env:DATABASE_URL = 'postgresql://USER:ENCODED_PASSWORD@POOLER_HOST:5432/postgres?sslmode=require'
   .\.venv\Scripts\alembic.exe upgrade head
   .\.venv\Scripts\alembic.exe current
   Remove-Item Env:DATABASE_URL
   Remove-Item Env:ENVIRONMENT
   ```

   Do not use a placeholder URL. Confirm `current` is at `head`; this report did not run against a Supabase database.
4. Create a Render web service from the Python project directory using `render.yaml` (or equivalent dashboard settings). Check `PYTHON_VERSION=3.13.17`, `ENVIRONMENT=production`, `HOST=0.0.0.0`, the service-provided `PORT`, `DATABASE_URL`, and exact `ALLOWED_ORIGINS`. Keep one instance and one process. Use `/ready` as the health check. The start command is `python -m quizverse`.
5. In Vercel set the project root to this Python project, framework preset to Other, and set `QUIZVERSE_API_BASE_URL` to the Render HTTPS origin (for example, the assigned service origin, with no path). The checked-in `vercel.json` runs `python scripts/build_vercel.py` and serves `dist`. That value is public by design; never put a database URL or API key there.
6. Verify `GET /health`, `GET /ready`, static page loads, REST requests, `/ws` and `/ws/presence` from the published frontend. Check Render logs and confirm PostgreSQL is the reported backend. Only then invite test users.
7. AI generation stays disabled unless deliberately enabled. If enabled, configure server-side Render variables `AI_GENERATION_ENABLED=true`, `AI_FREE_TIER_ONLY=true`, `GEMINI_API_KEY=<secret>`, and `GEMINI_MODEL=gemini-3.5-flash-lite` (or another currently verified model on the existing allowlist). Do not place the key in Vercel. Confirm current limits and data terms before sending user-supplied text or documents.

For a Free Supabase project, configure and periodically test an off-site logical export (for example, Supabase CLI `db dump`, or `pg_dump` from a trusted machine). Keep exports outside both Render's ephemeral filesystem and the database project. Free projects do not include downloadable automatic backups or PITR according to Supabase's current production guidance.

## Runtime limitations and expected cost

- **Vercel Hobby:** $0 within plan limits; terms limit it to personal, non-commercial use. Limits and service availability can change. This plan must not be treated as an acceptable commercial host. [Hobby plan](https://vercel.com/docs/plans/hobby) · [Terms](https://vercel.com/legal/terms).
- **Render Free:** $0 within current included quotas. Free services spin down after 15 minutes with no inbound HTTP request or WebSocket message; the next request/connection can take about a minute to warm. There are 750 free instance hours per workspace per month, shared among free services; free bandwidth/build allowances also apply. Free instances can restart and have ephemeral disk. They cannot scale beyond one instance. Keep database state on Supabase; do not rely on local uploads/files. [Free services](https://render.com/docs/free).
- **Render WebSockets:** inbound WebSockets are supported, with no fixed maximum connection duration. Connections still end during restarts, deploys, maintenance, network failures, or service replacement. The load balancer can send a reconnect to a different instance, which is a reason to keep this service at one instance. The current frontend reconnects, but the live room state is local to the old process. [WebSocket docs](https://render.com/docs/websocket).
- **Supabase Free:** $0 within the current quota; 500 MB database per project and 5 GB egress on the current pricing page. Free projects can be paused after roughly seven days of low database activity. Free does not include downloadable automatic backups or PITR; maintain tested off-site exports. [Pricing](https://supabase.com/pricing) · [Pausing](https://supabase.com/docs/guides/platform/free-project-pausing) · [Backups](https://supabase.com/docs/guides/platform/backups).
- **AI:** Gemini API use is optional and disabled by default. Google currently lists the existing default `gemini-3.5-flash-lite` as stable and with free-tier token pricing. Free tier has model-specific rate limits and Google states free-tier submitted content may be used to improve products; availability, quotas, pricing, and terms can change. Paid tiers can incur usage-based charges and have different data terms. This project does not configure billing or switch providers. [Model](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite) · [Pricing](https://ai.google.dev/gemini-api/docs/pricing) · [Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits).
- **Multiplayer:** active rooms, scores, answer state, question sequences, presence sockets and process-local admission/rate-limit state are not persisted/shared across instances. Restart, deploy, sleep, or instance loss aborts active matches and may invalidate reconnect capability. Persistent profiles, quiz-library data and other modeled records stored in PostgreSQL survive a service restart, but the database does not make live matches durable. The nine game modes remain supported, but this stack is suitable only for a low-stakes preview/demo with those interruptions accepted.
- **Expected infrastructure spend:** $0/month only while all three services remain within their current free allowances and eligible use. AI remains $0 only while its current free quota and terms apply. Custom domains, paid upgrades, overages or billing-enabled usage can add cost. No payment method or billing configuration has been added.

## Verification and readiness

Local results:

- Complete Python suite: **472 passed, 1 Starlette/httpx deprecation warning** (`320.99s`).
- Deployment/database focused tests: **22 passed, 1 warning**.
- Nine-mode browser verification: **passed** for Classic, Speed Challenge, Team Battle, Elimination, Solo Practice, Quiz Royale, Power Play, Bluff Master, and Escape Room. It exercised reconnect, rematch and responsive layouts where applicable.
- Quiz Library browser verification: **passed**, including owner access, editing, publishing, private/public visibility, AI generation review, keyboard/responsive behavior and navigation during a live room.
- `pip check`: **passed**, no broken requirements.
- JavaScript syntax (`deployment-config.js`, `app.js`, `quiz-library.js`), Python `compileall`, and `git diff --check`: **passed**.
- `vercel.json` JSON and `render.yaml` YAML parsing: **passed**.
- PostgreSQL verification: Psycopg 3 available; all **59 model tables** compile to PostgreSQL DDL; all **13 Alembic revisions** compile offline; SQLite production fail-fast and pool configuration checks pass. **No live PostgreSQL/Supabase connection was available.**
- A high-confidence scan found no AWS access-key, private-key-block, or Google API-key patterns in the project. An ignored local `quizverse.db` remains untracked and was not included.

These are local configuration/test results only. This work does not verify a public Vercel site, Render service, Supabase database, production migration, real cross-origin browser session, or live WebSocket handshake.

**Next-task readiness:** the repository is prepared for a controlled first deployment task after an owner manually creates eligible accounts/projects, provisions Supabase, applies Alembic migrations, fills the secret and exact-origin environment values, and accepts the free-tier restart/sleep limitations. It is not a verified live deployment and should not be presented as production-grade persistent multiplayer hosting.
