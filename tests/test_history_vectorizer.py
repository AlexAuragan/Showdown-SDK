# pyright: reportPrivateUsage=false
# Private history-vectorizer internals are intentionally tested because their
# dimensions and categorical ordering are part of the encoding schema.

import importlib
import os
from collections.abc import Iterator

import pytest

from showdown_sdk import MajorStatus, SideCondition, Weather
from showdown_sdk.classes.parser import (
    AbilityEvent,
    DamageEvent,
    FormeChangeEvent,
    ItemEvent,
    MajorStatusEvent,
    MoveEvent,
    PerishCountEvent,
    PokemonSwitchEvent,
    SideConditionEvent,
    StatChangeEvent,
    TransformEvent,
    TurnEvent,
    TypeChangeEvent,
    WeatherEvent,
)
from showdown_sdk.classes.parser.models import EffectSource, PokemonIdent
from showdown_sdk.models.pokemon.pokemon import PartyPokemon
from showdown_sdk.models.pokemon.status import Stat, Stats
from showdown_sdk.models.sdk.battle_state import BattleState, SourceType
from showdown_sdk.vectorizer import history
from showdown_sdk.vectorizer.utils import (
    ability_id,
    item_id,
    move_id,
    pokemon_id,
)

_HISTORY_LENGTH_ENV_VAR = "SHOWDOWN_SDK_VECTOR_HISTORY_LENGTH"


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_history_environment(  # pyright: ignore[reportUnusedFunction]
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    original = os.environ.get(_HISTORY_LENGTH_ENV_VAR)

    yield

    if original is None:
        monkeypatch.delenv(_HISTORY_LENGTH_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(_HISTORY_LENGTH_ENV_VAR, original)

    importlib.reload(history)


def _reload_history(
    monkeypatch: pytest.MonkeyPatch, length: int | str | None
) -> None:
    if length is None:
        monkeypatch.delenv(_HISTORY_LENGTH_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(_HISTORY_LENGTH_ENV_VAR, str(length))

    importlib.reload(history)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _party_pokemon(species: str, *, curr_hp: int = 300) -> PartyPokemon:
    match species:
        case "Pikachu":
            ability = "Static"
            item = "Light Ball"
            moves = ["thunderbolt", "quickattack", "thunderwave", "grassknot"]

        case "Venusaur":
            ability = "Overgrow"
            item = "Leftovers"
            moves = ["energyball", "sludgebomb", "sleeppowder", "synthesis"]

        case _:
            raise ValueError(f"Unsupported test Pokémon: {species!r}")

    return PartyPokemon(
        id=f"p1: {species}",
        lvl=100,
        details=f"{species}, L100",
        curr_hp=curr_hp,
        max_hp=300,
        stats=Stats(atk=100, def_=100, spa=100, spd=100, spe=100, max_hp=300),
        moves=moves,
        base_ability=ability,
        item=item,
        pokeball="pokeball",
    )


def _battle_state() -> BattleState:
    battle_state = BattleState()

    battle_state.player_id = "p1"
    battle_state.format.gen = 4
    battle_state.format.gametype = "singles"

    battle_state.update_team(
        [_party_pokemon("Pikachu"), _party_pokemon("Venusaur")]
    )

    battle_state.set_active_pokemon("p1: Pikachu")

    battle_state.witness_switch_in("p2a: Bob", lvl=100, species="Gyarados")

    return battle_state


def _own_pikachu() -> PokemonIdent:
    return PokemonIdent(player="p1", slot="a", name="Pikachu")


def _enemy_gyarados() -> PokemonIdent:
    return PokemonIdent(player="p2", slot="a", name="Bob")


def _move_event(
    action_id: int,
    *,
    source: PokemonIdent | None = None,
    target: PokemonIdent | None = None,
    move: str = "Thunderbolt",
) -> MoveEvent:
    if source is None:
        source = _own_pikachu()

    if target is None:
        target = _enemy_gyarados()

    return MoveEvent(
        action_id=action_id,
        move=move,
        source_pokemon=source,
        target_pokemon=target,
        success=True,
        does_hit=True,
    )


def _rows(vector: history.Vector) -> list[history.Vector]:
    dim = history._HISTORY_EVENT_DIM

    assert len(vector) % dim == 0

    return [vector[index : index + dim] for index in range(0, len(vector), dim)]


# ---------------------------------------------------------------------------
# Stable row offsets
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Schema / configuration
# ---------------------------------------------------------------------------


def test_history_event_dimension_is_stable() -> None:
    assert history._HISTORY_EVENT_DIM == 88


def test_default_history_length(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload_history(monkeypatch, None)

    assert history._HISTORY_LENGTH == 32
    assert history.HISTORY_DIM == 32 * 88


def test_custom_history_length(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload_history(monkeypatch, 8)

    assert history._HISTORY_LENGTH == 8
    assert history.HISTORY_DIM == 8 * 88

    value = history.vectorize_history(_battle_state())

    assert len(value) == 8 * 88


@pytest.mark.parametrize("value", ["0", "-1", "banana", "1.5"])
def test_invalid_history_length_is_rejected(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(_HISTORY_LENGTH_ENV_VAR, value)

    with pytest.raises(ValueError):
        importlib.reload(history)


# ---------------------------------------------------------------------------
# Padding / temporal ordering
# ---------------------------------------------------------------------------


def test_empty_history_is_all_zero_padding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reload_history(monkeypatch, 2)

    battle_state = _battle_state()

    value = history.vectorize_history(battle_state)

    assert value == [0] * (2 * history._HISTORY_EVENT_DIM)


def test_single_event_is_left_padded(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload_history(monkeypatch, 2)

    battle_state = _battle_state()

    battle_state.history.extend([TurnEvent(turn=3), _move_event(7)])

    rows = _rows(history.vectorize_history(battle_state))

    assert rows[0] == [0] * history._HISTORY_EVENT_DIM

    assert rows[1][_PRESENT] == 1
    assert rows[1][_TURN] == 3
    assert rows[1][_ACTION_ID_KNOWN] == 1
    assert rows[1][_ACTION_ID] == 7


def test_turn_event_does_not_consume_history_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reload_history(monkeypatch, 2)

    battle_state = _battle_state()

    battle_state.history.extend(
        [TurnEvent(turn=4), _move_event(1), TurnEvent(turn=5), _move_event(2)]
    )

    rows = _rows(history.vectorize_history(battle_state))

    assert [row[_TURN] for row in rows] == [4, 5]
    assert [row[_ACTION_ID] for row in rows] == [1, 2]


def test_history_keeps_last_n_meaningful_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reload_history(monkeypatch, 2)

    battle_state = _battle_state()

    battle_state.history.extend(
        [
            TurnEvent(turn=1),
            _move_event(10),
            _move_event(11),
            TurnEvent(turn=2),
            _move_event(12),
        ]
    )

    rows = _rows(history.vectorize_history(battle_state))

    assert len(rows) == 2

    assert rows[0][_ACTION_ID] == 11
    assert rows[0][_TURN] == 1

    assert rows[1][_ACTION_ID] == 12
    assert rows[1][_TURN] == 2


# ---------------------------------------------------------------------------
# Identity encoding
# ---------------------------------------------------------------------------


def test_own_identity_uses_request_team_order() -> None:
    battle_state = _battle_state()

    pikachu = history._vectorize_identity(_own_pikachu(), battle_state)

    venusaur = history._vectorize_identity(
        PokemonIdent(player="p1", slot="a", name="Venusaur"), battle_state
    )

    assert pikachu == [1, 1, 1]
    assert venusaur == [1, 1, 2]


def test_enemy_identity_uses_stable_reveal_order() -> None:
    battle_state = _battle_state()

    battle_state.witness_switch_in("p2a: Zap", lvl=100, species="Zapdos")

    gyarados = history._vectorize_identity(_enemy_gyarados(), battle_state)

    zapdos = history._vectorize_identity(
        PokemonIdent(player="p2", slot="a", name="Zap"), battle_state
    )

    assert gyarados == [1, 2, 1]
    assert zapdos == [1, 2, 2]


def test_move_event_encodes_actor_and_target_identity() -> None:
    battle_state = _battle_state()

    battle_state.history.append(_move_event(7))

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_ACTOR] == [1, 1, 1]
    assert row[_TARGET] == [1, 2, 1]


# ---------------------------------------------------------------------------
# Move event
# ---------------------------------------------------------------------------


def test_move_event_payload() -> None:
    battle_state = _battle_state()

    battle_state.history.extend(
        [
            TurnEvent(turn=3),
            MoveEvent(
                action_id=7,
                move="Thunderbolt",
                source_pokemon=_own_pikachu(),
                target_pokemon=_enemy_gyarados(),
                success=True,
                does_hit=True,
                hit_count=2,
            ),
        ]
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_PRESENT] == 1
    assert row[_EVENT_TYPE] == history._EVENT_TYPE_IDS[MoveEvent]
    assert row[_TURN] == 3

    assert row[_ACTION_ID_KNOWN] == 1
    assert row[_ACTION_ID] == 7

    assert row[_MOVE] == [1, move_id("Thunderbolt", 4)]

    assert row[_HIT_COUNT] == [1, 2]

    assert row[_SUCCESS] == 1
    assert row[_DOES_HIT] == 1


def test_failed_move_has_success_and_hit_flags_cleared() -> None:
    battle_state = _battle_state()

    battle_state.history.append(
        MoveEvent(
            action_id=8,
            move="Thunderbolt",
            source_pokemon=_own_pikachu(),
            target_pokemon=_enemy_gyarados(),
            success=False,
            does_hit=False,
            failure_reason="miss",
        )
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_SUCCESS] == 0
    assert row[_DOES_HIT] == 0


# ---------------------------------------------------------------------------
# Damage / EffectSource causality
# ---------------------------------------------------------------------------


def test_damage_event_inherits_move_source_and_action_id() -> None:
    battle_state = _battle_state()

    source = EffectSource(
        type=SourceType.MOVE,
        name="Thunderbolt",
        actor=_own_pikachu(),
        action_id=17,
    )

    battle_state.history.append(
        DamageEvent(
            source=source,
            target=_enemy_gyarados(),
            curr_hp=25,
            max_hp=100,
            hp_is_percentage=True,
            effectiveness=4.0,
            crit=True,
        )
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_EVENT_TYPE] == history._EVENT_TYPE_IDS[DamageEvent]

    assert row[_ACTION_ID_KNOWN] == 1
    assert row[_ACTION_ID] == 17

    assert row[_SOURCE_TYPE] == history._SOURCE_TYPE_IDS[SourceType.MOVE]

    # Actor is inferred from EffectSource.
    assert row[_ACTOR] == [1, 1, 1]
    assert row[_TARGET] == [1, 2, 1]

    # The causal move is retained on the effect event.
    assert row[_MOVE] == [1, move_id("Thunderbolt", 4)]

    assert row[_HP] == [
        1,  # HP payload present
        25,
        1,  # max HP known
        100,
        1,  # percentage HP
    ]

    assert row[_EFFECTIVENESS] == [1, 4.0]
    assert row[_CRIT] == 1


def test_non_move_damage_does_not_encode_effectiveness() -> None:
    battle_state = _battle_state()

    source = EffectSource(
        type=SourceType.STATUS, name="psn", actor=_enemy_gyarados()
    )

    battle_state.history.append(
        DamageEvent(
            source=source,
            target=_own_pikachu(),
            curr_hp=250,
            max_hp=300,
            hp_is_percentage=False,
            effectiveness=1.0,
        )
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_EFFECTIVENESS] == [0, 0]


# ---------------------------------------------------------------------------
# Switch event
# ---------------------------------------------------------------------------


def test_switch_event_encodes_species_hp_level_and_known_no_status() -> None:
    battle_state = _battle_state()

    battle_state.history.append(
        PokemonSwitchEvent(
            pokemon=_enemy_gyarados(),
            details="Gyarados, L100",
            level=100,
            curr_hp=76,
            max_hp=100,
            hp_is_percentage=True,
            major_status=None,
        )
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_ACTOR] == [1, 2, 1]

    assert row[_SPECIES] == [1, *pokemon_id("Gyarados", 4)]

    assert row[_HP] == [1, 76, 1, 100, 1]

    assert row[_LEVEL] == [1, 100]

    # A switch condition with no status explicitly tells us that the
    # Pokémon has no major status.
    assert row[_MAJOR_STATUS] == [1, 0]


# ---------------------------------------------------------------------------
# Stat events
# ---------------------------------------------------------------------------


def test_stat_change_encodes_relative_deltas() -> None:
    battle_state = _battle_state()

    source = EffectSource(
        type=SourceType.MOVE,
        name="Swords Dance",
        actor=_own_pikachu(),
        action_id=20,
    )

    battle_state.history.append(
        StatChangeEvent(
            source=source,
            target=_own_pikachu(),
            stat_changes=[(Stat.ATK, 2), (Stat.DEF, -1)],
        )
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_STAT_MODE] == 1

    assert row[_STAT_KNOWN] == [
        1,  # atk
        1,  # def
        0,  # spa
        0,  # spd
        0,  # spe
        0,  # evasion
        0,  # accuracy
    ]

    assert row[_STAT_VALUES] == [2, -1, 0, 0, 0, 0, 0]

    assert row[_SUCCESS] == 1


def test_multiple_changes_to_same_stat_are_accumulated() -> None:
    battle_state = _battle_state()

    source = EffectSource(
        type=SourceType.MOVE, name="Ancient Power", actor=_own_pikachu()
    )

    battle_state.history.append(
        StatChangeEvent(
            source=source,
            target=_own_pikachu(),
            stat_changes=[(Stat.ATK, 1), (Stat.ATK, 1)],
        )
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    atk_index = history._STAT_INDEX[Stat.ATK]

    assert row[_STAT_KNOWN][atk_index] == 1
    assert row[_STAT_VALUES][atk_index] == 2


# ---------------------------------------------------------------------------
# Type change
# ---------------------------------------------------------------------------


def test_type_change_is_multi_hot() -> None:
    battle_state = _battle_state()

    battle_state.history.append(
        TypeChangeEvent(
            source=EffectSource(
                type=SourceType.MOVE,
                name="Conversion",
                actor=_own_pikachu(),
                action_id=21,
            ),
            target=_own_pikachu(),
            types=("Water", "Flying"),
        )
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_TYPES_KNOWN] == 1

    type_bits = row[_TYPES]

    water = history._TYPE_NAMES.index("Water")
    flying = history._TYPE_NAMES.index("Flying")

    assert type_bits[water] == 1
    assert type_bits[flying] == 1
    assert sum(type_bits) == 2


def test_known_typeless_type_change_has_no_type_bits() -> None:
    value = history._vectorize_types(("???",), present=True)

    assert value[0] == 1
    assert value[1:] == [0] * 18


# ---------------------------------------------------------------------------
# Major status
# ---------------------------------------------------------------------------


def test_major_status_event_payload() -> None:
    battle_state = _battle_state()

    source = EffectSource(
        type=SourceType.MOVE,
        name="Thunder Wave",
        actor=_own_pikachu(),
        action_id=30,
    )

    battle_state.history.append(
        MajorStatusEvent(
            source=source,
            target=_enemy_gyarados(),
            status=MajorStatus.PARALYSIS,
            applied=True,
        )
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_EVENT_TYPE] == history._EVENT_TYPE_IDS[MajorStatusEvent]

    assert row[_ACTION_ID_KNOWN] == 1
    assert row[_ACTION_ID] == 30

    assert row[_ACTOR] == [1, 1, 1]
    assert row[_TARGET] == [1, 2, 1]

    assert row[_MOVE] == [1, move_id("Thunder Wave", 4)]

    assert row[_MAJOR_STATUS] == [
        1,
        history._MAJOR_STATUS_IDS[MajorStatus.PARALYSIS],
    ]

    assert row[_APPLIED] == 1


def test_major_status_cure_has_applied_flag_cleared() -> None:
    battle_state = _battle_state()

    battle_state.history.append(
        MajorStatusEvent(
            source=None,
            target=_enemy_gyarados(),
            status=MajorStatus.PARALYSIS,
            applied=False,
        )
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_MAJOR_STATUS] == [
        1,
        history._MAJOR_STATUS_IDS[MajorStatus.PARALYSIS],
    ]

    assert row[_APPLIED] == 0


# ---------------------------------------------------------------------------
# Ability
# ---------------------------------------------------------------------------


def test_ability_event_payload() -> None:
    battle_state = _battle_state()

    battle_state.history.append(
        AbilityEvent(
            pokemon=_enemy_gyarados(), ability="Intimidate", active=True
        )
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_EVENT_TYPE] == history._EVENT_TYPE_IDS[AbilityEvent]

    assert row[_ACTOR] == [1, 2, 1]

    assert row[_ABILITY] == [1, ability_id("Intimidate", 4)]

    assert row[_ACTIVE] == 1


def test_inactive_ability_event_keeps_ability_identity() -> None:
    battle_state = _battle_state()

    battle_state.history.append(
        AbilityEvent(
            pokemon=_enemy_gyarados(), ability="Intimidate", active=False
        )
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_ABILITY] == [1, ability_id("Intimidate", 4)]

    assert row[_ACTIVE] == 0


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------


def test_item_consumed_event_payload() -> None:
    battle_state = _battle_state()

    battle_state.history.append(
        ItemEvent(
            source=EffectSource(
                type=SourceType.ITEM,
                name="Sitrus Berry",
                owner=_enemy_gyarados(),
            ),
            pokemon=_enemy_gyarados(),
            item="Sitrus Berry",
            gained=False,
            consumed=True,
        )
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_EVENT_TYPE] == history._EVENT_TYPE_IDS[ItemEvent]

    assert row[_TARGET] == [1, 2, 1]

    assert row[_ITEM] == [1, item_id("Sitrus Berry", 4)]

    assert row[_GAINED] == 0
    assert row[_CONSUMED] == 1


def test_item_transfer_tracks_previous_owner() -> None:
    battle_state = _battle_state()

    source = EffectSource(
        type=SourceType.MOVE, name="Trick", actor=_own_pikachu(), action_id=31
    )

    battle_state.history.append(
        ItemEvent(
            source=source,
            pokemon=_own_pikachu(),
            item="Leftovers",
            gained=True,
            consumed=False,
            previous_owner=_enemy_gyarados(),
        )
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    # ItemEvent maps previous_owner -> actor and new owner -> target.
    assert row[_ACTOR] == [1, 2, 1]
    assert row[_TARGET] == [1, 1, 1]

    assert row[_ITEM] == [1, item_id("Leftovers", 4)]

    assert row[_ACTION_ID] == 31
    assert row[_GAINED] == 1
    assert row[_CONSUMED] == 0


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------


def test_weather_start_payload() -> None:
    battle_state = _battle_state()

    battle_state.history.append(
        WeatherEvent(
            weather=Weather.SANDSTORM,
            started=True,
            upkeep=False,
            source=EffectSource(
                type=SourceType.MOVE,
                name="Sandstorm",
                actor=_own_pikachu(),
                action_id=40,
            ),
        )
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_WEATHER] == [1, history._WEATHER_IDS[Weather.SANDSTORM]]

    assert row[_STARTED] == 1
    assert row[_UPKEEP] == 0

    assert row[_MOVE] == [1, move_id("Sandstorm", 4)]

    assert row[_ACTION_ID] == 40


def test_weather_upkeep_payload() -> None:
    battle_state = _battle_state()

    battle_state.history.append(
        WeatherEvent(weather=Weather.SANDSTORM, started=True, upkeep=True)
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_WEATHER] == [1, history._WEATHER_IDS[Weather.SANDSTORM]]

    assert row[_STARTED] == 1
    assert row[_UPKEEP] == 1


def test_weather_end_payload() -> None:
    battle_state = _battle_state()

    battle_state.history.append(
        WeatherEvent(weather=Weather.SANDSTORM, started=False, upkeep=False)
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_WEATHER] == [1, history._WEATHER_IDS[Weather.SANDSTORM]]

    assert row[_STARTED] == 0


# ---------------------------------------------------------------------------
# Side conditions
# ---------------------------------------------------------------------------


def test_enemy_side_condition_start() -> None:
    battle_state = _battle_state()

    battle_state.history.append(
        SideConditionEvent(
            source=EffectSource(
                type=SourceType.MOVE,
                name="Stealth Rock",
                actor=_own_pikachu(),
                action_id=50,
            ),
            side="p2",
            condition=SideCondition.STEALTH_ROCK,
            started=True,
        )
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_AFFECTED_SIDE] == [1, 2]

    assert row[_SIDE_CONDITION] == [
        1,
        history._SIDE_CONDITION_IDS[SideCondition.STEALTH_ROCK],
    ]

    assert row[_MOVE] == [1, move_id("Stealth Rock", 4)]

    assert row[_STARTED] == 1


def test_own_side_condition_end() -> None:
    battle_state = _battle_state()

    battle_state.history.append(
        SideConditionEvent(
            source=None,
            side="p1",
            condition=SideCondition.REFLECT,
            started=False,
        )
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_AFFECTED_SIDE] == [1, 1]

    assert row[_SIDE_CONDITION] == [
        1,
        history._SIDE_CONDITION_IDS[SideCondition.REFLECT],
    ]

    assert row[_STARTED] == 0


def test_field_side_condition_uses_field_side_id() -> None:
    battle_state = _battle_state()

    battle_state.history.append(
        SideConditionEvent(
            source=None,
            side=None,
            condition=SideCondition.TRICK_ROOM,
            started=True,
        )
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    # 3 = field / both.
    assert row[_AFFECTED_SIDE] == [1, 3]

    assert row[_SIDE_CONDITION] == [
        1,
        history._SIDE_CONDITION_IDS[SideCondition.TRICK_ROOM],
    ]


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------


def test_transform_event_tracks_source_and_target() -> None:
    battle_state = _battle_state()

    source = EffectSource(
        type=SourceType.MOVE,
        name="Transform",
        actor=_own_pikachu(),
        action_id=60,
    )

    battle_state.history.append(
        TransformEvent(
            source=source, pokemon=_own_pikachu(), target=_enemy_gyarados()
        )
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_EVENT_TYPE] == history._EVENT_TYPE_IDS[TransformEvent]

    assert row[_ACTOR] == [1, 1, 1]
    assert row[_TARGET] == [1, 2, 1]

    assert row[_MOVE] == [1, move_id("Transform", 4)]

    assert row[_ACTION_ID_KNOWN] == 1
    assert row[_ACTION_ID] == 60


# ---------------------------------------------------------------------------
# Perish count
# ---------------------------------------------------------------------------


def test_perish_count_event_payload() -> None:
    battle_state = _battle_state()

    battle_state.history.append(
        PerishCountEvent(
            source=EffectSource(
                type=SourceType.MOVE,
                name="Perish Song",
                actor=_enemy_gyarados(),
                action_id=70,
            ),
            target=_own_pikachu(),
            count=2,
        )
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_ACTOR] == [1, 2, 1]
    assert row[_TARGET] == [1, 1, 1]

    assert row[_PERISH_COUNT] == [1, 2]

    assert row[_MOVE] == [1, move_id("Perish Song", 4)]

    assert row[_ACTION_ID] == 70


def test_perish_zero_is_known_not_missing() -> None:
    battle_state = _battle_state()

    battle_state.history.append(
        PerishCountEvent(source=None, target=_own_pikachu(), count=0)
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_PERISH_COUNT] == [1, 0]


# ---------------------------------------------------------------------------
# Forme change
# ---------------------------------------------------------------------------


def test_forme_change_encodes_new_form_species() -> None:
    battle_state = _battle_state()

    battle_state.witness_switch_in("p2a: Cast", lvl=100, species="Castform")

    castform = PokemonIdent(player="p2", slot="a", name="Cast")

    battle_state.history.append(
        FormeChangeEvent(
            source=EffectSource(
                type=SourceType.ABILITY, name="Forecast", actor=castform
            ),
            pokemon=castform,
            forme="Castform-Sunny",
        )
    )

    row = _rows(history.vectorize_history(battle_state))[-1]

    assert row[_ACTOR] == [1, 2, 2]

    assert row[_SPECIES] == [1, *pokemon_id("Castform-Sunny", 4)]

    assert row[_ABILITY] == [1, ability_id("Forecast", 4)]


# ---------------------------------------------------------------------------
# Replay-style sequence
# ---------------------------------------------------------------------------


def test_realistic_battle_sequence_preserves_temporal_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reload_history(monkeypatch, 6)

    battle_state = _battle_state()

    move_source = EffectSource(
        type=SourceType.MOVE,
        name="Thunderbolt",
        actor=_own_pikachu(),
        action_id=100,
    )

    battle_state.history.extend(
        [
            TurnEvent(turn=1),
            PokemonSwitchEvent(
                pokemon=_enemy_gyarados(),
                details="Gyarados, L100",
                level=100,
                curr_hp=100,
                max_hp=100,
                hp_is_percentage=True,
                major_status=None,
            ),
            MoveEvent(
                action_id=100,
                move="Thunderbolt",
                source_pokemon=_own_pikachu(),
                target_pokemon=_enemy_gyarados(),
                success=True,
                does_hit=True,
            ),
            DamageEvent(
                source=move_source,
                target=_enemy_gyarados(),
                curr_hp=42,
                max_hp=100,
                hp_is_percentage=True,
                effectiveness=4.0,
            ),
            TurnEvent(turn=2),
            MajorStatusEvent(
                source=EffectSource(
                    type=SourceType.MOVE,
                    name="Thunder Wave",
                    actor=_own_pikachu(),
                    action_id=101,
                ),
                target=_enemy_gyarados(),
                status=MajorStatus.PARALYSIS,
                applied=True,
            ),
            WeatherEvent(
                weather=Weather.SANDSTORM,
                started=True,
                upkeep=False,
                source=EffectSource(
                    type=SourceType.MOVE,
                    name="Sandstorm",
                    actor=_enemy_gyarados(),
                    action_id=102,
                ),
            ),
        ]
    )

    rows = _rows(history.vectorize_history(battle_state))

    # Six configured rows, five meaningful events.
    assert len(rows) == 6

    # Left padding.
    assert rows[0] == [0] * history._HISTORY_EVENT_DIM

    switch_row = rows[1]
    move_row = rows[2]
    damage_row = rows[3]
    status_row = rows[4]
    weather_row = rows[5]

    assert (
        switch_row[_EVENT_TYPE] == history._EVENT_TYPE_IDS[PokemonSwitchEvent]
    )
    assert switch_row[_TURN] == 1

    assert move_row[_EVENT_TYPE] == history._EVENT_TYPE_IDS[MoveEvent]
    assert move_row[_TURN] == 1
    assert move_row[_ACTION_ID] == 100

    assert damage_row[_EVENT_TYPE] == history._EVENT_TYPE_IDS[DamageEvent]
    assert damage_row[_TURN] == 1
    assert damage_row[_ACTION_ID] == 100

    # Move and its resulting damage remain causally linked.
    assert move_row[_MOVE] == damage_row[_MOVE]

    assert damage_row[_HP] == [1, 42, 1, 100, 1]
    assert damage_row[_EFFECTIVENESS] == [1, 4.0]

    assert status_row[_EVENT_TYPE] == history._EVENT_TYPE_IDS[MajorStatusEvent]
    assert status_row[_TURN] == 2
    assert status_row[_ACTION_ID] == 101

    assert weather_row[_EVENT_TYPE] == history._EVENT_TYPE_IDS[WeatherEvent]
    assert weather_row[_TURN] == 2
    assert weather_row[_ACTION_ID] == 102

    assert len(history.vectorize_history(battle_state)) == history.HISTORY_DIM
