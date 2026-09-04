from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, override

from python_showdown.classes.combat_handler.battle_manager import BattleManager
from python_showdown.classes.parser.events.base import BaseEvent
from python_showdown.classes.parser.models import (
    EffectSource,
    PokemonIdent,
    RequestMove,
    RequestPokemon,
)
from python_showdown.models.dex import dex, to_id
from python_showdown.models.pokemon.moves import AvailableMove
from python_showdown.models.pokemon.pokemon import EnemyPokemon, PartyPokemon, Unknown
from python_showdown.models.pokemon.status import (
    MajorStatus,
    MinorStatus,
    Stat,
    Stats,
    Status,
)
from python_showdown.models.pokemon.terrain import SideCondition, Weather
from python_showdown.models.sdk.battle_state import BattleState, SourceType
from python_showdown.utils.serialization import SerializableObject


def _ident_raw(ident: PokemonIdent) -> str:
    """Reconstruct the protocol identifier string (`p2a: Magnemite`)."""
    if ident.slot is not None:
        return f"{ident.player}{ident.slot}: {ident.name}"
    return f"{ident.player}: {ident.name}"


def _ident_self_key(ident: PokemonIdent) -> str:
    """The side-level identifier used by `PartyPokemon.id` (`p1: Miltank`)."""
    return f"{ident.player}: {ident.name}"


def _is_self(battle_state: BattleState, ident: PokemonIdent) -> bool:
    if not battle_state.player_id:
        raise ValueError("Battle State has no player_id.")
    return bool(battle_state.player_id) and (ident.player == battle_state.player_id)


def _resolve_enemy(
    battle_state: BattleState,
    ident: PokemonIdent | None,
) -> EnemyPokemon | None:
    if (
        ident is None
        or not battle_state.player_id
        or ident.player == battle_state.player_id
    ):
        return None

    pokemon = battle_state.get_enemy_pokemon(
        _ident_raw(ident),
        not_found_ok=True,
    )
    if pokemon is not None:
        return pokemon

    if ident.slot is None:
        return battle_state.get_enemy_pokemon(
            f"{ident.player}a: {ident.name}",
            not_found_ok=True,
        )

    return None


def _resolve_self(battle_state: BattleState, ident: PokemonIdent | None):
    if (
        ident is None
        or not battle_state.player_id
        or ident.player != battle_state.player_id
    ):
        return None
    key = _ident_self_key(ident)
    return next((p for p in battle_state.team if p.id == key), None)

def _resolve_any_status(battle_state: BattleState, ident: PokemonIdent) -> Status:
    if _is_self(battle_state, ident):
        return battle_state.curr_pokemon_status
    pokemon = _resolve_enemy(battle_state, ident)
    if pokemon is None:
        raise RuntimeError(f"Pokemon {ident} not found in enemy team {battle_state.enemy_team}")
    return pokemon.status

def _parse_details(details: str) -> tuple[str | None, bool]:
    """Extract gender and shiny from a switch `details` string, returning the
    cleaned details along with them."""
    shiny = False
    gender: str | None = None
    if ", shiny" in details:
        shiny = True
        details = details.replace(", shiny", "")
    if ", M" in details:
        gender = "M"
        details = details.replace(", M", "")
    elif ", F" in details:
        gender = "F"
        details = details.replace(", F", "")
    return gender, shiny

def _reveal_effect_source(
    battle_state: BattleState,
    source: EffectSource,
) -> None:
    if source.actor is None or source.name is None:
        return

    enemy = _resolve_enemy(battle_state, source.actor)
    if enemy is None:
        return

    if source.type == SourceType.ITEM:
        if enemy.item is Unknown.VALUE:
            enemy.item = source.name
        else:
            if "berry" in source.name.lower():
                return # Consummable can be an effect for a pokemon with no item anymore
            assert enemy.item == source.name, f"{enemy.item=}, {source.name=}"
    return
    # Actually the source.actor is not always the owner of the ability, we can scratch that.
    # I wonder if we could use the dex to disambiguate the ability owner.
    # There also the issue of changing ability we need to catch, much like consummable items

