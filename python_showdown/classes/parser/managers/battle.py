"""Battle-scope protocol handling.

Aggregates raw battle-room protocol messages into complete semantic events.
A ``|move|`` line opens a group; effect lines decorate it; the next top-level
action or phase boundary flushes it. The running :class:`BattleState` is
updated incrementally as events are produced.

This is the battle manager: it only sees messages the aggregator routes to it
(lobby/global messages such as ``|updateuser|`` never reach it).
"""

import json
from dataclasses import dataclass, replace
from typing import override

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
    PokemonSwitchEvent,
    TeamPreviewRequestEvent,
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
from python_showdown.models.pokemon.status import MajorStatus
from python_showdown.models.sdk.battle_state import BattleState
from python_showdown.utils.serialization import (
    SerializableObject,
    expect_array,
    expect_bool,
    expect_int,
    expect_object,
    expect_optional_int,
    expect_string,
)

type Payload = bool | str | int | dict[str, Payload] | list[Payload]

MULTI_TURN_MOVES = {
    "fight",
    "skyattack",
    "solarbeam",
    "rollout",
    "outrage",
    "clamp",
    "firespin",
    "bind",
    "wrap",
    "dig",
}


def _validate_keys(
    value: SerializableObject,
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
    def gen(self) -> int:
        gen = self.protocol_context.gen
        if gen is None:
            raise ValueError("gen was accessed before getting initialized")
        return gen

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
            room_id = self._last_message_room_id
            self.reset()
            self._last_message_room_id = room_id

        self.raw_history.append(message)
        # if message.command == "request":
        #    request_event = self._parse_request_event(message)
        #    self.next_unparsed_message = len(self.raw_history)
        #    self.history.append(request_event)
        #    return [request_event]

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
        # self._last_message_room_id = ""

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
        if is_ignored_message(message):
            return ParseResult((), 1)
        if message.command == "move":
            return self._parse_move(player_id, start)
        if message.command == "request":
            return ParseResult((self._parse_request_event(message),), 1)

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

    def _parse_request_pokemon(
        self,
        data: SerializableObject,
    ) -> tuple[RequestPokemon, ...]:
        side = expect_object(data["side"], name="request['side']")

        side_keys = {"id", "name", "pokemon"}
        _validate_keys(
            side,
            allowed=side_keys,
            required=side_keys,
            name="request side",
        )

        side_id = expect_string(side["id"], name="request['side']['id']")

        if side_id != self.player_id:
            raise ValueError(
                f"Request player mismatch: {side_id=!r}, {self.player_id=!r}"
            )

        raw_pokemon = expect_array(
            side["pokemon"],
            name="request['side']['pokemon']",
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
            raw = expect_object(
                raw_value,
                name=f"request['side']['pokemon'][{i}]",
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

            stats = expect_object(
                raw["stats"],
                name=f"request['side']['pokemon'][{i}]['stats']",
            )

            if set(stats) != stats_keys:
                raise ValueError(f"Unexpected stats schema: {stats}")

            atk = expect_int(stats["atk"], name=f"pokemon[{i}].stats.atk")
            def_ = expect_int(stats["def"], name=f"pokemon[{i}].stats.def")
            spa = expect_int(stats["spa"], name=f"pokemon[{i}].stats.spa")
            spd = expect_int(stats["spd"], name=f"pokemon[{i}].stats.spd")
            spe = expect_int(stats["spe"], name=f"pokemon[{i}].stats.spe")

            condition = expect_string(
                raw["condition"],
                name=f"request['side']['pokemon'][{i}]['condition']",
            )

            if condition == "0 fnt":
                curr_hp = 0
                max_hp = None
                major_status = MajorStatus.FAINT
            else:
                try:
                    curr_str, rest = condition.split("/", 1)
                    curr_hp = int(curr_str)

                    if " " in rest:
                        max_str, major_status_str = rest.split(" ", 1)
                        max_hp = int(max_str)
                        major_status = MajorStatus(major_status_str)
                    else:
                        max_hp = int(rest)
                        major_status = None
                except (ValueError, TypeError) as exc:
                    raise ValueError(
                        f"Invalid Pokémon condition: {condition!r}"
                    ) from exc

            details = expect_string(
                raw["details"],
                name=f"request['side']['pokemon'][{i}]['details']",
            )

            clean_details = (
                details
                .replace(", shiny", "")
                .replace(", M", "")
                .replace(", F", "")
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

            raw_pokemon_moves = expect_array(
                raw["moves"],
                name=f"request['side']['pokemon'][{i}]['moves']",
            )

            pokemon_moves = tuple(
                expect_string(
                    move,
                    name=f"request['side']['pokemon'][{i}]['moves'][{j}]",
                )
                for j, move in enumerate(raw_pokemon_moves)
            )

            ident = expect_string(
                raw["ident"],
                name=f"request['side']['pokemon'][{i}]['ident']",
            )

            active = expect_bool(
                raw["active"],
                name=f"request['side']['pokemon'][{i}]['active']",
            )

            base_ability = expect_string(
                raw["baseAbility"],
                name=f"request['side']['pokemon'][{i}]['baseAbility']",
            )

            item = expect_string(
                raw["item"],
                name=f"request['side']['pokemon'][{i}]['item']",
            )

            pokeball = expect_string(
                raw["pokeball"],
                name=f"request['side']['pokemon'][{i}]['pokeball']",
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
                    major_status=major_status,
                )
            )

        if sum(p.active for p in pokemon) > 1:
            raise ValueError("Request contains more than one active Pokémon")

        return tuple(pokemon)

    def _parse_request_team_preview_event(
        self,
        data: SerializableObject,
    ) -> TeamPreviewRequestEvent:
        request_keys = {
            "teamPreview",
            "maxChosenTeamSize",
            "rqid",
            "side",
            "noCancel",
        }

        _validate_keys(
            data,
            allowed=request_keys,
            required={"teamPreview", "side"},
            name="team preview request",
        )

        team_preview = expect_bool(
            data["teamPreview"],
            name="request['teamPreview']",
        )

        if not team_preview:
            raise ValueError(
                "Team preview request has teamPreview=false"
            )

        raw_request_id = data.get("rqid")
        request_id = expect_optional_int(raw_request_id, name="request['rqid']")


        raw_max_chosen_team_size = data.get("maxChosenTeamSize")
        max_chosen_team_size = expect_optional_int(
                raw_max_chosen_team_size,
                name="request['maxChosenTeamSize']",

        )

        no_cancel = expect_bool(
            data.get("noCancel", False),
            name="request['noCancel']",
        )

        pokemon = self._parse_request_pokemon(data)

        if self.player_id is None:
            raise ValueError("self.player_id must not be None")

        if (
            max_chosen_team_size is not None
            and max_chosen_team_size > len(pokemon)
        ):
            raise ValueError(
                "maxChosenTeamSize cannot exceed the number of Pokémon: "
                + f"{max_chosen_team_size=} {len(pokemon)=}"
            )

        return TeamPreviewRequestEvent(
            player_id=self.player_id,
            request_id=request_id,
            pokemon=pokemon,
            max_chosen_team_size=max_chosen_team_size,
            no_cancel=no_cancel,
        )

    def _parse_request_event(
        self,
        message: ProtocolMessage,
    ) -> DecisionRequestEvent | TeamPreviewRequestEvent:
        if message.command != "request":
            raise ValueError(f"Expected request message, got {message.command!r}")

        if not message.arguments:
            raise ValueError("Request message has no JSON payload")

        raw_payload = "|".join(message.arguments)

        try:
            decoded = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON request payload: {raw_payload!r}"
            ) from exc

        data = expect_object(decoded, name="request")

        # Team Preview is a different kind of decision request.
        if "teamPreview" in data:
            return self._parse_request_team_preview_event(data)

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

        update = expect_bool(
            data.get("update", False),
            name="request['update']",
        )
        no_cancel = expect_bool(
            data.get("noCancel", False),
            name="request['noCancel']",
        )
        request_id = expect_int(
            data.get("rqid", 0),
            name="request['rqid']",
        )
        wait = expect_bool(
            data.get("wait", False),
            name="request['wait']",
        )

        raw_force_switch = expect_array(
            data.get("forceSwitch", []),
            name="request['forceSwitch']",
        )

        force_switch = tuple(
            expect_bool(
                value,
                name=f"request['forceSwitch'][{i}]",
            )
            for i, value in enumerate(raw_force_switch)
        )

        moves: list[RequestMove] = []

        trapped = False
        maybe_trapped = False
        maybe_locked = False
        maybe_disabled = False

        if "active" in data:
            active = expect_array(
                data["active"],
                name="request['active']",
            )

            if len(active) != 1:
                raise ValueError(
                    f"Expected exactly one active Pokémon, got {len(active)}"
                )

            active_request = expect_object(
                active[0],
                name="request['active'][0]",
            )

            active_keys = {
                "moves",
                "trapped",
                "maybeTrapped",
                "maybeLocked",
                "maybeDisabled",
            }

            _validate_keys(
                active_request,
                allowed=active_keys,
                required={"moves"},
                name="active request",
            )

            trapped = expect_bool(
                active_request.get("trapped", False),
                name="request['active'][0]['trapped']",
            )

            maybe_trapped = expect_bool(
                active_request.get("maybeTrapped", False),
                name="request['active'][0]['maybeTrapped']",
            )

            maybe_disabled = expect_bool(
                active_request.get("maybeDisabled", False),
                name="request['active'][0]['maybeDisabled']",
            )

            maybe_locked = expect_bool(
                active_request.get("maybeLocked", False),
                name="request['active'][0]['maybeLocked']",
            )

            raw_moves = expect_array(
                active_request["moves"],
                name="request['active'][0]['moves']",
            )

            move_keys = {
                "move",
                "id",
                "pp",
                "maxpp",
                "target",
                "disabled",
                "disabledSource",
            }

            for i, raw_move_value in enumerate(raw_moves):
                raw_move = expect_object(
                    raw_move_value,
                    name=f"request['active'][0]['moves'][{i}]",
                )

                _validate_keys(
                    raw_move,
                    allowed=move_keys,
                    required={"move", "id"},
                    name="move",
                )

                name = expect_string(
                    raw_move["move"],
                    name=f"move[{i}]['move']",
                )

                move_id = expect_string(
                    raw_move["id"],
                    name=f"move[{i}]['id']",
                )

                target = expect_string(
                    raw_move.get("target", "normal"),
                    name=f"move[{i}]['target']",
                )

                raw_disabled_source = raw_move.get(
                    "disabledSource",
                    "",
                )
                disabled_source = (
                    expect_string(
                        raw_disabled_source,
                        name=f"move[{i}]['disabledSource']",
                    )
                    or None
                )

                # Mostly phase 2 of two-turn moves, recharge,
                # Struggle, and other multi-turn moves.
                whitelist_no_pp = {
                    "recharge",
                    "struggle",
                }.union(MULTI_TURN_MOVES)

                if move_id in whitelist_no_pp:
                    curr_pp_value = raw_move.get("pp")
                    max_pp_value = raw_move.get("maxpp")
                    disabled_value = raw_move.get("disabled", False)

                    curr_pp = (
                        None
                        if curr_pp_value is None
                        else expect_int(
                            curr_pp_value,
                            name=f"move[{i}]['pp']",
                        )
                    )

                    max_pp = expect_optional_int(
                            max_pp_value,
                            name=f"move[{i}]['maxpp']",
                    )

                    disabled = expect_bool(
                        disabled_value,
                        name=f"move[{i}]['disabled']",
                    )

                    if move_id in {"recharge", "struggle"}:
                        target = None

                else:
                    missing = {
                        "pp",
                        "maxpp",
                        "disabled",
                    } - set(raw_move)

                    if missing:
                        raise ValueError(
                            "Move is missing required keys: "
                            + f"{sorted(missing)}, move: {raw_move}"
                        )

                    curr_pp = expect_int(
                        raw_move["pp"],
                        name=f"move[{i}]['pp']",
                    )

                    max_pp = expect_int(
                        raw_move["maxpp"],
                        name=f"move[{i}]['maxpp']",
                    )

                    disabled = expect_bool(
                        raw_move["disabled"],
                        name=f"move[{i}]['disabled']",
                    )

                moves.append(
                    RequestMove(
                        name=name,
                        id=move_id,
                        curr_pp=curr_pp,
                        max_pp=max_pp,
                        target=target,
                        disabled=disabled,
                        disabled_source=disabled_source,
                    )
                )

        pokemon = self._parse_request_pokemon(data)

        if self.player_id is None:
            raise ValueError("self.player_id must not be None")

        return DecisionRequestEvent(
            player_id=self.player_id,
            request_id=request_id,
            wait=wait,
            trapped=trapped,
            maybe_trapped=maybe_trapped,
            maybe_locked=maybe_locked,
            maybe_disabled=maybe_disabled,
            force_switch=force_switch,
            update=update,
            moves=tuple(moves),
            pokemon=pokemon,
            no_cancel=no_cancel,
        )
