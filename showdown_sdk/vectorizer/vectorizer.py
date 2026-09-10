import os
from collections.abc import Callable
from functools import cache
from typing import cast

from showdown_sdk.models.dex import dex, to_id
from showdown_sdk.models.pokemon.moves import AvailableMove
from showdown_sdk.models.pokemon.pokemon import (
    EnemyPokemon,
    PartyPokemon,
    Unknown,
)
from showdown_sdk.models.pokemon.status import MajorStatus, MinorStatus, Status
from showdown_sdk.models.pokemon.terrain import SideCondition, Weather
from showdown_sdk.models.sdk.battle_state import BattleState
from showdown_sdk.utils.serialization import (
    SerializableObject,
    expect_array,
    expect_int,
    expect_number,
    expect_object,
    expect_string,
)
from showdown_sdk.vectorizer.utils import (
    ability_id,
    item_id,
    move_id,
    pokemon_id,
)

Vector = list[int | float]


# ---------------------------------------------------------------------------
# Optional features
# ---------------------------------------------------------------------------

_FEATURE_ENV_VAR = "SHOWDOWN_SDK_VECTOR_FEATURES"

_ALLOWED_FEATURES = frozenset(
    {"types", "type_matchups", "base_stats", "move_metadata", "move_matchups"}
)


def _load_features() -> frozenset[str]:
    """
    Examples:

        SHOWDOWN_SDK_VECTOR_FEATURES=
        SHOWDOWN_SDK_VECTOR_FEATURES=types,base_stats
        SHOWDOWN_SDK_VECTOR_FEATURES=all

    Read once at import time on purpose. A process must have one stable
    vector schema.
    """
    raw = os.environ.get(_FEATURE_ENV_VAR, "").strip().lower()

    if not raw or raw == "none":
        return frozenset()

    if raw == "all":
        return _ALLOWED_FEATURES

    features = frozenset(
        part.strip() for part in raw.split(",") if part.strip()
    )

    unknown = features - _ALLOWED_FEATURES

    if unknown:
        raise ValueError(
            f"Unknown {_FEATURE_ENV_VAR} features: {sorted(unknown)!r}. "
            + f"Expected any of {sorted(_ALLOWED_FEATURES)!r}"
        )

    return features


_FEATURES = _load_features()

_TYPES_ENABLED = "types" in _FEATURES
_TYPE_MATCHUPS_ENABLED = "type_matchups" in _FEATURES
_BASE_STATS_ENABLED = "base_stats" in _FEATURES
_MOVE_METADATA_ENABLED = "move_metadata" in _FEATURES
_MOVE_MATCHUPS_ENABLED = "move_matchups" in _FEATURES


# ---------------------------------------------------------------------------
# Stable categorical orders
# ---------------------------------------------------------------------------

# Keep this order stable once training data exists.
_TYPE_NAMES = (
    "Normal",
    "Fire",
    "Water",
    "Electric",
    "Grass",
    "Ice",
    "Fighting",
    "Poison",
    "Ground",
    "Flying",
    "Psychic",
    "Bug",
    "Rock",
    "Ghost",
    "Dragon",
    "Dark",
    "Steel",
    "Fairy",
)

_TYPE_IDS = {
    type_name: index for index, type_name in enumerate(_TYPE_NAMES, start=1)
}

# Used so the defensive matchup vector does not expose meaningless
# Fairy/Dark/Steel entries in generations where the type did not exist.
_TYPE_INTRO_GEN = {
    "Normal": 1,
    "Fire": 1,
    "Water": 1,
    "Electric": 1,
    "Grass": 1,
    "Ice": 1,
    "Fighting": 1,
    "Poison": 1,
    "Ground": 1,
    "Flying": 1,
    "Psychic": 1,
    "Bug": 1,
    "Rock": 1,
    "Ghost": 1,
    "Dragon": 1,
    "Dark": 2,
    "Steel": 2,
    "Fairy": 6,
}

_MOVE_CATEGORY_IDS = {"Physical": 1, "Special": 2, "Status": 3}


# ---------------------------------------------------------------------------
# Base schema constants
# ---------------------------------------------------------------------------

_MINOR_STATUSES = tuple(MinorStatus)
_SIDE_CONDITIONS = tuple(SideCondition)
_WEATHERS = tuple(
    weather for weather in Weather if weather is not Weather.CLEAR_SKY
)

_MAJOR_STATUS_IDS = {status: i for i, status in enumerate(MajorStatus, start=1)}

_STATUS_DIM = 7 + 1 + len(_MINOR_STATUSES) + 2
_SIDE_CONDITIONS_DIM = len(_SIDE_CONDITIONS)

