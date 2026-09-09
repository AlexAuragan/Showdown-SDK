"""Move-group parsing.

A ``|move|`` line opens a group; effect lines decorate it. This module owns
the aggregation of those messages into a single :class:`MoveEvent` plus its
effect events. It may depend on ``handlers/effects.py``.
"""

from python_showdown.classes.parser.context import (
    EffectParseContext,
    MoveParseState,
    ProtocolContext,
    TargetModifiers,
)
from python_showdown.classes.parser.events.base import (
    BaseEvent,
    DiscardedEvent,
    unhandled_event,
)
from python_showdown.classes.parser.events.battle import (
    DesyncEvent,
    MoveEvent,
)
from python_showdown.classes.parser.fields import (
    make_move_source,
    parse_move_origin,
    parse_pokemon_ident,
)
from python_showdown.classes.parser.handlers.effects import (
    is_failed_stat_change,
    parse_effect_message,
)
from python_showdown.classes.parser.models import EffectSource, ProtocolMessage
from python_showdown.classes.parser.protocol import (
    annotation_value,
    is_ignored_message,
    require_arguments,
)
from python_showdown.models.sdk.battle_state import SourceType


def handle_hint(message: ProtocolMessage) -> list[BaseEvent]:
    require_arguments(message, 1)
    hint_message = message.arguments[0].strip()
    if hint_message == "Desync Clause Mod activated!":
        return [DesyncEvent()]
    return [DiscardedEvent(command=message.command, reason="Unhandled hint")]


def parse_standalone_effect(
    player_id: str | None,
    message: ProtocolMessage,
    context: ProtocolContext,
) -> list[BaseEvent]:
    """Parse one semantic effect outside a currently aggregated move."""

    parse_context = EffectParseContext(
        player_id=player_id,
        source=EffectSource(
            SourceType.UNKNOWN,
            annotation_value(message, "from"),
        ),
        protocol_context=context,
    )
    events = parse_effect_message(message, parse_context)
    if events is not None:
        return events
    return [unhandled_event(message)]


def parse_move_group(
    player_id: str | None,
    action_id: int,
    messages: tuple[ProtocolMessage, ...],
    context: ProtocolContext,
) -> list[BaseEvent]:
    if not messages or messages[0].command != "move":
        raise ValueError("A move group must start with a move message")

    move_message = messages[0]

    if len(move_message.arguments) < 2:
        raise ValueError(f"Malformed move message: {move_message.raw!r}")

    user = parse_pokemon_ident(move_message.arguments[0])
    move = move_message.arguments[1]

    raw_target = (
        move_message.arguments[2].strip() if len(move_message.arguments) > 2 else ""
    )
    target = parse_pokemon_ident(raw_target) if raw_target else None

    action_source = make_move_source(
        user,
        move,
        action_id,
    )

    state = MoveParseState()

    parse_context = EffectParseContext(
        player_id=player_id,
        source=action_source,
        protocol_context=context,
        action_id=action_id,
    )

    effects: list[BaseEvent] = []

    for message in messages[1:]:
        if is_ignored_message(message):
            continue

        if message.command == "-hint":
            effects.extend(handle_hint(message))
            continue

        if _handle_move_control_message(
            message,
            state,
            parse_context,
        ):
            continue

        parsed_events = parse_effect_message(
            message,
            parse_context,
        )

        if parsed_events is None:
            effects.append(
                unhandled_event(
                    message,
                    action_id,
                )
            )
        else:
            effects.extend(parsed_events)

    origin = parse_move_origin(move_message)

    return [
        MoveEvent(
            action_id=action_id,
            move=move,
            source_pokemon=user,
            target_pokemon=target,
            success=state.success,
            does_hit=state.does_hit,
            failure_reason=state.failure_reason,
            hit_count=state.hit_count,
            source=origin,
        ),
        *effects,
    ]


def _handle_move_control_message(
    message: ProtocolMessage,
    state: MoveParseState,
    context: EffectParseContext,
) -> bool:
    """Handle messages that mutate the enclosing MoveEvent rather than emit events."""

    command = message.command

    if command in {"-crit", "-resisted", "-supereffective"}:
        require_arguments(message, 1)
        target = parse_pokemon_ident(message.arguments[0])
        modifier = context.modifiers.setdefault(target, TargetModifiers())
        if command == "-crit":
            modifier.next_critical = True
        elif command == "-resisted":
            modifier.effectiveness = 0.5
        else:
            modifier.effectiveness = 2.0
        return True

    if command == "-miss":
        state.does_hit = False
        state.failure_reason = "miss"
        return True

    if command == "-immune":
        state.does_hit = False
        state.failure_reason = "immune"
        return True

    if command == "-fail" and is_failed_stat_change(message, context):
        return False

    if command in {"-fail", "-notarget"}:
        state.success = False
        state.does_hit = False
        state.failure_reason = command.removeprefix("-")
        return True

    if command == "-hitcount":
        require_arguments(message, 2)
        try:
            hit_count = int(message.arguments[1])
        except ValueError as error:
            raise ValueError(f"Invalid hit count: {message.raw!r}") from error
        if hit_count <= 0:
            raise ValueError(f"Hit count must be positive: {message.raw!r}")
        state.hit_count = hit_count
        return True

    return False
