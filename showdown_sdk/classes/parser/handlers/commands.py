"""Simple one-message command handlers.

These handlers consume a single protocol message and never depend on
move-group parsing internals.
"""

import json
from collections.abc import Callable

from showdown_sdk.classes.parser.events.base import BaseEvent
from showdown_sdk.classes.parser.events.battle import (
    BattleEndEvent,
    CantEvent,
    CustomShowdownBattleStateEvent,
    GameGenEvent,
    GameTierEvent,
    GameTypeEvent,
    PlayerEvent,
    PokemonSwitchEvent,
    RoomEvent,
    TurnEvent,
    UpkeepEvent,
)
from showdown_sdk.classes.parser.exceptions import (
    InvalidActionError,
    ObsoleteRequestIdError,
)
from showdown_sdk.classes.parser.fields import (
    is_percentage_hp,
    parse_condition,
    parse_level,
    parse_pokemon_ident,
)
from showdown_sdk.classes.parser.models import ProtocolMessage
from showdown_sdk.classes.parser.protocol import require_arguments

CommandHandler = Callable[[str | None, ProtocolMessage, str], list[BaseEvent]]


## Handlers


def handle_switch(
    player_id: str | None, message: ProtocolMessage, _room_id: str
) -> list[BaseEvent]:
    if player_id is None:
        raise ValueError("Player id not set")
    if message.command not in {"switch", "drag", "replace"}:
        raise ValueError(
            f"Expected switch-like command, got {message.command!r}"
        )
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
        raise RuntimeError("Got a message room_id meant from another room")
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


def handle_upkeep(
    _player_id: str | None, _message: ProtocolMessage, _room_id: str
) -> list[BaseEvent]:
    return [UpkeepEvent()]


## Registry


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
    "battlestate": handle_custom_showdown_battle_state,
    "upkeep": handle_upkeep,
}
