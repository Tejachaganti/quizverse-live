# QuizVerse Live Platform Expansion Specification

**Status:** Stage 1 design proposal; no runtime changes are authorized by this document.  
**Scope:** Domain model, security boundaries, game-mode rules, and acceptance criteria for later stages.  
**Compatibility baseline:** The existing ten-question, four-option, 15-second Classic game, server-calculated scoring, reconnect behavior, and Momentum Shift rules remain the default.

## 1. Product and compatibility principles

1. Normal play remains account-free. A guest can create or join an arena without creating a saved quiz or disclosing personal details.
2. The server owns authorization, quiz publication state, question selection, active match state, deadlines, correctness, scores, mode transitions, and results.
3. A saved quiz is a durable authoring artifact. A running match is an immutable server-side snapshot of the selected quiz and rules. Editing the saved quiz must not alter a match already in progress.
4. Existing Classic rooms retain their current rules: ten questions, four choices, 15 seconds per question, 1,000 base points, up to 500 speed points, and the published Momentum Shift award. Existing WebSocket event shapes should be extended compatibly where practical; a breaking protocol change requires an explicit versioning/migration decision.
5. No mode may accept client-supplied scores, correctness, deadlines, timestamps, identities, team assignments, or progression.
6. Do not describe built-in content as fact-checked unless a real editorial verification process exists. Content provenance must be visible.

## 2. Quiz and question schema

The field names below are logical names, not a commitment to a particular database or wire representation. IDs are server-generated. Clients may submit editable fields but never IDs, ownership, publication timestamps, share codes, or moderation/provenance claims as authoritative values.

### 2.1 Quiz

| Field | Type / constraints | Meaning |
| --- | --- | --- |
| `id` | Opaque random identifier; immutable | Internal/publicly addressable quiz identifier. It must be unguessable and must not embed owner information. |
| `owner_id` | Foreign key to anonymous owner record; immutable after creation | The capability principal allowed to manage this quiz. |
| `title` | Required trimmed string, 1–100 Unicode characters | Display title. Reject control characters and markup is rendered as text. |
| `description` | Optional trimmed string, 0–500 characters | Plain text description. |
| `category` | Extensible normalized category key, with optional display label | Default category for questions that do not override it. Initial catalog includes general knowledge, technology, programming, science, history, geography, sports, and aptitude. Additional categories are allowed through controlled normalization rather than schema changes. |
| `difficulty` | `easy`, `medium`, or `hard` | Default difficulty for questions that do not override it. |
| `visibility` | `private` or `public` | Private quizzes require owner authorization or an explicitly granted share capability. Public quizzes are discoverable and playable under the public-library policy. |
| `status` | `draft` or `published` | Drafts are owner-only and cannot be selected for public play. A published quiz must pass publication validation. |
| `question_count` | Derived from active question records | The stored question list is the source of truth; do not trust a submitted count. A host may choose a smaller match count if the selected quiz contains enough eligible questions. |
| `default_time_limit_seconds` | Integer, 5–120 inclusive; default 15 | Used when a question has no override. The existing Classic preset remains 15 seconds. |
| `provenance` | Enum plus optional metadata | `built_in_reviewed`, `user_authored`, or `ai_draft`/`ai_assisted`. Provenance is server-set or maintained as an audit field; it is not a verification guarantee. |
| `created_at`, `updated_at`, `published_at` | UTC timestamps; publication timestamp nullable | Server-managed lifecycle timestamps. |
| `version` | Positive integer, incremented on saved edits | Supports optimistic concurrency and immutable match snapshots. |
| `share_code` | Optional unique random code; revocable | Optional convenient sharing handle. It is separate from the unguessable quiz ID and grants only the access described by its permission scope. |

**Publication validity:** A published quiz has at least the configured minimum playable question count (recommended minimum: 1 for flexible practice and 10 for a full Classic match), every question is valid, all referenced categories/difficulties normalize successfully, and no required field is blank. A host cannot request more questions than the eligible bank contains. Product decision: whether public quizzes with fewer than ten questions may be published for non-Classic modes is addressed under unresolved questions.

### 2.2 Question

