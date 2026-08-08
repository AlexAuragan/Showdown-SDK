from abc import ABC
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from python_showdown.classes.parser.enums import (
    MajorStatus,
    MinorStatus,
    SideCondition,
    Stat,
    Weather,
)
from python_showdown.classes.parser.models import (
    EffectSource,
    PokemonIdent,
    ProtocolAnnotation,
    ProtocolMessage,
)
from python_showdown.classes.pokemon.moves import AvailableMove
from python_showdown.classes.pokemon.pokemon import PartyPokemon, Unknown
from python_showdown.classes.pokemon.stats import MinorStatus as PokemonMinorStatus
from python_showdown.classes.pokemon.stats import Stats, Status

if TYPE_CHECKING:
    from python_showdown.classes.client.client import Client
    from python_showdown.classes.combat.battle_state import BattleState


def _ident_raw(ident: PokemonIdent) -> str:
    """Reconstruct the protocol identifier string (`p2a: Magnemite`)."""
    if ident.slot is not None:
        return f"{ident.player}{ident.slot}: {ident.name}"
    return f"{ident.player}: {ident.name}"


def _ident_self_key(ident: PokemonIdent) -> str:
    """The side-level identifier used by `PartyPokemon.id` (`p1: Miltank`)."""
    return f"{ident.player}: {ident.name}"


def _is_self(battle_state: BattleState, ident: PokemonIdent) -> bool:
    return bool(battle_state.player_id) and ident.player == battle_state.player_id


def _resolve_enemy(battle_state: BattleState, ident: PokemonIdent | None):
    if ident is None or not battle_state.player_id or ident.player == battle_state.player_id:
        return None
    return battle_state.get_enemy_pokemon(_ident_raw(ident), not_found_ok=True)


def _resolve_self(battle_state: BattleState, ident: PokemonIdent | None):
    if ident is None or not battle_state.player_id or ident.player != battle_state.player_id:
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


_POKEMON_MINOR_BY_VALUE = {status.value: status for status in PokemonMinorStatus}


def _to_pokemon_minor(status: MinorStatus) -> PokemonMinorStatus | None:
    """Map a parser MinorStatus to the tracked pokemon MinorStatus, if any."""
    return _POKEMON_MINOR_BY_VALUE.get(status.value)


class BaseEvent(ABC):
    """A complete semantic event derived from one or more protocol messages."""

    # TODO: Make abstract once every event has a reducer implementation.
    # Every event should be able to edit the battle state, much like Git applies
    # changes while rebuilding history.
    def update_battle_state(self, battle_state: BattleState) -> None:
        return

    def update_client(self, client: Client) -> None:
        self.update_battle_state(client.battle_state)


def unhandled_event(message: ProtocolMessage, action_id: int | None = None) -> UnhandledEvent:
    return UnhandledEvent(message.command, message.arguments, message.annotations, message.raw, action_id)


@dataclass(frozen=True)
class MoveEvent(BaseEvent):
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
            raise ValueError(
                "A move hit count must be positive"
            )

    def update_battle_state(self, battle_state: BattleState) -> None:
        # Only the opponent's moves reveal new move slots; our own moveset is
        # known exactly from |request|. witness_move skips Struggle internally.
        if self.from_move == "Mirror Move":
            return
        if _resolve_enemy(battle_state, self.source) is not None:
            battle_state.witness_move(self.move)

@dataclass(frozen=True)
class DamageEvent(BaseEvent):
    source: EffectSource
    target: PokemonIdent
    curr_hp: int
    max_hp: int | None
    hp_is_percentage: bool
    effectiveness: float = 1.0
    crit: bool = False

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
class HealEvent(BaseEvent):
    source: EffectSource
    target: PokemonIdent
    curr_hp: int
    max_hp: int | None
    hp_is_percentage: bool

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
class MinorStatusEvent(BaseEvent):
    source: EffectSource | None
    target: PokemonIdent
    effect: MinorStatus
    started: bool

    def update_battle_state(self, battle_state: BattleState) -> None:
        # Volatile minor statuses are only tracked for the enemy; our own side's
        # volatile state is not modelled by the bot.
        enemy = _resolve_enemy(battle_state, self.target)
        if enemy is None:
            return
        minor = _to_pokemon_minor(self.effect)
        if minor is None:
            return
        if self.started:
            enemy.status.add_minor(minor)
        else:
            enemy.status.remove_minor(minor)