# Optional Pokémon features.
_TYPES_DIM = 1 + len(_TYPE_NAMES)
_TYPE_MATCHUPS_DIM = 1 + len(_TYPE_NAMES)
_BASE_STATS_DIM = 7

# Optional move features.
#
# MOVE_METADATA:
#   type_id
#   category_id
#   base_power
#   always_hits
#   accuracy
#   priority
_MOVE_METADATA_DIM = 6

# MOVE_MATCHUP:
#   effectiveness_known
#   type_chart_applies
#   multiplier
_MOVE_MATCHUP_DIM = 3

_POKEMON_EXTRA_DIM = (
    (_TYPES_DIM if _TYPES_ENABLED else 0)
    + (_TYPE_MATCHUPS_DIM if _TYPE_MATCHUPS_ENABLED else 0)
    + (_BASE_STATS_DIM if _BASE_STATS_ENABLED else 0)
)

_MOVE_EXTRA_DIM = (_MOVE_METADATA_DIM if _MOVE_METADATA_ENABLED else 0) + (
    _MOVE_MATCHUP_DIM if _MOVE_MATCHUPS_ENABLED else 0
)

_OWN_MOVE_SLOT_DIM = 2 + _MOVE_EXTRA_DIM
_MOVESET_SLOT_DIM = 3 + _MOVE_EXTRA_DIM

_AVAILABLE_MOVE_DIM = 10 + _MOVE_EXTRA_DIM
_ACTION_MASK_DIM = 10

_OWN_POKEMON_DIM = (
    1  # slot present
    + 3  # species: known, pokemon id, form id
    + _POKEMON_EXTRA_DIM
    + 1  # active
    + 1  # level
    + 2  # current HP, max HP
    + 5  # atk, def, spa, spd, spe
    + 2  # base ability: known, id
    + 2  # current ability: known, id
    + 2  # item: known, id
    + 4 * _OWN_MOVE_SLOT_DIM
    + _STATUS_DIM
    + 1  # transformed
)

_ENEMY_POKEMON_DIM = (
    3  # base species
    + 3  # current species / forme
    + _POKEMON_EXTRA_DIM
    + 1  # active
    + 1  # level
    + 1  # HP %
    + 1  # fainted
    + 2  # base ability
    + 2  # current ability
    + 2  # item
    + 4 * _MOVESET_SLOT_DIM  # learned moves
    + 4 * _MOVESET_SLOT_DIM  # temporary moves
    + _STATUS_DIM
    + 1  # transformed
)

_FIELD_DIM = (
    3
    + 3 * _SIDE_CONDITIONS_DIM
    + 2  # gen, turn, weather  # force switch, gen 1 desync
)

_VECTOR_DIM = (
    _FIELD_DIM
    + 6 * _OWN_POKEMON_DIM
    + 6 * _ENEMY_POKEMON_DIM
    + 4 * _AVAILABLE_MOVE_DIM
    + _ACTION_MASK_DIM
)


# ---------------------------------------------------------------------------
# Dex helpers
# ---------------------------------------------------------------------------


def _canonical_move_name(name: str) -> str:
    value = to_id(name)

    if value.startswith("hiddenpower"):
        return "hiddenpower"

    if value.startswith("return") and value[6:].isdigit():
        return "return"

    if value.startswith("frustration") and value[11:].isdigit():
        return "frustration"

    return value


def _hidden_power_type(move: str, *, gen: int) -> str | None:
    value = to_id(move)

    if not value.startswith("hiddenpower"):
        return None

    suffix = value[len("hiddenpower") :]

    for type_name in _TYPE_NAMES:
        type_id = to_id(type_name)

        if not suffix.startswith(type_id):
            continue

        if gen < _TYPE_INTRO_GEN[type_name]:
            return None

        return type_name

    return None


def _encoded_move_power(move: str, *, gen: int) -> int | None:
    value = to_id(move)

    for prefix in ("return", "frustration"):
        if not value.startswith(prefix):
            continue

        suffix = value[len(prefix) :]

        if suffix.isdigit():
            return int(suffix)

        return None

    if value.startswith("hiddenpower"):
        hidden_type = _hidden_power_type(move, gen=gen)

        if hidden_type is None:
            return None

        type_id = to_id(hidden_type)

        suffix = value[len("hiddenpower") + len(type_id) :]

        if suffix.isdigit():
            return int(suffix)

    return None


