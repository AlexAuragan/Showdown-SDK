from typing import cast

from showdown_sdk.exceptions import CombatHandlerError
from showdown_sdk.features import (
    ActionFeatures,
    BattleFeatures,
    EnemyPokemonFeatures,
    MoveActionFeatures,
    OwnPokemonFeatures,
    StatusFeatures,
    SwitchActionFeatures,
)
from showdown_sdk.models import dex, to_id
from showdown_sdk.models.sdk import BattleState
from showdown_sdk.utils import Serializable, SerializableObject, expect_array

type Action = tuple[str, int]
type PokemonFeatures = OwnPokemonFeatures | EnemyPokemonFeatures
type MoveEntry = tuple[ActionFeatures, MoveActionFeatures]
type SwitchEntry = tuple[ActionFeatures, SwitchActionFeatures]


def require_singles(features: BattleFeatures) -> int:
    gen = features.format.gen
    if gen is None:
        raise CombatHandlerError("Battle generation is not initialized")

    if features.format.gametype not in {None, "singles"}:
        raise CombatHandlerError(
            "These baseline handlers currently support singles only"
        )

    return gen


def move_entries(features: BattleFeatures) -> list[MoveEntry]:
    entries: list[MoveEntry] = []

    for action in features.available_actions:
        if action.kind == "move" and action.move is not None:
            entries.append((action, action.move))

    return entries


def switch_entries(features: BattleFeatures) -> list[SwitchEntry]:
    entries: list[SwitchEntry] = []

    for action in features.available_actions:
        if action.kind == "switch" and action.switch is not None:
            entries.append((action, action.switch))

    return entries


def active_own(features: BattleFeatures) -> OwnPokemonFeatures | None:
    slot = features.own_active_slot

    if slot is None or not (0 <= slot < len(features.own_team)):
        return None

    mon = features.own_team[slot]
    return mon if mon.present else None


def active_enemy(features: BattleFeatures) -> EnemyPokemonFeatures | None:
    slot = features.enemy_active_slot

    if slot is None or not (0 <= slot < len(features.enemy_team)):
        return None

    mon = features.enemy_team[slot]
    return mon if mon.revealed else None


def fallback_legal_actions(features: BattleFeatures) -> list[Action]:
    actions: list[Action] = []

    for action in features.available_actions:
        if action.kind == "move" and action.move is not None:
            actions.append(("move", action.move.request_index + 1))
        elif action.kind == "switch" and action.switch is not None:
            actions.append(("switch", action.switch.team_slot + 1))

    if not actions:
        raise CombatHandlerError("No legal actions available")

    return actions


def request_move_data(
    battle_state: BattleState, move: MoveActionFeatures, gen: int
) -> SerializableObject:
    return resolved_request_move(battle_state, move, gen)[1]


def resolved_request_move(
    battle_state: BattleState, move: MoveActionFeatures, gen: int
) -> tuple[str, SerializableObject]:
    try:
        raw_move = battle_state.available_moves[move.request_index]
    except IndexError as exc:
        raise CombatHandlerError(
            f"Move request index is out of range: {move.request_index}"
        ) from exc

    raw_id = to_id(raw_move.id or raw_move.name)
    raw_name = raw_move.name or raw_move.id
    lookup_id = raw_id

    if raw_id in {"recharge", "fight"}:
        return raw_id, {"exists": True, "name": "Recharge", "id": raw_id}

    if raw_id.startswith("hiddenpower"):
        lookup_id = "hiddenpower"

    data = move_data(gen, lookup_id)
    effective_id = raw_id

    if raw_id == "hiddenpower":
        normalized_name = to_id(raw_name)

        if normalized_name.startswith("hiddenpower"):
            suffix = normalized_name[len("hiddenpower") :]

            digits = "".join(char for char in suffix if char.isdigit())
            hp_type = "".join(char for char in suffix if not char.isdigit())

            if hp_type:
                data["type"] = hp_type

            if digits:
                data["basePower"] = int(digits)

            effective_id = f"hiddenpower{hp_type}" if hp_type else "hiddenpower"

    return effective_id, data


def move_data(gen: int, move: str) -> SerializableObject:
    raw = dex.gen(gen).move(move)

    if not isinstance(raw, dict):
        raise CombatHandlerError(f"Invalid dex move data for {move!r}: {raw!r}")

    return raw


