"""Battle-scope protocol handling.

Aggregates raw battle-room protocol messages into complete semantic events.
A ``|move|`` line opens a group; effect lines decorate it; the next top-level
action or phase boundary flushes it. The running :class:`BattleState` is
updated incrementally as events are produced.

This is the battle manager: it only sees messages the aggregator routes to it
(lobby/global messages such as ``|updateuser|`` never reach it).
"""

import json
from dataclasses import dataclass
from typing import cast, override

from python_showdown.classes.combat_handler.battle_manager import BattleManager
from python_showdown.classes.parser.ability_state import update_protocol_context
from python_showdown.classes.parser.battle_state_handler import BattleStateHandler
from python_showdown.classes.parser.command_handlers import (
    COMMAND_HANDLERS,
    handle_room,
    parse_move_group,
    parse_standalone_effect,
)
from python_showdown.classes.parser.context import ProtocolContext
from python_showdown.classes.parser.events import (
    BaseEvent,
    unhandled_event,
)
from python_showdown.classes.parser.events.battle import (
    BattleStartEvent,
    DecisionRequestEvent,
)
from python_showdown.classes.parser.managers.base import MessageParser
from python_showdown.classes.parser.models import (
    ProtocolMessage,
    RequestMove,
    RequestPokemon,
)
from python_showdown.classes.parser.protocol import (
    extract_protocol_line,
    is_ignored_message,
    is_move_boundary,
    parse_protocol_message,
)
from python_showdown.models.sdk.battle_state import BattleState

type Payload = bool | str | int | dict[str, Payload] | list[Payload]


def _expect_object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError(
            f"{name} must be an object, got {type(value).__name__}: {value!r}"
        )

    obj = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in obj):
        raise TypeError(f"{name} must have string keys")

    return cast(dict[str, object], value)