| Field | Type / constraints | Meaning |
| --- | --- | --- |
| `id` | Opaque random identifier; immutable | Stable question identity inside the saved quiz. |
| `quiz_id` | Parent quiz ID | Owning quiz. |
| `position` | Non-negative integer unique within a quiz | Authoring order; randomized selection may ignore this order for a match. |
| `prompt` | Required trimmed plain text, 1–500 characters | Question text. Reject control characters except supported whitespace. |
| `options` | Exactly four non-empty trimmed plain-text strings, each 1–200 characters | Ordered answer choices. Duplicate choices after Unicode normalization and case folding are rejected. |
| `correct_option_index` | Integer 0–3 | Answer key. Never included in a player-facing live-question response. |
| `explanation` | Optional plain text, 0–1,000 characters | Shown only after the question closes, or in authorized quiz preview/editor surfaces. |
| `category` | Optional normalized category key | Overrides the quiz default. |
| `difficulty` | Optional `easy`, `medium`, or `hard` | Overrides the quiz default. |
| `time_limit_seconds` | Optional integer 5–120 | Overrides the quiz default. Server validates and freezes it into the match snapshot. |
| `provenance` | Inherited or explicit source label | Distinguishes reviewed built-in material from user or AI material. |
| `created_at`, `updated_at` | Server-managed UTC timestamps | Audit and editing support. |

Validation additionally rejects missing/extra object fields where strict request models are used, bool values where integers are expected, invalid indexes, oversized request bodies, invalid Unicode/control characters, and non-finite or out-of-range numeric values. HTML is not accepted as trusted markup; frontend output is escaped. Database constraints should backstop application validation for required fields, ranges, foreign keys, and unique question positions.

### 2.3 Match selection configuration

The host may choose a published quiz or built-in bank and request category filters, difficulty filters, question count, and a mode preset. Configuration is validated and authorized before start. Question selection is random without replacement within a match. If a filter produces too few questions, the server returns a clear error before the match starts; it must not silently relax the filter or repeat questions. The match snapshot stores the selected question content and rules version so later edits cannot change it.

Recommended limits to keep payloads and authoring usable: at most 200 questions per quiz, at most 20 category filters, request body at most 256 KB for quiz writes, and existing WebSocket event payload limit remains 4 KB. These limits can be tuned from measured usage but must remain explicit and tested.

## 3. Anonymous guest ownership and capability tokens

### 3.1 No-account play

Joining or creating an ordinary live room continues to require only a nickname and room code. A room reconnect token remains a separate, short-lived capability for the existing 60-second reconnect window. It must not grant quiz-library ownership or analytics access.

Saved quiz authoring needs durable ownership. Recommended account-free design: when a guest first opts to save a quiz, issue a cryptographically random owner capability (at least 256 bits of entropy), store only a one-way hash of it in an anonymous owner record, and return the raw capability once to the browser. The browser stores it locally for that browser profile. Requests that manage owned quizzes present it in an authorization header over HTTPS; it never appears in a URL, logs, analytics, WebSocket broadcasts, or rendered HTML. The server compares hashes using constant-time comparison where applicable. Rotate/revoke support should be available. Losing the capability means losing private ownership access; there is no account recovery in this design.

The app should not create this durable owner record merely because a player joins a game. Offer quiz saving as an explicit authoring action. Public quizzes can be browsed and played without a capability. A share code may grant read/play access only, unless the owner deliberately creates a separate edit capability; edit capabilities must be high entropy, stored hashed, scope-limited, revocable, and never inferable from the public share code.

### 3.2 Token handling requirements

- Generate capabilities and share codes with a cryptographically secure random source; use separate token namespaces and scopes.
- Persist hashes, not raw bearer tokens. Never persist passwords because this design has no passwords.
- Use HTTPS in hosted environments; set secure, same-site cookie attributes if a future design moves capabilities from browser storage to cookies. Do not put bearer tokens in query parameters.
- Redact authorization headers and token values from application errors and access logs. Avoid returning the token from later retrieval endpoints.
- Apply per-IP and per-capability rate limits where feasible; do not rely on IP address as identity.
- A quiz capability grants access only to that owner's quiz resources, never room host rights, another owner's private data, or server administration.
- Existing live room tokens remain isolated from the persistent quiz ownership system.

## 4. Quiz lifecycle, visibility, and permissions

