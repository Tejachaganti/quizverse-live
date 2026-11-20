import asyncio
from dataclasses import FrozenInstanceError

import pytest

from quizverse.engine import (
    COUNTDOWN_SECONDS,
    QUESTION_COUNT,
    QUESTION_SECONDS,
    RECONNECT_SECONDS,
    ROOM_CAPACITY,
    GameEngine,
    GameError,
    Phase,
)
from quizverse.modes import (
    CLASSIC_CONFIG,
    SPEED_CHALLENGE_CONFIG,
    TEAM_BATTLE_CONFIG,
    ELIMINATION_CONFIG,
    SOLO_PRACTICE_CONFIG,
    GameMode,
    resolve_mode,
)


class FakeClock:
    def __init__(self) -> None:
        self.monotonic_now = 100.0
        self.wall_now = 1_800_000_000.0

    def monotonic(self) -> float:
        return self.monotonic_now

    def wall(self) -> float:
        return self.wall_now

    def advance(self, seconds: float) -> None:
        self.monotonic_now += seconds
        self.wall_now += seconds


@pytest.fixture
def setup_game():
    clock = FakeClock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    host = asyncio.run(engine.create_room("Nova"))
    guest = asyncio.run(engine.join_room(host["state"]["code"], "Orbit"))
    return engine, clock, host, guest


def test_secure_codes_names_and_capacity(setup_game):
    engine, _, host, guest = setup_game
    code = host["state"]["code"]
    assert len(code) == 6
    assert set(code) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
    for index in range(6):
        asyncio.run(engine.join_room(code, f"Player {index}"))
    with pytest.raises(GameError, match="full"):
        asyncio.run(engine.join_room(code, "Ninth"))
    for name in ("", "x" * 19, "<script>", "bad\nname"):
        with pytest.raises(GameError):
            asyncio.run(engine.create_room(name))
    assert len(engine._rooms[code].players) == 8
    assert len(guest["token"]) >= 40
    assert guest["token"] not in repr(engine._rooms[code].players[guest["player_id"]])


def test_host_permissions_and_membership(setup_game):
    engine, _, host, guest = setup_game
    code = host["state"]["code"]
    with pytest.raises(GameError, match="Only the host"):
        asyncio.run(engine.start_game(code, guest["player_id"]))
    with pytest.raises(GameError, match="expired"):
        asyncio.run(engine.snapshot(code, "somebody-else"))
    with pytest.raises(GameError, match="six letters"):
        asyncio.run(engine.join_room("bad", "Other"))
    started = asyncio.run(engine.start_game(code, host["player_id"]))
    assert started["phase"] == Phase.COUNTDOWN
    assert started["deadline"] == int((1_800_000_000 + COUNTDOWN_SECONDS) * 1000)


def test_room_uses_immutable_classic_config_and_rejects_unsupported_modes() -> None:
    engine = GameEngine()
    created = asyncio.run(engine.create_room("Nova"))
    room = engine._rooms[created["state"]["code"]]

    assert resolve_mode() is CLASSIC_CONFIG
    assert resolve_mode("classic") is CLASSIC_CONFIG
    assert room.match_config is CLASSIC_CONFIG
    assert room.match_config.mode is GameMode.CLASSIC
    assert created["state"]["mode"] == "classic"
    assert CLASSIC_CONFIG.min_players == 2
    assert CLASSIC_CONFIG.max_players == ROOM_CAPACITY
    assert CLASSIC_CONFIG.question_count == QUESTION_COUNT
    assert CLASSIC_CONFIG.countdown_seconds == COUNTDOWN_SECONDS
    assert CLASSIC_CONFIG.question_seconds == QUESTION_SECONDS
    assert CLASSIC_CONFIG.reveal_seconds == 3.0
    assert CLASSIC_CONFIG.base_points == 1000
    assert CLASSIC_CONFIG.speed_bonus_max == 500
    with pytest.raises(FrozenInstanceError):
        CLASSIC_CONFIG.base_points = 1

    invalid_engine = GameEngine()
    with pytest.raises(GameError, match="Unsupported game mode") as error:
        asyncio.run(invalid_engine.create_room("Nova", mode="not_a_mode"))
    assert error.value.code == "invalid_mode"
    assert asyncio.run(invalid_engine.room_codes()) == ()


@pytest.mark.parametrize(
    ("elapsed", "expected_bonus"),
    [(0.0, 500), (5.0, 250), (9.99, 1)],
)
def test_speed_challenge_scoring_timer_and_momentum_rules(elapsed: float, expected_bonus: int) -> None:
    clock = FakeClock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    host = asyncio.run(engine.create_room("Nova", mode="speed_challenge"))
    guest = asyncio.run(engine.join_room(host["state"]["code"], "Orbit"))
    code = host["state"]["code"]
    room = engine._rooms[code]

    assert room.match_config is SPEED_CHALLENGE_CONFIG
    assert SPEED_CHALLENGE_CONFIG.mode is GameMode.SPEED_CHALLENGE
    assert SPEED_CHALLENGE_CONFIG.min_players == 2
    assert SPEED_CHALLENGE_CONFIG.max_players == 8
    assert SPEED_CHALLENGE_CONFIG.question_count == 10
    assert SPEED_CHALLENGE_CONFIG.question_seconds == 10.0
    assert SPEED_CHALLENGE_CONFIG.base_points == 1000
    assert SPEED_CHALLENGE_CONFIG.speed_bonus_max == 500
    assert SPEED_CHALLENGE_CONFIG.momentum_enabled is False

    asyncio.run(engine.start_game(code, host["player_id"]))
    clock.advance(COUNTDOWN_SECONDS)
    asyncio.run(engine.advance_due())
    live = asyncio.run(engine.snapshot(code, host["player_id"]))
    assert live["mode"] == "speed_challenge"
    assert live["question_seconds"] == 10.0
    assert live["deadline"] - live["server_now"] == 10000
    assert live["momentum"]["enabled"] is False
    assert live["momentum"]["eligible"] is False

    correct = room.questions[0].answer
    clock.advance(elapsed)
    asyncio.run(engine.submit_answer(code, host["player_id"], correct))
    with pytest.raises(GameError, match="already locked"):
        asyncio.run(engine.submit_answer(code, host["player_id"], correct))
    asyncio.run(engine.submit_answer(code, guest["player_id"], (correct + 1) % 4))
    reveal = asyncio.run(engine.snapshot(code, host["player_id"]))
    assert reveal["answer_feedback"]["speed_bonus"] == expected_bonus
    assert reveal["answer_feedback"]["momentum_bonus"] == 0
    assert next(player for player in reveal["players"] if player["id"] == host["player_id"])["score"] == 1000 + expected_bonus
    assert room.momentum_adjustments == []


