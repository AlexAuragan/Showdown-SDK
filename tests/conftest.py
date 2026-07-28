"""Test helpers for replaying recorded battle logs through the parser.

The raw logs under tests/raw/ are single-client recordings: test.py only writes
files for one client, so each log is exactly the stream of server messages that
one client saw. Replaying a log therefore feeds the parser the same input it
would see in production, and lets us snapshot `BattleState` at any point and
compare it (==) to a saved JSON.
"""

import asyncio
import re
from pathlib import Path

from python_showdown.classes.client.parser import LogHandler
from python_showdown.classes.combat.random import RandomMoveCombatHandler
from python_showdown.logger import LogManager

# Each log line is prefixed with "<asctime> " by the BattleFileHandler
# formatter. Strip it so only the protocol message remains.
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} ")


def strip_timestamp(line: str) -> str:
    return _TS_RE.sub("", line, count=1)


def battle_room_from_lines(lines: list[str]) -> str:
    for line in lines:
        if line.startswith(">battle-"):
            return line.removeprefix(">")
    raise ValueError("Log has no >battle-<room> line; cannot determine room id")


class _ReadyStub:
    """Stand-in for asyncio.Event used by |updateuser|/|nametaken|."""
    def set(self) -> None: pass
    def clear(self) -> None: pass
    def wait(self): pass


class FakeClient:
    """Minimal stand-in for Client that the LogHandler can drive.

    The room redirect (`>battle-...`) and init (`|init|battle`) lines are logged
    out of order relative to their arrival on the wire, so the replay pre-sets
    `room_id`/`active_battle_room` and skips those two line types, doing the
    combat-handler reset manually. Every other line is fed to the parser in file
    order, which is the order the client processed it.
    """

    def __init__(self, username: str = "BOT1") -> None:
        self.username = username
        self.combat_handler = RandomMoveCombatHandler()
        self.log_manager = LogManager()
        self.log_manager.disable()
        self.log_handler = LogHandler()
        self.room_id = ""
        self.active_battle_room = ""
        self.battle_player_id = ""
        self.turn_count = 0
        self.named = True
        self.formats = []
        self.challenge_future = None
        self.challenged_user = None
        self.ready = _ReadyStub()

    # Stubs for the Client surface that handle_line touches.
    def start_action_timeout(self) -> None:
        pass

    def cancel_action_timeout(self) -> None:
        pass

    def finish_battle(self, winner: str | None) -> None:
        # Keep the final state intact so end-of-battle snapshots are meaningful.
        pass


def load_log_lines(log_path: Path) -> tuple[list[str], str]:
    raw = Path(log_path).read_text(encoding="utf-8").splitlines()
    lines = [strip_timestamp(l) for l in raw if l.strip()]

    room = battle_room_from_lines(lines)
    # Drop the room-management lines whose file ordering isn't the wire order.
    lines = [
        l for l in lines
        if not l.startswith(">battle-") and l != "|init|battle"
    ]

    # Pre-set room state and reset combat exactly as |init|battle would have.
    return lines, room


async def _replay(client: FakeClient, lines: list[str], room: str, stop_at: int | None) -> None:
    client.room_id = room
    client.active_battle_room = room
    client.battle_player_id = ""
    client.combat_handler.reset()

    log_handler = client.log_handler
    for i, line in enumerate(lines):
        if stop_at is not None and i >= stop_at:
            break
        await log_handler.handle_line(client, line)  # type: ignore[arg-type]


def replay_log(log_path: Path, stop_at: int | None = None) -> FakeClient:
    """Replay `log_path` through a fresh FakeClient and return it.

    If `stop_at` is given, only the first `stop_at` lines (after stripping the
    room-management lines) are processed; use this to snapshot state mid-battle.
    """
    client = FakeClient()
    lines, room = load_log_lines(Path(log_path))
    asyncio.run(_replay(client, lines, room, stop_at))
    return client


def log_lines(log_path: Path) -> list[str]:
    """The stripped, room-management-free line list the replay iterates over.

    Exposed so tests/generators can pick reproducible `stop_at` indices.
    """
    return load_log_lines(Path(log_path))[0]
