"""Pokémon Showdown protocol parser — aggregator entry point.

The parser is an *aggregator*: it does not interpret protocol messages itself.
Instead it routes each incoming :class:`ProtocolMessage` to the scoped
:class:`MessageParser` responsible for it (battle vs. lobby, with room for an
error/choice-retry manager later).

"""

from collections.abc import Sequence

from python_showdown.classes.combat_handler.battle_manager import BattleManager
from python_showdown.classes.parser.events import (
    BaseEvent,
)
from python_showdown.classes.parser.models import ProtocolMessage
from python_showdown.classes.parser.parsers.base import MessageParser
from python_showdown.classes.parser.parsers.battle import BattleParser
from python_showdown.classes.parser.parsers.lobby import LobbyParser
from python_showdown.classes.parser.protocol import (
    extract_protocol_line,
    parse_protocol_message,
)

_LOBBY_COMMANDS = frozenset(
    {
        "updateuser",
        "nametaken",
        "formats",
        "pm",
        "customgroups",
        "challstr",
        "updatesearch",
        "popup",
        "clearpoke",  # Not sure about these 3
        "poke",
        "teampreview",
    }
)


class Parser:
    """Aggregate raw protocol messages by routing them to scoped managers."""

    def __init__(self, manager: BattleManager) -> None:
        self.manager: BattleManager = manager

        self.battle: BattleParser = BattleParser(manager)
        self.lobby: LobbyParser = LobbyParser()

        self.expecting_battle_room: bool = False
        self.ignored_battle_rooms: set[str] = set()

    @property
    def last_message_room_id(self) -> str:
        return self.battle.last_message_room_id

    @last_message_room_id.setter
    def last_message_room_id(self, value: str):
        self.battle.last_message_room_id = value

    def handle_line(
        self,
        line: str,
        *,
        has_log_timestamp: bool = False,
    ) -> list[BaseEvent]:
        """Live entry point: route one raw line to the appropriate manager.

        Returns the semantic events produced this tick (possibly empty, e.g.
        while a multi-message ``|move|`` group is still being accumulated or
        the message was intentionally ignored).
        """
        protocol_line = extract_protocol_line(line, has_log_timestamp=has_log_timestamp)
        message = parse_protocol_message(protocol_line)
        parser = self._manager_for(message)

        if message.command == "room":
            room_id = message.arguments[0].strip()
            if not room_id:
                raise RuntimeError(
                    f"Receive empty room id from protocol line: {line!r}"
                )
            self.last_message_room_id = room_id

        if parser is self.battle:
            room_id = self.last_message_room_id
            active_room_id = self.manager.room_id

            if active_room_id is None:
                if not self.expecting_battle_room:
                    if room_id.startswith("battle-"):
                        self.ignored_battle_rooms.add(room_id)
                    return []

            elif room_id != active_room_id:
                if room_id.startswith("battle-"):
                    self.ignored_battle_rooms.add(room_id)
                return []
        events = parser.handle_message(self.manager, message)

        return events

    def _manager_for(self, message: ProtocolMessage) -> MessageParser:
        if message.command in _LOBBY_COMMANDS:
            return self.lobby
        return self.battle

    def finish(self, player_id: str) -> Sequence[BaseEvent]:
        """Flush any buffered battle events at the end of a replay stream."""
        return self.battle.finish(player_id)

    # -- battle accessors (log-replay / __main__ compatibility) -------------
    # Exposed so the replay harness and any external callers that previously
    # read these off the Parser keep working; they all reflect battle state.

    @property
    def raw_history(self) -> list[ProtocolMessage]:
        return self.battle.raw_history

    @property
    def history(self) -> Sequence[BaseEvent]:
        return self.battle.history

    @property
    def pending_messages(self) -> tuple[ProtocolMessage, ...]:
        return self.battle.pending_messages

    @property
    def battle_state(self):
        return self.manager.battle_state

    @property
    def player_id(self) -> str | None:
        return self.manager.player_id