| Action | Draft owner | Published owner | Public visitor | Valid read/play share capability |
| --- | --- | --- | --- | --- |
| Create | Creates a draft under own capability | — | May create only after obtaining an owner capability | — |
| Edit | Allowed | Allowed; increments version | Denied | Denied unless a separately scoped edit grant is explicitly supported |
| Preview | Full prompt/options/key/explanation for owner QA | Full author preview | Public preview omits answer key by default | Read/play preview omits answer key |
| Publish | Allowed after validation; status becomes published | Allowed after edits revalidate | Denied | Denied |
| Duplicate | Allowed; new ID, draft status, owner is current caller | Allowed; new ID, draft status | Allowed only if public duplication is a deliberate product policy | Denied by default |
| Delete | Allowed; soft-delete/archive recommended for references | Allowed; public listing removed and future starts denied | Denied | Denied |
| Share | Owner can create/revoke scoped link/code | Owner can create/revoke scoped link/code | Can copy public link; cannot grant edit | Can access only the granted scope until revoked/expired |
| Play | Owner may play own valid draft only in an explicit private preview/practice flow | Allowed according to visibility | Public published quiz only | Read/play grant only |

**Recommended lifecycle:** New quiz → draft → preview → validated publish → edit (new version) → republish. Editing a published quiz does not mutate existing match snapshots. If a published quiz becomes invalid after edits, the edit is saved as draft or the last published version remains available until a valid replacement is published. Deletion should initially be soft deletion: remove from search and prevent new starts, while preserving already completed match references and honoring retention rules. Hard deletion/purge can be a separate retention operation.

**Visibility and sharing:** `private` means not discoverable and accessible only to the owner, or to an explicit scoped recipient capability. `public` means discoverable and playable, but never exposes the answer key before reveal. Public does not confer edit rights. Share links should not contain owner secrets. Share-code entropy and rate limiting should make enumeration impractical. Public search should return a curated safe projection, not owner identifiers, tokens, answer keys, or private match data.

## 5. Persistence and state boundaries

| Data | Recommended persistence | Lifetime and behavior |
| --- | --- | --- |
| Quiz metadata and question content | Durable relational database | Survives process restarts. SQLite is the local default; PostgreSQL is the hosted target when available. |
| Anonymous owner records and hashed capability grants | Same durable database | Survives restart; revocation invalidates future requests. Never store raw capabilities. |
| Share grants/codes and visibility | Same durable database | Durable, scoped, revocable, and rate-limited. Store code/token hashes if they act as bearer grants. |
| Completed match summary, participants, round outcomes and response times | Durable database, subject to privacy/retention policy | Written transactionally when a match completes. Guest IDs should be pseudonymous; do not require names beyond in-game display names. |
| Active room membership, sockets, answer locks, deadlines, current score, reconnect window | In-memory single-process engine initially | Ephemeral. Process restart ends active games; clients receive a reconnect/session-expired state and start a new room. Do not silently restore a partially completed match from incomplete state. |
| Active match's question/rule snapshot | In-memory with active room | Immutable during that process lifetime. Persist only as part of a completed history record unless resumable matches are explicitly designed later. |
| AI provider credentials | Server environment/secret manager only | Never in database quiz rows or browser bundles. No credential is needed for manual quiz creation or multiplayer. |

**Transactions:** Quiz version/question replacement should be atomic. Publication changes status only after all questions validate. Completion history rows should commit together and be idempotent by match identifier so duplicate completion events do not create duplicate records. Database writes must not block the event loop for long work; implementation should use an appropriate async driver or bounded threadpool strategy and keep live engine locks separate from database transactions.

**Restart and scaling:** Current in-memory rooms disappear on restart. A one-instance deployment and one application worker are required for live rooms. SQLite is suitable for local use and a single-instance demo, not shared concurrent writes across replicas. PostgreSQL supports durable quiz/history data but does not by itself coordinate in-memory sockets/timers between workers. Redis or another shared coordination layer is a future requirement only if active games must survive/reach multiple processes; it is not required for this first persistence stage. Free services may sleep/restart and may have ephemeral local disks, so a hosted SQLite file may not be durable. Hosted persistence requires a documented persistent disk or managed PostgreSQL plan whose current cost/limits are checked before use.

**Migrations:** Use versioned migrations, never auto-create/alter production schema on app startup. Back up durable data before destructive migrations; use additive migrations where possible; include downgrade guidance when safe. Seed the reviewed built-in question bank idempotently and distinguish its provenance. Tests should exercise fresh-database migration and upgrade from the previous schema. No existing saved-quiz data exists, so the initial schema requires no legacy quiz-data migration. Existing in-memory game rooms are intentionally not migrated.

## 6. Game modes and explicit rule proposals

