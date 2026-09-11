"""History event vectorizer.

Encodes ``EventFeatures`` objects into fixed-width numeric vectors.
This module knows only feature-layer types and vocabulary helpers — it
never imports parser event classes or ``BattleState``.
"""

from collections.abc import Callable
from typing import cast

from showdown_sdk.features import EventFeatures, PokemonRefFeatures
from showdown_sdk.vectorizer.vocabulary import (
    ability_id,
    item_id,
    move_id,
    pokemon_id,
)

Vector = list[int | float]


## Schema

# These IDs are SDK schema IDs, not Showdown/Dex IDs.
# Never reorder once training data exists.
_EVENT_TYPE_IDS: dict[str, int] = {
    "move": 1,
    "damage": 2,
    "heal": 3,
    "pokemonswitch": 4,
    "majorstatus": 5,
    "minorstatus": 6,
    "minorstatusactivation": 7,
    "statchange": 8,
    "statset": 9,
    "clearboosts": 10,
    "clearallboosts": 11,
    "copyboost": 12,
    "clearnegativeboosts": 13,
    "teamcure": 14,
    "weather": 15,
    "sidecondition": 16,
    "ability": 17,
    "item": 18,
    "transform": 19,
    "typechange": 20,
    "formechange": 21,
    "moveprepare": 22,
    "movecopied": 23,
    "moveactivation": 24,
    "singlemove": 25,
    "cant": 26,
    "perishcount": 27,
    "partialtrap": 28,
    "sethp": 29,
    "detailschange": 30,
    "desync": 31,
}

_SOURCE_TYPE_IDS: dict[str, int] = {
    "move": 1,
    "item": 2,
    "ability": 3,
    "status": 4,
    "weather": 5,
    "terrain": 6,
    "sidecondition": 7,
    "recoil": 8,
    "unknown": 9,
}

# Canonical (lowercase) type names used by the feature layer.
# The feature layer canonicalizes to Showdown IDs, so every layer
# after ``features`` should deal in lowercase type names.
_TYPE_NAMES = (
    "normal",
    "fire",
    "water",
    "electric",
    "grass",
    "ice",
    "fighting",
    "poison",
    "ground",
    "flying",
    "psychic",
    "bug",
    "rock",
    "ghost",
    "dragon",
    "dark",
    "steel",
    "fairy",
)

_IDENTITY_DIM = 3
_SIDE_DIM = 2
_SPECIES_DIM = 3
_KNOWN_ID_DIM = 2
_STAT_PAYLOAD_DIM = 1 + 7 + 7  # mode + 7 mask + 7 values
_HP_PAYLOAD_DIM = 5
_LEVEL_PAYLOAD_DIM = 2
_HIT_COUNT_PAYLOAD_DIM = 2
_EFFECTIVENESS_PAYLOAD_DIM = 2
_PERISH_COUNT_PAYLOAD_DIM = 2
_EVENT_FLAGS_DIM = 10
_TYPES_DIM = 1 + len(_TYPE_NAMES)

# Event row layout (88 values):
#   present                                      1
#   event_type_id                                1
#   turn                                         1
#   action_id: known, value                      2
#   source_type_id                               1
#   actor identity                               3
#   target identity                              3
#   affected side: known, side                   2
#   species: known, national dex id, form id     3
#   move: known, id                              2
#   ability: known, id                           2
#   item: known, id                              2
#   major status: known, id                      2
#   minor status: known, id                      2
#   weather: known, id                           2
#   side condition: known, id                    2
#   stat payload: mode, 7 masks, 7 values       15
#   HP payload                                   5
#   level: known, value                          2
#   hit count: known, value                      2
#   effectiveness: known, value                  2
#   perish count: known, value                   2
#   event flags                                  10
#   type change: known, 18 type bits             19
EVENT_VECTOR_DIM = (
    1
    + 1
    + 1
    + 2
    + 1
    + _IDENTITY_DIM
    + _IDENTITY_DIM
    + _SIDE_DIM
    + _SPECIES_DIM
    + 7 * _KNOWN_ID_DIM
    + _STAT_PAYLOAD_DIM
    + _HP_PAYLOAD_DIM
    + _LEVEL_PAYLOAD_DIM
    + _HIT_COUNT_PAYLOAD_DIM
    + _EFFECTIVENESS_PAYLOAD_DIM
    + _PERISH_COUNT_PAYLOAD_DIM
    + _EVENT_FLAGS_DIM
    + _TYPES_DIM
)

assert EVENT_VECTOR_DIM == 88, f"EVENT_VECTOR_DIM changed to {EVENT_VECTOR_DIM}"


## Vectorization


def _vectorize_identity(ref: PokemonRefFeatures | None) -> Vector:
    """
    [0, 0, 0] -> no identity in this event field
    [1, 1, slot] -> own Pokémon, 1-based slot
    [1, 2, slot] -> enemy Pokémon, 1-based slot
    """
    if ref is None:
        return [0, 0, 0]

    side_id = 1 if ref.side == "self" else 2
    encoded_slot = 0 if ref.slot is None else ref.slot + 1
    return [1, side_id, encoded_slot]


