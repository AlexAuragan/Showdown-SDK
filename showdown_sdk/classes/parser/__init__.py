"""Pokémon Showdown protocol parser.

This package aggregates raw protocol messages into complete semantic events.

Public entry point: :class:`Parser`.
"""

from showdown_sdk.classes.parser.battle_state_handler import BattleStateHandler
from showdown_sdk.classes.parser.context import (
    EffectHandler,
    EffectParseContext,
    EffectPredicate,
    EffectRule,
    MoveParseState,
    ParsedCondition,
    ProtocolContext,
    TargetModifiers,
)
from showdown_sdk.classes.parser.context_updates import (
    update_protocol_context,
)
from showdown_sdk.classes.parser.events.base import (
    BaseEvent,
    DiscardedEvent,
    UnhandledEvent,
    unhandled_event,
)
from showdown_sdk.classes.parser.events.battle import (
    AbilityEvent,
    BattleEndEvent,
    BattleStartEvent,
    CantEvent,
    ClearAllBoostsEvent,
    ClearNegativeBostsEvent,
    DamageEvent,
    DecisionRequestEvent,
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
    PerishCountEvent,
    PlayerEvent,
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
from showdown_sdk.classes.parser.events.lobby import (
    FormatsEvent,
    NameTakenEvent,
    PrivateMessageEvent,
    UpdateUserEvent,
)
from showdown_sdk.classes.parser.exceptions import (
    ParserException,
    WrongRoomException,
)
from showdown_sdk.classes.parser.fields import (
    is_percentage_hp,
    make_move_source,
    parse_condition,
    parse_effect_source,
    parse_level,
    parse_minor_status,
    parse_pokemon_ident,
    parse_side_ident,
)
from showdown_sdk.classes.parser.handlers.commands import (
    COMMAND_HANDLERS,
    CommandHandler,
    handle_battle_end,
    handle_cant,
    handle_switch,
    handle_turn,
)
from showdown_sdk.classes.parser.handlers.moves import (
    parse_move_group,
    parse_standalone_effect,
)
from showdown_sdk.classes.parser.models import (
    EffectSource,
    PokemonIdent,
    ProtocolAnnotation,
    ProtocolMessage,
)
from showdown_sdk.classes.parser.parser import Parser
from showdown_sdk.classes.parser.parsers.base import MessageParser
from showdown_sdk.classes.parser.parsers.battle import BattleParser, ParseResult
from showdown_sdk.classes.parser.parsers.lobby import (
    LobbyParser,
)
from showdown_sdk.classes.parser.protocol import (
    annotation_value,
    extract_protocol_line,
    has_annotation,
    is_ignored_message,
    is_move_boundary,
    parse_protocol_message,
    require_arguments,
)

# ruff: noqa: RUF022
__all__ = [
    # Entry point
    "Parser",
    "ParseResult",
    # Parsers
    "MessageParser",
    "BattleParser",
    "LobbyParser",
    # Lobby events
    "UpdateUserEvent",
    "NameTakenEvent",
    "FormatsEvent",
    "PrivateMessageEvent",
    # Exceptions
    "ParserException",
    "WrongRoomException",
    # Meta models
    "ProtocolAnnotation",
    "ProtocolMessage",
    "PokemonIdent",
    "EffectSource",
    # Events
    "BaseEvent",
    "MoveEvent",
    "DamageEvent",
    "HealEvent",
    "MinorStatusEvent",
    "MajorStatusEvent",
    "MoveCopiedEvent",
    "MinorStatusActivationEvent",
    "StatChangeEvent",
    "MovePrepareEvent",
    "TeamCureEvent",
    "ClearAllBoostsEvent",
    "ClearNegativeBostsEvent",
    "SetHpEvent",
    "SideConditionEvent",
    "PokemonSwitchEvent",
    "TransformEvent",
    "AbilityEvent",
    "StatSetEvent",
    "MoveActivationEvent",
    "ItemEvent",
    "CantEvent",
    "DecisionRequestEvent",
    "PerishCountEvent",
    "TurnEvent",
    "WeatherEvent",
    "BattleEndEvent",
    "BattleStartEvent",
    "PlayerEvent",
    "UnhandledEvent",
    "DiscardedEvent",
    "SingleMoveEvent",
    "TypeChangeEvent",
    "FormeChangeEvent",
    "unhandled_event",
    # Context
    "ProtocolContext",
    "ParsedCondition",
    "TargetModifiers",
    "EffectParseContext",
    "MoveParseState",
    "EffectHandler",
    "EffectPredicate",
    "EffectRule",
    # Protocol helpers
    "parse_protocol_message",
    "extract_protocol_line",
    "annotation_value",
    "has_annotation",
    "is_ignored_message",
    "is_move_boundary",
    "require_arguments",
    # Field parsers
    "parse_pokemon_ident",
    "parse_condition",
    "parse_level",
    "is_percentage_hp",
    "parse_minor_status",
    "parse_side_ident",
    "parse_effect_source",
    "make_move_source",
    # Command handlers
    "CommandHandler",
    "COMMAND_HANDLERS",
    "handle_switch",
    "handle_turn",
    "handle_cant",
    "handle_battle_end",
    "parse_move_group",
    "parse_standalone_effect",
    # Protocol context
    "update_protocol_context",
    # Battle state
    "BattleStateHandler",
]
