import asyncio
import logging
import traceback
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from python_showdown.classes.client.client import Client
from python_showdown.classes.combat.random import RandomMoveCombatHandler
from python_showdown.logger import TRACE, LogManager, create_battle_file_handler

WEBSOCKET_URL = "ws://192.168.1.154:8000/showdown/websocket"
BATTLE_COUNT = 1000
# FORMAT = "gen1randombattle"
# FORMAT = "gen2randombattle"
FORMAT = "gen3randombattle"
# FORMAT = "gen4randombattle"

ERROR_LOG = Path("simulation_errors.log")


async def run_battle(
    client_1: Client,
    client_2: Client,
    battle_number: int,
) -> dict[str, object]:
    battle_waiter = asyncio.create_task(
        client_1.wait_for_battle_end(timeout=300)
    )

    try:
        await client_1.challenge(
            client_2.username,
            FORMAT,
            timeout=30,
        )
        await client_2.accept_challenge(client_1.username)

        result = await battle_waiter

        print(
            f"Battle {battle_number}/{BATTLE_COUNT}: "
            f"winner={result.winner}, turns={result.move_count}, "
            f"duration={result.duration_seconds:.4f}s, "
            f"average={result.average_seconds_per_move:.6f}s/turn"
        )

        return asdict(result)

    except BaseException:
        battle_waiter.cancel()

        try:
            await battle_waiter
        except asyncio.CancelledError:
            pass

        raise


async def main() -> None:
    logs_1 = LogManager(tag="BOT1")
    logs_2 = LogManager(tag="BOT2")

    # One file per battle room holding the raw server output (TRACE on
    # the protocol logger) -> logs/battle/raw/<room_id>.txt
    logs_1.add_handler(
        create_battle_file_handler(
            Path("logs/battle/raw"),
            level=TRACE,
        ),
        loggers="protocol",
    )

    # One file per battle room holding info/debug/error logging for the
    # battle and errors loggers -> logs/battle/info/<room_id>.txt
    logs_1.add_handler(
        create_battle_file_handler(
            Path("logs/battle/info"),
            level=logging.DEBUG,
        ),
        loggers=(logs_1.battle, logs_1.errors),
    )
    client_1 = Client(
        WEBSOCKET_URL,
        combat_handler=RandomMoveCombatHandler(),
        log_manager=logs_1,
    )
    client_2 = Client(
        WEBSOCKET_URL,
        combat_handler=RandomMoveCombatHandler(),
        log_manager=logs_2,
    )

    results: list[dict[str, object]] = []
    failed_battles = 0

    try:
        await asyncio.gather(
            client_1.connect(),
            client_2.connect(),
        )

        await asyncio.gather(
            client_1.login("BOT1"),
            client_2.login("BOT2"),
        )
        print("client 1 is connected", client_1.username)
        print("client 2 is connected", client_2.username)

        t0 = perf_counter()
        for battle_number in range(1, BATTLE_COUNT + 1):
            try:
                result = await run_battle(
                    client_1,
                    client_2,
                    battle_number,
                )
                results.append(result)

            except Exception:
                failed_battles += 1

                error = (
                    f"\n{'=' * 80}\n"
                    f"Battle {battle_number} failed\n"
                    f"Format: {FORMAT}\n"
                    f"{traceback.format_exc()}"
                )

                print(error)

                with ERROR_LOG.open("a", encoding="utf-8") as file:
                    file.write(error)

        successful_battles = len(results)

        if successful_battles > 0:
            total_duration = sum(
                float(result["duration_seconds"])
                for result in results
            )
            total_turns = sum(
                int(result["move_count"])
                for result in results
            )

            average_battle_duration = (
                total_duration / successful_battles
            )
            average_turn_duration = (
                total_duration / total_turns
                if total_turns > 0
                else 0.0
            )

            print(f"\nSimulation complete in {perf_counter() - t0}s")
            print(f"Successful battles: {successful_battles}")
            print(f"Failed battles: {failed_battles}")
            print(
                f"Average battle duration: {average_battle_duration:.6f}s"
            )
            print(
                f"Average time per turn: {average_turn_duration:.6f}s"
            )

    finally:
        await asyncio.gather(
            client_1.close(),
            client_2.close(),
            return_exceptions=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