These rules are proposals for approval before implementation. Terms:

- **Correct base:** 1,000 points for a correct answer.
- **Speed bonus:** `round(500 × (time_limit − elapsed) ÷ time_limit)`, clamped to 0–500, based on server receipt time and the server-owned question start. An incorrect, missing, late, or eliminated answer earns zero.
- **Momentum Shift:** Preserve the current rule exactly in Classic: at question open, a player trailing the current leader by 1–1,500 points is eligible; a correct response accepted within the first 5 seconds earns +250. It is server-calculated, logged, never subtracts points, and is not silently multiplied. Whether it applies in other modes needs the product decision below.
- **Tie-break order (recommended):** total points descending, correct answers descending, then cumulative server-recorded response time for correct answers ascending. If still tied, declare a shared place; do not use client timestamps or an arbitrary random winner.
- **Question uniqueness:** select without replacement within the match. Per-player answers and all transitions are serialized by the authoritative engine.

### 6.1 Classic Live Quiz

- **Eligibility:** 2–8 connected real guests at start; one host. This is the existing default.
- **Scoring/timing:** Ten questions, four options each, 15 seconds per question; 1,000 correct base plus up to 500 speed bonus. Preserve current Momentum Shift unchanged.
- **Progression:** Existing launch countdown, question, reveal, and results states. Server deadline closes the question; all connected players answering may trigger early reveal, as today. A timeout reveals and advances deterministically. Disconnected players remain subject to the existing reconnect policy; only connected players are required for the all-answered early reveal condition.
- **Completion/ties:** Finish after ten rounds and rank using the recommended tie-break order. Keep current result fields and event behavior compatible; add fields only in a versioned/optional manner.
- **Backward compatibility:** This is the exact current default preset; adding quiz selection must not change its point formula, timing, question count, or Momentum Shift behavior.

### 6.2 Speed Challenge

- **Eligibility:** 2–8 players, same room/host/reconnect rules as Classic.
- **Scoring/timing recommendation:** Ten questions, default 10 seconds each; correct base remains 1,000 and speed formula is scaled to the configured 10-second limit, still capped at 500. No separate hidden multiplier. To avoid double speed rewards, this mode should either retain the established speed bonus and omit Momentum Shift, or preserve both; recommended: **omit Momentum Shift in Speed Challenge**, because the mode already emphasizes speed. Label this variant before start.
- **Progression:** Same synchronized phases and server deadlines as Classic; missed/late answer is zero. Early reveal after all connected players answer remains available.
- **Ties:** Recommended common tie-break order; fastest correct-response total is particularly relevant but points/correctness remain ahead of time.

### 6.3 Team Battle

- **Eligibility:** 4–8 players, even number, divided into two balanced teams. Recommended default assignment alternates server-assigned players in join order after start; allow host to configure teams before start only if the assignment is server-validated and visible to everyone. No bots or simulated team members.
- **Scoring/timing recommendation:** Individual points use Classic scoring (1,000 + up to 500). Team score is the sum of member match points; team accuracy is the sum of correct responses. No extra team multiplier. Recommended: no Momentum Shift in Team Battle, since eligibility against an individual leader creates ambiguous team incentives.
- **Timing/progression:** Ten questions, 15 seconds each, shared deadlines. Each player answers individually; the team cannot submit on another member's behalf. Disconnected/reconnecting players retain their seat and can answer only while connected before the deadline. The match continues if a member disconnects; it does not create replacement/fake players.
- **Completion/ties:** Highest team sum wins. Tie-break by team correct count, then sum of server-recorded correct response times; if equal, shared team placement. Show both team totals and individual contributions.

### 6.4 Elimination Mode

- **Eligibility:** 2–8 players. Requires a question count and per-question time limit configured before start.
- **Scoring/timing recommendation:** Each question is a survival round. A wrong, missing, or late answer eliminates a player at reveal; correct answers remain eligible. To avoid inventing a punitive hidden score system, award the existing 1,000 + up to 500 only for correct answers, with no score subtraction. Recommended: disable Momentum Shift in Elimination.
- **Progression:** Synchronized questions and reveals. Continue until one survivor remains or the configured question count is exhausted. If everyone is eliminated in one round, those eliminated in that same round share the last survival placement; the match proceeds to results immediately. A reconnect cannot undo an elimination or extend a deadline.
- **Completion/ties:** Rank first by survival round (later survives rank higher), then total points, correct answers, and correct response time. Players surviving to the end share the highest survival rank and are ordered by the common point tiebreakers. This avoids a random sudden-death extension unless separately approved.

