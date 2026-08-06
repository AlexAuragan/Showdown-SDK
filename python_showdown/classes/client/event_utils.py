

import json
import re
from dataclasses import dataclass, field
from typing import Sequence

from python_showdown.classes.client.event_classes import (
    AbilityEvent,
    BaseEvent,
    BattleEndEvent,
    CantEvent,
    ClearAllBoostsEvent,
    ClearNegativeBostsEvent,
    DamageEvent,
    DecisionRequestEvent,
    EffectSource,
    FieldActivationEvent,
    FormeChangeEvent,
    HealEvent,
    ItemEvent,
    MajorStatus,
    MajorStatusEvent,
    MinorStatus,
    MinorStatusActivationEvent,
    MinorStatusEvent,
    MoveActivationEvent,
    MoveCopiedEvent,
    MoveEvent,
    MovePrepareEvent,
    PerishCountEvent,
    PokemonIdent,
    PokemonSwitchEvent,
    ProtocolAnnotation,
    ProtocolMessage,
    SetHpEvent,
    SideCondition,
    SideConditionEvent,
    SingleMoveEvent,
    SourceType,
    Stat,
    StatChangeEvent,
    StatSetEvent,
    TeamCureEvent,
    TransformEvent,
    TurnEvent,
    TypeChangeEvent,
    UnhandledEvent,
    Weather,
    WeatherEvent,
)

IGNORED_COMMANDS = {
    "",
    "init",
    "title",
    "room",
    "J",
    "L",
    "pm",
    "t:",
    "gametype",
    "player",
    "gen",
    "tier",
    "rule",
    "teamsize",
    "start",
    "updatesearch",
    "upkeep",
    "-hint",
    "-anim",
    "-message",
    "challstr",
    "message",
    "bigerror",
    "expire", # TODO for client
    "deinit", # TODO for client
    "error", # TODO for bot
    "popup",
    "sentchoice"
}


# A move is complete once the next top-level action or phase starts.
MOVE_BOUNDARY_COMMANDS = {
    "move",
    "cant",
    "switch",
    "drag",
    "replace",
    "turn",
    "upkeep",
    "request",
    "win",
    "tie",
}

ANNOTATION_PATTERN = re.compile(r"^\[(?P<name>[^\]]+)\](?:\s*(?P<value>.*))?$")
LEVEL_PATTERN = re.compile(r"(?:^|,\s*)L(?P<level>\d+)(?:,|$)")


@dataclass
class ProtocolContext:
    active_ability_states: dict[
        tuple[str, str | None],
        set[str],
    ] = field(default_factory=dict)

    known_ability_states: dict[
        tuple[str, str | None],
        set[str],
    ] = field(default_factory=dict)

@dataclass(frozen=True)
class ParsedCondition:
    current_hp: int
    max_hp: int | None
    status: MajorStatus | None


@dataclass
class TargetModifiers:
    # Critical-hit metadata applies to the next damage line for this target.
    next_critical: bool = False
    # Effectiveness normally applies to each hit of the same move.
    effectiveness: float = 1.0


def parse_protocol_message(line: str) -> ProtocolMessage:
    """Normalize one raw Pokémon Showdown protocol line without interpreting it."""

    raw = line.rstrip("\r\n")

    if raw.startswith(">"):
        return ProtocolMessage("room", (raw[1:],), (), raw)

    if not raw.startswith("|"):
        raise ValueError(f"Protocol line must start with '|' or '>': {raw!r}")

    parts = raw.split("|")
    command = parts[1] if len(parts) > 1 else ""
    fields: Sequence[str] = parts[2:] if len(parts) > 2 else ()
    arguments: list[str] = []
    annotations: list[ProtocolAnnotation] = []

    for field in fields:
        match = ANNOTATION_PATTERN.fullmatch(field)
        if match is None:
            arguments.append(field)
            continue

        value = match.group("value") or None
        annotations.append(ProtocolAnnotation(match.group("name"), value))

    return ProtocolMessage(command, tuple(arguments), tuple(annotations), raw)


def annotation_value(message: ProtocolMessage, name: str) -> str | None:
    for annotation in message.annotations:
        if annotation.name == name:
            return annotation.value
    return None


def is_ignored_message(message: ProtocolMessage) -> bool:
    return message.command in IGNORED_COMMANDS


def is_move_boundary(message: ProtocolMessage) -> bool:
    return message.command in MOVE_BOUNDARY_COMMANDS


def parse_pokemon_ident(value: str) -> PokemonIdent:
    try:
        position, name = value.split(": ", 1)
    except ValueError as error:
        raise ValueError(f"Invalid Pokémon identifier: {value!r}") from error

    if len(position) == 2:
        player, slot = position, None
    elif len(position) == 3:
        player, slot = position[:2], position[2]
    else:
        raise ValueError(f"Invalid Pokémon position: {position!r}")

    if player not in {"p1", "p2", "p3", "p4"}:
        raise ValueError(f"Invalid Pokémon player: {player!r}")
    if slot is not None and slot not in {"a", "b", "c"}:
        raise ValueError(f"Invalid active slot: {slot!r}")

    return PokemonIdent(player, slot, name)


