"""Feature objects and converters for individual Pokémon."""

from dataclasses import dataclass, field

from showdown_sdk.features.common import (
    Knowledge,
    StatFeatures,
    StatusFeatures,
    ability_to_feature,
    canonical,
    canonical_move,
    hp_ratio,
    knowledge,
    unknown,
)
from showdown_sdk.models.pokemon import (
    EnemyPokemon,
    MajorStatus,
    PartyPokemon,
    Status,
    Unknown,
)
from showdown_sdk.models.sdk import BattleState

## Data models


@dataclass(frozen=True)
class OwnMoveFeatures:
    """One move slot of an own Pokémon.

    ``present=False`` marks an empty/padding slot; SDK-loaded moves do not
    track in-battle PP, so that stays ``None`` unless a future request provides
    it.
    """

    present: bool
    name: str | None = None
    current_pp: int | None = None
    max_pp: int | None = None
    disabled: bool = False


@dataclass(frozen=True)
class OwnPokemonFeatures:
    present: bool
    slot: int
    species: str | None = None
    current_species: str | None = None
    level: int | None = None
    pokemon_id: str | None = None
    active: bool = False
    fainted: bool = False
    hp_ratio: float | None = None
    hp_current: int | None = None
    hp_max: int | None = None
    base_ability: str | None = None
    current_ability: str | None = None
    item: str | None = None
    moves: tuple[OwnMoveFeatures, ...] = ()
    status: StatusFeatures = field(default_factory=StatusFeatures)
    stats: StatFeatures | None = None
    transformed: bool = False
    forme: str | None = None
    type_override: tuple[str, ...] | None = None


@dataclass(frozen=True)
class EnemyPokemonFeatures:
    revealed: bool
    slot: int
    species: str | None = None
    current_species: str | None = None
    level: int | None = None
    pokemon_id: str | None = None
    active: bool = False
    fainted: bool = False
    hp_ratio: float | None = None
    base_ability: Knowledge[str] = field(default_factory=unknown)
    current_ability: Knowledge[str] = field(default_factory=unknown)
    item: Knowledge[str] = field(default_factory=unknown)
    moves: tuple[Knowledge[str], ...] = ()
    temporary_moves: tuple[str, ...] = ()
    status: StatusFeatures = field(default_factory=StatusFeatures)
    transformed: bool = False
    forme: str | None = None
    type_override: tuple[str, ...] | None = None


## Feature builders


def status_to_features(status: Status) -> StatusFeatures:
    return StatusFeatures(
        major=status.major.value if status.major is not None else None,
        attack_stage=status.atk_stage,
        defense_stage=status.def_stage,
        special_attack_stage=status.spa_stage,
        special_defense_stage=status.spd_stage,
        speed_stage=status.spe_stage,
        accuracy_stage=status.acc_stage,
        evasion_stage=status.eva_stage,
        minor=tuple(canonical(s.value) for s in sorted(status.minor, key=str)),
        perish_count=status.perish_count,
        must_recharge=status.must_recharge,
    )


def species_from_details(details: str) -> str:
    species = details.split(",", 1)[0].strip()

    if not species:
        raise ValueError(f"Could not extract species from details: {details!r}")

    return species


def _fainted(status: StatusFeatures) -> bool:
    return status.major == canonical(MajorStatus.FAINT.value)


def own_pokemon_to_features(
    battle_state: BattleState, pokemon: PartyPokemon | None, *, slot: int
) -> OwnPokemonFeatures:
    """Convert one own Pokémon. ``slot`` is 0-based and preserved as given."""
    if pokemon is None:
        return OwnPokemonFeatures(present=False, slot=slot)

    assert isinstance(pokemon.id, str)

    active_state = battle_state.active_pokemon
    is_active = pokemon.id == battle_state.curr_pokemon

    base_species = canonical(species_from_details(pokemon.details))

    if is_active:
        status = status_to_features(active_state.status)
        transform_target = active_state.transformed_into
        transformed = transform_target is not None
        forme = active_state.forme

        current_species = base_species
        if forme is not None:
            current_species = canonical(forme)
        if transform_target is not None:
            current_species = canonical(transform_target)

        current_ability: str | None = (
            active_state.ability
            if active_state.ability is not Unknown.VALUE
            else None
        )
        type_override = active_state.type_override
    else:
        status = StatusFeatures(
            major=(pokemon.major_status.value if pokemon.major_status else None)
        )
        transformed = False
        forme = None
        current_species = base_species
        current_ability = pokemon.base_ability
        type_override = None

    hp_max = pokemon.max_hp

    return OwnPokemonFeatures(
        present=True,
        slot=slot,
        pokemon_id=canonical(pokemon.id),
        species=base_species,
        current_species=current_species,
        level=pokemon.lvl,
        active=is_active,
        fainted=_fainted(status) or pokemon.curr_hp <= 0,
        hp_ratio=hp_ratio(pokemon.curr_hp, hp_max),
        hp_current=pokemon.curr_hp,
        hp_max=hp_max,
        base_ability=ability_to_feature(pokemon.base_ability),
        current_ability=ability_to_feature(current_ability),
        item=canonical(pokemon.item) if pokemon.item else None,
        moves=tuple(
            [
                OwnMoveFeatures(present=True, name=canonical_move(move))
                for move in pokemon.moves
            ]
            + [
                OwnMoveFeatures(present=False)
                for _ in range(4 - len(pokemon.moves))
            ]
        )[:4],
        status=status,
        stats=StatFeatures(
            hp=hp_max,
            attack=pokemon.stats.atk,
            defense=pokemon.stats.def_,
            special_attack=pokemon.stats.spa,
            special_defense=pokemon.stats.spd,
            speed=pokemon.stats.spe,
        ),
        transformed=transformed,
        forme=canonical(forme) if forme else None,
        type_override=(
            tuple(canonical(t) for t in type_override)
            if type_override is not None
            else None
        ),
    )


