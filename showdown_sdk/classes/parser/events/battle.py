from dataclasses import dataclass
from typing import ClassVar

from showdown_sdk.classes.parser.events.base import BaseEvent
from showdown_sdk.classes.parser.models import (
    EffectSource,
    PokemonIdent,
    RequestMove,
    RequestPokemon,
)
from showdown_sdk.models.pokemon.status import MajorStatus, MinorStatus, Stat
from showdown_sdk.models.pokemon.terrain import SideCondition, Weather
from showdown_sdk.utils.serialization import SerializableObject


class BattleEvent(BaseEvent):
    """Semantic battle protocol event.

    Battle events are immutable facts. Applying them to SDK state or live
    runtime state is handled by ``parser.reducers.battle``.
    """


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


@dataclass(frozen=True)
class DamageEvent(BattleEvent):
    source: EffectSource
    target: PokemonIdent
    curr_hp: int
    max_hp: int | None
    hp_is_percentage: bool
    effectiveness: float = 1.0
    crit: bool = False


@dataclass(frozen=True)
class HealEvent(BattleEvent):
    source: EffectSource
    target: PokemonIdent
    curr_hp: int
    max_hp: int | None
    hp_is_percentage: bool


@dataclass(frozen=True)
class MinorStatusEvent(BattleEvent):
    source: EffectSource | None
    target: PokemonIdent
    effect: MinorStatus
    started: bool


@dataclass(frozen=True)
class MajorStatusEvent(BattleEvent):
    source: EffectSource | None
    target: PokemonIdent
    status: MajorStatus
    applied: bool


@dataclass(frozen=True)
class MoveCopiedEvent(BattleEvent):
    """Records a temporary move copy such as Mimic."""

    source: EffectSource
    target: PokemonIdent
    copied_move: str


@dataclass(frozen=True)
class MinorStatusActivationEvent(BattleEvent):
    """Records that an existing volatile status activated."""

    source: EffectSource
    target: PokemonIdent
    effect: MinorStatus


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


@dataclass(frozen=True)
class MovePrepareEvent(BattleEvent):
    """Records the preparation turn of a multi-turn move."""

    pokemon: PokemonIdent
    move: str


@dataclass(frozen=True)
class TeamCureEvent(BattleEvent):
    """Records all major statuses being cured on one side."""

    source: EffectSource
    side: str
    actor: PokemonIdent


@dataclass(frozen=True)
class ClearBoostsEvent(BattleEvent):
    """Reset one Pokémon's stat stages."""

    source: EffectSource
    target: PokemonIdent


@dataclass(frozen=True)
class ClearAllBoostsEvent(BattleEvent):
    """Reset every active Pokémon's stat stages."""

    source: EffectSource


@dataclass(frozen=True)
class CopyBoostEvent(BattleEvent):
    user: PokemonIdent
    target: PokemonIdent
    source: EffectSource


@dataclass(frozen=True)
class ClearNegativeBostsEvent(BattleEvent):
    """Resets all active Pokémon's negative stat changes to zero."""

    target: PokemonIdent
    source: EffectSource | None


@dataclass(frozen=True)
class SetHpEvent(BattleEvent):
    source: EffectSource
    target: PokemonIdent
    curr_hp: int
    max_hp: int | None
    hp_is_percentage: bool


@dataclass(frozen=True)
class SideConditionEvent(BattleEvent):
    """Records a side-wide condition starting or ending."""

    source: EffectSource | None
    side: str | None  # None here means both
    condition: SideCondition
    started: bool


@dataclass(frozen=True)
class PokemonSwitchEvent(BattleEvent):
    pokemon: PokemonIdent
    details: str
    level: int
    curr_hp: int
    max_hp: int | None
    hp_is_percentage: bool
    major_status: MajorStatus | None
    command: str = "switch"
    baton_pass: bool = False


@dataclass(frozen=True)
class TransformEvent(BattleEvent):
    """Records one Pokémon transforming into another."""

    source: EffectSource
    pokemon: PokemonIdent
    target: PokemonIdent


@dataclass(frozen=True)
class AbilityEvent(BattleEvent):
    pokemon: PokemonIdent
    ability: str
    active: bool = True
    source: EffectSource | None = None
    context: str | None = None
    reveals_base: bool = False