def parse_condition(value: str) -> ParsedCondition:
    parts = value.split()
    if not parts:
        raise ValueError("Cannot parse an empty condition")

    hp_text = parts[0]
    status = None
    if len(parts) > 1:
        try:
            status = MajorStatus(parts[1])
        except ValueError as error:
            raise ValueError(f"Unsupported major status in {value!r}") from error

    if "/" in hp_text:
        current_text, max_text = hp_text.split("/", 1)
        current_hp, max_hp = int(current_text), int(max_text)
    else:
        current_hp, max_hp = int(hp_text), None

    if current_hp < 0 or (max_hp is not None and not 0 <= current_hp <= max_hp):
        raise ValueError(f"Invalid HP condition: {value!r}")

    return ParsedCondition(current_hp, max_hp, status)


def parse_level(details: str) -> int | None:
    match = LEVEL_PATTERN.search(details)
    return int(match.group("level")) if match is not None else None


def is_percentage_hp(player_id: str, pokemon: PokemonIdent, condition: ParsedCondition) -> bool:
    return pokemon.player != player_id and (condition.max_hp == 100 or condition.max_hp is None)


def parse_minor_status(value: str) -> MinorStatus:
    normalized = value.removeprefix("move: ").casefold()
    for status in MinorStatus:
        if status.value.casefold() == normalized:
            return status
    raise ValueError(f"Unsupported minor status: {value!r}")


def make_move_source(user: PokemonIdent, move: str, action_id: int) -> EffectSource:
    return EffectSource(SourceType.MOVE, move, user, action_id)


def parse_effect_source(
    message: ProtocolMessage,
    default_source: EffectSource,
    *,
    affected: PokemonIdent | None = None,
) -> EffectSource:
    from_value = annotation_value(message, "from")
    if from_value is None:
        return default_source

    actor_value = annotation_value(message, "of")
    actor = parse_pokemon_ident(actor_value) if actor_value is not None else affected
    normalized = from_value.strip()
    lowered = normalized.casefold()

    if lowered == "recoil":
        return EffectSource(SourceType.RECOIL, default_source.name, default_source.actor, default_source.action_id)
    if lowered.startswith("move: "):
        return EffectSource(SourceType.MOVE, normalized[6:], actor, default_source.action_id)
    if lowered.startswith("item: "):
        return EffectSource(SourceType.ITEM, normalized[6:], actor, default_source.action_id)
    if lowered.startswith("ability: "):
        return EffectSource(SourceType.ABILITY, normalized[9:], actor, default_source.action_id)
    if lowered in {status.value.casefold() for status in MajorStatus}:
        return EffectSource(SourceType.STATUS, lowered, actor, None)
    if lowered in {"sandstorm", "hail", "snow"}:
        return EffectSource(SourceType.WEATHER, normalized, None, None)
    return EffectSource(SourceType.UNKNOWN, normalized, actor, default_source.action_id)


def handle_switch(player_id: str, message: ProtocolMessage) -> PokemonSwitchEvent:
    if message.command not in {"switch", "drag", "replace"}:
        raise ValueError(f"Expected switch-like command, got {message.command!r}")
    _require_arguments(message, 3)
    pokemon = parse_pokemon_ident(message.arguments[0])
    details = message.arguments[1]
    condition = parse_condition(message.arguments[2])
    return PokemonSwitchEvent(
        pokemon=pokemon,
        details=details,
        level=parse_level(details),
        curr_hp=condition.current_hp,
        max_hp=condition.max_hp,
        hp_is_percentage=is_percentage_hp(player_id, pokemon, condition),
        major_status=condition.status,
        command=message.command,
    )


def handle_turn(message: ProtocolMessage) -> TurnEvent:
    _require_arguments(message, 1)
    return TurnEvent(int(message.arguments[0]))


def handle_request(player_id: str, message: ProtocolMessage) -> list[BaseEvent]:
    _require_arguments(message, 1)
    payload = json.loads(message.arguments[0])
    if not isinstance(payload, dict):
        raise ValueError("Request payload must be a JSON object")
    request_id = payload.get("rqid")
    wait = payload.get("wait", False)
    force_switch_raw = payload.get("forceSwitch", [])
    if request_id is not None and not isinstance(request_id, int):
        raise ValueError("rqid must be an integer or None")
    if not isinstance(wait, bool) or not isinstance(force_switch_raw, list):
        raise ValueError("Malformed request payload")
    force_switch = tuple(bool(value) for value in force_switch_raw)
    return [DecisionRequestEvent(player_id, request_id, wait, force_switch, payload)]


def handle_cant(message: ProtocolMessage) -> CantEvent:
    if len(message.arguments) < 2:
        raise ValueError(f"Malformed cant message: {message.raw!r}")
    return CantEvent(
        parse_pokemon_ident(message.arguments[0]),
        message.arguments[1],
        message.arguments[2] if len(message.arguments) > 2 else None,
    )


def handle_battle_end(message: ProtocolMessage) -> BattleEndEvent:
    if message.command == "tie":
        return BattleEndEvent(None)
    if message.command == "win":
        _require_arguments(message, 1)
        return BattleEndEvent(message.arguments[0])
    raise ValueError(f"Not a battle-end message: {message.raw!r}")