def test_speed_challenge_rejects_deadline_answer_and_rematch_keeps_mode() -> None:
    clock = FakeClock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    host = asyncio.run(engine.create_room("Nova", mode="speed_challenge"))
    guest = asyncio.run(engine.join_room(host["state"]["code"], "Orbit"))
    code = host["state"]["code"]
    asyncio.run(engine.start_game(code, host["player_id"]))
    clock.advance(COUNTDOWN_SECONDS)
    asyncio.run(engine.advance_due())
    clock.advance(SPEED_CHALLENGE_CONFIG.question_seconds)

    with pytest.raises(GameError) as error:
        asyncio.run(engine.submit_answer(code, guest["player_id"], 0))
    assert error.value.code == "stale_answer"
    assert error.value.state_changed is True
    reveal = asyncio.run(engine.snapshot(code, guest["player_id"]))
    assert reveal["phase"] == Phase.REVEAL
    assert reveal["answered"] is False
    assert all(player["score"] == 0 for player in reveal["players"])

    # Force the remaining nine rounds to expire, then confirm rematches keep
    # the room's server-selected ruleset.
    clock.advance(SPEED_CHALLENGE_CONFIG.reveal_seconds)
    asyncio.run(engine.advance_due())
    for _ in range(9):
        clock.advance(SPEED_CHALLENGE_CONFIG.question_seconds + SPEED_CHALLENGE_CONFIG.reveal_seconds)
        asyncio.run(engine.advance_due())
    results = asyncio.run(engine.snapshot(code, host["player_id"]))
    assert results["phase"] == Phase.RESULTS
    lobby = asyncio.run(engine.set_lobby(code, host["player_id"], rematch=True))
    assert lobby["phase"] == Phase.WAITING
    assert lobby["mode"] == "speed_challenge"
    asyncio.run(engine.start_game(code, host["player_id"]))
    clock.advance(SPEED_CHALLENGE_CONFIG.countdown_seconds)
    asyncio.run(engine.advance_due())
    restarted = asyncio.run(engine.snapshot(code, host["player_id"]))
    assert restarted["mode"] == "speed_challenge"
    assert restarted["deadline"] - restarted["server_now"] == 10000


def create_team_room(player_count: int = 4):
    clock = FakeClock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    host = asyncio.run(engine.create_room("Player 1", mode="team_battle"))
    players = [host]
    for number in range(2, player_count + 1):
        players.append(asyncio.run(engine.join_room(host["state"]["code"], f"Player {number}")))
    return engine, clock, players


def test_team_battle_configuration_and_legacy_classic_regression() -> None:
    engine = GameEngine()
    classic = asyncio.run(engine.create_room("Classic"))
    explicit_classic = asyncio.run(engine.create_room("Classic Two", mode="classic"))
    assert classic["state"]["mode"] == explicit_classic["state"]["mode"] == "classic"
    assert resolve_mode("team_battle") is TEAM_BATTLE_CONFIG
    assert TEAM_BATTLE_CONFIG.mode is GameMode.TEAM_BATTLE
    assert TEAM_BATTLE_CONFIG.min_players == 4
    assert TEAM_BATTLE_CONFIG.max_players == 8
    assert TEAM_BATTLE_CONFIG.question_count == 10
    assert TEAM_BATTLE_CONFIG.countdown_seconds == 3.0
    assert TEAM_BATTLE_CONFIG.question_seconds == 15.0
    assert TEAM_BATTLE_CONFIG.reveal_seconds == 3.0
    assert TEAM_BATTLE_CONFIG.base_points == 1000
    assert TEAM_BATTLE_CONFIG.speed_bonus_max == 500
    assert TEAM_BATTLE_CONFIG.momentum_enabled is False
    assert TEAM_BATTLE_CONFIG.even_player_count is True
    with pytest.raises(FrozenInstanceError):
        TEAM_BATTLE_CONFIG.question_seconds = 1


@pytest.mark.parametrize(("roster_size", "eligible"), [(3, False), (4, True), (5, False), (6, True), (7, False), (8, True)])
def test_team_battle_start_requires_four_to_eight_even_roster(roster_size: int, eligible: bool) -> None:
    engine, _, players = create_team_room(roster_size)
    host_id = players[0]["player_id"]
    state = asyncio.run(engine.snapshot(players[0]["state"]["code"], host_id))
    assert state["can_start"] is eligible
    if eligible:
        started = asyncio.run(engine.start_game(players[0]["state"]["code"], host_id))
        assert started["phase"] == Phase.COUNTDOWN
    else:
        with pytest.raises(GameError) as error:
            asyncio.run(engine.start_game(players[0]["state"]["code"], host_id))
        assert error.value.code == "invalid_team_roster"


def test_team_battle_rejects_insufficient_connected_players() -> None:
    engine, _, players = create_team_room(4)
    code = players[0]["state"]["code"]
    asyncio.run(engine.disconnect(code, players[-1]["player_id"]))
    state = asyncio.run(engine.snapshot(code, players[0]["player_id"]))
    assert state["can_start"] is False
    assert "four players must be connected" in state["can_start_reason"]
    with pytest.raises(GameError) as error:
        asyncio.run(engine.start_game(code, players[0]["player_id"]))
    assert error.value.code == "not_enough_players"


def test_team_battle_room_rejects_ninth_player() -> None:
    engine, _, players = create_team_room(8)
    with pytest.raises(GameError) as error:
        asyncio.run(engine.join_room(players[0]["state"]["code"], "Ninth"))
    assert error.value.code == "room_full"


def test_team_battle_assignment_is_balanced_frozen_and_reconnect_stable() -> None:
    engine, _, players = create_team_room(8)
    code = players[0]["state"]["code"]
    lobby = asyncio.run(engine.snapshot(code, players[0]["player_id"]))
    assert [player["team_id"] for player in lobby["players"]] == ["violet", "cyan"] * 4
    started = asyncio.run(engine.start_game(code, players[0]["player_id"]))
    assert [player["team_id"] for player in started["players"]] == ["violet", "cyan"] * 4
    assert {team["score"] for team in started["teams"]} == {0}

    player = players[2]
    asyncio.run(engine.disconnect(code, player["player_id"]))
    resumed = asyncio.run(engine.reconnect(player["token"]))
    assert next(entry for entry in resumed["state"]["players"] if entry["id"] == player["player_id"])["team_id"] == "violet"


