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
from python_showdown.models.sdk.battle_state import BattleState


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
    battle_state: BattleState, ident: PokemonIdent | None
) -> EnemyPokemon | None:
    if (
        ident is None
        or not battle_state.player_id
        or ident.player == battle_state.player_id
    ):
        return None
    return battle_state.get_enemy_pokemon(_ident_raw(ident), not_found_ok=True)


def _resolve_self(battle_state: BattleState, ident: PokemonIdent | None):
    if (
        ident is None
        or not battle_state.player_id
        or ident.player != battle_state.player_id
    ):
        return None
    key = _ident_self_key(ident)
    return next((p for p in battle_state.team if p.id == key), None)


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


class BattleEvent(BaseEvent, metaclass=ABCMeta):
    @abstractmethod
    def update_battle_state(self, battle_state: BattleState) -> None:
        pass

    def update_manager(self, manager: BattleManager) -> None:
        self.update_battle_state(manager.battle_state)


@dataclass(frozen=True)
class MoveEvent(BattleEvent):
    """Base move event"""

    action_id: int
    move: str
    source: PokemonIdent
    target: PokemonIdent | None
    success: bool  # The move did not fail.
    does_hit: bool  # The move did not miss or hit an immunity.
    failure_reason: str | None = None
    hit_count: int | None = None
    from_move: str | None = None

    def __post_init__(self) -> None:
        if not self.success and self.does_hit:
            raise ValueError("A failed move cannot be marked as having hit")
        if self.hit_count is not None and self.hit_count <= 0:
            raise ValueError("A move hit count must be positive")

    @override
    def update_battle_state(self, battle_state: BattleState) -> None:
        # Only the opponent's moves reveal new move slots; our own moveset is
        # known exactly from |request|. witness_move skips Struggle internally.
        if self.from_move == "Mirror Move":
            return

        enemy = _resolve_enemy(battle_state, self.source)
        if enemy is None:
            return

        battle_state.gen_1_desync = enemy.witness_move(
            self.move,
            battle_state.gen_1_desync,
        )


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
    def update_battle_state(self, battle_state: BattleState) -> None:
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
    def update_battle_state(self, battle_state: BattleState) -> None:
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
    def update_battle_state(self, battle_state: BattleState) -> None:
        # Volatile minor statuses are only tracked for the enemy; our own side's
        # volatile state is not modelled by the bot.
        enemy = _resolve_enemy(battle_state, self.target)
        if enemy is None:
            return
        minor = self.effect
        if self.started:
            enemy.status.add_minor(minor)
        else:
            enemy.status.remove_minor(minor)


@dataclass(frozen=True)
class MajorStatusEvent(BattleEvent):
    source: EffectSource | None
    target: PokemonIdent
    status: MajorStatus
    applied: bool

    @override
    def update_battle_state(self, battle_state: BattleState) -> None:
        if self.status is MajorStatus.FAINT:
            # `|faint|` is emitted as a FAINT MajorStatusEvent. Both sides are
            # affected; the enemy is marked fainted and benched.
            enemy = _resolve_enemy(battle_state, self.target)
            if enemy is not None:
                enemy.fainted = True
                enemy.active = False
                enemy.curr_hp_percent = 0
                return
            own = _resolve_self(battle_state, self.target)
            if own is not None:
                own.curr_hp = 0
            return

        enemy = _resolve_enemy(battle_state, self.target)
        if enemy is None:
            return
        if self.applied:
            enemy.status.set_status(self.status)
        else:
            enemy.status.clear_status(self.status)


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
    def update_battle_state(self, battle_state: BattleState) -> None:
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
    def update_battle_state(self, battle_state: BattleState) -> None:
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
    def update_battle_state(self, battle_state: BattleState) -> None:
        if not self.success:
            return
        enemy = _resolve_enemy(battle_state, self.target)
        if enemy is None:
            return
        for stat, delta in self.stat_changes:
            enemy.status.boost(stat, delta)


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
    def update_battle_state(self, battle_state: BattleState) -> None:
        # |-prepare| reveals the charging move as a known slot for the enemy.
        if _resolve_enemy(battle_state, self.pokemon) is not None:
            battle_state.witness_move(self.move)


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
    def update_battle_state(self, battle_state: BattleState) -> None:
        # |-cureteam| clears the major status of every pokemon on the actor's
        # side; only the enemy side is tracked here.
        if _is_self(battle_state, self.actor):
            return
        for pokemon in battle_state.enemy_team:
            pokemon.status.clear_all_major_status()


