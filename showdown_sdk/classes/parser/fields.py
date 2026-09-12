from showdown_sdk.classes.parser.context import ParsedCondition
from showdown_sdk.classes.parser.models import (
    EffectSource,
    PokemonDetails,
    PokemonIdent,
    ProtocolMessage,
)
from showdown_sdk.classes.parser.protocol import LEVEL_PATTERN, annotation_value
from showdown_sdk.exceptions import (
    MalformedProtocolError,
    UnsupportedProtocolError,
)
from showdown_sdk.models.pokemon import MajorStatus, MinorStatus
from showdown_sdk.models.sdk import SourceType

## Parsers


def parse_pokemon_ident(value: str) -> PokemonIdent:
    try:
        position, name = value.split(": ", 1)
    except ValueError as error:
        raise MalformedProtocolError(
            f"Invalid Pokémon identifier: {value!r}"
        ) from error

    if len(position) == 2:
        player, slot = position, None
    elif len(position) == 3:
        player, slot = position[:2], position[2]
    else:
        raise MalformedProtocolError(f"Invalid Pokémon position: {position!r}")

    if player not in {"p1", "p2", "p3", "p4"}:
        raise MalformedProtocolError(f"Invalid Pokémon player: {player!r}")
    if slot is not None and slot not in {"a", "b", "c"}:
        raise MalformedProtocolError(f"Invalid active slot: {slot!r}")

    return PokemonIdent(player, slot, name)


def parse_condition(value: str) -> ParsedCondition:
    parts = value.split()
    if not parts:
        raise MalformedProtocolError("Cannot parse an empty condition")

    hp_text = parts[0]
    status = None
    if len(parts) > 1:
        try:
            status = MajorStatus(parts[1])
        except ValueError as error:
            raise MalformedProtocolError(
                f"Unsupported major status in {value!r}"
            ) from error

    try:
        if "/" in hp_text:
            current_text, max_text = hp_text.split("/", 1)
            current_hp, max_hp = int(current_text), int(max_text)
        else:
            current_hp, max_hp = int(hp_text), None
    except ValueError as error:
        raise MalformedProtocolError(
            f"Invalid HP condition: {value!r}"
        ) from error

    if current_hp < 0 or (max_hp is not None and not 0 <= current_hp <= max_hp):
        raise MalformedProtocolError(f"Invalid HP condition: {value!r}")

    return ParsedCondition(current_hp, max_hp, status)


def parse_level(details: str) -> int:
    """Parse the level, if pokémon is lvl 100 it is omited"""
    match = LEVEL_PATTERN.search(details)
    return int(match.group("level")) if match is not None else 100


def parse_pokemon_details(details: str) -> PokemonDetails:
    """Parse the common metadata encoded in a Showdown Pokémon details field.

    Examples:
        Pikachu
        Pikachu, L80
        Pikachu, L80, M
        Pikachu, L80, F, shiny

    Unknown detail tokens are intentionally ignored here. This function only
    owns the metadata the SDK currently models.
    """
    normalized = details.strip()
    if not normalized:
        raise MalformedProtocolError("Cannot parse empty Pokémon details")

    parts = tuple(part.strip() for part in normalized.split(","))

    gender: str | None = None
    shiny = False

    for part in parts[1:]:
        if part == "M":
            if gender == "F":
                raise MalformedProtocolError(
                    f"Conflicting genders in Pokémon details: {details!r}"
                )
            gender = "M"
        elif part == "F":
            if gender == "M":
                raise MalformedProtocolError(
                    f"Conflicting genders in Pokémon details: {details!r}"
                )
            gender = "F"
        elif part == "shiny":
            shiny = True

    return PokemonDetails(
        level=parse_level(normalized), gender=gender, shiny=shiny
    )


def is_percentage_hp(
    player_id: str, pokemon: PokemonIdent, condition: ParsedCondition
) -> bool:
    return pokemon.player != player_id and (
        condition.max_hp == 100 or condition.max_hp is None
    )


