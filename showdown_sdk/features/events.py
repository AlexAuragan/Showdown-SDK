"""Feature objects and converters for the semantic battle-event history."""

from dataclasses import dataclass, field

from showdown_sdk.classes.parser import (
    AbilityEvent,
    BaseEvent,
    CantEvent,
    ClearAllBoostsEvent,
    ClearBoostsEvent,
    ClearNegativeBostsEvent,
    CopyBoostEvent,
    DamageEvent,
    DesyncEvent,
    DetailsChangeEvent,
    EffectSource,
    FormeChangeEvent,
    HealEvent,
    ItemEvent,
    MajorStatusEvent,
    MinorStatusActivationEvent,
    MinorStatusEvent,
    MoveActivationEvent,
    MoveCopiedEvent,
    MoveEvent,
    MovePrepareEvent,
    PartialTrapEvent,
    PerishCountEvent,
    PokemonIdent,
    PokemonSwitchEvent,
    SetHpEvent,
    SideConditionEvent,
    SingleMoveEvent,
    StatChangeEvent,
    StatSetEvent,
    TeamCureEvent,
    TransformEvent,
    TurnEvent,
    TypeChangeEvent,
    WeatherEvent,
)
from showdown_sdk.features.common import (
    SIDE_OPPONENT,
    SIDE_SELF,
    JSONScalar,
    PokemonRefFeatures,
    canonical,
    canonical_move,
    hp_ratio,
)
from showdown_sdk.features.pokemon import species_from_details
from showdown_sdk.models import to_id
from showdown_sdk.models.pokemon import (
    MajorStatus,
    MinorStatus,
    SideCondition,
    Unknown,
    Weather,
)
from showdown_sdk.models.sdk import BattleState, SourceType

## Constants


# Event type name -> converter prefix. Keeping an explicit tuple makes new
# and removed event classes a lintable, schema-visible decision.
_EVENT_TYPES: tuple[type[BaseEvent], ...] = (
    MoveEvent,
    DamageEvent,
    HealEvent,
    PokemonSwitchEvent,
    MajorStatusEvent,
    MinorStatusEvent,
    MinorStatusActivationEvent,
    StatChangeEvent,
    StatSetEvent,
    ClearBoostsEvent,
    ClearAllBoostsEvent,
    CopyBoostEvent,
    ClearNegativeBostsEvent,
    TeamCureEvent,
    WeatherEvent,
    SideConditionEvent,
    AbilityEvent,
    ItemEvent,
    TransformEvent,
    TypeChangeEvent,
    FormeChangeEvent,
    MovePrepareEvent,
    MoveCopiedEvent,
    MoveActivationEvent,
    SingleMoveEvent,
    CantEvent,
    PerishCountEvent,
    PartialTrapEvent,
    SetHpEvent,
    DetailsChangeEvent,
    DesyncEvent,
)

_EVENT_TYPE_NAMES: dict[type[BaseEvent], str] = {
    cls: cls.__name__.removesuffix("Event").lower() for cls in _EVENT_TYPES
}


## Data models


@dataclass(frozen=True)
class EventFeatures:
    """One semantic battle event, in chronological position."""

    event_type: str
    turn: int | None = None
    action_id: int | None = None

    source: PokemonRefFeatures | None = None
    target: PokemonRefFeatures | None = None

    effect_source_type: str | None = None

    move: str | None = None
    ability: str | None = None
    item: str | None = None
    species: str | None = None

    major_status: str | None = None
    minor_status: str | None = None
    weather: str | None = None
    side_condition: str | None = None

    stat: str | None = None
    stat_delta: int | None = None
    stat_value: int | None = None
    stat_changes: tuple[tuple[str, int], ...] | None = None

    hp_current: int | None = None
    hp_max: int | None = None
    hp_ratio: float | None = None

    effectiveness: float | None = None
    crit: bool | None = None
    hit_count: int | None = None

    success: bool | None = None
    does_hit: bool | None = None

    level: int | None = None
    types: tuple[str, ...] | None = None

    payload: dict[str, JSONScalar] = field(default_factory=dict)