def unhandled_event(message: ProtocolMessage, action_id: int | None = None) -> UnhandledEvent:
    return UnhandledEvent(message.command, message.arguments, message.annotations, message.raw, action_id)

def _activation_event(
    message: ProtocolMessage,
) -> BaseEvent:
    if len(message.arguments) not in {2, 3}:
        raise ValueError(
            f"Expected 2 or 3 arguments for '-activate', "
            f"got {len(message.arguments)} in {message.raw!r}"
        )

    pokemon = parse_pokemon_ident(
        message.arguments[0]
    )
    effect = message.arguments[1]

    if effect.casefold().startswith("ability: "):
        ability = effect[9:].strip()

        if not ability:
            raise ValueError(
                f"Empty activated ability in {message.raw!r}"
            )

        context = (
            message.arguments[2]
            if len(message.arguments) == 3
            else None
        )

        return AbilityEvent(
            pokemon=pokemon,
            ability=ability,
            active=True,
            context=context,
            source=EffectSource(
                type=SourceType.ABILITY,
                name=ability,
                actor=pokemon,
                action_id=None,
            ),
        )

    if effect.casefold().startswith("move: "):
        move = effect[6:].strip()

        if not move:
            raise ValueError(
                f"Empty activated move in {message.raw!r}"
            )

        if (
            move.casefold() == "mimic"
            and len(message.arguments) == 3
        ):
            return MoveCopiedEvent(
                source=EffectSource(
                    type=SourceType.MOVE,
                    name="Mimic",
                    actor=pokemon,
                    action_id=None,
                ),
                target=pokemon,
                copied_move=message.arguments[2],
            )

        return MoveActivationEvent(
            pokemon=pokemon,
            move=move,
        )
    if effect.casefold().startswith("item: "):
        item = effect[6:].strip()

        if not item:
            raise ValueError(
                f"Empty activated item in {message.raw!r}"
            )

        return ItemEvent(
            source=EffectSource(
                type=SourceType.ITEM,
                name=item,
                actor=pokemon,
                action_id=None,
            ),
            pokemon=pokemon,
            item=item,
            gained=False,
            consumed=(
                has_annotation(message, "consumed")
                or has_annotation(message, "eat")
            ),
            previous_owner=None,
        )
    if len(message.arguments) != 2:
        return unhandled_event(message)

    try:
        minor_status = parse_minor_status(effect)
    except ValueError:
        return unhandled_event(message)

    return MinorStatusActivationEvent(
        target=pokemon,
        effect=minor_status,
    )