### 6.5 Solo Practice

- **Eligibility:** One real player; no room code or opponent required. The interface must not display fake competitors or an invented online count. Optional asynchronous personal bests are statistics, not simulated opponents.
- **Scoring/timing recommendation:** Ten questions by default; same 1,000 + up to 500 scoring formula and selected question time limits. No Momentum Shift because there is no rival/leader. Results can compare only to the player's own prior performance if history consent/retention is enabled.
- **Progression:** Same server-authoritative deadlines and answer privacy, with immediate reveal after answer or timeout. The single player cannot skip/reopen a question through client events.
- **Completion/ties:** No match ranking; show score, accuracy, and response-time summary. Personal best ties use score, correct count, then response time; equal results remain tied.

### 6.6 Rules shared by all modes

- A mode's rule version and validated configuration are frozen in the match snapshot and included in completed history.
- Host chooses mode/configuration only in the lobby. Non-host requests are rejected. Starting with insufficient eligible players, no matching questions, or invalid team configuration returns a structured error and leaves the room waiting.
- The engine lock makes answer acceptance, scoring, disconnects, expiry, and timer advancement atomic. Server monotonic time decides acceptance; wall clock is display-only.
- Correct answer and explanation are withheld from all player-facing state until the question closes. Preview/editor APIs are separately authorized.
- Simultaneous answers are serialized. Duplicate and stale submissions fail without changing scores. Score adjustments, including Momentum Shift, have an auditable reason and cannot produce negative points.

## 7. AI-generated question workflow

AI generation is an optional draft assistant, never a requirement for multiplayer, manual quiz authoring, or use of built-in questions.

1. An owner submits a topic or study notes, category, difficulty, count, and desired time limit to the server.
2. The server applies input-size/rate limits and calls a configured provider adapter using credentials only from server environment/secret storage. Provider and model are configurable; the client does not select arbitrary URLs or send credentials.
3. Provider output is untrusted. Parse strict structured output and validate every question using the same schema (four distinct options, one valid answer index, field lengths, category/difficulty/time range). Reject or return per-item validation errors; never silently repair an answer key in a way that changes meaning.
4. Store results as an owner-only draft labeled `ai_draft`, with provider/model metadata only if privacy policy permits. Never mark it verified or publish it automatically.
5. The owner can edit, preview, regenerate selected drafts, discard them, or explicitly approve/publish after review.
6. Handle timeout, provider rate limits, unavailable credentials, malformed output, and service errors with clear non-sensitive messages. Do not return provider internals, prompts containing private notes, or credentials in logs/responses.
7. Study notes may contain sensitive information. Document retention and avoid sending notes to a provider unless the user explicitly invokes generation and the configured provider's handling is disclosed.

## 8. Security, privacy, validation, and reliability requirements

- Reuse strict Pydantic request validation; add authorization and resource-level checks to every operation. Unknown fields are rejected where mutation schemas are used.
- Keep server authority over ownership, sharing grants, host rights, modes, team membership, deadlines, question order, correctness, scoring, and history writes.
- Preserve WebSocket origin checks, payload limits, event rate limits, guest reconnect tokens, and answer privacy. Add HTTP origin/CSRF protections appropriate to the chosen bearer-token transport, secure response headers, and per-route rate limits for creation, publication, sharing, and AI generation.
- Treat user content and AI output as untrusted plain text; escape at render time and avoid unsafe HTML insertion for authored fields. Do not expose answer keys in public library/search, pre-reveal snapshots, logs, error reports, or telemetry.
- Use parameterized database access, least-privilege database credentials, transaction boundaries, foreign keys, and unique constraints. Validate both at request boundary and persistence boundary.
- Collect only nickname and game data needed for play. Do not require email, legal name, birth date, or account profile. Establish retention/deletion rules for anonymous owners, quizzes, match histories, and AI notes before hosting durable user data.
- Do not log raw capability tokens, private notes, or full answer keys. Error payloads use stable codes and safe messages; detailed exceptions stay server-side with secrets redacted.
- Define cleanup for expired in-memory rooms, reconnect identities, and stale rate-limit keys. Durable quiz deletion and owner-grant revocation must not delete references needed by completed history unless the data retention policy says to anonymize/purge them.
- Use a migration process with backups and tested upgrades. Deployment health checks distinguish process readiness from database readiness; a database failure must not be reported as healthy for quiz-library features.