def _vectorize_species(value: str | None, *, gen: int) -> Vector:
    if value is None or value == "":
        return [1, 0, 0]

    species_id, form_id = pokemon_id(value, gen)
    return [1, species_id, form_id]


def _vectorize_known_id(
    value: str | None, *, gen: int, get_id: Callable[[str, int], int]
) -> Vector:
    if value is None or value == "":
        return [1, 0]
    return [1, get_id(value, gen)]


def _vectorize_types(types: tuple[str, ...] | None) -> Vector:
    if types is None:
        return [0] * _TYPES_DIM

    # Normalize to lowercase — the feature layer produces canonical IDs.
    normalized = {t.lower() for t in types} - {"???"}
    invalid = normalized - set(_TYPE_NAMES)
    if invalid:
        raise ValueError(
            f"Unexpected type(s) in event types: {sorted(invalid)!r}"
        )

    vector: Vector = [1]
    vector.extend(int(name in normalized) for name in _TYPE_NAMES)

    assert len(vector) == _TYPES_DIM
    return vector


def vectorize_event(event: EventFeatures, *, gen: int) -> Vector:
    """Vectorize a single semantic event into an 88-value row."""
    event_type_id = _EVENT_TYPE_IDS.get(event.event_type)

    if event_type_id is None:
        raise ValueError(f"Unsupported event type: {event.event_type!r}")

    source_type_id = 0
    if event.effect_source_type is not None:
        source_type_id = _SOURCE_TYPE_IDS.get(event.effect_source_type, 0)

    vector: Vector = [
        1,  # present
        event_type_id,
        event.turn or 0,
        1 if event.action_id is not None else 0,
        event.action_id if event.action_id is not None else 0,
        source_type_id,
    ]

    # Actor / target identity
    vector.extend(_vectorize_identity(event.source))
    vector.extend(_vectorize_identity(event.target))

    # Affected side
    # "self" -> [1, 1], "opponent" -> [1, 2], "field" -> [1, 3], None -> [0, 0]
    if event.affected_side is not None:
        side_id = {"self": 1, "opponent": 2, "field": 3}.get(
            event.affected_side, 0
        )
        vector.extend([1, side_id])
    else:
        vector.extend([0, 0])

    # Species
    vector.extend(_vectorize_species(event.species, gen=gen))

    # Move, ability, item
    vector.extend(_vectorize_known_id(event.move, gen=gen, get_id=move_id))
    vector.extend(
        _vectorize_known_id(event.ability, gen=gen, get_id=ability_id)
    )
    vector.extend(_vectorize_known_id(event.item, gen=gen, get_id=item_id))

    # Major status, minor status, weather, side condition
    vector.extend(
        _vectorize_known_id(
            event.major_status, gen=gen, get_id=lambda v, g: _status_id(v)
        )
    )
    vector.extend(
        _vectorize_known_id(
            event.minor_status, gen=gen, get_id=lambda v, g: _status_id(v)
        )
    )
    vector.extend(
        _vectorize_known_id(
            event.weather, gen=gen, get_id=lambda v, g: _weather_id(v)
        )
    )
    vector.extend(
        _vectorize_known_id(
            event.side_condition,
            gen=gen,
            get_id=lambda v, g: _side_condition_id(v),
        )
    )

    # Stat payload
    if event.stat_changes:
        # mode 1 = relative delta (accumulated)
        vector.append(1)
        stat_mask, stat_values = _stat_changes_mask_values(event.stat_changes)
        vector.extend(stat_mask)
        vector.extend(stat_values)
    elif event.stat_value is not None:
        # mode 2 = absolute stage
        vector.append(2)
        stat_mask = [0] * 7
        if event.stat:
            idx = _STAT_ORDER_IDS.get(event.stat)
            if idx is not None:
                stat_mask[idx] = 1
        vector.extend(stat_mask)
        values = [0] * 7
        if event.stat:
            idx = _STAT_ORDER_IDS.get(event.stat)
            if idx is not None:
                values[idx] = event.stat_value
        vector.extend(values)
    else:
        vector.append(0)
        vector.extend([0] * 7)  # stat_known
        vector.extend([0] * 7)  # stat_values

    # HP payload
    if event.hp_current is not None:
        vector.extend(
            [
                1,
                event.hp_current,
                1 if event.hp_max is not None else 0,
                event.hp_max if event.hp_max is not None else 0,
                0,  # hp_is_percentage (not tracked in EventFeatures)
            ]
        )
    else:
        vector.extend([0] * _HP_PAYLOAD_DIM)

    # Level
    if event.level is not None:
        vector.extend([1, event.level])
    else:
        vector.extend([0, 0])

    # Hit count
    if event.hit_count is not None:
        vector.extend([1, event.hit_count])
    else:
        vector.extend([0, 0])

    # Effectiveness
    if event.effectiveness is not None:
        vector.extend([1, event.effectiveness])
    else:
        vector.extend([0, 0.0])

    # Perish count
    payload_perish = (
        event.payload.get("perish_count") if event.payload else None
    )
    if payload_perish is not None:
        payload_perish = cast(int, payload_perish)
        vector.extend([1, payload_perish])
    else:
        vector.extend([0, 0])

    # Event flags
    flags = _event_flags(event)
    vector.extend(flags)

    # Types
    vector.extend(_vectorize_types(event.types))

    assert len(vector) == EVENT_VECTOR_DIM
    return vector