def test_team_battle_unchanged_roster_rematch_preserves_assignments() -> None:
    engine, clock, players = create_team_room(4)
    code = players[0]["state"]["code"]
    asyncio.run(engine.start_game(code, players[0]["player_id"]))
    clock.advance(TEAM_BATTLE_CONFIG.countdown_seconds)
    asyncio.run(engine.advance_due())
    for _ in range(TEAM_BATTLE_CONFIG.question_count):
        clock.advance(TEAM_BATTLE_CONFIG.question_seconds)
        asyncio.run(engine.advance_due())
        clock.advance(TEAM_BATTLE_CONFIG.reveal_seconds)
        asyncio.run(engine.advance_due())
    room = engine._rooms[code]
    original = {player.id: player.team_id for player in room.players.values()}
    assert asyncio.run(engine.snapshot(code, players[0]["player_id"]))["phase"] == Phase.RESULTS
    lobby = asyncio.run(engine.set_lobby(code, players[0]["player_id"], rematch=True))
    assert lobby["phase"] == Phase.WAITING
    restarted = asyncio.run(engine.start_game(code, players[0]["player_id"]))
    assert {player["id"]: player["team_id"] for player in restarted["players"]} == original


def test_team_battle_scores_players_and_aggregates_correct_answers_and_time() -> None:
    engine, clock, players = create_team_room(4)
    code = players[0]["state"]["code"]
    asyncio.run(engine.start_game(code, players[0]["player_id"]))
    clock.advance(3.0)
    asyncio.run(engine.advance_due())
    correct = engine._rooms[code].questions[0].answer

    clock.advance(1.0)
    asyncio.run(engine.submit_answer(code, players[0]["player_id"], correct))
    with pytest.raises(GameError) as duplicate:
        asyncio.run(engine.submit_answer(code, players[0]["player_id"], correct))
    assert duplicate.value.code == "duplicate_answer"
    asyncio.run(engine.submit_answer(code, players[1]["player_id"], (correct + 1) % 4))
    clock.advance(2.0)
    asyncio.run(engine.submit_answer(code, players[2]["player_id"], correct))
    asyncio.run(engine.submit_answer(code, players[3]["player_id"], correct))

    state = asyncio.run(engine.snapshot(code, players[0]["player_id"]))
    violet = next(team for team in state["teams"] if team["team_id"] == "violet")
    cyan = next(team for team in state["teams"] if team["team_id"] == "cyan")
    assert violet["score"] == sum(player["score"] for player in state["players"] if player["team_id"] == "violet")
    assert cyan["score"] == sum(player["score"] for player in state["players"] if player["team_id"] == "cyan")
    assert violet["correct"] == 2
    assert cyan["correct"] == 1
    assert violet["correct_response_time_ms"] == 4000
    assert cyan["correct_response_time_ms"] == 3000
    assert state["momentum"]["enabled"] is False
    assert engine._rooms[code].momentum_adjustments == []


def test_team_battle_tie_break_order_and_shared_first_place() -> None:
    engine, _, players = create_team_room(4)
    code = players[0]["state"]["code"]
    asyncio.run(engine.start_game(code, players[0]["player_id"]))
    room = engine._rooms[code]
    room.phase = Phase.RESULTS
    violet_players = [player for player in room.players.values() if player.team_id == "violet"]
    cyan_players = [player for player in room.players.values() if player.team_id == "cyan"]

    violet_players[0].score = 100
    cyan_players[0].score = 90
    state = asyncio.run(engine.snapshot(code, players[0]["player_id"]))
    assert state["results"]["winning_team_ids"] == ["violet"]
    assert state["results"]["is_draw"] is False

    violet_players[0].score = cyan_players[0].score = 100
    violet_players[0].correct = 2
    cyan_players[0].correct = 1
    state = asyncio.run(engine.snapshot(code, players[0]["player_id"]))
    assert state["results"]["winning_team_ids"] == ["violet"]

    violet_players[0].correct = cyan_players[0].correct = 2
    violet_players[0].correct_response_time_ms = 2200
    cyan_players[0].correct_response_time_ms = 1700
    state = asyncio.run(engine.snapshot(code, players[0]["player_id"]))
    assert state["results"]["winning_team_ids"] == ["cyan"]

    violet_players[0].correct_response_time_ms = cyan_players[0].correct_response_time_ms = 1700
    state = asyncio.run(engine.snapshot(code, players[0]["player_id"]))
    assert state["results"]["winning_team_ids"] == ["violet", "cyan"]
    assert state["results"]["is_draw"] is True
    assert {team["placement"] for team in state["results"]["teams"]} == {1}
    assert all(team["shared_placement"] for team in state["results"]["teams"])


def test_team_battle_roster_changes_reassign_and_expired_member_keeps_match_contribution() -> None:
    engine, clock, players = create_team_room(4)
    code = players[0]["state"]["code"]
    assignments = {entry["id"]: entry["team_id"] for entry in asyncio.run(engine.snapshot(code, players[0]["player_id"]))["players"]}
    asyncio.run(engine.start_game(code, players[0]["player_id"]))
    clock.advance(COUNTDOWN_SECONDS)
    asyncio.run(engine.advance_due())
    correct = engine._rooms[code].questions[0].answer
    asyncio.run(engine.submit_answer(code, players[0]["player_id"], correct))
    player_to_expire = players[2]
    asyncio.run(engine.submit_answer(code, player_to_expire["player_id"], correct))
    asyncio.run(engine.disconnect(code, player_to_expire["player_id"]))
    clock.advance(QUESTION_SECONDS)
    with pytest.raises(GameError) as late:
        asyncio.run(engine.submit_answer(code, players[1]["player_id"], correct))
    assert late.value.code == "stale_answer"
    assert late.value.state_changed is True
    assert asyncio.run(engine.snapshot(code, players[0]["player_id"]))["phase"] == Phase.REVEAL
    clock.advance(RECONNECT_SECONDS + 1)
    asyncio.run(engine.cleanup())
    room = engine._rooms[code]
    assert player_to_expire["player_id"] in room.players
    assert room.players[player_to_expire["player_id"]].reconnect_expired is True
    assert room.players[player_to_expire["player_id"]].team_id == assignments[player_to_expire["player_id"]]
    assert room.players[player_to_expire["player_id"]].score > 0

    # Finish the match by timeout, return to lobby, then remove the expired seat.
    for _ in range(9):
        clock.advance(QUESTION_SECONDS + TEAM_BATTLE_CONFIG.reveal_seconds)
        asyncio.run(engine.advance_due())
    assert asyncio.run(engine.snapshot(code, players[0]["player_id"]))["phase"] == Phase.RESULTS
    lobby = asyncio.run(engine.set_lobby(code, players[0]["player_id"], rematch=True))
    assert player_to_expire["player_id"] not in room.players
    assert lobby["can_start"] is False
    replacement = asyncio.run(engine.join_room(code, "Replacement"))
    assert len(room.players) == 4
    started = asyncio.run(engine.start_game(code, players[0]["player_id"]))
    reassigned = {entry["id"]: entry["team_id"] for entry in started["players"]}
    assert reassigned[players[3]["player_id"]] != assignments[players[3]["player_id"]]
    assert reassigned[replacement["player_id"]] == "cyan"