## 9. Deployment and operational constraints

- Keep the app deployable as one Python ASGI service serving both static frontend and WebSockets; use one worker while live state is in memory.
- Free-tier providers must be checked at deployment time for WebSocket support, sleep/restart policy, persistent storage, outbound traffic, database limits, and current pricing. Do not assume availability from documentation alone or create accounts without approval.
- A free ephemeral filesystem is not durable storage for SQLite quiz data. Use SQLite locally and for tests; hosted SQLite requires a verified persistent disk. Otherwise use PostgreSQL, only after checking whether a suitable free tier is actually available and its limits.
- The database can make quizzes/history durable while active matches remain ephemeral. A process restart ends active matches and invalidates their reconnect state; show a clear interruption message on the client. Durable active matches, multi-worker rooms, and horizontal scale are explicitly out of scope until a shared state/coordination design is approved.
- Never run multiple workers against the current in-memory room engine. PostgreSQL alone does not make WebSocket room state shared.
- No deployment, external account, paid service, public URL, or secret is part of implementation without separate explicit authorization.

## 10. Later-stage acceptance criteria and test matrix

All tests must run and report exact counts. A test that cannot execute is a blocker or unverified item, not a pass. Existing Python and Node tests remain regression gates. Browser verification must use independent browser contexts/sessions for real server connections; do not simulate opponents in UI tests.

### Stage 2 — Persistence foundation

- Fresh SQLite migration creates all expected tables and constraints; migration can be rerun safely.
- Save and retrieve a quiz across a process/application restart.
- Test ownership token hashing, token rotation/revocation, and that raw token is absent from database rows and logs.
- Test unique IDs/share grants, foreign keys, question position uniqueness, ranges, and atomic quiz/question writes.
- Test idempotent built-in question seeding and migration upgrade/downgrade where supported.
- PostgreSQL integration tests run only when an actual test service is configured; otherwise mark them unverified, not passed.

### Stage 3 — Quiz library API and permissions

- Create/edit/preview/publish/duplicate/delete/share/revoke workflows; reject invalid titles, lengths, choices, answer indexes, timing, categories, difficulty, and extra fields.
- Verify owner A cannot read/edit/delete owner B's private quiz; public visitor can browse/play published public quizzes but cannot edit or retrieve answer keys prematurely.
- Verify draft visibility, publication validation, stale version conflict behavior, share-link scope, expiry/revocation, and clear structured error codes.
- Verify search/category/difficulty filters exclude private and deleted quizzes and do not leak ownership secrets.

### Stage 4 — Authoring and library interface

- Browser workflows for create, edit, reorder, add/remove questions, validate, preview, save draft, publish, duplicate, delete, and share/revoke.
- Test empty, loading, validation, offline, permission-denied, and server-error states.
- Keyboard-only navigation, visible focus, accessible labels/errors, touch target size, reduced-motion preference, and contrast review.
- Verify no horizontal overflow and usable controls at representative mobile, tablet, and desktop widths. All visible controls have working actions.

### Stage 5 — Configurable quizzes and modes

- Verify filtered random selection has no repeats when enough questions exist and returns a clear pre-start error when insufficient; no silent fallback.
- Test Classic compatibility fixtures against current 10×15-second rules, score formula, Momentum eligibility/cutoff/award/audit log, reconnect, host transfer, answer privacy, timeout, rematch, and return-to-lobby behavior.
- Speed Challenge: timing boundary, score cap, no Momentum variant, timeout, and tie cases.
- Team Battle: eligibility, balanced assignment, no forged/team-switched identity, aggregate score, disconnect/reconnect, ties, and no fake members.
- Elimination: wrong/missing/late elimination, reconnect cannot reverse elimination, simultaneous last-player elimination, completion, ranks, and tie cases.
- Solo Practice: one-player eligibility, no rival/leader UI, authoritative timer/scoring, result summary, and no Momentum.
- For every mode, test unauthorized host controls, duplicate/stale events, simultaneous answers, progression, deadline equality, malformed mode configuration, and correct-answer privacy.
- Test one full ten-question Classic match and a complete match for every mode using server-driven clients; verify persistence of completed rules version and summary.

### Stage 6 — AI draft generation