def own_team_to_features(
    battle_state: BattleState,
) -> tuple[OwnPokemonFeatures, ...]:
    """Exactly six slots, in team order, padded with absent entries."""
    if len(battle_state.team) > 6:
        raise ValueError(
            f"Expected at most 6 own Pokémon, got {len(battle_state.team)}"
        )

    return tuple(
        own_pokemon_to_features(battle_state, pokemon, slot=slot)
        for slot, pokemon in enumerate(battle_state.team)
    ) + tuple(
        OwnPokemonFeatures(present=False, slot=slot)
        for slot in range(len(battle_state.team), 6)
    )


def _empty_enemy_pokemon(slot: int) -> EnemyPokemonFeatures:
    return EnemyPokemonFeatures(
        revealed=False,
        slot=slot,
        moves=(unknown(), unknown(), unknown(), unknown()),
    )


def enemy_pokemon_to_features(
    pokemon: EnemyPokemon | None, *, slot: int
) -> EnemyPokemonFeatures:
    """Convert one enemy Pokémon; ``slot`` is 0-based, reveal order."""
    if pokemon is None or pokemon.id is Unknown.VALUE:
        return _empty_enemy_pokemon(slot)

    base_species = (
        canonical(pokemon.species) if pokemon.species is not None else None
    )
    transform_target = pokemon.transformed_into
    forme = pokemon.forme
    transformed = transform_target is not None
    current_species = (
        canonical(transform_target)
        if transformed
        else canonical(forme)
        if forme is not None
        else base_species
    )

    moves: list[Knowledge[str]] = [
        (
            Knowledge(known=True, value=canonical_move(m))
            if m is not Unknown.VALUE
            else unknown()
        )
        for m in pokemon.learnt_moves
    ]

    # Keep exactly four slots: pad with unknown, trim excess.
    moves += [unknown()] * (4 - len(moves))
    moves = moves[:4]

    hp = hp_ratio(pokemon.curr_hp_percent, 100)

    return EnemyPokemonFeatures(
        revealed=True,
        slot=slot,
        species=base_species,
        current_species=current_species,
        level=pokemon.lvl,
        pokemon_id=canonical(pokemon.id),
        active=pokemon.active,
        fainted=pokemon.fainted,
        hp_ratio=hp,
        base_ability=(
            Knowledge(
                known=True, value=ability_to_feature(pokemon.base_ability)
            )
            if pokemon.base_ability is not Unknown.VALUE
            else unknown()
        ),
        current_ability=(
            Knowledge(
                known=True, value=ability_to_feature(pokemon.current_ability)
            )
            if pokemon.current_ability is not Unknown.VALUE
            else unknown()
        ),
        item=(
            knowledge(pokemon.item)
            if pokemon.item is not Unknown.VALUE
            else unknown()
        ),
        moves=tuple(moves),
        temporary_moves=tuple(
            canonical_move(m) for m in pokemon.temporary_moves
        ),
        status=status_to_features(pokemon.status),
        transformed=transformed,
        forme=canonical(forme) if forme else None,
        type_override=pokemon.type_override,
    )


def enemy_team_to_features(
    battle_state: BattleState,
) -> tuple[EnemyPokemonFeatures, ...]:
    """Exactly six slots in stable reveal order; unrevealed slots are not
    dropped."""
    revealed = [
        pokemon
        for pokemon in battle_state.enemy_team
        if pokemon.id is not Unknown.VALUE
    ]

    if len(revealed) > 6:
        raise ValueError(
            f"Expected at most 6 enemy Pokémon, got {len(revealed)}"
        )

    converted = tuple(
        enemy_pokemon_to_features(pokemon, slot=slot)
        for slot, pokemon in enumerate(revealed)
    )

    return converted + tuple(
        _empty_enemy_pokemon(slot) for slot in range(len(revealed), 6)
    )
