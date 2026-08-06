from abc import ABC
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from python_showdown.classes.client.client import Client
    from python_showdown.classes.combat.battle_state import BattleState


@dataclass(frozen=True)
class ProtocolAnnotation:
    """An annotation such as `[from] psn`, `[of] p1a: Pikachu`, or `[silent]`."""

    name: str
    value: str | None = None


@dataclass(frozen=True)
class ProtocolMessage:
    """A normalized, lossless Pokémon Showdown protocol line."""

    command: str
    arguments: tuple[str, ...]
    annotations: tuple[ProtocolAnnotation, ...]
    raw: str


@dataclass(frozen=True)
class PokemonIdent:
    """A protocol Pokémon reference such as `p1a: Poliwhirl` or `p1: Snorlax`."""

    player: str
    slot: str | None
    name: str


class SourceType(str, Enum):
    MOVE = "move"
    ITEM = "item"
    ABILITY = "ability"
    STATUS = "status"
    WEATHER = "weather"
    TERRAIN = "terrain"
    SIDE_CONDITION = "side_condition"
    RECOIL = "recoil"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EffectSource:
    """What caused an effect, and the move action it belongs to when applicable."""

    type: SourceType
    name: str | None = None
    actor: PokemonIdent | None = None
    action_id: int | None = None


class Weather(str, Enum):
    SUNNY_DAY = "SunnyDay"
    RAIN = "RainDance"
    HAIL = "Hail"
    SNOW = "Snow"
    CLEAR_SKY = "none"  # default
    SANDSTORM = "Sandstorm"


class Stat(str, Enum):
    ATK = "atk"
    DEF = "def"
    SPA = "spa"
    SPD = "spd"
    SPE = "spe"
    EVA = "evasion"
    ACC = "accuracy"


class SideCondition(str, Enum):
    SPIKES = "Spikes"
    TOXIC_SPIKES = "Toxic Spikes"
    STEALTH_ROCK = "Stealth Rock"
    REFLECT = "Reflect"
    SAFEGUARD = "Safeguard"
    LIGHT_SCREEN = "Light Screen"


class MajorStatus(str, Enum):
    SLEEP = "slp"
    POISON = "psn"
    TOXIC = "tox"
    PARALYSIS = "par"
    BURN = "brn"
    FREEZE = "frz"
    FAINT = "fnt"


class MinorStatus(str, Enum):
    CONFUSION = "confusion"
    LEECH_SEED = "Leech Seed"
    SUBSTITUTE = "Substitute"
    ENCORE = "Encore"
    ATTRACT = "Attract"
    YAWN = "Yawn"
    TYPECHANGE = "typechange"
    PERISH_SONG = "perish"  # countdown tracked separately in `perish_count`
    FLASH_FIRE = "Flash Fire"  # ability-granted immunity flag
    WRAP = "Wrap"  # trapping moves such as Wrap, Bind, Clamp, etc.
    FLINCH = "Flinch"
    RECHARGE = "Recharge"
    FLY = "Fly"
    DIVE = "Dive"
    TUNNEL = "Tunnel"
    PROTECT = "Protect"
    NIGHTMARE = "Nightmare"
    ENDURE = "Endure"
    TRAPPED = "Trapped"
    WHIRLPOOL = "Whirlpool"
    ROOST = "Roost"
    TAUNT = "Taunt"
    FOCUS_PUNCH = "Focus Punch"


class BaseEvent(ABC):
    """A complete semantic event derived from one or more protocol messages."""

    # TODO: Make abstract once every event has a reducer implementation.
    # Every event should be able to edit the battle state, much like Git applies
    # changes while rebuilding history.
    def update_battle_state(self, battle_state: BattleState) -> None:
        return

    def update_client(self, client: Client) -> None:
        self.update_battle_state(client.battle_state)


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

    def __post_init__(self) -> None:
        if not self.success and self.does_hit:
            raise ValueError("A failed move cannot be marked as having hit")
        if self.hit_count is not None and self.hit_count <= 0:
            raise ValueError(
                "A move hit count must be positive"
            )

@dataclass(frozen=True)
class DamageEvent(BaseEvent):
    source: EffectSource
    target: PokemonIdent
    curr_hp: int
    max_hp: int | None
    hp_is_percentage: bool
    effectiveness: float = 1.0
    crit: bool = False


@dataclass(frozen=True)
class HealEvent(BaseEvent):
    source: EffectSource
    target: PokemonIdent
    curr_hp: int
    max_hp: int | None
    hp_is_percentage: bool


@dataclass(frozen=True)
class MinorStatusEvent(BaseEvent):
    source: EffectSource | None
    target: PokemonIdent
    effect: MinorStatus
    started: bool


@dataclass(frozen=True)
class MajorStatusEvent(BaseEvent):
    source: EffectSource | None
    target: PokemonIdent
    status: MajorStatus
    applied: bool

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

@dataclass(frozen=True)
class MovePrepareEvent(BaseEvent):
    """
    Records the preparation turn of a multi-turn move.

    Example:
        |-prepare|p1a: Pidgeot|Sky Attack
    """

    pokemon: PokemonIdent
    move: str

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

@dataclass(frozen=True)
class ClearAllBoostsEvent(BaseEvent):
    """
    Resets all active Pokémon's stat stages to zero.
    """

    source: EffectSource | None

@dataclass(frozen=True)
class ClearNegativeBostsEvent(BaseEvent):
    """
    Resests all active Pokémon's négative stat changes to zéro
    """
    source: EffectSource | None

@dataclass(frozen=True)
class SetHpEvent(BaseEvent):
    source: EffectSource
    target: PokemonIdent
    curr_hp: int
    max_hp: int | None
    hp_is_percentage: bool

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

@dataclass(frozen=True)
class AbilityEvent(BaseEvent):
    pokemon: PokemonIdent
    ability: str
    active: bool = True
    source: EffectSource | None = None
    context: str | None = None

@dataclass(frozen=True)
class StatSetEvent(BaseEvent):
    source: EffectSource
    target: PokemonIdent
    stat: Stat
    stage: int

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

@dataclass(frozen=True)
class CantEvent(BaseEvent):
    pokemon: PokemonIdent
    reason: str
    move: str | None = None


@dataclass(frozen=True)
class DecisionRequestEvent(BaseEvent):
    player_id: str
    request_id: int | None
    wait: bool
    force_switch: tuple[bool, ...]
    payload: dict[str, Any]

@dataclass(frozen=True)
class PerishCountEvent(BaseEvent):
    source: EffectSource | None
    target: PokemonIdent
    count: int

@dataclass(frozen=True)
class TurnEvent(BaseEvent):
    turn: int

@dataclass(frozen=True)
class WeatherEvent(BaseEvent):
    weather: Weather
    started: bool
    upkeep: bool
    source: EffectSource | None = None

@dataclass(frozen=True)
class BattleEndEvent(BaseEvent):
    winner: str | None


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