@dataclass(frozen=True)
class MajorStatusEvent(BaseEvent):
    source: EffectSource | None
    target: PokemonIdent
    status: MajorStatus
    applied: bool

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
            enemy.status.set_status(self.status.value)
        else:
            enemy.status.clear_status(self.status.value)


@dataclass(frozen=True)
class MoveCopiedEvent(BaseEvent):
    """
    Records a temporary move copy such as Mimic.

    Example:
        |-start|p2a: Magnemite|Mimic|Hyper Beam
    """

    source: EffectSource
    target: PokemonIdent
    copied_move: str

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
class MinorStatusActivationEvent(BaseEvent):
    """
    Records that an existing volatile status activated.

    Example:
        |-activate|p1a: Dragonair|confusion

    This does not start or end the status. It records that the status affected
    the current action.
    """

    target: PokemonIdent
    effect: MinorStatus


@dataclass(frozen=True)
class StatChangeEvent(BaseEvent):
    source: EffectSource
    target: PokemonIdent
    stat_changes: tuple[tuple[Stat, int], ...]
    success: bool = True
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if self.success and not self.stat_changes:
            raise ValueError(
                "A successful stat change must contain at least one change"
            )

        if not self.success and self.failure_reason is None:
            raise ValueError(
                "A failed stat change must have a failure reason"
            )

    def update_battle_state(self, battle_state: BattleState) -> None:
        if not self.success:
            return
        enemy = _resolve_enemy(battle_state, self.target)
        if enemy is None:
            return
        for stat, delta in self.stat_changes:
            enemy.status.boost(stat.value, delta)

@dataclass(frozen=True)
class MovePrepareEvent(BaseEvent):
    """
    Records the preparation turn of a multi-turn move.

    Example:
        |-prepare|p1a: Pidgeot|Sky Attack
    """

    pokemon: PokemonIdent
    move: str

    def update_battle_state(self, battle_state: BattleState) -> None:
        # |-prepare| reveals the charging move as a known slot for the enemy.
        if _resolve_enemy(battle_state, self.pokemon) is not None:
            battle_state.witness_move(self.move)

@dataclass(frozen=True)
class TeamCureEvent(BaseEvent):
    """
    Records all major statuses being cured on one side.

    Example:
        |-cureteam|p2a: Miltank|[from] move: Heal Bell
    """

    source: EffectSource
    side: str
    actor: PokemonIdent

    def update_battle_state(self, battle_state: BattleState) -> None:
        # |-cureteam| clears the major status of every pokemon on the actor's
        # side; only the enemy side is tracked here.
        if _is_self(battle_state, self.actor):
            return
        for pokemon in battle_state.enemy_team:
            pokemon.status.clear_all_major_status()

@dataclass(frozen=True)
class ClearAllBoostsEvent(BaseEvent):
    """
    Resets all active Pokémon's stat stages to zero.
    """

    source: EffectSource | None

    def update_battle_state(self, battle_state: BattleState) -> None:
        # Haze / Clear Smog reset every pokemon's stat stages; only the enemy
        # side's stages are tracked by the bot.
        for pokemon in battle_state.enemy_team:
            pokemon.status.reset_all_stages()

