# Player identity and match history

Player profiles are optional, pseudonymous identities. Casual room creation and joining continue to work without a profile. A nickname is a display label and is never used for authentication.

## Credentials

Creating a profile issues a 256-bit random bearer credential once. Only its SHA-256 hash is stored. Requests send it in an `Authorization: Bearer …` header; it is never accepted in a URL or broadcast to room members. Credential rotation revokes all earlier credentials in the same database transaction. Profile deletion revokes every credential and changes the profile status to `anonymized` with the display name `Deleted player`.

The frontend keeps a signed-in credential in tab-scoped `sessionStorage`, never in a URL or rendered page text. Creation and rotation offer a copy action so the user can save the one-time credential in a password manager. A lost anonymous credential cannot be recovered. Signing out clears the current tab's credential but does not revoke it; rotation or profile deletion revokes credentials on the server.

To bind a profile to a new room seat, the browser first exchanges its HTTP bearer credential for a 30-second, single-use in-memory WebSocket ticket. The ticket is consumed during create/join and is not echoed. The server records the profile ID on the in-memory seat; clients cannot change that binding. Reconnect uses the existing separate room token and preserves the binding.

## Match history

After an engine-owned match reaches its terminal results phase, the server builds a validated result snapshot. Casual matches with no profile-bound participant are not persisted. The persisted record contains mode, rule version, timestamps, a SHA-256 question-set digest, mode-specific result type, and seat/team metrics. It contains no room code, nickname snapshot, correct answer, prompt, bearer token, IP address, or client-submitted score. A unique engine match ID and canonical result digest make retries idempotent; conflicting retries are rejected.

Mode result semantics are preserved:

- Individual modes store server-derived placements and win/loss/draw outcomes.
- Team Battle stores team standings separately from player seats; it does not assign individual placements.
- Escape Room stores the shared success/failure and puzzle progress, without individual scores, placements, or competitive wins.
- Solo Practice stores practice metrics and the `practice` outcome, without a placement or competitive win.

The match-history API returns only the authenticated profile's own seat result and its team summary when relevant. It does not return opponents' profile IDs, other participant records, question-set hashes, or result digests. Statistics distinguish no persisted history (`history_available: false`) from an existing history with zero wins.

## Retention and operational assumptions

Deleting a profile revokes access and anonymizes its identifying display name. Match and participant facts remain for integrity, including the anonymized profile foreign key and non-identifying scores/outcomes. There is no automatic expiration or physical erasure of match rows in this stage. This is an explicit provisional product assumption, not a legal retention policy or a claim of legal compliance. A retention duration, deletion exceptions, export process, and any future audit needs require product/legal review before public launch.

Completed profile-linked results are retried in the existing single process with bounded backoff. `/health` reports the counts of pending and retrying history writes, and server logs identify the engine match ID and error type without logging result payloads or credentials. A rematch is blocked until its prior profile-linked result is durably saved. The result stays in memory while the process is alive; a process crash before a successful write can still lose an unsaved result. This limitation is visible rather than represented as a successful save. No Redis or external queue is introduced. Continue to run one Uvicorn worker because room state remains process-local.

## API and migration

Apply the additive migration before using profile/history APIs:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

The profile endpoints are `POST /api/player-profiles`, `GET/PATCH/DELETE /api/player-profiles/me`, `POST /api/player-profiles/me/credentials/rotate`, and `POST /api/player-profiles/me/live-ticket`. Personal history and statistics are at `GET /api/player-profiles/me/matches` and `GET /api/player-profiles/me/competitive-stats`.

Profile creation, credential rotation, and live-ticket issuance use a bounded process-local per-client rate limiter. Like the current WebSocket room state and quiz-owner creation limiter, the limiter does not coordinate across multiple workers.