def _effective_move_type(move: str, *, gen: int) -> str | None:
    canonical = _canonical_move_name(move)

    if canonical == "hiddenpower":
        return _hidden_power_type(move, gen=gen)

    entry = _move_entry(move, gen)

    if entry is None:
        return None

    move_type = expect_string(entry["type"], name=f"move {move!r}.type")

    if move_type not in _TYPE_IDS:
        return None

    return move_type


@cache
def _pokemon_entry(species: str, gen: int) -> SerializableObject:
    return expect_object(
        dex.gen(gen).pokemon(species), name=f"pokemon {species!r}"
    )


@cache
def _move_entry(move: str, gen: int) -> SerializableObject | None:
    canonical = _canonical_move_name(move)

    # Synthetic Showdown request action.
    if canonical == "recharge":
        return None

    return expect_object(
        dex.gen(gen).move(canonical), name=f"move {canonical!r}"
    )


@cache
def _species_types(species: str, gen: int) -> tuple[str, ...]:
    entry = _pokemon_entry(species, gen)

    raw_types = expect_array(entry["types"], name=f"pokemon {species!r}.types")

    types = tuple(
        expect_string(value, name=f"pokemon {species!r}.types[{i}]")
        for i, value in enumerate(raw_types)
    )

    invalid = set(types) - set(_TYPE_NAMES)

    if invalid:
        raise ValueError(
            f"Unexpected types for {species!r}: {sorted(invalid)!r}"
        )

    return types


@cache
def _species_base_stats(
    species: str, gen: int
) -> tuple[int, int, int, int, int, int]:
    entry = _pokemon_entry(species, gen)

    stats = expect_object(
        entry["baseStats"], name=f"pokemon {species!r}.baseStats"
    )

    return (
        expect_int(stats["hp"], name=f"{species}.baseStats.hp"),
        expect_int(stats["atk"], name=f"{species}.baseStats.atk"),
        expect_int(stats["def"], name=f"{species}.baseStats.def"),
        expect_int(stats["spa"], name=f"{species}.baseStats.spa"),
        expect_int(stats["spd"], name=f"{species}.baseStats.spd"),
        expect_int(stats["spe"], name=f"{species}.baseStats.spe"),
    )


@cache
def _type_effectiveness(
    attacking_type: str, defending_types: tuple[str, ...], gen: int
) -> float:
    type_entry = expect_object(
        dex.gen(gen).types[attacking_type], name=f"type {attacking_type!r}"
    )

    effectiveness = expect_object(
        type_entry["effectiveness"],
        name=f"type {attacking_type!r}.effectiveness",
    )

    multiplier = 1.0

    for defending_type in defending_types:
        multiplier *= expect_number(
            effectiveness[defending_type],
            name=(
                f"type {attacking_type!r}.effectiveness"
                + f"[{defending_type!r}]"
            ),
        )

    return float(multiplier)


# ---------------------------------------------------------------------------
# Primitive categorical encodings
# ---------------------------------------------------------------------------


def _vectorize_known_id(
    value: str | Unknown | None, *, gen: int, get_id: Callable[[str, int], int]
) -> Vector:
    """
    [0, 0] -> unknown
    [1, 0] -> known to be nothing
    [1, N] -> known value N
    """
    if value is Unknown.VALUE:
        return [0, 0]

    if value is None or value == "":
        return [1, 0]

    return [1, get_id(value, gen)]


def _vectorize_known_move_id(
    value: str | Unknown | None, *, gen: int
) -> Vector:
    if value is Unknown.VALUE:
        return [0, 0]

    if value is None or value == "":
        return [1, 0]

    canonical = _canonical_move_name(value)

    if canonical == "recharge":
        return [1, 0]

    return [1, move_id(canonical, gen)]


def _vectorize_known_pokemon_id(
    value: str | Unknown | None, *, gen: int
) -> Vector:
    """
    [0, 0, 0] -> unknown
    [1, 0, 0] -> known to be nothing
    [1, pokemon_id, form_id] -> known species
    """
    if value is Unknown.VALUE:
        return [0, 0, 0]

    if value is None or value == "":
        return [1, 0, 0]

    species_id, form_id = pokemon_id(value, gen)
    return [1, species_id, form_id]


# ---------------------------------------------------------------------------
# Optional Pokémon features
# ---------------------------------------------------------------------------


def _vectorize_types(types: tuple[str, ...] | None) -> Vector:
    if types is None:
        return [0] * _TYPES_DIM

    active_types = set(types)

    vector: Vector = [1]

    vector.extend(int(type_name in active_types) for type_name in _TYPE_NAMES)

    assert len(vector) == _TYPES_DIM
    return vector


