"""Scoped protocol message managers.

Each :class:`MessageManager` handles one slice of the Pokémon Showdown
protocol. The top-level :class:`~python_showdown.classes.parser.Parser`
aggregator routes incoming :class:`ProtocolMessage` objects to the appropriate
manager based on the message's command.

- :class:`BattleParser` — battle-room messages (moves, effects, requests, ...).
- :class:`LobbyParser` — session messages (login, formats, challenges).
"""

from python_showdown.classes.parser.events.lobby import (
    FormatsEvent,
    NameTakenEvent,
    PrivateMessageEvent,
    UpdateUserEvent,
)
from python_showdown.classes.parser.parsers.base import MessageParser
from python_showdown.classes.parser.parsers.battle import BattleParser, ParseResult
from python_showdown.classes.parser.parsers.lobby import LobbyParser

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