def parse_minor_status(value: str) -> MinorStatus:
    normalized = value.removeprefix("move: ").casefold()
    for status in MinorStatus:
        if status.value.casefold() == normalized:
            return status
    raise UnsupportedProtocolError(f"Unsupported minor status: {value!r}")


def parse_side_ident(value: str) -> str:
    side = value.split(":", 1)[0].strip()

    if side not in {"p1", "p2", "p3", "p4"}:
        raise MalformedProtocolError(f"Invalid side identifier: {value!r}")

    return side


def make_move_source(
    user: PokemonIdent, move: str, action_id: int
) -> EffectSource:
    return EffectSource(SourceType.MOVE, move, user, action_id)


def parse_effect_source(
    message: ProtocolMessage,
    default_source: EffectSource,
    *,
    affected: PokemonIdent | None = None,
    inherit_default: bool = True,
) -> EffectSource:
    from_value = annotation_value(message, "from")

    if from_value is None:
        if inherit_default:
            return default_source
        return EffectSource(type=SourceType.UNKNOWN)

    normalized = from_value.strip()
    prefix, source_name = _split_source_value(normalized)

    of_value = annotation_value(message, "of")
    explicit_actor = (
        parse_pokemon_ident(of_value) if of_value is not None else None
    )
    actor = explicit_actor if explicit_actor is not None else affected

    if prefix is None:
        lowered = source_name.casefold()

        if lowered == "recoil":
            return EffectSource(
                type=SourceType.RECOIL,
                name=default_source.name,
                actor=default_source.actor,
                action_id=default_source.action_id,
            )

        if lowered in {status.value.casefold() for status in MajorStatus}:
            return EffectSource(
                type=SourceType.STATUS, name=lowered, actor=actor
            )

        if lowered in {"sandstorm", "hail", "snow"}:
            return EffectSource(type=SourceType.WEATHER, name=source_name)

        return EffectSource(
            type=SourceType.UNKNOWN,
            name=normalized,
            actor=actor,
            action_id=default_source.action_id,
        )

    if prefix == "move":
        return EffectSource(
            type=SourceType.MOVE,
            name=source_name,
            actor=actor,
            action_id=default_source.action_id,
        )

    if prefix == "item":
        return EffectSource(
            type=SourceType.ITEM,
            name=source_name,
            actor=actor,
            action_id=default_source.action_id,
            owner=explicit_actor,
        )

    if prefix == "ability":
        owner = None

        if affected is not None and (
            explicit_actor is None or explicit_actor == affected
        ):
            owner = affected

        return EffectSource(
            type=SourceType.ABILITY,
            name=source_name,
            actor=actor,
            action_id=default_source.action_id,
            owner=owner,
        )

    return EffectSource(
        type=SourceType.UNKNOWN,
        name=normalized,
        actor=actor,
        action_id=default_source.action_id,
    )


def parse_move_origin(message: ProtocolMessage) -> EffectSource | None:
    """Parse the ``[from]`` annotation attached to a ``|move|`` command.

    This is intentionally separate from ``parse_effect_source`` because
    Showdown uses ``[from]`` differently on move commands and effect commands.
    """
    from_value = annotation_value(message, "from")
    if from_value is None:
        return None

    prefix, source_name = _split_source_value(from_value)

    if prefix is None:
        if source_name == "Mirror Move":
            return EffectSource(type=SourceType.MOVE, name=source_name)

        return EffectSource(type=SourceType.UNKNOWN, name=source_name)

    if prefix == "ability":
        return EffectSource(type=SourceType.ABILITY, name=source_name)

    if prefix == "move":
        return EffectSource(type=SourceType.MOVE, name=source_name)

    raise UnsupportedProtocolError(f"Unknown move origin: {from_value!r}")


## Helpers


def _split_source_value(value: str) -> tuple[str | None, str]:
    normalized = value.strip()
    if not normalized:
        raise MalformedProtocolError("Cannot parse empty effect source")

    prefix, separator, name = normalized.partition(": ")

    if not separator:
        return None, normalized

    prefix = prefix.strip().casefold()
    name = name.strip()

    if not prefix or not name:
        raise MalformedProtocolError(f"Invalid effect source: {value!r}")

    return prefix, name
