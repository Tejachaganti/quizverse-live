# QuizVerse Live

**A real-time trivia arena for your crew.** QuizVerse Live brings 2–8 people together with a six-character room code, guest nicknames, ten quick-fire questions, and a live leaderboard. The single-process live game needs no accounts or Redis. The persistent quiz-library foundation now includes a validated HTTP API; the authoring UI is a later stage.

The project is intentionally Python-first: FastAPI serves the web client and its native WebSocket endpoint, Pydantic validates client messages, and a separately testable asynchronous game engine owns rooms, questions, timers, answers, scoring, and lifecycle transitions. SQLAlchemy and Alembic provide durable quiz-library schema and persistence primitives; the live engine remains in memory and is not wired to that database.

## Play format

- A host creates an arena; guests join with its six-character code and a unique nickname.
- The host starts when at least two players are connected.
- Solo Practice is available from the format selector and starts with one real player; no room code or opponent is needed.
- Each match contains ten unique general-knowledge questions, each with four options.
- A three-second launch countdown precedes each 15-second question. Correct answers are shown only after reveal.
- Correct answers earn **1,000 points** plus a server-calculated speed bonus: `round(500 × (15 − elapsed seconds) ÷ 15)`, clamped to **0–500**.
- A correct answer also earns the separately published **Momentum Shift** bonus when eligible (see below).
- Each answer is accepted once. A room reaches results after round ten and its host can start a rematch or return the players to the lobby.

### Momentum Shift

At the beginning of a question, a player qualifies when their score is **1–1,500 points below the current leader**. A qualified player earns **+250 points** for a correct answer submitted within the first **5 seconds** of that question. The leader and tied players are not eligible while they are not trailing. The rules are published in the lobby and in the question view; the server determines eligibility from its score state and records each award in the final results. Momentum Shift never subtracts points or changes the ordinary speed bonus.

### Speed Challenge and Team Battle

Speed Challenge uses the same individual scoring formula over a 10-second question timer and disables Momentum Shift. Team Battle requires an even roster of 4, 6, or 8 real room members and at least four connected players to start. The server assigns Team Violet and Team Cyan alternately by join order and freezes those assignments for the match. Team Battle uses the Classic 15-second timer and individual scoring, disables Momentum Shift, and derives each team total from its members' scores.

Elimination supports 2–8 real players, uses ten 15-second questions, and disables Momentum Shift. A wrong or unanswered response eliminates a player at the reveal; a disconnected player remains eligible to reconnect until the current question deadline. Eliminated players keep their room seat and can watch the finish, but cannot submit answers in later rounds. The match ends after the reveal when one survivor remains, all remaining players are eliminated together, or question ten is complete. A single last survivor wins; a same-round all-out cohort shares the best remaining placement and is reported as a draw. If multiple players survive question ten, rank them by score, correct answers, then lower correct-response time; exact ties share placement. Earlier eliminations rank behind later eliminations, with score, correct answers, and correct-response time breaking same-round ties. The server owns all elimination decisions and placements.

Team standings use total score, then aggregate correct answers, then the lower sum of server-recorded correct-response milliseconds. If all three values tie, the teams share first place; the result state explicitly identifies every shared winner. Individual leaderboard ordering remains unchanged.

Solo Practice is a one-player, ten-question format using the Classic 15-second timer, 1,000-point correct base, up to 500 speed points, and no Momentum Shift. It uses the same server-owned room, answer, deadline, reveal, reconnect, and replay flow; room capacity is one, and the interface does not display a join code, opponent, or leaderboard. Practice results report score, correct, incorrect, unanswered, and accuracy. Accuracy is correct answers divided by attempted answers; unanswered questions are excluded, and no attempts display as an em dash.

## Start locally

Requires Python 3.13 or later. A virtual environment keeps Python packages isolated from the original Node project.

### Windows PowerShell

```powershell
cd quizverse-live-python
py -3.13 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e ".[dev,browser]"
Copy-Item .env.example .env
& .\.venv\Scripts\python.exe -m quizverse
```

Open `http://127.0.0.1:8000`. For browser verification, install Microsoft Edge or run `python -m playwright install chromium`, then use the project’s Python executable:

```powershell
& .\.venv\Scripts\python.exe scripts\browser_verification.py
```

### macOS / Linux

