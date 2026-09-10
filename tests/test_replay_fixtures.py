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
"""

from pathlib import Path

import pytest

from tests.replay import FIXTURE_DIRECTORY, replay_battle_raw


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
        for battle_dir in sorted(
            path for path in fmt_dir.iterdir() if path.is_dir()
        ):
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