def parse_standalone_effect(
    player_id: str,
    message: ProtocolMessage,
    context: ProtocolContext,
) -> list[BaseEvent]:
    """
    Parse an effect that is not attached to a currently aggregated move.
    """

    unknown_source = EffectSource(
        SourceType.UNKNOWN,
        annotation_value(message, "from"),
    )

    if (
        message.command in {"-start", "-end"}
        and len(message.arguments) >= 2
        and message.arguments[1]
        .casefold()
        .startswith("ability: ")
    ):
        return [
            _ability_event(
                message=message,
                default_source=unknown_source,
            )
        ]

    if is_known_ability_end(
        context,
        message,
    ):
        return [
            _ability_event(
                message=message,
                default_source=unknown_source,
            )
        ]

    if message.command == "-damage":
        return [
            _damage_event(
                player_id,
                message,
                unknown_source,
                {},
                consume_crit=True,
            )
        ]

    if message.command == "-heal":
        return [
            _heal_event(
                player_id,
                message,
                unknown_source,
            )
        ]

    if (
        message.command == "-start"
        and len(message.arguments) >= 2
        and message.arguments[1].casefold() == "typechange"
    ):
        return [
            _type_change_event(
                message=message,
                default_source=unknown_source,
            )
        ]

    if message.command == "-status":
        return [
            _major_status_event(
                message,
                unknown_source,
                True,
            )
        ]

    if message.command == "-curestatus":
        return [
            _major_status_event(
                message,
                unknown_source,
                False,
            )
        ]

    if message.command in {
        "-fieldactivate",
        "-fieldstart",
        "-fieldend",
    }:
        return [
            _field_activation_event(
                message,
                unknown_source,
            )
        ]

    if message.command == "-singlemove":
        return [
            _single_move_event(
                message,
                unknown_source,
            )
        ]

    if message.command == "-ability":
        return [
            _ability_event(
                message=message,
                default_source=unknown_source,
            )
        ]

    if is_known_ability_end(
        context,
        message,
    ):
        return [
            _ability_event(
                message=message,
                default_source=unknown_source,
            )
        ]

    if is_duplicate_silent_ability_end(
        context,
        message,
    ):
        return []

    if message.command == "-clearallboost":
        _require_arguments(message, 0)

        return [
            ClearAllBoostsEvent(
                source=unknown_source,
            )
        ]

    if message.command == "-clearnegativeboost":
        _require_arguments(message, 0)
        return [
            ClearNegativeBostsEvent(
                source=unknown_source
            )
        ]

    if (
        message.command == "-fail"
        and len(message.arguments) >= 2
        and message.arguments[1] in {
            "boost",
            "unboost",
        }
    ):
        return [
            _failed_stat_change_event(
                message=message,
                default_source=unknown_source,
            )
        ]

    if message.command == "-formechange":
        return [
            _forme_change_event(
                message=message,
                default_source=unknown_source,
            )
        ]

    if message.command == "-sethp":
        return [
            _set_hp_event(
                player_id=player_id,
                message=message,
                default_source=unknown_source,
            )
        ]

    if message.command in {"-item", "-enditem"}:
        return [
            _item_event(
                message=message,
                default_source=unknown_source,
                gained=message.command == "-item",
            )
        ]

    if message.command == "-setboost":
        return [
            _stat_set_event(
                message=message,
                source=unknown_source,
            )
        ]

    if message.command in {"-boost", "-unboost"}:
        return [
            _stat_change_event(
                message=message,
                source=unknown_source,
                direction=(
                    1
                    if message.command == "-boost"
                    else -1
                ),
            )
        ]

    if message.command == "-singleturn":
        _require_arguments(message, 2)

        target = parse_pokemon_ident(
            message.arguments[0]
        )

        try:
            effect = parse_minor_status(
                message.arguments[1]
            )
        except ValueError:
            return [unhandled_event(message)]

        return [
            MinorStatusEvent(
                source=unknown_source,
                target=target,
                effect=effect,
                started=True,
            )
        ]


    if (
        message.command == "-start"
        and len(message.arguments) >= 2
        and message.arguments[1]
        .casefold()
        .startswith("perish")
    ):
        return [
            _perish_count_event(
                message,
                unknown_source,
            )
        ]

    if message.command in {"-start", "-end"}:
        try:
            return [
                _minor_status_event(
                    message,
                    unknown_source,
                    message.command == "-start",
                )
            ]
        except ValueError:
            return [unhandled_event(message)]

    if message.command == "-activate":
        return [_activation_event(message)]

    if message.command == "faint":
        _require_arguments(message, 1)

        return [
            MajorStatusEvent(
                unknown_source,
                parse_pokemon_ident(
                    message.arguments[0]
                ),
                MajorStatus.FAINT,
                True,
            )
        ]

    if message.command in {"-sidestart", "-sideend"}:
        return [
            _side_condition_event(
                message=message,
                source=unknown_source,
                started=message.command == "-sidestart",
            )
        ]

    if message.command == "-weather":
        return [
            _weather_event(
                message=message,
                default_source=unknown_source,
            )
        ]

    return [unhandled_event(message)]

