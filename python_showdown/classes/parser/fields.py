from python_showdown.classes.parser.context import ParsedCondition
from python_showdown.classes.parser.enums import (
    MajorStatus,
    MinorStatus,
    SourceType,
)
from python_showdown.classes.parser.models import EffectSource, PokemonIdent
from python_showdown.classes.parser.protocol import LEVEL_PATTERN, annotation_value


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


def parse_side_ident(value: str) -> str:
    side = value.split(":", 1)[0].strip()

    if side not in {"p1", "p2", "p3", "p4"}:
        raise ValueError(
            f"Invalid side identifier: {value!r}"
        )

    return side


def make_move_source(user: PokemonIdent, move: str, action_id: int) -> EffectSource:
    return EffectSource(SourceType.MOVE, move, user, action_id)


def parse_effect_source(
    message,
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
