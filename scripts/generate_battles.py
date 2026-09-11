"""Run random-move battles against a local Showdown server.

Single entry point for all formats. Select what to run by editing the
config block below (or the environment remote overrides at the bottom),
then:

    uv run scripts/generate_battles.py            # one pass over FORMATS
    uv run scripts/generate_battles.py overnight  # cycle forever (fail_fast)

The first failed battle is archived into tests/sample_battles_auto/<fmt>
by scripts.utils.save_failed_battle (only when the cause is the SDK;
network/server failures are logged but skipped).

Requires the local server (WEBSOCKET_URL) to be running.
"""

# pyright: reportConstantRedefinition=false
import asyncio
import os
import shutil
import sys
import traceback
from pathlib import Path
from time import perf_counter
from typing import NoReturn

from tqdm import tqdm

from scripts.utils import (
    is_infra_failure,
    run_battle,
    save_failed_battle,
    write_failure_outputs,
)
from showdown_sdk import (
    TRACE,
    LogManager,
    create_battle_file_handler,
    start_file_io_worker,
    stop_file_io_worker,
)
from showdown_sdk.classes.client import Client
from showdown_sdk.classes.combat_handler import RandomMoveCombatHandler
from showdown_sdk.models.sdk import SampleTeamGenerator
from showdown_sdk.utils import SerializableObject

# ======================================================================
# Configuration -- edit here to select the run
# ======================================================================

WEBSOCKET_URL = "ws://127.0.0.1:8000/showdown/websocket"

# Formats to simulate, in order.
FORMATS = [
    "gen1randombattle",
    "gen2randombattle",
    "gen3randombattle",
    "gen4randombattle",
    "gen1ou",
    "gen2ou",
    "gen3ou",
    "gen4ou",
    "gen1ubers@@@!standard,standardag",
    "gen2ubers@@@!standard,standardag",
    "gen3ubers@@@!standard,standardag",
    "gen4ubers@@@!standard,standardag",
]

os.environ["SHOWDOWN_USE_REQUEST_STATE"] = "1"
# Total battles per format, spread evenly over PLAYER_COUNT / 2 pairs.
BATTLE_COUNT = 100
PLAYER_COUNT = 8  # must be even

# Generate sample teams before each battle instead of playing with the
# format's random stacks. None = auto: enabled unless the format name
# contains "randombattle".
GENERATE_TEAMS: bool | None = None

TEAM_SEED = 42

# Where per-format battle logs are written ("<logs_root>/<format>/...").
LOGS_ROOT = Path("logs")

# What gets recorded per battle:
# - RAW_WEBSOCKET: the raw WebSocket lines received by each bot
#   (logs/<fmt>/battle_<n>/client_{1,2}_raw.txt). Disabling this also
#   disables archiving failed battles (no raw log to keep).
# - EVENT_STACK: the event stack built from those messages (events.json).
# - PARSER_BATTLE_STATE: the SDK battle state at every turn
#   (battle_states.json).
# - SHOWDOWN_BATTLE_STATE: the custom Showdown battle state (the
#   |battlestate| payload) at every turn (showdown_states.json).
LOG_RAW_WEBSOCKET = True
LOG_EVENT_STACK = True
LOG_PARSER_BATTLE_STATE = True
LOG_SHOWDOWN_BATTLE_STATE = True

# Log level of the raw per-client protocol logs. Anything from
# showdown_sdk.logger (DEBUG, TRACE, ...).
RAW_LOG_LEVEL = TRACE


# File that failures are appended to (relative to cwd).
ERROR_LOG = Path("simulation_errors.log")

# Sleep between formats / overnight cycles, seconds.
SLEEP_BETWEEN_RUNS_SECONDS = 5.0

# ======================================================================
# End of configuration
# ======================================================================


def use_teams(fmt: str) -> bool:
    if GENERATE_TEAMS is None:
        return "randombattle" not in fmt
    return GENERATE_TEAMS


def write_error(error: str) -> None:
    with ERROR_LOG.open("a", encoding="utf-8") as file:
        file.write(error)