def _expect_array(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(
            f"{name} must be an array, got {type(value).__name__}: {value!r}"
        )

    return cast(list[object], value)


def _expect_str(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, got {type(value).__name__}")
    return value


def _expect_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a bool, got {type(value).__name__}")
    return value


def _expect_int(value: object, name: str) -> int:
    # bool is a subclass of int, but isn't a valid integer here.
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    return value


def _validate_keys(
    value: dict[str, object],
    *,
    allowed: set[str],
    name: str,
    required: set[str] | None = None,
) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"Unhandled {name} keys: {sorted(unknown)}")

    if required is not None:
        missing = required - set(value)
        if missing:
            raise ValueError(f"Missing {name} keys: {sorted(missing)}")


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
        self.battle_state_handler: BattleStateHandler = BattleStateHandler()
        self._manager: BattleManager = manager
        self._last_message_room_id: str = ""

    @property
    def player_id(self) -> str | None:
        return self._manager.player_id

    @player_id.setter
    def player_id(self, value: str) -> None:
        self._manager.player_id = value

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
        if self.player_id is None and message.command not in {"room", "init"}:
            raise ValueError("player_id not set")

        self.raw_history.append(message)
        if message.command == "request":
            request_event = self._parse_request_event(message)
            self.next_unparsed_message = len(self.raw_history)
            self.history.append(request_event)
            return [request_event]

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
        self._manager.battle_state.reset()
        self._manager.player_id = ""

    def finish(self, player_id: str) -> list[BaseEvent]:
        if self.input_finished:
            return []
        self.input_finished = True
        if player_id:
            self.player_id = player_id

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

        if player_id is None:
            raise RuntimeError("player_id not set")

        if is_ignored_message(message):
            return ParseResult((), 1)
        if message.command == "move":
            return self._parse_move(player_id, start)

        handler = COMMAND_HANDLERS.get(message.command)
        if handler is not None:
            return ParseResult(
                tuple(handler(player_id, message, self._last_message_room_id)), 1
            )

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
            self.protocol_context,
        )
        self.next_action_id += 1
        return ParseResult(tuple(events), end - start)

    def _find_move_end(self, start: int) -> int | None:
        for index in range(start + 1, len(self.raw_history)):
            if is_move_boundary(self.raw_history[index]):
                return index
        return len(self.raw_history) if self.input_finished else None

    def _parse_request_event(
        self,
        message: ProtocolMessage,
    ) -> DecisionRequestEvent:
        if message.command != "request":
            raise ValueError(f"Expected request message, got {message.command!r}")

        if not message.arguments:
            raise ValueError("Request message has no JSON payload")

        raw_payload = "|".join(message.arguments)

        try:
            decoded = cast(object, json.loads(raw_payload))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON request payload: {raw_payload!r}") from exc

        data = _expect_object(decoded, "request")

        request_keys = {
            "active",
            "forceSwitch",
            "rqid",
            "side",
            "wait",
            "noCancel",
            "update",
        }
        _validate_keys(
            data,
            allowed=request_keys,
            required={"side"},
            name="request",
        )

        update = _expect_bool(data.get("update", False), "request['update']")
        no_cancel = _expect_bool(data.get("noCancel", False), "request['noCancel']")
        request_id = _expect_int(data.get("rqid", 0), "request['rqid']")
        wait = _expect_bool(data.get("wait", False), "request['wait']")

        raw_force_switch = _expect_array(
            data.get("forceSwitch", []),
            "request['forceSwitch']",
        )
        force_switch = tuple(
            _expect_bool(value, f"request['forceSwitch'][{i}]")
            for i, value in enumerate(raw_force_switch)
        )

        side = _expect_object(data["side"], "request['side']")

        side_keys = {"id", "name", "pokemon"}
        _validate_keys(
            side,
            allowed=side_keys,
            required=side_keys,
            name="request side",
        )

        side_id = _expect_str(side["id"], "request['side']['id']")

        player_id = self.player_id
        if not player_id:
            self.player_id = side_id
            player_id = side_id
        elif side_id != player_id:
            raise ValueError(f"Request player mismatch: {side_id=!r}, {player_id=!r}")

        moves: list[RequestMove] = []
        trapped = False
        maybe_trapped = False

        if "active" in data:
            active = _expect_array(data["active"], "request['active']")

            if len(active) != 1:
                raise ValueError(
                    f"Expected exactly one active Pokémon, got {len(active)}"
                )

            active_request = _expect_object(
                active[0],
                "request['active'][0]",
            )

            active_keys = {"moves", "trapped", "maybeTrapped"}
            _validate_keys(
                active_request,
                allowed=active_keys,
                required={"moves"},
                name="active request",
            )

            trapped = _expect_bool(
                active_request.get("trapped", False),
                "request['active'][0]['trapped']",
            )
            maybe_trapped = _expect_bool(
                active_request.get("maybeTrapped", False),
                "request['active'][0]['maybeTrapped']",
            )

            raw_moves = _expect_array(
                active_request["moves"],
                "request['active'][0]['moves']",
            )

            move_keys = {
                "move",
                "id",
                "pp",
                "maxpp",
                "target",
                "disabled",
            }

            for i, raw_move_value in enumerate(raw_moves):
                raw_move = _expect_object(
                    raw_move_value,
                    f"request['active'][0]['moves'][{i}]",
                )

                _validate_keys(
                    raw_move,
                    allowed=move_keys,
                    required={"move", "id"},
                    name="move",
                )

                name = _expect_str(
                    raw_move["move"],
                    f"move[{i}]['move']",
                )
                move_id = _expect_str(
                    raw_move["id"],
                    f"move[{i}]['id']",
                )
                target = _expect_str(
                    raw_move.get("target", "normal"),
                    f"move[{i}]['target']",
                )

                if len(raw_moves) == 1:
                    curr_pp = _expect_int(
                        raw_move.get("pp", 100),
                        f"move[{i}]['pp']",
                    )
                    max_pp = _expect_int(
                        raw_move.get("maxpp", 100),
                        f"move[{i}]['maxpp']",
                    )
                    disabled = False
                else:
                    missing = {"pp", "maxpp", "disabled"} - set(raw_move)
                    if missing:
                        raise ValueError(
                            f"Move is missing required keys: {sorted(missing)}"
                        )

                    curr_pp = _expect_int(
                        raw_move["pp"],
                        f"move[{i}]['pp']",
                    )
                    max_pp = _expect_int(
                        raw_move["maxpp"],
                        f"move[{i}]['maxpp']",
                    )
                    disabled = _expect_bool(
                        raw_move["disabled"],
                        f"move[{i}]['disabled']",
                    )

                moves.append(
                    RequestMove(
                        name=name,
                        id=move_id,
                        curr_pp=curr_pp,
                        max_pp=max_pp,
                        target=target,
                        disabled=disabled,
                    )
                )

        raw_pokemon = _expect_array(
            side["pokemon"],
            "request['side']['pokemon']",
        )

        if len(raw_pokemon) > 6:
            raise ValueError(
                "Malformed request: expected at most 6 Pokémon, "
                + f"got {len(raw_pokemon)}"
            )

        pokemon: list[RequestPokemon] = []

        pokemon_keys = {
            "condition",
            "ident",
            "stats",
            "details",
            "active",
            "moves",
            "item",
            "pokeball",
            "baseAbility",
        }

        stats_keys = {"atk", "def", "spa", "spd", "spe"}

        for i, raw_value in enumerate(raw_pokemon):
            raw = _expect_object(
                raw_value,
                f"request['side']['pokemon'][{i}]",
            )

            if set(raw) != pokemon_keys:
                missing = pokemon_keys - set(raw)
                unknown = set(raw) - pokemon_keys
                raise ValueError(
                    "Unexpected Pokémon schema: "
                    + f"missing={sorted(missing)}, "
                    + f"unknown={sorted(unknown)}, "
                    + f"pokemon={raw}"
                )

            stats = _expect_object(
                raw["stats"],
                f"request['side']['pokemon'][{i}]['stats']",
            )

            if set(stats) != stats_keys:
                raise ValueError(f"Unexpected stats schema: {stats}")

            atk = _expect_int(stats["atk"], f"pokemon[{i}].stats.atk")
            def_ = _expect_int(stats["def"], f"pokemon[{i}].stats.def")
            spa = _expect_int(stats["spa"], f"pokemon[{i}].stats.spa")
            spd = _expect_int(stats["spd"], f"pokemon[{i}].stats.spd")
            spe = _expect_int(stats["spe"], f"pokemon[{i}].stats.spe")

            condition = _expect_str(
                raw["condition"],
                f"request['side']['pokemon'][{i}]['condition']",
            )

            if condition == "0 fnt":
                curr_hp = 0
                max_hp = None
                status_token = None
            else:
                try:
                    curr_str, rest = condition.split("/", 1)
                    curr_hp = int(curr_str)

                    if " " in rest:
                        max_str, status_token = rest.split(" ", 1)
                        max_hp = int(max_str)
                    else:
                        max_hp = int(rest)
                        status_token = None
                except (ValueError, TypeError) as exc:
                    raise ValueError(
                        f"Invalid Pokémon condition: {condition!r}"
                    ) from exc

            details = _expect_str(
                raw["details"],
                f"request['side']['pokemon'][{i}]['details']",
            )

            clean_details = (
                details.replace(", shiny", "").replace(", M", "").replace(", F", "")
            )

            if ", L" in clean_details:
                try:
                    level = int(clean_details.split(", L", 1)[1])
                except ValueError as exc:
                    raise ValueError(
                        f"Invalid Pokémon level in details: {details!r}"
                    ) from exc
            else:
                level = 100

            raw_pokemon_moves = _expect_array(
                raw["moves"],
                f"request['side']['pokemon'][{i}]['moves']",
            )
            pokemon_moves = tuple(
                _expect_str(
                    move,
                    f"request['side']['pokemon'][{i}]['moves'][{j}]",
                )
                for j, move in enumerate(raw_pokemon_moves)
            )

            ident = _expect_str(
                raw["ident"],
                f"request['side']['pokemon'][{i}]['ident']",
            )
            active = _expect_bool(
                raw["active"],
                f"request['side']['pokemon'][{i}]['active']",
            )
            base_ability = _expect_str(
                raw["baseAbility"],
                f"request['side']['pokemon'][{i}]['baseAbility']",
            )
            item = _expect_str(
                raw["item"],
                f"request['side']['pokemon'][{i}]['item']",
            )
            pokeball = _expect_str(
                raw["pokeball"],
                f"request['side']['pokemon'][{i}]['pokeball']",
            )

            pokemon.append(
                RequestPokemon(
                    ident=ident,
                    details=details,
                    level=level,
                    active=active,
                    atk=atk,
                    def_=def_,
                    spa=spa,
                    spd=spd,
                    spe=spe,
                    moves=pokemon_moves,
                    base_ability=base_ability,
                    item=item,
                    pokeball=pokeball,
                    curr_hp=curr_hp,
                    max_hp=max_hp,
                    status_token=status_token,
                )
            )

        if sum(p.active for p in pokemon) > 1:
            raise ValueError("Request contains more than one active Pokémon")

        return DecisionRequestEvent(
            player_id=player_id,
            request_id=request_id,
            wait=wait,
            trapped=trapped,
            maybe_trapped=maybe_trapped,
            force_switch=force_switch,
            update=update,
            moves=tuple(moves),
            pokemon=tuple(pokemon),
            no_cancel=no_cancel,
        )
