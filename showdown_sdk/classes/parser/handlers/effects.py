from showdown_sdk.classes.parser.context import (
    EffectHandler,
    EffectParseContext,
    EffectRule,
    TargetModifiers,
)
from showdown_sdk.classes.parser.context_updates import (
    is_duplicate_silent_ability_end,
    is_known_ability_end,
)
from showdown_sdk.classes.parser.events.base import (
    BaseEvent,
    DiscardedEvent,
    UnhandledEvent,
    unhandled_event,
)
from showdown_sdk.classes.parser.events.battle import (
    AbilityEvent,
    ClearAllBoostsEvent,
    ClearBoostsEvent,
    ClearNegativeBoostsEvent,
    CopyBoostEvent,
    DamageEvent,
    DetailsChangeEvent,
    FormeChangeEvent,
    HealEvent,
    ItemEvent,
    MajorStatusEvent,
    MinorStatusActivationEvent,
    MinorStatusEvent,
    MoveCopiedEvent,
    MovePrepareEvent,
    PerishCountEvent,
    SetHpEvent,
    SideConditionEvent,
    SingleMoveEvent,
    StatChangeEvent,
    StatSetEvent,
    TeamCureEvent,
    TransformEvent,
    TypeChangeEvent,
    WeatherEvent,
)
from showdown_sdk.classes.parser.fields import (
    is_percentage_hp,
    parse_condition,
    parse_effect_source,
    parse_level,
    parse_minor_status,
    parse_pokemon_ident,
    parse_side_ident,
)
from showdown_sdk.classes.parser.models import EffectSource, ProtocolMessage
from showdown_sdk.classes.parser.protocol import (
    annotation_value,
    has_annotation,
    require_arguments,
)
from showdown_sdk.exceptions import (
    MalformedProtocolError,
    ParserStateError,
    UnsupportedProtocolError,
)
from showdown_sdk.models import dex
from showdown_sdk.models.pokemon import (
    MajorStatus,
    MinorStatus,
    SideCondition,
    Stat,
    Weather,
)
from showdown_sdk.models.sdk import SourceType

MINOR_STATUS_BY_NAME: dict[str, MinorStatus] = {
    status.value.casefold(): status for status in MinorStatus
}
SIDE_CONDITION_VALUES = {condition.value for condition in SideCondition}


## Shared builders (multiple handlers)


def _major_status_event(
    message: ProtocolMessage, source: EffectSource, applied: bool
) -> MajorStatusEvent:
    require_arguments(message, 2)
    target = parse_pokemon_ident(message.arguments[0])
    try:
        major_status = MajorStatus(message.arguments[1])
    except ValueError as error:
        raise UnsupportedProtocolError(
            f"Unsupported major status: {message.arguments[1]!r}",
            raw=message.raw,
            command=message.command,
        ) from error
    return MajorStatusEvent(
        parse_effect_source(message, source, affected=target),
        target,
        major_status,
        applied,
    )