@dataclass(frozen=True)
class ClearAllBoostsEvent(BattleEvent):
    """
    Resets all active Pokémon's stat stages to zero.
    """

    source: EffectSource | None

    @override
    def update_battle_state(self, battle_state: BattleState) -> None:
        # Haze / Clear Smog reset every pokemon's stat stages; only the enemy
        # side's stages are tracked by the bot.
        for pokemon in battle_state.enemy_team:
            pokemon.status.reset_all_stages()


@dataclass(frozen=True)
class ClearNegativeBostsEvent(BattleEvent):
    """
    Resests all active Pokémon's négative stat changes to zéro
    """

    source: EffectSource | None

    @override
    def update_battle_state(self, battle_state: BattleState) -> None:
        # |-clearnegativeboost| zeroes negative stat stages on the affected
        # side; only the enemy side is tracked.
        for pokemon in battle_state.enemy_team:
            for attr in (
                "atk_stage",
                "def_stage",
                "spa_stage",
                "spd_stage",
                "spe_stage",
                "eva_stage",
                "acc_stage",
            ):
                if getattr(pokemon.status, attr) < 0:
                    setattr(pokemon.status, attr, 0)


@dataclass(frozen=True)
class SetHpEvent(BattleEvent):
    source: EffectSource
    target: PokemonIdent
    curr_hp: int
    max_hp: int | None
    hp_is_percentage: bool

    @override
    def update_battle_state(self, battle_state: BattleState) -> None:
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
    def update_battle_state(self, battle_state: BattleState) -> None:
        side_conds = battle_state.side_conditions
        if self.side:
            if self.started:
                slot = side_conds.get(self.side, {})
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
    def update_battle_state(self, battle_state: BattleState) -> None:
        if _is_self(battle_state, self.pokemon):
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
    def update_battle_state(self, battle_state: BattleState) -> None:
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
    def update_battle_state(self, battle_state: BattleState) -> None:
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
    def update_battle_state(self, battle_state: BattleState) -> None:
        enemy = _resolve_enemy(battle_state, self.target)
        if enemy is None:
            return
        enemy.status.set_stage(self.stat, self.stage)


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
    def update_battle_state(self, battle_state: BattleState) -> None:
        return  # This event is an activation of a move we already know about.


@dataclass(frozen=True)
class ItemEvent(BattleEvent):
    """
    Records a Pokémon gaining, revealing, losing, or transferring an item.

    `previous_owner` is populated for transfers such as Thief.
    """

    source: EffectSource
    pokemon: PokemonIdent
    item: str
    gained: bool
    consumed: bool
    previous_owner: PokemonIdent | None = None

    @override
    def update_battle_state(self, battle_state: BattleState) -> None:
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
    def update_battle_state(self, battle_state: BattleState) -> None:
        enemy = _resolve_enemy(battle_state, self.pokemon)
        if enemy is None:
            return
        # `recharge` consumes the must-recharge flag; status-based `cant` (slp /
        # par / frz) also clears a stale must-recharge. `flinch` is a one-off
        # intra-turn effect we don't track.
        if self.reason != "flinch":
            enemy.status.must_recharge = False


