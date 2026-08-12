"""Pokémon Showdown protocol parser.

This package aggregates raw protocol messages into complete semantic events.

Public entry point: :class:`Parser`.
"""

from python_showdown.classes.parser.ability_state import (
    ProtocolContext,
    update_protocol_context,
)
from python_showdown.classes.parser.battle_state_handler import BattleStateHandler
from python_showdown.classes.parser.command_handlers import (
    COMMAND_HANDLERS,
    CommandHandler,
    handle_battle_end,
    handle_cant,
    handle_request,
    handle_switch,
    handle_turn,
    parse_move_group,
    parse_standalone_effect,
)
from python_showdown.classes.parser.context import (
    EffectHandler,
    EffectParseContext,
    EffectPredicate,
    EffectRule,
    MoveParseState,
    ParsedCondition,
    TargetModifiers,
)
from python_showdown.classes.parser.events import (
    AbilityEvent,
    BaseEvent,
    BattleEndEvent,
    BattleStartEvent,
    CantEvent,
    ClearAllBoostsEvent,
    ClearNegativeBostsEvent,
    DamageEvent,
    DecisionRequestEvent,
    DiscardedEvent,
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
    UnhandledEvent,
    WeatherEvent,
    unhandled_event,
)
from python_showdown.classes.parser.exceptions import (
    ParserException,
    WrongRoomException,
)
from python_showdown.classes.parser.fields import (
    is_percentage_hp,
    make_move_source,
    parse_condition,
    parse_effect_source,
    parse_level,
    parse_minor_status,
    parse_pokemon_ident,
    parse_side_ident,
)
from python_showdown.classes.parser.managers.base import MessageParser
from python_showdown.classes.parser.managers.battle import BattleParser, ParseResult
from python_showdown.classes.parser.managers.lobby import (
    FormatsEvent,
    LobbyParser,
    NameTakenEvent,
    PrivateMessageEvent,
    UpdateUserEvent,
)
from python_showdown.classes.parser.models import (
    EffectSource,
    PokemonIdent,
    ProtocolAnnotation,
    ProtocolMessage,
)
from python_showdown.classes.parser.parser import Parser
from python_showdown.classes.parser.protocol import (
    annotation_value,
    extract_protocol_line,
    has_annotation,
    is_ignored_message,
    is_move_boundary,
    parse_protocol_message,
    require_arguments,
)

__all__ = [
    # Entry point
    "Parser",
    "ParseResult",

    # Managers
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
    "handle_request",
    "handle_cant",
    "handle_battle_end",
    "parse_move_group",
    "parse_standalone_effect",

    # Ability state
    "update_protocol_context",

    # Battle state
    "BattleStateHandler",
]