def test_invalid_duplicate_stale_and_private_answers(setup_game):
    engine, clock, host, guest = setup_game
    code = host["state"]["code"]
    asyncio.run(engine.start_game(code, host["player_id"]))
    clock.advance(COUNTDOWN_SECONDS)
    asyncio.run(engine.advance_due())
    live = asyncio.run(engine.snapshot(code, host["player_id"]))
    assert live["phase"] == Phase.QUESTION
    assert "answer" not in live["question"]
    with pytest.raises(GameError, match="four answer options"):
        asyncio.run(engine.submit_answer(code, host["player_id"], True))
    asyncio.run(engine.submit_answer(code, host["player_id"], 0))
    with pytest.raises(GameError, match="already locked"):
        asyncio.run(engine.submit_answer(code, host["player_id"], 1))
    clock.advance(QUESTION_SECONDS)
    asyncio.run(engine.advance_due())
    with pytest.raises(GameError, match="closed"):
        asyncio.run(engine.submit_answer(code, guest["player_id"], 0))
    reveal = asyncio.run(engine.snapshot(code, host["player_id"]))
    assert reveal["phase"] == Phase.REVEAL
    assert reveal["answer_feedback"]["correct"] in range(4)


def test_late_answer_advances_reveal_without_accepting_the_answer(setup_game):
    engine, clock, host, guest = setup_game
    code = host["state"]["code"]
    asyncio.run(engine.start_game(code, host["player_id"]))
    clock.advance(COUNTDOWN_SECONDS)
    asyncio.run(engine.advance_due())
    clock.advance(QUESTION_SECONDS)

    with pytest.raises(GameError, match="Time is up") as error:
        asyncio.run(engine.submit_answer(code, guest["player_id"], 0))

    assert error.value.code == "stale_answer"
    assert error.value.state_changed is True
    reveal = asyncio.run(engine.snapshot(code, guest["player_id"]))
    assert reveal["phase"] == Phase.REVEAL
    assert reveal["answered"] is False
    assert reveal["answer_feedback"]["selected"] is None
    assert all(player["score"] == 0 for player in reveal["players"])


def test_simultaneous_answers_are_serialized_and_scoring_is_bounded(setup_game):
    engine, clock, host, guest = setup_game
    code = host["state"]["code"]
    asyncio.run(engine.start_game(code, host["player_id"]))
    clock.advance(COUNTDOWN_SECONDS)
    asyncio.run(engine.advance_due())
    answer = engine._rooms[code].questions[0].answer
    async def submit_simultaneously():
        return await asyncio.gather(
            engine.submit_answer(code, host["player_id"], answer),
            engine.submit_answer(code, guest["player_id"], answer),
        )

    results = asyncio.run(submit_simultaneously())
    state = asyncio.run(engine.snapshot(code, host["player_id"]))
    assert {result["phase"] for result in results} <= {Phase.QUESTION, Phase.REVEAL}
    assert state["phase"] == Phase.REVEAL
    assert all(1000 <= player["score"] <= 1500 for player in state["players"])
    assert all(player["correct"] == 1 for player in state["players"])


def test_timer_expiry_progresses_all_ten_rounds_to_results(setup_game):
    engine, clock, host, _ = setup_game
    code = host["state"]["code"]
    asyncio.run(engine.start_game(code, host["player_id"]))
    clock.advance(COUNTDOWN_SECONDS)
    asyncio.run(engine.advance_due())
    for round_index in range(QUESTION_COUNT):
        assert asyncio.run(engine.snapshot(code, host["player_id"]))["phase"] == Phase.QUESTION
        clock.advance(QUESTION_SECONDS)
        asyncio.run(engine.advance_due())
        assert asyncio.run(engine.snapshot(code, host["player_id"]))["phase"] == Phase.REVEAL
        clock.advance(3.0)
        asyncio.run(engine.advance_due())
        if round_index < QUESTION_COUNT - 1:
            assert asyncio.run(engine.snapshot(code, host["player_id"]))["round"] == round_index + 2
    final_state = asyncio.run(engine.snapshot(code, host["player_id"]))
    assert final_state["phase"] == Phase.RESULTS
    assert final_state["round"] == QUESTION_COUNT + 1


def test_reconnect_host_departure_cleanup_and_rematch(setup_game):
    engine, clock, host, guest = setup_game
    code = host["state"]["code"]
    connected = asyncio.run(engine.disconnect(code, host["player_id"]))
    assert connected[0]["host_id"] == guest["player_id"]
    resumed = asyncio.run(engine.reconnect(host["token"]))
    assert resumed["player_id"] == host["player_id"]
    asyncio.run(engine.disconnect(code, host["player_id"]))
    asyncio.run(engine.disconnect(code, guest["player_id"]))
    clock.advance(RECONNECT_SECONDS + 1)
    assert asyncio.run(engine.cleanup()) == [code]
    with pytest.raises(GameError, match="expired"):
        asyncio.run(engine.reconnect(guest["token"]))

    # A completed room can be reset by its current host without leaving stale scores.
    clock2 = FakeClock()
    engine2 = GameEngine(monotonic=clock2.monotonic, wall_time=clock2.wall)
    host2 = asyncio.run(engine2.create_room("Nova"))
    guest2 = asyncio.run(engine2.join_room(host2["state"]["code"], "Orbit"))
    code2 = host2["state"]["code"]
    asyncio.run(engine2.start_game(code2, host2["player_id"]))
    clock2.advance(COUNTDOWN_SECONDS)
    asyncio.run(engine2.advance_due())
    for round_index in range(QUESTION_COUNT):
        clock2.advance(QUESTION_SECONDS)
        asyncio.run(engine2.advance_due())
        clock2.advance(3.0)
        asyncio.run(engine2.advance_due())
    assert asyncio.run(engine2.snapshot(code2, host2["player_id"]))["phase"] == Phase.RESULTS
    waiting = asyncio.run(engine2.set_lobby(code2, host2["player_id"], rematch=True))
    assert waiting["phase"] == Phase.WAITING
    assert all(player["score"] == 0 for player in waiting["players"])
    with pytest.raises(GameError, match="Only the host"):
        asyncio.run(engine2.set_lobby(code2, guest2["player_id"]))