def parse_move_group(
    player_id: str,
    action_id: int,
    messages: tuple[ProtocolMessage, ...],
    context: ProtocolContext,
) -> list[BaseEvent]:
    if not messages or messages[0].command != "move":
        raise ValueError(
            "A move group must start with a move message"
        )

    move_message = messages[0]

    if len(move_message.arguments) < 2:
        raise ValueError(
            f"Malformed move message: {move_message.raw!r}"
        )

    user = parse_pokemon_ident(
        move_message.arguments[0]
    )
    move = move_message.arguments[1]

    raw_target = (
        move_message.arguments[2].strip()
        if len(move_message.arguments) > 2
        else ""
    )

    target = (
        parse_pokemon_ident(raw_target)
        if raw_target
        else None
    )

    source = make_move_source(
        user,
        move,
        action_id,
    )

    modifiers: dict[PokemonIdent, TargetModifiers] = {}
    effects: list[BaseEvent] = []

    success = True
    does_hit = True
    failure_reason: str | None = None
    hit_count: int | None = None

    for message in messages[1:]:
        if is_ignored_message(message):
            continue

        command = message.command

        if command in {
            "-crit",
            "-resisted",
            "-supereffective",
        }:
            _require_arguments(message, 1)

            effect_target = parse_pokemon_ident(
                message.arguments[0]
            )

            modifier = modifiers.setdefault(
                effect_target,
                TargetModifiers(),
            )

            if command == "-crit":
                modifier.next_critical = True
            elif command == "-resisted":
                modifier.effectiveness = 0.5
            else:
                modifier.effectiveness = 2.0

            continue

        if command == "-miss":
            does_hit = False
            failure_reason = "miss"
            continue

        if command == "-immune":
            does_hit = False
            failure_reason = "immune"
            continue

        if (
            command == "-fail"
            and len(message.arguments) >= 2
            and message.arguments[1] in {
                "boost",
                "unboost",
            }
        ):
            effects.append(
                _failed_stat_change_event(
                    message=message,
                    default_source=source,
                )
            )
            continue

        if command in {"-fail", "-notarget"}:
            success = False
            does_hit = False
            failure_reason = command.removeprefix("-")
            continue

        if command == "-item":
            effects.append(
                _item_event(
                    message=message,
                    default_source=source,
                    gained=True,
                )
            )
            continue

        if command == "-enditem":
            effects.append(
                _item_event(
                    message=message,
                    default_source=source,
                    gained=False,
                )
            )
            continue

        if command == "-hitcount":
            _require_arguments(message, 2)

            try:
                parsed_hit_count = int(
                    message.arguments[1]
                )
            except ValueError as error:
                raise ValueError(
                    f"Invalid hit count: {message.raw!r}"
                ) from error

            if parsed_hit_count <= 0:
                raise ValueError(
                    f"Hit count must be positive: {message.raw!r}"
                )

            hit_count = parsed_hit_count
            continue

        if command == "-damage":
            effects.append(
                _damage_event(
                    player_id,
                    message,
                    source,
                    modifiers,
                    consume_crit=True,
                )
            )

        elif command == "-heal":
            effects.append(
                _heal_event(
                    player_id,
                    message,
                    source,
                )
            )

        elif command == "-status":
            effects.append(
                _major_status_event(
                    message,
                    source,
                    True,
                )
            )

        elif command == "-weather":
            effects.append(
                _weather_event(
                    message=message,
                    default_source=source,
                )
            )

        elif command == "-curestatus":
            effects.append(
                _major_status_event(
                    message,
                    source,
                    False,
                )
            )

        elif command == "-formechange":
            effects.append(
                _forme_change_event(
                    message=message,
                    default_source=source,
                )
            )

        elif command in {
            "-fieldactivate",
            "-fieldstart",
            "-fieldend",
        }:
            effects.append(
                _field_activation_event(
                    message,
                    source,
                )
            )

        elif command == "-singlemove":
            effects.append(
                _single_move_event(
                    message,
                    source,
                )
            )

        elif (
            command in {"-start", "-end"}
            and len(message.arguments) >= 2
            and message.arguments[1]
            .casefold()
            .startswith("ability: ")
        ) or is_known_ability_end(context, message):

            effects.append(
                _ability_event(
                    message=message,
                    default_source=source,
                )
            )
            continue

        elif is_duplicate_silent_ability_end(
            context,
            message,
        ):
            continue

        elif (
            command == "-start"
            and len(message.arguments) >= 2
            and message.arguments[1]
            .casefold()
            == "typechange"
        ):
            effects.append(
                _type_change_event(
                    message=message,
                    default_source=source,
                )
            )

        elif command == "-sethp":
            effects.append(
                _set_hp_event(
                    player_id=player_id,
                    message=message,
                    default_source=source,
                )
            )

        elif command == "-setboost":
            effects.append(
                _stat_set_event(
                    message=message,
                    source=source,
                )
            )

        elif command == "-ability":
            effects.append(
                _ability_event(
                    message=message,
                    default_source=source,
                )
            )

        elif command == "-clearallboost":
            _require_arguments(message, 0)

            effects.append(
                ClearAllBoostsEvent(
                    source=source,
                )
            )

        elif command == "-clearnegativeboost":
            _require_arguments(message, 0)
            effects.append(ClearNegativeBostsEvent(
                source=source
            ))

        elif command in {"-sidestart", "-sideend"}:
            effects.append(
                _side_condition_event(
                    message=message,
                    source=source,
                    started=command == "-sidestart",
                )
            )

        elif command == "-transform":
            _require_arguments(message, 2)

            effects.append(
                TransformEvent(
                    source=source,
                    pokemon=parse_pokemon_ident(
                        message.arguments[0]
                    ),
                    target=parse_pokemon_ident(
                        message.arguments[1]
                    ),
                )
            )

        elif command == "-activate":
            activation = _activation_event(message)

            if isinstance(activation, UnhandledEvent):
                activation = unhandled_event(
                    message,
                    action_id,
                )

            effects.append(activation)

        elif (
            command == "-start"
            and len(message.arguments) >= 3
            and message.arguments[1]
            .casefold()
            == "mimic"
        ):
            effects.append(
                MoveCopiedEvent(
                    source=source,
                    target=parse_pokemon_ident(
                        message.arguments[0]
                    ),
                    copied_move=message.arguments[2],
                )
            )

        elif (
            command in {"-start", "-end"}
            and len(message.arguments) >= 2
            and message.arguments[1] in {
                condition.value
                for condition in SideCondition
            }
        ):
            effect_target = parse_pokemon_ident(
                message.arguments[0]
            )

            effects.append(
                SideConditionEvent(
                    source=source,
                    side=effect_target.player,
                    condition=SideCondition(
                        message.arguments[1]
                    ),
                    started=command == "-start",
                )
            )

        elif (
            command == "-start"
            and len(message.arguments) >= 2
            and message.arguments[1].casefold().startswith("perish")
        ):
            effects.append(
                _perish_count_event(
                    message,
                    source,
                )
            )
        elif command in {"-start", "-end"}:
            try:
                effects.append(
                    _minor_status_event(
                        message,
                        source,
                        command == "-start",
                    )
                )
            except ValueError:
                effects.append(
                    unhandled_event(
                        message,
                        action_id,
                    )
                )

        elif command in {"-boost", "-unboost"}:
            effects.append(
                _stat_change_event(
                    message,
                    source,
                    1 if command == "-boost" else -1,
                )
            )

        elif command == "-mustrecharge":
            _require_arguments(message, 1)

            effects.append(
                MinorStatusEvent(
                    source,
                    parse_pokemon_ident(
                        message.arguments[0]
                    ),
                    MinorStatus.RECHARGE,
                    True,
                )
            )

        elif command == "faint":
            _require_arguments(message, 1)

            effects.append(
                MajorStatusEvent(
                    source,
                    parse_pokemon_ident(
                        message.arguments[0]
                    ),
                    MajorStatus.FAINT,
                    True,
                )
            )

        elif command == "-prepare":
            _require_arguments(message, 2)

            effects.append(
                MovePrepareEvent(
                    pokemon=parse_pokemon_ident(
                        message.arguments[0]
                    ),
                    move=message.arguments[1],
                )
            )

        elif command == "-cureteam":
            effects.append(
                _team_cure_event(
                    message=message,
                    default_source=source,
                )
            )

        elif command == "-singleturn":
            _require_arguments(message, 2)

            effect_target = parse_pokemon_ident(
                message.arguments[0]
            )

            try:
                effect = parse_minor_status(
                    message.arguments[1]
                )
            except ValueError:
                effects.append(
                    unhandled_event(
                        message,
                        action_id,
                    )
                )
            else:
                effects.append(
                    MinorStatusEvent(
                        source=source,
                        target=effect_target,
                        effect=effect,
                        started=True,
                    )
                )

        else:
            effects.append(
                unhandled_event(
                    message,
                    action_id,
                )
            )

    move_event = MoveEvent(
        action_id=action_id,
        move=move,
        source=user,
        target=target,
        success=success,
        does_hit=does_hit,
        failure_reason=failure_reason,
        hit_count=hit_count,
    )

    return [
        move_event,
        *effects,
    ]

