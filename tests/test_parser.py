"""Parser tests.

Two layers:

* Snapshot (unit) tests -- replay a recorded single-client log up to a chosen
  point and assert `BattleState.to_json()` equals the saved JSON in
  tests/battle_states/<gen>/. These lock in that the parser produces exactly
  the expected state (team, enemy team, statuses, boosts, HP, weather, items,
  ...) for that moment.
* End-to-end tests -- replay a full log start to finish and assert the parser
  runs without raising and ends with a fully populated battle (6 vs 6).
"""


import asyncio
import json
from pathlib import Path

import pytest

from python_showdown.classes.client.parser import LogHandler
from tests.conftest import FakeClient, replay_log

TESTS_DIR = Path(__file__).resolve().parent
RAW_ROOT = TESTS_DIR / "raw"
STATES_ROOT = TESTS_DIR / "battle_states"

# (gen, log filename, snapshot name, stop_at) -- must match
# tests/generate_snapshots.py.
SNAPSHOTS = [
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
    ("gen2randombattle", "battle-gen2randombattle-7810.txt", "7810_weather", 250),
    ("gen2randombattle", "battle-gen2randombattle-7810.txt", "7810_weather_late", 444),
    ("gen2randombattle", "battle-gen2randombattle-7827.txt", "7827_weather", 1500),
    ("gen2randombattle", "battle-gen2randombattle-7827.txt", "7827_activate", 1120),
    ("gen2randombattle", "battle-gen2randombattle-7817.txt", "7817_weather", 360),
    ("gen2randombattle", "battle-gen2randombattle-7812.txt", "7812_item", 270),
]

# Logs replayed in full for the end-to-end test.
E2E_LOGS = sorted({(gen, name) for gen, name, _, _ in SNAPSHOTS})


@pytest.mark.parametrize("gen,log_name,snapshot_name,stop_at", SNAPSHOTS)
def test_battle_state_matches_snapshot(gen, log_name, snapshot_name, stop_at):
    log_path = RAW_ROOT / gen / log_name
    expected_path = STATES_ROOT / gen / f"{snapshot_name}.json"

    client = replay_log(log_path, stop_at=stop_at)
    actual = json.loads(client.combat_handler.battle_state.to_json())
    expected = json.loads(expected_path.read_text(encoding="utf-8"))

    assert actual == expected, f"state diverged at {log_name}@{stop_at}"


@pytest.mark.parametrize(
    "line,expected",
    [
        ("|-weather|SunnyDay", "SunnyDay"),
        ("|-weather|SunnyDay|[upkeep]", "SunnyDay"),
        ("|-weather|RainDance|[from] move: Rain Dance", "RainDance"),
        ("|-weather|none", None),
    ],
)
def test_weather_string_parsing(line, expected):
    """A weather line sets the weather to exactly the weather name, regardless
    of trailing [upkeep] or [from] clauses."""
    client = FakeClient()
    handler = LogHandler()
    asyncio.run(handler.handle_line(client, line))  # type: ignore[arg-type]
    assert client.combat_handler.battle_state.weather == expected


@pytest.mark.parametrize("gen,log_name", E2E_LOGS)
def test_replay_full_log_without_error(gen, log_name):
    """A whole battle replays without raising and ends 6-vs-6."""
    client = replay_log(RAW_ROOT / gen / log_name)
    state = client.combat_handler.battle_state
    assert len(state.team) == 6
    known_enemies = [p for p in state.enemy_team if str(p.id) != "unknown"]
    assert len(known_enemies) == 6
