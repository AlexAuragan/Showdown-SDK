# pyright: reportPrivateUsage=false, reportExplicitAny=false
"""Tests for the history vectorizer's public API.

Tests call only ``vectorize_event`` and ``vectorize_history`` with
``EventFeatures`` objects. Private internals like ``_EVENT_TYPE_IDS``
and ``_TYPE_NAMES`` are schema constants that must stay stable across
releases — they are tested for regression, not invoked dynamically.
"""

from __future__ import annotations

from typing import Any

from showdown_sdk.features import EventFeatures, PokemonRefFeatures
from showdown_sdk.vectorizer import (
    EVENT_VECTOR_DIM,
    vectorize_event,
    vectorize_history,
)
from showdown_sdk.vectorizer.vocabulary import (
    ability_id,
    item_id,
    move_id,
    pokemon_id,
)

## Stable row offsets

_PRESENT = 0
_EVENT_TYPE = 1
_TURN = 2
_ACTION_ID_KNOWN = 3
_ACTION_ID = 4
_SOURCE_TYPE = 5
_ACTOR = slice(6, 9)
_TARGET = slice(9, 12)
_AFFECTED_SIDE = slice(12, 14)
_SPECIES = slice(14, 17)
_MOVE = slice(17, 19)
_ABILITY = slice(19, 21)
_ITEM = slice(21, 23)
_MAJOR_STATUS = slice(23, 25)
_MINOR_STATUS = slice(25, 27)
_WEATHER = slice(27, 29)
_SIDE_CONDITION = slice(29, 31)
_STAT_MODE = 31
_STAT_KNOWN = slice(32, 39)
_STAT_VALUES = slice(39, 46)
_HP = slice(46, 51)
_LEVEL = slice(51, 53)
_HIT_COUNT = slice(53, 55)
_EFFECTIVENESS = slice(55, 57)
_PERISH_COUNT = slice(57, 59)
_SUCCESS = 59
_DOES_HIT = 60
_CRIT = 61
_STARTED = 62
_APPLIED = 63
_ACTIVE = 64
_GAINED = 65
_CONSUMED = 66
_BATON_PASS = 67
_UPKEEP = 68
_TYPES_KNOWN = 69
_TYPES = slice(70, 88)

# Known event type IDs (schema-stable, from history._EVENT_TYPE_IDS)
_MOVE_TYPE_ID = 1
_DAMAGE_TYPE_ID = 2
_SWITCH_TYPE_ID = 4
_MAJOR_STATUS_TYPE_ID = 5
_STAT_CHANGE_TYPE_ID = 8
_WEATHER_TYPE_ID = 15
_ABILITY_TYPE_ID = 17
_ITEM_TYPE_ID = 18
_SIDE_CONDITION_TYPE_ID = 16
_PERISH_COUNT_TYPE_ID = 27
_FORMECHANGE_TYPE_ID = 21
_TYPECHANGE_TYPE_ID = 20


## Helpers


def _rows(vector: list[int | float]) -> list[list[int | float]]:
    assert len(vector) % EVENT_VECTOR_DIM == 0
    return [
        vector[i : i + EVENT_VECTOR_DIM]
        for i in range(0, len(vector), EVENT_VECTOR_DIM)
    ]


def _make(event_type: str, **kwargs: Any) -> EventFeatures:
    """Build an EventFeatures with only the fields that are not None."""
    filtered: dict[str, Any] = {
        k: v for k, v in kwargs.items() if v is not None
    }
    return EventFeatures(event_type=event_type, **filtered)


## Schema


def test_event_dimension_is_stable() -> None:
    assert EVENT_VECTOR_DIM == 88


def test_vectorize_single_event() -> None:
    event = _make(
        "move",
        turn=3,
        action_id=7,
        source=PokemonRefFeatures(side="self", slot=0),
        target=PokemonRefFeatures(side="opponent", slot=0),
        move="thunderbolt",
        success=True,
        does_hit=True,
    )
    vector = vectorize_event(event, gen=4)
    assert len(vector) == EVENT_VECTOR_DIM
    assert vector[_PRESENT] == 1
    assert vector[_EVENT_TYPE] == _MOVE_TYPE_ID
    assert vector[_TURN] == 3
    assert vector[_ACTION_ID_KNOWN] == 1
    assert vector[_ACTION_ID] == 7