def _vectorize_type_matchups(
    types: tuple[str, ...] | None, *, gen: int
) -> Vector:
    if types is None:
        return [0] * _TYPE_MATCHUPS_DIM

    vector: Vector = [1]

    for attacking_type in _TYPE_NAMES:
        if gen < _TYPE_INTRO_GEN[attacking_type]:
            vector.append(0.0)
            continue

        vector.append(_type_effectiveness(attacking_type, types, gen))

    assert len(vector) == _TYPE_MATCHUPS_DIM
    return vector


def _vectorize_base_stats(species: str | None, *, gen: int) -> Vector:
    if species is None:
        return [0] * _BASE_STATS_DIM

    vector: Vector = [1]

    vector.extend(_species_base_stats(species, gen))

    assert len(vector) == _BASE_STATS_DIM
    return vector


def _current_types(
    species: str | None,
    type_override: tuple[str, ...] | None,
    *,
    gen: int,
    status: Status | None = None,
) -> tuple[str, ...] | None:
    if type_override is not None:
        types = type_override
    elif species is not None:
        types = _species_types(species, gen)
    else:
        return None

    # Roost temporarily removes Flying typing.
    if status is not None and MinorStatus.ROOST in status.minor:
        types = tuple(type_name for type_name in types if type_name != "Flying")

    return types


def _vectorize_pokemon_extras(
    species: str | None,
    base_stats_species: str | None,
    type_override: tuple[str, ...] | None,
    *,
    status: Status | None,
    gen: int,
) -> Vector:
    vector: Vector = []

    types: tuple[str, ...] | None = None

    if _TYPES_ENABLED or _TYPE_MATCHUPS_ENABLED:
        types = _current_types(species, type_override, gen=gen, status=status)

    if _TYPES_ENABLED:
        vector.extend(_vectorize_types(types))

    if _TYPE_MATCHUPS_ENABLED:
        vector.extend(_vectorize_type_matchups(types, gen=gen))

    if _BASE_STATS_ENABLED:
        vector.extend(_vectorize_base_stats(base_stats_species, gen=gen))

    assert len(vector) == _POKEMON_EXTRA_DIM
    return vector


def _current_own_base_stats_species(
    battle_state: BattleState, pokemon: PartyPokemon
) -> str:
    """
    Return the species/form whose intrinsic base stats apply.

    Forme changes affect base stats; Transform does not.
    """
    species = _species_from_details(pokemon.details)

    if pokemon.id != battle_state.curr_pokemon:
        return species

    if battle_state.active_pokemon.forme is not None:
        species = battle_state.active_pokemon.forme

    return species


def _enemy_base_stats_species(pokemon: EnemyPokemon) -> str:
    """
    Return the species/form whose intrinsic base stats apply.

    Forme changes affect base stats; Transform does not.
    """
    if pokemon.forme is not None:
        return pokemon.forme

    return _enemy_base_species(pokemon)


# ---------------------------------------------------------------------------
# Optional move features
# ---------------------------------------------------------------------------


def _vectorize_move_metadata(move: str | Unknown | None, *, gen: int) -> Vector:
    if move is Unknown.VALUE or move is None or move == "":
        return [0] * _MOVE_METADATA_DIM

    entry = _move_entry(move, gen)

    if entry is None:
        return [0] * _MOVE_METADATA_DIM

    category = expect_string(entry["category"], name=f"move {move!r}.category")

    if category not in _MOVE_CATEGORY_IDS:
        raise ValueError(
            f"Unexpected move category {category!r} " + f"for move {move!r}"
        )

    move_type = _effective_move_type(move, gen=gen)

    type_id = 0 if move_type is None else _TYPE_IDS[move_type]

    encoded_power = _encoded_move_power(move, gen=gen)

    if encoded_power is not None:
        base_power = encoded_power
    else:
        base_power = expect_int(
            entry["basePower"], name=f"move {move!r}.basePower"
        )

    raw_accuracy = entry["accuracy"]

    if raw_accuracy is True:
        always_hits = 1
        accuracy: int | float = 0
    else:
        always_hits = 0

        accuracy = expect_number(raw_accuracy, name=f"move {move!r}.accuracy")

    priority = expect_int(entry["priority"], name=f"move {move!r}.priority")

    vector: Vector = [
        type_id,
        _MOVE_CATEGORY_IDS[category],
        base_power,
        always_hits,
        accuracy,
        priority,
    ]

    assert len(vector) == _MOVE_METADATA_DIM
    return vector


