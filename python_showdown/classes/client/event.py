import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from python_showdown.classes.client.event_classes import (
    BaseEvent,
    ProtocolMessage,
    UnhandledEvent,
)
from python_showdown.classes.client.event_utils import (
    ProtocolContext,
    handle_battle_end,
    handle_cant,
    handle_request,
    handle_switch,
    handle_turn,
    is_ignored_message,
    is_move_boundary,
    parse_move_group,
    parse_protocol_message,
    parse_standalone_effect,
    unhandled_event,
    update_protocol_context,
)

if TYPE_CHECKING:
    from python_showdown.classes.client.client import Client


@dataclass(frozen=True)
class ParseResult:
    events: tuple[BaseEvent, ...]
    consumed: int

    def __post_init__(self) -> None:
        if self.consumed <= 0:
            raise ValueError("ParseResult must consume at least one message")


class Parser:
    """Aggregate raw protocol messages into complete semantic events."""

    def __init__(self) -> None:
        self.raw_history: list[ProtocolMessage] = []
        self.history: list[BaseEvent] = []
        self.next_unparsed_message = 0
        self.next_action_id = 1
        self.input_finished = False
        self.protocol_context = ProtocolContext()

    @property
    def pending_messages(self) -> tuple[ProtocolMessage, ...]:
        return tuple(self.raw_history[self.next_unparsed_message :])

    def handle_line(
        self,
        client: Client,
        line: str,
        *,
        has_log_timestamp: bool = False,
    ) -> list[BaseEvent]:
        events = self.feed_line(
            player_id=client.battle_player_id,
            line=line,
            has_log_timestamp=has_log_timestamp,
        )
        for event in events:
            event.update_client(client)
        return events

    def feed_line(
        self,
        player_id: str,
        line: str,
        *,
        has_log_timestamp: bool = False,
    ) -> list[BaseEvent]:
        if self.input_finished:
            raise RuntimeError("Cannot feed lines after finish()")
        protocol_line = self._extract_protocol_line(line, has_log_timestamp=has_log_timestamp)
        self.raw_history.append(parse_protocol_message(protocol_line))
        return self._parse_available_events(player_id)

    def finish(self, player_id: str) -> list[BaseEvent]:
        if self.input_finished:
            return []
        self.input_finished = True
        events = self._parse_available_events(player_id)
        if self.next_unparsed_message != len(self.raw_history):
            pending = "\n".join(message.raw for message in self.pending_messages)
            raise RuntimeError(f"Input ended with an incomplete group:\n{pending}")
        return events

    def parse_next(self, player_id: str) -> ParseResult | None:
        start = self.next_unparsed_message
        if start >= len(self.raw_history):
            return None
        message = self.raw_history[start]

        if is_ignored_message(message):
            return ParseResult((), 1)
        if message.command == "move":
            return self._parse_move(player_id, start)
        if message.command in {"switch", "drag", "replace"}:
            return ParseResult((handle_switch(player_id, message),), 1)
        if message.command == "turn":
            return ParseResult((handle_turn(message),), 1)
        if message.command == "request":
            return ParseResult(tuple(handle_request(player_id, message)), 1)
        if message.command == "cant":
            return ParseResult((handle_cant(message),), 1)
        if message.command in {"win", "tie"}:
            return ParseResult((handle_battle_end(message),), 1)
        if message.command.startswith("-") or message.command == "faint":
            return ParseResult(tuple(parse_standalone_effect(player_id, message, self.protocol_context)), 1)

        # Keep parser coverage honest: valid unknown commands are preserved as
        # explicit semantic gaps instead of crashing or being silently ignored.
        return ParseResult((unhandled_event(message),), 1)

    def _parse_available_events(self, player_id: str) -> list[BaseEvent]:
        completed: list[BaseEvent] = []
        while self.next_unparsed_message < len(self.raw_history):
            result = self.parse_next(player_id)
            if result is None:
                break

            self.next_unparsed_message += result.consumed
            self.history.extend(result.events)
            update_protocol_context(
                self.protocol_context,
                result.events,
            )
            completed.extend(result.events)
        return completed



    def _parse_move(self, player_id: str, start: int) -> ParseResult | None:
        end = self._find_move_end(start)
        if end is None:
            return None
        action_id = self.next_action_id
        events = parse_move_group(
            player_id,
            action_id,
            tuple(self.raw_history[start:end]),
            self.protocol_context
        )
        self.next_action_id += 1
        return ParseResult(tuple(events), end - start)

    def _find_move_end(self, start: int) -> int | None:
        for index in range(start + 1, len(self.raw_history)):
            if is_move_boundary(self.raw_history[index]):
                return index
        return len(self.raw_history) if self.input_finished else None

    @staticmethod
    def _extract_protocol_line(line: str, *, has_log_timestamp: bool) -> str:
        line = line.rstrip("\r\n")
        if not has_log_timestamp:
            return line
        parts = line.split(" ", 2)
        if len(parts) != 3:
            raise ValueError(f"Invalid timestamped log line: {line!r}")
        return parts[2]



from collections import Counter

if __name__ == "__main__":
    path = "gen1randombattle"
    for path in ["gen1randombattle", "gen2randombattle", "gen3randombattle", "gen4randombattle"]:

        for file in os.listdir(f"logs/{path}/raw"):
            parser = Parser()
            log_path = f"logs/{path}/raw/" + file


            with open(log_path, "r", encoding="utf-8") as file:
                for line_number, line in enumerate(file, start=1):
                    try:
                        parser.feed_line(
                            player_id="p1",
                            line=line,
                            has_log_timestamp=True,
                        )
                    except Exception:
                        print(
                            f"Failed to parse line {line_number}: "
                            f"{line.rstrip()!r}"
                        )
                        raise

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
            unhandled_events = [
                event
                for event in parser.history
                if isinstance(event, UnhandledEvent)
            ]

            if unhandled_events:
                print()
                print("Unhandled events:", file)

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
