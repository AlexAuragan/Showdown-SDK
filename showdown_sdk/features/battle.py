"""Top-level battle feature objects and the ``battle_to_features`` entrypoint.

The feature layer describes *what a legitimate player knows*; the AI project
decides how to turn that into tensors. It must never import a tensor or
embedding library.
"""

from dataclasses import dataclass
from typing import cast

from showdown_sdk.exceptions import FeatureExtractionError
from showdown_sdk.features.actions import (
    ActionFeatures,
    available_actions_to_features,
)
from showdown_sdk.features.common import canonical
from showdown_sdk.features.events import EventFeatures, history_to_features
from showdown_sdk.features.pokemon import (
    EnemyPokemonFeatures,
    OwnPokemonFeatures,
    enemy_team_to_features,
    own_team_to_features,
)
from showdown_sdk.models.pokemon import Weather
from showdown_sdk.models.sdk import BattleState

## Constants


FEATURE_SCHEMA_VERSION = 1


## Data models


@dataclass(frozen=True)
class SideConditionFeatures:
    name: str
    value: int


@dataclass(frozen=True)
class BattleFormatFeatures:
    gen: int | None = None
    gametype: str | None = None
    tier: str | None = None


@dataclass(frozen=True)
class FieldFeatures:
    turn: int = 0
    weather: str | None = None
    gen_1_desync: bool = False
    own_side_conditions: tuple[SideConditionFeatures, ...] = ()
    enemy_side_conditions: tuple[SideConditionFeatures, ...] = ()
    field_conditions: tuple[SideConditionFeatures, ...] = ()


@dataclass(frozen=True)
class BattleFeatures:
    format: BattleFormatFeatures
    field: FieldFeatures

    own_team: tuple[OwnPokemonFeatures, ...]
    enemy_team: tuple[EnemyPokemonFeatures, ...]

    own_active_slot: int | None = None
    enemy_active_slot: int | None = None

    force_switch: bool = False
    available_actions: tuple[ActionFeatures, ...] = ()

    history: tuple[EventFeatures, ...] = ()

    schema_version: int = FEATURE_SCHEMA_VERSION


## Feature builders


def format_to_features(battle_state: BattleState) -> BattleFormatFeatures:
    fmt = battle_state.format

    return BattleFormatFeatures(
        gen=fmt.gen, gametype=fmt.gametype, tier=fmt.tier
    )


def weather_to_features(battle_state: BattleState) -> str | None:
    weather = battle_state.weather

    if weather is None or weather == Weather.CLEAR_SKY.value:
        return None

    for candidate in Weather:
        if weather == candidate.value:
            return canonical(candidate.value)

    raise FeatureExtractionError(f"Unknown weather: {weather!r}")


def field_to_features(battle_state: BattleState) -> FieldFeatures:
    own = _side_conditions(battle_state, battle_state.player_id)
    foe_side_ids = [
        side_id
        for side_id in battle_state.side_conditions
        if side_id not in {battle_state.player_id, "field"}
    ]

    if len(foe_side_ids) > 1:
        raise FeatureExtractionError(
            f"Expected at most one opponent side, got {foe_side_ids!r}"
        )

    enemy = (
        _side_conditions(battle_state, foe_side_ids[0]) if foe_side_ids else ()
    )

    return FieldFeatures(
        turn=battle_state.turn,
        weather=weather_to_features(battle_state),
        gen_1_desync=battle_state.gen_1_desync,
        own_side_conditions=own,
        enemy_side_conditions=enemy,
        field_conditions=_side_conditions(battle_state, "field"),
    )


def battle_to_features(battle_state: BattleState) -> BattleFeatures:
    own_team = own_team_to_features(battle_state)
    enemy_team = enemy_team_to_features(battle_state)

    own_active_slot = next(
        (
            pokemon.slot
            for pokemon in own_team
            if pokemon.present and pokemon.active
        ),
        None,
    )

    enemy_active_slot = next(
        (pokemon.slot for pokemon in enemy_team if pokemon.active), None
    )

    return BattleFeatures(
        format=format_to_features(battle_state),
        field=field_to_features(battle_state),
        own_team=own_team,
        enemy_team=enemy_team,
        own_active_slot=own_active_slot,
        enemy_active_slot=enemy_active_slot,
        force_switch=battle_state.force_switch,
        available_actions=available_actions_to_features(battle_state),
        history=history_to_features(battle_state),
    )


## Serializers


def features_to_dict(value: object) -> object:
    """Recursive plain-dict conversion; result must be JSON-serializable.

    Feature objects contain only dataclasses, tuples, dicts, and JSON
    scalars, all handled by ``dataclasses.asdict``.
    """
    from dataclasses import asdict, is_dataclass

    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, (tuple, list)):
        items: list[object] = []
        for item in cast("tuple[object, ...] | list[object]", value):
            items.append(features_to_dict(item))
        return items
    return value


## Private helpers


def _side_conditions(
    battle_state: BattleState, key: str | None
) -> tuple[SideConditionFeatures, ...]:
    if key is None:
        return ()

    conditions = battle_state.side_conditions.get(key, {})

    return tuple(
        sorted(
            (
                SideConditionFeatures(
                    name=canonical(condition.value), value=count
                )
                for condition, count in conditions.items()
            ),
            key=lambda entry: entry.name,
        )
    )


__all__ = [
    "FEATURE_SCHEMA_VERSION",
    "BattleFeatures",
    "BattleFormatFeatures",
    "FieldFeatures",
    "SideConditionFeatures",
    "battle_to_features",
    "features_to_dict",
]