@dataclass(frozen=True)
class ClearNegativeBostsEvent(BaseEvent):
    """
    Resests all active Pokémon's négative stat changes to zéro
    """
    source: EffectSource | None

    def update_battle_state(self, battle_state: BattleState) -> None:
        # |-clearnegativeboost| zeroes negative stat stages on the affected
        # side; only the enemy side is tracked.
        for pokemon in battle_state.enemy_team:
            for attr in (
                "atk_stage", "def_stage", "spa_stage",
                "spd_stage", "spe_stage", "eva_stage", "acc_stage",
            ):
                if getattr(pokemon.status, attr) < 0:
                    setattr(pokemon.status, attr, 0)

@dataclass(frozen=True)
class SetHpEvent(BaseEvent):
    source: EffectSource
    target: PokemonIdent
    curr_hp: int
    max_hp: int | None
    hp_is_percentage: bool

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
class SideConditionEvent(BaseEvent):
    """
    Records a side-wide condition starting or ending.

    Examples:
        Reflect
        Light Screen
        Spikes
    """

    source: EffectSource | None
    side: str
    condition: SideCondition
    started: bool

    def update_battle_state(self, battle_state: BattleState) -> None:
        conds = battle_state.side_conditions
        if self.started:
            slot = conds.setdefault(self.side, {})
            slot[self.condition] = slot.get(self.condition, 0) + 1
        else:
            slot = conds.get(self.side)
            if slot is not None:
                slot.pop(self.condition, None)

@dataclass(frozen=True)
class PokemonSwitchEvent(BaseEvent):
    pokemon: PokemonIdent
    details: str
    level: int | None
    curr_hp: int
    max_hp: int | None
    hp_is_percentage: bool
    major_status: MajorStatus | None
    command: str = "switch"

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
class TransformEvent(BaseEvent):
    """
    Records one Pokémon transforming into another.

    Example:
        |-transform|p1a: Ditto|p2a: Venonat
    """

    source: EffectSource
    pokemon: PokemonIdent
    target: PokemonIdent

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
class AbilityEvent(BaseEvent):
    pokemon: PokemonIdent
    ability: str
    active: bool = True
    source: EffectSource | None = None
    context: str | None = None

    def update_battle_state(self, battle_state: BattleState) -> None:
        enemy = _resolve_enemy(battle_state, self.pokemon)
        if enemy is not None:
            enemy.base_ability = self.ability


@dataclass(frozen=True)
class StatSetEvent(BaseEvent):
    source: EffectSource
    target: PokemonIdent
    stat: Stat
    stage: int

    def update_battle_state(self, battle_state: BattleState) -> None:
        enemy = _resolve_enemy(battle_state, self.target)
        if enemy is None:
            return
        enemy.status.set_stage(self.stat.value, self.stage)


@dataclass(frozen=True)
class MoveActivationEvent(BaseEvent):
    """
    Records a move-related activation that is not itself a normal |move| line.

    Example:
        |-activate|p1a: Ditto|move: Struggle
    """

    pokemon: PokemonIdent
    move: str


@dataclass(frozen=True)
class ItemEvent(BaseEvent):
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
                    f"{enemy.item=} vs {self.item}"
                )
            enemy.item = None


