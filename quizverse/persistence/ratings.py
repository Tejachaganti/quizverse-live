"""Competitive Elo ratings, multiplayer calculation, eligibility, and persistence."""

from __future__ import annotations

import base64
import binascii
import json
import math
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from quizverse.persistence.db import Database
from quizverse.persistence.models import (
    CompetitiveMatch,
    MatchParticipant,
    PlayerProfile,
    PlayerRating,
    RatingHistory,
    utc_now,
)


ELIGIBLE_MODES = {
    "classic",
    "speed_challenge",
    "elimination",
    "quiz_royale",
    "power_play",
    "bluff_master",
}

EXCLUDED_MODES = {
    "solo_practice",
    "escape_room",
    "team_battle",
}


@dataclass(frozen=True, slots=True)
class RatingConfig:
    starting_rating: int = 1000
    default_k_factor: int = 32
    provisional_threshold: int = 10
    algorithm_version: str = "elo_v1"
    scale: float = 400.0

    def __post_init__(self) -> None:
        if isinstance(self.starting_rating, bool) or not isinstance(self.starting_rating, int) or self.starting_rating < 0:
            raise ValueError("Starting rating must be a non-negative integer.")
        if isinstance(self.default_k_factor, bool) or not isinstance(self.default_k_factor, int) or self.default_k_factor <= 0:
            raise ValueError("The Elo K factor must be a positive integer.")
        if isinstance(self.provisional_threshold, bool) or not isinstance(self.provisional_threshold, int) or self.provisional_threshold < 1:
            raise ValueError("The provisional threshold must be a positive integer.")
        if not math.isfinite(float(self.scale)) or self.scale <= 0:
            raise ValueError("The Elo scale must be a finite positive number.")
        if not isinstance(self.algorithm_version, str) or not self.algorithm_version or len(self.algorithm_version) > 32:
            raise ValueError("The algorithm version must be 1 to 32 characters.")


DEFAULT_CONFIG = RatingConfig()


@dataclass(frozen=True, slots=True)
class ParticipantRatingState:
    player_id: str
    prior_rating: int
    placement: int
    outcome: str


@dataclass(frozen=True, slots=True)
class ParticipantRatingAdjustment:
    player_id: str
    prior_rating: int
    rating_delta: int
    new_rating: int
    actual_score: float
    expected_score: float
    algorithm_version: str


