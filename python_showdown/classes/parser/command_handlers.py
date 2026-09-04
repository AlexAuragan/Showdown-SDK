import json
from collections.abc import Callable

from python_showdown.classes.parser.context import (
    EffectParseContext,
    MoveParseState,
    ProtocolContext,
    TargetModifiers,
)
from python_showdown.classes.parser.effect_handlers import (
    is_failed_stat_change,
    parse_effect_message,
)
from python_showdown.classes.parser.events.base import (
    BaseEvent,
    DiscardedEvent,
    unhandled_event,
)
from python_showdown.classes.parser.events.battle import (
    BattleEndEvent,
    CantEvent,
    CustomShowdownBattleStateEvent,
    DesyncEvent,
    GameGenEvent,
    GameTierEvent,
    GameTypeEvent,
    MoveEvent,
    PlayerEvent,
    PokemonSwitchEvent,
    RoomEvent,
    TurnEvent,
)
from python_showdown.classes.parser.exceptions import (
    InvalidActionError,
    ObsoleteRequestIdError,
)
from python_showdown.classes.parser.fields import (
    is_percentage_hp,
    make_move_source,
    parse_condition,
    parse_level,
    parse_pokemon_ident,
)
from python_showdown.classes.parser.models import EffectSource, ProtocolMessage
from python_showdown.classes.parser.protocol import (
    annotation_value,
    is_ignored_message,
    require_arguments,
)
from python_showdown.models.sdk.battle_state import SourceType

CommandHandler = Callable[[str | None, ProtocolMessage, str], list[BaseEvent]]


def handle_switch(
    player_id: str | None, message: ProtocolMessage, _room_id: str, baton_pass: bool
) -> list[BaseEvent]:
    if player_id is None:
        raise ValueError("Player id not set")
    if message.command not in {"switch", "drag", "replace"}:
        raise ValueError(f"Expected switch-like command, got {message.command!r}")
    require_arguments(message, 3)
    pokemon = parse_pokemon_ident(message.arguments[0])
    details = message.arguments[1]
    condition = parse_condition(message.arguments[2])
    return [
        PokemonSwitchEvent(
            pokemon=pokemon,
            details=details,
            level=parse_level(details),
            curr_hp=condition.current_hp,
            max_hp=condition.max_hp,
            hp_is_percentage=is_percentage_hp(player_id, pokemon, condition),
            major_status=condition.status,
            command=message.command,
            baton_pass=baton_pass
        )
    ]


def handle_turn(
    _player_id: str | None, message: ProtocolMessage, _room_id: str
) -> list[BaseEvent]:
    require_arguments(message, 1)
    return [TurnEvent(int(message.arguments[0]))]


def handle_cant(
    _player_id: str | None, message: ProtocolMessage, _room_id: str
) -> list[BaseEvent]:
    if len(message.arguments) < 2:
        raise ValueError(f"Malformed cant message: {message.raw!r}")

    return [
        CantEvent(
            parse_pokemon_ident(message.arguments[0]),
            message.arguments[1],
            message.arguments[2] if len(message.arguments) > 2 else None,
        )
    ]


def handle_battle_end(
    _player_id: str | None, message: ProtocolMessage, room_id: str
) -> list[BaseEvent]:
    if message.command == "tie":
        return [BattleEndEvent(None, room_id)]
    if message.command == "win":
        require_arguments(message, 1)
        return [BattleEndEvent(message.arguments[0], room_id)]
    raise ValueError(f"Not a battle-end message: {message.raw!r}")


def handle_player(
    _player_id: str | None, message: ProtocolMessage, _room_id: str
) -> list[BaseEvent]:
    """
    |player|p2| <- ignore this one
    |player|p1|BOT5|266|
    |player|p2|BOT6|102|
    |player|p1| <- ignore this one

    """
    require_arguments(message, 2)

    slot = message.arguments[0].strip()
    name = message.arguments[1].strip()
    if not name:
        return []  # ignore messages like '|player|p1|'
    return [PlayerEvent(slot=slot, name=name)]