class BattleEvent(BaseEvent, metaclass=ABCMeta):
    @abstractmethod
    def _update_battle_state(self, battle_state: BattleState) -> None:
        pass

    def update_battle_state(self, battle_state: BattleState) -> None:

        # Atuo reveal item / abilities if it is a source of an effect
        source = getattr(self, "source", None)
        if isinstance(source, EffectSource):
            _reveal_effect_source(battle_state, source)

        self._update_battle_state(battle_state)


    def update_manager(self, manager: BattleManager) -> None: # pyright: ignore[reportUnusedParameter]
        return


@dataclass(frozen=True)
class MoveEvent(BattleEvent):
    """Base move event"""

    action_id: int
    move: str
    source_pokemon: PokemonIdent
    target_pokemon: PokemonIdent | None
    success: bool  # The move did not fail.
    does_hit: bool  # The move did not miss or hit an immunity.
    failure_reason: str | None = None
    hit_count: int | None = None
    source: EffectSource | None = None

    def __post_init__(self) -> None:
        if not self.success and self.does_hit:
            raise ValueError("A failed move cannot be marked as having hit")
        if self.hit_count is not None and self.hit_count <= 0:
            raise ValueError("A move hit count must be positive")

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:

        if battle_state.gen_1_desync:
            # After a desync, the next move can be bogus
            battle_state.gen_1_desync = False
            return

        # Ignore moves that are copies
        gen = battle_state.gen
        if gen is None:
            raise RuntimeError("gen is not set")
        if self.source is not None:
            if self.source.type == SourceType.MOVE and self.source.name == "Mirror Move":
                return
            if self.source.type == SourceType.ABILITY and self.source.name == "Magic Bounce":
                return
            if self.source.name == self.move and to_id(self.move) in dex.get_charge_moves(gen):
                # Double part moves can cause issue with Mirror move
                return
        enemy = _resolve_enemy(battle_state, self.source_pokemon)
        if enemy is None:
            return

        enemy.witness_move(self.move)


@dataclass(frozen=True)
class DamageEvent(BattleEvent):
    source: EffectSource
    target: PokemonIdent
    curr_hp: int
    max_hp: int | None
    hp_is_percentage: bool
    effectiveness: float = 1.0
    crit: bool = False

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        enemy = _resolve_enemy(battle_state, self.target)
        if enemy is not None:
            enemy.curr_hp_percent = self.curr_hp
            if self.curr_hp == 0:
                enemy.fainted = True
            return
        own = _resolve_self(battle_state, self.target)
        if own is not None:
            own.curr_hp = self.curr_hp


@dataclass(frozen=True)
class HealEvent(BattleEvent):
    source: EffectSource
    target: PokemonIdent
    curr_hp: int
    max_hp: int | None
    hp_is_percentage: bool

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        enemy = _resolve_enemy(battle_state, self.target)
        if enemy is not None:
            enemy.curr_hp_percent = self.curr_hp
            if self.curr_hp == 0:
                enemy.fainted = True
            return
        own = _resolve_self(battle_state, self.target)
        if own is not None:
            own.curr_hp = self.curr_hp


@dataclass(frozen=True)
class MinorStatusEvent(BattleEvent):
    source: EffectSource | None
    target: PokemonIdent
    effect: MinorStatus
    started: bool

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        # Volatile minor statuses are only tracked for the enemy; our own side's
        # volatile state is not modelled by the bot.
        status = _resolve_any_status(battle_state, self.target)

        if self.started:
            status.add_minor(self.effect)
        else:
            status.remove_minor(self.effect)