def _vectorize_move_matchup(
    move: str | Unknown | None,
    *,
    target_types: tuple[str, ...] | None,
    gen: int,
) -> Vector:
    """
    [effectiveness_known, type_chart_applies, multiplier]
    """
    if move is Unknown.VALUE or move is None or move == "":
        return [0, 0, 0]

    entry = _move_entry(move, gen)

    if entry is None:
        return [1, 0, 0]

    category = expect_string(entry["category"], name=f"move {move!r}.category")

    if category == "Status":
        return [1, 0, 0]

    move_type = _effective_move_type(move, gen=gen)

    # Hidden Power definitely uses the type chart, but if its subtype
    # is not known we cannot calculate the multiplier.
    if _canonical_move_name(move) == "hiddenpower" and move_type is None:
        return [0, 1, 0]

    # Genuine typeless / ??? damaging move.
    if move_type is None:
        return [1, 0, 0]

    if target_types is None:
        return [0, 1, 0]

    return [1, 1, _type_effectiveness(move_type, target_types, gen)]


def _vectorize_move_extras(
    move: str | Unknown | None,
    *,
    target_types: tuple[str, ...] | None,
    gen: int,
) -> Vector:
    vector: Vector = []

    if _MOVE_METADATA_ENABLED:
        vector.extend(_vectorize_move_metadata(move, gen=gen))

    if _MOVE_MATCHUPS_ENABLED:
        vector.extend(
            _vectorize_move_matchup(move, target_types=target_types, gen=gen)
        )

    assert len(vector) == _MOVE_EXTRA_DIM
    return vector


def _vectorize_known_move(
    move: str | Unknown | None,
    *,
    target_types: tuple[str, ...] | None,
    gen: int,
) -> Vector:
    vector = _vectorize_known_move_id(move, gen=gen)

    vector.extend(
        _vectorize_move_extras(move, target_types=target_types, gen=gen)
    )

    assert len(vector) == _OWN_MOVE_SLOT_DIM
    return vector


# ---------------------------------------------------------------------------
# Status / field
# ---------------------------------------------------------------------------


def _vectorize_status(status: Status) -> Vector:
    vector: Vector = [
        status.atk_stage,
        status.def_stage,
        status.spa_stage,
        status.spd_stage,
        status.spe_stage,
        status.eva_stage,
        status.acc_stage,
        0 if status.major is None else _MAJOR_STATUS_IDS[status.major],
    ]

    vector.extend(int(effect in status.minor) for effect in _MINOR_STATUSES)

    vector.append(0 if status.perish_count is None else status.perish_count)

    vector.append(int(status.must_recharge))

    assert len(vector) == _STATUS_DIM
    return vector


def _vectorize_side_conditions(conditions: dict[SideCondition, int]) -> Vector:
    vector: Vector = [
        conditions.get(condition, 0) for condition in _SIDE_CONDITIONS
    ]

    assert len(vector) == _SIDE_CONDITIONS_DIM
    return vector


# ---------------------------------------------------------------------------
# Species helpers
# ---------------------------------------------------------------------------


def _current_own_species(
    battle_state: BattleState, pokemon: PartyPokemon
) -> str:
    """
    Return the Pokémon's effective current species/form.

    PartyPokemon.details is the persistent/request representation.

    Active-only state is layered on top:
        base/details -> forme -> Transform target
    """
    species = _species_from_details(pokemon.details)

    if pokemon.id != battle_state.curr_pokemon:
        return species

    if battle_state.active_pokemon.forme is not None:
        species = battle_state.active_pokemon.forme

    if battle_state.active_pokemon.transformed_into is not None:
        species = battle_state.active_pokemon.transformed_into

    return species


def _species_from_details(details: str) -> str:
    species = details.split(",", 1)[0].strip()

    if not species:
        raise ValueError(f"Could not extract species from details: {details!r}")

    return species


def _enemy_base_species(pokemon: EnemyPokemon) -> str:
    if pokemon.species is None:
        raise ValueError(
            "Enemy species is unknown for "
            + f"{pokemon.id!r}; protocol ident "
            + "must not be treated as a species"
        )

    return pokemon.species


def _enemy_current_species(pokemon: EnemyPokemon) -> str:
    if pokemon.transformed_into is not None:
        return pokemon.transformed_into

    if pokemon.forme is not None:
        return pokemon.forme

    return _enemy_base_species(pokemon)


def _active_enemy_types(battle_state: BattleState) -> tuple[str, ...] | None:
    for pokemon in battle_state.enemy_team:
        if pokemon.id is Unknown.VALUE or not pokemon.active:
            continue

        return _current_types(
            _enemy_current_species(pokemon),
            pokemon.type_override,
            gen=battle_state.gen,
            status=pokemon.status,
        )

    return None


