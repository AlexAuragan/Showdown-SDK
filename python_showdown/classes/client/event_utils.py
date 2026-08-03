from python_showdown.classes.client.event_classes import (
    MoveEvent,
    PokemonSwitchEvent,
    Target,
    TurnEvent,
)
from python_showdown.classes.pokemon.pokemon import EnemyPokemon, PartyPokemon


def handle_switch(player_side: str,line: str) -> PokemonSwitchEvent:
    _, player, pkmn, status = line.strip("|").split("|")
    player = player.split(": ")[0]

    target = Target.active_pokemon if player.startswith(player_side) else Target.enemy_pokemon
    pokemon_id, level = pkmn.split(", L")
    curr_hp, max_hp = status.split("/")
    return PokemonSwitchEvent(
        target=target, pokemon_id=pokemon_id, level=level, curr_hp=curr_hp, max_hp=max_hp, major_status=None
    )

def handle_turn(line: str) -> TurnEvent:
    turn = int(line.removeprefix("|turn|").removesuffix("\n"))
    return TurnEvent(turn)

def handle_request(line) -> list:
    return []

def handle_move(player_id, line) -> MoveEvent:
    _, source, move, target = line.strip("|").split("|")
    source = Target.active_pokemon if source.split(": ")[0].startswith(player_id) else Target.enemy_pokemon
    target  = Target.active_pokemon if target.split(": ")[0].startswith(player_id) else Target.enemy_pokemon

    return MoveEvent(
        source=source, target=target, does_hit=True, success=True, move=move
    )
