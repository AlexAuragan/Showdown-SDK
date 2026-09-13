"""Deterministic Gen 1 Mirror Move -> Mimic reproducer.

Runs two fixed custom-game teams:
- Fearow: Mirror Move / Agility / Drill Peck / Double-Edge
- Alakazam: Mimic

Both handlers always choose move slot 1. Since Alakazam is faster, it uses
Mimic first; Fearow then uses Mirror Move and should invoke Mimic through
Mirror Move on the same turn.

If the SDK bug is present, the Fearow-side parser should raise:
    BattleStateInvariantError:
    Own Pokémon used Mimic but expected exactly one Mimic slot:
    ['mirrormove', 'agility', 'drillpeck', 'doubleedge']
"""

import asyncio
import os
import traceback
from pathlib import Path

from scripts.utils import write_failure_outputs
from showdown_sdk import (
    TRACE,
    LogManager,
    create_battle_file_handler,
    start_file_io_worker,
    stop_file_io_worker,
)
from showdown_sdk.classes.client import Client
from showdown_sdk.models.sdk import (
    BattleState,
    TeamSet,
    print_reproduction_teams,
)

WEBSOCKET_URL = "ws://127.0.0.1:8000/showdown/websocket"
FORMAT = "gen1customgame"
BATTLE_COUNT = 1000
LOGS_ROOT = Path("logs") / "mirror_move_mimic_repro"

os.environ["SHOWDOWN_USE_REQUEST_STATE"] = "1"


TEAM_MIRROR_MOVE = TeamSet.from_showdown(
    """
Fearow
- Mirror Move
- Agility
- Drill Peck
- Double-Edge
"""
)

TEAM_MIMIC = TeamSet.from_showdown(
    """
Alakazam
- Mimic
"""
)


class FirstMoveHandler:
    """Always choose move slot 1."""

    def select_top_actions(
        self, _battle_state: BattleState
    ) -> list[tuple[str, int]]:
        return [("move", 1)]

    @staticmethod
    def select_team_order() -> list[int]:
        return [1, 2, 3, 4, 5, 6]


async def run_fixed_battle(client_1: Client, client_2: Client) -> None:
    await asyncio.gather(
        client_1.ensure_connected(), client_2.ensure_connected()
    )

    if client_1.username is None:
        raise RuntimeError("Client 1 is not logged in")
    if client_2.username is None:
        raise RuntimeError("Client 2 is not logged in")

    await client_1.challenge(
        client_2.username, FORMAT, timeout=60, team=TEAM_MIRROR_MOVE
    )
    await client_2.accept_challenge(client_1.username, team=TEAM_MIMIC)

    await asyncio.gather(
        client_1.battle_manager.room_ready.wait(),
        client_2.battle_manager.room_ready.wait(),
    )

    await asyncio.gather(
        client_1.wait_for_battle_end(timeout=300),
        client_2.wait_for_battle_end(timeout=300),
    )


def make_client(tag: str, raw_filename: str) -> Client:
    logs = LogManager(tag=tag)
    logs.add_handler(
        create_battle_file_handler(
            LOGS_ROOT / FORMAT, level=TRACE, filename=raw_filename
        ),
        loggers="protocol",
    )

    return Client(WEBSOCKET_URL, log_manager=logs)


async def main() -> None:
    client_1 = make_client("MIRROR", "client_1_raw.txt")
    client_2 = make_client("MIMIC", "client_2_raw.txt")

    start_file_io_worker()

    try:
        await asyncio.gather(client_1.connect(), client_2.connect())
        await asyncio.gather(
            client_1.login("MIRRORREPRO"), client_2.login("MIMICREPRO")
        )

        print(f"Format: {FORMAT}")
        print(f"Battles requested: {BATTLE_COUNT}")
        print_reproduction_teams(TEAM_MIRROR_MOVE, TEAM_MIMIC)

        for battle_number in range(1, BATTLE_COUNT + 1):
            print(f"battle {battle_number}/{BATTLE_COUNT}")

            try:
                await run_fixed_battle(client_1, client_2)
            except BaseException:
                print(f"\nFAILED ON BATTLE {battle_number}")
                print_reproduction_teams(TEAM_MIRROR_MOVE, TEAM_MIMIC)

                for client in (client_1, client_2):
                    try:
                        await asyncio.to_thread(
                            write_failure_outputs,
                            client,
                            include_events=True,
                            include_parser_state=True,
                            include_showdown_state=True,
                        )
                    except BaseException:
                        print(
                            "Could not write failure outputs for "
                            + f"{client.username}:"
                        )
                        traceback.print_exc()
                        raise
                raise

        print(f"Completed all {BATTLE_COUNT} battles without reproducing.")

    finally:
        stop_file_io_worker()
        await asyncio.gather(
            client_1.close(), client_2.close(), return_exceptions=True
        )


if __name__ == "__main__":
    asyncio.run(main())
