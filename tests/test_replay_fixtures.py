"""Regression boundary tests for the replay/oracle system.

Every curated raw log under ``tests/replay_fixtures/`` — both client
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
remains the primary oracle. They are only attached to the client_1 logs
listed in :data:`GOLDEN_FIXTURES`, not to every fixture.

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

# Client_1 fixtures with a golden event stream (semantic regression check).
GOLDEN_FIXTURES = (
    # Gen 1 randomly-trapped battle: the original bug this harness tracks.
    "gen1ou/battle_612889_maybetrapped/client_1_raw.txt",
    # Gen 4 battle with a dozen Showdown state checks.
    "gen4randombattle/battle_610012/client_1_raw.txt",
)


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


@pytest.mark.parametrize("relative", GOLDEN_FIXTURES)
def test_replay_events_match_golden(relative: str) -> None:
    """Compare semantic event order/contents against the recorded golden."""
    path = FIXTURE_DIRECTORY / relative
    result = replay_battle_raw(path, collect_events=True)
    assert result.events is not None

    golden_path = path.parent / (path.stem + "_golden_events.json")
    actual = json.dumps(result.events, indent=1) + "\n"

    if os.environ.get(GOLDEN_UPDATE_ENV) == "1":
        # Developer workflow only (like the promotion script); pytest never
        # just overwrites a failing fixture silently in normal runs.
        golden_path.write_text(actual, encoding="utf-8")
        pytest.fail(f"golden updated for {path}, re-run without the env var")

    assert golden_path.is_file(), f"missing golden file {golden_path}"
    expected = golden_path.read_text(encoding="utf-8")
    assert actual == expected, (
        f"semantic event stream changed for {relative}; "
        + "investigate before regenerating the golden"
    )
