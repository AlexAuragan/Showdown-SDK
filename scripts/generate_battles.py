import asyncio
import logging
import traceback
from collections.abc import Awaitable
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from python_showdown.classes.client.client import Client
from python_showdown.classes.combat_handler.random_handler import (
    RandomMoveCombatHandler,
)
from python_showdown.logger import TRACE, LogManager, create_battle_file_handler

WEBSOCKET_URL = "ws://192.168.1.154:8000/showdown/websocket"
BATTLE_COUNT = 1000
# Number of players (must be even). Players are paired up and each pair
# runs its share of the battles; all pairs run concurrently.
PLAYER_COUNT = 8
PAIR_COUNT = PLAYER_COUNT // 2
BATTLES_PER_PAIR = BATTLE_COUNT // PAIR_COUNT

ERROR_LOG = Path("simulation_errors.log")

FORMATS = [
    "gen1randombattle",
    "gen2randombattle",
    "gen3randombattle",
    "gen4randombattle",
]


async def run_battle(
    client_1: Client,
    client_2: Client,
    fmt: str,
    battle_number: int,
) -> dict[str, object]:
    _ = await asyncio.gather(
        client_1.ensure_connected(),
        client_2.ensure_connected(),
    )

    if client_1.username is None:
        raise RuntimeError("client not connected")

    if client_2.username is None:
        raise RuntimeError("client not connected")

    battle_waiter_1, battle_waiter_2 = None, None
    try:
        _ = await client_1.challenge(
            client_2.username,
            fmt,
            timeout=60,
        )
        await client_2.accept_challenge(
            client_1.username,
        )

        _ = await asyncio.gather(
            client_1.battle_manager.room_ready.wait(),
            client_2.battle_manager.room_ready.wait(),
        )

        battle_waiter_1 = asyncio.create_task(client_1.wait_for_battle_end(timeout=300))
        battle_waiter_2 = asyncio.create_task(client_2.wait_for_battle_end(timeout=300))

        result_1, _ = await asyncio.gather(
            battle_waiter_1,
            battle_waiter_2,
        )

        print(
            f"Battle {battle_number}/{BATTLE_COUNT}: "
            + f"winner={result_1.winner}, turns={result_1.move_count}, "
            + f"duration={result_1.duration_seconds:.4f}s, "
            + f"average={result_1.average_seconds_per_move:.6f}s/turn"
        )

        return asdict(result_1)

    except BaseException:
        if battle_waiter_1 is None or battle_waiter_2 is None:
            raise
        _ = battle_waiter_1.cancel()
        _ = battle_waiter_2.cancel()

        _ = await asyncio.gather(
            battle_waiter_1,
            battle_waiter_2,
            return_exceptions=True,
        )

        raise


def write_error(error: str) -> None:
    with ERROR_LOG.open("a", encoding="utf-8") as file:
        _ = file.write(error)


async def run_pair(
    client_1: Client,
    client_2: Client,
    fmt: str,
    pair_index: int,
    battle_offset: int,
) -> tuple[list[dict[str, object]], int]:
    """Run BATTLES_PER_PAIR battles sequentially between two clients.

    `battle_offset` is the global battle number this pair starts at, used
    only for nicer log messages.
    """
    results: list[dict[str, object]] = []
    failed_battles = 0

    for i in range(BATTLES_PER_PAIR):
        battle_number = battle_offset + i + 1
        try:
            result = await run_battle(
                client_1,
                client_2,
                battle_number=battle_number,
                fmt=fmt,
            )
            results.append(result)

        except Exception:  # noqa: BLE001 # This is intentional
            failed_battles += 1

            error = (
                f"\n{'=' * 80}\n"
                f"Battle {battle_number} failed (pair {pair_index + 1})\n"
                f"Format: {fmt}\n"
                f"{traceback.format_exc()}"
            )

            print(error)
            await asyncio.to_thread(write_error, error)

    return results, failed_battles