def _ability_event(
    message: ProtocolMessage, default_source: EffectSource
) -> AbilityEvent:
    if message.command == "-ability":
        if len(message.arguments) not in {2, 3}:
            raise MalformedProtocolError(
                "Expected 2 or 3 arguments for '-ability', "
                + f"got {len(message.arguments)} in {message.raw!r}",
                raw=message.raw,
                command=message.command,
            )

        pokemon = parse_pokemon_ident(message.arguments[0])
        ability = message.arguments[1]
        context = message.arguments[2] if len(message.arguments) == 3 else None
        active = True

    elif message.command in {"-start", "-end"}:
        require_arguments(message, 2)

        pokemon = parse_pokemon_ident(message.arguments[0])
        ability = message.arguments[1].removeprefix("ability: ").strip()
        context = None
        active = message.command == "-start"

    else:
        raise MalformedProtocolError(
            f"Not an ability message: {message.raw!r}",
            raw=message.raw,
            command=message.command,
        )

    if not ability:
        raise MalformedProtocolError(
            f"Empty ability in {message.raw!r}",
            raw=message.raw,
            command=message.command,
        )

    source = parse_effect_source(
        message=message, default_source=default_source, affected=pokemon
    )

    if source.type == SourceType.ABILITY:
        # [of] is not guaranteed to be the owner of the source ability.
        # Trace, for example, uses [of] for the Pokémon being copied.
        source = EffectSource(
            type=source.type,
            name=source.name,
            actor=source.actor,
            action_id=source.action_id,
        )

    elif source.type == SourceType.UNKNOWN:
        source = EffectSource(
            type=SourceType.ABILITY,
            actor=pokemon,
            action_id=default_source.action_id,
            owner=pokemon,
        )

    reveals_base = message.command == "-ability" and not has_annotation(
        message, "from"
    )
    return AbilityEvent(
        pokemon=pokemon,
        ability=ability,
        active=active,
        context=context,
        source=source,
        reveals_base=reveals_base,
    )


def _activation_event(
    message: ProtocolMessage, context: EffectParseContext
) -> BaseEvent | list[BaseEvent]:
    if len(message.arguments) not in {2, 3}:
        raise MalformedProtocolError(
            "Expected 2 or 3 arguments for '-activate', "
            + f"got {len(message.arguments)} in {message.raw!r}",
            raw=message.raw,
            command=message.command,
        )

    pokemon = parse_pokemon_ident(message.arguments[0])
    effect = message.arguments[1]

    if effect.casefold().startswith("ability: "):
        ability = effect[9:].strip()

        if not ability:
            raise MalformedProtocolError(
                f"Empty activated ability in {message.raw!r}",
                raw=message.raw,
                command=message.command,
            )

        context_str = (
            message.arguments[2] if len(message.arguments) == 3 else None
        )
        reveals_base = message.command == "-ability" and not has_annotation(
            message, "from"
        )
        return AbilityEvent(
            pokemon=pokemon,
            ability=ability,
            active=True,
            context=context_str,
            source=EffectSource(
                type=SourceType.ABILITY,
                name=ability,
                actor=pokemon,
                action_id=None,
                owner=pokemon,
            ),
            reveals_base=reveals_base,
        )

    if effect.casefold().startswith("move: "):
        move = effect[6:].strip()

        if not move:
            raise MalformedProtocolError(
                f"Empty activated move in {message.raw!r}",
                raw=message.raw,
                command=message.command,
            )

        if move.casefold() == "mimic" and len(message.arguments) == 3:
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

        volatile_status = dex.gen(context.gen).move_volatile_status(move)
        if volatile_status == MinorStatus.PARTIALLY_TRAPPED.value:
            return MinorStatusEvent(
                source=parse_effect_source(
                    message, context.source, affected=pokemon
                ),
                target=pokemon,
                effect=MinorStatus.PARTIALLY_TRAPPED,
                started=True,
            )

        return []

    if effect.casefold().startswith("item: "):
        item = effect[6:].strip()

        if not item:
            raise MalformedProtocolError(
                f"Empty activated item in {message.raw!r}",
                raw=message.raw,
                command=message.command,
            )
        consumed = has_annotation(message, "consumed") or has_annotation(
            message, "eat"
        )

        return ItemEvent(
            source=EffectSource(
                type=SourceType.ITEM,
                name=item,
                actor=pokemon,
                action_id=None,
                owner=pokemon,
            ),
            pokemon=pokemon,
            item=item,
            gained=not consumed,
            consumed=consumed,
            previous_owner=None,
        )
    if len(message.arguments) != 2:
        return unhandled_event(message)

    try:
        minor_status = parse_minor_status(effect)
    except UnsupportedProtocolError:
        return unhandled_event(message)

    return MinorStatusActivationEvent(
        source=context.source, target=pokemon, effect=minor_status
    )