@dataclass
class _Ctx:
    turn: int
    battle_state: BattleState

    source: EffectSource | None = None
    action_id: int | None = None
    actor: PokemonIdent | None = None
    target: PokemonIdent | None = None
    side: str | None = None

    species: str | None = None
    move: str | None = None
    ability: str | None = None
    item: str | None = None
    major_status: str | None = None
    minor_status: str | None = None
    weather: str | None = None
    side_condition: str | None = None

    stat: str | None = None
    stat_delta: int | None = None
    stat_value: int | None = None
    stat_changes: tuple[tuple[str, int], ...] | None = None

    curr_hp: int | None = None
    max_hp: int | None = None
    hp_is_percentage: bool = False

    effectiveness: float | None = None
    crit: bool | None = None
    hit_count: int | None = None
    success: bool | None = None
    does_hit: bool | None = None
    level: int | None = None
    types: tuple[str, ...] | None = None

    payload: dict[str, JSONScalar] = field(default_factory=dict)

    def event_features(self, event_type: str) -> EventFeatures:
        if self.curr_hp is None:
            ratio = None
        elif self.max_hp is not None:
            ratio = hp_ratio(self.curr_hp, self.max_hp)
        elif self.hp_is_percentage:
            ratio = self.curr_hp / 100
        else:
            ratio = None

        return EventFeatures(
            event_type=event_type,
            turn=self.turn,
            action_id=self.action_id,
            source=ref_to_features(self.actor, self.battle_state),
            target=ref_to_features(self.target, self.battle_state),
            effect_source_type=_source_type_name(self.source),
            move=self.move,
            ability=self.ability,
            item=self.item,
            species=self.species,
            major_status=self.major_status,
            minor_status=self.minor_status,
            weather=self.weather,
            side_condition=self.side_condition,
            stat=self.stat,
            stat_delta=self.stat_delta,
            stat_value=self.stat_value,
            stat_changes=self.stat_changes,
            hp_current=self.curr_hp,
            hp_max=self.max_hp,
            hp_ratio=ratio,
            effectiveness=self.effectiveness,
            crit=self.crit,
            hit_count=self.hit_count,
            success=self.success,
            does_hit=self.does_hit,
            level=self.level,
            types=self.types,
            payload=self.payload,
        )


## Helpers


def relative_side(player: str, battle_state: BattleState) -> str:
    player_id = battle_state.player_id

    if player_id is None:
        raise ValueError("battle_state.player_id is not initialized")

    if player == player_id:
        return SIDE_SELF

    if player in {"p1", "p2"} and player_id in {"p1", "p2"}:
        return SIDE_OPPONENT

    raise ValueError(
        f"Cannot resolve side {player!r} relative to {player_id!r}"
    )


def ref_to_features(
    ident: PokemonIdent | None, battle_state: BattleState
) -> PokemonRefFeatures | None:
    if ident is None:
        return None

    side = relative_side(ident.player, battle_state)
    key = f"{ident.player}{ident.slot or ''}: {ident.name}"

    if side == SIDE_SELF:
        slot = _own_slot_lookup(battle_state, ident)
    else:
        slot = _enemy_reveal_slots(battle_state).get(key)

    return PokemonRefFeatures(
        side=side,
        slot=slot,
        pokemon_id=canonical(key),
        species=_ident_species(ident, battle_state),
    )


## Feature builders


def event_to_features(
    event: BaseEvent, *, battle_state: BattleState, turn: int
) -> EventFeatures | None:
    """Convert one event. ``None`` for events not part of the semantic
    sequence (TurnEvent timestamps following events instead)."""
    event_type = _EVENT_TYPE_NAMES.get(type(event))

    if event_type is None or isinstance(event, TurnEvent):
        # TurnEvents are folded into the ``turn`` timestamp field instead of
        # consuming a history position.
        return None

    ctx = _Ctx(turn=turn, battle_state=battle_state)
    _convert_event(event, ctx)
    _apply_effect_source(ctx)

    return ctx.event_features(event_type)


def history_to_features(battle_state: BattleState) -> tuple[EventFeatures, ...]:
    """Full ordered semantic history; no truncation and no padding."""
    events: list[EventFeatures] = []
    turn = 0

    for event in battle_state.history:
        if isinstance(event, TurnEvent):
            turn = event.turn
            continue

        features = event_to_features(
            event, battle_state=battle_state, turn=turn
        )

        if features is not None:
            events.append(features)

    return tuple(events)


## Private helpers


def _source_type_name(source: EffectSource | None) -> str | None:
    return source.type.value if source is not None else None


def _own_slot_lookup(
    battle_state: BattleState, ident: PokemonIdent
) -> int | None:
    key = f"{ident.player}: {ident.name}"

    for index, pokemon in enumerate(battle_state.team):
        if pokemon.id == key:
            return index

    return None


