"""Privileged feature extraction from a custom Showdown battle snapshot."""

from dataclasses import replace

from showdown_sdk.exceptions import FeatureExtractionError
from showdown_sdk.features.battle import BattleFeatures, battle_to_features
from showdown_sdk.features.common import (
    Knowledge,
    StatFeatures,
    StatusFeatures,
    ability_to_feature,
    canonical,
    hp_ratio,
    knowledge,
    parse_move_name,
)
from showdown_sdk.features.moves import (
    MoveMechanicsFeatures,
    move_mechanics_to_features,
)
from showdown_sdk.features.pokemon import (
    EnemyPokemonFeatures,
    pokemon_mechanics_to_features,
)
from showdown_sdk.models.sdk import BattleState
from showdown_sdk.utils import (
    SerializableObject,
    expect_array,
    expect_bool,
    expect_int,
    expect_object,
    expect_string,
)


def oracle_battle_to_features(battle_state: BattleState) -> BattleFeatures:
    """Build privileged training features from the synchronized Showdown state.

    Public information, actions, and history still come from the SDK's
    reconstructed BattleState. Only the opponent team is replaced by the
    simulator's complete ground-truth representation.
    """

    public = battle_to_features(battle_state)

    snapshot = battle_state.custom_showdown_battlestate
    if snapshot is None:
        raise FeatureExtractionError(
            "Oracle features require custom_showdown_battlestate"
        )

    player_id = battle_state.player_id
    if player_id is None:
        raise FeatureExtractionError(
            "Oracle features require BattleState.player_id"
        )

    sides = [
        expect_object(side, name="showdown side")
        for side in expect_array(snapshot["sides"], name="showdown sides")
    ]

    opponent_sides = [
        side
        for side in sides
        if expect_string(side["id"], name="showdown side id") != player_id
    ]

    if len(opponent_sides) != 1:
        raise FeatureExtractionError(
            f"Expected one opponent side, got {len(opponent_sides)}"
        )

    opponent_side = opponent_sides[0]

    raw_team = [
        expect_object(pokemon, name="showdown pokemon")
        for pokemon in expect_array(
            opponent_side["pokemon"], name="showdown opponent team"
        )
    ]

    if len(raw_team) > 6:
        raise FeatureExtractionError(
            f"Expected at most 6 opponent Pokémon, got {len(raw_team)}"
        )

    gen = battle_state.format.gen
    if gen is None:
        raise FeatureExtractionError(
            "Oracle features require the battle generation"
        )

    oracle_team = [
        _oracle_enemy_pokemon(raw, gen=gen, slot=index)
        for index, raw in enumerate(raw_team)
    ]

    aligned_team = _align_with_public_slots(
        oracle_team=oracle_team, public_team=public.enemy_team
    )

    while len(aligned_team) < 6:
        aligned_team.append(_empty_oracle_slot(len(aligned_team)))

    aligned_team = [
        replace(pokemon, slot=index)
        for index, pokemon in enumerate(aligned_team[:6])
    ]

    enemy_active_slot = next(
        (
            pokemon.slot
            for pokemon in aligned_team
            if pokemon.revealed and pokemon.active
        ),
        None,
    )

    return replace(
        public,
        enemy_team=tuple(aligned_team),
        enemy_active_slot=enemy_active_slot,
    )