@dataclass(frozen=True)
class MajorStatusEvent(BattleEvent):
    source: EffectSource | None
    target: PokemonIdent
    status: MajorStatus
    applied: bool

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        if self.status is MajorStatus.FAINT:
            # `|faint|` is emitted as a FAINT MajorStatusEvent. Both sides are
            # affected; the enemy is marked fainted and benched.
            enemy = _resolve_enemy(battle_state, self.target)
            if enemy is not None:
                enemy.reset_on_switch_in()
                enemy.status.clear_all_major_status()
                enemy.fainted = True
                enemy.active = False
                enemy.curr_hp_percent = 0
                return
            own = _resolve_self(battle_state, self.target)
            if own is not None:
                own.curr_hp = 0
                own.major_status = None
            return

        status = _resolve_any_status(battle_state, self.target)
        if self.applied:
            status.set_status(self.status)
        else:
            status.clear_status(self.status)


@dataclass(frozen=True)
class MoveCopiedEvent(BattleEvent):
    """
    Records a temporary move copy such as Mimic.

    Example:
        |-start|p2a: Magnemite|Mimic|Hyper Beam
    """

    source: EffectSource
    target: PokemonIdent
    copied_move: str

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        # Mimic: disable the Mimic slot and expose the copied move on top of the
        # base moveset. Tracked for the enemy only.
        enemy = _resolve_enemy(battle_state, self.target)
        if enemy is None:
            return
        if self.copied_move not in enemy.temporary_moves:
            enemy.temporary_moves.append(self.copied_move)
        if "Mimic" not in enemy.disabled_moves:
            enemy.disabled_moves.append("Mimic")


@dataclass(frozen=True)
class MinorStatusActivationEvent(BattleEvent):
    """
    Records that an existing volatile status activated.

    Example:
        |-activate|p1a: Dragonair|confusion

    This does not start or end the status. It records that the status affected
    the current action.
    """

    target: PokemonIdent
    effect: MinorStatus

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        return  # This event is an activation of a status we already know about.


@dataclass(frozen=True)
class StatChangeEvent(BattleEvent):
    source: EffectSource
    target: PokemonIdent
    stat_changes: list[tuple[Stat, int]]
    success: bool = True
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if self.success and not self.stat_changes:
            raise ValueError(
                "A successful stat change must contain at least one change"
            )

        if not self.success and self.failure_reason is None:
            raise ValueError("A failed stat change must have a failure reason")

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        if not self.success:
            return

        status = _resolve_any_status(battle_state, self.target)
        for stat, delta in self.stat_changes:
            status.boost(stat, delta)


@dataclass(frozen=True)
class MovePrepareEvent(BattleEvent):
    """
    Records the preparation turn of a multi-turn move.

    Example:
        |-prepare|p1a: Pidgeot|Sky Attack
    """

    pokemon: PokemonIdent
    move: str

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
       return # prepare is always after a |move|, so we don't record it here

@dataclass(frozen=True)
class TeamCureEvent(BattleEvent):
    """
    Records all major statuses being cured on one side.

    Example:
        |-cureteam|p2a: Miltank|[from] move: Heal Bell
    """

    source: EffectSource
    side: str
    actor: PokemonIdent

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        # |-cureteam| clears the major status of every pokemon on the actor's
        # side
        if _is_self(battle_state, self.actor):
            for pokemon in battle_state.team:
                pokemon.major_status = None
            battle_state.curr_pokemon_status.major = None

        for pokemon in battle_state.enemy_team:
            pokemon.status.clear_all_major_status()


@dataclass(frozen=True)
class ClearAllBoostsEvent(BattleEvent):
    """
    Resets all active Pokémon's stat stages to zero.
    """
    source: EffectSource

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        # Haze / Clear Smog reset every pokemon's stat stages; only the enemy
        # side's stages are track
        actor = self.source.actor
        if actor is None:
            raise RuntimeError(f"Tried to clear all boost but the actor is unkown, {self.source}")
        if _is_self(battle_state, actor):
            battle_state.curr_pokemon_status.reset_all_stages()

        for pokemon in battle_state.enemy_team:
            pokemon.status.reset_all_stages()


