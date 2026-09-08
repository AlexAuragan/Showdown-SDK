"""Run random-move OU battles all night.

Runs cycles of scripts/generate_battles_ou.run_format with fail_fast enabled:
the first failed battle is archived into tests/sample_battles_auto/<fmt> by
scripts.utils.save_failed_battle, the build is torn down cleanly, the format's
log directory is wiped, and a fresh cycle starts from scratch.

Requires the local Showdown server (WEBSOCKET_URL) to be running:

    uv run scripts/run_ou_overnight.py
"""

import asyncio
import shutil
import traceback
from pathlib import Path

from scripts.generate_battles_ou import FORMATS, run_format

LOGS_ROOT = Path("logs")
SLEEP_BETWEEN_CYCLES_SECONDS = 5.0


def wipe_fmt_logs(fmt: str) -> None:
    shutil.rmtree(LOGS_ROOT / fmt, ignore_errors=True)


async def main() -> None:
    cycle = 0
    while True:
        cycle += 1
        for fmt in FORMATS:
            print(f"\n=== cycle {cycle} | {fmt} ===")
            results = []
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
                print(f"cycle {cycle} {fmt}: {len(results)} battles, {failed} failed")
            else:
                print(
                    f"cycle {cycle} {fmt}: aborted on error, "
                    + "failed battle archived in tests/sample_battles_auto"
                )

            await asyncio.sleep(SLEEP_BETWEEN_CYCLES_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