def _enemy_reveal_slots(battle_state: BattleState) -> dict[str, int]:

    slots: dict[str, int] = {}

    for index, pokemon in enumerate(battle_state.enemy_team):
        if pokemon.id is not Unknown.VALUE:
            slots[pokemon.id] = index

    return slots


def _ident_species(
    ident: PokemonIdent, battle_state: BattleState
) -> str | None:
    side = relative_side(ident.player, battle_state)
    key = f"{ident.player}: {ident.name}"

    if side == SIDE_SELF:
        for pokemon in battle_state.team:
            if pokemon.id == key:
                return canonical(species_from_details(pokemon.details))
        return None

    slots = _enemy_reveal_slots(battle_state)

    if key in slots:
        enemy = battle_state.enemy_team[slots[key]]
        if enemy.species is not None:
            return canonical(enemy.species)

    return None


def _source_actor(source: EffectSource) -> PokemonIdent | None:
    return source.actor if source.actor is not None else source.owner


def _major_status_name(name: str) -> str | None:
    normalized = to_id(name.removeprefix("move: "))

    for status in MajorStatus:
        if to_id(status.value) == normalized:
            return canonical(status.value)

    return None


def _minor_status_name(name: str) -> str | None:
    normalized = to_id(name.removeprefix("move: "))

    for status in MinorStatus:
        if to_id(status.value) == normalized:
            return canonical(status.value)

    return None


def _weather_name(name: str) -> str | None:
    normalized = to_id(name)

    for weather in Weather:
        if to_id(weather.value) == normalized:
            return canonical(weather.value)

    return None


def _side_condition_name(name: str) -> str | None:
    normalized = to_id(name.removeprefix("move: "))

    for condition in SideCondition:
        if to_id(condition.value) == normalized:
            return canonical(condition.value)

    return None