def _species_data(gen: int, species: str) -> SerializableObject:
    raw = dex.gen(gen).pokemon(species)

    if not isinstance(raw, dict):
        raise CombatHandlerError(
            f"Invalid dex species data for {species!r}: {raw!r}"
        )

    return raw


def pokemon_species(mon: PokemonFeatures) -> str:
    species = mon.current_species or mon.species

    if not species:
        raise CombatHandlerError(f"Pokemon species is unknown: {mon!r}")

    return species


def pokemon_types(mon: PokemonFeatures, gen: int) -> list[str]:
    if mon.type_override:
        return list(mon.type_override)

    species = pokemon_species(mon)
    species_data = _species_data(gen, species)
    raw_types = species_data.get("types")

    if not isinstance(raw_types, list):
        raise CombatHandlerError(
            f"Invalid type data for {species!r}: {raw_types!r}"
        )

    return cast("list[str]", raw_types)


def base_stat(mon: PokemonFeatures, stat: str, gen: int) -> float:
    species = pokemon_species(mon)
    species_data = _species_data(gen, species)
    raw_stats = species_data.get("baseStats")

    if not isinstance(raw_stats, dict):
        raise CombatHandlerError(
            f"Invalid baseStats for {species!r}: {raw_stats!r}"
        )

    key = {
        "atk": "atk",
        "def": "def",
        "spa": "spa",
        "spd": "spd",
        "spe": "spe",
    }[stat]

    value = raw_stats.get(key)

    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise CombatHandlerError(
            f"Invalid {key} base stat for {species!r}: {value!r}"
        )

    return float(value)


def stage(status: StatusFeatures, stat: str) -> int:
    attr = {
        "atk": "attack_stage",
        "def": "defense_stage",
        "spa": "special_attack_stage",
        "spd": "special_defense_stage",
        "spe": "speed_stage",
        "accuracy": "accuracy_stage",
        "evasion": "evasion_stage",
    }.get(to_id(stat))

    if attr is None:
        return 0

    return getattr(status, attr)


def hp_ratio(mon: PokemonFeatures) -> float:
    if mon.hp_ratio is None:
        return 1.0

    return mon.hp_ratio


def type_multiplier(
    attacking_type: str, defending_types: list[str], gen: int
) -> float:
    if not attacking_type:
        return 1.0

    multiplier = 1.0

    for defending_type in defending_types:
        raw_type = dex.gen(gen).types[defending_type]

        if not isinstance(raw_type, dict):
            raise CombatHandlerError(
                f"Invalid type dex entry for {defending_type!r}: {raw_type!r}"
            )

        raw_damage_taken = raw_type.get("damageTaken")

        if not isinstance(raw_damage_taken, dict):
            raise CombatHandlerError(
                f"Invalid damageTaken table for {defending_type!r}"
            )

        code: int | None = None

        for attack_name, raw_code in raw_damage_taken.items():
            if (
                to_id(attack_name) == to_id(attacking_type)
                and isinstance(raw_code, int)
                and not isinstance(raw_code, bool)
            ):
                code = raw_code
                break

        # Pokémon Showdown TypeData damageTaken encoding:
        # 0 = neutral, 1 = weak, 2 = resist, 3 = immune.
        multiplier *= {0: 1.0, 1: 2.0, 2: 0.5, 3: 0.0}.get(
            code if code is not None else 0, 1.0
        )

    return multiplier


def accuracy(raw: Serializable) -> float:
    if raw is True:
        return 1.0

    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw) / 100.0

    return 1.0


def expected_hits(move_id: str, raw_multihit: Serializable) -> float:
    move_id = to_id(move_id)

    if move_id in {"triplekick", "tripleaxel"}:
        return 1 + 2 * 0.9 + 3 * 0.81

    if isinstance(raw_multihit, int) and not isinstance(raw_multihit, bool):
        return float(raw_multihit)

    if (
        isinstance(raw_multihit, list)
        and len(raw_multihit) == 2
        and all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in raw_multihit
        )
    ):
        raw_multihit = expect_array(raw_multihit)
        min_hits = cast("int", raw_multihit[0])
        max_hits = cast("int", raw_multihit[1])

        if min_hits == max_hits:
            return float(min_hits)

        if (min_hits, max_hits) == (2, 5):
            # Same formula as poke-env's Move.expected_hits.
            return (2 + 3) / 3 + (4 + 5) / 6

        return (min_hits + max_hits) / 2

    return 1.0


def number(value: object, *, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    return default