def _damage_event(
    player_id: str,
    message: ProtocolMessage,
    default_source: EffectSource,
    modifiers: dict[PokemonIdent, TargetModifiers],
    *,
    consume_crit: bool,
) -> DamageEvent:
    _require_arguments(message, 2)
    target = parse_pokemon_ident(message.arguments[0])
    condition = parse_condition(message.arguments[1])
    modifier = modifiers.get(target, TargetModifiers())
    critical = modifier.next_critical
    if consume_crit and target in modifiers:
        modifiers[target].next_critical = False
    return DamageEvent(
        parse_effect_source(message, default_source, affected=target),
        target,
        condition.current_hp,
        condition.max_hp,
        is_percentage_hp(player_id, target, condition),
        modifier.effectiveness,
        critical,
    )


def _heal_event(player_id: str, message: ProtocolMessage, default_source: EffectSource) -> HealEvent:
    _require_arguments(message, 2)
    target = parse_pokemon_ident(message.arguments[0])
    condition = parse_condition(message.arguments[1])
    return HealEvent(
        parse_effect_source(message, default_source, affected=target),
        target,
        condition.current_hp,
        condition.max_hp,
        is_percentage_hp(player_id, target, condition),
    )


def _major_status_event(message: ProtocolMessage, source: EffectSource, applied: bool) -> MajorStatusEvent:
    _require_arguments(message, 2)
    return MajorStatusEvent(
        parse_effect_source(message, source, affected=parse_pokemon_ident(message.arguments[0])),
        parse_pokemon_ident(message.arguments[0]),
        MajorStatus(message.arguments[1]),
        applied,
    )


def _minor_status_event(message: ProtocolMessage, source: EffectSource, started: bool) -> MinorStatusEvent:
    _require_arguments(message, 2)
    target = parse_pokemon_ident(message.arguments[0])
    return MinorStatusEvent(
        parse_effect_source(message, source, affected=target),
        target,
        parse_minor_status(message.arguments[1]),
        started,
    )


def _stat_change_event(message: ProtocolMessage, source: EffectSource, direction: int) -> StatChangeEvent:
    _require_arguments(message, 3)
    target = parse_pokemon_ident(message.arguments[0])
    stages = int(message.arguments[2]) * direction
    return StatChangeEvent(
        parse_effect_source(message, source, affected=target),
        target,
        ((Stat(message.arguments[1]), stages),),
    )


def _require_arguments(message: ProtocolMessage, count: int) -> None:
    if len(message.arguments) < count:
        raise ValueError(
            f"Expected {count} arguments for {message.command!r}, "
            f"got {len(message.arguments)} in {message.raw!r}"
        )

def _item_event(
    message: ProtocolMessage,
    default_source: EffectSource,
    gained: bool,
) -> ItemEvent:
    _require_arguments(message, 2)

    pokemon = parse_pokemon_ident(
        message.arguments[0]
    )
    item = message.arguments[1]

    previous_owner_value = annotation_value(
        message,
        "of",
    )

    previous_owner = (
        parse_pokemon_ident(previous_owner_value)
        if previous_owner_value is not None
        else None
    )

    source = parse_effect_source(
        message=message,
        default_source=default_source,
        affected=previous_owner,
    )

    return ItemEvent(
        source=source,
        pokemon=pokemon,
        item=item,
        gained=gained,
        consumed=has_annotation(message, "eat"),
        previous_owner=previous_owner,
    )
