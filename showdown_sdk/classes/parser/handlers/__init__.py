"""
Handlers for the parsers
raw line ---> [handler] ---> list[event]
"""

from showdown_sdk.classes.parser.handlers.commands import (
    COMMAND_HANDLERS,
    CommandHandler,
    handle_battle_end,
    handle_cant,
    handle_custom_showdown_battle_state,
    handle_error,
    handle_gametype,
    handle_gen,
    handle_player,
    handle_room,
    handle_switch,
    handle_tier,
    handle_turn,
    handle_upkeep,
)
from showdown_sdk.classes.parser.handlers.effects import (
    is_failed_stat_change,
    parse_effect_message,
)
from showdown_sdk.classes.parser.handlers.moves import (
    parse_move_group,
    parse_standalone_effect,
)
from showdown_sdk.classes.parser.handlers.requests import parse_request_event

__all__ = [
    "COMMAND_HANDLERS",
    "CommandHandler",
    "handle_battle_end",
    "handle_cant",
    "handle_custom_showdown_battle_state",
    "handle_error",
    "handle_gametype",
    "handle_gen",
    "handle_player",
    "handle_room",
    "handle_switch",
    "handle_tier",
    "handle_turn",
    "handle_upkeep",
    "is_failed_stat_change",
    "parse_effect_message",
    "parse_move_group",
    "parse_request_event",
    "parse_standalone_effect",
]