async def run_pair(
    client_1: Client,
    client_2: Client,
    logs: LogManager,
    fmt: str,
    pair_index: int,
    battle_offset: int,
    battles_per_pair: int,
    progress: tqdm[NoReturn],
    fail_fast: bool,
    team_generator: SampleTeamGenerator | None,
) -> tuple[list[SerializableObject], int]:
    results: list[SerializableObject] = []
    failed_battles = 0

    for i in range(battles_per_pair):
        battle_number = battle_offset + i + 1
        logs.clear_latest_raw_log_path()

        try:
            result = await run_battle(
                client_1,
                client_2,
                fmt=fmt,
                team_generator=team_generator,
                include_events=LOG_EVENT_STACK,
                include_parser_state=LOG_PARSER_BATTLE_STATE,
                include_showdown_state=LOG_SHOWDOWN_BATTLE_STATE,
            )
            if result is not None:
                results.append(result)

        except Exception as exc:
            failed_battles += 1
            raw_log_path = logs.latest_raw_log_path()

            infra = is_infra_failure(exc)

            error = (
                f"\n{'=' * 80}\n"
                f"Battle {battle_number} failed (pair {pair_index + 1})\n"
                f"Format: {fmt}\n"
                f"Cause: {'network/server (infra)' if infra else 'SDK (state check)'}\n"
                f"Client 1 raw log: "
                f"{raw_log_path if raw_log_path is not None else 'not available'}\n"
                f"{traceback.format_exc()}"
            )

            print()  # print file here
            print(error)

            await asyncio.to_thread(write_error, error)

            if infra:
                # Networking / server issue: nothing to validate against the
                # SDK parser, so don't archive (or abort under fail_fast).
                if fail_fast:
                    print(
                        "Battle failed due to network/server issue; "
                        + "continuing despite fail_fast"
                    )
                continue

            if raw_log_path is not None:
                try:
                    await asyncio.to_thread(
                        write_failure_outputs,
                        client_2,
                        include_events=LOG_EVENT_STACK,
                        include_parser_state=LOG_PARSER_BATTLE_STATE,
                        include_showdown_state=LOG_SHOWDOWN_BATTLE_STATE,
                    )
                except Exception:  # noqa: BLE001
                    print("write_failure_outputs failed:")
                    print(traceback.format_exc())

                try:
                    archive_path = await asyncio.to_thread(
                        save_failed_battle,
                        fmt,
                        raw_log_path.parent.name,
                        logs_root=LOGS_ROOT,
                    )
                    if archive_path is not None:
                        print(f"Failed battle archived to {archive_path}")
                    else:
                        print(
                            "Failed battle archive skipped: "
                            + f"{LOGS_ROOT / fmt / raw_log_path.parent.name} not found"
                        )
                except Exception:  # noqa: BLE001
                    print("Failed to archive failed battle:")
                    print(traceback.format_exc())

            if fail_fast:
                raise

        finally:
            progress.update(1)

    return results, failed_battles