def parse_side_ident(value: str) -> str:
    side = value.split(":", 1)[0].strip()

    if side not in {"p1", "p2", "p3", "p4"}:
        raise ValueError(
            f"Invalid side identifier: {value!r}"
        )

    return side


def _side_condition_event(
    message: ProtocolMessage,
    source: EffectSource,
    started: bool,
) -> SideConditionEvent:
    _require_arguments(message, 2)

    return SideConditionEvent(
        source=source,
        side=parse_side_ident(message.arguments[0]),
        condition=SideCondition(message.arguments[1].removeprefix("move: ")),
        started=started,
    )

def _team_cure_event(
    message: ProtocolMessage,
    default_source: EffectSource,
) -> TeamCureEvent:
    _require_arguments(message, 1)

    actor = parse_pokemon_ident(
        message.arguments[0]
    )

    source = parse_effect_source(
        message=message,
        default_source=default_source,
        affected=actor,
    )

    return TeamCureEvent(
        source=source,
        side=actor.player,
        actor=actor,
    )


def _weather_event(
    message: ProtocolMessage,
    default_source: EffectSource,
) -> WeatherEvent:
    _require_arguments(message, 1)

    weather = Weather(
        message.arguments[0]
    )
    upkeep = annotation_value(message, "upkeep") is not None

    return WeatherEvent(
        weather=weather,
        started=weather != Weather.CLEAR_SKY,
        upkeep=upkeep,
        source=parse_effect_source(
            message=message,
            default_source=default_source,
        ),
    )

def has_annotation(
    message: ProtocolMessage,
    name: str,
) -> bool:
    return any(
        annotation.name == name
        for annotation in message.annotations
    )

def _set_hp_event(
    player_id: str,
    message: ProtocolMessage,
    default_source: EffectSource,
) -> SetHpEvent:
    _require_arguments(message, 2)

    target = parse_pokemon_ident(
        message.arguments[0]
    )
    condition = parse_condition(
        message.arguments[1]
    )

    return SetHpEvent(
        source=parse_effect_source(
            message,
            default_source,
            affected=target,
        ),
        target=target,
        curr_hp=condition.current_hp,
        max_hp=condition.max_hp,
        hp_is_percentage=is_percentage_hp(
            player_id,
            target,
            condition,
        ),
    )

def _stat_set_event(
    message: ProtocolMessage,
    source: EffectSource,
) -> StatSetEvent:
    _require_arguments(message, 3)

    target = parse_pokemon_ident(
        message.arguments[0]
    )
    stage = int(message.arguments[2])

    if not -6 <= stage <= 6:
        raise ValueError(
            f"Invalid stat stage in {message.raw!r}"
        )

    return StatSetEvent(
        source=parse_effect_source(
            message,
            source,
            affected=target,
        ),
        target=target,
        stat=Stat(message.arguments[1]),
        stage=stage,
    )

def _perish_count_event(
    message: ProtocolMessage,
    source: EffectSource | None,
) -> PerishCountEvent:
    _require_arguments(message, 2)

    target = parse_pokemon_ident(
        message.arguments[0]
    )
    value = message.arguments[1]

    if not value.casefold().startswith("perish"):
        raise ValueError(
            f"Not a Perish Song countdown: {message.raw!r}"
        )

    count_text = value[6:]

    try:
        count = int(count_text)
    except ValueError as error:
        raise ValueError(
            f"Invalid Perish Song count: {message.raw!r}"
        ) from error

    if count not in {0, 1, 2, 3}:
        raise ValueError(
            f"Unexpected Perish Song count: {count}"
        )

    return PerishCountEvent(
        source=source,
        target=target,
        count=count,
    )

def _field_activation_event(
    message: ProtocolMessage,
    default_source: EffectSource,
) -> FieldActivationEvent:
    _require_arguments(message, 1)

    if message.command not in {
        "-fieldactivate",
        "-fieldstart",
        "-fieldend",
    }:
        raise ValueError(
            f"Unexpected field event command: {message.raw!r}"
        )

    value = message.arguments[0].strip()

    if value.casefold().startswith("move: "):
        effect_type = SourceType.MOVE
        effect_name = value[6:].strip()
    else:
        effect_type = SourceType.UNKNOWN
        effect_name = value

    if not effect_name:
        raise ValueError(
            f"Empty field effect name: {message.raw!r}"
        )

    actor = default_source.actor

    of_value = annotation_value(message, "of")

    if of_value is not None:
        actor = parse_pokemon_ident(of_value)

    return FieldActivationEvent(
        source=EffectSource(
            type=effect_type,
            name=effect_name,
            actor=actor,
            action_id=default_source.action_id,
        ),
        active=message.command != "-fieldend",
    )
def _single_move_event(
    message: ProtocolMessage,
    source: EffectSource | None,
) -> SingleMoveEvent:
    _require_arguments(message, 2)

    return SingleMoveEvent(
        source=source,
        pokemon=parse_pokemon_ident(
            message.arguments[0]
        ),
        move=message.arguments[1],
    )