```sh
cd quizverse-live-python
python3.13 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,browser]'
cp .env.example .env
.venv/bin/python -m quizverse
```

Open `http://127.0.0.1:8000`. The browser verification script uses Microsoft Edge when it finds the standard Windows installation; otherwise it launches Playwright Chromium.

The server reads `HOST`, `PORT`, and `ALLOWED_ORIGINS` from `.env`. For play from another device on the same network, set `HOST=0.0.0.0` and add the host machine’s exact origin (for example `http://192.168.1.20:8000`) to `ALLOWED_ORIGINS`. Do not use `*` for production.

## Persistence foundation (Stage 2)

The quiz persistence layer is separate from active rooms. Merely importing or starting the game does not create or alter a database. Schema changes are applied only with an explicit Alembic command. SQLite is the local default; PostgreSQL is supported by setting `DATABASE_URL` and installing the optional driver extra.

After installing the project, copy `.env.example` to `.env`. From `quizverse-live-python`:

```powershell
& .\.venv\Scripts\alembic.exe upgrade head
& .\.venv\Scripts\quizverse-seed-builtins.exe
```

The equivalent cross-platform commands are `alembic upgrade head` and `quizverse-seed-builtins`. Seeding is optional and idempotent. Built-in questions receive content-derived stable IDs and `built_in_reviewed` provenance. If the reviewed bank changes, create a new versioned bank identity instead of silently rewriting the seeded bank.

Inspect the applied revision with `alembic current`; create a future migration with `alembic revision --autogenerate -m "describe schema change"` and review its generated operations before applying it. `alembic downgrade -1` reverts one revision and can remove persisted data, so back up the database first. Migrations are never run automatically by the app.

For a PostgreSQL database, configure a driver URL such as `postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE` in the server environment and install `pip install -e '.[postgres]'`. Do not place real credentials in `.env.example` or source control. Alembic must be run explicitly against the configured database before using persistence features. The current game process does not connect active rooms to PostgreSQL.

## Architecture

```mermaid
flowchart LR
    A[Desktop browser] <-->|HTTPS and WebSocket| B[FastAPI app]
    C[Mobile browser] <-->|HTTPS and WebSocket| B
    B --> D[Strict Pydantic event contracts]
    D --> E[Connection hub and room authorization]
    E --> F[Async GameEngine]
    F --> G[In-memory room and session state]
    F --> H[Monotonic deadline scheduler]
    B --> I[Static HTML, CSS, JavaScript]
    F --> J[Question bank]
```

### Module map

| Path | Responsibility |
| --- | --- |
| `quizverse/app.py` | ASGI application entry point |
| `quizverse/realtime.py` | WebSocket transport, origin checks, event rate limits, connection routing, security headers, scheduler |
| `quizverse/protocol.py` | Strict Pydantic models for every client event |
| `quizverse/engine.py` | Atomic room transitions, memberships, monotonic deadlines, authoritative validation and scoring |
| `quizverse/questions.py` | Immutable multiple-choice question bank |
| `quizverse/quiz_api.py` | Strict quiz-library HTTP request/response contracts, owner/share authorization routing, and safe API error handling |
| `quizverse/persistence/` | SQLAlchemy models, database configuration, atomic quiz repository, capability grants, and explicit built-in seeding |
| `quizverse/migrations/` | Alembic migration environment and versioned schema changes; never run automatically at app startup |
| `quizverse/static/` | Packaged responsive interface served by FastAPI |
| `tests/` | Deterministic game tests, protocol tests, and real two-client WebSocket integration tests |
| `scripts/browser_verification.py` | Two independent Edge/Chromium browser contexts and a full match verification |

The async engine lock serializes every game mutation and deadline transition inside one event loop. The scheduler checks deadlines independently of client messages; overdue transitions are applied in deadline order so a delayed scheduler does not grant extra answer time. No client-provided score, correctness, host ID, answer timestamp, deadline, or round index is accepted. Database schema management is an operator command and is not part of application startup.

### AI Quiz Generator (Stage 6)