def _active_own_types(battle_state: BattleState) -> tuple[str, ...] | None:
    if not battle_state.curr_pokemon:
        return None

    pokemon = battle_state.get_curr_pokemon()

    return _current_types(
        _current_own_species(battle_state, pokemon),
        battle_state.active_pokemon.type_override,
        gen=battle_state.gen,
        status=battle_state.active_pokemon.status,
    )


# ---------------------------------------------------------------------------
# Move slots
# ---------------------------------------------------------------------------


def _vectorize_move_slot(
    move: str | Unknown | None,
    *,
    disabled_moves: set[str],
    target_types: tuple[str, ...] | None,
    gen: int,
) -> Vector:
    vector = _vectorize_known_move_id(move, gen=gen)

    if move is Unknown.VALUE or move is None:
        vector.append(0)
    else:
        vector.append(int(_canonical_move_name(move) in disabled_moves))

    vector.extend(
        _vectorize_move_extras(move, target_types=target_types, gen=gen)
    )

    assert len(vector) == _MOVESET_SLOT_DIM
    return vector


def _vectorize_move_slots(
    moves: list[str | Unknown],
    *,
    disabled_moves: list[str],
    target_types: tuple[str, ...] | None,
    gen: int,
) -> Vector:
    if len(moves) > 4:
        raise ValueError(f"Expected at most 4 move slots, got {len(moves)}")

    disabled = {_canonical_move_name(move) for move in disabled_moves}

    vector: Vector = []

    for move in moves:
        vector.extend(
            _vectorize_move_slot(
                move,
                disabled_moves=disabled,
                target_types=target_types,
                gen=gen,
            )
        )

    for _ in range(4 - len(moves)):
        vector.extend(
            _vectorize_move_slot(
                None,
                disabled_moves=disabled,
                target_types=target_types,
                gen=gen,
            )
        )

    assert len(vector) == 4 * _MOVESET_SLOT_DIM
    return vector


# ---------------------------------------------------------------------------
# Own Pokémon
# ---------------------------------------------------------------------------


def _vectorize_party_pokemon(
    pokemon: PartyPokemon | None,
    *,
    battle_state: BattleState,
    target_types: tuple[str, ...] | None,
) -> Vector:
    if pokemon is None:
        return [0] * _OWN_POKEMON_DIM

    gen = battle_state.gen

    base_species = _species_from_details(pokemon.details)

    is_active = pokemon.id == battle_state.curr_pokemon

    if is_active:
        status = battle_state.active_pokemon.status
        current_ability = battle_state.active_pokemon.ability
        transformed_into = battle_state.active_pokemon.transformed_into
        type_override = battle_state.active_pokemon.type_override

        current_species = _current_own_species(battle_state, pokemon)
        base_stats_species = _current_own_base_stats_species(
            battle_state, pokemon
        )
    else:
        status = Status(major=pokemon.major_status)
        current_ability = pokemon.base_ability
        transformed_into = None
        type_override = None

        current_species = base_species
        base_stats_species = base_species

    if len(pokemon.moves) > 4:
        raise ValueError(
            "Expected at most 4 moves for "
            + f"{pokemon.id!r}, got {len(pokemon.moves)}"
        )

    vector: Vector = [1]

    # Persistent Pokémon identity.
    vector.extend(_vectorize_known_pokemon_id(base_species, gen=gen))

    # Mechanical information uses the effective current species:
    # normal species, current forme, or Transform target.
    vector.extend(
        _vectorize_pokemon_extras(
            current_species,
            base_stats_species=base_stats_species,
            type_override=type_override,
            gen=gen,
            status=status,
        )
    )

    vector.extend(
        [
            int(is_active),
            pokemon.lvl,
            pokemon.curr_hp,
            pokemon.max_hp,
            pokemon.stats.atk,
            pokemon.stats.def_,
            pokemon.stats.spa,
            pokemon.stats.spd,
            pokemon.stats.spe,
        ]
    )

    vector.extend(
        _vectorize_known_id(pokemon.base_ability, gen=gen, get_id=ability_id)
    )

    vector.extend(
        _vectorize_known_id(current_ability, gen=gen, get_id=ability_id)
    )

    vector.extend(
        _vectorize_known_id(
            None if pokemon.item == "" else pokemon.item,
            gen=gen,
            get_id=item_id,
        )
    )

    for move in pokemon.moves:
        vector.extend(
            _vectorize_known_move(move, target_types=target_types, gen=gen)
        )

    for _ in range(4 - len(pokemon.moves)):
        vector.extend(
            _vectorize_known_move(None, target_types=target_types, gen=gen)
        )

    vector.extend(_vectorize_status(status))

    vector.append(int(transformed_into is not None))

    assert len(vector) == _OWN_POKEMON_DIM

    return vector