def prepare_1500_point_lead(engine, clock, host, guest):
    code = host["state"]["code"]
    asyncio.run(engine.start_game(code, host["player_id"]))
    clock.advance(COUNTDOWN_SECONDS)
    asyncio.run(engine.advance_due())
    correct = engine._rooms[code].questions[0].answer
    asyncio.run(engine.submit_answer(code, host["player_id"], correct))
    asyncio.run(engine.submit_answer(code, guest["player_id"], (correct + 1) % 4))
    assert asyncio.run(engine.snapshot(code, host["player_id"]))["phase"] == Phase.REVEAL
    clock.advance(3.0)
    asyncio.run(engine.advance_due())
    return code, engine._rooms[code].questions[1].answer


def test_momentum_shift_eligibility_award_audit_and_rematch_reset(setup_game):
    engine, clock, host, guest = setup_game
    code, correct = prepare_1500_point_lead(engine, clock, host, guest)
    host_state = asyncio.run(engine.snapshot(code, host["player_id"]))
    guest_state = asyncio.run(engine.snapshot(code, guest["player_id"]))
    assert host_state["momentum"]["eligible"] is False
    assert guest_state["momentum"] == {
        "enabled": True,
        "eligible": True,
        "bonus_points": 250,
        "gap_points": 1500,
        "speed_seconds": 5.0,
    }

    asyncio.run(engine.submit_answer(code, guest["player_id"], correct))
    asyncio.run(engine.submit_answer(code, host["player_id"], (correct + 1) % 4))
    reveal = asyncio.run(engine.snapshot(code, guest["player_id"]))
    assert reveal["answer_feedback"]["momentum_bonus"] == 250
    assert reveal["answer_feedback"]["speed_bonus"] == 500
    assert next(player for player in reveal["players"] if player["id"] == guest["player_id"])["score"] == 1750
    assert engine._rooms[code].momentum_adjustments == [
        {
            "player_id": guest["player_id"],
            "name": "Orbit",
            "round": 2,
            "points": 250,
            "reason": "Eligible player answered correctly within 5 seconds.",
        }
    ]

    # Finish the remaining eight unanswered rounds by server timeout.
    clock.advance(3.0)
    asyncio.run(engine.advance_due())
    for _ in range(8):
        clock.advance(QUESTION_SECONDS)
        asyncio.run(engine.advance_due())
        clock.advance(3.0)
        asyncio.run(engine.advance_due())
    results = asyncio.run(engine.snapshot(code, host["player_id"]))
    assert results["phase"] == Phase.RESULTS
    assert results["results"]["momentum_adjustments"][0]["points"] == 250
    assert next(player for player in results["players"] if player["id"] == guest["player_id"])["momentum_points"] == 250
    lobby = asyncio.run(engine.set_lobby(code, host["player_id"], rematch=True))
    assert all(player["score"] == player["momentum_points"] == 0 for player in lobby["players"])
    assert engine._rooms[code].momentum_adjustments == []


def test_momentum_speed_cutoff_and_gap_edge(setup_game):
    engine, clock, host, guest = setup_game
    code, correct = prepare_1500_point_lead(engine, clock, host, guest)
    clock.advance(5.01)
    asyncio.run(engine.submit_answer(code, guest["player_id"], correct))
    asyncio.run(engine.submit_answer(code, host["player_id"], (correct + 1) % 4))
    late_reveal = asyncio.run(engine.snapshot(code, guest["player_id"]))
    assert late_reveal["answer_feedback"]["momentum_bonus"] == 0
    assert late_reveal["answer_feedback"]["speed_bonus"] > 0
    assert engine._rooms[code].momentum_adjustments == []

    # The leader can score normally; a 3,000 point lead makes the next-round
    # comeback bonus ineligible without deducting or multiplying anyone's score.
    clock2 = FakeClock()
    engine2 = GameEngine(monotonic=clock2.monotonic, wall_time=clock2.wall)
    host2 = asyncio.run(engine2.create_room("Nova"))
    guest2 = asyncio.run(engine2.join_room(host2["state"]["code"], "Orbit"))
    code2, correct2 = prepare_1500_point_lead(engine2, clock2, host2, guest2)
    asyncio.run(engine2.submit_answer(code2, host2["player_id"], correct2))
    asyncio.run(engine2.submit_answer(code2, guest2["player_id"], (correct2 + 1) % 4))
    clock2.advance(3.0)
    asyncio.run(engine2.advance_due())
    distant = asyncio.run(engine2.snapshot(code2, guest2["player_id"]))
    assert distant["momentum"]["eligible"] is False
    assert next(player for player in distant["players"] if player["id"] == host2["player_id"])["score"] == 3000

    # The five-second limit is inclusive and awards no bonus when nobody is trailing at start.
    clock3 = FakeClock()
    engine3 = GameEngine(monotonic=clock3.monotonic, wall_time=clock3.wall)
    host3 = asyncio.run(engine3.create_room("Nova"))
    guest3 = asyncio.run(engine3.join_room(host3["state"]["code"], "Orbit"))
    code3 = host3["state"]["code"]
    asyncio.run(engine3.start_game(code3, host3["player_id"]))
    clock3.advance(COUNTDOWN_SECONDS)
    asyncio.run(engine3.advance_due())
    assert asyncio.run(engine3.snapshot(code3, host3["player_id"]))["momentum"]["eligible"] is False
    assert asyncio.run(engine3.snapshot(code3, guest3["player_id"]))["momentum"]["eligible"] is False

    # The engine has to establish a legitimate trailing player before the 5.0 s boundary.
    clock4 = FakeClock()
    engine4 = GameEngine(monotonic=clock4.monotonic, wall_time=clock4.wall)
    host4 = asyncio.run(engine4.create_room("Nova"))
    guest4 = asyncio.run(engine4.join_room(host4["state"]["code"], "Orbit"))
    code4, correct4 = prepare_1500_point_lead(engine4, clock4, host4, guest4)
    clock4.advance(5.0)
    asyncio.run(engine4.submit_answer(code4, guest4["player_id"], correct4))
    asyncio.run(engine4.submit_answer(code4, host4["player_id"], (correct4 + 1) % 4))
    boundary = asyncio.run(engine4.snapshot(code4, guest4["player_id"]))
    assert boundary["answer_feedback"]["momentum_bonus"] == 250