- No API key: manual authoring, built-in questions, and multiplayer still work; AI action reports a clear configuration error.
- Mock provider timeout, rate limit, server error, malformed JSON/schema, wrong option count, invalid key, invalid category/difficulty, and oversized output.
- Verify valid results remain owner-only editable drafts, never auto-publish, carry AI provenance, and require explicit user review/approval.
- Verify provider credentials and study notes never appear in browser payloads, logs, or error messages.

### Stage 7 — Match history, analytics, and final verification

- Check stored match totals, accuracy, response-time calculations, category aggregates, personal bests, ties, and question review against known fixtures.
- Verify history write is atomic/idempotent and correct answers/explanations appear only at permitted reveal/history views.
- Test guest privacy, retention/deletion/anonymization behavior, and that no account or unnecessary identity fields are collected.
- Run all Python tests, original Node tests, frontend syntax checks, migration checks, and browser flows; report exact command/output summary and outstanding environment limitations.
- Verify README/setup/env/deployment documentation matches actual implementation and does not claim unavailable services or untested deployments.

## 11. Decisions, options, and unresolved product questions

The recommendations below make the design implementable while preserving room for explicit product decisions. No recommendation changes the existing Classic behavior.

| Question | Options | Recommendation for approval |
| --- | --- | --- |
| Published quiz minimum | Require 10 questions for every published quiz; or allow smaller quizzes for compatible modes | Allow one or more questions for draft/preview and non-Classic modes; require at least ten eligible questions to start the existing ten-question Classic preset. Reject insufficient selection before starting. |
| Guest quiz ownership persistence | Browser-stored owner capability; temporary local-only drafts; introduce accounts | Use an explicit opt-in, browser-held owner capability. No accounts in this expansion. Warn that losing the capability loses access. |
| Momentum Shift outside Classic | Apply everywhere; disable in some modes; define per-mode variants | Preserve exactly in Classic. Disable in Speed Challenge, Team Battle, Elimination, and Solo Practice as specified above unless separately approved. |
| Team assignment | Alternating server assignment; host chooses teams; random assignment | Alternate server-assigned players in join order, publish assignment before start, and prevent client-side team changes. Consider host assignment later if requested. |
| Elimination ending | Continue to one survivor; fixed question count; sudden death | Fixed configured count, stop when one remains, and share placement when all remaining players are eliminated together. No surprise sudden-death rounds. |
| Match history identity | No history; anonymous capability-linked stats; accounts | Store pseudonymous history only when the user opts into durable quiz/history features; no account requirement. Define retention period before shipping history. |
| Public quiz duplication | Permit copy of public quiz into an owned draft; forbid duplication | Permit duplication only for public quizzes, create a new draft/provenance record, and display the source attribution. Private share grants do not allow duplication by default. |
| AI provider | Single provider; configurable compatible providers; no AI | Use a small server-side provider interface with no default credential/provider requirement. Select a concrete provider only when credentials and policy are available. |
| Hosted database | Persistent-disk SQLite; PostgreSQL; no hosted persistence | Prefer PostgreSQL for hosted durable library/history if a verified eligible plan exists. Use SQLite locally. Do not use ephemeral hosted disk for durable SQLite data. |

Before implementing the corresponding stage, product approval is needed for the recommendations involving public quiz duplication/attribution, match-history retention, and Team Battle assignment. A provider choice is needed only if enabling live AI generation; it is not a prerequisite for the rest of the platform.

## 12. Mapping to the seven implementation stages

1. **Domain and mode specification:** This document; approve schema, permissions, persistence boundaries, and proposed rules.
2. **Persistence foundation:** Relational models/migrations, SQLite local setup, optional configured PostgreSQL, anonymous ownership grants, and persistence tests.
3. **Quiz library API:** Validated CRUD/lifecycle/share/search endpoints and authorization tests.
4. **Authoring/library UI:** Integrate editor and library into the current visual system; accessible browser checks.
5. **Game modes and quiz selection:** Add server-authoritative configuration/modes while preserving Classic and Momentum Shift compatibility.
6. **AI generation:** Add optional provider-backed editable drafts and failure tests; no key required for core product.
7. **History, analytics, docs, final verification:** Persist completed summaries, implement privacy-aware statistics/review, update operational docs, and run full regression/browser verification.

**Stage 1 exit:** Review and approve this specification and resolve any mode, persistence, ownership, or retention decisions needed before Stage 2. No runtime behavior is changed by completing this stage.