@dataclass(frozen=True)
class CantEvent(BaseEvent):
    pokemon: PokemonIdent
    reason: str
    move: str | None = None

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
class DecisionRequestEvent(BaseEvent):
    player_id: str
    request_id: int | None
    wait: bool
    force_switch: tuple[bool, ...]
    payload: dict[str, Any]

    def update_battle_state(self, battle_state: BattleState) -> None:
        # `|request|` is the authoritative snapshot of our own side: it rebuilds
        # our team, available moves, active pokemon, and force-switch flag. The
        # perspective player id is also fixed from the request.
        battle_state.player_id = self.player_id
        data = self.payload
        available_moves: list[AvailableMove] = []
        available_pokemons: list[PartyPokemon] = []
        force_switch = "active" not in data

        if not force_switch:
            active_moves = data["active"][0]["moves"]
            if len(active_moves) == 1:
                move = active_moves[0]
                available_moves.append(AvailableMove(
                    name=move["move"],
                    id=move["id"],
                    curr_pp=move.get("pp", 100),
                    max_pp=move.get("maxpp", 100),
                    target=move.get("target", "normal"),
                    disabled=False,
                ))
            else:
                for raw_move in active_moves:
                    available_moves.append(AvailableMove(
                        name=raw_move["move"],
                        id=raw_move["id"],
                        curr_pp=raw_move["pp"],
                        max_pp=raw_move["maxpp"],
                        target=raw_move.get("target", "normal"),
                        disabled=raw_move["disabled"],
                    ))

        for raw_pkmn in data["side"]["pokemon"]:
            status = Status()
            cond = raw_pkmn["condition"]
            if cond == "0 fnt":
                curr_hp = 0
                existing = next(
                    (p for p in battle_state.team if p.id == raw_pkmn["ident"]),
                    None,
                )
                max_hp = existing.max_hp if existing is not None and existing.max_hp > 0 else 0
            else:
                curr_str, rest = cond.split("/", 1)
                curr_hp = int(curr_str)
                if " " in rest:
                    max_str, status_token = rest.split(" ", 1)
                    max_hp = int(max_str)
                    status.set_status(status_token)
                else:
                    max_hp = int(rest)
            stats = Stats(
                atk=raw_pkmn["stats"]["atk"],
                def_=raw_pkmn["stats"]["def"],
                spa=raw_pkmn["stats"]["spa"],
                spd=raw_pkmn["stats"]["spd"],
                spe=raw_pkmn["stats"]["spe"],
                max_hp=int(max_hp),
            )
            details = raw_pkmn["details"]
            available_pokemons.append(PartyPokemon(
                id=raw_pkmn["ident"],
                details=details,
                lvl=(
                    int(details.replace(", shiny", "").replace(", M", "").replace(", F", "").split(", L")[1])
                    if ", L" in details
                    else 100
                ),
                active=raw_pkmn["active"],
                stats=stats,
                moves=raw_pkmn["moves"],
                base_ability=raw_pkmn["baseAbility"],
                item=raw_pkmn["item"],
                pokeball=raw_pkmn["pokeball"],
                status=status,
                curr_hp=curr_hp,
                max_hp=max_hp,
            ))

        if len(available_pokemons) > 6:
            raise RuntimeError(
                f"Malformed request: expected at most 6 Pokémon, "
                f"got {len(available_pokemons)}"
            )

        battle_state.update_team(available_pokemons)
        active = next((pkmn for pkmn in available_pokemons if pkmn.active), None)
        if active is not None:
            battle_state.set_active_pokemon(str(active.id))
        battle_state.update_moves(available_moves)
        battle_state.force_switch = force_switch

    def update_client(self, client: Client) -> None:
        # Rebuild our side's snapshot on the client's battle state, then arm the
        # pending decision. ``wait`` requests carry no actionable rqid (mirrors
        # the legacy parser, which nulled rqid when ``wait`` was set), so the
        # receive loop's ``if request_id is not None: await act()`` won't fire.
        self.update_battle_state(client.battle_state)
        client.request_id = None if self.wait else self.request_id

@dataclass(frozen=True)
class PerishCountEvent(BaseEvent):
    source: EffectSource | None
    target: PokemonIdent
    count: int

    def update_battle_state(self, battle_state: BattleState) -> None:
        enemy = _resolve_enemy(battle_state, self.target)
        if enemy is None:
            return
        enemy.status.perish_count = self.count
        enemy.status.add_minor(PokemonMinorStatus.PERISH_SONG)


@dataclass(frozen=True)
class TurnEvent(BaseEvent):
    turn: int

    def update_client(self, client: Client) -> None:
        # Track the turn for move counting and (re)start the action-timeout guard:
        # if we don't receive a |request| to act on soon, the battle is stuck.
        client.turn_count = self.turn
        client.start_action_timeout()


