"""Offline regression harness: replay every sample battle log into the parser.

Feeds every raw log found in ``tests/sample_battles/<fmt>/`` and
``tests/sample_battles_auto/<fmt>/`` through ``Parser.handle_line`` in a
synchronous loop (no websocket, no server), replicating the small amount of
bookkeeping ``Client._receive_loop`` performs on each event:

- ``BattleEvent.update_manager(battle_manager)``
- ``LobbyEvent.update_client(client)``
- ``expecting_battle_room`` bookkeeping for ``BattleStartEvent``

A file passes when every line is either parsed without error or rejected by
one of the tolerated per-line errors (``InvalidActionError`` /
``ObsoleteRequestIdError``), the whole battle finishes through
``parser.finish()`` with no pending messages, and there is no
``UnhandledEvent`` in the final history.

Usage:
    uv run scripts/test_sample_battles.py [format ...]
"""

import json
import sys
import traceback
from pathlib import Path

from python_showdown.classes.client.client import Client
from python_showdown.classes.parser.events import (
    BattleEvent,
    DiscardedEvent,
    LobbyEvent,
    UnhandledEvent,
)
from python_showdown.classes.parser.events.battle import (
    BattleStartEvent,
    CustomShowdownBattleStateEvent,
)
from python_showdown.classes.parser.exceptions import (
    InvalidActionError,
    ObsoleteRequestIdError,
)
from python_showdown.classes.parser.protocol import extract_protocol_line
from python_showdown.models.sdk.check import check_battle_state_against_showdown
from scripts.utils import write_battle_outputs

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIRECTORIES = (
    PROJECT_ROOT / "tests" / "sample_battles",
    PROJECT_ROOT / "tests" / "sample_battles_auto",
)


class ReplayError(Exception):
    """A sample battle could not be replayed."""


class NotABattleLogFile(ReplayError):
    """The file is not a raw battle protocol log (e.g. a debug/info log)."""


def _detect_player(raw_lines: list[str]) -> tuple[str, str]:
    """Return ``(username, player_id)`` of the client that recorded the log.

    The recorder is the side addressed by the first ``|request|`` JSON payload
    (``side.id`` / ``side.name``), e.g. the 'BOT6' / 'p2' pair. Falls back to
    the ``|player|p1|`` name when the log has no request (battle cut short
    before the first turn).
    """
    slots: dict[str, str] = {}
    from_request: tuple[str, str] | None = None

    for line in raw_lines:
        try:
            protocol_line = extract_protocol_line(line, has_log_timestamp=True)
        except ValueError:
            continue

        if protocol_line.startswith("|player|"):
            fields = protocol_line.split("|")
            slot = fields[2].strip()
            name = fields[3].strip() if len(fields) > 3 else ""
            if name:
                slots[slot] = name
        elif protocol_line.startswith("|request|") and from_request is None:
            try:
                payload = json.loads(protocol_line.split("|", 2)[2])
            except ValueError:
                continue
            side: dict[str, str] = payload.get("side") or {}
            side_id = side.get("id")
            side_name = side.get("name")
            if side_id in {"p1", "p2"} and side_name:
                from_request = (side_name, side_id)

    if from_request is not None:
        return from_request

    if slots:
        if "p1" in slots:
            return slots["p1"], "p1"
        return next(iter(slots.items()))

    raise ReplayError("Could not determine which player recorded this log")


def split_frames(raw_lines: list[str]) -> list[list[str]]:
    """Group log lines into websocket frames (each starts with a >room header).

    ``Client._receive_loop`` processes one websocket frame at a time and only
    applies its frame-end logic (choice/state synchronization) once per frame,
    so replay must preserve frame boundaries.
    """
    frames: list[list[str]] = []
    current: list[str] = []

    for line in raw_lines:
        try:
            protocol_line = extract_protocol_line(line, has_log_timestamp=True)
        except ValueError:
            continue

        if protocol_line.startswith(">"):
            if current:
                frames.append(current)
            current = [line]
        else:
            if not current:
                current = [line]
            else:
                current.append(line)

    if current:
        frames.append(current)

    return frames