The Quiz Library includes optional topic-based and document-based question generation. It is disabled by default. To enable it, set `AI_GENERATION_ENABLED=true`, `AI_FREE_TIER_ONLY=true`, and `GEMINI_API_KEY` in the backend environment; `GEMINI_MODEL` is restricted to the configured free-tier allowlist. The default `gemini-3.5-flash-lite` supports structured output and is listed on Google's free tier; `gemini-3.8-flash` is also allowed. Use an API key from a project without billing enabled, and confirm the selected model's current free-tier limits and data-handling terms. The application does not activate billing, choose another provider, or switch to a paid model. Without the explicit flags and a key, the interface reports that generation is unavailable and the API returns `ai_generation_unavailable`. The Gemini API key remains server-side.

`POST /api/ai/generations` accepts JSON `{ "method":"topic", "subject":"Computer Science", "topic":"Operating Systems", "objectives":null, "question_count":5, "difficulty":"medium" }`. `POST /api/ai/generations/document` accepts multipart fields `subject`, `topic`, `objectives`, `question_count`, `difficulty`, and `document` (PDF, DOCX, or TXT). Both require an owner bearer capability and return validated unsaved questions plus an opaque, short-lived server-signed `generation_ticket`. Pass that ticket with the normal `POST /api/quizzes` draft payload to preserve server-controlled `ai_assisted` provenance. The ticket is bound to the owner capability, expires after one hour, and is invalidated when the process restarts. Quiz saving and all later edits continue through the existing library API.

Generation is limited to 20 questions per request and five requests per owner per hour. The limiter is bounded and process-local, so it follows the app's single-worker deployment model and resets on restart. Uploads are limited to 10 MB and extracted text to 100,000 characters. PDF, DOCX, and UTF-8 TXT are supported; scanned PDFs, encrypted PDFs, OCR, and other formats are rejected. DOCX ZIP entry count and expanded size are bounded. Documents are not persisted, and extracted content is not logged. Document text is sent to the configured Gemini provider as untrusted evidence, not trusted instructions. Google's free tier may use submitted content to improve products; users are told this before upload and should not submit sensitive material. Generated quizzes remain private drafts unless the owner explicitly changes visibility or publishes them through the existing workflow. Public and shared player projections continue to omit answer keys and explanations.

## HTTP and WebSocket contract

### Quiz library API

The persistent quiz API is available under `/api`. Apply the Alembic migration before using it; app startup does not create tables. Create an anonymous quiz-owner capability with `POST /api/quiz-owners`; the raw capability is returned once, marked `Cache-Control: no-store`, and must be kept private. Send it as `Authorization: Bearer <capability>` for owner operations. This capability is separate from live-room reconnect tokens. Owner capabilities are long-lived bearer secrets and do not currently expire. There is no user-facing account recovery, owner-token rotation, or owner-token revocation route. If the token is lost, ownership cannot be restored through the API; if stolen, access remains until an operator revokes that anonymous owner in the database. Keep a protected backup of the token and never put it in a URL, source control, or logs.

| Method | Path | Access and behavior |
| --- | --- | --- |
| `POST` | `/api/quiz-owners` | Create an account-free owner capability; returns it once. |
| `GET` | `/api/quizzes` | Search/filter published public quizzes by `category`, `difficulty`, `q`, and `limit`; answer-safe response. |
| `GET` | `/api/quizzes/mine` | Owner capability; returns the caller's quizzes, including drafts and answer keys. |
| `POST` | `/api/quizzes` | Owner capability; creates a draft with validated questions. |
| `GET` | `/api/quizzes/{quiz_id}` | Owner gets authoring data; public visitors get published public data; private published data requires a quiz-specific read capability. Drafts are owner-only. |
| `PATCH` | `/api/quizzes/{quiz_id}` | Owner only; requires `expected_version` and accepts validated quiz fields and/or replacement questions. |
| `POST` | `/api/quizzes/{quiz_id}/publish` | Owner only; requires at least one valid question. |
| `POST` | `/api/quizzes/{quiz_id}/unpublish` | Owner only; invalidates future read access until republished. |
| `POST` | `/api/quizzes/{quiz_id}/duplicate` | Owner capability; duplicates only an eligible published public quiz as a private draft with source attribution. |
| `DELETE` | `/api/quizzes/{quiz_id}` | Owner only; soft-deletes the quiz and revokes its grants. |
| `POST` | `/api/quizzes/{quiz_id}/shares` | Owner only; creates a read-only capability for a published quiz. Optional `expires_in_seconds` is 1–31,536,000. |
| `DELETE` | `/api/quizzes/{quiz_id}/shares/{grant_id}` | Owner only; revokes a grant scoped to that quiz. |