@dataclass(frozen=True)
class WeatherEvent(BaseEvent):
    weather: Weather
    started: bool
    upkeep: bool
    source: EffectSource | None = None

    def update_battle_state(self, battle_state: BattleState) -> None:
        # `started` is False for `|-weather|none` (weather cleared). Upkeep is
        # an ongoing-weather marker and carries no state change of its own.
        if not self.started:
            battle_state.weather = None
        else:
            battle_state.weather = self.weather.value


@dataclass(frozen=True)
class BattleEndEvent(BaseEvent):
    winner: str | None

    def update_client(self, client: Client) -> None:
        # Only end the battle we're actively driving. The legacy parser ignored
        # |win|/|tie| from rooms other than the active battle room; the receive
        # loop sets client.room_id from the preceding >roomid line, so a mismatch
        # means the line belongs to a different (e.g. stale) room.
        if client.room_id == client.active_battle_room:
            client.finish_battle(self.winner)


@dataclass(frozen=True)
class RoomEvent(BaseEvent):
    """``>roomid`` — the following messages belong to this room.

    Sets ``client.room_id`` so subsequent events (notably ``BattleStartEvent``)
    know which room they apply to. Mirrors the legacy parser, which set the
    client's room id from the ``>`` line before dispatching any battle/lobby
    handler.
    """

    room_id: str

    def update_client(self, client: Client) -> None:
        client.room_id = self.room_id


@dataclass(frozen=True)
class BattleStartEvent(BaseEvent):
    """``|init|battle`` — the server opening a new battle room.

    In live mode the parser may serve many battles over one connection, so this
    event resets the client's per-battle state so the previous battle's residue
    (active room, request id, side id) cannot leak into the new one. The parser's
    own accumulated state is reset separately in ``BattleParser``.
    """

    def update_client(self, client: Client) -> None:
        client.active_battle_room = client.room_id
        client.battle_state.reset()
        client.request_id = None
        client.battle_player_id = ""


@dataclass(frozen=True)
class PlayerEvent(BaseEvent):
    """``|player|<slot>|<name>|...`` — a side announcement.

    Learns our own side id by matching the announced name against the logged-in
    ``client.username`` (mirrors the legacy ``_handle_player``). Only the side
    we are playing is recorded as ``battle_player_id``; the opponent's
    ``|player|`` line is ignored.
    """

    slot: str
    name: str

    def update_client(self, client: Client) -> None:
        if client.username and self.name == client.username:
            client.battle_state.player_id = self.slot




@dataclass(frozen=True)
class UnhandledEvent(BaseEvent):
    """A valid protocol message whose semantic reducer is not implemented yet."""

    command: str
    arguments: tuple[str, ...]
    annotations: tuple[ProtocolAnnotation, ...]
    raw: str
    action_id: int | None = None


@dataclass(frozen=True)
class DiscardedEvent(BaseEvent):
    """Optional marker for a deliberately ignored protocol message."""

    command: str
    reason: str | None = None


@dataclass(frozen=True)
class FieldActivationEvent(BaseEvent):
    source: EffectSource
    active: bool = True

@dataclass(frozen=True)
class SingleMoveEvent(BaseEvent):
    source: EffectSource | None
    pokemon: PokemonIdent
    move: str

@dataclass(frozen=True)
class TypeChangeEvent(BaseEvent):
    source: EffectSource
    target: PokemonIdent
    types: tuple[str, ...]

    def update_battle_state(self, battle_state: BattleState) -> None:
        enemy = _resolve_enemy(battle_state, self.target)
        if enemy is not None:
            enemy.status.add_minor(PokemonMinorStatus.TYPECHANGE)


@dataclass(frozen=True)
class FormeChangeEvent(BaseEvent):
    """
    Records a Pokémon changing to a different forme.

    Example:
        |-formechange|p1a: Cherrim|Cherrim-Sunshine
    """

    source: EffectSource
    pokemon: PokemonIdent
    forme: str

    def update_battle_state(self, battle_state: BattleState) -> None:
        enemy = _resolve_enemy(battle_state, self.pokemon)
        if enemy is not None:
            enemy.forme = self.forme
