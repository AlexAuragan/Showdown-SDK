from python_showdown.classes.parser.context import ParsedCondition
from python_showdown.classes.parser.models import (
    EffectSource,
    PokemonIdent,
    ProtocolMessage,
)
from python_showdown.classes.parser.protocol import LEVEL_PATTERN, annotation_value
from python_showdown.models.pokemon.status import MajorStatus, MinorStatus
from python_showdown.models.sdk.battle_state import SourceType


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
    raise ValueError(f"Unsupported minor status: {value!r}")


def parse_side_ident(value: str) -> str:
    side = value.split(":", 1)[0].strip()

    if side not in {"p1", "p2", "p3", "p4"}:
        raise ValueError(f"Invalid side identifier: {value!r}")

    return side


def make_move_source(user: PokemonIdent, move: str, action_id: int) -> EffectSource:
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
        return (
            default_source
            if inherit_default
            else EffectSource(type=SourceType.UNKNOWN)
        )

    of_value = annotation_value(message, "of")
    explicit_actor = (
        parse_pokemon_ident(of_value)
        if of_value is not None
        else None
    )

    actor = (
        explicit_actor
        if explicit_actor is not None
        else affected
    )

    normalized = from_value.strip()
    lowered = normalized.casefold()

    if lowered == "recoil":
        return EffectSource(
            type=SourceType.RECOIL,
            name=default_source.name,
            actor=default_source.actor,
            action_id=default_source.action_id,
        )

    if lowered.startswith("move: "):
        return EffectSource(
            type=SourceType.MOVE,
            name=normalized[6:],
            actor=actor,
            action_id=default_source.action_id,
        )

    if lowered.startswith("item: "):
        return EffectSource(
            type=SourceType.ITEM,
            name=normalized[6:],
            actor=actor,
            action_id=default_source.action_id,
            owner=explicit_actor,
        )

    if lowered.startswith("ability: "):
        return EffectSource(
            type=SourceType.ABILITY,
            name=normalized[9:],
            actor=actor,
            action_id=default_source.action_id,
            owner=explicit_actor,
        )

    if lowered in {
        status.value.casefold()
        for status in MajorStatus
    }:
        return EffectSource(
            type=SourceType.STATUS,
            # name=lowered,
            actor=actor,
        )

    if lowered in {"sandstorm", "hail", "snow"}:
        return EffectSource(
            type=SourceType.WEATHER,
            name=normalized,
        )

    return EffectSource(
        type=SourceType.UNKNOWN,
        name=normalized,
        actor=actor,
        action_id=default_source.action_id,
    )
