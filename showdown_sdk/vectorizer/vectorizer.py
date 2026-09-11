"""Flat baseline vectorizer for ``BattleFeatures``.

This module converts typed feature objects into flat numeric vectors
suitable for logistic regression, random forests, linear models, and
flat MLP baselines.

It never imports domain models (``BattleState``, ``PartyPokemon``, etc.)
or parser event classes. All semantic extraction is the responsibility
of ``showdown_sdk.features``.

The module makes **no Dex queries**. Types, base stats, and type
effectiveness are not derived here — if those are needed in the vector
schema, the feature layer must expose them explicitly.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from showdown_sdk.features import (
    ActionFeatures,
    BattleFeatures,
    EnemyPokemonFeatures,
    Knowledge,
    OwnPokemonFeatures,
    SideConditionFeatures,
    StatusFeatures,
)
from showdown_sdk.vectorizer.history import vectorize_history
from showdown_sdk.vectorizer.vocabulary import (
    ability_id,
    item_id,
    move_id,
    pokemon_id,
)

Vector = list[int | float]

VECTOR_SCHEMA_VERSION = 3


## Configuration


@dataclass(frozen=True)
class VectorizerConfig:
    """Explicit, serializable configuration for the baseline vectorizer.

    Never hide vector shape changes behind environment variables.
    A trained model must know its VectorizerConfig to interpret vectors
    correctly.
    """

    history_length: int = 32


## Fixed dimensions

_STATUS_DIM = (
    7
    + 1
    + len(
        (
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
    )
    + 2
)  # perish_count + must_recharge

_SIDE_CONDITIONS_DIM = len(
    (
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
)

_OWN_MOVE_SLOT_DIM = 2  # known move ID
_ENEMY_MOVE_SLOT_DIM = 2  # known, ID (Knowledge)

_ACTION_DIM = (
    1  # kind: 0=padding, 1=move, 2=switch
    + 1  # move name known
    + 1  # move id
    + 1  # disabled
    + 2  # current_pp known, value
    + 2  # max_pp known, value
    + 1  # switch team_slot
    + 1  # switch species known
    + 2  # switch species id, form id
)

# NOTE(V2→V3): removed _TYPES_DIM (19), _TYPE_MATCHUPS_DIM (19), and
# _BASE_STATS_DIM (7). The feature layer does not expose types or base
# stats directly — only type_override, which is encoded as a single
# boolean flag. Types, matchups, and base-stats extraction would be
# feature-layer responsibilities if added back.

_OWN_POKEMON_DIM = (
    1  # slot present
    + 1  # species known
    + 2  # species id, form id
    + 1  # current_species known
    + 2  # current species id, form id
    + 1  # active
    + 1  # level
    + 2  # hp ratio known, value
    + 1  # fainted
    + 5  # stats: atk, def, spa, spd, spe (actual in-battle stats)
    + 2  # base ability: known, id
    + 2  # current ability: known, id
    + 2  # item: known, id
    + 4 * _OWN_MOVE_SLOT_DIM
    + _STATUS_DIM
    + 1  # transformed
    + 1  # forme known
    + 1  # has type override
)

_ENEMY_POKEMON_DIM = (
    1  # revealed
    + 1  # species known
    + 2  # species id, form id
    + 1  # current_species known
    + 2  # current species id, form id
    + 1  # active
    + 1  # level
    + 2  # hp ratio known, value
    + 1  # fainted
    + 2  # base ability: known, id
    + 2  # current ability: known, id
    + 2  # item: known, id
    + 4 * _ENEMY_MOVE_SLOT_DIM  # moves (knowledge)
    + _STATUS_DIM
    + 1  # transformed
    + 1  # forme known
    + 1  # has type override
)

_FIELD_DIM = (
    1  # gen
    + 1  # turn
    + 2  # weather known, id
    + 1  # gen_1_desync
    + 3 * _SIDE_CONDITIONS_DIM  # own, enemy, field conditions
)


## Vocabulary helpers


_MINOR_STATUS_NAMES = frozenset(
    {
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
    }
)

_MAJOR_STATUS_IDS: dict[str, int] = {
    "psn": 1,
    "tox": 2,
    "par": 3,
    "slp": 4,
    "frz": 5,
    "brn": 6,
    "fnt": 7,
}


def _major_status_id(name: str | None) -> int:
    if name is None:
        return 0
    return _MAJOR_STATUS_IDS.get(name, 0)


## Vectorization helpers


def _vectorize_optional_known_id(
    value: str | None, *, gen: int, get_id: Callable[[str, int], int]
) -> Vector:
    """[0, 0] -> unknown; [1, 0] -> known absent; [1, N] -> known value."""
    if value is None:
        return [1, 0]

    return [1, get_id(value, gen)]


def _vectorize_knowledge_id(
    knowledge: Knowledge[str], *, gen: int, get_id: Callable[[str, int], int]
) -> Vector:
    """Encode Knowledge[T] via the [known, id] pattern."""
    if not knowledge.known:
        return [0, 0]
    if knowledge.value is None:
        return [1, 0]
    return [1, get_id(knowledge.value, gen)]


def _vectorize_status(status: StatusFeatures) -> Vector:
    vector: Vector = [
        status.attack_stage,
        status.defense_stage,
        status.special_attack_stage,
        status.special_defense_stage,
        status.speed_stage,
        status.evasion_stage,
        status.accuracy_stage,
        _major_status_id(status.major),
    ]

    minor_set = set(status.minor or ())
    vector.extend(
        int(effect in minor_set) for effect in sorted(_MINOR_STATUS_NAMES)
    )

    vector.append(status.perish_count or 0)
    vector.append(int(status.must_recharge))

    assert len(vector) == _STATUS_DIM
    return vector


def _vectorize_side_conditions(
    conditions: tuple[SideConditionFeatures, ...],
) -> Vector:
    condition_map: dict[str, int] = {}
    for c in conditions:
        condition_map[c.name] = c.value

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

    vector: Vector = [
        condition_map.get(name, 0) for name in _SIDE_CONDITION_NAMES
    ]
    assert len(vector) == _SIDE_CONDITIONS_DIM
    return vector


## Pokémon vectorizers


def _vectorize_own_pokemon(pokemon: OwnPokemonFeatures, *, gen: int) -> Vector:
    if not pokemon.present:
        return [0] * _OWN_POKEMON_DIM

    vector: Vector = [1]

    # Species
    if pokemon.species is not None:
        species_id, form_id = pokemon_id(pokemon.species, gen)
        vector.extend([1, species_id, form_id])
    else:
        vector.extend([0, 0, 0])

    # Current species
    if (
        pokemon.current_species is not None
        and pokemon.current_species != pokemon.species
    ):
        species_id, form_id = pokemon_id(pokemon.current_species, gen)
        vector.extend([1, species_id, form_id])
    else:
        vector.extend([0, 0, 0])

    # --- No type vector here (V3) ---
    # Types and type matchups are feature-layer responsibilities.
    # type_override is captured as a single boolean later.

    vector.append(int(pokemon.active))
    vector.append(pokemon.level or 0)

    # HP ratio
    if pokemon.hp_ratio is not None:
        vector.extend([1, pokemon.hp_ratio])
    else:
        vector.extend([0, 0.0])

    vector.append(int(pokemon.fainted))

    # Actual in-battle stats (not base stats)
    if pokemon.stats is not None:
        vector.extend(
            [
                pokemon.stats.attack or 0,
                pokemon.stats.defense or 0,
                pokemon.stats.special_attack or 0,
                pokemon.stats.special_defense or 0,
                pokemon.stats.speed or 0,
            ]
        )
    else:
        vector.extend([0, 0, 0, 0, 0])

    # Base ability
    vector.extend(
        _vectorize_optional_known_id(
            pokemon.base_ability, gen=gen, get_id=ability_id
        )
    )

    # Current ability
    vector.extend(
        _vectorize_optional_known_id(
            pokemon.current_ability, gen=gen, get_id=ability_id
        )
    )

    # Item
    vector.extend(
        _vectorize_optional_known_id(pokemon.item, gen=gen, get_id=item_id)
    )

    # Moves
    for move in pokemon.moves:
        if move.present and move.name is not None:
            vector.extend([1, move_id(move.name, gen)])
        else:
            vector.extend([0, 0])

    assert len(pokemon.moves) <= 4
    for _ in range(4 - len(pokemon.moves)):
        vector.extend([0, 0])

    vector.extend(_vectorize_status(pokemon.status))

    vector.append(int(pokemon.transformed))

    # Forme known
    vector.append(1 if pokemon.forme is not None else 0)

    # Type override
    vector.append(1 if pokemon.type_override is not None else 0)

    assert len(vector) == _OWN_POKEMON_DIM
    return vector


def _vectorize_enemy_pokemon(
    pokemon: EnemyPokemonFeatures, *, gen: int
) -> Vector:
    if not pokemon.revealed:
        return [0] * _ENEMY_POKEMON_DIM

    vector: Vector = [1]

    # Species
    if pokemon.species is not None:
        species_id, form_id = pokemon_id(pokemon.species, gen)
        vector.extend([1, species_id, form_id])
    else:
        vector.extend([0, 0, 0])

    # Current species
    if (
        pokemon.current_species is not None
        and pokemon.current_species != pokemon.species
    ):
        species_id, form_id = pokemon_id(pokemon.current_species, gen)
        vector.extend([1, species_id, form_id])
    else:
        vector.extend([0, 0, 0])

    # --- No type vector here (V3) ---

    vector.append(int(pokemon.active))
    vector.append(pokemon.level or 0)

    # HP ratio
    if pokemon.hp_ratio is not None:
        vector.extend([1, pokemon.hp_ratio])
    else:
        vector.extend([0, 0.0])

    vector.append(int(pokemon.fainted))

    # Base ability
    vector.extend(
        _vectorize_knowledge_id(
            pokemon.base_ability, gen=gen, get_id=ability_id
        )
    )

    # Current ability
    vector.extend(
        _vectorize_knowledge_id(
            pokemon.current_ability, gen=gen, get_id=ability_id
        )
    )

    # Item
    vector.extend(
        _vectorize_knowledge_id(pokemon.item, gen=gen, get_id=item_id)
    )

    # Moves (Knowledge[ str ])
    for move in pokemon.moves:
        vector.extend(_vectorize_knowledge_id(move, gen=gen, get_id=move_id))

    assert len(pokemon.moves) <= 4
    for _ in range(4 - len(pokemon.moves)):
        vector.extend([0, 0])

    vector.extend(_vectorize_status(pokemon.status))

    vector.append(int(pokemon.transformed))

    # Forme known
    vector.append(1 if pokemon.forme is not None else 0)

    # Type override
    vector.append(1 if pokemon.type_override is not None else 0)

    assert len(vector) == _ENEMY_POKEMON_DIM
    return vector


def _vectorize_own_team(
    team: tuple[OwnPokemonFeatures, ...], *, gen: int
) -> Vector:
    if len(team) > 6:
        raise ValueError(f"Expected at most 6 own Pokémon, got {len(team)}")

    vector: Vector = []
    for pokemon in team:
        vector.extend(_vectorize_own_pokemon(pokemon, gen=gen))

    assert len(vector) == 6 * _OWN_POKEMON_DIM
    return vector


def _vectorize_enemy_team(
    team: tuple[EnemyPokemonFeatures, ...], *, gen: int
) -> Vector:
    if len(team) > 6:
        raise ValueError(f"Expected at most 6 enemy Pokémon, got {len(team)}")

    vector: Vector = []
    for pokemon in team:
        vector.extend(_vectorize_enemy_pokemon(pokemon, gen=gen))

    assert len(vector) == 6 * _ENEMY_POKEMON_DIM
    return vector


## Action vectorizers


def vectorize_action(action: ActionFeatures, *, gen: int) -> Vector:
    """Vectorize a single legal action.

    This is an independent API — callers should use ``vectorize_actions``
    to encode the full set of legal actions.
    """
    if action.kind == "move" and action.move is not None:
        move = action.move
        vector: Vector = [
            1,  # kind: move
            1,  # name always known
            move_id(move.name, gen),
            1 if move.disabled else 0,
            1 if move.current_pp is not None else 0,
            move.current_pp if move.current_pp is not None else 0,
            1 if move.max_pp is not None else 0,
            move.max_pp if move.max_pp is not None else 0,
            0,  # switch team_slot (not applicable)
            0,  # switch species known
            0,
            0,  # switch species id, form id
        ]
    elif action.kind == "switch" and action.switch is not None:
        s = action.switch
        species_id, form_id = (
            pokemon_id(s.species, gen) if s.species else (0, 0)
        )
        vector = [
            2,  # kind: switch
            0,  # move name known
            0,  # move id
            0,  # disabled
            0,  # current_pp known
            0,  # current_pp value
            0,  # max_pp known
            0,  # max_pp value
            s.team_slot + 1,  # switch team_slot (1-based, 0 = no slot)
            1,  # switch species known
            species_id,  # species id
            form_id,  # form id
        ]
    else:
        # Empty/padding action
        vector = [0] * _ACTION_DIM

    assert len(vector) == _ACTION_DIM
    return vector


def vectorize_actions(
    actions: tuple[ActionFeatures, ...], *, gen: int
) -> list[Vector]:
    """Vectorize all legal actions, one vector per action."""
    return [vectorize_action(action, gen=gen) for action in actions]


## Field vectorizer


def _vectorize_field(features: BattleFeatures) -> Vector:
    field = features.field

    weather_id = 0
    if field.weather is not None:
        weather_names = (
            "raindance",
            "sunnyday",
            "sandstorm",
            "hail",
            "desolateland",
            "primordialsea",
            "deltastream",
            "heavysnow",
        )
        for i, name in enumerate(weather_names, start=1):
            if field.weather == name:
                weather_id = i
                break

    gen_value = _require_gen(features)

    vector: Vector = [
        gen_value,
        field.turn,
        1 if field.weather is not None else 0,
        weather_id,
        1 if field.gen_1_desync else 0,
    ]

    vector.extend(_vectorize_side_conditions(field.own_side_conditions))
    vector.extend(_vectorize_side_conditions(field.enemy_side_conditions))
    vector.extend(_vectorize_side_conditions(field.field_conditions))

    assert len(vector) == _FIELD_DIM
    return vector


## Top-level vectorizer


def vectorize_battle_features(
    features: BattleFeatures, *, config: VectorizerConfig | None = None
) -> Vector:
    """Convert ``BattleFeatures`` into a flat baseline numeric vector.

    The returned vector contains field, team, history, and a 10-slot
    action mask. **It does not contain per-action representations.**
    Use ``vectorize_actions()`` separately to encode legal actions.

    Args:
        features: The semantic battle features to vectorize.
        config: Optional explicit configuration. If None, uses defaults
            (history_length=32).

    Returns:
        A flat list of int/float values.
    """
    if config is None:
        config = VectorizerConfig()

    gen = _require_gen(features)

    vector: Vector = []

    vector.extend(_vectorize_field(features))
    vector.extend(_vectorize_own_team(features.own_team, gen=gen))
    vector.extend(_vectorize_enemy_team(features.enemy_team, gen=gen))

    # History (flat padded/truncated sequence)
    vector.extend(
        vectorize_history(
            features.history, gen=gen, max_events=config.history_length
        )
    )

    # Action mask (10-slot compatibility: 4 moves + 6 switches)
    # This is a boolean mask only — action details come from
    # ``vectorize_actions()``.
    vector.extend(_action_mask_from_features(features))

    return vector


## Action mask


def _action_mask_from_features(features: BattleFeatures) -> Vector:
    """Legacy 10-slot action mask (4 moves + 6 switches).

    Uses ``request_index`` for moves and ``team_slot`` for switches
    to preserve the correct slot positions even when some slots are
    disabled or absent.
    """
    move_mask = [0, 0, 0, 0]

    for action in features.available_actions:
        if action.kind == "move" and action.move is not None:
            idx = action.move.request_index
            if 0 <= idx < 4:
                move_mask[idx] = 1

    switch_mask = [0, 0, 0, 0, 0, 0]

    for action in features.available_actions:
        if action.kind == "switch" and action.switch is not None:
            idx = action.switch.team_slot
            if 0 <= idx < 6:
                switch_mask[idx] = 1

    return cast(Vector, move_mask + switch_mask)


## Validation


def _require_gen(features: BattleFeatures) -> int:
    """Fail loudly if generation is missing — vocabulary lookups need it."""
    gen = features.format.gen
    if gen is None:
        raise ValueError(
            "BattleFeatures.format.gen is required for vectorization"
        )
    return gen


__all__ = [
    "VECTOR_SCHEMA_VERSION",
    "Vector",
    "VectorizerConfig",
    "vectorize_action",
    "vectorize_actions",
    "vectorize_battle_features",
]