def _minor_status_or_none(value: str) -> MinorStatus | None:
    normalized = value.removeprefix("move: ").casefold()
    return MINOR_STATUS_BY_NAME.get(normalized)


## Effect handlers


def parse_effect_message(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent] | None:
    """Convert one protocol effect message into semantic events.

    ``None`` means the command does not belong to the shared effect layer.
    An empty list means it was recognized and deliberately emitted no event.
    """
    if context.player_id is None:
        raise ParserStateError("Player id not set", state="player_id")

    for rule in SPECIAL_EFFECT_RULES.get(message.command, ()):
        if rule.predicate(message, context):
            return rule.handler(message, context)

    handler = SIMPLE_EFFECT_HANDLERS.get(message.command)
    if handler is None:
        return None
    try:
        return handler(message, context)
    except Exception:
        print(message, context)
        raise


def _parse_damage(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    if context.player_id is None:
        raise ParserStateError("Player id not set", state="player_id")
    require_arguments(message, 2)
    target = parse_pokemon_ident(message.arguments[0])
    condition = parse_condition(message.arguments[1])
    modifier = context.modifiers.get(target, TargetModifiers())
    critical = modifier.next_critical
    if target in context.modifiers:
        context.modifiers[target].next_critical = False
    return [
        DamageEvent(
            parse_effect_source(message, context.source, affected=target),
            target,
            condition.current_hp,
            condition.max_hp,
            is_percentage_hp(context.player_id, target, condition),
            modifier.effectiveness,
            critical,
        )
    ]


def _parse_heal(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    if context.player_id is None:
        raise ParserStateError("Player id not set", state="player_id")
    require_arguments(message, 2)
    target = parse_pokemon_ident(message.arguments[0])
    condition = parse_condition(message.arguments[1])
    return [
        HealEvent(
            parse_effect_source(message, context.source, affected=target),
            target,
            condition.current_hp,
            condition.max_hp,
            is_percentage_hp(context.player_id, target, condition),
        )
    ]


def _parse_status(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    return [_major_status_event(message, context.source, applied=True)]


def _parse_cure_status(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    return [_major_status_event(message, context.source, applied=False)]


def _parse_weather(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 1)
    try:
        weather = Weather(message.arguments[0])
    except ValueError as error:
        raise UnsupportedProtocolError(
            f"Unsupported weather: {message.arguments[0]!r}",
            raw=message.raw,
            command=message.command,
        ) from error
    upkeep = has_annotation(message, "upkeep")
    started = weather != Weather.CLEAR_SKY
    source = parse_effect_source(
        message=message,
        default_source=context.source,
        inherit_default=started and not upkeep,
    )

    return [
        WeatherEvent(
            weather=weather, started=started, upkeep=upkeep, source=source
        )
    ]


def _parse_forme_change(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 2)
    pokemon = parse_pokemon_ident(message.arguments[0])
    forme = message.arguments[1].strip()
    if not forme:
        raise MalformedProtocolError(
            f"Empty forme change in {message.raw!r}",
            raw=message.raw,
            command=message.command,
        )
    return [
        FormeChangeEvent(
            source=parse_effect_source(
                message=message, default_source=context.source, affected=pokemon
            ),
            pokemon=pokemon,
            forme=forme,
        )
    ]


def _parse_field(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 1)
    if message.command not in {"-fieldactivate", "-fieldstart", "-fieldend"}:
        raise MalformedProtocolError(
            f"Unexpected field event command: {message.raw!r}",
            raw=message.raw,
            command=message.command,
        )
    value = message.arguments[0].strip()
    if value.casefold().startswith("move: "):
        effect_type = SourceType.MOVE
        effect_name = value.removeprefix("move: ").strip()
    else:
        effect_type = SourceType.UNKNOWN
        effect_name = value
    if not effect_name:
        raise MalformedProtocolError(
            f"Empty field effect name: {message.raw!r}",
            raw=message.raw,
            command=message.command,
        )
    actor = context.source.actor
    of_value = annotation_value(message, "of")
    if of_value is not None:
        actor = parse_pokemon_ident(of_value)

    if effect_type == SourceType.MOVE and effect_name == "Perish Song":
        return []  # The perish song "field event" ends up being a per Pokemon minor status
        # These status are given via the following lines.

    if effect_type == SourceType.MOVE and effect_name == "Trick Room":
        return [
            SideConditionEvent(
                source=EffectSource(
                    type=effect_type,
                    name=effect_name,
                    actor=actor,
                    action_id=context.source.action_id,
                ),
                side=None,
                condition=SideCondition.TRICK_ROOM,
                started=message.command == "-fieldstart",
            )
        ]

    return [
        UnhandledEvent.from_message(message, action_id=context.source.action_id)
    ]


def _parse_single_move(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 2)
    return [
        SingleMoveEvent(
            source=context.source,
            pokemon=parse_pokemon_ident(message.arguments[0]),
            move=message.arguments[1],
        )
    ]


def _parse_explicit_ability(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    return [_ability_event(message, context.source)]


def _parse_set_hp(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    if context.player_id is None:
        raise ParserStateError("Player id not set", state="player_id")
    require_arguments(message, 2)
    target = parse_pokemon_ident(message.arguments[0])
    condition = parse_condition(message.arguments[1])
    return [
        SetHpEvent(
            source=parse_effect_source(
                message, context.source, affected=target
            ),
            target=target,
            curr_hp=condition.current_hp,
            max_hp=condition.max_hp,
            hp_is_percentage=is_percentage_hp(
                context.player_id, target, condition
            ),
        )
    ]


def _parse_set_boost(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 3)
    target = parse_pokemon_ident(message.arguments[0])
    try:
        stage = int(message.arguments[2])
    except ValueError as error:
        raise MalformedProtocolError(
            f"Invalid stat stage in {message.raw!r}",
            raw=message.raw,
            command=message.command,
        ) from error
    if not -6 <= stage <= 6:
        raise MalformedProtocolError(
            f"Invalid stat stage in {message.raw!r}",
            raw=message.raw,
            command=message.command,
        )
    try:
        stat = Stat(message.arguments[1])
    except ValueError as error:
        raise UnsupportedProtocolError(
            f"Unsupported stat: {message.arguments[1]!r}",
            raw=message.raw,
            command=message.command,
        ) from error
    return [
        StatSetEvent(
            source=parse_effect_source(
                message, context.source, affected=target
            ),
            target=target,
            stat=stat,
            stage=stage,
        )
    ]


def _parse_clear_boosts(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 1)

    target = parse_pokemon_ident(message.arguments[0])

    return [
        ClearBoostsEvent(
            source=parse_effect_source(
                message, context.source, affected=target
            ),
            target=target,
        )
    ]


def _parse_clear_all_boosts(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 0)

    return [ClearAllBoostsEvent(source=context.source)]


def _parse_clear_negative_boosts(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 1)
    target = parse_pokemon_ident(message.arguments[0])
    return [ClearNegativeBoostsEvent(source=context.source, target=target)]


def _parse_item(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 2)
    pokemon_str = message.arguments[0].strip()
    pokemon = parse_pokemon_ident(pokemon_str) if pokemon_str else None
    item = message.arguments[1]
    previous_owner_value = annotation_value(message, "of")
    previous_owner = (
        parse_pokemon_ident(previous_owner_value)
        if previous_owner_value is not None
        else None
    )
    source = parse_effect_source(
        message=message, default_source=context.source, affected=previous_owner
    )
    return [
        ItemEvent(
            source=source,
            pokemon=pokemon,
            item=item,
            gained=message.command == "-item",
            consumed=has_annotation(message, "eat"),
            previous_owner=previous_owner,
        )
    ]


def _parse_stat_change(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 3)
    target = parse_pokemon_ident(message.arguments[0])
    direction = 1 if message.command == "-boost" else -1
    try:
        stages = int(message.arguments[2]) * direction
    except ValueError as error:
        raise MalformedProtocolError(
            f"Invalid stat stage in {message.raw!r}",
            raw=message.raw,
            command=message.command,
        ) from error
    try:
        stat = Stat(message.arguments[1])
    except ValueError as error:
        raise UnsupportedProtocolError(
            f"Unsupported stat: {message.arguments[1]!r}",
            raw=message.raw,
            command=message.command,
        ) from error
    events: list[BaseEvent] = [
        StatChangeEvent(
            parse_effect_source(message, context.source, affected=target),
            target,
            [(stat, stages)],
        )
    ]
    return events


def _parse_side_condition(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 2)
    started = message.command == "-sidestart"
    source = parse_effect_source(
        message, context.source, inherit_default=started
    )
    condition_name = message.arguments[1].removeprefix("move: ")
    try:
        condition = SideCondition(condition_name)
    except ValueError as error:
        raise UnsupportedProtocolError(
            f"Unsupported side condition: {condition_name!r}",
            raw=message.raw,
            command=message.command,
        ) from error
    return [
        SideConditionEvent(
            source=source,
            side=parse_side_ident(message.arguments[0]),
            condition=condition,
            started=started,
        )
    ]


def _parse_activation(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    events = _activation_event(message, context)
    if not isinstance(events, list):
        events = [events]
    for event in events:
        if isinstance(event, UnhandledEvent):
            unhandled_event(message, context.action_id)
    return events


def _parse_single_turn(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 2)
    status = _minor_status_or_none(message.arguments[1])
    if status is None:
        return [unhandled_event(message, context.action_id)]
    return [
        MinorStatusEvent(
            source=context.source,
            target=parse_pokemon_ident(message.arguments[0]),
            effect=status,
            started=True,
        )
    ]


def _parse_must_recharge(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 1)
    return [
        MinorStatusEvent(
            context.source,
            parse_pokemon_ident(message.arguments[0]),
            MinorStatus.RECHARGE,
            True,
        )
    ]


def _parse_transform(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 2)
    return [
        TransformEvent(
            source=context.source,
            pokemon=parse_pokemon_ident(message.arguments[0]),
            target=parse_pokemon_ident(message.arguments[1]),
        )
    ]


def _parse_prepare(
    message: ProtocolMessage, _context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 2)
    return [
        MovePrepareEvent(
            pokemon=parse_pokemon_ident(message.arguments[0]),
            move=message.arguments[1],
        )
    ]


def _parse_team_cure(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 1)
    actor = parse_pokemon_ident(message.arguments[0])
    source = parse_effect_source(
        message=message, default_source=context.source, affected=actor
    )
    return [TeamCureEvent(source=source, side=actor.player, actor=actor)]


def _parse_faint(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 1)
    return [
        MajorStatusEvent(
            context.source,
            parse_pokemon_ident(message.arguments[0]),
            MajorStatus.FAINT,
            True,
        )
    ]


def _parse_details_change(
    message: ProtocolMessage, _context: EffectParseContext
) -> list[BaseEvent]:
    if len(message.arguments) not in {2, 3}:
        raise MalformedProtocolError(
            "Expected 2 or 3 arguments for 'detailschange', "
            + f"got {len(message.arguments)} in {message.raw!r}",
            raw=message.raw,
            command=message.command,
        )

    pokemon = parse_pokemon_ident(message.arguments[0])
    details = message.arguments[1].strip()

    if not details:
        raise MalformedProtocolError(
            f"Empty details change in {message.raw!r}",
            raw=message.raw,
            command=message.command,
        )

    level = parse_level(details)
    return [DetailsChangeEvent(pokemon=pokemon, details=details, level=level)]


# --- Special-rule predicates and handlers (-start / -end / -fail) ----------


## Special rules


def is_failed_stat_change(
    message: ProtocolMessage, _context: EffectParseContext
) -> bool:
    return len(message.arguments) >= 2 and message.arguments[1] in {
        "boost",
        "unboost",
    }


def _parse_failed_stat_change(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 2)
    target = parse_pokemon_ident(message.arguments[0])
    return [
        StatChangeEvent(
            source=parse_effect_source(
                message=message, default_source=context.source, affected=target
            ),
            target=target,
            stat_changes=[],
            success=False,
            failure_reason=message.arguments[1],
        )
    ]


def _is_ability_start_or_end(
    message: ProtocolMessage, context: EffectParseContext
) -> bool:
    return (
        len(message.arguments) >= 2
        and message.arguments[1].casefold().startswith("ability: ")
    ) or is_known_ability_end(context.protocol_context, message)


def _parse_ability_start_or_end(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    ability_event = _ability_event(message, context.source)

    ability_name = message.arguments[1].removeprefix("ability: ").strip()

    minor_status = _minor_status_or_none(ability_name)

    if minor_status is None:
        return [ability_event]

    return [
        ability_event,
        MinorStatusEvent(
            source=context.source,
            target=parse_pokemon_ident(message.arguments[0]),
            effect=minor_status,
            started=message.command == "-start",
        ),
    ]


def _is_duplicate_ability_end(
    message: ProtocolMessage, context: EffectParseContext
) -> bool:
    return is_duplicate_silent_ability_end(context.protocol_context, message)


def _discard_effect(
    _message: ProtocolMessage, _context: EffectParseContext
) -> list[BaseEvent]:
    return []


def _has_effect_name(message: ProtocolMessage, name: str) -> bool:
    return (
        len(message.arguments) >= 2
        and message.arguments[1].casefold() == name.casefold()
    )


def _is_type_change(
    message: ProtocolMessage, _context: EffectParseContext
) -> bool:
    return _has_effect_name(message, "typechange")


def _parse_type_change(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 3)
    target = parse_pokemon_ident(message.arguments[0])
    types = tuple(
        type_name.strip()
        for type_name in message.arguments[2].split("/")
        if type_name.strip()
    )
    if not types:
        raise MalformedProtocolError(
            f"Empty type change in {message.raw!r}",
            raw=message.raw,
            command=message.command,
        )
    return [
        TypeChangeEvent(
            source=parse_effect_source(
                message=message, default_source=context.source, affected=target
            ),
            target=target,
            types=types,
        )
    ]


def _is_perish_count(
    message: ProtocolMessage, _context: EffectParseContext
) -> bool:
    return len(message.arguments) >= 2 and message.arguments[
        1
    ].casefold().startswith("perish")


def _parse_perish_count(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 2)
    target = parse_pokemon_ident(message.arguments[0])
    value = message.arguments[1]
    if not value.casefold().startswith("perish"):
        raise MalformedProtocolError(
            f"Not a Perish Song countdown: {message.raw!r}",
            raw=message.raw,
            command=message.command,
        )
    count_text = value[6:]
    try:
        count = int(count_text)
    except ValueError as error:
        raise MalformedProtocolError(
            f"Invalid Perish Song count: {message.raw!r}",
            raw=message.raw,
            command=message.command,
        ) from error
    if count not in {0, 1, 2, 3}:
        raise MalformedProtocolError(
            f"Unexpected Perish Song count: {count}",
            raw=message.raw,
            command=message.command,
        )
    return [PerishCountEvent(source=context.source, target=target, count=count)]


def _parse_copyboost(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    user = parse_pokemon_ident(message.arguments[0])
    target = parse_pokemon_ident(message.arguments[1])
    source = parse_effect_source(
        message,
        default_source=EffectSource(
            SourceType.MOVE, "copyboost", user, action_id=context.action_id
        ),
    )

    return [CopyBoostEvent(user=user, target=target, source=source)]


def _is_mimic_copy(
    message: ProtocolMessage, _context: EffectParseContext
) -> bool:
    return len(message.arguments) >= 3 and _has_effect_name(message, "mimic")


def _parse_mimic_copy(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    return [
        MoveCopiedEvent(
            source=context.source,
            target=parse_pokemon_ident(message.arguments[0]),
            copied_move=message.arguments[2],
        )
    ]


def _is_volatile_side_condition(
    message: ProtocolMessage, _context: EffectParseContext
) -> bool:
    return (
        len(message.arguments) >= 2
        and message.arguments[1] in SIDE_CONDITION_VALUES
    )


def _parse_volatile_side_condition(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    target = parse_pokemon_ident(message.arguments[0])
    started = message.command == "-start"
    source = parse_effect_source(
        message,
        default_source=context.source,
        inherit_default=started,
        affected=target,
    )
    condition_name = message.arguments[1]

    try:
        condition = SideCondition(condition_name)
    except ValueError as error:
        raise UnsupportedProtocolError(
            f"Unsupported side condition: {condition_name!r}",
            raw=message.raw,
            command=message.command,
        ) from error

    # gens override
    if context.gen == 1:
        if condition_name.casefold() == "reflect":
            return [
                MinorStatusEvent(
                    source=source,
                    target=target,
                    effect=MinorStatus.REFLECT,
                    started=started,
                )
            ]

        if condition_name.casefold() == "light screen":
            return [
                MinorStatusEvent(
                    source=source,
                    target=target,
                    effect=MinorStatus.LIGHT_SCREEN,
                    started=started,
                )
            ]

    return [
        SideConditionEvent(
            source=source,
            side=target.player,
            condition=condition,
            started=started,
        )
    ]


def _is_minor_status(
    message: ProtocolMessage, _context: EffectParseContext
) -> bool:
    return (
        len(message.arguments) >= 2
        and _minor_status_or_none(message.arguments[1]) is not None
    )


def _parse_minor_status(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 2)
    target = parse_pokemon_ident(message.arguments[0])
    started = message.command == "-start"
    return [
        MinorStatusEvent(
            parse_effect_source(
                message,
                context.source,
                affected=target,
                inherit_default=started,
            ),
            target,
            parse_minor_status(message.arguments[1]),
            started,
        )
    ]


def _parse_unknown_start_end(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    return [unhandled_event(message, context.action_id)]


def _always(_message: ProtocolMessage, _context: EffectParseContext) -> bool:
    return True


def _parse_hint(
    message: ProtocolMessage, _context: EffectParseContext
) -> list[BaseEvent]:
    if message.arguments[0] in [
        (
            "In Gen 1, if a Pokemon with a Substitute hurts itself due to"
            + " confusion or Jump Kick/Hi Jump Kick recoil and the target does not have a "
            + "Substitute there is no damage dealt."
        ),
        (
            "In Gen 1, if a Pokemon with a Substitute hurts itself due to "
            + "confusion or Jump Kick/Hi Jump Kick recoil and the target has a "
            + "Substitute, the target's Substitute takes the damage."
        ),
        (
            "In Gen 2, Toxic's counter is retained through Baton Pass/Heal Bell and applies to PSN/BRN."
        ),
        ("If you want to tie earlier, consider using `/offertie`."),
        (
            "In Gen 3, Intimidate does not activate if every target has a Substitute."
        ),
        (
            "In Gen 4, Intimidate does not activate if every target has a Substitute (or the Substitute was just broken by U-turn)."
        ),
        (
            "Sleep Clause Mod prevents players from putting more than one of their opponent's Pokémon to sleep at a time"
        ),
        (
            "In Gen 1, if a Pokémon spends a turn partially trapped and switches to a Pokémon that is asleep, the sleep"
            + " counter will not decrease until you select a move with a different Pokémon."
        ),
        (
            "In Gen 2, a stat will roll over to a small number if it is larger than 1024."
        ),
    ]:
        return [
            DiscardedEvent(
                command=message.command,
                reason="Unhandled hint: " + message.arguments[0],
            )
        ]
    raise UnsupportedProtocolError(message.arguments[0])
    # return [UnhandledEvent.from_message(message)]


def _minor_status_end_or_none(message: ProtocolMessage) -> MinorStatus | None:
    if message.command != "-end" or len(message.arguments) < 2:
        return None

    status = _minor_status_or_none(message.arguments[1])
    if status is not None:
        return status

    for annotation in message.annotations:
        status = MINOR_STATUS_BY_NAME.get(annotation.name.casefold())
        if status is not None:
            return status

    return None


def _is_minor_status_end(
    message: ProtocolMessage, _context: EffectParseContext
) -> bool:
    return _minor_status_end_or_none(message) is not None


def _parse_minor_status_end(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    target = parse_pokemon_ident(message.arguments[0])
    status = _minor_status_end_or_none(message)

    if status is None:
        raise MalformedProtocolError(
            f"Not a minor status end: {message.raw!r}",
            raw=message.raw,
            command=message.command,
        )

    return [
        MinorStatusEvent(
            source=parse_effect_source(
                message, context.source, affected=target
            ),
            target=target,
            effect=status,
            started=False,
        )
    ]


## Registries


SIMPLE_EFFECT_HANDLERS: dict[str, EffectHandler] = {
    "-damage": _parse_damage,
    "-heal": _parse_heal,
    "-status": _parse_status,
    "-curestatus": _parse_cure_status,
    "-weather": _parse_weather,
    "-formechange": _parse_forme_change,
    "-fieldactivate": _parse_field,
    "-fieldstart": _parse_field,
    "-fieldend": _parse_field,
    "-singlemove": _parse_single_move,
    "-ability": _parse_explicit_ability,
    "-sethp": _parse_set_hp,
    "-setboost": _parse_set_boost,
    "-clearboost": _parse_clear_boosts,
    "-clearallboost": _parse_clear_all_boosts,
    "-clearnegativeboost": _parse_clear_negative_boosts,
    "-item": _parse_item,
    "-enditem": _parse_item,
    "-boost": _parse_stat_change,
    "-unboost": _parse_stat_change,
    "-sidestart": _parse_side_condition,
    "-sideend": _parse_side_condition,
    "-activate": _parse_activation,
    "-singleturn": _parse_single_turn,
    "-mustrecharge": _parse_must_recharge,
    "-transform": _parse_transform,
    "-prepare": _parse_prepare,
    "-cureteam": _parse_team_cure,
    "faint": _parse_faint,
    "-hint": _parse_hint,
    "-copyboost": _parse_copyboost,
    "detailschange": _parse_details_change,
}

SPECIAL_EFFECT_RULES: dict[str, tuple[EffectRule, ...]] = {
    "-fail": (EffectRule(is_failed_stat_change, _parse_failed_stat_change),),
    "-start": (
        EffectRule(_is_ability_start_or_end, _parse_ability_start_or_end),
        EffectRule(_is_type_change, _parse_type_change),
        EffectRule(_is_perish_count, _parse_perish_count),
        EffectRule(_is_mimic_copy, _parse_mimic_copy),
        EffectRule(_is_volatile_side_condition, _parse_volatile_side_condition),
        EffectRule(_is_minor_status, _parse_minor_status),
        EffectRule(_always, _parse_unknown_start_end),
    ),
    "-end": (
        EffectRule(_is_ability_start_or_end, _parse_ability_start_or_end),
        EffectRule(_is_duplicate_ability_end, _discard_effect),
        EffectRule(_is_volatile_side_condition, _parse_volatile_side_condition),
        EffectRule(_is_minor_status_end, _parse_minor_status_end),
        EffectRule(_always, _parse_unknown_start_end),
    ),
}