## Move event


def test_move_event_payload() -> None:
    event = _make(
        "move",
        turn=3,
        action_id=7,
        source=PokemonRefFeatures(side="self", slot=0),
        target=PokemonRefFeatures(side="opponent", slot=0),
        move="thunderbolt",
        success=True,
        does_hit=True,
        hit_count=2,
    )
    vector = vectorize_event(event, gen=4)
    assert vector[_PRESENT] == 1
    assert vector[_MOVE] == [1, move_id("thunderbolt", 4)]
    assert vector[_HIT_COUNT] == [1, 2]
    assert vector[_SUCCESS] == 1
    assert vector[_DOES_HIT] == 1


def test_failed_move() -> None:
    event = _make(
        "move",
        turn=1,
        action_id=8,
        source=PokemonRefFeatures(side="self", slot=0),
        target=PokemonRefFeatures(side="opponent", slot=0),
        move="thunderbolt",
        success=False,
        does_hit=False,
    )
    vector = vectorize_event(event, gen=4)
    assert vector[_SUCCESS] == 0
    assert vector[_DOES_HIT] == 0


## Damage


def test_damage_event_with_move_source() -> None:
    event = _make(
        "damage",
        turn=1,
        action_id=17,
        effect_source_type="move",
        source=PokemonRefFeatures(side="self", slot=0),
        target=PokemonRefFeatures(side="opponent", slot=0),
        move="thunderbolt",
        hp_current=25,
        hp_max=100,
        hp_ratio=0.25,
        effectiveness=4.0,
        crit=True,
    )
    vector = vectorize_event(event, gen=4)
    assert vector[_ACTION_ID_KNOWN] == 1
    assert vector[_ACTION_ID] == 17
    assert vector[_SOURCE_TYPE] == 1  # move source
    assert vector[_ACTOR] == [1, 1, 1]
    assert vector[_TARGET] == [1, 2, 1]
    assert vector[_MOVE] == [1, move_id("thunderbolt", 4)]
    assert vector[_HP] == [1, 25, 1, 100, 0]
    assert vector[_EFFECTIVENESS] == [1, 4.0]
    assert vector[_CRIT] == 1


## Switch


def test_switch_event_encodes_species_hp_level() -> None:
    event = _make(
        "pokemonswitch",
        turn=1,
        source=PokemonRefFeatures(side="opponent", slot=0),
        species="gyarados",
        level=100,
        hp_current=76,
        hp_max=100,
        hp_ratio=0.76,
    )
    vector = vectorize_event(event, gen=4)
    assert vector[_SPECIES] == [1, *pokemon_id("gyarados", 4)]
    assert vector[_HP] == [1, 76, 1, 100, 0]
    assert vector[_LEVEL] == [1, 100]


## Stat change


def test_stat_change_encodes_relative_deltas() -> None:
    event = _make(
        "statchange",
        turn=1,
        action_id=20,
        source=PokemonRefFeatures(side="self", slot=0),
        target=PokemonRefFeatures(side="self", slot=0),
        stat_changes=(("atk", 2), ("def", -1)),
        success=True,
    )
    vector = vectorize_event(event, gen=4)
    assert vector[_STAT_MODE] == 1
    assert vector[_STAT_KNOWN] == [1, 1, 0, 0, 0, 0, 0]
    assert vector[_STAT_VALUES] == [2, -1, 0, 0, 0, 0, 0]
    assert vector[_SUCCESS] == 1


## Type change


def test_type_change_is_multi_hot() -> None:
    event = _make(
        "typechange",
        turn=1,
        action_id=21,
        source=PokemonRefFeatures(side="self", slot=0),
        target=PokemonRefFeatures(side="self", slot=0),
        types=("water", "flying"),
    )
    vector = vectorize_event(event, gen=4)
    assert vector[_TYPES_KNOWN] == 1
    type_bits = vector[_TYPES]
    # Positions follow the stable _TYPE_NAMES order from history.py
    water_idx = 2  # 0=normal, 1=fire, 2=water
    flying_idx = 9  # 9=flying
    assert type_bits[water_idx] == 1
    assert type_bits[flying_idx] == 1
    assert sum(type_bits) == 2