def create_elimination_room(player_count: int = 2):
    clock = FakeClock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    players = [asyncio.run(engine.create_room("Nova", mode="elimination"))]
    for index in range(1, player_count):
        players.append(asyncio.run(engine.join_room(players[0]["state"]["code"], f"Player {index}")))
    return engine, clock, players


def elimination_question(engine, clock, players):
    code = players[0]["state"]["code"]
    asyncio.run(engine.start_game(code, players[0]["player_id"]))
    clock.advance(ELIMINATION_CONFIG.countdown_seconds)
    asyncio.run(engine.advance_due())
    return code, engine._rooms[code].questions[engine._rooms[code].round_index].answer


def test_elimination_config_is_server_owned_and_legacy_classic_is_unchanged():
    engine, _, players = create_elimination_room()
    room = engine._rooms[players[0]["state"]["code"]]
    legacy = asyncio.run(GameEngine().create_room("Legacy"))

    assert room.match_config is ELIMINATION_CONFIG
    assert ELIMINATION_CONFIG.min_players == 2
    assert ELIMINATION_CONFIG.max_players == 8
    assert ELIMINATION_CONFIG.question_count == 10
    assert ELIMINATION_CONFIG.countdown_seconds == 3
    assert ELIMINATION_CONFIG.question_seconds == 15
    assert ELIMINATION_CONFIG.reveal_seconds == 3
    assert ELIMINATION_CONFIG.base_points == 1000
    assert ELIMINATION_CONFIG.speed_bonus_max == 500
    assert not ELIMINATION_CONFIG.momentum_enabled
    assert ELIMINATION_CONFIG.elimination
    assert legacy["state"]["mode"] == "classic"


def test_elimination_roster_enforces_two_to_eight_real_players():
    engine, _, players = create_elimination_room(8)
    with pytest.raises(GameError, match="full"):
        asyncio.run(engine.join_room(players[0]["state"]["code"], "Ninth"))
    assert len(engine._rooms[players[0]["state"]["code"]].players) == 8
    small_engine = GameEngine()
    single = asyncio.run(small_engine.create_room("Solo", mode="elimination"))
    with pytest.raises(GameError) as error:
        asyncio.run(small_engine.start_game(single["state"]["code"], single["player_id"]))
    assert error.value.code == "not_enough_players"


def test_elimination_wrong_answer_eliminates_and_last_survivor_wins():
    engine, clock, players = create_elimination_room()
    code, correct = elimination_question(engine, clock, players)

    asyncio.run(engine.submit_answer(code, players[0]["player_id"], correct))
    with pytest.raises(GameError) as duplicate:
        asyncio.run(engine.submit_answer(code, players[0]["player_id"], correct))
    assert duplicate.value.code == "duplicate_answer"
    reveal = asyncio.run(engine.submit_answer(code, players[1]["player_id"], (correct + 1) % 4))
    assert reveal["phase"] == Phase.REVEAL
    assert reveal["momentum"]["enabled"] is False
    eliminated = next(player for player in reveal["players"] if player["id"] == players[1]["player_id"])
    assert eliminated["eliminated"] is True
    assert eliminated["eliminated_round"] == 1
    assert eliminated["elimination_reason"] == "incorrect"
    assert eliminated["score"] == 0

    clock.advance(ELIMINATION_CONFIG.reveal_seconds)
    asyncio.run(engine.advance_due())
    results = asyncio.run(engine.snapshot(code, players[0]["player_id"]))
    assert results["phase"] == Phase.RESULTS
    assert results["results"]["end_reason"] == "one_survivor"
    assert results["results"]["winning_player_ids"] == [players[0]["player_id"]]
    assert results["results"]["leaderboard"][0]["placement"] == 1
    assert results["results"]["leaderboard"][1]["placement"] == 2


def test_elimination_missing_answer_times_out_but_disconnected_player_can_reconnect():
    engine, clock, players = create_elimination_room()
    code, correct = elimination_question(engine, clock, players)
    asyncio.run(engine.submit_answer(code, players[0]["player_id"], correct))
    asyncio.run(engine.disconnect(code, players[1]["player_id"]))

    assert asyncio.run(engine.snapshot(code, players[0]["player_id"]))["phase"] == Phase.QUESTION
    clock.advance(5)
    resumed = asyncio.run(engine.reconnect(players[1]["token"]))
    assert resumed["state"]["phase"] == Phase.QUESTION
    asyncio.run(engine.submit_answer(code, players[1]["player_id"], correct))
    assert asyncio.run(engine.snapshot(code, players[0]["player_id"]))["phase"] == Phase.REVEAL
    clock.advance(ELIMINATION_CONFIG.reveal_seconds)
    asyncio.run(engine.advance_due())
    assert asyncio.run(engine.snapshot(code, players[0]["player_id"]))["phase"] == Phase.QUESTION

    room = engine._rooms[code]
    room.players[players[0]["player_id"]].answer = None
    room.players[players[1]["player_id"]].answer = None
    clock.advance(ELIMINATION_CONFIG.question_seconds)
    asyncio.run(engine.advance_due())
    timed_out = asyncio.run(engine.snapshot(code, players[0]["player_id"]))
    assert timed_out["phase"] == Phase.REVEAL
    assert all(player["elimination_reason"] == "timeout" for player in timed_out["players"])


def test_elimination_late_answer_is_rejected_and_timeout_reveals_to_all_connected():
    engine, clock, players = create_elimination_room()
    code, correct = elimination_question(engine, clock, players)
    asyncio.run(engine.submit_answer(code, players[0]["player_id"], correct))
    clock.advance(ELIMINATION_CONFIG.question_seconds)
    with pytest.raises(GameError) as error:
        asyncio.run(engine.submit_answer(code, players[1]["player_id"], correct))
    assert error.value.code == "stale_answer"
    assert error.value.state_changed
    reveal = asyncio.run(engine.snapshot(code, players[1]["player_id"]))
    assert reveal["phase"] == Phase.REVEAL
    assert reveal["answered"] is False
    assert reveal["answer_feedback"]["selected"] is None
    assert next(player for player in reveal["players"] if player["id"] == players[1]["player_id"])["elimination_reason"] == "timeout"


def test_elimination_all_out_shares_terminal_placement_and_sets_draw():
    engine, clock, players = create_elimination_room(3)
    code, correct = elimination_question(engine, clock, players)
    for player in players:
        asyncio.run(engine.submit_answer(code, player["player_id"], (correct + 1) % 4))
    assert asyncio.run(engine.snapshot(code, players[0]["player_id"]))["phase"] == Phase.REVEAL
    clock.advance(ELIMINATION_CONFIG.reveal_seconds)
    asyncio.run(engine.advance_due())
    final = asyncio.run(engine.snapshot(code, players[0]["player_id"]))
    assert final["results"]["is_draw"] is True
    assert final["results"]["winning_player_ids"] == []
    assert final["results"]["end_reason"] == "all_eliminated"
    assert {player["placement"] for player in final["results"]["leaderboard"]} == {1}
    assert all(player["shared_placement"] for player in final["results"]["leaderboard"])