@dataclass(frozen=True)
class CopyBoostEvent(BattleEvent):
    user: PokemonIdent
    target: PokemonIdent
    source: EffectSource

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        user_status = _resolve_any_status(battle_state, self.user)
        target_status = _resolve_any_status(battle_state, self.target)

        user_status.acc_stage = target_status.acc_stage
        user_status.eva_stage = target_status.eva_stage
        user_status.atk_stage = target_status.atk_stage
        user_status.def_stage = target_status.def_stage
        user_status.spa_stage = target_status.spa_stage
        user_status.spd_stage = target_status.spd_stage
        user_status.spe_stage = target_status.spe_stage


@dataclass(frozen=True)
class ClearNegativeBostsEvent(BattleEvent):
    """
    Resests all active Pokémon's negative stat changes to zero
    """
    target: PokemonIdent
    source: EffectSource | None

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        # |-clearnegativeboost| zeroes negative stat stages on the affected
        # side; only the enemy side is tracked.
        status = _resolve_any_status(battle_state, self.target)
        status.clear_negative_stages()


@dataclass(frozen=True)
class SetHpEvent(BattleEvent):
    source: EffectSource
    target: PokemonIdent
    curr_hp: int
    max_hp: int | None
    hp_is_percentage: bool

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        enemy = _resolve_enemy(battle_state, self.target)
        if enemy is not None:
            enemy.curr_hp_percent = self.curr_hp
            if self.curr_hp == 0:
                enemy.fainted = True
            return
        own = _resolve_self(battle_state, self.target)
        if own is not None:
            own.curr_hp = self.curr_hp


@dataclass(frozen=True)
class SideConditionEvent(BattleEvent):
    """
    Records a side-wide condition starting or ending.

    Examples:
        Reflect
        Light Screen
        Spikes
    """

    source: EffectSource | None
    side: str | None  # None here means both
    condition: SideCondition
    started: bool

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        side_conds = battle_state.side_conditions
        if self.side:
            if self.started:
                slot = side_conds.setdefault(self.side, {})
                slot[self.condition] = slot.get(self.condition, 0) + 1
            else:
                slot = side_conds.get(self.side)
                if slot is not None:
                    slot.pop(self.condition, None)
            return

        # if side is set to None, apply the condition event on all sides
        for conds in side_conds.values():
            if self.started:
                conds[self.condition] = conds.get(self.condition, 0) + 1
            else:
                if self.condition in conds:
                    conds.pop(self.condition)


@dataclass(frozen=True)
class PokemonSwitchEvent(BattleEvent):
    pokemon: PokemonIdent
    details: str
    level: int | None
    curr_hp: int
    max_hp: int | None
    hp_is_percentage: bool
    major_status: MajorStatus | None
    command: str = "switch"

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        if _is_self(battle_state, self.pokemon):
            battle_state.set_active_pokemon(_ident_self_key(self.pokemon))
            if not battle_state.team:
                return # happens before any request is made i.e. at thestart of the battle, no reset needed

            battle_state.curr_pokemon_status = Status()
            battle_state.curr_pokemon_status.major = self.major_status
            return

        gender, shiny = _parse_details(self.details)
        level = self.level if self.level is not None else 100
        battle_state.witness_switch_in(
            _ident_raw(self.pokemon), level, gender=gender, shiny=shiny
        )
        enemy = battle_state.get_enemy_pokemon(
            battle_state.curr_enemy_pokemon, not_found_ok=True
        )
        if enemy is not None:
            enemy.reset_on_switch_in()


@dataclass(frozen=True)
class TransformEvent(BattleEvent):
    """
    Records one Pokémon transforming into another.

    Example:
        |-transform|p1a: Ditto|p2a: Venonat
    """

    source: EffectSource
    pokemon: PokemonIdent
    target: PokemonIdent

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        # Only the enemy transforming is tracked. If it copies our active
        # pokemon, we already know that pokemon's exact moveset from |request|.
        if _is_self(battle_state, self.pokemon):
            return
        copied_moves: list[str] | None = None
        own = _resolve_self(battle_state, self.target)
        if own is not None:
            copied_moves = list(own.moves)
        battle_state.witness_transform(
            _ident_raw(self.pokemon), _ident_raw(self.target), copied_moves
        )