## Major status


def test_major_status_event_payload() -> None:
    event = _make(
        "majorstatus",
        turn=1,
        action_id=30,
        source=PokemonRefFeatures(side="self", slot=0),
        target=PokemonRefFeatures(side="opponent", slot=0),
        move="thunderwave",
        major_status="par",
        payload={"applied": True},
    )
    vector = vectorize_event(event, gen=4)
    assert vector[_ACTION_ID] == 30
    assert vector[_ACTOR] == [1, 1, 1]
    assert vector[_TARGET] == [1, 2, 1]
    assert vector[_MOVE] == [1, move_id("thunderwave", 4)]
    assert vector[_MAJOR_STATUS][0] == 1  # known
    assert vector[_MAJOR_STATUS][1] >= 1  # ID
    assert vector[_APPLIED] == 1


## Ability


def test_ability_event_payload() -> None:
    event = _make(
        "ability",
        turn=1,
        source=PokemonRefFeatures(side="opponent", slot=0),
        ability="intimidate",
        payload={"active": True},
    )
    vector = vectorize_event(event, gen=4)
    assert vector[_ABILITY] == [1, ability_id("intimidate", 4)]
    assert vector[_ACTIVE] == 1


## Item


def test_item_event_payload() -> None:
    event = _make(
        "item",
        turn=1,
        source=PokemonRefFeatures(side="opponent", slot=0),
        target=PokemonRefFeatures(side="opponent", slot=0),
        item="sitrusberry",
        payload={"gained": False, "consumed": True},
    )
    vector = vectorize_event(event, gen=4)
    assert vector[_ITEM] == [1, item_id("sitrusberry", 4)]
    assert vector[_GAINED] == 0
    assert vector[_CONSUMED] == 1


## Weather


def test_weather_start_payload() -> None:
    event = _make(
        "weather",
        turn=1,
        action_id=40,
        source=PokemonRefFeatures(side="self", slot=0),
        move="sandstorm",
        weather="sandstorm",
        payload={"started": True, "upkeep": False},
    )
    vector = vectorize_event(event, gen=4)
    assert vector[_WEATHER][0] == 1
    assert vector[_STARTED] == 1
    assert vector[_UPKEEP] == 0


def test_weather_upkeep_payload() -> None:
    event = _make(
        "weather",
        turn=1,
        weather="sandstorm",
        payload={"started": True, "upkeep": True},
    )
    vector = vectorize_event(event, gen=4)
    assert vector[_STARTED] == 1
    assert vector[_UPKEEP] == 1


## Side condition


def test_side_condition_event() -> None:
    event = _make(
        "sidecondition",
        turn=1,
        action_id=50,
        source=PokemonRefFeatures(side="self", slot=0),
        move="stealthrock",
        side_condition="stealthrock",
        payload={"started": True},
    )
    vector = vectorize_event(event, gen=4)
    assert vector[_MOVE] == [1, move_id("stealthrock", 4)]
    assert vector[_STARTED] == 1


## Perish count


def test_perish_count_event_payload() -> None:
    event = _make(
        "perishcount",
        turn=1,
        action_id=70,
        source=PokemonRefFeatures(side="opponent", slot=0),
        target=PokemonRefFeatures(side="self", slot=0),
        move="perishsong",
        payload={"perish_count": 2},
    )
    vector = vectorize_event(event, gen=4)
    assert vector[_PERISH_COUNT] == [1, 2]


## Forme change


def test_forme_change_encodes_new_form_species() -> None:
    event = _make(
        "formechange",
        turn=1,
        source=PokemonRefFeatures(side="opponent", slot=0),
        species="castformsunny",
        ability="forecast",
    )
    vector = vectorize_event(event, gen=4)
    assert vector[_SPECIES] == [1, *pokemon_id("castformsunny", 4)]
    assert vector[_ABILITY] == [1, ability_id("forecast", 4)]