Request models reject unknown fields and enforce lengths, option count, time limits, category keys, and answer-index ranges. Errors use `{"error":{"code":"...","message":"..."}}`; request-validation errors omit submitted input values. Private/draft access failures use a non-disclosing `404`. Player-facing quiz projections omit `correct_option_index`, explanations, and owner identifiers. Authoring projections include answer data only after owner authorization. The API does not implement match reveal; active-game answer disclosure remains owned by the live game engine.

Owner creation has a separate process-local limit of **5 requests per 60 seconds per ASGI client address**. Excess requests receive `429`, `Retry-After`, and `Cache-Control: no-store`; other quiz API routes do not consume this quota. The limiter is bounded in memory and resets on restart. It is suitable for the documented single-worker process, but it is not a shared limit across workers. Behind a reverse proxy, configure Uvicorn to trust forwarded client addresses only from that proxy and require an upstream per-client rate limit on `POST /api/quiz-owners` before public deployment; the application does not trust caller-supplied forwarding headers. Do not run multiple workers expecting the process-local limiter to coordinate them.

### HTTP

| Method | Path | Result |
| --- | --- | --- |
| `GET` | `/` | QuizVerse Live client |
| `GET` | `/health` | `{"ok": true, "service": "quizverse-live"}` |
| `GET` | `/openapi.json` | FastAPI OpenAPI document |

### WebSocket

Connect to `ws://localhost:8000/ws` locally or `wss://<your-host>/ws` over HTTPS. Every client event is a JSON object. Unknown keys, event names, choice values, and malformed payloads are rejected. Messages are limited to 4 KB; each socket is limited to 30 events per 10 seconds, and each remote address to 40 connection attempts per minute.

| Client event | Required fields | Rule |
| --- | --- | --- |
| `create` | `name` | Create a room and join as host |
| `join` | `name`, `code` | Join a waiting room with a valid six-character code |
| `reconnect` | `token` | Resume the guest session within 60 seconds |
| `start` | — | Host starts with at least two connected players |
| `answer` | `choice` (integer 0–3) | Submit one answer for the active question |
| `rematch` | — | Host resets a completed match to the lobby |
| `lobby` | — | Host returns a completed match to the lobby |

Server events include `joined`, `state`, `player-joined`, `player-reconnected`, `player-left`, `player-expired`, `answer-update`, `lobby`, and `error`. State snapshots are personalized with the viewer’s answer status and reveal feedback. The correct choice is absent from all question-phase snapshots. An error has the shape `{"event":"error","code":"duplicate_answer","message":"…"}`. `state.phase` is one of `waiting`, `countdown`, `question`, `reveal`, or `results`; deadlines are absolute server epoch milliseconds paired with a server clock sample for client display alignment.

## Security and operating limits

- Room codes use cryptographically secure random selection from an alphabet without ambiguous characters. Guest IDs and bearer session tokens use cryptographic randomness; only a hash of each token is retained in memory.
- The connection’s authenticated room/player identity is stored server-side. No identity or score fields in later events can switch membership or grant host rights.
- Browser WebSocket origins are checked against `ALLOWED_ORIGINS`. Non-browser WebSocket clients without an `Origin` header are allowed for native clients and tests.
- Strict Pydantic validation, a 4 KB event limit, per-socket event throttling, per-address connection throttling, and HTTP security headers are enabled.
- Anonymous quiz-owner creation has its own process-local HTTP rate limit. Public deployments behind a proxy must also enforce an upstream per-client limit for `POST /api/quiz-owners` and configure trusted proxy forwarding; the app does not trust arbitrary `X-Forwarded-For` values.
- The browser escapes server strings before rendering them and keeps the reconnect token in tab-scoped `sessionStorage`.
- Question deadlines use server monotonic time. Client clock samples align the visual countdown; clients cannot extend the server deadline.
- A process restart clears all active rooms. State, session tokens, rate limits, and timer scheduling are in-process and are not shared between workers. Run **one Uvicorn worker**. Horizontal scaling, durable rooms, or process-independent reconnects require a shared store and coordination layer such as Redis; adding it now would add operational cost without improving this single-instance portfolio build.
- The host deployment guide below targets a free portfolio/demo service. Free tiers can restart or idle-sleep, so this in-memory architecture is not suitable for a production tournament with uptime or durable match requirements.