def _vectorize_own_team(battle_state: BattleState) -> Vector:
    if len(battle_state.team) > 6:
        raise ValueError(
            "Expected at most 6 own Pokémon, " + f"got {len(battle_state.team)}"
        )

    target_types = (
        _active_enemy_types(battle_state) if _MOVE_MATCHUPS_ENABLED else None
    )

    vector: Vector = []

    for pokemon in battle_state.team:
        vector.extend(
            _vectorize_party_pokemon(
                pokemon, battle_state=battle_state, target_types=target_types
            )
        )

    for _ in range(6 - len(battle_state.team)):
        vector.extend(
            _vectorize_party_pokemon(
                None, battle_state=battle_state, target_types=target_types
            )
        )

    assert len(vector) == 6 * _OWN_POKEMON_DIM
    return vector


# ---------------------------------------------------------------------------
# Enemy Pokémon
# ---------------------------------------------------------------------------


def _vectorize_enemy_pokemon(
    pokemon: EnemyPokemon | None,
    *,
    target_types: tuple[str, ...] | None,
    gen: int,
) -> Vector:
    if pokemon is None or pokemon.id is Unknown.VALUE:
        return [0] * _ENEMY_POKEMON_DIM

    base_species = _enemy_base_species(pokemon)
    current_species = _enemy_current_species(pokemon)
    base_stats_species = _enemy_base_stats_species(pokemon)

    vector: Vector = []

    vector.extend(_vectorize_known_pokemon_id(base_species, gen=gen))

    vector.extend(_vectorize_known_pokemon_id(current_species, gen=gen))

    vector.extend(
        _vectorize_pokemon_extras(
            current_species,
            base_stats_species=base_stats_species,
            type_override=pokemon.type_override,
            gen=gen,
            status=pokemon.status,
        )
    )

    vector.extend(
        [
            int(pokemon.active),
            pokemon.lvl,
            pokemon.curr_hp_percent,
            int(pokemon.fainted),
        ]
    )

    vector.extend(
        _vectorize_known_id(pokemon.base_ability, gen=gen, get_id=ability_id)
    )

    vector.extend(
        _vectorize_known_id(pokemon.current_ability, gen=gen, get_id=ability_id)
    )

    vector.extend(_vectorize_known_id(pokemon.item, gen=gen, get_id=item_id))

    vector.extend(
        _vectorize_move_slots(
            list(pokemon.learnt_moves),
            disabled_moves=pokemon.disabled_moves,
            target_types=target_types,
            gen=gen,
        )
    )

    vector.extend(
        _vectorize_move_slots(
            list(pokemon.temporary_moves),
            disabled_moves=pokemon.disabled_moves,
            target_types=target_types,
            gen=gen,
        )
    )

    vector.extend(_vectorize_status(pokemon.status))

    vector.append(int(pokemon.transformed_into is not None))

    assert len(vector) == _ENEMY_POKEMON_DIM
    return vector


def _vectorize_enemy_team(battle_state: BattleState) -> Vector:
    revealed = [
        pokemon
        for pokemon in battle_state.enemy_team
        if pokemon.id is not Unknown.VALUE
    ]

    if len(revealed) > 6:
        raise ValueError(
            "Expected at most 6 enemy Pokémon, " + f"got {len(revealed)}"
        )

    target_types = (
        _active_own_types(battle_state) if _MOVE_MATCHUPS_ENABLED else None
    )

    vector: Vector = []

    for pokemon in revealed:
        vector.extend(
            _vectorize_enemy_pokemon(
                pokemon, target_types=target_types, gen=battle_state.gen
            )
        )

    for _ in range(6 - len(revealed)):
        vector.extend(
            _vectorize_enemy_pokemon(
                None, target_types=target_types, gen=battle_state.gen
            )
        )

    assert len(vector) == 6 * _ENEMY_POKEMON_DIM
    return vector


# ---------------------------------------------------------------------------
# Current decision
# ---------------------------------------------------------------------------


def _vectorize_available_move(
    move: AvailableMove | None,
    *,
    target_types: tuple[str, ...] | None,
    gen: int,
) -> Vector:
    if move is None:
        return [0] * _AVAILABLE_MOVE_DIM

    # Stable identity comes from the request id.
    identity_name = move.id or move.name

    # Mechanical metadata should use the human-readable request name,
    # because Showdown can expose details such as Hidden Power's type/power.
    mechanical_name = move.name or identity_name

    canonical = _canonical_move_name(identity_name)

    vector: Vector = [1]

    vector.extend(_vectorize_known_move_id(identity_name, gen=gen))

    vector.extend([int(canonical == "recharge"), int(canonical == "struggle")])

    vector.extend(
        [
            1,
            0 if move.curr_pp is None else move.curr_pp,
            1,
            0 if move.max_pp is None else move.max_pp,
            int(move.disabled),
        ]
    )

    vector.extend(
        _vectorize_move_extras(
            mechanical_name, target_types=target_types, gen=gen
        )
    )

    assert len(vector) == _AVAILABLE_MOVE_DIM
    return vector


