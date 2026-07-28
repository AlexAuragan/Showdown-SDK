#!/usr/bin/env python3
"""Generate the battle-state JSON snapshots used by the parser tests.

For each (gen, log, stop_at) point below, replay the log through the parser and
dump BattleState.to_json() into tests/battle_states/<gen>/<name>.json. The
chosen logs are first copied into tests/raw/<gen>/ so tests are self-contained.

Run after regenerating raw logs with test.py:

    uv run python -m tests.generate_snapshots

Re-runs are idempotent: re-emitting a snapshot overwrites the existing file, so
this is also how you refresh expectations after an intentional parser change.
"""

import shutil
from pathlib import Path

from tests.conftest import replay_log

ROOT = Path(__file__).resolve().parent
RAW_SRC = ROOT.parent / "logs" / "battle" / "raw"

# (gen, log filename, snapshot name, stop_at index into the stripped line list).
# Points are picked mid-battle to exercise statuses, boosts, faints, reveals and
# (for gen2) weather / items.
POINTS = [
    # --- Gen 1 ---
    ("gen1randombattle", "battle-gen1randombattle-7790.txt", "7790_early", 45),
    ("gen1randombattle", "battle-gen1randombattle-7790.txt", "7790_boosts", 141),
    ("gen1randombattle", "battle-gen1randombattle-7790.txt", "7790_status", 330),
    ("gen1randombattle", "battle-gen1randombattle-7726.txt", "7726_early", 31),
    ("gen1randombattle", "battle-gen1randombattle-7726.txt", "7726_status", 228),
    ("gen1randombattle", "battle-gen1randombattle-7728.txt", "7728_early", 31),
    ("gen1randombattle", "battle-gen1randombattle-7728.txt", "7728_boosts", 166),
    ("gen1randombattle", "battle-gen1randombattle-7710.txt", "7710_early", 31),
    ("gen1randombattle", "battle-gen1randombattle-7710.txt", "7710_faints", 84),
    ("gen1randombattle", "battle-gen1randombattle-7710.txt", "7710_boosts", 263),
    # --- Gen 2 ---
    # Weather (SunnyDay) active mid-battle; also has a Thief item reveal.
    ("gen2randombattle", "battle-gen2randombattle-7810.txt", "7810_weather", 250),
    ("gen2randombattle", "battle-gen2randombattle-7810.txt", "7810_weather_late", 444),
    # Weather set late; snapshot right after it starts.
    ("gen2randombattle", "battle-gen2randombattle-7827.txt", "7827_weather", 1500),
    # Activate (Struggle) hot without weather.
    ("gen2randombattle", "battle-gen2randombattle-7827.txt", "7827_activate", 1120),
    # Weather + faints/boosts.
    ("gen2randombattle", "battle-gen2randombattle-7817.txt", "7817_weather", 360),
    # Two Thief item reveals.
    ("gen2randombattle", "battle-gen2randombattle-7812.txt", "7812_item", 270),
]

# Logs we keep in tests/raw/ (deduped), per gen.
KEEP_LOGS = sorted({(gen, name) for gen, name, _, _ in POINTS})


def main() -> None:
    for gen, name in KEEP_LOGS:
        raw_dst = ROOT / "raw" / gen
        raw_dst.mkdir(parents=True, exist_ok=True)
        src = RAW_SRC / name
        # logs/battle/raw/ only holds the most recently run format, so earlier
        # gens may already live only under tests/raw/<gen>/ -- keep them as-is.
        if not src.exists():
            if not (raw_dst / name).exists():
                raise FileNotFoundError(src)
            continue
        shutil.copy2(src, raw_dst / name)

    for gen, log_name, snap_name, stop_at in POINTS:
        log_path = ROOT / "raw" / gen / log_name
        client = replay_log(log_path, stop_at=stop_at)
        state_json = client.combat_handler.battle_state.to_json()
        states_dst = ROOT / "battle_states" / gen
        states_dst.mkdir(parents=True, exist_ok=True)
        out = states_dst / f"{snap_name}.json"
        out.write_text(state_json, encoding="utf-8")
        print(f"wrote {out.relative_to(ROOT)} ({len(state_json)} bytes) <- {log_name}@{stop_at}")


if __name__ == "__main__":
    main()
