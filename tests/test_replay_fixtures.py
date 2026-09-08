"""Regression boundary tests for the replay/oracle system.

Every curated battle under ``tests/replay_fixtures/`` is replayed through the
same reusable core the CLI scripts use (``tests/replay.py``):

- the replay must complete without any error (unexpected parser exceptions,
  unhandled protocol events, inconsistent parser state, pending messages and
  Showdown ``rqid`` desyncs all fail the replay), and
- every Showdown state comparison (``check_battle_state_against_showdown``)
  is a hard assertion inside the core; a battle that recorded
  ``|battlestate|`` frames must therefore verify at least one state check.

A few important fixtures additionally have a *golden event stream* (JSON,
produced with the existing ``BaseEvent.to_dict()`` serialization). Golden
tests only detect semantic event regressions; the Showdown state comparison
inside the replay remains the primary oracle. They are only attached to the
client_1 logs listed in :data:`GOLDEN_FIXTURES`, not to every fixture.

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

# Every battle sibling raw log must equally replay, but only one parameter
# entry per battle directory keeps the pytest id readable.


def battle_paths() -> list[Path]:
    root = FIXTURE_DIRECTORY
    paths: list[Path] = []
    for fmt_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        for battle_dir in sorted(path for path in fmt_dir.iterdir() if path.is_dir()):
            logs = sorted(battle_dir.glob("client_*_raw.txt"))
            if not logs:
                raise AssertionError(f"No client_*_raw.txt in fixture {battle_dir}")
            paths.append(logs[0])
    return paths


def replay_companion(path: Path) -> None:
    """Replay the *other* client log of the same battle directory, if any."""
    battle_dir = path.parent
    for companion in sorted(battle_dir.glob("client_*_raw.txt")):
        if companion == path:
            continue
        replay_battle_raw(companion)


def test_all_curated_fixtures_replay_cleanly() -> None:
    """Every curated fixture must replay with a verified Showdown state check.

    This is the CI gate required of the curated fixture directory: no
    unexpected parser exceptions, no unhandled protocol events, no
    inconsistent parser state, and no SDK/Showdown divergence — any of those
    raises inside ``replay_battle_raw``.
    """
    paths = battle_paths()
    assert paths, "no curated replay fixtures found"

    verified_checks = 0
    for path in paths:
        result = replay_battle_raw(path)
        replay_companion(path)
        if result.has_battlestate_frames:
            expected = "at least one Showdown state check"
            assert result.showdown_state_checks > 0, (
                f"{path}: {result.showdown_state_checks} yet {expected}"
            )
        verified_checks += result.showdown_state_checks

    assert verified_checks > 0, "expected at least one Showdown state check"


def test_replay_client_1_gen1ou() -> None:
    result = replay_battle_raw(
        FIXTURE_DIRECTORY / "gen1ou/battle_612889_maybetrapped/client_1_raw.txt"
    )
    replay_companion(result.path)
    assert result.has_battlestate_frames
    assert result.showdown_state_checks >= 1
    assert result.username
    assert result.frame_count >= 1
    assert result.line_count >= result.frame_count


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
