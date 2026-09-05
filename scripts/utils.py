import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from python_showdown.classes.client.client import Client
from python_showdown.utils.serialization import (
    Serializable,
    SerializableArray,
    SerializableObject,
)


def write_json(path: Path, data: list[SerializableObject] | Serializable | SerializableArray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        file.write(
            json.dumps(data)
        )



def write_battle_outputs(client: Client) -> None:
    raw_log_path = client.log_manager.latest_raw_log_path()
    if raw_log_path is None:
        return

    battle_directory = raw_log_path.parent

    write_json(
        battle_directory / "events.json",
        client.battle_manager.last_battle_events,
    )
    write_json(
        battle_directory / "battle_states.json",
        client.battle_manager.last_battle_turn_states,
    )



async def run_battle(
    client_1: Client,
    client_2: Client,
    fmt: str,
) -> SerializableObject:
    await asyncio.gather(
        client_1.ensure_connected(),
        client_2.ensure_connected(),
    )

    if client_1.username is None:
        raise RuntimeError("client not connected")

    if client_2.username is None:
        raise RuntimeError("client not connected")

    battle_waiter_1, battle_waiter_2 = None, None
    try:
        await client_1.challenge(
            client_2.username,
            fmt,
            timeout=60,
        )
        await client_2.accept_challenge(
            client_1.username,
        )

        await asyncio.gather(
            client_1.battle_manager.room_ready.wait(),
            client_2.battle_manager.room_ready.wait(),
        )

        battle_waiter_1 = asyncio.create_task(client_1.wait_for_battle_end(timeout=300))
        battle_waiter_2 = asyncio.create_task(client_2.wait_for_battle_end(timeout=300))

        result_1, _ = await asyncio.gather(
            battle_waiter_1,
            battle_waiter_2,
        )
        # await asyncio.to_thread(write_battle_outputs, client_2)
        # print(
        #     f"Battle {battle_number}/{BATTLE_COUNT}: "
        #     + f"winner={result_1.winner}, turns={result_1.move_count}, "
        #     + f"duration={result_1.duration_seconds:.4f}s, "
        #     + f"average={result_1.average_seconds_per_move:.6f}s/turn"
        # )

        return asdict(result_1)

    except BaseException:
        if battle_waiter_1 is None or battle_waiter_2 is None:
            raise
        battle_waiter_1.cancel()
        battle_waiter_2.cancel()

        await asyncio.gather(
            battle_waiter_1,
            battle_waiter_2,
            return_exceptions=True,
        )

        raise