@dataclass(frozen=True)
class AbilityEvent(BattleEvent):
    pokemon: PokemonIdent
    ability: str
    active: bool = True
    source: EffectSource | None = None
    context: str | None = None

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        enemy = _resolve_enemy(battle_state, self.pokemon)
        if enemy is not None:
            enemy.base_ability = self.ability


@dataclass(frozen=True)
class StatSetEvent(BattleEvent):
    source: EffectSource
    target: PokemonIdent
    stat: Stat
    stage: int

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        status = _resolve_any_status(battle_state, self.target)
        status.set_stage(self.stat, self.stage)


@dataclass(frozen=True)
class MoveActivationEvent(BattleEvent):
    """
    Records a move-related activation that is not itself a normal |move| line.

    Example:
        |-activate|p1a: Ditto|move: Struggle
    """

    pokemon: PokemonIdent
    move: str

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        return  # This event is an activation of a move we already know about.


@dataclass(frozen=True)
class ItemEvent(BattleEvent):
    """
    Records a Pokémon gaining, revealing, losing, or transferring an item.

    `previous_owner` is populated for transfers such as Thief.
    """

    source: EffectSource
    pokemon: PokemonIdent | None # in gen 5, the ability Frisk reveal one of the enemy item, without knowing which
    # is the holder
    item: str
    gained: bool
    consumed: bool
    previous_owner: PokemonIdent | None = None

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        if self.gained:
            # A transfer (Thief / Knock Off): the previous owner loses the item.
            if self.previous_owner is not None:
                victim = _resolve_enemy(battle_state, self.previous_owner)
                if victim is not None:
                    victim.item = None
            enemy = _resolve_enemy(battle_state, self.pokemon)
            if enemy is not None:
                enemy.item = self.item
        else:
            enemy = _resolve_enemy(battle_state, self.pokemon)
            if enemy is None:
                return
            if (
                enemy.item is not None
                and enemy.item is not Unknown.VALUE
                and enemy.item != self.item
            ):
                raise RuntimeError(
                    "Item mismatch between protocol and battle state: "
                    + f"{enemy.item=} vs {self.item}"
                )
            enemy.item = None


@dataclass(frozen=True)
class CantEvent(BattleEvent):
    pokemon: PokemonIdent
    reason: str
    move: str | None = None

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        status = _resolve_any_status(battle_state, self.pokemon)
        # `recharge` consumes the must-recharge flag; status-based `cant` (slp /
        # par / frz) also clears a stale must-recharge. `flinch` is a one-off
        # intra-turn effect we don't track.
        if self.reason == "recharge":
            status.remove_minor(MinorStatus.RECHARGE)


@dataclass(frozen=True)
class PerishCountEvent(BattleEvent):
    source: EffectSource | None
    target: PokemonIdent
    count: int

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        status = _resolve_any_status(battle_state, self.target)
        status.perish_count = self.count
        status.add_minor(MinorStatus.PERISH_SONG)


@dataclass(frozen=True)
class TurnEvent(BattleEvent):
    turn: int

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        return

    @override
    def update_manager(self, manager: BattleManager) -> None:
        manager.turn = self.turn


@dataclass(frozen=True)
class WeatherEvent(BattleEvent):
    weather: Weather
    started: bool
    upkeep: bool
    source: EffectSource | None = None

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        # `started` is False for `|-weather|none` (weather cleared). Upkeep is
        # an ongoing-weather marker and carries no state change of its own.
        if not self.started:
            battle_state.weather = None
        else:
            battle_state.weather = self.weather.value


@dataclass(frozen=True)
class BattleEndEvent(BattleEvent):
    winner: str | None
    room_id: str

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        return

    @override
    def update_manager(self, manager: BattleManager) -> None:
        # Only end the battle we're actively driving.
        if manager.room_id == self.room_id:
            manager.finish_battle(self.winner)


