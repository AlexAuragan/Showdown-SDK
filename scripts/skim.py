import os
import shutil
from collections import defaultdict
from pathlib import Path

from tqdm import tqdm

from python_showdown.classes.client.client import Client
from python_showdown.classes.parser.events.battle import (
    BattleEvent,
    MoveEvent,
    PokemonSwitchEvent,
)
from python_showdown.classes.parser.events.lobby import LobbyEvent
from python_showdown.classes.parser.exceptions import (
    InvalidActionError,
    ObsoleteRequestIdError,
)

POKEMON_TO_FILE: dict[str, set[Path]] = defaultdict(set)
MOVE_TO_FILE: dict[str, set[Path]] = defaultdict(set)


def list_instances(path: Path, formats: list[str]):
    client = Client("ws://192.168.1.154:8000/showdown/websocket")
    parser = client.parser
    for fmt in formats:
        logs_path = path / fmt / "raw"
        for file in tqdm(os.listdir(path / fmt / "raw")):
            if fmt not in file:
                continue
            with open(logs_path / file, "r") as f:
                client.battle_manager.player_id = "p2"
                for line in f:
                    line = " ".join(line.split(" ")[2:])
                    try:
                        events = parser.handle_line(line)
                    except InvalidActionError, ObsoleteRequestIdError:
                        events = []
                    except Exception:
                        print(line)
                        raise
                    for event in events:
                        # We need to ingest the events anyway to be aware of player id and other meta concept.
                        if isinstance(event, LobbyEvent):
                            event.update_client(client)
                        elif isinstance(event, BattleEvent):
                            event.update_manager(client.battle_manager)

                        if isinstance(event, PokemonSwitchEvent):
                            POKEMON_TO_FILE[event.pokemon.name].add(logs_path / file)
                        if isinstance(event, MoveEvent):
                            MOVE_TO_FILE[event.move].add(logs_path / file)


def find_least_files_for_all():
    pokemon_rarities: dict[str, float] = {}
    move_rarities: dict[str, float] = {}

    for pokemon, files in POKEMON_TO_FILE.items():
        pokemon_rarities[pokemon] = 1 / (len(files) or 1)
    for move, files in MOVE_TO_FILE.items():
        move_rarities[move] = 1 / (len(files) or 1)

    all_files: set[Path] = set()
    all_files = all_files.union(*POKEMON_TO_FILE.values())

    file_values = {
        file: sum(
            rarity
            for pokemon, rarity in pokemon_rarities.items()
            if file in POKEMON_TO_FILE[pokemon]
        )
        + sum(
            rarity
            for move, rarity in move_rarities.items()
            if file in MOVE_TO_FILE[move]
        )
        for file in all_files
    }

    ordered_files = [
        x[0] for x in sorted(file_values.items(), key=lambda x: x[1], reverse=True)
    ]

    seen_pokemon: set[str] = set()
    seen_moves: set[str] = set()
    keep_files: set[Path] = set()

    for file in ordered_files:
        if seen_pokemon == POKEMON_TO_FILE.keys() and seen_moves == MOVE_TO_FILE.keys():
            break
        new_pokemon = {
            pokemon
            for pokemon, files in POKEMON_TO_FILE.items()
            if file in files and pokemon not in seen_pokemon
        }

        new_moves = {
            move
            for move, files in MOVE_TO_FILE.items()
            if file in files and move not in seen_moves
        }

        if not new_pokemon and not new_moves:
            continue
        keep_files.add(file)
        for pokemon, files in POKEMON_TO_FILE.items():
            if file in files:
                seen_pokemon.add(pokemon)
        for move, files in MOVE_TO_FILE.items():
            if file in files:
                seen_moves.add(move)

    return keep_files


if __name__ == "__main__":
    for fmt in ["gen1randombattle", "gen3randombattle", "gen4randombattle"]:
        list_instances(Path("logs_odd"), [fmt])

        keep = find_least_files_for_all()
        print(keep)

        for file in keep:
            _ = shutil.copy2(file, Path(f"tests/sample_battles/{fmt}") / file.name)
