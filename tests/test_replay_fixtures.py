"""Regression boundary tests for the replay/oracle system.

Every curated raw log under ``tests/sample_battles/`` — both client
perspectives of every battle — is replayed through the same reusable core
the CLI scripts use (``tests/replay.py``):

- the replay must complete without any error (unexpected parser exceptions,
  unhandled protocol events, inconsistent parser state, pending messages and
  Showdown ``rqid`` desyncs all fail the replay), and
- every Showdown state comparison (``check_battle_state_against_showdown``)
  is a hard assertion inside the core; a log that recorded
  ``|battlestate|`` frames must therefore verify at least one state check —
  asserted independently per log, so a parser that silently stops emitting
  ``CustomShowdownBattleStateEvent`` for one side cannot go unnoticed.

A few important fixtures additionally have a *golden event stream* (JSON,
produced with the existing ``BaseEvent.to_dict()`` serialization; the
recorded Showdown oracle payload is excluded). Golden tests only detect
semantic event regressions; the Showdown state comparison inside the replay
remains the primary oracle. A battle gets a golden test by storing a
``<log>_golden_events.json`` file next to the raw log — no code change
needed; battles without one are simply not golden-checked.

A stale golden (raw log renamed/removed but golden left behind) fails
during collection.

Set ``SHOWDOWN_REPLAY_UPDATE_GOLDEN=1`` to (re)generate the golden files
after an intentional, verified semantic change — never to make a failing
test pass.
"""

import json
import os
from pathlib import Path

import pytest

from tests.replay import FIXTURE_DIRECTORY, replay_battle_raw

GOLDEN_UPDATE_ENV = "SHOWDOWN_REPLAY_UPDATE_GOLDEN"


def raw_log_ids() -> list[str]:
    return [
        f"{path.parent.parent.name}-{path.parent.name}-{path.name.removesuffix('_raw.txt')}"
        for path in raw_log_paths()
    ]


def raw_log_paths() -> list[Path]:
    """Every curated raw log: both client perspectives of every battle."""
    root = FIXTURE_DIRECTORY
    paths: list[Path] = []
    for fmt_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        for battle_dir in sorted(path for path in fmt_dir.iterdir() if path.is_dir()):
            logs = sorted(battle_dir.glob("client_*_raw.txt"))
            assert logs, f"No client_*_raw.txt in fixture {battle_dir}"
            paths.extend(logs)
    return paths


def golden_paths() -> list[Path]:
    """Every ``*_golden_events.json`` stored next to a curated raw log."""
    return sorted(FIXTURE_DIRECTORY.rglob("client_*_raw_golden_events.json"))


@pytest.mark.parametrize("path", raw_log_paths(), ids=raw_log_ids())
def test_replayed_log_verifies_oracle(path: Path) -> None:
    result = replay_battle_raw(path)
    assert result.username
    assert result.frame_count >= 1
    assert result.line_count >= result.frame_count
    assert result.final_state is not None
    if result.has_battlestate_frames:
        assert result.showdown_state_checks > 0, (
            f"{path}: replay completed without a single Showdown state "
            + "check although the log records |battlestate| frames"
        )


def test_no_stale_golden_files() -> None:
    log_names = {path.name for path in raw_log_paths()}
    for golden_path in golden_paths():
        stem = golden_path.name.removesuffix("_golden_events.json")
        assert stem + ".txt" in log_names, (
            f"stale golden without matching raw log: {golden_path}"
        )


@pytest.mark.parametrize("golden_path", golden_paths())
def test_replay_events_match_golden(golden_path: Path) -> None:
    """Compare semantic event order/contents against the recorded golden."""
    log_name = golden_path.name.removesuffix("_golden_events.json") + ".txt"
    path = golden_path.parent / log_name
    result = replay_battle_raw(path, collect_events=True)
    assert result.events is not None

    actual = json.dumps(result.events, indent=1) + "\n"

    if os.environ.get(GOLDEN_UPDATE_ENV) == "1":
        # Developer workflow only (like the promotion script); pytest never
        # just overwrites a failing fixture silently in normal runs.
        golden_path.write_text(actual, encoding="utf-8")
        pytest.fail(f"golden updated for {path}, re-run without the env var")

    expected = golden_path.read_text(encoding="utf-8")
    assert actual == expected, (
        f"semantic event stream changed for {path.relative_to(FIXTURE_DIRECTORY)}; "
        + "investigate before regenerating the golden"
    )