def vectorize_history(
    history: tuple[EventFeatures, ...], *, gen: int, max_events: int = 32
) -> Vector:
    """
    Vectorize a sequence of events into a flat padded/truncated vector.

    Events are chronological (oldest -> newest). Excess events are
    truncated from the front (oldest removed).
    Missing events are left-padded with zero rows.
    """
    clamped = history[-max_events:]

    vector: Vector = [0] * ((max_events - len(clamped)) * EVENT_VECTOR_DIM)

    for event in clamped:
        vector.extend(vectorize_event(event, gen=gen))

    assert len(vector) == max_events * EVENT_VECTOR_DIM
    return vector


## Private helpers


_STAT_ORDER = ("atk", "def", "spa", "spd", "spe", "eva", "acc")
_STAT_ORDER_IDS = {name: idx for idx, name in enumerate(_STAT_ORDER)}

# These ID mappings match the legacy schema for compatibility.
# They are stable SDK schema IDs, not related to Dex IDs.
_MAJOR_STATUS_NAMES = ("psn", "tox", "par", "slp", "frz", "brn", "fnt")
_MINOR_STATUS_NAMES = (
    "confusion",
    "flinch",
    "trapped",
    "partiallytrapped",
    "leechseed",
    "cursed",
    "nightmare",
    "torment",
    "taunt",
    "disable",
    "infestation",
    "embargo",
    "healblock",
    "substitute",
    "identified",
    "roost",
    "charging",
    "recharge",
)
_WEATHER_NAMES = (
    "raindance",
    "sunnyday",
    "sandstorm",
    "hail",
    "desolateland",
    "primordialsea",
    "deltastream",
    "heavysnow",
)
_SIDE_CONDITION_NAMES = (
    "stealthrock",
    "spikes",
    "toxicspikes",
    "reflect",
    "lightscreen",
    "safeguard",
    "mist",
    "tailwind",
    "stickyweb",
    "auroraveil",
    "gmaxsteelsurge",
    "gmaxwildfire",
    "gmaxvinelash",
    "gmaxcannonade",
    "gmaxvoltcrash",
    "gmaxbefuddle",
    "gmaxcentiferno",
    "gmaxsandblast",
    "gmaxsnooze",
    "gmaxresonance",
    "gmaxfoamburst",
    "gmaxdepletion",
    "gmaxchistrike",
    "gmaxgraymeteor",
    "gmaxreplenish",
    "gmaxmalodor",
    "gmaxstonesurge",
    "gmaxwindrage",
    "gmaxhydrosnipe",
    "gmaxdrumsolo",
    "gmaxfireball",
    "gmaxgolurk",
    "gmaxvolcalith",
    "gmaxcuddle",
    "gmaxspirit",
    "gmaxcrimsonsand",
    "gmaxmeltdown",
    "gmaxchaos",
    "gmaxsynergy",
    "gmaxnova",
    "gmaxrengoku",
)


def _status_id(value: str) -> int:
    """Stable SDK-internal ID for status names (matches legacy schema)."""
    for idx, name in enumerate(_MAJOR_STATUS_NAMES, start=1):
        if name == value:
            return idx
    for idx, name in enumerate(_MINOR_STATUS_NAMES, start=1):
        if name == value:
            return idx
    return 0


def _weather_id(value: str) -> int:
    for idx, name in enumerate(_WEATHER_NAMES, start=1):
        if name == value:
            return idx
    return 0


def _side_condition_id(value: str) -> int:
    for idx, name in enumerate(_SIDE_CONDITION_NAMES, start=1):
        if name == value:
            return idx
    return 0


def _stat_changes_mask_values(
    changes: tuple[tuple[str, int], ...],
) -> tuple[list[int], list[int]]:
    mask = [0] * 7
    values = [0] * 7

    for stat_name, delta in changes:
        idx = _STAT_ORDER_IDS.get(stat_name)
        if idx is not None:
            mask[idx] = 1
            values[idx] += delta

    return mask, values


def _event_flags(event: EventFeatures) -> list[int]:
    """Extract the 10 event flag bits from an EventFeatures object."""
    payload = event.payload or {}

    return [
        1 if event.success else 0,  # 0: success
        1 if event.does_hit else 0,  # 1: does_hit
        1 if event.crit else 0,  # 2: crit
        1 if payload.get("started") else 0,  # 3: started
        1 if payload.get("applied") else 0,  # 4: applied
        1 if payload.get("active") else 0,  # 5: active
        1 if payload.get("gained") else 0,  # 6: gained
        1 if payload.get("consumed") else 0,  # 7: consumed
        1 if payload.get("baton_pass") else 0,  # 8: baton_pass
        1 if payload.get("upkeep") else 0,  # 9: upkeep
    ]


__all__ = ["EVENT_VECTOR_DIM", "vectorize_event", "vectorize_history"]