def _vectorize_available_moves(battle_state: BattleState) -> Vector:
    moves = battle_state.available_moves

    if len(moves) > 4:
        raise ValueError(
            "Expected at most 4 available moves, " + f"got {len(moves)}"
        )

    target_types = (
        _active_enemy_types(battle_state) if _MOVE_MATCHUPS_ENABLED else None
    )

    vector: Vector = []

    for move in moves:
        vector.extend(
            _vectorize_available_move(
                move, target_types=target_types, gen=battle_state.gen
            )
        )

    for _ in range(4 - len(moves)):
        vector.extend(
            _vectorize_available_move(
                None, target_types=target_types, gen=battle_state.gen
            )
        )

    assert len(vector) == 4 * _AVAILABLE_MOVE_DIM
    return vector


def _vectorize_action_mask(battle_state: BattleState) -> Vector:
    move_mask = [0, 0, 0, 0]

    if not battle_state.force_switch:
        for i, move in enumerate(battle_state.available_moves):
            move_mask[i] = int(not move.disabled)

    switch_mask = [0, 0, 0, 0, 0, 0]

    has_decision = battle_state.force_switch or bool(
        battle_state.available_moves
    )

    trapped = (
        battle_state.active_pokemon.trapped
        or MinorStatus.TRAPPED in battle_state.active_pokemon.status.minor
        or MinorStatus.PARTIALLY_TRAPPED
        in battle_state.active_pokemon.status.minor
    )

    # maybe_trapped is informational only:
    # Showdown still accepts a switch while the active Pokémon
    # is only "maybe" trapped.
    if has_decision and (battle_state.force_switch or not trapped):
        for i, pokemon in enumerate(battle_state.team):
            is_active = pokemon.id == battle_state.curr_pokemon

            switch_mask[i] = int(pokemon.curr_hp > 0 and not is_active)

    vector: Vector = cast(Vector, move_mask + switch_mask)

    assert len(vector) == _ACTION_MASK_DIM

    return vector


# ---------------------------------------------------------------------------
# Global battle state
# ---------------------------------------------------------------------------


def _vectorize_field(battle_state: BattleState) -> Vector:
    player_id = battle_state.player_id

    if player_id is None:
        raise ValueError("BattleState.player_id is not initialized")

    own_conditions = battle_state.side_conditions.get(player_id, {})

    field_conditions = battle_state.side_conditions.get("field", {})

    foe_side_ids = [
        side_id
        for side_id in battle_state.side_conditions
        if side_id not in {player_id, "field"}
    ]

    if len(foe_side_ids) > 1:
        raise ValueError(
            "Expected at most one opponent side, " + f"got {foe_side_ids!r}"
        )

    foe_conditions = (
        battle_state.side_conditions.get(foe_side_ids[0], {})
        if foe_side_ids
        else {}
    )

    weather_id = 0

    if battle_state.weather not in {None, Weather.CLEAR_SKY.value}:
        for i, weather in enumerate(_WEATHERS, start=1):
            if battle_state.weather == weather.value:
                weather_id = i
                break
        else:
            raise ValueError("Unknown weather: " + f"{battle_state.weather!r}")

    vector: Vector = [battle_state.gen, battle_state.turn, weather_id]

    vector.extend(_vectorize_side_conditions(own_conditions))

    vector.extend(_vectorize_side_conditions(foe_conditions))

    vector.extend(_vectorize_side_conditions(field_conditions))

    vector.extend(
        [int(battle_state.force_switch), int(battle_state.gen_1_desync)]
    )

    assert len(vector) == _FIELD_DIM
    return vector


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def vectorize_battle_state(battle_state: BattleState) -> Vector:
    vector: Vector = []

    vector.extend(_vectorize_field(battle_state))

    vector.extend(_vectorize_own_team(battle_state))

    vector.extend(_vectorize_enemy_team(battle_state))

    vector.extend(_vectorize_available_moves(battle_state))

    vector.extend(_vectorize_action_mask(battle_state))

    assert len(vector) == _VECTOR_DIM
    return vector


__all__ = ["vectorize_battle_state"]