async def run_format(
    fmt: str, fail_fast: bool = False
) -> tuple[list[SerializableObject], int]:
    """Spin up PLAYER_COUNT clients and run all pairs concurrently."""
    pair_count = PLAYER_COUNT // 2
    battles_per_pair = BATTLE_COUNT // pair_count

    clients: list[Client] = []
    log_managers: list[LogManager] = []

    for i in range(1, PLAYER_COUNT + 1):
        tag = f"BOT{i}"
        logs = LogManager(tag=tag)

        output_directory = LOGS_ROOT / fmt
        client_role = "client_1" if i % 2 == 1 else "client_2"

        if LOG_RAW_WEBSOCKET:
            logs.add_handler(
                create_battle_file_handler(
                    output_directory,
                    level=RAW_LOG_LEVEL,
                    filename=f"{client_role}_raw.txt",
                ),
                loggers="protocol",
            )

        client = Client(
            WEBSOCKET_URL,
            combat_handler=RandomMoveCombatHandler(),
            log_manager=logs,
        )
        clients.append(client)
        log_managers.append(logs)

    failed_battles = 0
    results: list[SerializableObject] = []

    team_generator: SampleTeamGenerator | None = None
    if use_teams(fmt):
        team_generator = SampleTeamGenerator(TEAM_SEED)

    start_file_io_worker()
    try:
        await asyncio.gather(*(client.connect() for client in clients))

        await asyncio.gather(
            *(
                client.login(f"BOT{i}")
                for i, client in enumerate(clients, start=1)
            )
        )
        for i, client in enumerate(clients, start=1):
            print(f"client {i} is connected", client.username)

        t0 = perf_counter()

        progress = tqdm(
            total=BATTLE_COUNT, desc=fmt, unit="battle", dynamic_ncols=True
        )

        # Pair up clients: (0,1), (2,3), ... and run each pair concurrently.
        pair_tasks: list[
            asyncio.Task[tuple[list[SerializableObject], int]]
        ] = []

        for pair_index in range(pair_count):
            client_1 = clients[pair_index * 2]
            client_2 = clients[pair_index * 2 + 1]
            battle_offset = pair_index * battles_per_pair

            pair_tasks.append(
                asyncio.create_task(
                    run_pair(
                        client_1,
                        client_2,
                        fmt=fmt,
                        logs=log_managers[pair_index * 2],
                        pair_index=pair_index,
                        battle_offset=battle_offset,
                        battles_per_pair=battles_per_pair,
                        progress=progress,
                        fail_fast=fail_fast,
                        team_generator=team_generator,
                    )
                )
            )

        try:
            pair_results = await asyncio.gather(*pair_tasks)
        except BaseException:
            for task in pair_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pair_tasks, return_exceptions=True)
            raise
        finally:
            progress.close()

        for pair_results_list, pair_failed in pair_results:
            results.extend(pair_results_list)
            failed_battles += pair_failed

        successful_battles = len(results)
        print_results(fmt, t0, results, successful_battles, failed_battles)

    finally:
        stop_file_io_worker()
        await asyncio.gather(
            *(client.close() for client in clients), return_exceptions=True
        )

    return results, failed_battles


def print_results(
    fmt: str,
    t0: float,
    results: list[SerializableObject],
    successful_battles: int,
    failed_battles: int,
) -> None:
    if successful_battles <= 0:
        return
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

    print(f"\nSimulation for {fmt} complete in {perf_counter() - t0}s")
    print(f"Successful battles: {successful_battles}")
    print(f"Failed battles: {failed_battles}")
    print(
        f"Average battle duration: {total_duration / successful_battles:.6f}s"
    )
    average_turn = total_duration / total_turns if total_turns else 0.0
    print(f"Average time per turn: {average_turn:.6f}s")


def wipe_fmt_logs(fmt: str) -> None:
    shutil.rmtree(LOGS_ROOT / fmt, ignore_errors=True)


async def run_once(fail_fast: bool = False) -> None:
    for fmt in FORMATS:
        await run_format(fmt, fail_fast=fail_fast)
        await asyncio.sleep(SLEEP_BETWEEN_RUNS_SECONDS)


async def run_overnight() -> None:
    """Run cycles of run_format with fail_fast across all FORMATS forever."""
    cycle = 0
    while True:
        cycle += 1
        for fmt in FORMATS:
            print(f"\n=== cycle {cycle} | {fmt} ===")
            results: list[SerializableObject] = []
            try:
                results, failed = await run_format(fmt, fail_fast=True)
                completed = True
            except Exception:  # noqa: BLE001 # keep running overnight
                traceback.print_exc()
                failed = -1
                completed = False
            finally:
                wipe_fmt_logs(fmt)

            if completed:
                print(
                    f"cycle {cycle} {fmt}: {len(results)} battles, {failed} failed"
                )
            else:
                print(
                    f"cycle {cycle} {fmt}: aborted on error, "
                    + "failed battle archived in tests/sample_battles_auto"
                )

            await asyncio.sleep(SLEEP_BETWEEN_RUNS_SECONDS)


async def main(mode: str) -> None:
    global LOG_RAW_WEBSOCKET
    global LOG_EVENT_STACK
    global LOG_PARSER_BATTLE_STATE
    global LOG_SHOWDOWN_BATTLE_STATE

    if mode == "overnight":
        LOG_RAW_WEBSOCKET = True
        LOG_EVENT_STACK = False
        LOG_PARSER_BATTLE_STATE = False
        LOG_SHOWDOWN_BATTLE_STATE = True
        await run_overnight()
    else:
        await run_once(fail_fast=False)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"

    t0 = perf_counter()
    try:
        asyncio.run(main(mode))
    except KeyboardInterrupt:
        pass
    print("Took ", perf_counter() - t0)