def _oracle_enemy_pokemon(
    raw: SerializableObject, gen: int, slot: int
) -> EnemyPokemonFeatures:
    pokemon_set = expect_object(raw["set"], name="showdown pokemon set")

    base_species = canonical(
        expect_string(
            pokemon_set["speciesId"], name="showdown pokemon set speciesId"
        )
    )

    species_state = expect_object(
        raw["speciesState"], name="showdown pokemon speciesState"
    )

    current_species = canonical(
        expect_string(
            species_state["id"], name="showdown pokemon speciesState.id"
        )
    )

    transformed = expect_bool(
        raw["transformed"], name="showdown pokemon transformed"
    )

    raw_types = tuple(
        canonical(expect_string(value, name="showdown pokemon type"))
        for value in expect_array(raw["types"], name="showdown pokemon types")
    )

    normal_mechanics = pokemon_mechanics_to_features(current_species, gen=gen)

    type_override = (
        raw_types if raw_types and raw_types != normal_mechanics.types else None
    )

    mechanics = pokemon_mechanics_to_features(
        current_species, gen=gen, type_override=type_override
    )

    move_slots = [
        expect_object(move, name="showdown move slot")
        for move in expect_array(
            raw["moveSlots"], name="showdown pokemon moveSlots"
        )
    ]

    moves: list[Knowledge[str]] = []
    move_mechanics: list[Knowledge[MoveMechanicsFeatures]] = []

    for move_slot in move_slots[:4]:
        parsed = parse_move_name(
            expect_string(move_slot["id"], name="showdown move id")
        )

        moves.append(Knowledge(known=True, value=parsed.id))
        move_mechanics.append(
            Knowledge(
                known=True, value=move_mechanics_to_features(parsed, gen=gen)
            )
        )

    while len(moves) < 4:
        moves.append(Knowledge(known=True, value=None))
        move_mechanics.append(Knowledge(known=True, value=None))

    boosts = expect_object(raw["boosts"], name="showdown pokemon boosts")
    volatiles = expect_object(
        raw["volatiles"], name="showdown pokemon volatiles"
    )

    raw_status = expect_string(raw["status"], name="showdown pokemon status")

    major_status = None if raw_status in {"", "fnt"} else canonical(raw_status)

    status = StatusFeatures(
        major=major_status,
        attack_stage=expect_int(boosts["atk"], name="boost atk"),
        defense_stage=expect_int(boosts["def"], name="boost def"),
        special_attack_stage=expect_int(boosts["spa"], name="boost spa"),
        special_defense_stage=expect_int(boosts["spd"], name="boost spd"),
        speed_stage=expect_int(boosts["spe"], name="boost spe"),
        accuracy_stage=expect_int(boosts["accuracy"], name="boost accuracy"),
        evasion_stage=expect_int(boosts["evasion"], name="boost evasion"),
        must_recharge="mustrecharge" in volatiles,
    )

    stored_stats = expect_object(
        raw["storedStats"], name="showdown pokemon storedStats"
    )

    max_hp = expect_int(raw["maxhp"], name="showdown pokemon maxhp")
    current_hp = expect_int(raw["hp"], name="showdown pokemon hp")

    stats = StatFeatures(
        hp=max_hp,
        attack=expect_int(stored_stats["atk"], name="showdown pokemon atk"),
        defense=expect_int(stored_stats["def"], name="showdown pokemon def"),
        special_attack=expect_int(
            stored_stats["spa"], name="showdown pokemon spa"
        ),
        special_defense=expect_int(
            stored_stats["spd"], name="showdown pokemon spd"
        ),
        speed=expect_int(stored_stats["spe"], name="showdown pokemon spe"),
    )

    base_ability = ability_to_feature(
        expect_string(raw["baseAbility"], name="showdown pokemon baseAbility")
    )

    current_ability = ability_to_feature(
        expect_string(raw["ability"], name="showdown pokemon ability")
    )

    level = expect_int(pokemon_set["level"], name="showdown pokemon level")

    return EnemyPokemonFeatures(
        revealed=True,
        slot=slot,
        species=base_species,
        current_species=current_species,
        level=level,
        pokemon_id=base_species,
        active=expect_bool(raw["isActive"], name="showdown pokemon isActive"),
        fainted=expect_bool(raw["fainted"], name="showdown pokemon fainted"),
        hp_ratio=hp_ratio(current_hp, max_hp),
        base_ability=Knowledge(known=True, value=base_ability),
        current_ability=Knowledge(known=True, value=current_ability),
        item=knowledge(
            expect_string(raw["item"], name="showdown pokemon item")
        ),
        moves=tuple(moves),
        status=status,
        stats=stats,
        transformed=transformed,
        forme=(
            current_species
            if current_species != base_species and not transformed
            else None
        ),
        type_override=type_override,
        mechanics=mechanics,
        move_mechanics=tuple(move_mechanics),
    )


def _align_with_public_slots(
    oracle_team: list[EnemyPokemonFeatures],
    public_team: tuple[EnemyPokemonFeatures, ...],
) -> list[EnemyPokemonFeatures]:
    """Preserve reveal-order slots already established by public history."""

    remaining = list(oracle_team)
    aligned: list[EnemyPokemonFeatures] = []

    for public_pokemon in public_team:
        if not public_pokemon.revealed or public_pokemon.species is None:
            continue

        match_index = next(
            (
                index
                for index, oracle_pokemon in enumerate(remaining)
                if oracle_pokemon.species == public_pokemon.species
            ),
            None,
        )

        if match_index is None:
            raise FeatureExtractionError(
                "Could not align revealed opponent Pokémon "
                + f"{public_pokemon.species!r} with Showdown state"
            )

        aligned.append(remaining.pop(match_index))

    aligned.extend(remaining)

    return aligned


def _empty_oracle_slot(slot: int) -> EnemyPokemonFeatures:
    return EnemyPokemonFeatures(
        revealed=False,
        slot=slot,
        moves=(
            Knowledge(known=False, value=None),
            Knowledge(known=False, value=None),
            Knowledge(known=False, value=None),
            Knowledge(known=False, value=None),
        ),
        move_mechanics=(
            Knowledge(known=False, value=None),
            Knowledge(known=False, value=None),
            Knowledge(known=False, value=None),
            Knowledge(known=False, value=None),
        ),
    )
