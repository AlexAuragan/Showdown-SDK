"""Reusable raw-battle replay core for the oracle/regression boundary.

This is the replay logic extracted from ``scripts/test_sample_battles.py``
(scripts/test_sample_battles.py). It feeds every raw log found in
``tests/sample_battles/<fmt>/`` and ``tests/sample_battles_auto/<fmt>/``
through ``Parser.handle_line`` in a synchronous loop (no websocket, no
server), replicating the small amount of bookkeeping
``Client._receive_loop`` performs on each event:

- ``apply_battle_runtime_event(battle_manager, event)``
- ``LobbyEvent.update_client(client)``
- ``expecting_battle_room`` bookkeeping for ``BattleStartEvent``

What a replay verifies (these are hard assertions):
- every line is either parsed without error or rejected by one of the
  tolerated per-line errors (``InvalidActionError`` / ``ObsoleteRequestIdError``)
- no ``UnhandledEvent`` is produced by the parser
- frame-end synchronization behaves exactly like the live receive loop:
  the Showdown ``|battlestate|`` answer matches the pending ``rqid`` and
  ``check_battle_state_against_showdown()`` accepts the SDK state
- ``parser.finish()`` leaves no pending messages

The core :func:`replay_battle_raw` does not write any files; callers get a
:class:`ReplayResult` and decide what (if anything) to persist. The CLI
scripts keep the file-writing behavior (scripts/utils.py).

Usage from pytest::

    from tests.replay import replay_battle_raw

    result = replay_battle_raw(path)
    assert result.showdown_state_checks > 0
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from python_showdown.classes.client.client import Client
from python_showdown.classes.parser.events import (
    BattleEvent,
    DiscardedEvent,
    LobbyEvent,
    UnhandledEvent,
)
from python_showdown.classes.parser.events.base import BaseEvent
from python_showdown.classes.parser.events.battle import (
    BattleStartEvent,
    CustomShowdownBattleStateEvent,
)
from python_showdown.classes.parser.exceptions import (
    InvalidActionError,
    ObsoleteRequestIdError,
)
from python_showdown.classes.parser.protocol import extract_protocol_line
from python_showdown.classes.parser.reducers.battle import apply_battle_runtime_event
from python_showdown.models.sdk.battle_state import BattleState
from python_showdown.models.sdk.check import check_battle_state_against_showdown
from python_showdown.utils.serialization import Serializable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIRECTORIES = (
    PROJECT_ROOT / "tests" / "sample_battles",
    PROJECT_ROOT / "tests" / "sample_battles_auto",
)
# Curated, git-tracked regression corpus replayed by pytest (tests/).
FIXTURE_DIRECTORY = PROJECT_ROOT / "tests" / "sample_battles"


class ReplayError(Exception):
    """A sample battle could not be replayed cleanly (hard assertion)."""


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
        slot, name = next(iter(slots.items()))
        return name, slot
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


@dataclass
class ReplayResult:
    """Everything a raw replay needs to know, without any file writes.

    - ``client``: the offline client (legacy hook for the CLI wrapper and
      its file-writing path). Prefer ``final_state``: it will keep working
      when the ``Client -> Parser -> Manager -> BattleState`` coupling is
      restructured (Step 2).
    - ``final_state``: the final SDK battle state
      (``client.battle_manager.battle_state``).
    - ``events``: serialized emitted events, present only when
      ``replay_battle_raw(..., collect_events=True)`` was requested.
    - ``snapshots``: serialized SDK battle state at each Showdown state
      check, present only when ``record_snapshots=True`` was requested.
    - ``showdown_state_checks``: number of Showdown state comparisons that
      were verified (0 for older logs that did not record ``|battlestate|``
      frames).
    """

    path: Path
    client: Client
    showdown_state_checks: int
    has_battlestate_frames: bool
    events: list[Serializable] | None = None
    snapshots: list[Serializable] | None = None

    @property
    def final_state(self) -> BattleState:
        """Final SDK battle state, independent of the Client/Manager chain."""
        return self.client.battle_manager.battle_state

    player_id: str = ""
    username: str = ""
    frame_count: int = 0
    line_count: int = 0
    tolerated_errors: dict[str, int] = field(default_factory=dict)


def replay_battle_raw(
    path: Path,
    *,
    collect_events: bool = False,
    record_snapshots: bool = False,
) -> ReplayResult:
    """Replay a single battle log through the parser. Raises on failure.

    This is the oracle-oracle-free inner loop of
    ``scripts/test_sample_battles.py``: it performs no file writes and no
    promotion; file outputs remain a CLI-script concern (see
    ``scripts/test_sample_battles.replay_battle``).
    """
    raw_lines = path.read_text(encoding="utf-8").splitlines()

    if not any(line.split(" ", 2)[-1].startswith(">battle-") for line in raw_lines):
        raise NotABattleLogFile(f"{path.name}: no battle room frame")

    username, player_id = _detect_player(raw_lines)

    client = Client("unused://offline-replay")
    manager = client.battle_manager
    # The logs are recorded from one client's perspective; setting the
    # username lets PlayerEvent resolve which side we are playing from.
    client.username = username
    client.parser.expecting_battle_room = True
    parser = client.parser

    has_battlestate_frames = any(
        line.split(" ", 2)[-1].startswith("|battlestate|") for line in raw_lines
    )
    state_check_count = 0
    snapshots: list[Serializable] | None = [] if record_snapshots else None
    events: list[Serializable] | None = [] if collect_events else None
    tolerated_errors: dict[str, int] = {}
    frames = split_frames(raw_lines)
    line_count = sum(len(frame) for frame in frames)

    def process_events(
        parsed_events: list[BaseEvent],
        line_number: int,
    ) -> bool:
        received_custom_state = False

        # Client._receive_loop does this before runtime event handling.
        manager.battle_state.history.extend(parsed_events)

        for event in parsed_events:
            if events is not None and not isinstance(
                event,
                CustomShowdownBattleStateEvent,
            ):
                events.append(event.to_dict())

            if isinstance(event, UnhandledEvent):
                raise ReplayError(
                    f"{path.name}:{line_number} produced an UnhandledEvent: "
                    + f"{event.raw!r}"
                )

            # Do not make this an elif.
            # CustomShowdownBattleStateEvent is also a BattleEvent.
            if isinstance(event, CustomShowdownBattleStateEvent):
                received_custom_state = True

            if isinstance(event, BattleEvent):
                apply_battle_runtime_event(manager, event)
                if isinstance(event, BattleStartEvent):
                    if manager.room_id != parser.last_message_room_id:
                        raise ReplayError(
                            "BattleStartEvent established the wrong room: "
                            + f"{manager.room_id!r}"
                        )

                    client.parser.expecting_battle_room = False

            elif isinstance(event, LobbyEvent):
                event.update_client(client)

            elif isinstance(event, DiscardedEvent):
                pass

            else:
                raise ReplayError(
                    f"{path.name}:{line_number} got unknown {type(event)}"
                )

        return received_custom_state

    def finish_frame(received_custom_state: bool) -> None:
        nonlocal state_check_count

        # Mirror Client._receive_loop choice-retry restoration.
        if (
            manager.request_id is None
            and manager.choice_rejected
            and manager.last_request_id is not None
            and (
                manager.retry_rqid != manager.last_request_id
                or manager.retry_count < 5
            )
        ):
            if manager.retry_rqid != manager.last_request_id:
                manager.retry_rqid = manager.last_request_id
                manager.retry_count = 0

            manager.retry_count += 1
            manager.request_id = manager.last_request_id

        # Same branch priority as Client._receive_loop.
        if manager.requires_team_preview:
            # Simulate sending `/choose team ...`.
            # No network operation is performed by replay.
            manager.requires_team_preview = False
            return

        if received_custom_state:
            pending = client.pending_state_request_id

            if pending is None:
                raise ReplayError(
                    "Received Showdown battle state without a pending request"
                )

            if manager.request_id != pending:
                raise ReplayError(
                    "Battle state synchronization failed: "
                    + f"requested rqid={pending}, "
                    + f"current rqid={manager.request_id}"
                )

            check_battle_state_against_showdown(manager.battle_state)
            manager.record_turn_start_state(pending)
            state_check_count += 1

            if snapshots is not None:
                snapshots.append(manager.battle_state.to_dict())

            client.pending_state_request_id = None

            # Simulate Client.act().
            #
            # act() records the rqid being acted on before sending /choose.
            manager.last_request_id = manager.request_id
            manager.request_id = None
            return

        if manager.request_id is None:
            return

        if has_battlestate_frames:
            pending = client.pending_state_request_id

            if pending is None:
                # Simulate get_custom_showdown_battle_state().
                client.pending_state_request_id = manager.request_id
                return

            if manager.request_id != pending:
                raise ReplayError(
                    "Received a different decision while waiting for "
                    + "Showdown state: "
                    + f"pending rqid={pending}, "
                    + f"current rqid={manager.request_id}"
                )

            return

        # Old logs without |battlestate| frames.
        # Simulate act() without performing network I/O.
        manager.last_request_id = manager.request_id
        manager.request_id = None

    for frame in frames:
        received_custom_state = False
        # Reset per-frame state exactly like the live receive loop.
        manager.choice_rejected = False
        client.parser.last_message_room_id = ""

        for line_number, line in enumerate(frame, start=1):
            try:
                parsed_events = parser.handle_line(line, has_log_timestamp=True)
            except ObsoleteRequestIdError as error:
                error.request_id = manager.request_id
                manager.choice_rejected = False

                name = type(error).__name__
                tolerated_errors[name] = tolerated_errors.get(name, 0) + 1
                continue

            except InvalidActionError as error:
                manager.choice_rejected = error.category != "Unavailable choice"

                name = type(error).__name__
                tolerated_errors[name] = tolerated_errors.get(name, 0) + 1
                continue

            if process_events(parsed_events, line_number):
                received_custom_state = True

        finish_frame(received_custom_state)

    parser.finish(player_id)

    if parser.pending_messages:
        pending = "\n".join(message.raw for message in parser.pending_messages)
        raise ReplayError(f"finished with an incomplete group:\n{pending}")

    return ReplayResult(
        path=path,
        client=client,
        showdown_state_checks=state_check_count,
        has_battlestate_frames=has_battlestate_frames,
        events=events,
        snapshots=snapshots,
        player_id=player_id,
        username=username,
        frame_count=len(frames),
        line_count=line_count,
        tolerated_errors=tolerated_errors,
    )
