# Persistent competitive Elo rankings

## Rating method

New ratings start at **1000**. The initial implementation uses Elo with **K = 32** and the conventional **400 point scale**. For each completed match, it evaluates every pair of participants:

```text
expected(i, j) = 1 / (1 + 10 ^ ((rating(j) - rating(i)) / 400))
actual(i, j)   = 1 for a higher placement, 0.5 for a tie, 0 for a lower placement

delta(i) = 32 * (average(actual(i, j)) - average(expected(i, j)))
```

The pairwise scores are averaged across the other participants. Deltas are rounded to integers, then a deterministic correction distributes rounding residuals by player ID so each match remains zero-sum. Ratings have a zero floor; when a loss would cross it, the clipped points are taken from match winners' gains so stored deltas still equal `new_rating - prior_rating` and still sum to zero. The algorithm version is stored with each history entry.

Players remain provisional for their first **10 rated matches**. This is a display/placement label; the initial K factor stays at 32 during those matches.

## Eligibility

Ratings require a completed, individual result from Classic, Speed Challenge, Elimination, Quiz Royale, Power Play, or Bluff Master. Every participant must be bound to a different active player profile. Anonymous participants, solo practice, team results, cooperative Escape Room results, unsupported modes, incomplete results, and duplicate profile seats are not rated. Disconnections and forfeits use the completed server result's placement and do not change eligibility by themselves.

Each eligible match updates a global rating and a rating for that mode in the same database transaction as its persistent match result. Completed pre-rankings history is reconciled at application startup; failed reconciliation is logged and retried on a later startup. Idempotency is enforced by the unique engine match ID and unique player/scope/match history keys.

## API and privacy

- `GET /api/rankings?scope=global&limit=20&offset=0`
- `GET /api/rankings?scope=mode&mode=classic&limit=20&offset=0`
- `GET /api/player-profiles/me/ratings` (requires the profile credential)
- `GET /api/player-profiles/me/ratings/history?scope=global&limit=20&offset=0` (requires the profile credential)

Public rankings expose the random pseudonymous player ID, active display name, rating, rated-match counts, record, provisional status, and algorithm version. Personal ratings and rating history are available only to the authenticated profile. The browser stores the credential in tab-scoped session storage; it is never included in URLs. There are no client-write endpoints for ratings or rating history.

Rating history is append-only in application services and has no HTTP mutation routes. Database administrators can still change stored rows directly; this implementation does not add database triggers or cryptographic signatures. Leaderboards are computed from current rating rows rather than materialized snapshots.

## Database compatibility

The additive migration uses SQLAlchemy/Alembic portable types, constraints, indexes, and boolean defaults for SQLite and PostgreSQL. SQLite migration and downgrade paths are covered in the test suite. PostgreSQL runtime migration and query behavior still require a PostgreSQL service for verification.