def _convert_event(event: BaseEvent, ctx: _Ctx) -> None:
    match event:
        case MoveEvent():
            ctx.source = event.source
            ctx.action_id = event.action_id
            ctx.actor = event.source_pokemon
            ctx.target = event.target_pokemon
            ctx.move = canonical_move(event.move)
            ctx.success = event.success
            ctx.does_hit = event.does_hit
            ctx.hit_count = event.hit_count
            if event.failure_reason is not None:
                ctx.payload["failure_reason"] = event.failure_reason

        case DamageEvent():
            ctx.source = event.source
            ctx.target = event.target
            ctx.curr_hp = event.curr_hp
            ctx.max_hp = event.max_hp
            ctx.hp_is_percentage = event.hp_is_percentage
            ctx.effectiveness = (
                event.effectiveness
                if event.source.type is SourceType.MOVE
                else None
            )
            ctx.crit = event.crit

        case HealEvent() | SetHpEvent():
            ctx.source = event.source
            ctx.target = event.target
            ctx.curr_hp = event.curr_hp
            ctx.max_hp = event.max_hp
            ctx.hp_is_percentage = event.hp_is_percentage

        case MinorStatusEvent():
            ctx.source = event.source
            ctx.target = event.target
            ctx.minor_status = canonical(event.effect.value)
            ctx.payload["started"] = event.started

        case MajorStatusEvent():
            ctx.source = event.source
            ctx.target = event.target
            ctx.major_status = canonical(event.status.value)
            ctx.payload["applied"] = event.applied

        case MoveCopiedEvent():
            ctx.source = event.source
            ctx.target = event.target
            ctx.move = canonical_move(event.copied_move)
            ctx.payload["copied"] = True

        case MinorStatusActivationEvent():
            ctx.source = event.source
            ctx.target = event.target
            ctx.minor_status = canonical(event.effect.value)
            ctx.payload["activated"] = True

        case StatChangeEvent():
            ctx.source = event.source
            ctx.target = event.target
            ctx.success = event.success
            changes = tuple(
                (canonical(stat.value), delta)
                for stat, delta in event.stat_changes
            )
            ctx.stat_changes = changes
            if len(changes) == 1:
                ctx.stat = changes[0][0]
                ctx.stat_delta = changes[0][1]
            if event.failure_reason is not None:
                ctx.payload["failure_reason"] = event.failure_reason

        case StatSetEvent():
            ctx.source = event.source
            ctx.target = event.target
            ctx.stat = canonical(event.stat.value)
            ctx.stat_value = event.stage

        case MovePrepareEvent():
            ctx.actor = event.pokemon
            ctx.move = canonical_move(event.move)

        case TeamCureEvent():
            ctx.source = event.source
            ctx.actor = event.actor
            ctx.side = relative_side(event.side, ctx.battle_state)

        case ClearBoostsEvent() | ClearNegativeBostsEvent():
            ctx.source = event.source
            ctx.target = event.target

        case ClearAllBoostsEvent():
            ctx.source = event.source

        case CopyBoostEvent():
            ctx.source = event.source
            ctx.actor = event.user
            ctx.target = event.target

        case SideConditionEvent():
            ctx.source = event.source
            ctx.side_condition = canonical(event.condition.value)
            ctx.payload["started"] = event.started
            if event.side is not None:
                ctx.side = relative_side(event.side, ctx.battle_state)
            else:
                ctx.side = "field"

        case PokemonSwitchEvent():
            ctx.actor = event.pokemon
            ctx.species = canonical(species_from_details(event.details))
            ctx.level = event.level
            ctx.curr_hp = event.curr_hp
            ctx.max_hp = event.max_hp
            ctx.hp_is_percentage = event.hp_is_percentage
            ctx.major_status = (
                canonical(event.major_status.value)
                if event.major_status
                else None
            )
            ctx.payload["baton_pass"] = event.baton_pass

        case TransformEvent():
            ctx.source = event.source
            ctx.actor = event.pokemon
            ctx.target = event.target

        case AbilityEvent():
            ctx.source = event.source
            ctx.actor = event.pokemon
            ctx.ability = event.ability
            ctx.payload["active"] = event.active

        case MoveActivationEvent():
            ctx.actor = event.pokemon
            ctx.move = canonical_move(event.move)

        case ItemEvent():
            ctx.source = event.source
            ctx.actor = event.previous_owner
            ctx.target = event.pokemon
            ctx.item = canonical(event.item)
            ctx.payload["gained"] = event.gained
            ctx.payload["consumed"] = event.consumed

        case CantEvent():
            ctx.actor = event.pokemon
            ctx.move = canonical_move(event.move) if event.move else None
            ctx.payload["reason"] = event.reason

        case PerishCountEvent():
            ctx.source = event.source
            ctx.target = event.target
            ctx.payload["perish_count"] = event.count

        case WeatherEvent():
            ctx.source = event.source
            ctx.weather = canonical(event.weather.value)
            ctx.payload["started"] = event.started
            ctx.payload["upkeep"] = event.upkeep

        case SingleMoveEvent():
            ctx.source = event.source
            ctx.actor = event.pokemon
            ctx.move = canonical_move(event.move)

        case TypeChangeEvent():
            ctx.source = event.source
            ctx.target = event.target
            ctx.types = event.types

        case FormeChangeEvent():
            ctx.source = event.source
            ctx.actor = event.pokemon
            ctx.species = canonical(event.forme)

        case PartialTrapEvent():
            ctx.source = event.source
            ctx.target = event.target
            ctx.move = canonical_move(event.move)
            ctx.payload["started"] = event.started

        case DetailsChangeEvent():
            ctx.actor = event.pokemon
            ctx.species = canonical(species_from_details(event.details))
            ctx.level = event.level

        case DesyncEvent():
            pass

        case _:
            raise AssertionError(f"Unhandled history event: {event!r}")


def _apply_effect_source(ctx: _Ctx) -> None:
    source = ctx.source

    if source is None:
        return

    if ctx.action_id is None:
        ctx.action_id = source.action_id

    if ctx.actor is None:
        ctx.actor = _source_actor(source)

    if source.name is None:
        return

    match source.type:
        case SourceType.MOVE | SourceType.RECOIL:
            ctx.move = ctx.move or canonical_move(source.name)
        case SourceType.ABILITY:
            ctx.ability = ctx.ability or source.name
        case SourceType.ITEM:
            ctx.item = ctx.item or source.name
        case SourceType.STATUS:
            if ctx.major_status is None and ctx.minor_status is None:
                ctx.major_status = _major_status_name(source.name)
                if ctx.major_status is None:
                    ctx.minor_status = _minor_status_name(source.name)
        case SourceType.WEATHER:
            ctx.weather = ctx.weather or _weather_name(source.name)
        case SourceType.SIDE_CONDITION:
            ctx.side_condition = ctx.side_condition or _side_condition_name(
                source.name
            )
        case _:
            pass
