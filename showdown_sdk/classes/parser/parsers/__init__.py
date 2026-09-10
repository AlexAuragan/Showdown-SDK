"""Scoped protocol message parsers.

Each :class:`MessageParser` handles one slice of the Pokémon Showdown
protocol. The top-level :class:`~showdown_sdk.classes.parser.Parser`
aggregator routes incoming :class:`ProtocolMessage` objects to the appropriate
parser based on the message's command.

- :class:`BattleParser` — battle-room messages (moves, effects, requests, ...).
- :class:`LobbyParser` — session messages (login, formats, challenges).
"""

from showdown_sdk.classes.parser.events.lobby import (
    FormatsEvent,
    NameTakenEvent,
    PrivateMessageEvent,
    UpdateUserEvent,
)
from showdown_sdk.classes.parser.parsers.base import MessageParser
from showdown_sdk.classes.parser.parsers.battle import BattleParser, ParseResult
from showdown_sdk.classes.parser.parsers.lobby import LobbyParser

__all__ = [
    "BattleParser",
    "FormatsEvent",
    "LobbyParser",
    "MessageParser",
    "NameTakenEvent",
    "ParseResult",
    "PrivateMessageEvent",
    "UpdateUserEvent",
]