async def run_format(fmt: str) -> tuple[list[dict[str, object]], int]:
    """Spin up PLAYER_COUNT clients and run all pairs concurrently."""
    clients: list[Client] = []
    log_managers: list[LogManager] = []

    for i in range(1, PLAYER_COUNT + 1):
        tag = f"BOT{i}"
        logs = LogManager(tag=tag)

        # One file per battle room holding the raw server output (TRACE on
        # the protocol logger) -> logs/<format>/raw/<room_id>.txt
        if i % 2 == 0:
            logs.add_handler(
                create_battle_file_handler(
                    Path(f"logs_odd/{fmt}/raw"),
                    level=TRACE,
                ),
                loggers="protocol",
            )

            # One file per battle room holding info/debug/error logging for the
            # battle and errors loggers -> logs/<format>/info/<room_id>.txt
            logs.add_handler(
                create_battle_file_handler(
                    Path(f"logs_odd/{fmt}/info"),
                    level=logging.DEBUG,
                ),
                loggers=(logs.battle, logs.errors),
            )

        else:
            logs.add_handler(
                create_battle_file_handler(
                    Path(f"logs_even/{fmt}/raw"),
                    level=TRACE,
                ),
                loggers="protocol",
            )

            # One file per battle room holding info/debug/error logging for the
            # battle and errors loggers -> logs/<format>/info/<room_id>.txt
            logs.add_handler(
                create_battle_file_handler(
                    Path(f"logs_even/{fmt}/info"),
                    level=logging.DEBUG,
                ),
                loggers=(logs.battle, logs.errors),
            )
        client = Client(
            WEBSOCKET_URL,
            combat_handler=RandomMoveCombatHandler(),
            log_manager=logs,
        )
        clients.append(client)
        log_managers.append(logs)

    failed_battles = 0
    results: list[dict[str, object]] = []

    try:
        _ = await asyncio.gather(*(client.connect() for client in clients))

        _ = await asyncio.gather(
            *(client.login(f"BOT{i}") for i, client in enumerate(clients, start=1))
        )
        for i, client in enumerate(clients, start=1):
            print(f"client {i} is connected", client.username)

        t0 = perf_counter()

        # Pair up clients: (0,1), (2,3), ... and run each pair concurrently.
        pair_tasks: list[Awaitable[tuple[list[dict[str, object]], int]]] = []
        for pair_index in range(PAIR_COUNT):
            client_1 = clients[pair_index * 2]
            client_2 = clients[pair_index * 2 + 1]
            battle_offset = pair_index * BATTLES_PER_PAIR
            pair_tasks.append(
                run_pair(
                    client_1,
                    client_2,
                    fmt=fmt,
                    pair_index=pair_index,
                    battle_offset=battle_offset,
                )
            )

        pair_results = await asyncio.gather(*pair_tasks)

        for pair_results_list, pair_failed in pair_results:
            results.extend(pair_results_list)
            failed_battles += pair_failed

        successful_battles = len(results)

        if successful_battles > 0:
            total_duration = 0.0
            total_turns = 0

            for result in results:
                duration = result["duration_seconds"]
                move_count = result["move_count"]

                if not isinstance(duration, float):
                    raise TypeError(
                        f"duration_seconds must be float, got {type(duration).__name__}"
                    )

                if isinstance(move_count, bool) or not isinstance(move_count, int):
                    raise TypeError(
                        f"move_count must be int, got {type(move_count).__name__}"
                    )

                total_duration += duration
                total_turns += move_count

            average_battle_duration = total_duration / successful_battles
            average_turn_duration = (
                total_duration / total_turns if total_turns > 0 else 0.0
            )

            print(f"\nSimulation for {fmt} complete in {perf_counter() - t0}s")
            print(f"Successful battles: {successful_battles}")
            print(f"Failed battles: {failed_battles}")
            print(f"Average battle duration: {average_battle_duration:.6f}s")
            print(f"Average time per turn: {average_turn_duration:.6f}s")

    finally:
        _ = await asyncio.gather(
            *(client.close() for client in clients),
            return_exceptions=True,
        )

    return results, failed_battles


async def main() -> None:
    for fmt in FORMATS:
        _ = await run_format(fmt)


if __name__ == "__main__":
    t0 = perf_counter()
    asyncio.run(main())
    print("Took ", perf_counter() - t0)
