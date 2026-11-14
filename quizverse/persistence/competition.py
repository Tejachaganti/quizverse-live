"""Competitive intelligence services: advanced analytics, head-to-head comparison, honors cabinet, and fair-play monitoring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from quizverse.persistence.db import Database
from quizverse.persistence.models import (
    CompetitiveMatch,
    League,
    LeagueSeason,
    MatchParticipant,
    PlayerProfile,
    PlayerRating,
    RatingHistory,
    SeasonParticipant,
    Tournament,
    TournamentParticipant,
)


class CompetitionError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class AdvancedStatsService:
    """Calculates comprehensive individual competitive performance analytics."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def get_advanced_stats(self, player_id: str, mode: str | None = None) -> dict[str, Any]:
        with self._database.session() as session:
            profile = session.get(PlayerProfile, player_id)
            if profile is None or profile.status != "active":
                raise CompetitionError("player_not_found", "The specified player profile was not found or is inactive.", 404)

            # 1. Base individual competitive matches query (strictly excludes practice and cooperative)
            base_query = (
                select(
                    CompetitiveMatch.id,
                    CompetitiveMatch.mode,
                    CompetitiveMatch.completed_at,
                    MatchParticipant.final_score,
                    MatchParticipant.correct_count,
                    MatchParticipant.answer_count,
                    MatchParticipant.placement,
                    MatchParticipant.outcome,
                    MatchParticipant.forfeited,
                )
                .select_from(MatchParticipant)
                .join(CompetitiveMatch, CompetitiveMatch.id == MatchParticipant.match_id)
                .where(
                    MatchParticipant.player_id == player_id,
                    CompetitiveMatch.status == "completed",
                    CompetitiveMatch.result_kind == "individual",
                    MatchParticipant.outcome.in_(["win", "loss", "draw"]),
                )
            )

            if mode:
                base_query = base_query.where(CompetitiveMatch.mode == mode)

            # Order by completed_at desc, id desc for chronological analysis
            rows = session.execute(
                base_query.order_by(CompetitiveMatch.completed_at.desc(), CompetitiveMatch.id.desc())
            ).all()

            total_matches = len(rows)
            wins = sum(1 for r in rows if r.outcome == "win")
            losses = sum(1 for r in rows if r.outcome == "loss")
            draws = sum(1 for r in rows if r.outcome == "draw")
            win_pct = round((wins / total_matches) * 100.0, 1) if total_matches > 0 else 0.0

            total_correct = sum((r.correct_count or 0) for r in rows)
            total_answered = sum((r.answer_count or 0) for r in rows)
            accuracy_pct = round((total_correct / total_answered) * 100.0, 1) if total_answered > 0 else 0.0

            # 2. Streaks and recent form (rows are in reverse chronological order: newest first)
            outcomes = [r.outcome for r in rows]
            recent_form_5 = outcomes[:5]
            recent_form_10 = outcomes[:10]

            if not outcomes:
                current_streak = {"type": "none", "count": 0}
            else:
                first_type = outcomes[0]
                streak_count = 0
                for out in outcomes:
                    if out == first_type:
                        streak_count += 1
                    else:
                        break
                current_streak = {"type": first_type, "count": streak_count}

            # Longest win streak: traverse chronological order (oldest to newest)
            longest_win_streak = 0
            cur_run = 0
            for out in reversed(outcomes):
                if out == "win":
                    cur_run += 1
                    if cur_run > longest_win_streak:
                        longest_win_streak = cur_run
                else:
                    cur_run = 0

            # Forfeit monitoring: last 10 competitive matches
            forfeit_flags = [bool(r.forfeited) for r in rows[:10]]
            forfeit_count = sum(forfeit_flags)
            in_window = len(forfeit_flags)
            forfeit_rate_pct = round((forfeit_count / in_window) * 100.0, 1) if in_window > 0 else 0.0
            forfeit_warning = bool(forfeit_count >= 3 and in_window >= 5)

            # 3. Mode performance breakdown
            mode_groups: dict[str, dict[str, Any]] = {}
            for r in rows:
                entry = mode_groups.setdefault(
                    r.mode,
                    {
                        "mode": r.mode,
                        "matches": 0,
                        "wins": 0,
                        "losses": 0,
                        "draws": 0,
                        "total_score": 0,
                        "best_score": 0,
                        "total_correct": 0,
                        "total_answered": 0,
                    },
                )
                entry["matches"] += 1
                if r.outcome == "win":
                    entry["wins"] += 1
                elif r.outcome == "loss":
                    entry["losses"] += 1
                elif r.outcome == "draw":
                    entry["draws"] += 1
                score = r.final_score or 0
                entry["total_score"] += score
                if score > entry["best_score"]:
                    entry["best_score"] = score
                entry["total_correct"] += r.correct_count or 0
                entry["total_answered"] += r.answer_count or 0

            mode_performance = []
            for m_key in sorted(mode_groups.keys()):
                mg = mode_groups[m_key]
                m_count = mg["matches"]
                m_wins = mg["wins"]
                m_ans = mg["total_answered"]
                m_cor = mg["total_correct"]
                mode_performance.append({
                    "mode": m_key,
                    "matches": m_count,
                    "wins": m_wins,
                    "losses": mg["losses"],
                    "draws": mg["draws"],
                    "win_rate_pct": round((m_wins / m_count) * 100.0, 1) if m_count > 0 else 0.0,
                    "avg_score": round(mg["total_score"] / m_count, 1) if m_count > 0 else 0.0,
                    "best_score": mg["best_score"],
                    "accuracy_pct": round((m_cor / m_ans) * 100.0, 1) if m_ans > 0 else 0.0,
                })

            # 4. Ratings summary & peak rating
            ratings_records = session.scalars(
                select(PlayerRating).where(PlayerRating.player_id == player_id)
            ).all()

            global_rating_rec = next((r for r in ratings_records if r.rating_scope == "global"), None)
            global_current = global_rating_rec.rating if global_rating_rec else 1000
            global_rated_count = global_rating_rec.rated_match_count if global_rating_rec else 0
            is_provisional = global_rated_count < 10

            peak_global = session.scalar(
                select(func.max(RatingHistory.new_rating))
                .where(
                    RatingHistory.player_id == player_id,
                    RatingHistory.rating_scope == "global",
                )
            )
            peak_rating = peak_global if peak_global is not None else global_current

            # Mode ratings dict
            mode_ratings = {
                r.rating_scope.partition(":")[2]: {
                    "rating": r.rating,
                    "rated_matches": r.rated_match_count,
                    "wins": r.wins,
                    "losses": r.losses,
                    "draws": r.draws,
                }
                for r in ratings_records
                if r.rating_scope.startswith("mode:")
            }

            return {
                "player_id": player_id,
                "display_name": profile.display_name,
                "total_matches": total_matches,
                "wins": wins,
                "losses": losses,
                "draws": draws,
                "win_percentage": win_pct,
                "current_streak": current_streak,
                "longest_win_streak": longest_win_streak,
                "recent_form": {
                    "last_5": recent_form_5,
                    "last_10": recent_form_10,
                },
                "total_correct": total_correct,
                "total_answered": total_answered,
                "accuracy_pct": accuracy_pct,
                "peak_rating": peak_rating,
                "current_rating": global_current,
                "is_provisional": is_provisional,
                "rated_match_count": global_rated_count,
                "mode_performance": mode_performance,
                "mode_ratings": mode_ratings,
                "forfeit_monitoring": {
                    "forfeited_count": forfeit_count,
                    "forfeit_rate_pct": forfeit_rate_pct,
                    "forfeit_warning": forfeit_warning,
                },
            }


