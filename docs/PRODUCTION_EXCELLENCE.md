# QuizVerse Live — Production Excellence Architecture & Operations Guide

## 1. System Architecture Overview

QuizVerse Live is a real-time multiplayer trivia platform and competitive learning engine built with:
- **FastAPI**: Asynchronous HTTP and WebSocket application framework.
- **GameEngine**: In-memory, server-authoritative state machine controlling all nine game modes.
- **ConnectionHub**: Manages live WebSocket sessions, broadcast channels, client heartbeats, and rate limiting.
- **SQLAlchemy 2.0 & Alembic**: Database abstraction, connection pooling, and linear schema migrations.
- **PostgreSQL**: Production relational database for persistent storage (quizzes, match histories, player profiles, ratings, tournaments, leagues, social graphs, notifications, AI learning paths, and community publications).
- **SQLite**: Zero-configuration embedded database for local development and automated CI testing.
- **Vanilla JavaScript, HTML5 & CSS3**: High-performance Single Page Application with responsive design for desktop, tablet, and mobile browsers, wrapped cleanly for Android.

```
                    ┌────────────────────────────────────────────────────────┐
                    │          Client Layer (Web / Android WebView)          │
                    │   Vanilla JS SPA | CSS3 Responsive | WebSockets        │
                    └───────────────────────────┬────────────────────────────┘
                                                │ HTTPS / WSS
                                                ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                          FastAPI Single-Process Container                                   │
│                                                                                             │
│  ┌─────────────────────────┐   ┌───────────────────────────┐   ┌─────────────────────────┐  │
│  │   Security Middlewares  │   │      HTTP REST APIs       │   │    WebSocket Hub / WS   │  │
│  │  CSP, HSTS, CORS, Limits│   │  Auth, Quiz, Tournaments, │   │  Heartbeat, Rate Limit, │  │
│  │  Readiness / Health Probes  │  Community, AI Intelligence│   │  Authoritative Events   │  │
│  └─────────────────────────┘   └─────────────┬─────────────┘   └────────────┬────────────┘  │
│                                              │                              │               │
│                                              ▼                              ▼               │
│                                ┌─────────────────────────────────────────────────────────┐  │
│                                │             Authoritative GameEngine                    │  │
│                                │   9 Game Modes | Timers | Fairness | Elo Reconciliation │  │
│                                └─────────────────────────────┬───────────────────────────┘  │
│                                                              │                              │
│                                                              ▼                              │
│                                ┌─────────────────────────────────────────────────────────┐  │
│                                │          Service Layer & Persistence Repositories       │  │
│                                │  Ratings, Tournaments, Leagues, Social, Community, AI   │  │
│                                └─────────────────────────────┬───────────────────────────┘  │
│                                                              │                              │
└──────────────────────────────────────────────────────────────┼──────────────────────────────┘
                                                               │ SQLAlchemy Pool
                                                               ▼
                                ┌──────────────────────────────────────────────────────────┐
                                │          PostgreSQL Production Database                  │
                                │   59 Tables | Foreign Keys | Constraints | Indexes       │
                                └──────────────────────────────────────────────────────────┘
```

---

## 2. In-Memory Architecture & Deployment Concurrency Constraints

The core `GameEngine`, `ConnectionHub`, and `PresenceService` rely on process-local in-memory state:
1. **Active Match Rooms**: Room state, question sequences, timer countdowns, player scores, and submitted answers are maintained authoritative in memory during a match.
2. **Presence & WebSocket Hub**: Open WebSocket socket references are process-local.
3. **Deployment Constraint (CRITICAL)**:
   - When deploying to containerized cloud platforms (such as Google Cloud Run), the application **MUST** be configured with `maxScale: 1` (`--max-instances=1`).
   - Running multiple uncoordinated processes or containers (`--max-instances > 1`) will cause split-brain room state where players connecting to different container instances cannot see each other or communicate.
   - A single container with 2 CPU cores and 2 GB RAM comfortably supports up to 200 concurrent active WebSocket sessions and >270 HTTP requests/second.
4. **Process Restart Behavior**:
   - Stored match history, ratings, profiles, quizzes, and community content survive restarts completely because they are committed transactionally to the database.
   - Any match actively in progress during an abrupt container restart will terminate. Match history reconciliation automatically verifies and recalculates ratings on the next startup.

---

## 3. Database Management: PostgreSQL & SQLite

### Dialect Compatibility
All models in `quizverse.persistence.models` are strictly dialect-portable:
- Timestamps use timezone-aware `DateTime(timezone=True)`.
- Booleans map cleanly to PostgreSQL `BOOLEAN` and SQLite `INTEGER (0/1)`.
- Foreign keys enforce cascade or restrict behaviors.
- Alembic migrations (revisions 1 through 13) support both dialects natively.

### Connection Pooling
For PostgreSQL in production, pooling is managed via `quizverse.persistence.db`:
- `DB_POOL_SIZE`: Default `10`
- `DB_MAX_OVERFLOW`: Default `20`
- `DB_POOL_RECYCLE`: Default `1800` (seconds, prevents stale connections behind NAT/firewalls)
- `DB_POOL_TIMEOUT`: Default `30` (seconds)
- `pool_pre_ping=True`: Verifies connection liveness before checking out from pool.

### Production Mode Enforcement
When `ENVIRONMENT=production`, the application strictly refuses to start on SQLite:
```python
if env_mode == "production":
    if parsed.get_backend_name() != "postgresql":
        raise RuntimeError("Production environment requires PostgreSQL.")
```

---

## 4. Security Hardening

- **Content Security Policy (CSP)**: Disallows inline scripts, forbids framing (`frame-ancestors 'none'`), restricts connect destinations to `'self'` and WebSocket schemes (`ws:`, `wss:`).
- **HTTP Strict Transport Security (HSTS)**: Automatically added in production mode (`max-age=31536000; includeSubDomains`).
- **CORS Protection**: `CORSMiddleware` parses `ALLOWED_ORIGINS` to allow only authorized domains and mobile application origins (`capacitor://localhost`).
- **Pseudonymous Credentials**: Zero plaintext passwords; tokens hashed with SHA-256 and compared via `hmac.compare_digest`.
- **Single-Use Tickets**: Short-lived WebSocket admission tickets (`LiveIdentityTickets`) expire after 30 seconds and are destroyed upon first use.
- **Server-Side Authorization**: Object-level permissions for creator analytics, quiz editing, and moderation actions. Regular users cannot elevate themselves to moderator or admin.
- **Traceback Masking**: Unhandled server exceptions log the full traceback on the server and return a sanitized JSON error (`{"ok": false, "error": "Internal server error"}`) in production.

---

## 5. Operational Probes & Monitoring

- **Liveness Probe**: `GET /health` (Verifies FastAPI process and engine liveness).
- **Readiness Probe**: `GET /ready` (Verifies database connectivity with `SELECT 1`, verifies engine match history persistence health, returns HTTP 200 when ready, HTTP 503 when database is down).
- **Structured Logging**: Configurable via `LOG_LEVEL` (`INFO`, `WARNING`, `ERROR`, `DEBUG`).
