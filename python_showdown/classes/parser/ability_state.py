from python_showdown.classes.parser.context import ProtocolContext
from python_showdown.classes.parser.events.battle import AbilityEvent, BaseEvent
from python_showdown.classes.parser.fields import parse_pokemon_ident
from python_showdown.classes.parser.models import PokemonIdent, ProtocolMessage
from python_showdown.classes.parser.protocol import has_annotation


def pokemon_key(pokemon: PokemonIdent) -> tuple[str, str | None]:
    return pokemon.player, pokemon.slot


def register_ability_start(
    context: ProtocolContext,
    pokemon: PokemonIdent,
    ability: str,
) -> None:
    key = pokemon_key(pokemon)

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
    key = pokemon_key(pokemon)

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
    key = pokemon_key(pokemon)

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
    key = pokemon_key(pokemon)

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