def calculate_multiplayer_elo(
    participants: Sequence[ParticipantRatingState],
    config: RatingConfig = DEFAULT_CONFIG,
    ineligible_pairs: set[frozenset[str]] | None = None,
) -> list[ParticipantRatingAdjustment]:
    """Calculate pairwise multiplayer Elo adjustments with deterministic zero-sum rounding.

    Formula:
      For player i and opponent j:
        E_ij = 1 / (1 + 10 ** ((R_j - R_i) / scale))
        S_ij = 1.0 (win, p_i < p_j), 0.5 (draw, p_i == p_j), 0.0 (loss, p_i > p_j)

      Fair-play anti-collusion:
        If pair {i, j} is in ineligible_pairs, their pairwise game is neutralized:
        S_ij = E_ij, so (S_ij - E_ij) = 0.0. This prevents rating transfer between
        the frequent/colluding pair while preserving the exact zero-sum invariant
        and allowing legitimate ratings against all other opponents.

      Averages against N-1 opponents:
        E_avg,i = sum_{j != i} E_ij / (N - 1)
        S_avg,i = sum_{j != i} S_ij / (N - 1)

      Raw delta:
        delta_raw,i = K * (S_avg,i - E_avg,i)

      Deterministic Largest-Remainder Rounding:
        Initial rounding r_i = round(delta_raw,i).
        Sum of raw deltas is mathematically zero; any non-zero residual sum(r_i) is
        due to integer rounding. Discrepancy is distributed to the participants with
        the largest rounding error in the corrective direction, with ties broken
        deterministically by player_id.
    """
    n = len(participants)
    if n < 2:
        raise ValueError("Multiplayer Elo calculation requires at least two participants.")
    player_ids: set[str] = set()
    for participant in participants:
        if not isinstance(participant.player_id, str) or not participant.player_id:
            raise ValueError("Elo participants must have a unique player ID.")
        if participant.player_id in player_ids:
            raise ValueError("Elo participants must have a unique player ID.")
        player_ids.add(participant.player_id)
        if isinstance(participant.prior_rating, bool) or not isinstance(participant.prior_rating, int) or participant.prior_rating < 0:
            raise ValueError("Prior ratings must be non-negative integers.")
        if isinstance(participant.placement, bool) or not isinstance(participant.placement, int) or participant.placement < 1:
            raise ValueError("Placements must be positive integers.")

    k = float(config.default_k_factor)
    scale = float(config.scale)

    raw_calculations: list[dict[str, Any]] = []
    for i, p_i in enumerate(participants):
        expected_sum = 0.0
        actual_sum = 0.0
        for j, p_j in enumerate(participants):
            if i == j:
                continue
            # Pairwise expected score for i against j
            diff = (p_j.prior_rating - p_i.prior_rating) / scale
            e_ij = 1.0 / (1.0 + math.pow(10.0, diff))
            expected_sum += e_ij

            # Pairwise actual score for i against j
            pair_key = frozenset([p_i.player_id, p_j.player_id])
            if ineligible_pairs and pair_key in ineligible_pairs:
                # Neutralize rating transfer between ineligible pair
                s_ij = e_ij
            elif p_i.placement < p_j.placement:
                s_ij = 1.0
            elif p_i.placement == p_j.placement:
                s_ij = 0.5
            else:
                s_ij = 0.0
            actual_sum += s_ij

        opponents = float(n - 1)
        e_avg = expected_sum / opponents
        s_avg = actual_sum / opponents
        raw_delta = k * (s_avg - e_avg)

        # Standard rounding
        rounded_delta = int(round(raw_delta))
        rounding_error = raw_delta - float(rounded_delta)

        raw_calculations.append({
            "player_id": p_i.player_id,
            "prior_rating": p_i.prior_rating,
            "raw_delta": raw_delta,
            "rounded_delta": rounded_delta,
            "rounding_error": rounding_error,
            "actual_score": round(s_avg, 6),
            "expected_score": round(e_avg, 6),
        })

    # Zero-sum discrepancy adjustment
    discrepancy = sum(item["rounded_delta"] for item in raw_calculations)

    if discrepancy > 0:
        # Sum was too high: decrement players rounded up the most (most negative rounding_error)
        sorted_for_decrement = sorted(
            raw_calculations,
            key=lambda x: (x["rounding_error"], x["player_id"]),
        )
        for idx in range(discrepancy):
            sorted_for_decrement[idx % n]["rounded_delta"] -= 1
    elif discrepancy < 0:
        # Sum was too low: increment players rounded down the most (most positive rounding_error)
        needed = abs(discrepancy)
        sorted_for_increment = sorted(
            raw_calculations,
            key=lambda x: (-x["rounding_error"], x["player_id"]),
        )
        for idx in range(needed):
            sorted_for_increment[idx % n]["rounded_delta"] += 1

    # Keep the configured zero rating floor without creating or destroying rating
    # points. Losses clipped at zero are removed from the winners' gains in a
    # deterministic order, so stored deltas still equal new_rating - prior_rating.
    floor_shortfall = 0
    for item in raw_calculations:
        if item["prior_rating"] + item["rounded_delta"] < 0:
            floor_delta = -item["prior_rating"]
            floor_shortfall += floor_delta - item["rounded_delta"]
            item["rounded_delta"] = floor_delta
    if floor_shortfall:
        gainers = sorted(
            (item for item in raw_calculations if item["rounded_delta"] > 0),
            key=lambda x: (-x["rounded_delta"], x["player_id"]),
        )
        for item in gainers:
            correction = min(floor_shortfall, item["rounded_delta"])
            item["rounded_delta"] -= correction
            floor_shortfall -= correction
            if not floor_shortfall:
                break
        assert floor_shortfall == 0, "Rating floor correction could not preserve zero-sum Elo."

    # Verify zero-sum invariant holds strictly
    final_sum = sum(item["rounded_delta"] for item in raw_calculations)
    assert final_sum == 0, f"Elo rounding invariant violated: sum={final_sum}"

    results: list[ParticipantRatingAdjustment] = []
    for item in raw_calculations:
        delta = item["rounded_delta"]
        new_rating = item["prior_rating"] + delta
        results.append(
            ParticipantRatingAdjustment(
                player_id=item["player_id"],
                prior_rating=item["prior_rating"],
                rating_delta=delta,
                new_rating=new_rating,
                actual_score=item["actual_score"],
                expected_score=item["expected_score"],
                algorithm_version=config.algorithm_version,
            )
        )

    return results