## Tests and browser evidence

Run the Python suite:

```sh
python -m pytest -q
```

Database migration and persistence tests are included in the Python suite and use isolated temporary SQLite files. PostgreSQL integration has not been tested in this workspace; an optional driver configuration is not evidence of PostgreSQL compatibility.

Run the original Node project’s suite from its directory:

```sh
cd ../quizverse-live
npm test
```

Run two real browser sessions with a full match:

```sh
python scripts/browser_verification.py
```

The browser script uses three isolated browser contexts (desktop, tablet, and mobile), makes three separate WebSocket connections, plays one complete ten-question game including an actual 15-second no-answer timeout, starts a rematch, completes it, and returns to the lobby. Python unit tests use a deterministic monotonic clock for edge cases and fast full-match progression.

### Verified in this workspace

- Python **3.13.17**; FastAPI, Uvicorn, Pydantic, HTTPX, pytest, and Playwright installed in the project-local `.venv`.
- Python suite command: `python -m pytest -q`. Test counts vary as coverage changes; use the command output for the current result.
- Playwright Edge verification: independent desktop, tablet, and mobile sessions created/joined, invalid code rejected, host departure transferred control, host session reconnected, synchronized 15-second question deadline observed, simultaneous answers scored, unanswered round timed out, full ten-question match reached final rankings, rematch completed, return-to-lobby succeeded, and the 390 px mobile plus 820 px tablet layouts had no horizontal overflow.
- Original Node suite: **6 passed** after changing the integration test’s child-process working directory from URL `.pathname` to `fileURLToPath`, fixing Windows drive-path handling.
- JavaScript syntax (`node --check client/app.js`) and Python bytecode compilation (`python -m compileall`) completed without errors.
- This workspace has no Git metadata. Tests and browser verification were run locally; no public deployment was created.

## Deployment assessment: Render free web service

Render currently documents inbound WebSocket support on web services, and provides a free Python web-service instance suitable for a portfolio demo. The free instance has limits: it idles after 15 minutes without inbound HTTP or WebSocket traffic, may restart, has ephemeral disk, and can take about a minute to resume. Render states free instances are for hobby projects and previews rather than production. Its included-hours, bandwidth, and build-minute limits can suspend service or builds; usage beyond included outbound bandwidth/build minutes can incur charges if a payment method is attached. Review current limits and spend settings before creating a service. See [Render WebSockets](https://render.com/docs/websocket), [free instance limits](https://render.com/docs/free), [web-service setup](https://render.com/docs/web-services), and [Python version pinning](https://render.com/docs/python-version).

No hosting account was available to inspect or use in this workspace; no deployment or external account action has been taken. To create a free portfolio demo yourself:

1. This workspace had no Git metadata. To create a repository, run these commands from `T:\Projects\quizverse-live` and replace the remote with a repository you control:

   ```powershell
   git init
   git add .gitignore README.md quizverse-live quizverse-live-python
   git commit -m "Build Python QuizVerse Live"
   git branch -M main
   git remote add origin https://github.com/<account>/<repository>.git
   git push -u origin main
   ```

2. In Render, create **New → Web Service**, choose that repository, and set **Root Directory** to `quizverse-live-python`.
3. Select the Python runtime and **Free** compute plan. Set the build command to `pip install .` and the start command to `quizverse-live`.
4. Add environment variables `HOST=0.0.0.0`, `PYTHON_VERSION=3.13.17`, and `ALLOWED_ORIGINS=https://<the-service-name>.onrender.com`. Render assigns the service subdomain during setup; use that exact HTTPS origin. Set the health check path to `/health`.
5. Deploy, wait for Render’s deployment status to report live, then open the assigned service URL and verify `GET /health`, the home page, and a real `wss://<assigned-host>/ws` room with two devices.
6. Keep one instance and do not set multiple workers. A redeploy/restart drops active rooms; free idling and maintenance can interrupt games. Never add a database or paid service without deciding that durable state or uptime is needed and reviewing the cost.

Render assigns the URL; this repository does not contain or claim a deployed public URL.
