from python_showdown.classes.parser.ability_state import (
    is_duplicate_silent_ability_end,
    is_known_ability_end,
)
from python_showdown.classes.parser.context import (
    EffectHandler,
    EffectParseContext,
    EffectRule,
    TargetModifiers,
)
from python_showdown.classes.parser.events.base import (
    BaseEvent,
    DiscardedEvent,
    UnhandledEvent,
    unhandled_event,
)
from python_showdown.classes.parser.events.battle import (
    AbilityEvent,
    ClearAllBoostsEvent,
    ClearNegativeBostsEvent,
    DamageEvent,
    FormeChangeEvent,
    HealEvent,
    ItemEvent,
    MajorStatusEvent,
    MinorStatusActivationEvent,
    MinorStatusEvent,
    MoveActivationEvent,
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
from python_showdown.classes.parser.fields import (
    is_percentage_hp,
    parse_condition,
    parse_effect_source,
    parse_minor_status,
    parse_pokemon_ident,
    parse_side_ident,
)
from python_showdown.classes.parser.models import (
    EffectSource,
    ProtocolMessage,
)
from python_showdown.classes.parser.protocol import (
    annotation_value,
    has_annotation,
    require_arguments,
)
from python_showdown.models.pokemon.status import MajorStatus, MinorStatus, Stat
from python_showdown.models.pokemon.terrain import SideCondition, Weather
from python_showdown.models.sdk.battle_state import SourceType

MINOR_STATUS_BY_NAME: dict[str, MinorStatus] = {
    status.value.casefold(): status for status in MinorStatus
}
SIDE_CONDITION_VALUES = {condition.value for condition in SideCondition}


# --- Shared builders (multiple handlers) -----------------------------------


def _major_status_event(
    message: ProtocolMessage,
    source: EffectSource,
    applied: bool,
) -> MajorStatusEvent:
    require_arguments(message, 2)
    target = parse_pokemon_ident(message.arguments[0])
    return MajorStatusEvent(
        parse_effect_source(message, source, affected=target),
        target,
        MajorStatus(message.arguments[1]),
        applied,
    )


def _ability_event(
    message: ProtocolMessage,
    default_source: EffectSource,
) -> AbilityEvent:
    if message.command == "-ability":
        if len(message.arguments) not in {2, 3}:
            raise ValueError(
                "Expected 2 or 3 arguments for '-ability', "
                + f"got {len(message.arguments)} in {message.raw!r}"
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
        raise ValueError(f"Not an ability message: {message.raw!r}")

    if not ability:
        raise ValueError(f"Empty ability in {message.raw!r}")

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


def _activation_event(
    message: ProtocolMessage,
) -> BaseEvent:
    if len(message.arguments) not in {2, 3}:
        raise ValueError(
            "Expected 2 or 3 arguments for '-activate', "
            + f"got {len(message.arguments)} in {message.raw!r}"
        )

    pokemon = parse_pokemon_ident(message.arguments[0])
    effect = message.arguments[1]

    if effect.casefold().startswith("ability: "):
        ability = effect[9:].strip()

        if not ability:
            raise ValueError(f"Empty activated ability in {message.raw!r}")

        context = message.arguments[2] if len(message.arguments) == 3 else None

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
            raise ValueError(f"Empty activated move in {message.raw!r}")

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

        return MoveActivationEvent(
            pokemon=pokemon,
            move=move,
        )
    if effect.casefold().startswith("item: "):
        item = effect[6:].strip()

        if not item:
            raise ValueError(f"Empty activated item in {message.raw!r}")
        consumed = has_annotation(message, "consumed") or has_annotation(message, "eat")

        return ItemEvent(
            source=EffectSource(
                type=SourceType.ITEM,
                name=item,
                actor=pokemon,
                action_id=None,
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
    except ValueError:
        return unhandled_event(message)

    return MinorStatusActivationEvent(
        target=pokemon,
        effect=minor_status,
    )


def _minor_status_or_none(value: str) -> MinorStatus | None:
    normalized = value.removeprefix("move: ").casefold()
    return MINOR_STATUS_BY_NAME.get(normalized)


# --- Effect handlers (registry entries) ------------------------------------


def parse_effect_message(
    message: ProtocolMessage,
    context: EffectParseContext,
) -> list[BaseEvent] | None:
    """Convert one protocol effect message into semantic events.

    ``None`` means the command does not belong to the shared effect layer.
    An empty list means it was recognized and deliberately emitted no event.
    """

    for rule in SPECIAL_EFFECT_RULES.get(message.command, ()):
        if rule.predicate(message, context):
            return rule.handler(message, context)

    handler = SIMPLE_EFFECT_HANDLERS.get(message.command)
    if handler is None:
        return None
    return handler(message, context)


def _parse_damage(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
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
    weather = Weather(message.arguments[0])
    upkeep = annotation_value(message, "upkeep") is not None
    return [
        WeatherEvent(
            weather=weather,
            started=weather != Weather.CLEAR_SKY,
            upkeep=upkeep,
            source=parse_effect_source(
                message=message,
                default_source=context.source,
            ),
        )
    ]


def _parse_forme_change(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 2)
    pokemon = parse_pokemon_ident(message.arguments[0])
    forme = message.arguments[1].strip()
    if not forme:
        raise ValueError(f"Empty forme change in {message.raw!r}")
    return [
        FormeChangeEvent(
            source=parse_effect_source(
                message=message,
                default_source=context.source,
                affected=pokemon,
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
        raise ValueError(f"Unexpected field event command: {message.raw!r}")
    value = message.arguments[0].strip()
    if value.casefold().startswith("move: "):
        effect_type = SourceType.MOVE
        effect_name = value.removeprefix("move: ").strip()
    else:
        effect_type = SourceType.UNKNOWN
        effect_name = value
    if not effect_name:
        raise ValueError(f"Empty field effect name: {message.raw!r}")
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

    return [UnhandledEvent.from_message(message, action_id=context.source.action_id)]


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
    require_arguments(message, 2)
    target = parse_pokemon_ident(message.arguments[0])
    condition = parse_condition(message.arguments[1])
    return [
        SetHpEvent(
            source=parse_effect_source(
                message,
                context.source,
                affected=target,
            ),
            target=target,
            curr_hp=condition.current_hp,
            max_hp=condition.max_hp,
            hp_is_percentage=is_percentage_hp(
                context.player_id,
                target,
                condition,
            ),
        )
    ]


def _parse_set_boost(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 3)
    target = parse_pokemon_ident(message.arguments[0])
    stage = int(message.arguments[2])
    if not -6 <= stage <= 6:
        raise ValueError(f"Invalid stat stage in {message.raw!r}")
    return [
        StatSetEvent(
            source=parse_effect_source(
                message,
                context.source,
                affected=target,
            ),
            target=target,
            stat=Stat(message.arguments[1]),
            stage=stage,
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
    require_arguments(message, 0)
    return [ClearNegativeBostsEvent(source=context.source)]


def _parse_item(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 2)
    pokemon = parse_pokemon_ident(message.arguments[0])
    item = message.arguments[1]
    previous_owner_value = annotation_value(message, "of")
    previous_owner = (
        parse_pokemon_ident(previous_owner_value)
        if previous_owner_value is not None
        else None
    )
    source = parse_effect_source(
        message=message,
        default_source=context.source,
        affected=previous_owner,
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
    stages = int(message.arguments[2]) * direction
    events: list[BaseEvent] = [
        StatChangeEvent(
            parse_effect_source(message, context.source, affected=target),
            target,
            [(Stat(message.arguments[1]), stages)],
        )
    ]
    return events


def _parse_side_condition(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 2)
    return [
        SideConditionEvent(
            source=context.source,
            side=parse_side_ident(message.arguments[0]),
            condition=SideCondition(message.arguments[1].removeprefix("move: ")),
            started=message.command == "-sidestart",
        )
    ]


def _parse_activation(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    event = _activation_event(message)
    if isinstance(event, UnhandledEvent):
        event = unhandled_event(message, context.action_id)
    return [event]


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
        message=message,
        default_source=context.source,
        affected=actor,
    )
    return [
        TeamCureEvent(
            source=source,
            side=actor.player,
            actor=actor,
        )
    ]


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


# --- Special-rule predicates and handlers (-start / -end / -fail) ----------


def is_failed_stat_change(
    message: ProtocolMessage,
    _context: EffectParseContext,
) -> bool:
    return len(message.arguments) >= 2 and message.arguments[1] in {"boost", "unboost"}


def _parse_failed_stat_change(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 2)
    target = parse_pokemon_ident(message.arguments[0])
    return [
        StatChangeEvent(
            source=parse_effect_source(
                message=message,
                default_source=context.source,
                affected=target,
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
    return [_ability_event(message, context.source)]


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


def _is_type_change(message: ProtocolMessage, _context: EffectParseContext) -> bool:
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
        raise ValueError(f"Empty type change in {message.raw!r}")
    return [
        TypeChangeEvent(
            source=parse_effect_source(
                message=message,
                default_source=context.source,
                affected=target,
            ),
            target=target,
            types=types,
        )
    ]


def _is_perish_count(message: ProtocolMessage, _context: EffectParseContext) -> bool:
    return len(message.arguments) >= 2 and message.arguments[1].casefold().startswith(
        "perish"
    )


def _parse_perish_count(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 2)
    target = parse_pokemon_ident(message.arguments[0])
    value = message.arguments[1]
    if not value.casefold().startswith("perish"):
        raise ValueError(f"Not a Perish Song countdown: {message.raw!r}")
    count_text = value[6:]
    try:
        count = int(count_text)
    except ValueError as error:
        raise ValueError(f"Invalid Perish Song count: {message.raw!r}") from error
    if count not in {0, 1, 2, 3}:
        raise ValueError(f"Unexpected Perish Song count: {count}")
    return [
        PerishCountEvent(
            source=context.source,
            target=target,
            count=count,
        )
    ]


def _is_mimic_copy(message: ProtocolMessage, _context: EffectParseContext) -> bool:
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
    return len(message.arguments) >= 2 and message.arguments[1] in SIDE_CONDITION_VALUES


def _parse_volatile_side_condition(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    target = parse_pokemon_ident(message.arguments[0])
    return [
        SideConditionEvent(
            source=context.source,
            side=target.player,
            condition=SideCondition(message.arguments[1]),
            started=message.command == "-start",
        )
    ]


def _is_minor_status(message: ProtocolMessage, _context: EffectParseContext) -> bool:
    return (
        len(message.arguments) >= 2
        and _minor_status_or_none(message.arguments[1]) is not None
    )


def _parse_minor_status(
    message: ProtocolMessage, context: EffectParseContext
) -> list[BaseEvent]:
    require_arguments(message, 2)
    target = parse_pokemon_ident(message.arguments[0])
    return [
        MinorStatusEvent(
            parse_effect_source(message, context.source, affected=target),
            target,
            parse_minor_status(message.arguments[1]),
            message.command == "-start",
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
            "In Gen 2, Toxic's counter is retained through Baton Pass/Heal Bell and applies to PSN/BRN."
        ),
        ("If you want to tie earlier, consider using `/offertie`."),
        ("In Gen 3, Intimidate does not activate if every target has a Substitute."),
        (
            "In Gen 4, Intimidate does not activate if every target has a Substitute (or the Substitute was just broken by U-turn)."
        ),
        (
            "Sleep Clause Mod prevents players from putting more than one of their opponent's Pokémon to sleep at a time"
        ),
    ]:
        return [
            DiscardedEvent(
                command=message.command,
                reason="Unhandled hint: " + message.arguments[0],
            )
        ]
    return [UnhandledEvent.from_message(message)]


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
        EffectRule(_is_minor_status, _parse_minor_status),
        EffectRule(_always, _parse_unknown_start_end),
    ),
}