class RatingEligibilityChecker:
    """Authoritative checker for whether a match qualifies for competitive Elo rating."""

    @staticmethod
    def check_eligibility(
        match_data: dict[str, Any],
        session=None,
    ) -> tuple[bool, str | None]:
        # 1. Status check
        if match_data.get("status") != "completed":
            return False, "match_not_completed"

        # 2. Mode check
        mode = match_data.get("mode")
        if mode in EXCLUDED_MODES:
            return False, f"mode_excluded:{mode}"
        if mode not in ELIGIBLE_MODES:
            return False, f"mode_unsupported:{mode}"

        # 3. Result kind check
        result_kind = match_data.get("result_kind")
        if result_kind != "individual":
            return False, f"result_kind_excluded:{result_kind}"

        # 4. Participants presence & minimum count
        participants = match_data.get("participants")
        if not isinstance(participants, list) or len(participants) < 2:
            return False, "insufficient_participants"

        # 5. Persistent authenticated player identities
        player_ids: list[str] = []
        for p in participants:
            pid = p.get("player_id") if isinstance(p, dict) else getattr(p, "player_id", None)
            if pid is None or not isinstance(pid, str) or len(pid) != 32:
                return False, "unauthenticated_participants"
            player_ids.append(pid)

        # 6. Reject duplicate player identities within a match
        if len(player_ids) != len(set(player_ids)):
            return False, "duplicate_player_identity"

        # 7. Complete valid server-produced placements
        for p in participants:
            placement = p.get("placement") if isinstance(p, dict) else getattr(p, "placement", None)
            if placement is None or isinstance(placement, bool) or not isinstance(placement, int) or placement < 1:
                return False, "invalid_placements"

        # 8. Check player active profile status
        if session is not None:
            active_count = session.scalar(
                select(func.count())
                .select_from(PlayerProfile)
                .where(PlayerProfile.id.in_(player_ids), PlayerProfile.status == "active")
            )
            if active_count != len(player_ids):
                return False, "inactive_player_profile"

        # 9. Fair-play: Short-match detection (< 5.0 seconds for >= 5 questions/answers)
        started = match_data.get("started_at")
        completed = match_data.get("completed_at")
        if isinstance(started, datetime) and isinstance(completed, datetime):
            duration_sec = (completed - started).total_seconds()
            max_answers = max(
                (p.get("answer_count") or 0 if isinstance(p, dict) else getattr(p, "answer_count", 0) or 0)
                for p in participants
            ) if participants else 0
            if max_answers >= 5 and duration_sec < 5.0:
                return False, "match_duration_too_short"

        # 10. Fair-play: Repeated-opponent match frequency monitoring (rolling 24h limit: 5 rated matches)
        context = match_data.get("result_context") or {}
        is_official_fixture = bool(context.get("tournament_match_id") or context.get("league_fixture_id"))
        if not is_official_fixture and session is not None and len(player_ids) >= 2:
            since_time = datetime.now(UTC) - timedelta(hours=24)
            ineligible_pairs: set[frozenset[str]] = set()
            for idx_a in range(len(player_ids)):
                for idx_b in range(idx_a + 1, len(player_ids)):
                    pa, pb = player_ids[idx_a], player_ids[idx_b]
                    mp_a = aliased(MatchParticipant)
                    mp_b = aliased(MatchParticipant)
                    pair_count = session.scalar(
                        select(func.count(CompetitiveMatch.id))
                        .select_from(CompetitiveMatch)
                        .join(mp_a, (mp_a.match_id == CompetitiveMatch.id) & (mp_a.player_id == pa))
                        .join(mp_b, (mp_b.match_id == CompetitiveMatch.id) & (mp_b.player_id == pb))
                        .where(
                            CompetitiveMatch.is_rated == True,
                            CompetitiveMatch.completed_at >= since_time,
                        )
                    ) or 0
                    if pair_count >= 5:
                        ineligible_pairs.add(frozenset([pa, pb]))

            total_possible_pairs = (len(player_ids) * (len(player_ids) - 1)) // 2
            if len(ineligible_pairs) >= total_possible_pairs:
                return False, "pair_frequency_exceeded"

        return True, None