def handle_error(
    _player_id: str | None, message: ProtocolMessage, _room_id: str
) -> list[BaseEvent]:
    try:
        category = message.annotations[0].name
        content = str(message.annotations[0].value)
    except IndexError:
        print(message)
        raise
    if "too late to make a different move" in content:
        raise ObsoleteRequestIdError()
    raise InvalidActionError(message=content, category=category)


def handle_room(
    _player_id: str | None, message: ProtocolMessage, room_id: str
) -> list[BaseEvent]:
    given_room_id = message.arguments[0].strip() if message.arguments else ""
    if room_id and room_id != given_room_id:
        raise RuntimeError(
            "Got a message room_id meant from another room",
        )
    return [RoomEvent(room_id=given_room_id)]


def handle_gametype(
    _player_id: str | None, message: ProtocolMessage, _room_id: str
) -> list[BaseEvent]:
    return [GameTypeEvent(type=message.arguments[0])]


def handle_gen(
    _player_id: str | None, message: ProtocolMessage, _room_id: str
) -> list[BaseEvent]:
    return [GameGenEvent(gen=int(message.arguments[0]))]


def handle_tier(
    _player_id: str | None, message: ProtocolMessage, _room_id: str
) -> list[BaseEvent]:
    tier = message.annotations[0].value
    if tier is None:
        raise ValueError()
    return [GameTierEvent(tier=tier)]

def handle_custom_showdown_battle_state(
    _player_id: str | None, message: ProtocolMessage, _room_id: str
) -> list[BaseEvent]:
    return [
        CustomShowdownBattleStateEvent(
            content=json.loads(message.raw.strip("|").split("|", 1)[-1])
        )
    ]

COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "switch": handle_switch,
    "drag": handle_switch,
    "replace": handle_switch,
    "turn": handle_turn,
    "cant": handle_cant,
    "win": handle_battle_end,
    "tie": handle_battle_end,
    "player": handle_player,
    "error": handle_error,
    "room": handle_room,
    "gametype": handle_gametype,
    "gen": handle_gen,
    "tier": handle_tier,
    "battlestate": handle_custom_showdown_battle_state
}


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
    source = make_move_source(user, move, action_id)

    state = MoveParseState()
    parse_context = EffectParseContext(
        player_id=player_id,
        source=source,
        protocol_context=context,
        action_id=action_id,

    )
    effects: list[BaseEvent] = []

    for message in messages[1:]:
        if is_ignored_message(message):
            continue

        if (
            message.command == "-hint"
        ):  # outside move control since it can create new events
            effects.extend(handle_hint(message))
            continue

        if _handle_move_control_message(message, state, parse_context):
            continue

        parsed_events = parse_effect_message(message, parse_context)
        if parsed_events is None:
            effects.append(unhandled_event(message, action_id))
        else:
            effects.extend(parsed_events)

    from_ = annotation_value(move_message, "from")

    if from_ is None:
        source = None
    elif ": " not in from_:
        if from_ == "Mirror Move":
            source = EffectSource(type=SourceType.MOVE, name=from_)
        else:
            source = EffectSource(type=SourceType.UNKNOWN, name=from_)
    else:
        source_type, effect_name = from_.strip().split(": ")
        match source_type:
            case "ability":
                source = EffectSource(type=SourceType.ABILITY, name=effect_name)
            case "move":
                source = EffectSource(type=SourceType.MOVE, name=effect_name)
            case _:
                raise ValueError(f"Unkown source from: {from_}")
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
            source=source,
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


def handle_hint(message: ProtocolMessage) -> list[BaseEvent]:
    require_arguments(message, 1)
    hint_message = message.arguments[0].strip()
    if hint_message == "Desync Clause Mod activated!":
        return [DesyncEvent()]
    return [DiscardedEvent(command=message.command, reason="Unhandled hint")]
