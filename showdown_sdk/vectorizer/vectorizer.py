from collections.abc import Callable

from showdown_sdk.models.dex import to_id
from showdown_sdk.models.pokemon.moves import AvailableMove
from showdown_sdk.models.pokemon.pokemon import EnemyPokemon, PartyPokemon, Unknown
from showdown_sdk.models.pokemon.status import MajorStatus, MinorStatus, Status
from showdown_sdk.models.pokemon.terrain import SideCondition, Weather
from showdown_sdk.models.sdk.battle_state import BattleState
from showdown_sdk.vectorizer.utils import ability_id, item_id, move_id, pokemon_id

Vector = list[int]

_MINOR_STATUSES = tuple(MinorStatus)
_SIDE_CONDITIONS = tuple(SideCondition)
_WEATHERS = tuple(w for w in Weather if w is not Weather.CLEAR_SKY)
_MAJOR_STATUS_IDS = {status: i for i, status in enumerate(MajorStatus, start=1)}

_STATUS_DIM = 7 + 1 + len(_MINOR_STATUSES) + 2
_SIDE_CONDITIONS_DIM = len(_SIDE_CONDITIONS)
_MOVESET_SLOT_DIM = 3
_AVAILABLE_MOVE_DIM = 10
_ACTION_MASK_DIM = 10

_OWN_POKEMON_DIM = (
    1  # slot present
    + 3  # species: known, pokemon id, form id
    + 1  # active
    + 1  # level
    + 2  # current HP, max HP
    + 5  # atk, def, spa, spd, spe
    + 2  # base ability: known, id
    + 2  # current ability: known, id
    + 2  # item: known, id
    + 8  # four moves * (known, id)
    + _STATUS_DIM
    + 1  # transformed
)

_ENEMY_POKEMON_DIM = (
    3  # base species
    + 3  # current species / forme
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
    3  # gen, turn, weather
    + 3 * _SIDE_CONDITIONS_DIM  # us, opponent, field
    + 2  # force switch, gen 1 desync
)

_VECTOR_DIM = (
    _FIELD_DIM
    + 6 * _OWN_POKEMON_DIM
    + 6 * _ENEMY_POKEMON_DIM
    + 4 * _AVAILABLE_MOVE_DIM
    + _ACTION_MASK_DIM
)


def _canonical_move_name(name: str) -> str:
    value = to_id(name)

    # Showdown request/team IDs can include information that is not part
    # of the Dex move ID.
    if value.startswith("hiddenpower"):
        return "hiddenpower"

    if value.startswith("return") and value[6:].isdigit():
        return "return"

    if value.startswith("frustration") and value[11:].isdigit():
        return "frustration"

    return value


def _vectorize_known_id(
    value: str | Unknown | None,
    *,
    gen: int,
    get_id: Callable[[str, int], int],
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
    value: str | Unknown | None,
    *,
    gen: int,
) -> Vector:
    if value is Unknown.VALUE:
        return [0, 0]

    if value is None or value == "":
        return [1, 0]

    canonical = _canonical_move_name(value)

    # Recharge is a synthetic Showdown request action, not a Dex move.
    # _vectorize_available_move has a separate recharge flag.
    if canonical == "recharge":
        return [1, 0]

    return [1, move_id(canonical, gen)]


def _vectorize_known_pokemon_id(
    value: str | Unknown | None,
    *,
    gen: int,
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


def _vectorize_status(status: Status) -> Vector:
    vector = [
        status.atk_stage,
        status.def_stage,
        status.spa_stage,
        status.spd_stage,
        status.spe_stage,
        status.eva_stage,
        status.acc_stage,
        0 if status.major is None else _MAJOR_STATUS_IDS[status.major],
    ]

    # Minor statuses can coexist, so they are a bitset rather than one ID.
    vector.extend(int(effect in status.minor) for effect in _MINOR_STATUSES)

    # PERISH_SONG's bit distinguishes:
    # no perish song -> 0
    # perish0        -> 0 with PERISH_SONG bit set
    vector.append(0 if status.perish_count is None else status.perish_count)

    vector.append(int(status.must_recharge))

    assert len(vector) == _STATUS_DIM
    return vector


def _vectorize_side_conditions(
    conditions: dict[SideCondition, int],
) -> Vector:
    vector = [conditions.get(condition, 0) for condition in _SIDE_CONDITIONS]

    assert len(vector) == _SIDE_CONDITIONS_DIM
    return vector


def _species_from_ident(ident: str) -> str:
    if ": " in ident:
        return ident.split(": ", 1)[1]
    return ident


def _species_from_details(details: str) -> str:
    species = details.split(",", 1)[0].strip()

    if not species:
        raise ValueError(f"Could not extract species from details: {details!r}")

    return species


def _vectorize_move_slot(
    move: str | Unknown | None,
    *,
    disabled_moves: set[str],
    gen: int,
) -> Vector:
    vector = _vectorize_known_move_id(
        move,
        gen=gen,
    )

    if move is Unknown.VALUE or move is None:
        vector.append(0)
    else:
        vector.append(int(_canonical_move_name(move) in disabled_moves))

    assert len(vector) == _MOVESET_SLOT_DIM
    return vector


def _vectorize_move_slots(
    moves: list[str | Unknown],
    *,
    disabled_moves: list[str],
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
                gen=gen,
            )
        )

    # These are actual empty slots, not unknown slots.
    for _ in range(4 - len(moves)):
        vector.extend(
            _vectorize_move_slot(
                None,
                disabled_moves=disabled,
                gen=gen,
            )
        )

    assert len(vector) == 4 * _MOVESET_SLOT_DIM
    return vector


