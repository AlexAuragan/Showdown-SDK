"""Pokémon Showdown protocol parser — aggregator entry point.

The parser is an *aggregator*: it does not interpret protocol messages itself.
Instead it routes each incoming :class:`ProtocolMessage` to the scoped
:class:`MessageParser` responsible for it (battle vs. lobby, with room for an
error/choice-retry manager later) and then applies the resulting events onto
the client.

"""

import os
from collections import Counter
from collections.abc import Sequence
from pprint import pprint
from typing import TYPE_CHECKING

from python_showdown.classes.combat_handler.battle_manager import BattleManager
from python_showdown.classes.parser.events import (
    BaseEvent,
    UnhandledEvent,
)
from python_showdown.classes.parser.exceptions import (
    InvalidActionError,
    ObsoleteRequestIdError,
)
from python_showdown.classes.parser.managers.base import MessageParser
from python_showdown.classes.parser.managers.battle import BattleParser
from python_showdown.classes.parser.managers.lobby import LobbyParser
from python_showdown.classes.parser.models import ProtocolMessage
from python_showdown.classes.parser.protocol import (
    extract_protocol_line,
    parse_protocol_message,
)

if TYPE_CHECKING:
    from python_showdown.classes.client.client import Client


_LOBBY_COMMANDS = frozenset(
    {
        "updateuser",
        "nametaken",
        "formats",
        "pm",
        "customgroups",
        "challstr",
        "updatesearch",
    }
)


class Parser:
    """Aggregate raw protocol messages by routing them to scoped managers."""

    def __init__(self, manager: BattleManager, client: Client) -> None:
        self.manager: BattleManager = manager
        self.client: Client = client
        self.battle: BattleParser = BattleParser(manager)
        self.lobby: LobbyParser = LobbyParser()

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

        if (
            parser is self.battle
            and message.command != "init"
            and self.manager.room_id
            and self.manager.room_id != self.last_message_room_id
        ):
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
        return self.client.battle_manager.battle_state

    @property
    def player_id(self) -> str | None:
        return self.manager.player_id


if __name__ == "__main__":
    from python_showdown.classes.client.client import Client
    from python_showdown.classes.parser.exceptions import ParserException

    for path in [
        "gen1randombattle",
        "gen2randombattle",
        "gen3randombattle",
        "gen4randombattle",
    ]:
        for filename in os.listdir(f"logs_odd/{path}/raw"):
            log_path = f"logs_odd/{path}/raw/" + filename
            log_path = (
                "logs_odd/gen1randombattle/raw/battle-gen1randombattle-333760.txt"
            )
            room_id = os.path.splitext(filename)[0]
            client = Client("ws://192.168.1.154:8000/showdown/websocket")
            parser = client.parser

            skipped = False
            with open(log_path, "r", encoding="utf-8") as f:
                for line_number, line in enumerate(f.readlines(), start=1):
                    try:
                        _ = parser.handle_line(
                            line=line,
                            has_log_timestamp=True,
                        )
                    except InvalidActionError, ObsoleteRequestIdError:
                        pass  # The player tried an illegal move, happens
                    except ParserException as exc:
                        print(f"Skipping {filename}: {exc}")
                        skipped = True
                        raise
                    except Exception:
                        raise

            if skipped:
                continue

            _ = parser.finish(player_id="p1")

            raw_counts = Counter(message.command for message in parser.raw_history)
            event_counts = Counter(type(event).__name__ for event in parser.history)

            unhandled_events = [
                event for event in parser.history if isinstance(event, UnhandledEvent)
            ]

            if unhandled_events:
                print()
                print("Unhandled events:", filename)

                for event in unhandled_events:
                    print(
                        f"  command={event.command!r}, "
                        + f"raw={event.raw!r}, "
                        + f"action_id={event.action_id!r}"
                    )

                pprint(parser.history)
                raise RuntimeError(
                    f"Found {len(unhandled_events)} unhandled semantic events"
                )

            if parser.pending_messages:
                raise RuntimeError(
                    f"Found {len(parser.pending_messages)} pending messages"
                )
