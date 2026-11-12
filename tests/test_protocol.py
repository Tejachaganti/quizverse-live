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