def replay_battle(path: Path) -> int:
    """Replay a single battle log through the parser. Raises on failure.

    Returns the number of Showdown-state checks that were verified (0 for
    older logs that did not record ``|battlestate|`` frames).
    """
    raw_lines = path.read_text(encoding="utf-8").splitlines()

    if not any(
        line.split(" ", 2)[-1].startswith(">battle-")
        for line in raw_lines
    ):
        raise NotABattleLogFile(f"{path.name}: no battle room frame")

    username, player_id = _detect_player(raw_lines)

    client = Client("unused://offline-replay")
    manager = client.battle_manager
    # The logs are recorded from one client's perspective; setting the
    # username lets PlayerEvent resolve which side we are playing from.
    client.username = username
    client.expecting_battle_room = True
    parser = client.parser

    has_battlestate_frames = any(
        line.split(" ", 2)[-1].startswith("|battlestate|")
        for line in raw_lines
    )
    state_check_count = 0

    def finish_frame(received_custom_state: bool) -> None:
        # Mirrors the frame-end logic of Client._receive_loop: the custom
        # Showdown battle state must answer the request the client is waiting
        # on, and the SDK state is validated against it (models/sdk/check.py).
        nonlocal state_check_count

        if received_custom_state:
            pending = client.pending_state_request_id
            if pending is None:
                raise ReplayError(
                    "Received Showdown battle state without a pending request"
                )
            if manager.request_id != pending:
                raise ReplayError(
                    "Battle state synchronization failed: "
                    + f"requested rqid={pending}, current rqid={manager.request_id}"
                )

            check_battle_state_against_showdown(manager.battle_state)
            manager.record_turn_start_state(pending)
            state_check_count += 1

            client.pending_state_request_id = None
            # act() in the live loop consumes the request.
            manager.request_id = None

        elif has_battlestate_frames and manager.request_id is not None:
            pending = client.pending_state_request_id
            if pending is None:
                # get_custom_showdown_battle_state() in the live loop; the
                # server's response is already part of the log.
                client.pending_state_request_id = manager.request_id
            elif manager.request_id != pending:
                raise ReplayError(
                    "Received a different decision while waiting for "
                    + "Showdown state: "
                    + f"pending rqid={pending}, current rqid={manager.request_id}"
                )

        if manager.requires_team_preview:
            # select_team_order() + /choose team in the live loop.
            manager.requires_team_preview = False

    for frame in split_frames(raw_lines):
        received_custom_state = False
        # Reset per-frame state exactly like the live receive loop.
        manager.choice_rejected = False
        client.parser.last_message_room_id = ""

        for line_number, line in enumerate(frame, start=1):
            try:
                events = parser.handle_line(line, has_log_timestamp=True)
            except (InvalidActionError, ObsoleteRequestIdError):
                # The recorder tried an illegal action; the live receive loop
                # tolerates this too.
                continue

            for event in events:
                if isinstance(event, UnhandledEvent):
                    raise ReplayError(
                        f"{path.name}:{line_number} produced an UnhandledEvent: "
                        + f"{event.raw!r}"
                    )
                elif isinstance(event, CustomShowdownBattleStateEvent):
                    # Must be matched before BattleEvent: it subclasses it.
                    received_custom_state = True
                elif isinstance(event, BattleEvent):
                    event.update_manager(manager)
                    if isinstance(event, BattleStartEvent):
                        if manager.room_id != parser.last_message_room_id:
                            raise ReplayError(
                                "BattleStartEvent established the wrong room: "
                                + f"{manager.room_id!r}"
                            )
                        client.expecting_battle_room = False
                elif isinstance(event, LobbyEvent):
                    event.update_client(client)
                elif isinstance(event, DiscardedEvent):
                    pass
                else:
                    raise ReplayError(
                        f"{path.name}:{line_number} got unknown {type(event)}"
                    )

        finish_frame(received_custom_state)

    parser.finish(player_id)

    if parser.pending_messages:
        pending = "\n".join(message.raw for message in parser.pending_messages)
        raise ReplayError(f"finished with an incomplete group:\n{pending}")

    # Emulate finish_battle()'s snapshot so write_battle_outputs produces the
    # same battle_states.json as a live battle.
    manager.last_battle_turn_states = list(manager.turn_start_states)

    write_battle_outputs(client, path.parent)

    return state_check_count


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
                for root in SAMPLE_DIRECTORIES if root.exists()
                for path in root.iterdir() if path.is_dir()
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