def test_elimination_ten_question_limit_ranks_survivors_and_rematch_resets_state():
    engine, clock, players = create_elimination_room(3)
    code, _ = elimination_question(engine, clock, players)
    room = engine._rooms[code]
    # Deterministically keep all three alive through all ten questions.
    for round_index in range(ELIMINATION_CONFIG.question_count):
        answer = room.questions[round_index].answer
        for player in players:
            if room.phase == Phase.QUESTION:
                asyncio.run(engine.submit_answer(code, player["player_id"], answer))
        clock.advance(ELIMINATION_CONFIG.reveal_seconds)
        asyncio.run(engine.advance_due())
    final = asyncio.run(engine.snapshot(code, players[0]["player_id"]))
    assert final["phase"] == Phase.RESULTS
    assert final["results"]["end_reason"] == "question_limit"
    assert final["results"]["remaining_players"] == 3
    assert final["results"]["is_draw"] is True
    assert {player["placement"] for player in final["results"]["leaderboard"]} == {1}

    lobby = asyncio.run(engine.set_lobby(code, players[0]["player_id"], rematch=True))
    assert lobby["phase"] == Phase.WAITING
    assert lobby["remaining_players"] == 3
    assert all(not player["eliminated"] and player["eliminated_round"] is None for player in lobby["players"])
    assert all(player["score"] == player["correct"] == 0 for player in lobby["players"])


def test_elimination_expired_disconnect_keeps_match_record_until_results_then_prunes():
    engine, clock, players = create_elimination_room(3)
    code, correct = elimination_question(engine, clock, players)
    disconnected = players[2]
    asyncio.run(engine.disconnect(code, disconnected["player_id"]))
    asyncio.run(engine.submit_answer(code, players[0]["player_id"], correct))
    asyncio.run(engine.submit_answer(code, players[1]["player_id"], correct))
    clock.advance(RECONNECT_SECONDS + 1)
    asyncio.run(engine.cleanup())
    assert disconnected["player_id"] in engine._rooms[code].players
    assert engine._rooms[code].players[disconnected["player_id"]].reconnect_expired
    with pytest.raises(GameError):
        asyncio.run(engine.reconnect(disconnected["token"]))
    clock.advance(ELIMINATION_CONFIG.question_seconds)
    asyncio.run(engine.advance_due())
    clock.advance(ELIMINATION_CONFIG.reveal_seconds)
    asyncio.run(engine.advance_due())
    final = asyncio.run(engine.snapshot(code, players[0]["player_id"]))
    assert final["phase"] == Phase.RESULTS
    assert any(player["id"] == disconnected["player_id"] for player in final["results"]["leaderboard"])
    asyncio.run(engine.set_lobby(code, players[0]["player_id"], rematch=True))
    assert disconnected["player_id"] not in engine._rooms[code].players


def create_solo_practice():
    clock = FakeClock()
    engine = GameEngine(monotonic=clock.monotonic, wall_time=clock.wall)
    player = asyncio.run(engine.create_room("Practice Player", mode="solo_practice"))
    return engine, clock, player


def start_solo_question(engine, clock, player):
    code = player["state"]["code"]
    state = asyncio.run(engine.start_game(code, player["player_id"]))
    assert state["phase"] == Phase.COUNTDOWN
    clock.advance(SOLO_PRACTICE_CONFIG.countdown_seconds)
    states = asyncio.run(engine.advance_due())
    assert states and states[0]["phase"] == Phase.QUESTION
    return code, engine._rooms[code].questions[0].answer


def test_solo_practice_immutable_config_one_player_start_and_capacity():
    engine, _, player = create_solo_practice()
    code = player["state"]["code"]
    room = engine._rooms[code]
    assert resolve_mode("solo_practice") is SOLO_PRACTICE_CONFIG
    assert room.match_config is SOLO_PRACTICE_CONFIG
    assert SOLO_PRACTICE_CONFIG.mode is GameMode.SOLO_PRACTICE
    assert (SOLO_PRACTICE_CONFIG.min_players, SOLO_PRACTICE_CONFIG.max_players) == (1, 1)
    assert SOLO_PRACTICE_CONFIG.question_count == 10
    assert SOLO_PRACTICE_CONFIG.countdown_seconds == 3
    assert SOLO_PRACTICE_CONFIG.question_seconds == 15
    assert SOLO_PRACTICE_CONFIG.reveal_seconds == 3
    assert SOLO_PRACTICE_CONFIG.base_points == 1000
    assert SOLO_PRACTICE_CONFIG.speed_bonus_max == 500
    assert SOLO_PRACTICE_CONFIG.momentum_enabled is False
    assert player["state"]["can_start"] is True
    with pytest.raises(FrozenInstanceError):
        SOLO_PRACTICE_CONFIG.max_players = 8
    with pytest.raises(GameError, match="1 player maximum") as full:
        asyncio.run(engine.join_room(code, "Unexpected Guest"))
    assert full.value.code == "room_full"
    assert len(room.players) == 1
    assert asyncio.run(engine.start_game(code, player["player_id"]))["phase"] == Phase.COUNTDOWN


def test_question_filters_freeze_common_sequence_and_keep_solo_preferences_private():
    host_engine = GameEngine()
    created = asyncio.run(host_engine.create_room("Host", selection={
        "question_count": 3,
        "categories": ["category:science"],
        "difficulty": "medium",
        "topic_preferences": {"category:science": "normal"},
    }))
    guest = asyncio.run(host_engine.join_room(created["state"]["code"], "Guest"))
    code = created["state"]["code"]
    host_started = asyncio.run(host_engine.start_game(code, created["player_id"]))
    host_engine._show_question(host_engine._rooms[code])
    host_started = asyncio.run(host_engine.snapshot(code, created["player_id"]))
    guest_snapshot = asyncio.run(host_engine.snapshot(code, guest["player_id"]))
    assert host_started["total_rounds"] == 3
    assert host_started["match_filters"]["categories"] == ["category:science"]
    assert "topic_preferences" not in host_started["match_filters"]
    assert host_started["question"] == guest_snapshot["question"]
    assert len(host_engine._rooms[code].questions) == 3
    assert all(question.category_id and question.category_id.startswith("category:science") for question in host_engine._rooms[code].questions)

    solo_engine = GameEngine()
    solo = asyncio.run(solo_engine.create_room("Solo", mode="solo_practice", selection={
        "question_count": 2,
        "difficulty": "medium",
        "topic_preferences": {"category:science": "exclude"},
    }))
    assert "topic_preferences" not in solo["state"]["match_filters"]
    assert asyncio.run(solo_engine.start_game(solo["state"]["code"], solo["player_id"]))["total_rounds"] == 2