@dataclass(frozen=True)
class RoomEvent(BattleEvent):
    """``>roomid`` — the following messages belong to this room."""

    room_id: str | None

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        return

    @override
    def update_manager(self, manager: BattleManager) -> None:
        if not manager.room_id:
            manager.room_id = self.room_id
            manager.room_ready.set()

        if manager.room_id != self.room_id:
            raise RuntimeError(
                "Room id changed during battle", manager.room_id, self.room_id
            )


@dataclass(frozen=True)
class BattleStartEvent(BattleEvent):
    """``|init|battle`` — the server opening a new battle room."""

    room_id: str

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        battle_state.clear_battle()

    @override
    def update_manager(self, manager: BattleManager) -> None:
        manager.room_id = self.room_id
        manager.room_ready.set()



@dataclass(frozen=True)
class PlayerEvent(BattleEvent):
    """``|player|<slot>|<name>|...`` — a side announcement."""

    slot: str
    name: str

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        return  # This event only update the client, not the battle state

    @override
    def update_manager(self, manager: BattleManager) -> None:
        if self.name == manager.player_username:
            manager.player_id = self.slot


@dataclass(frozen=True)
class SingleMoveEvent(BattleEvent):
    source: EffectSource | None
    pokemon: PokemonIdent
    move: str

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        # This is an event, not a discovery
        return


@dataclass(frozen=True)
class TypeChangeEvent(BattleEvent):
    source: EffectSource
    target: PokemonIdent
    types: tuple[str, ...]

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        status = _resolve_any_status(battle_state, self.target)
        status.add_minor(MinorStatus.TYPECHANGE)


@dataclass(frozen=True)
class FormeChangeEvent(BattleEvent):
    """
    Records a Pokémon changing to a different forme.

    Example:
        |-formechange|p1a: Cherrim|Cherrim-Sunshine
    """

    source: EffectSource
    pokemon: PokemonIdent
    forme: str

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        enemy = _resolve_enemy(battle_state, self.pokemon)
        if enemy is not None:
            enemy.forme = self.forme


@dataclass(frozen=True)
class DesyncEvent(BattleEvent):
    """
    Records when Gen 1 battle get a desync

    Example:
         |-hint|Desync Clause Mod activated!
         |-hint|In Gen 1, if both players would see the same Pokémon
         using different moves, the Pokemon defaults to the move shown
         from the perspective of the player controlling that Pokémon.
    """

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        battle_state.gen_1_desync = True


@dataclass(frozen=True)
class DecisionRequestEvent(BattleEvent):
    player_id: str
    request_id: int | None
    wait: bool
    trapped: bool  # The pokemon cannot switch
    maybe_trapped: (
        bool  # The pokemon might be trapped (cannot switch out), unknown for the player
    )
    maybe_locked: bool  # Maybe the pokemon cannot change moves
    maybe_disabled: bool  # Maybe a move is disabled
    update: bool  # The server sends the update flag when it detected its own mistake in the previous request message.
    force_switch: tuple[bool, ...]
    moves: tuple[RequestMove, ...]
    pokemon: tuple[RequestPokemon, ...]
    no_cancel: bool  # When True, the user cannot cancel their decision, hopefully not relevent for a bot. Can become true depending on the server battle state.

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        battle_state.player_id = self.player_id

        available_moves = [
            AvailableMove(
                name=move.name,
                id=move.id,
                curr_pp=move.curr_pp,
                max_pp=move.max_pp,
                target=move.target,
                disabled=move.disabled,
            )
            for move in self.moves
        ]

        available_pokemons: list[PartyPokemon] = []

        for pokemon in self.pokemon:
            max_hp = pokemon.max_hp

            if max_hp is None:
                existing = next(
                    (p for p in battle_state.team if p.id == pokemon.ident),
                    None,
                )
                max_hp = (
                    existing.max_hp
                    if existing is not None and existing.max_hp > 0
                    else 0
                )

            stats = Stats(
                atk=pokemon.atk,
                def_=pokemon.def_,
                spa=pokemon.spa,
                spd=pokemon.spd,
                spe=pokemon.spe,
                max_hp=max_hp,
            )

            available_pokemons.append(
                PartyPokemon(
                    id=pokemon.ident,
                    details=pokemon.details,
                    lvl=pokemon.level,
                    active=pokemon.active,
                    stats=stats,
                    moves=list(pokemon.moves),
                    base_ability=pokemon.base_ability,
                    item=pokemon.item,
                    pokeball=pokemon.pokeball,
                    major_status=pokemon.major_status,
                    curr_hp=pokemon.curr_hp,
                    max_hp=max_hp,
                )
            )

        battle_state.update_team(available_pokemons)

        active = next(
            (pokemon for pokemon in available_pokemons if pokemon.active),
            None,
        )
        if active is not None:
            battle_state.set_active_pokemon(str(active.id))
            battle_state.curr_pokemon_status.major = active.major_status

        battle_state.update_moves(available_moves)
        battle_state.force_switch = any(self.force_switch)

    @override
    def update_manager(self, manager: BattleManager) -> None:
        new_id = None if self.wait else self.request_id
        manager.log_manager.battle.debug(
            "|request| update_manager: setting request_id=%r (was %r, wait=%s, "
            + "force_switch=%s, rqid=%r)",
            new_id,
            manager.request_id,
            self.wait,
            self.force_switch,
            self.request_id,
            extra={"room_id": manager.room_id},
        )

        manager.request_id = new_id
        manager.choice_rejected = False
        manager.retry_rqid = None
        manager.retry_count = 0

        if not self.wait:
            manager.last_request_id = None