class HeadToHeadService:
    """Calculates pairwise head-to-head records and historical encounters between two players."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def get_head_to_head(
        self,
        player_one_id: str,
        player_two_id: str,
        mode: str | None = None,
    ) -> dict[str, Any]:
        if player_one_id == player_two_id:
            raise CompetitionError("invalid_opponent", "A player cannot compare head-to-head statistics against themselves.", 400)

        with self._database.session() as session:
            p1 = session.get(PlayerProfile, player_one_id)
            p2 = session.get(PlayerProfile, player_two_id)
            if p1 is None or p1.status != "active":
                raise CompetitionError("player_not_found", f"Player '{player_one_id}' was not found or is inactive.", 404)
            if p2 is None or p2.status != "active":
                raise CompetitionError("player_not_found", f"Player '{player_two_id}' was not found or is inactive.", 404)

            # Query shared matches where both participants played
            mp1 = aliased(MatchParticipant)
            mp2 = aliased(MatchParticipant)

            stmt = (
                select(
                    CompetitiveMatch.id,
                    CompetitiveMatch.mode,
                    CompetitiveMatch.completed_at,
                    mp1.final_score.label("p1_score"),
                    mp1.placement.label("p1_placement"),
                    mp1.outcome.label("p1_outcome"),
                    mp2.final_score.label("p2_score"),
                    mp2.placement.label("p2_placement"),
                    mp2.outcome.label("p2_outcome"),
                )
                .select_from(CompetitiveMatch)
                .join(mp1, (mp1.match_id == CompetitiveMatch.id) & (mp1.player_id == player_one_id))
                .join(mp2, (mp2.match_id == CompetitiveMatch.id) & (mp2.player_id == player_two_id))
                .where(
                    CompetitiveMatch.status == "completed",
                    CompetitiveMatch.result_kind.in_(["individual", "team"]),
                )
            )

            if mode:
                stmt = stmt.where(CompetitiveMatch.mode == mode)

            stmt = stmt.order_by(CompetitiveMatch.completed_at.desc(), CompetitiveMatch.id.desc())
            rows = session.execute(stmt).all()

            total_encounters = len(rows)
            p1_wins = 0
            p2_wins = 0
            draws = 0
            p1_total_score = 0
            p2_total_score = 0
            mode_map: dict[str, dict[str, int]] = {}
            recent_encounters: list[dict[str, Any]] = []

            for r in rows:
                p1_s = r.p1_score or 0
                p2_s = r.p2_score or 0
                p1_total_score += p1_s
                p2_total_score += p2_s

                # Determine head-to-head outcome between the two players
                if r.p1_placement is not None and r.p2_placement is not None:
                    if r.p1_placement < r.p2_placement:
                        outcome = "player_one_win"
                        winner_id = player_one_id
                        p1_wins += 1
                    elif r.p2_placement < r.p1_placement:
                        outcome = "player_two_win"
                        winner_id = player_two_id
                        p2_wins += 1
                    else:
                        outcome = "draw"
                        winner_id = None
                        draws += 1
                else:
                    if p1_s > p2_s:
                        outcome = "player_one_win"
                        winner_id = player_one_id
                        p1_wins += 1
                    elif p2_s > p1_s:
                        outcome = "player_two_win"
                        winner_id = player_two_id
                        p2_wins += 1
                    else:
                        outcome = "draw"
                        winner_id = None
                        draws += 1

                # Mode breakdown
                m_entry = mode_map.setdefault(r.mode, {"mode": r.mode, "encounters": 0, "player_one_wins": 0, "player_two_wins": 0, "draws": 0})
                m_entry["encounters"] += 1
                if outcome == "player_one_win":
                    m_entry["player_one_wins"] += 1
                elif outcome == "player_two_win":
                    m_entry["player_two_wins"] += 1
                else:
                    m_entry["draws"] += 1

                # Record encounter
                if len(recent_encounters) < 20:
                    recent_encounters.append({
                        "match_id": r.id,
                        "mode": r.mode,
                        "completed_at": r.completed_at,
                        "player_one_score": p1_s,
                        "player_two_score": p2_s,
                        "outcome": outcome,
                        "winner_id": winner_id,
                    })

            p1_win_pct = round((p1_wins / total_encounters) * 100.0, 1) if total_encounters > 0 else 0.0
            p2_win_pct = round((p2_wins / total_encounters) * 100.0, 1) if total_encounters > 0 else 0.0
            p1_avg_score = round(p1_total_score / total_encounters, 1) if total_encounters > 0 else 0.0
            p2_avg_score = round(p2_total_score / total_encounters, 1) if total_encounters > 0 else 0.0
            score_diff = p1_total_score - p2_total_score

            # Fetch current ratings
            r1 = session.scalar(select(PlayerRating.rating).where(PlayerRating.player_id == player_one_id, PlayerRating.rating_scope == "global"))
            r2 = session.scalar(select(PlayerRating.rating).where(PlayerRating.player_id == player_two_id, PlayerRating.rating_scope == "global"))
            p1_rating = r1 if r1 is not None else 1000
            p2_rating = r2 if r2 is not None else 1000

            return {
                "player_one": {
                    "id": p1.id,
                    "display_name": p1.display_name,
                    "rating": p1_rating,
                },
                "player_two": {
                    "id": p2.id,
                    "display_name": p2.display_name,
                    "rating": p2_rating,
                },
                "elo_difference": p1_rating - p2_rating,
                "total_encounters": total_encounters,
                "player_one_wins": p1_wins,
                "player_two_wins": p2_wins,
                "draws": draws,
                "player_one_win_pct": p1_win_pct,
                "player_two_win_pct": p2_win_pct,
                "player_one_avg_score": p1_avg_score,
                "player_two_avg_score": p2_avg_score,
                "score_difference": score_diff,
                "mode_breakdown": sorted(mode_map.values(), key=lambda x: str(x["mode"])),
                "recent_encounters": recent_encounters,
            }


class HonorsService:
    """Manages player honors, trophies, and tournament/league accolades."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def get_player_honors(self, player_id: str) -> dict[str, Any]:
        with self._database.session() as session:
            profile = session.get(PlayerProfile, player_id)
            if profile is None or profile.status != "active":
                raise CompetitionError("player_not_found", "The specified player profile was not found or is inactive.", 404)

            # 1. Completed Tournaments
            tourn_rows = session.execute(
                select(Tournament, TournamentParticipant)
                .join(TournamentParticipant, TournamentParticipant.tournament_id == Tournament.id)
                .where(
                    TournamentParticipant.player_id == player_id,
                    Tournament.status == "completed",
                    TournamentParticipant.status.in_(["champion", "runner_up"]) | TournamentParticipant.final_placement.in_([1, 2]),
                )
                .order_by(Tournament.completed_at.desc(), Tournament.id.desc())
            ).all()

            # 2. Completed League Seasons
            season_rows = session.execute(
                select(League, LeagueSeason, SeasonParticipant)
                .join(LeagueSeason, LeagueSeason.league_id == League.id)
                .join(SeasonParticipant, SeasonParticipant.season_id == LeagueSeason.id)
                .where(
                    SeasonParticipant.player_id == player_id,
                    LeagueSeason.status == "completed",
                    SeasonParticipant.final_placement.in_([1, 2, 3]),
                )
                .order_by(LeagueSeason.completed_at.desc(), LeagueSeason.id.desc())
            ).all()

            trophies: list[dict[str, Any]] = []
            tourn_champs = 0
            tourn_runners = 0
            league_champs = 0
            league_podiums = 0

            for tourn, part in tourn_rows:
                placement = part.final_placement or (1 if part.status == "champion" else 2)
                tier = "gold" if placement == 1 else "silver"
                title = "Tournament Champion" if placement == 1 else "Tournament Runner-Up"
                if placement == 1:
                    tourn_champs += 1
                else:
                    tourn_runners += 1

                trophies.append({
                    "id": f"tournament_{tourn.id}",
                    "category": "tournament",
                    "tier": tier,
                    "title": title,
                    "event_id": tourn.id,
                    "event_name": tourn.name,
                    "mode": tourn.mode,
                    "placement": placement,
                    "completed_at": tourn.completed_at,
                })

            for league, season, part in season_rows:
                placement = part.final_placement or 1
                if placement == 1:
                    tier = "gold"
                    title = "League Champion"
                    league_champs += 1
                elif placement == 2:
                    tier = "silver"
                    title = "League Runner-Up"
                else:
                    tier = "bronze"
                    title = "League 3rd Place"
                league_podiums += 1

                trophies.append({
                    "id": f"season_{season.id}",
                    "category": "league",
                    "tier": tier,
                    "title": title,
                    "event_id": season.id,
                    "event_name": f"{league.name} — {season.name}",
                    "mode": league.mode,
                    "placement": placement,
                    "completed_at": season.completed_at,
                })

            # Sort combined trophies by completed_at desc
            trophies.sort(key=lambda x: x["completed_at"] or datetime.min.replace(tzinfo=UTC), reverse=True)

            return {
                "player_id": player_id,
                "display_name": profile.display_name,
                "tournament_championships": tourn_champs,
                "tournament_runner_ups": tourn_runners,
                "league_championships": league_champs,
                "league_podiums": league_podiums,
                "total_honors": len(trophies),
                "trophies": trophies,
            }