def test_question_filter_inventory_error_happens_before_countdown():
    engine = GameEngine()
    player = asyncio.run(engine.create_room("Solo", mode="solo_practice", selection={"difficulty": "hard", "question_count": 17}))
    with pytest.raises(GameError, match="eligible questions are available") as error:
        asyncio.run(engine.start_game(player["state"]["code"], player["player_id"]))
    assert error.value.code == "insufficient_inventory"
    assert asyncio.run(engine.snapshot(player["state"]["code"], player["player_id"]))["phase"] == Phase.WAITING


@pytest.mark.parametrize(("elapsed", "expected_score"), [(0.0, 1500), (7.5, 1250), (14.99, 1000)])
def test_solo_practice_correct_answer_uses_authoritative_classic_scoring(elapsed, expected_score):
    engine, clock, player = create_solo_practice()
    code, correct = start_solo_question(engine, clock, player)
    clock.advance(elapsed)
    reveal = asyncio.run(engine.submit_answer(code, player["player_id"], correct))
    assert reveal["phase"] == Phase.REVEAL
    assert reveal["momentum"]["enabled"] is False
    assert reveal["answer_feedback"]["was_correct"] is True
    assert reveal["answer_feedback"]["speed_bonus"] == expected_score - 1000
    assert reveal["players"][0]["score"] == expected_score
    with pytest.raises(GameError) as duplicate:
        asyncio.run(engine.submit_answer(code, player["player_id"], correct))
    assert duplicate.value.code == "stale_answer"
    assert engine._rooms[code].players[player["player_id"]].answers_submitted == 1


def test_solo_practice_wrong_and_timeout_answers_progress_without_scoring():
    engine, clock, player = create_solo_practice()
    code, correct = start_solo_question(engine, clock, player)
    reveal = asyncio.run(engine.submit_answer(code, player["player_id"], (correct + 1) % 4))
    assert reveal["phase"] == Phase.REVEAL
    assert reveal["answer_feedback"]["was_correct"] is False
    assert reveal["players"][0]["score"] == 0
    clock.advance(SOLO_PRACTICE_CONFIG.reveal_seconds)
    asyncio.run(engine.advance_due())
    assert asyncio.run(engine.snapshot(code, player["player_id"]))["phase"] == Phase.QUESTION

    clock.advance(SOLO_PRACTICE_CONFIG.question_seconds)
    asyncio.run(engine.advance_due())
    timeout = asyncio.run(engine.snapshot(code, player["player_id"]))
    assert timeout["phase"] == Phase.REVEAL
    assert timeout["answered"] is False
    assert timeout["answer_feedback"]["selected"] is None
    with pytest.raises(GameError) as late:
        asyncio.run(engine.submit_answer(code, player["player_id"], 0))
    assert late.value.code == "stale_answer"
    clock.advance(SOLO_PRACTICE_CONFIG.reveal_seconds)
    asyncio.run(engine.advance_due())
    assert asyncio.run(engine.snapshot(code, player["player_id"]))["round"] == 3


def test_solo_practice_full_match_reports_counts_accuracy_and_replay_resets_them():
    engine, clock, player = create_solo_practice()
    code, _ = start_solo_question(engine, clock, player)
    room = engine._rooms[code]
    for index in range(SOLO_PRACTICE_CONFIG.question_count):
        question = room.questions[room.round_index]
        if index == 2:
            clock.advance(SOLO_PRACTICE_CONFIG.question_seconds)
            asyncio.run(engine.advance_due())
            assert room.phase == Phase.REVEAL
        else:
            choice = question.answer if index != 1 else (question.answer + 1) % 4
            asyncio.run(engine.submit_answer(code, player["player_id"], choice))
            assert room.phase == Phase.REVEAL
        clock.advance(SOLO_PRACTICE_CONFIG.reveal_seconds)
        asyncio.run(engine.advance_due())

    final = asyncio.run(engine.snapshot(code, player["player_id"]))
    assert final["phase"] == Phase.RESULTS
    assert "leaderboard" not in final["results"]
    summary = final["results"]["practice_summary"]
    assert summary == {
        "score": 12000,
        "correct_answers": 8,
        "incorrect_answers": 1,
        "unanswered_questions": 1,
        "attempted_questions": 9,
        "total_questions": 10,
        "accuracy_percent": 88.9,
        "correct_response_time_ms": 0,
    }

    lobby = asyncio.run(engine.set_lobby(code, player["player_id"], rematch=True))
    assert lobby["phase"] == Phase.WAITING
    assert lobby["players"][0]["id"] == player["player_id"]
    assert lobby["players"][0]["score"] == 0
    assert room.players[player["player_id"]].answers_submitted == 0
    start_solo_question(engine, clock, player)
    for _ in range(SOLO_PRACTICE_CONFIG.question_count):
        clock.advance(SOLO_PRACTICE_CONFIG.question_seconds)
        asyncio.run(engine.advance_due())
        clock.advance(SOLO_PRACTICE_CONFIG.reveal_seconds)
        asyncio.run(engine.advance_due())
    replay = asyncio.run(engine.snapshot(code, player["player_id"]))
    assert replay["results"]["practice_summary"]["accuracy_percent"] is None
    assert replay["results"]["practice_summary"]["incorrect_answers"] == 0
    assert replay["results"]["practice_summary"]["unanswered_questions"] == 10


def test_solo_practice_reconnect_and_room_cleanup_respect_existing_window():
    engine, clock, player = create_solo_practice()
    code, _ = start_solo_question(engine, clock, player)
    asyncio.run(engine.disconnect(code, player["player_id"]))
    clock.advance(5)
    resumed = asyncio.run(engine.reconnect(player["token"]))
    assert resumed["player_id"] == player["player_id"]
    assert resumed["state"]["phase"] == Phase.QUESTION

    asyncio.run(engine.disconnect(code, player["player_id"]))
    clock.advance(RECONNECT_SECONDS + 1)
    assert asyncio.run(engine.cleanup()) == [code]
    with pytest.raises(GameError, match="invalid or expired"):
        asyncio.run(engine.reconnect(player["token"]))
