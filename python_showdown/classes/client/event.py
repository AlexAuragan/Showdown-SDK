from typing import TYPE_CHECKING

from python_showdown.classes.client.event_classes import BaseEvent, DiscardedEvent
from python_showdown.classes.client.event_utils import *
from python_showdown.classes.pokemon.pokemon import Pokemon

if TYPE_CHECKING:
    from python_showdown.classes.client.client import Client

class Parser:
    def __init__(self) -> None:
        self.history = []

    def handle_line(self, client: Client, line: str):
        date, time, line = line.split(" ", 3)
        player_side = client.battle_player_id
        events = self.parse_event(client.battle_player_id, line)
        self.history.extend(events)
        for event in events:
            event.update_client(client)

    def parse_event(self, player_id: str, line: str) -> list[BaseEvent]:
        void_prefixes = [
            "|init|battle\n", "|title|", ">battle", "|J|", "|L|", "|pm|", "|t:|", "|gametype|", "|player|", "|gen|",
            "|tier|", "|rule|", "|teamsize|"
        ]
        for pref in void_prefixes:
            if line.startswith(pref):
                return [DiscardedEvent()]

        voids = ["|\n", "|start\n"]
        for void in voids:
            if line == void:
                return [DiscardedEvent()]

        if line.startswith("|switch|"):
            return [handle_switch(player_id, line)]

        if line.startswith("|turn|"):
            return [handle_turn(line)]

        if line.startswith("|request|"):
            return handle_request(line)

        if line.startswith("|move|"):
            return [handle_move(player_id,line)]

        raise RuntimeError("Could not parse line", line)


if __name__ == "__main__":
    parser = Parser()
    with open("logs/battle_gen_1/raw/battle-gen1randombattle-12099.txt", "r") as f:
        lines = f.readlines()
        for line in lines:
            date, time, line = line.split(" ", 2)
            try:
                parser.parse_event("p1",line)
            except Exception:
                print("Fail to parse", line)
                raise