def _vectorize_party_pokemon(
    pokemon: PartyPokemon | None,
    *,
    battle_state: BattleState,
) -> Vector:
    if pokemon is None:
        return [0] * _OWN_POKEMON_DIM

    gen = battle_state.gen

    if pokemon.active:
        status = battle_state.active_pokemon.status
        current_ability = battle_state.active_pokemon.ability
        transformed = battle_state.active_pokemon.transformed
    else:
        status = Status(
            major=pokemon.major_status,
        )
        current_ability = pokemon.base_ability
        transformed = False

    if len(pokemon.moves) > 4:
        raise ValueError(
            "Expected at most 4 moves for "
            + f"{pokemon.id!r}, got {len(pokemon.moves)}"
        )

    vector: Vector = [1]

    vector.extend(
        _vectorize_known_pokemon_id(
            _species_from_details(pokemon.details),
            gen=gen,
        )
    )

    vector.extend(
        [
            int(pokemon.active),
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
        _vectorize_known_id(
            pokemon.base_ability,
            gen=gen,
            get_id=ability_id,
        )
    )

    vector.extend(
        _vectorize_known_id(
            current_ability,
            gen=gen,
            get_id=ability_id,
        )
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
            _vectorize_known_move_id(
                move,
                gen=gen,
            )
        )

    # We know our own complete moveset, so missing slots are known empty.
    for _ in range(4 - len(pokemon.moves)):
        vector.extend([1, 0])

    vector.extend(_vectorize_status(status))

    vector.append(int(transformed))

    assert len(vector) == _OWN_POKEMON_DIM
    return vector


def _vectorize_enemy_pokemon(
    pokemon: EnemyPokemon | None,
    *,
    gen: int,
) -> Vector:
    if pokemon is None or pokemon.id is Unknown.VALUE:
        return [0] * _ENEMY_POKEMON_DIM

    base_species = _species_from_ident(pokemon.id)

    if pokemon.transformed_into is not None:
        current_species = _species_from_ident(pokemon.transformed_into)
    elif pokemon.forme is not None:
        current_species = pokemon.forme
    else:
        current_species = base_species

    vector: Vector = []

    vector.extend(
        _vectorize_known_pokemon_id(
            base_species,
            gen=gen,
        )
    )

    vector.extend(
        _vectorize_known_pokemon_id(
            current_species,
            gen=gen,
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
        _vectorize_known_id(
            pokemon.base_ability,
            gen=gen,
            get_id=ability_id,
        )
    )

    vector.extend(
        _vectorize_known_id(
            pokemon.current_ability,
            gen=gen,
            get_id=ability_id,
        )
    )

    vector.extend(
        _vectorize_known_id(
            pokemon.item,
            gen=gen,
            get_id=item_id,
        )
    )

    vector.extend(
        _vectorize_move_slots(
            list(pokemon.learnt_moves),
            disabled_moves=pokemon.disabled_moves,
            gen=gen,
        )
    )

    vector.extend(
        _vectorize_move_slots(
            list(pokemon.temporary_moves),
            disabled_moves=pokemon.disabled_moves,
            gen=gen,
        )
    )

    vector.extend(_vectorize_status(pokemon.status))

    vector.append(int(pokemon.transformed_into is not None))

    assert len(vector) == _ENEMY_POKEMON_DIM
    return vector


def _vectorize_own_team(
    battle_state: BattleState,
) -> Vector:
    if len(battle_state.team) > 6:
        raise ValueError(
            "Expected at most 6 own Pokémon, " + f"got {len(battle_state.team)}"
        )

    vector: Vector = []

    for pokemon in battle_state.team:
        vector.extend(
            _vectorize_party_pokemon(
                pokemon,
                battle_state=battle_state,
            )
        )

    for _ in range(6 - len(battle_state.team)):
        vector.extend(
            _vectorize_party_pokemon(
                None,
                battle_state=battle_state,
            )
        )

    assert len(vector) == 6 * _OWN_POKEMON_DIM
    return vector


def _vectorize_enemy_team(
    battle_state: BattleState,
) -> Vector:
    revealed = [
        pokemon
        for pokemon in battle_state.enemy_team
        if pokemon.id is not Unknown.VALUE
    ]

    if len(revealed) > 6:
        raise ValueError("Expected at most 6 enemy Pokémon, " + f"got {len(revealed)}")

    vector: Vector = []

    for pokemon in revealed:
        vector.extend(
            _vectorize_enemy_pokemon(
                pokemon,
                gen=battle_state.gen,
            )
        )

    for _ in range(6 - len(revealed)):
        vector.extend(
            _vectorize_enemy_pokemon(
                None,
                gen=battle_state.gen,
            )
        )

    assert len(vector) == 6 * _ENEMY_POKEMON_DIM
    return vector


def _vectorize_available_move(
    move: AvailableMove | None,
    *,
    gen: int,
) -> Vector:
    if move is None:
        return [0] * _AVAILABLE_MOVE_DIM

    name = move.id or move.name
    canonical = _canonical_move_name(name)

    vector: Vector = [1]

    vector.extend(
        _vectorize_known_move_id(
            name,
            gen=gen,
        )
    )

    vector.extend(
        [
            int(canonical == "recharge"),
            int(canonical == "struggle"),
        ]
    )

    # PP being None is known information for synthetic moves such as
    # Recharge and Struggle, not an unknown value.
    vector.extend(
        [
            1,
            0 if move.curr_pp is None else move.curr_pp,
            1,
            0 if move.max_pp is None else move.max_pp,
            int(move.disabled),
        ]
    )

    assert len(vector) == _AVAILABLE_MOVE_DIM
    return vector


def _vectorize_available_moves(
    battle_state: BattleState,
) -> Vector:
    moves = battle_state.available_moves

    if len(moves) > 4:
        raise ValueError("Expected at most 4 available moves, " + f"got {len(moves)}")

    vector: Vector = []

    for move in moves:
        vector.extend(
            _vectorize_available_move(
                move,
                gen=battle_state.gen,
            )
        )

    for _ in range(4 - len(moves)):
        vector.extend(
            _vectorize_available_move(
                None,
                gen=battle_state.gen,
            )
        )

    assert len(vector) == 4 * _AVAILABLE_MOVE_DIM
    return vector


def _vectorize_action_mask(
    battle_state: BattleState,
) -> Vector:
    move_mask = [0, 0, 0, 0]

    if not battle_state.force_switch:
        for i, move in enumerate(battle_state.available_moves):
            move_mask[i] = int(not move.disabled)

    switch_mask = [0, 0, 0, 0, 0, 0]

    has_decision = battle_state.force_switch or bool(battle_state.available_moves)

    trapped = (
        MinorStatus.TRAPPED in battle_state.active_pokemon.status.minor
        or MinorStatus.PARTIALLY_TRAPPED in battle_state.active_pokemon.status.minor
    )

    if has_decision and (battle_state.force_switch or not trapped):
        for i, pokemon in enumerate(battle_state.team):
            switch_mask[i] = int(pokemon.curr_hp > 0 and not pokemon.active)

    vector = move_mask + switch_mask

    assert len(vector) == _ACTION_MASK_DIM
    return vector


def _vectorize_field(
    battle_state: BattleState,
) -> Vector:
    player_id = battle_state.player_id

    if player_id is None:
        raise ValueError("BattleState.player_id is not initialized")

    own_conditions = battle_state.side_conditions.get(
        player_id,
        {},
    )

    field_conditions = battle_state.side_conditions.get(
        "field",
        {},
    )

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
        battle_state.side_conditions.get(
            foe_side_ids[0],
            {},
        )
        if foe_side_ids
        else {}
    )

    weather_id = 0

    if battle_state.weather not in {
        None,
        Weather.CLEAR_SKY.value,
    }:
        for i, weather in enumerate(
            _WEATHERS,
            start=1,
        ):
            if battle_state.weather == weather.value:
                weather_id = i
                break
        else:
            raise ValueError("Unknown weather: " + f"{battle_state.weather!r}")

    vector: Vector = [
        battle_state.gen,
        battle_state.turn,
        weather_id,
    ]

    vector.extend(_vectorize_side_conditions(own_conditions))

    vector.extend(_vectorize_side_conditions(foe_conditions))

    vector.extend(_vectorize_side_conditions(field_conditions))

    vector.extend(
        [
            int(battle_state.force_switch),
            int(battle_state.gen_1_desync),
        ]
    )

    assert len(vector) == _FIELD_DIM
    return vector


def vectorize_battle_state(
    battle_state: BattleState,
) -> Vector:
    vector: Vector = []

    vector.extend(_vectorize_field(battle_state))

    vector.extend(_vectorize_own_team(battle_state))

    vector.extend(_vectorize_enemy_team(battle_state))

    vector.extend(_vectorize_available_moves(battle_state))

    vector.extend(_vectorize_action_mask(battle_state))

    assert len(vector) == _VECTOR_DIM
    return vector


__all__ = ["vectorize_battle_state"]
