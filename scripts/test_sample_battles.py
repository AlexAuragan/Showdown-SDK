"""Offline regression harness: replay every sample battle log into the parser.

Feeds every raw log found in ``tests/sample_battles/<fmt>/`` through the
reusable replay core (``tests/replay.py``), which is also what the pytest
regression suite calls directly. This script keeps only the CLI loops and the
file-writing behavior (``write_battle_outputs``) — see scripts/utils.py.

A file passes when every line is either parsed without error or rejected by
one of the tolerated per-line errors (``InvalidActionError`` /
``ObsoleteRequestIdError``), the whole battle finishes through
``parser.finish()`` with no pending messages, and there is no
``UnhandledEvent`` in the final history. Every Showdown state comparison
(``check_battle_state_against_showdown()``) is a hard assertion — see
``tests/replay.py``.

Usage:
    uv run scripts/test_sample_battles.py [format ...]
"""

import sys
import traceback
from pathlib import Path

from scripts.utils import write_battle_outputs
from tests.replay import (
    NotABattleLogFile,
    ReplayError,
    ReplayResult,
    replay_battle_raw,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SAMPLE_DIRECTORIES = (PROJECT_ROOT / "tests" / "sample_battles",)


def replay_battle(path: Path) -> int:
    """CLI wrapper around :func:`tests.replay.replay_battle_raw`.

    Keeps the historical contract used by scripts/test_sample_auto.py:
    replays one battle log (raising on failure) and writes the same
    ``battle_states.json`` a live battle would produce next to the raw log.
    Returns the number of Showdown-state checks that were verified (0 for
    older logs that did not record ``|battlestate|`` frames).
    """
    result = replay_battle_raw(path)

    # Emulate finish_battle()'s snapshot so write_battle_outputs produces the
    # same battle_states.json as a live battle.
    manager = result.client.battle_manager
    manager.last_battle_turn_states = list(manager.turn_start_states)

    write_battle_outputs(
        result.client,
        path.parent,
        include_events=False,
        include_parser_state=True,
        include_showdown_state=False,
    )

    return result.showdown_state_checks


__all__ = [
    "PROJECT_ROOT",
    "SAMPLE_DIRECTORIES",
    "NotABattleLogFile",
    "ReplayError",
    "ReplayResult",
    "collect_files",
    "main",
    "replay_battle",
]


def collect_files(format_name: str) -> list[Path]:
    files: list[Path] = []
    for root in SAMPLE_DIRECTORIES:
        format_dir = root / format_name
        if not format_dir.is_dir():
            continue
        files.extend(sorted(format_dir.rglob("*.txt")))
    return files


def main() -> int:
    formats = sys.argv[1:]
    if not formats:
        formats = sorted(
            {
                path.name
                for root in SAMPLE_DIRECTORIES
                if root.exists()
                for path in root.iterdir()
                if path.is_dir()
            }
        )

    if not formats:
        print("No sample battle formats found")
        return 1

    total_ok = 0
    total_failed = 0
    total_checks = 0

    for format_name in formats:
        files = collect_files(format_name)
        ok_count = 0
        format_checks = 0
        failures: list[tuple[Path, str]] = []

        for path in files:
            try:
                format_checks += replay_battle(path)
            except NotABattleLogFile:
                continue  # e.g. *_info.txt debug logs
            except Exception:  # noqa: BLE001
                captured = traceback.format_exc()
                failures.append((path, captured))
                print(f"FAIL {path}: {captured.splitlines()[-1]}")
            else:
                ok_count += 1

        total_ok += ok_count
        total_checks += format_checks
        total_failed += len(failures)

        if failures:
            print(f"{format_name}: {len(failures)} failed")

        print(
            f"{format_name}: {ok_count}/{len(files)} replayed, "
            + f"{format_checks} Showdown-state checks verified"
            + (f" ({len(failures)} failed)" if failures else "")
        )

    print(
        f"\nTOTAL: {total_ok}/{total_ok + total_failed} passed, "
        + f"{total_checks} Showdown-state checks"
    )
    return 0 if total_failed == 0 and total_ok > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
