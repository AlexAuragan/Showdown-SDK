import json
import os
import sys
from pathlib import Path

from python_showdown.classes.client.client import Client
from python_showdown.classes.parser.events.battle import BattleEvent
from python_showdown.classes.parser.events.lobby import LobbyEvent
from python_showdown.classes.parser.exceptions import (
    InvalidActionError,
    ObsoleteRequestIdError,
)


def list_fights(path: Path, formats: list[str]):
    client = Client("ws://192.168.1.154:8000/showdown/websocket")
    parser = client.parser
    for fmt in formats:
        logs_path = path / fmt / "raw"
        for file in os.listdir(path / fmt / "raw"):
            if fmt not in file:
                continue
            print(logs_path / file)
            with open(logs_path / file, "r") as f:
                client.battle_manager.player_id = "p2"
                for line in f:
                    line = " ".join(line.split(" ")[2:])
                    try:
                        new_events = parser.handle_line(line)
                        client.battle_manager.battle_state.history.extend(new_events)
                    except InvalidActionError, ObsoleteRequestIdError:
                        new_events = []
                    except Exception:
                        print(line)
                        raise
                    for event in new_events:
                        # We need to ingest the events anyway to be aware of player id and other meta concept.
                        if isinstance(event, LobbyEvent):
                            event.update_client(client)
                        elif isinstance(event, BattleEvent):
                            event.update_manager(client.battle_manager)

                battle_state = client.battle_manager.battle_state
                # print(battle_state.to_json())
                print(json.dumps(battle_state.history_json(), indent=4))
                sys.exit(0)


if __name__ == "__main__":
    list_fights(Path("logs_odd"), ["gen1randombattle"])