class RatingService:
    """Manages competitive player ratings, updates, history, and leaderboards."""

    def __init__(self, database: Database, config: RatingConfig = DEFAULT_CONFIG) -> None:
        self._database = database
        self.config = config

    def check_eligibility(
        self,
        match_data: dict[str, Any],
        session=None,
    ) -> tuple[bool, str | None]:
        return RatingEligibilityChecker.check_eligibility(match_data, session=session)

    def apply_match_ratings(
        self,
        session,
        match: CompetitiveMatch,
        participants: Sequence[dict[str, Any] | MatchParticipant],
    ) -> bool:
        """Applies global and mode rating adjustments atomically within an existing transaction.

        Idempotent: if the match is already rated, returns False immediately.
        """
        # Idempotency guard: check if match was already rated
        if match.is_rated:
            return False

        # Guard: check if rating_history already exists for this match
        existing_history = session.scalar(
            select(RatingHistory.id).where(RatingHistory.match_id == match.id).limit(1)
        )
        if existing_history is not None:
            match.is_rated = True
            if match.rated_at is None:
                match.rated_at = utc_now()
            return False

        # Extract participant details
        items: list[tuple[str, int, str]] = []
        for p in participants:
            pid = p["player_id"] if isinstance(p, dict) else p.player_id
            placement = p["placement"] if isinstance(p, dict) else p.placement
            outcome = p["outcome"] if isinstance(p, dict) else p.outcome
            if pid is None or placement is None:
                raise ValueError("Cannot rate match with missing participant player_id or placement.")
            items.append((str(pid), int(placement), str(outcome)))

        now = utc_now()
        scopes = ["global", f"mode:{match.mode}"]

        # Fair-play: detect pair-frequency ineligible pairs for multiplayer matches
        context = match.result_context or {}
        is_official_fixture = bool(context.get("tournament_match_id") or context.get("league_fixture_id"))
        ineligible_pairs: set[frozenset[str]] = set()
        pids = [pid for pid, _, _ in items]
        if not is_official_fixture and len(pids) > 2:
            since_time = datetime.now(UTC) - timedelta(hours=24)
            for idx_a in range(len(pids)):
                for idx_b in range(idx_a + 1, len(pids)):
                    pa, pb = pids[idx_a], pids[idx_b]
                    mp_a = aliased(MatchParticipant)
                    mp_b = aliased(MatchParticipant)
                    pair_count = session.scalar(
                        select(func.count(CompetitiveMatch.id))
                        .select_from(CompetitiveMatch)
                        .join(mp_a, (mp_a.match_id == CompetitiveMatch.id) & (mp_a.player_id == pa))
                        .join(mp_b, (mp_b.match_id == CompetitiveMatch.id) & (mp_b.player_id == pb))
                        .where(
                            CompetitiveMatch.is_rated == True,
                            CompetitiveMatch.completed_at >= since_time,
                        )
                    ) or 0
                    if pair_count >= 5:
                        ineligible_pairs.add(frozenset([pa, pb]))
            if ineligible_pairs:
                context_copy = dict(context)
                context_copy["ineligible_pairs"] = [sorted(list(p)) for p in ineligible_pairs]
                match.result_context = context_copy

        for scope in scopes:
            # 1. Fetch or initialize PlayerRating records for this scope
            existing_ratings = session.scalars(
                select(PlayerRating).where(
                    PlayerRating.player_id.in_(pids),
                    PlayerRating.rating_scope == scope,
                )
            ).all()
            rating_map: dict[str, PlayerRating] = {r.player_id: r for r in existing_ratings}

            # Prepare participant states for calculation
            calc_states: list[ParticipantRatingState] = []
            for pid, placement, outcome in items:
                pr = rating_map.get(pid)
                prior = pr.rating if pr else self.config.starting_rating
                calc_states.append(
                    ParticipantRatingState(
                        player_id=pid,
                        prior_rating=prior,
                        placement=placement,
                        outcome=outcome,
                    )
                )

            # 2. Compute multiplayer Elo adjustments with pair-level fair-play neutralization
            adjustments = calculate_multiplayer_elo(calc_states, self.config, ineligible_pairs=ineligible_pairs)

            # 3. Apply updates and record history
            for adj in adjustments:
                outcome = next(o for p, _, o in items if p == adj.player_id)
                pr = rating_map.get(adj.player_id)
                if pr is None:
                    pr = PlayerRating(
                        id=secrets.token_hex(16),
                        player_id=adj.player_id,
                        rating_scope=scope,
                        rating=adj.new_rating,
                        rated_match_count=1,
                        wins=1 if outcome == "win" else 0,
                        losses=1 if outcome == "loss" else 0,
                        draws=1 if outcome == "draw" else 0,
                        algorithm_version=self.config.algorithm_version,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(pr)
                    rating_map[adj.player_id] = pr
                else:
                    pr.rating = adj.new_rating
                    pr.rated_match_count += 1
                    if outcome == "win":
                        pr.wins += 1
                    elif outcome == "loss":
                        pr.losses += 1
                    elif outcome == "draw":
                        pr.draws += 1
                    pr.algorithm_version = self.config.algorithm_version
                    pr.updated_at = now

                # Insert immutable history entry
                history_entry = RatingHistory(
                    id=secrets.token_hex(16),
                    player_id=adj.player_id,
                    rating_scope=scope,
                    match_id=match.id,
                    prior_rating=adj.prior_rating,
                    rating_delta=adj.rating_delta,
                    new_rating=adj.new_rating,
                    actual_score=adj.actual_score,
                    expected_score=adj.expected_score,
                    algorithm_version=self.config.algorithm_version,
                    created_at=now,
                )
                session.add(history_entry)

        # 4. Mark match as successfully rated
        match.is_rated = True
        match.rated_at = now
        match.rating_ineligible_reason = None
        return True

    def reconcile_unrated_matches(self) -> list[str]:
        """Finds completed eligible matches that were persisted without ratings and rates them."""
        reconciled: list[str] = []
        with self._database.sessions.begin() as session:
            unrated = session.scalars(
                select(CompetitiveMatch)
                .where(
                    CompetitiveMatch.status == "completed",
                    CompetitiveMatch.is_rated == False,  # noqa: E712
                    CompetitiveMatch.rating_ineligible_reason.is_(None),
                )
                .order_by(CompetitiveMatch.completed_at.asc(), CompetitiveMatch.id.asc())
            ).all()

            for match in unrated:
                eligible, reason = self.check_eligibility(
                    {
                        "status": match.status,
                        "mode": match.mode,
                        "result_kind": match.result_kind,
                        "participants": [
                            {"player_id": p.player_id, "placement": p.placement, "outcome": p.outcome}
                            for p in match.participants
                        ],
                    },
                    session=session,
                )
                if eligible:
                    applied = self.apply_match_ratings(session, match, match.participants)
                    if applied:
                        reconciled.append(match.id)
                else:
                    match.rating_ineligible_reason = reason
        return reconciled

    def detect_unrated_eligible_matches(self) -> list[str]:
        """Auditable query to detect completed eligible matches missing rating history."""
        with self._database.session() as session:
            rows = session.scalars(
                select(CompetitiveMatch)
                .where(
                    CompetitiveMatch.status == "completed",
                    CompetitiveMatch.is_rated == False,  # noqa: E712
                )
            ).all()
            eligible_missing: list[str] = []
            for match in rows:
                eligible, _ = self.check_eligibility(
                    {
                        "status": match.status,
                        "mode": match.mode,
                        "result_kind": match.result_kind,
                        "participants": [
                            {"player_id": p.player_id, "placement": p.placement, "outcome": p.outcome}
                            for p in match.participants
                        ],
                    },
                    session=session,
                )
                if eligible:
                    eligible_missing.append(match.id)
            return eligible_missing

    def leaderboard(
        self,
        scope: str = "global",
        mode: str | None = None,
        limit: int = 20,
        offset: int = 0,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Returns stable, deterministically ordered public leaderboard entries."""
        resolved_scope = f"mode:{mode}" if scope == "mode" and mode else "global"

        with self._database.session() as session:
            # Base query: active non-anonymized profiles with rated_match_count > 0
            base_query = (
                select(PlayerRating, PlayerProfile)
                .join(PlayerProfile, PlayerProfile.id == PlayerRating.player_id)
                .where(
                    PlayerRating.rating_scope == resolved_scope,
                    PlayerRating.rated_match_count > 0,
                    PlayerProfile.status == "active",
                )
            )

            # Counts
            total = int(session.scalar(
                select(func.count()).select_from(PlayerRating)
                .join(PlayerProfile, PlayerProfile.id == PlayerRating.player_id)
                .where(
                    PlayerRating.rating_scope == resolved_scope,
                    PlayerRating.rated_match_count > 0,
                    PlayerProfile.status == "active",
                )
            ) or 0)

            established_count = int(session.scalar(
                select(func.count()).select_from(PlayerRating)
                .join(PlayerProfile, PlayerProfile.id == PlayerRating.player_id)
                .where(
                    PlayerRating.rating_scope == resolved_scope,
                    PlayerRating.rated_match_count >= self.config.provisional_threshold,
                    PlayerProfile.status == "active",
                )
            ) or 0)

            provisional_count = total - established_count

            # Cursor decode
            effective_offset = offset
            if cursor:
                try:
                    if len(cursor) > 128:
                        raise ValueError
                    decoded = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
                    cursor_offset = decoded.get("offset") if isinstance(decoded, dict) else None
                    if isinstance(cursor_offset, bool) or not isinstance(cursor_offset, int) or not 0 <= cursor_offset <= 10000:
                        raise ValueError
                    effective_offset = cursor_offset
                except (ValueError, binascii.Error) as error:
                    raise ValueError("Invalid leaderboard cursor.") from error

            # Deterministic ordering: rating DESC, rated_match_count DESC, created_at ASC, player_id ASC
            rows = session.execute(
                base_query.order_by(
                    PlayerRating.rating.desc(),
                    PlayerRating.rated_match_count.desc(),
                    PlayerRating.created_at.asc(),
                    PlayerProfile.id.asc(),
                )
                .limit(limit)
                .offset(effective_offset)
            ).all()

            entries = []
            for rank_idx, (rating_rec, profile) in enumerate(rows, start=effective_offset + 1):
                is_provisional = rating_rec.rated_match_count < self.config.provisional_threshold
                entries.append({
                    "rank": rank_idx,
                    "player_id": profile.id,
                    "display_name": profile.display_name,
                    "rating": rating_rec.rating,
                    "rated_match_count": rating_rec.rated_match_count,
                    "wins": rating_rec.wins,
                    "losses": rating_rec.losses,
                    "draws": rating_rec.draws,
                    "is_provisional": is_provisional,
                    "provisional": is_provisional,
                    "algorithm_version": rating_rec.algorithm_version,
                })

            has_more = (effective_offset + len(entries)) < total
            next_cursor = None
            if has_more:
                next_data = {"offset": effective_offset + len(entries)}
                next_cursor = base64.urlsafe_b64encode(
                    json.dumps(next_data).encode("utf-8")
                ).decode("ascii")

            return {
                "scope": resolved_scope,
                "mode": mode if scope == "mode" else None,
                "total": total,
                "established_total": established_count,
                "provisional_total": provisional_count,
                "limit": limit,
                "offset": effective_offset,
                "cursor": cursor,
                "next_cursor": next_cursor,
                "has_more": has_more,
                "as_of": utc_now(),
                "rankings": entries,
            }

    def player_ratings_summary(self, player_id: str) -> dict[str, Any]:
        """Returns the private profile ratings view: global rating, mode ratings, recent history."""
        with self._database.session() as session:
            profile = session.get(PlayerProfile, player_id)
            if profile is None or profile.status != "active":
                raise ValueError("Player profile is unavailable.")

            records = session.scalars(
                select(PlayerRating).where(PlayerRating.player_id == player_id)
            ).all()

            global_rating = None
            mode_ratings: dict[str, Any] = {}

            for r in records:
                entry = {
                    "rating": r.rating,
                    "rated_match_count": r.rated_match_count,
                    "wins": r.wins,
                    "losses": r.losses,
                    "draws": r.draws,
                    "is_provisional": r.rated_match_count < self.config.provisional_threshold,
                    "provisional": r.rated_match_count < self.config.provisional_threshold,
                    "algorithm_version": r.algorithm_version,
                    "updated_at": r.updated_at,
                }
                if r.rating_scope == "global":
                    global_rating = entry
                elif r.rating_scope.startswith("mode:"):
                    mode_name = r.rating_scope.partition(":")[2]
                    mode_ratings[mode_name] = entry

            # Recent rating history (last 10 adjustments)
            recent_rows = session.execute(
                select(RatingHistory, CompetitiveMatch.mode)
                .join(CompetitiveMatch, CompetitiveMatch.id == RatingHistory.match_id)
                .where(RatingHistory.player_id == player_id)
                .order_by(RatingHistory.created_at.desc(), RatingHistory.id.desc())
                .limit(10)
            ).all()

            recent_changes = [
                {
                    "id": h.id,
                    "scope": h.rating_scope,
                    "match_id": h.match_id,
                    "mode": mode,
                    "prior_rating": h.prior_rating,
                    "rating_delta": h.rating_delta,
                    "new_rating": h.new_rating,
                    "actual_score": h.actual_score,
                    "expected_score": h.expected_score,
                    "algorithm_version": h.algorithm_version,
                    "created_at": h.created_at,
                }
                for h, mode in recent_rows
            ]

            return {
                "player_id": player_id,
                "display_name": profile.display_name,
                "global_rating": global_rating,
                "mode_ratings": mode_ratings,
                "recent_changes": recent_changes,
            }

    def player_rating_history(
        self,
        player_id: str,
        scope: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Returns paginated, immutable rating history for an authenticated player."""
        with self._database.session() as session:
            profile = session.get(PlayerProfile, player_id)
            if profile is None or profile.status != "active":
                raise ValueError("Player profile is unavailable.")

            query = (
                select(RatingHistory, CompetitiveMatch.mode)
                .join(CompetitiveMatch, CompetitiveMatch.id == RatingHistory.match_id)
                .where(RatingHistory.player_id == player_id)
            )
            count_query = (
                select(func.count())
                .select_from(RatingHistory)
                .where(RatingHistory.player_id == player_id)
            )

            if scope:
                query = query.where(RatingHistory.rating_scope == scope)
                count_query = count_query.where(RatingHistory.rating_scope == scope)

            total = int(session.scalar(count_query) or 0)
            rows = session.execute(
                query.order_by(RatingHistory.created_at.desc(), RatingHistory.id.desc())
                .limit(limit)
                .offset(offset)
            ).all()

            history = [
                {
                    "id": h.id,
                    "scope": h.rating_scope,
                    "match_id": h.match_id,
                    "mode": mode,
                    "prior_rating": h.prior_rating,
                    "rating_delta": h.rating_delta,
                    "new_rating": h.new_rating,
                    "actual_score": h.actual_score,
                    "expected_score": h.expected_score,
                    "algorithm_version": h.algorithm_version,
                    "created_at": h.created_at,
                }
                for h, mode in rows
            ]

            return {
                "total": total,
                "limit": limit,
                "offset": offset,
                "history": history,
            }