def _ability_event(
    message: ProtocolMessage,
    default_source: EffectSource,
) -> AbilityEvent:
    if message.command == "-ability":
        if len(message.arguments) not in {2, 3}:
            raise ValueError(
                f"Expected 2 or 3 arguments for '-ability', "
                f"got {len(message.arguments)} in {message.raw!r}"
            )

        pokemon = parse_pokemon_ident(
            message.arguments[0]
        )
        ability = message.arguments[1]
        context = (
            message.arguments[2]
            if len(message.arguments) == 3
            else None
        )
        active = True

    elif message.command in {"-start", "-end"}:
        _require_arguments(message, 2)

        pokemon = parse_pokemon_ident(
            message.arguments[0]
        )
        ability = (
            message.arguments[1]
            .removeprefix("ability: ")
            .strip()
        )
        context = None
        active = message.command == "-start"

    else:
        raise ValueError(
            f"Not an ability message: {message.raw!r}"
        )

    if not ability:
        raise ValueError(
            f"Empty ability in {message.raw!r}"
        )

    source = parse_effect_source(
        message=message,
        default_source=default_source,
        affected=pokemon,
    )

    if source.type == SourceType.UNKNOWN:
        source = EffectSource(
            type=SourceType.ABILITY,
            name=ability,
            actor=pokemon,
            action_id=default_source.action_id,
        )

    return AbilityEvent(
        pokemon=pokemon,
        ability=ability,
        active=active,
        context=context,
        source=source,
    )

def _type_change_event(
    message: ProtocolMessage,
    default_source: EffectSource,
) -> TypeChangeEvent:
    _require_arguments(message, 3)

    target = parse_pokemon_ident(
        message.arguments[0]
    )

    types = tuple(
        type_name.strip()
        for type_name in message.arguments[2].split("/")
        if type_name.strip()
    )

    if not types:
        raise ValueError(
            f"Empty type change in {message.raw!r}"
        )

    return TypeChangeEvent(
        source=parse_effect_source(
            message=message,
            default_source=default_source,
            affected=target,
        ),
        target=target,
        types=types,
    )

def _failed_stat_change_event(
    message: ProtocolMessage,
    default_source: EffectSource,
) -> StatChangeEvent:
    _require_arguments(message, 2)

    target = parse_pokemon_ident(
        message.arguments[0]
    )

    return StatChangeEvent(
        source=parse_effect_source(
            message=message,
            default_source=default_source,
            affected=target,
        ),
        target=target,
        stat_changes=(),
        success=False,
        failure_reason=message.arguments[1],
    )

def _pokemon_key(pokemon: PokemonIdent) -> tuple[str, str | None]:
    return (pokemon.player, pokemon.slot)

def register_ability_start(
    context: ProtocolContext,
    pokemon: PokemonIdent,
    ability: str,
) -> None:
    key = _pokemon_key(pokemon)

    context.known_ability_states.setdefault(
        key,
        set(),
    ).add(ability)

    context.active_ability_states.setdefault(
        key,
        set(),
    ).add(ability)

def is_duplicate_silent_ability_end(
    context: ProtocolContext,
    message: ProtocolMessage,
) -> bool:
    """For some reason, the end ability message can be displayed twice"""
    if (
        message.command != "-end"
        or len(message.arguments) < 2
        or not has_annotation(message, "silent")
    ):
        return False

    pokemon = parse_pokemon_ident(
        message.arguments[0]
    )
    ability = message.arguments[1].strip()
    key = _pokemon_key(pokemon)

    was_seen = ability in context.known_ability_states.get(
        key,
        set(),
    )
    is_still_active = ability in context.active_ability_states.get(
        key,
        set(),
    )

    return was_seen and not is_still_active

def register_ability_end(
    context: ProtocolContext,
    pokemon: PokemonIdent,
    ability: str,
) -> None:
    key = _pokemon_key(pokemon)

    if key not in context.active_ability_states:
        return

    context.active_ability_states[key].discard(ability)

    if not context.active_ability_states[key]:
        del context.active_ability_states[key]


def is_known_ability_end(
    context: ProtocolContext,
    message: ProtocolMessage,
) -> bool:
    if message.command != "-end":
        return False

    if len(message.arguments) < 2:
        return False

    pokemon = parse_pokemon_ident(message.arguments[0])
    ability = message.arguments[1].strip()
    key = _pokemon_key(pokemon)

    return ability in context.active_ability_states.get(key, set())


def update_protocol_context(
    context: ProtocolContext,
    events: tuple[BaseEvent, ...],
) -> None:
    for event in events:
        if not isinstance(event, AbilityEvent):
            continue

        if event.active:
            register_ability_start(
                context,
                event.pokemon,
                event.ability,
            )
        else:
            register_ability_end(
                context,
                event.pokemon,
                event.ability,
            )


def _forme_change_event(
    message: ProtocolMessage,
    default_source: EffectSource,
) -> FormeChangeEvent:
    _require_arguments(message, 2)

    pokemon = parse_pokemon_ident(
        message.arguments[0]
    )

    forme = message.arguments[1].strip()

    if not forme:
        raise ValueError(
            f"Empty forme change in {message.raw!r}"
        )

    return FormeChangeEvent(
        source=parse_effect_source(
            message=message,
            default_source=default_source,
            affected=pokemon,
        ),
        pokemon=pokemon,
        forme=forme,
    )
