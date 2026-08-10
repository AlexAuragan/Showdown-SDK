"""Battle-scope protocol handling.

Aggregates raw battle-room protocol messages into complete semantic events.
A ``|move|`` line opens a group; effect lines decorate it; the next top-level
action or phase boundary flushes it. The running :class:`BattleState` is
updated incrementally as events are produced.

This is the battle manager: it only sees messages the aggregator routes to it
(lobby/global messages such as ``|updateuser|`` never reach it).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from python_showdown.classes.client.battle_manager import BattleManager
from python_showdown.classes.parser.ability_state import (
    ProtocolContext,
    update_protocol_context,
)
from python_showdown.classes.parser.battle_state_handler import BattleStateHandler
from python_showdown.classes.parser.command_handlers import (
    COMMAND_HANDLERS,
    handle_request,
    parse_move_group,
    parse_standalone_effect,
)
from python_showdown.classes.parser.events import (
    BaseEvent,
    BattleStartEvent,
    ProtocolMessage,
    unhandled_event,
)
from python_showdown.classes.parser.managers.base import MessageManager
from python_showdown.classes.parser.protocol import (
    extract_protocol_line,
    is_ignored_message,
    is_move_boundary,
    parse_protocol_message,
)
from python_showdown.models.sdk.battle_state import BattleState

if TYPE_CHECKING:
    from python_showdown.classes.client.client import Client


@dataclass(frozen=True)
class ParseResult:
    events: tuple[BaseEvent, ...]
    consumed: int

    def __post_init__(self) -> None:
        if self.consumed <= 0:
            raise ValueError("ParseResult must consume at least one message")


class BattleParser(MessageManager):
    """Aggregate raw battle protocol messages into complete semantic events."""

    def __init__(self, manager: BattleManager) -> None:
        self.raw_history: list[ProtocolMessage] = []
        self.history: list[BaseEvent] = []
        self.next_unparsed_message = 0
        self.next_action_id = 1
        self.input_finished = False
        self.protocol_context = ProtocolContext()
        self.battle_state_handler = BattleStateHandler()
        self.battle_state: BattleState = self.battle_state_handler.apply_events(manager, [])
        self._manager = manager
        self._last_message_room_id: str = ""

    @property
    def player_id(self) -> str | None:
        return self._manager.player_id

    @player_id.setter
    def player_id(self, value: str) -> None:
        self._manager.player_id = value

    @property
    def last_message_room_id(self) -> str:
        return self._last_message_room_id

    @last_message_room_id.setter
    def room_id(self, value: str) -> None:
        self._last_message_room_id = value

    @property
    def pending_messages(self) -> tuple[ProtocolMessage, ...]:
        return tuple(self.raw_history[self.next_unparsed_message :])

    # -- live client entry point (routed here by the aggregator) -------------

    def handle_message(
        self,
        manager: BattleManager,
        message: ProtocolMessage,
    ) -> list[BaseEvent]:
        return self.feed_message(message)


    # -- log replay / direct feed (battle only) ------------------------------

    def feed_line(
        self,
        player_id: str,
        line: str,
        *,
        has_log_timestamp: bool = False,
    ) -> list[BaseEvent]:
        if self.input_finished:
            raise RuntimeError("Cannot feed lines after finish()")

        if player_id:
            self.player_id = player_id
        protocol_line = extract_protocol_line(line, has_log_timestamp=has_log_timestamp)
        message = parse_protocol_message(protocol_line)
        return self.feed_message(message)

    def feed_message(
        self,
        message: ProtocolMessage,
    ) -> list[BaseEvent]:

        if self.input_finished:
            raise RuntimeError("Cannot feed lines after finish()")
        if message.command == "init" and self.history:
            self.reset()
        if self.player_id is None:
            raise ValueError("player_id not set")

        self.raw_history.append(message)
        if message.command == "request":
            # On request, consume everything, just in case of server error
            request_events = handle_request(self.player_id, message, self._last_message_room_id)
            self.next_unparsed_message = len(self.raw_history)
            self.history.extend(request_events)
            return request_events

        return self._parse_available_events(self.player_id)


    def reset(self) -> None:
        """Discard all accumulated battle state so the parser can drive a new battle.

        Called in live mode when a |init|battle arrives after the previous
        battle ended. Not used in single-room log replay (that raises
        ``BattleReinitializedException`` instead). The client-side battle state
        is reset separately by :class:`BattleStartEvent`.
        """
        self.raw_history = []
        self.history = []
        self.next_unparsed_message = 0
        self.next_action_id = 1
        self.input_finished = False
        self.protocol_context = ProtocolContext()
        self.battle_state_handler = BattleStateHandler()
        self.battle_state = self.battle_state_handler.apply_events(self._client, [])
        self.player_id = ""

    def finish(self, player_id: str) -> list[BaseEvent]:
        if self.input_finished:
            return []
        self.input_finished = True
        if player_id:
            self.player_id = player_id
        events = self._parse_available_events(self.player_id)
        if self.next_unparsed_message != len(self.raw_history):
            pending = "\n".join(message.raw for message in self.pending_messages)
            raise RuntimeError(f"Input ended with an incomplete group:\n{pending}")
        return events

    def parse_next(self, player_id: str) -> ParseResult | None:
        start = self.next_unparsed_message
        if start >= len(self.raw_history):
            return None
        message = self.raw_history[start]

        if message.command == "init":
            return ParseResult((BattleStartEvent(),), 1)

        if is_ignored_message(message):
            return ParseResult((), 1)
        if message.command == "move":
            return self._parse_move(player_id, start)

        handler = COMMAND_HANDLERS.get(message.command)
        if handler is not None:
            return ParseResult(tuple(handler(player_id, message, self._last_message_room_id)), 1)

        if message.command.startswith("-") or message.command == "faint":
            return ParseResult(tuple(parse_standalone_effect(player_id, message, self.protocol_context)), 1)

        # raise ValueError(message.raw)
        return ParseResult((unhandled_event(message),), 1)

    def _parse_available_events(self, player_id: str) -> list[BaseEvent]:
        completed: list[BaseEvent] = []
        while self.next_unparsed_message < len(self.raw_history):
            start = self.next_unparsed_message
            try:
                result = self.parse_next(player_id)
            except Exception:
                self.next_unparsed_message = start + 1
                raise
            if result is None:
                break

            self.next_unparsed_message += result.consumed
            self.history.extend(result.events)
            update_protocol_context(
                self.protocol_context,
                result.events,
            )
            # Apply each newly produced event onto the running battle state.
            for event in result.events:
                self.battle_state_handler.apply_event(self.battle_state, event)
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
