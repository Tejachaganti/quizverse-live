import pytest

from quizverse.engine import GameError
from quizverse.realtime import _parse_event


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        "[]",
        '{"event":"start","score":99999}',
        '{"event":"answer","choice":4}',
        '{"event":"answer","choice":true}',
        '{"event":"join","name":"A","code":"abc123","extra":1}',
        '{"event":"do-admin-things"}',
    ],
)
def test_invalid_or_untrusted_events_are_rejected(payload: str) -> None:
    with pytest.raises(GameError):
        _parse_event(payload)


def test_valid_client_events_have_strict_typed_fields() -> None:
    event = _parse_event('{"event":"answer","choice":2}')
    assert event.event == "answer"
    assert event.choice == 2


def test_legacy_create_defaults_to_classic_and_explicit_classic_is_valid() -> None:
    legacy = _parse_event('{"event":"create","name":"Nova"}')
    explicit = _parse_event('{"event":"create","name":"Nova","mode":"classic"}')

    assert legacy.event == explicit.event == "create"
    assert legacy.mode == explicit.mode == "classic"


def test_speed_challenge_create_mode_is_valid() -> None:
    event = _parse_event('{"event":"create","name":"Nova","mode":"speed_challenge"}')

    assert event.event == "create"
    assert event.mode == "speed_challenge"


def test_team_battle_create_mode_is_valid() -> None:
    event = _parse_event('{"event":"create","name":"Nova","mode":"team_battle"}')

    assert event.event == "create"
    assert event.mode == "team_battle"


def test_elimination_create_mode_is_valid() -> None:
    event = _parse_event('{"event":"create","name":"Nova","mode":"elimination"}')

    assert event.event == "create"
    assert event.mode == "elimination"


def test_solo_practice_create_mode_is_valid() -> None:
    event = _parse_event('{"event":"create","name":"Nova","mode":"solo_practice"}')

    assert event.event == "create"
    assert event.mode == "solo_practice"


@pytest.mark.parametrize("mode", ["bluff_master", "escape_room"])
def test_structured_game_modes_are_valid(mode: str) -> None:
    event = _parse_event(f'{{"event":"create","name":"Nova","mode":"{mode}"}}')
    assert event.mode == mode


def test_structured_actions_are_strict_and_bounded():
    event = _parse_event('{"event":"bluff_submit","answer":"a believable answer","request_id":"bluff-request-0001"}')
    assert event.answer == "a believable answer"
    vote = _parse_event('{"event":"bluff_vote","choice_id":"randomized_choice_123","request_id":"vote-request-000001"}')
    assert vote.choice_id == "randomized_choice_123"
    puzzle = _parse_event('{"event":"escape_answer","answer":"seven","puzzle_index":2,"request_id":"puzzle-request-0001"}')
    assert puzzle.puzzle_index == 2
    hint = _parse_event('{"event":"escape_hint","request_id":"hint-request-000001"}')
    assert hint.event == "escape_hint"
    with pytest.raises(GameError):
        _parse_event('{"event":"escape_answer","answer":"seven","puzzle_index":10,"request_id":"puzzle-request-0001"}')
    with pytest.raises(GameError):
        _parse_event('{"event":"bluff_vote","choice_id":"real-answer","request_id":"vote-request-000001","author_id":"p1"}')


def test_new_modes_default_to_server_owned_round_counts_and_ability_action_is_strict():
    royale = _parse_event('{"event":"create","name":"Nova","mode":"quiz_royale"}')
    assert royale.question_count is None
    ability = _parse_event('{"event":"ability","ability":"fifty_fifty","request_id":"request-123456789"}')
    assert ability.ability == "fifty_fifty"
    with pytest.raises(GameError):
        _parse_event('{"event":"ability","ability":"shield","request_id":"short"}')
    with pytest.raises(GameError):
        _parse_event('{"event":"ability","ability":"shield","request_id":"request-123456789","energy":5}')


def test_create_event_accepts_only_bounded_taxonomy_filters():
    event = _parse_event('{"event":"create","name":"Nova","categories":["category:science"],"difficulty":"mixed","question_count":7}')
    assert event.categories == ["category:science"]
    assert event.difficulty == "mixed"
    assert event.question_count == 7
    with pytest.raises(GameError):
        _parse_event('{"event":"create","name":"Nova","categories":["private-category"]}')


@pytest.mark.parametrize(
    "payload",
    [
        '{"event":"create","name":"Nova","mode":"unknown_mode"}',
        '{"event":"create","name":"Nova","mode":"elimination","eliminated":false}',
        '{"event":"create","name":"Nova","mode":"elimination","placement":1}',
        '{"event":"create","name":"Nova","mode":"solo_practice","question_seconds":1}',
        '{"event":"create","name":"Nova","mode":"solo_practice","min_players":1}',
        '{"event":"create","name":"Nova","base_points":999999}',
        '{"event":"create","name":"Nova","deadline":9999999999999}',
        '{"event":"create","name":"Nova","answer_key":1}',
        '{"event":"create","name":"Nova","question_index":3}',
        '{"event":"create","name":"Nova","team_id":"violet"}',
        '{"event":"start","round":10}',
        '{"event":"start","teams":{"p1":"violet"}}',
        '{"event":"answer","choice":1,"correct":true}',
        '{"event":"answer","choice":1,"score":999999}',
        '{"event":"answer","choice":1,"correct_option_index":3}',
        '{"event":"answer","choice":1,"team_id":"violet"}',
        '{"event":"answer","choice":1,"progression":10}',
    ],
)
def test_unsupported_modes_and_client_owned_rules_are_rejected(payload: str) -> None:
    with pytest.raises(GameError):
        _parse_event(payload)