@dataclass(frozen=True)
class GameTypeEvent(BattleEvent):
    type: str
    _IMPLEMENTED_TYPES: ClassVar[tuple[str, ...]] = ("singles",)

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        if self.type not in self._IMPLEMENTED_TYPES:
            raise NotImplementedError(
                f"Gametype not implemented yet: {self.type} not in {self._IMPLEMENTED_TYPES}"
            )
        battle_state.gametype = self.type


@dataclass(frozen=True)
class GameGenEvent(BattleEvent):
    gen: int
    _LAST_IMPLEMENTED_GEN: ClassVar[int] = 5

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        if self.gen <= 0:
            raise ValueError("Pokemon gen must be between 1 and 9")
        if self.gen > self._LAST_IMPLEMENTED_GEN:
            raise NotImplementedError(
                f"Only gen up to {self._LAST_IMPLEMENTED_GEN} was implemented"
            )
        battle_state.gen = self.gen


@dataclass(frozen=True)
class GameTierEvent(BattleEvent):
    tier: str
    _IMPLEMENTED_TIERS: ClassVar[tuple[str, ...]] = ("Random Battle", "OU")

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        if self.tier not in self._IMPLEMENTED_TIERS:
            raise NotImplementedError(
                f"Game tier not implemented yet: {self.tier} not in {self._IMPLEMENTED_TIERS}"
            )

@dataclass(frozen=True)
class PartialTrapEvent(BattleEvent):
    target: PokemonIdent
    move: str
    source: EffectSource
    started: bool

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        status = _resolve_any_status(battle_state, self.target)
        if self.started:
            status.minor.add(MinorStatus.PARTIALLY_TRAPPED)
        else:
            status.minor.remove(MinorStatus.PARTIALLY_TRAPPED)

@dataclass(frozen=True)
class TeamPreviewRequestEvent(BattleEvent):
    player_id: str
    request_id: int | None
    pokemon: tuple[RequestPokemon, ...]
    max_chosen_team_size: int | None
    no_cancel: bool

    @property
    def chosen_team_size(self) -> int:
        if self.max_chosen_team_size is not None:
            return self.max_chosen_team_size
        return len(self.pokemon)

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        return

    @override
    def update_manager(self, manager: BattleManager) -> None:
        # manager.reset(keep_room_id=True)
        manager.requires_team_preview = True
        manager.player_id = self.player_id

@dataclass(frozen=True)
class CustomShowdownBattleStateEvent(BattleEvent):
    content: SerializableObject

    @override
    def _update_battle_state(self, battle_state: BattleState) -> None:
        battle_state.custom_showdown_battlestate = self.content
