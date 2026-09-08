"""Offline regression harness for the auto-collected sample battles only.

Same replay as scripts/test_sample_battles.py, but it only feeds
``tests/sample_battles_auto/<fmt>/``. Battles that replay cleanly are then
promoted into ``tests/sample_battles/<fmt>/`` (the whole battle directory is
moved, keeping client_1/client_2 raw logs and battle_states.json together);
battles that fail stay in place in sample_battles_auto so the bug can be
fixed and this script re-run.

Passing battles are usually duplicates produced by the same underlying bug,
so only the first passing battle per format is promoted; the rest are
deleted to keep ``tests/sample_battles/<fmt>/`` from flooding.

Usage:
    uv run scripts/test_sample_auto.py [format ...]
"""

import shutil
import sys
import traceback
from pathlib import Path

import scripts.test_sample_battles as harness
from scripts.utils import PROJECT_ROOT

AUTO_DIRECTORY = PROJECT_ROOT / "tests" / "sample_battles_auto"
PROMOTE_DIRECTORY = PROJECT_ROOT / "tests" / "sample_battles"


def main() -> int:
    formats = sys.argv[1:]
    if not formats:
        if not AUTO_DIRECTORY.is_dir():
            print("No formats found in tests/sample_battles_auto")
            return 1
        formats = sorted(
            path.name for path in AUTO_DIRECTORY.iterdir() if path.is_dir()
        )

    if not formats:
        print("No formats found in tests/sample_battles_auto")
        return 1

    total_ok = 0
    total_failed = 0
    total_checks = 0
    total_promoted = 0

    for format_name in formats:
        format_dir = AUTO_DIRECTORY / format_name
        if not format_dir.is_dir():
            continue

        files = sorted(format_dir.rglob("*.txt"))
        ok_paths: list[Path] = []
        format_checks = 0
        failures: list[tuple[Path, str]] = []

        for path in files:
            try:
                format_checks += harness.replay_battle(path)
            except harness.NotABattleLogFile:
                continue  # e.g. *_info.txt debug logs
            except Exception:  # noqa: BLE001
                captured = traceback.format_exc()
                failures.append((path, captured))
                print(f"FAIL {path}: {captured.splitlines()[-1]}")
            else:
                ok_paths.append(path)

        # Re-verify the battle's other raw log (client_2) before promoting.
        promoted_paths: list[Path] = []
        for path in ok_paths:
            battle_dir = path.parent
            expected_logs = {p.name for p in battle_dir.glob("client_*_raw.txt")}
            if expected_logs - {path.name}:
                try:
                    harness.replay_battle(next(
                        battle_dir / name for name in expected_logs
                        if name != path.name
                    ))
                except Exception:  # noqa: BLE001
                    captured = traceback.format_exc()
                    failures.append((path, captured))
                    print(f"FAIL {path}: {captured.splitlines()[-1]}")
                    continue
            promoted_paths.append(path)

        # Promote passing battles into tests/sample_battles/<fmt>.
        # A battle directory can appear multiple times in promoted_paths
        # (once per client log), so only consider each directory once.
        # Passing battles are usually near-identical duplicates created by
        # the same bug, so only promote the first one and delete the rest
        # to keep tests/sample_battles from flooding. Failing battles stay
        # in sample_battles_auto until the bug is fixed.
        for index, battle_dir in enumerate(dict.fromkeys(
            path.parent for path in promoted_paths
        )):
            destination = PROMOTE_DIRECTORY / format_name
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / battle_dir.name
            suffix = 1
            while target.exists():
                suffix += 1
                target = destination / f"{battle_dir.name}_{suffix}"
            if index == 0:
                shutil.move(str(battle_dir), str(target))
                print(
                    f"PROMOTED {battle_dir.name} -> "
                    f"{target.relative_to(PROJECT_ROOT)}"
                )
            else:
                shutil.rmtree(battle_dir)
                print(f"REMOVED duplicate {battle_dir.name}")

        total_ok += len(promoted_paths)
        total_checks += format_checks
        total_failed += len(failures)
        total_promoted += len(promoted_paths)

        print(
            f"{format_name}: {len(promoted_paths)}/{len(files)} replayed, "
            + f"{format_checks} Showdown-state checks verified"
            + (f" ({len(failures)} failed)" if failures else "")
        )

    print(
        f"\nTOTAL: {total_ok}/{total_ok + total_failed} passed, "
        + f"{total_checks} Showdown-state checks\n"
        + f"PROMOTED: {total_promoted} battle(s) moved into tests/sample_battles"
    )
    return 0 if total_failed == 0 and total_ok > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