@dataclass(frozen=True)
class PerishCountEvent(BattleEvent):
    source: EffectSource | None
    target: PokemonIdent
    count: int

    @override
    def update_battle_state(self, battle_state: BattleState) -> None:
        enemy = _resolve_enemy(battle_state, self.target)
        if enemy is None:
            return
        enemy.status.perish_count = self.count
        enemy.status.add_minor(MinorStatus.PERISH_SONG)


@dataclass(frozen=True)
class TurnEvent(BattleEvent):
    turn: int

    @override
    def update_battle_state(self, battle_state: BattleState) -> None:
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
    def update_battle_state(self, battle_state: BattleState) -> None:
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
    def update_battle_state(self, battle_state: BattleState) -> None:
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
    def update_battle_state(self, battle_state: BattleState) -> None:
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
    def update_battle_state(self, battle_state: BattleState) -> None:
        return

    @override
    def update_manager(self, manager: BattleManager) -> None:
        manager.room_id = self.room_id
        manager.room_ready.set()

        manager.battle_state.reset(keep_player_id=True)


@dataclass(frozen=True)
class PlayerEvent(BattleEvent):
    """``|player|<slot>|<name>|...`` — a side announcement."""

    slot: str
    name: str

    @override
    def update_battle_state(self, battle_state: BattleState) -> None:
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
    def update_battle_state(self, battle_state: BattleState) -> None:
        # This is an event, not a discovery
        return


@dataclass(frozen=True)
class TypeChangeEvent(BattleEvent):
    source: EffectSource
    target: PokemonIdent
    types: tuple[str, ...]

    @override
    def update_battle_state(self, battle_state: BattleState) -> None:
        enemy = _resolve_enemy(battle_state, self.target)
        if enemy is not None:
            enemy.status.add_minor(MinorStatus.TYPECHANGE)


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
    def update_battle_state(self, battle_state: BattleState) -> None:
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
    def update_battle_state(self, battle_state: BattleState) -> None:
        battle_state.gen_1_desync = True


@dataclass(frozen=True)
class DecisionRequestEvent(BattleEvent):
    player_id: str
    request_id: int | None
    wait: bool
    trapped: bool  # The pokemon cannot switch
    maybe_trapped: bool  # The pokemon might be trapped, unknown for the player
    update: bool  # The server sends the update flag when it detected its own mistake in the previous request message.
    force_switch: tuple[bool, ...]
    moves: tuple[RequestMove, ...]
    pokemon: tuple[RequestPokemon, ...]
    no_cancel: bool  # When True, the user cannot cancel their decision, hopefully not relevent for a bot. Can become true depending on the server battle state.

    @override
    def update_battle_state(self, battle_state: BattleState) -> None:
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

            status = Status()
            if pokemon.major_status is not None:
                status.set_status(pokemon.major_status)

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
                    status=status,
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

        battle_state.update_moves(available_moves)
        battle_state.force_switch = any(self.force_switch)

    @override
    def update_manager(self, manager: BattleManager) -> None:
        self.update_battle_state(manager.battle_state)

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
    def update_battle_state(self, battle_state: BattleState) -> None:
        if self.type not in self._IMPLEMENTED_TYPES:
            raise NotImplementedError(
                f"Gametype not implemented yet: {self.type} not in {self._IMPLEMENTED_TYPES}"
            )
        battle_state.gametype = self.type


@dataclass(frozen=True)
class GameGenEvent(BattleEvent):
    gen: int
    _LAST_IMPLEMENTED_GEN: ClassVar[int] = 4

    @override
    def update_battle_state(self, battle_state: BattleState) -> None:
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
    _IMPLEMENTED_TIERS: ClassVar[tuple[str, ...]] = ("Random Battle",)

    @override
    def update_battle_state(self, battle_state: BattleState) -> None:
        if self.tier not in self._IMPLEMENTED_TIERS:
            raise NotImplementedError(
                f"Game tier not implemented yet: {self.tier} not in {self._IMPLEMENTED_TIERS}"
            )