## vectorize_history


def test_empty_history_is_empty_padding() -> None:
    vector = vectorize_history((), gen=4, max_events=2)
    assert len(vector) == 2 * EVENT_VECTOR_DIM
    assert vector == [0] * (2 * EVENT_VECTOR_DIM)


def test_single_event_is_left_padded() -> None:
    events = (
        _make(
            "move",
            turn=3,
            action_id=7,
            source=PokemonRefFeatures(side="self", slot=0),
            target=PokemonRefFeatures(side="opponent", slot=0),
            move="thunderbolt",
        ),
    )
    rows = _rows(vectorize_history(events, gen=4, max_events=2))
    assert len(rows) == 2
    assert rows[0] == [0] * EVENT_VECTOR_DIM
    assert rows[1][_PRESENT] == 1
    assert rows[1][_TURN] == 3
    assert rows[1][_ACTION_ID] == 7


def test_history_keeps_last_n_events() -> None:
    events = (
        _make("move", turn=1, action_id=10, move="thunderbolt"),
        _make("move", turn=1, action_id=11, move="quickattack"),
        _make("move", turn=2, action_id=12, move="thunderbolt"),
    )
    rows = _rows(vectorize_history(events, gen=4, max_events=2))
    assert len(rows) == 2
    assert rows[0][_ACTION_ID] == 11
    assert rows[1][_ACTION_ID] == 12


def test_realistic_battle_sequence() -> None:
    events = (
        _make(
            "pokemonswitch",
            turn=1,
            source=PokemonRefFeatures(side="opponent", slot=0),
            species="gyarados",
            level=100,
            hp_current=100,
            hp_max=100,
        ),
        _make(
            "move",
            turn=1,
            action_id=100,
            source=PokemonRefFeatures(side="self", slot=0),
            target=PokemonRefFeatures(side="opponent", slot=0),
            move="thunderbolt",
            success=True,
            does_hit=True,
        ),
        _make(
            "damage",
            turn=1,
            action_id=100,
            effect_source_type="move",
            source=PokemonRefFeatures(side="self", slot=0),
            target=PokemonRefFeatures(side="opponent", slot=0),
            move="thunderbolt",
            hp_current=42,
            hp_max=100,
            effectiveness=4.0,
        ),
        _make(
            "majorstatus",
            turn=2,
            action_id=101,
            source=PokemonRefFeatures(side="self", slot=0),
            target=PokemonRefFeatures(side="opponent", slot=0),
            move="thunderwave",
            major_status="par",
            payload={"applied": True},
        ),
        _make(
            "weather",
            turn=2,
            action_id=102,
            source=PokemonRefFeatures(side="opponent", slot=0),
            move="sandstorm",
            weather="sandstorm",
            payload={"started": True},
        ),
    )
    rows = _rows(vectorize_history(events, gen=4, max_events=6))
    assert len(rows) == 6
    assert rows[0] == [0] * EVENT_VECTOR_DIM  # left padding
    for i in range(5):
        assert rows[1 + i][_PRESENT] == 1
    # Causal link between move and damage
    assert rows[2][_ACTION_ID] == rows[3][_ACTION_ID]
    assert rows[2][_MOVE] == rows[3][_MOVE]
    assert rows[3][_HP] == [1, 42, 1, 100, 0]
    assert rows[3][_EFFECTIVENESS] == [1, 4.0]


def test_consistent_dimension() -> None:
    events = (
        _make("move", turn=1, action_id=1, move="thunderbolt"),
        _make("damage", turn=1, action_id=1, hp_current=50, hp_max=100),
    )
    assert (
        len(vectorize_history(events, gen=4, max_events=4))
        == 4 * EVENT_VECTOR_DIM
    )
    assert (
        len(vectorize_history(events, gen=4, max_events=8))
        == 8 * EVENT_VECTOR_DIM
    )
    assert (
        len(vectorize_history((), gen=4, max_events=32))
        == 32 * EVENT_VECTOR_DIM
    )
