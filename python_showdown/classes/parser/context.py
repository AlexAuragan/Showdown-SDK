from collections.abc import Callable
from dataclasses import dataclass, field

from python_showdown.classes.parser.events import BaseEvent
from python_showdown.classes.parser.models import (
    EffectSource,
    PokemonIdent,
    ProtocolMessage,
)
from python_showdown.models.pokemon.status import MajorStatus


@dataclass
class ProtocolContext:
    active_ability_states: dict[
        tuple[str, str | None],
        set[str],
    ] = field(default_factory=dict)

    known_ability_states: dict[
        tuple[str, str | None],
        set[str],
    ] = field(default_factory=dict)

@dataclass(frozen=True)
class ParsedCondition:
    current_hp: int
    max_hp: int | None
    status: MajorStatus | None


@dataclass
class TargetModifiers:
    # Critical-hit metadata applies to the next damage line for this target.
    next_critical: bool = False
    # Effectiveness normally applies to each hit of the same move.
    effectiveness: float = 1.0


@dataclass
class EffectParseContext:
    player_id: str
    source: EffectSource
    protocol_context: ProtocolContext
    action_id: int | None = None
    modifiers: dict[PokemonIdent, TargetModifiers] = field(default_factory=dict)


@dataclass
class MoveParseState:
    success: bool = True
    does_hit: bool = True
    failure_reason: str | None = None
    hit_count: int | None = None


EffectHandler = Callable[[ProtocolMessage, EffectParseContext], list[BaseEvent]]
EffectPredicate = Callable[[ProtocolMessage, EffectParseContext], bool]


@dataclass(frozen=True)
class EffectRule:
    predicate: EffectPredicate
    handler: EffectHandler
