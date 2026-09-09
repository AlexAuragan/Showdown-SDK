"""Battle-scope protocol handling.

Aggregates raw battle-room protocol messages into complete semantic events.
A ``|move|`` line opens a group; effect lines decorate it; the next top-level
action or phase boundary flushes it. The running :class:`BattleState` is
updated incrementally as events are produced.

This is the battle manager: it only sees messages the aggregator routes to it
(lobby/global messages such as ``|updateuser|`` never reach it).
"""

from dataclasses import dataclass, replace
from typing import override

from python_showdown.classes.combat_handler.battle_manager import BattleManager
from python_showdown.classes.parser.context import ProtocolContext
from python_showdown.classes.parser.context_updates import update_protocol_context
from python_showdown.classes.parser.events import (
    BaseEvent,
    unhandled_event,
)
from python_showdown.classes.parser.events.battle import (
    BattleStartEvent,
    CustomShowdownBattleStateEvent,
    PokemonSwitchEvent,
)
from python_showdown.classes.parser.handlers.commands import (
    COMMAND_HANDLERS,
    handle_room,
)
from python_showdown.classes.parser.handlers.moves import (
    parse_move_group,
    parse_standalone_effect,
)
from python_showdown.classes.parser.handlers.requests import parse_request_event
from python_showdown.classes.parser.managers.base import MessageParser
from python_showdown.classes.parser.models import (
    ProtocolMessage,
)
from python_showdown.classes.parser.protocol import (
    extract_protocol_line,
    is_ignored_message,
    is_move_boundary,
    parse_protocol_message,
)
from python_showdown.classes.parser.reducers import reduce_battle_state
from python_showdown.models.sdk.battle_state import BattleState


@dataclass(frozen=True)
class ParseResult:
    events: tuple[BaseEvent, ...]
    consumed: int

    def __post_init__(self) -> None:
        if self.consumed <= 0:
            raise ValueError("ParseResult must consume at least one message")


class BattleParser(MessageParser):
    """Aggregate raw battle protocol messages into complete semantic events."""

    def __init__(self, manager: BattleManager) -> None:
        self.raw_history: list[ProtocolMessage] = []
        self.history: list[BaseEvent] = []
        self.next_unparsed_message: int = 0
        self.next_action_id: int = 1
        self.input_finished: bool = False
        self.protocol_context: ProtocolContext = ProtocolContext()
        self._manager: BattleManager = manager
        self._last_message_room_id: str = ""

    @property
    def gen(self) -> int:
        gen = self.protocol_context.gen
        if gen is None:
            raise ValueError("gen was accessed before getting initialized")
        return gen

    @property
    def player_id(self) -> str | None:
        return self._manager.player_id

    @property
    def battle_state(self) -> BattleState:
        return self._manager.battle_state

    @property
    def last_message_room_id(self) -> str:
        return self._last_message_room_id

    @last_message_room_id.setter
    def last_message_room_id(self, value: str) -> None:
        self._last_message_room_id = value

    @property
    def pending_messages(self) -> tuple[ProtocolMessage, ...]:
        return tuple(self.raw_history[self.next_unparsed_message :])

    @override
    def handle_message(
        self,
        manager: BattleManager,
        message: ProtocolMessage,
    ) -> list[BaseEvent]:
        return self.feed_message(message)

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
            self.battle_state.player_id = player_id
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
            room_id = self._last_message_room_id
            self.reset()
            self._last_message_room_id = room_id

        self.raw_history.append(message)

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
        # self._last_message_room_id = ""

    def finish(self, player_id: str) -> list[BaseEvent]:
        if self.input_finished:
            return []
        self.input_finished = True
        if player_id:
            self.battle_state.player_id = player_id

        if self.player_id is None:
            raise RuntimeError("player_id not set")

        events = self._parse_available_events(self.player_id)
        if self.next_unparsed_message != len(self.raw_history):
            pending = "\n".join(message.raw for message in self.pending_messages)
            raise RuntimeError(f"Input ended with an incomplete group:\n{pending}")
        return events

    def parse_next(self, player_id: str | None) -> ParseResult | None:
        start = self.next_unparsed_message
        if start >= len(self.raw_history):
            return None
        message = self.raw_history[start]

        # Routed before for client setup
        if message.command == "init":
            return ParseResult((BattleStartEvent(self.last_message_room_id),), 1)
        if message.command == "room":
            return ParseResult(
                tuple(handle_room(player_id, message, self._last_message_room_id)), 1
            )
        if is_ignored_message(message):
            return ParseResult((), 1)
        if message.command == "move":
            return self._parse_move(player_id, start)
        if message.command == "request":
            if player_id is None:
                raise ValueError("player_id not set")
            return ParseResult(
                (parse_request_event(message, player_id=player_id),),
                1,
            )

        handler = COMMAND_HANDLERS.get(message.command)
        if handler is not None:
            events = tuple(
                handler(
                    player_id,
                    message,
                    self._last_message_room_id,
                )
            )

            if message.command == "switch":
                events = tuple(
                    replace(
                        event,
                        baton_pass=(
                            event.pokemon.player
                            in self.protocol_context.baton_pass_pending
                        ),
                    )
                    if isinstance(event, PokemonSwitchEvent)
                    else event
                    for event in events
                )
            return ParseResult(events, 1)

        if message.command.startswith("-") or message.command == "faint":
            return ParseResult(
                tuple(
                    parse_standalone_effect(player_id, message, self.protocol_context)
                ),
                1,
            )

        # raise ValueError(message.raw)
        return ParseResult((unhandled_event(message),), 1)

    def _parse_available_events(self, player_id: str | None) -> list[BaseEvent]:
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
            self.history.extend(
                event
                for event in result.events
                if not isinstance(event, CustomShowdownBattleStateEvent)
            )
            update_protocol_context(
                self.protocol_context,
                result.events,
            )
            # Apply each newly produced event onto the running battle state.
            for event in result.events:
                reduce_battle_state(self.battle_state, event)
            completed.extend(result.events)

        return completed

    def _parse_move(self, player_id: str | None, start: int) -> ParseResult | None:
        end = self._find_move_end(start)
        if end is None:
            return None
        action_id = self.next_action_id
        events = parse_move_group(
            player_id,
            action_id,
            tuple(self.raw_history[start:end]),
            self.protocol_context,
        )
        self.next_action_id += 1
        return ParseResult(tuple(events), end - start)

    def _find_move_end(self, start: int) -> int | None:
        for index in range(start + 1, len(self.raw_history)):
            if is_move_boundary(self.raw_history[index]):
                return index
        return len(self.raw_history) if self.input_finished else None