@dataclass(frozen=True)
class StatSetEvent(BattleEvent):
    source: EffectSource
    target: PokemonIdent
    stat: Stat
    stage: int


@dataclass(frozen=True)
class MoveActivationEvent(BattleEvent):
    """Records a move-related activation that is not a normal |move| line."""

    pokemon: PokemonIdent
    move: str


@dataclass(frozen=True)
class ItemEvent(BattleEvent):
    """Records a Pokémon gaining, revealing, losing, or transferring an item."""

    source: EffectSource
    pokemon: PokemonIdent | None
    item: str
    gained: bool
    consumed: bool
    previous_owner: PokemonIdent | None = None


@dataclass(frozen=True)
class CantEvent(BattleEvent):
    pokemon: PokemonIdent
    reason: str
    move: str | None = None


@dataclass(frozen=True)
class PerishCountEvent(BattleEvent):
    source: EffectSource | None
    target: PokemonIdent
    count: int


@dataclass(frozen=True)
class TurnEvent(BattleEvent):
    turn: int


@dataclass(frozen=True)
class UpkeepEvent(BattleEvent):
    pass


@dataclass(frozen=True)
class WeatherEvent(BattleEvent):
    weather: Weather
    started: bool
    upkeep: bool
    source: EffectSource | None = None


@dataclass(frozen=True)
class BattleEndEvent(BattleEvent):
    winner: str | None
    room_id: str


@dataclass(frozen=True)
class RoomEvent(BattleEvent):
    """``>roomid`` — the following messages belong to this room."""

    room_id: str | None


@dataclass(frozen=True)
class BattleStartEvent(BattleEvent):
    """``|init|battle`` — the server opening a new battle room."""

    room_id: str


@dataclass(frozen=True)
class PlayerEvent(BattleEvent):
    """``|player|<slot>|<name>|...`` — a side announcement."""

    slot: str
    name: str


@dataclass(frozen=True)
class SingleMoveEvent(BattleEvent):
    source: EffectSource | None
    pokemon: PokemonIdent
    move: str


@dataclass(frozen=True)
class TypeChangeEvent(BattleEvent):
    source: EffectSource
    target: PokemonIdent
    types: tuple[str, ...]


@dataclass(frozen=True)
class FormeChangeEvent(BattleEvent):
    """Records a Pokémon changing to a different forme."""

    source: EffectSource
    pokemon: PokemonIdent
    forme: str


@dataclass(frozen=True)
class DesyncEvent(BattleEvent):
    """Records when a Gen 1 battle gets a desync."""


@dataclass(frozen=True)
class DecisionRequestEvent(BattleEvent):
    player_id: str
    request_id: int | None
    wait: bool
    trapped: bool | None  # None means showdown didn't tell us
    maybe_trapped: bool | None
    maybe_locked: bool
    maybe_disabled: bool
    update: bool
    force_switch: tuple[bool, ...]
    moves: tuple[RequestMove, ...]
    pokemon: tuple[RequestPokemon, ...]
    no_cancel: bool


@dataclass(frozen=True)
class GameTypeEvent(BattleEvent):
    type: str
    IMPLEMENTED_TYPES: ClassVar[tuple[str, ...]] = ("singles",)


@dataclass(frozen=True)
class GameGenEvent(BattleEvent):
    gen: int
    LAST_IMPLEMENTED_GEN: ClassVar[int] = 5


@dataclass(frozen=True)
class GameTierEvent(BattleEvent):
    tier: str
    IMPLEMENTED_TIERS: ClassVar[tuple[str, ...]] = (
        "Random Battle",
        "OU",
        "Ubers",
    )


@dataclass(frozen=True)
class PartialTrapEvent(BattleEvent):
    target: PokemonIdent
    move: str
    source: EffectSource
    started: bool


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


@dataclass(frozen=True)
class CustomShowdownBattleStateEvent(BattleEvent):
    content: SerializableObject


@dataclass(frozen=True)
class DetailsChangeEvent(BattleEvent):
    """Records a permanent change to a Pokémon's visible details."""

    pokemon: PokemonIdent
    details: str
    level: int
