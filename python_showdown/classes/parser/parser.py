"""Pokémon Showdown protocol parser — aggregator entry point.

The parser is an *aggregator*: it does not interpret protocol messages itself.
Instead it routes each incoming :class:`ProtocolMessage` to the scoped
:class:`MessageManager` responsible for it (battle vs. lobby, with room for an
error/choice-retry manager later) and then applies the resulting events onto
the client.

Public entry points:

- :meth:`Parser.handle_line` — live websocket mode (routed to a manager).
- :meth:`Parser.feed_line` / :meth:`Parser.finish` — battle-log replay
  (battle only; lobby messages never appear in a battle log).
"""

import os
from collections import Counter
from typing import TYPE_CHECKING

from python_showdown.classes.parser.managers.base import MessageManager
from python_showdown.classes.parser.managers.battle import BattleParser
from python_showdown.classes.parser.managers.lobby import LobbyParser
from python_showdown.classes.parser.models import ProtocolMessage
from python_showdown.classes.parser.protocol import (
    extract_protocol_line,
    parse_protocol_message,
)

if TYPE_CHECKING:
    from python_showdown.classes.client.client import Client


_LOBBY_COMMANDS = frozenset({"updateuser", "nametaken", "formats", "pm", "room"})


class Parser:
    """Aggregate raw protocol messages by routing them to scoped managers.

    Holds one :class:`BattleParser` and one :class:`LobbyParser` for the
    lifetime of the owning client. Each incoming line is normalized to a
    :class:`ProtocolMessage`, dispatched to the matching manager, and the
    produced events are applied onto the client via ``event.update_client``.
    """

    def __init__(self, client: Client) -> None:
        self.battle = BattleParser(client)
        self.lobby = LobbyParser()
        self.client = client

    @property
    def room_id(self) -> str:
        return self.client.room_id

    def handle_line(
        self,
        line: str,
        *,
        has_log_timestamp: bool = False,
    ) -> list:
        """Live entry point: route one raw line to the appropriate manager.

        Returns the semantic events produced this tick (possibly empty, e.g.
        while a multi-message ``|move|`` group is still being accumulated or
        the message was intentionally ignored).
        """
        protocol_line = extract_protocol_line(line, has_log_timestamp=has_log_timestamp)
        message = parse_protocol_message(protocol_line)
        manager = self._manager_for(message)

        if (
            manager is self.battle
            and message.command != "init"
            and self.client.active_battle_room
            and self.client.room_id != self.client.active_battle_room
        ):
                return []

        events = manager.handle_message(self.client, message)

        for event in events:
            event.update_client(self.client)
        return events

    def _manager_for(self, message: ProtocolMessage) -> MessageManager:
        if message.command in _LOBBY_COMMANDS:
            return self.lobby
        return self.battle


    def finish(self, player_id: str) -> list:
        """Flush any buffered battle events at the end of a replay stream."""
        return self.battle.finish(player_id)

    # -- battle accessors (log-replay / __main__ compatibility) -------------
    # Exposed so the replay harness and any external callers that previously
    # read these off the Parser keep working; they all reflect battle state.

    @property
    def raw_history(self) -> list[ProtocolMessage]:
        return self.battle.raw_history

    @property
    def history(self) -> list:
        return self.battle.history

    @property
    def pending_messages(self) -> tuple[ProtocolMessage, ...]:
        return self.battle.pending_messages

    @property
    def battle_state(self):
        return self.battle.battle_state

    @property
    def player_id(self) -> str:
        return self.battle.player_id


if __name__ == "__main__":
    from python_showdown.classes.client.client import Client
    from python_showdown.classes.parser.exceptions import ParserException
    for path in ["gen1randombattle", "gen2randombattle", "gen3randombattle", "gen4randombattle"]:

        for filename in os.listdir(f"logs/{path}/raw"):
            log_path = f"logs/{path}/raw/" + filename
            room_id = os.path.splitext(filename)[0]
            print(log_path)
            client = Client("ws://192.168.1.154:8000/showdown/websocket")
            parser = client.parser

            skipped = False
            with open(log_path, "r", encoding="utf-8") as f:
                for line_number, line in enumerate(f.readlines(), start=1):
                    try:
                        parser.handle_line(
                            line=line,
                            has_log_timestamp=True,
                        )
                    except ParserException as exc:
                        print(f"Skipping {filename}: {exc}")
                        skipped = True
                        raise
                    except Exception:
                        print(
                            f"Failed to parse line {line_number}: "
                            f"{line.rstrip()!r}"
                        )
                        raise


            if skipped:
                continue

            parser.finish(player_id="p1")

            raw_counts = Counter(
                message.command
                for message in parser.raw_history
            )
            event_counts = Counter(
                type(event).__name__
                for event in parser.history
            )

            print(
                f"Parsed {len(parser.raw_history)} protocol messages "
                f"into {len(parser.history)} semantic events."
            )
            print(f"Pending messages: {len(parser.pending_messages)}")
            from python_showdown.classes.parser.events import UnhandledEvent
            unhandled_events = [
                event
                for event in parser.history
                if isinstance(event, UnhandledEvent)
            ]

            if unhandled_events:
                print()
                print("Unhandled events:", filename)

                for event in unhandled_events:
                    print(
                        f"  command={event.command!r}, "
                        f"raw={event.raw!r}, "
                        f"action_id={event.action_id!r}"
                    )

                raise RuntimeError(
                    f"Found {len(unhandled_events)} unhandled semantic events"
                )

            if parser.pending_messages:
                raise RuntimeError(
                    f"Found {len(parser.pending_messages)} pending messages"
                )
