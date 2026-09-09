import asyncio
import json
import re
import shutil
import urllib.error
from dataclasses import asdict
from pathlib import Path

from python_showdown.classes.client.client import Client
from python_showdown.models.sdk.pokemon_set import TeamSet
from python_showdown.models.sdk.sample_team_generator import SampleTeamGenerator
from python_showdown.utils.serialization import (
    Serializable,
    SerializableArray,
    SerializableObject,
)


def write_json(
    path: Path, data: list[SerializableObject] | Serializable | SerializableArray
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# websocket library may be different depending on dependency used
try:  # pragma: no cover - import path detection
    from websockets.exceptions import WebSocketException
except ImportError:  # pragma: no cover
    WebSocketException = None


def _exception_chain(exc: BaseException) -> list[BaseException]:
    seen: set[int] = set()
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def is_infra_failure(exc: BaseException) -> bool:
    """True if the failure is network/server related, not an SDK state check."""
    for item in _exception_chain(exc):
        if isinstance(item, AssertionError):
            return False
        if isinstance(
            item,
            (urllib.error.URLError, ConnectionError, TimeoutError, OSError),
        ):
            return True
        if WebSocketException is not None and isinstance(item, WebSocketException):
            return True
    return False


def save_failed_battle(
    fmt: str,
    battle_id: str,
    logs_root: Path = PROJECT_ROOT / "logs",
) -> Path | None:
    """Move a failed battle's log directory into tests/sample_battles_auto/<fmt>.

    ``battle_id`` may be the raw room id (``battle-gen3randombattle-123``),
    the log directory name (``battle_123``) or the battle number (``123``).
    The whole directory is moved so client_1/client_2 raw logs and any
    events.json / battle_states.json stay together for later replay by
    scripts/test_sample_battles.py. Returns the destination path, or None if
    the log directory does not exist.
    """
    battle_number = re.split(r"[-_]", str(battle_id))[-1]
    logs_root = Path(logs_root)
    if not logs_root.is_absolute():
        logs_root = (PROJECT_ROOT / logs_root).resolve()
    source = logs_root / fmt / f"battle_{battle_number}"

    if not source.is_dir():
        return None

    destination = PROJECT_ROOT / "tests" / "sample_battles_auto" / fmt
    destination.mkdir(parents=True, exist_ok=True)

    target = destination / source.name
    suffix = 1
    while target.exists():
        suffix += 1
        target = destination / f"{source.name}_{suffix}"

    shutil.move(str(source), str(target))
    return target


def write_battle_outputs(
    client: Client,
    battle_directory: Path | None = None,
    *,
    include_events: bool = True,
    include_parser_state: bool = True,
    include_showdown_state: bool = True,
) -> None:
    if battle_directory is None:
        raw_log_path = client.log_manager.latest_raw_log_path()
        if raw_log_path is None:
            return
        battle_directory = raw_log_path.parent

    manager = client.battle_manager

    if include_parser_state:
        # SDK parser battle state, snapshotted at every decision point.
        write_json(
            battle_directory / "battle_states.json",
            manager.last_battle_turn_states,
        )

    if include_events:
        # Event stack produced from the raw protocol messages.
        history = getattr(manager, "last_battle_history", None)
        if history is None:
            history = manager.battle_state.history
        write_json(
            battle_directory / "events.json",
            [event.to_dict() for event in history],
        )

    if include_showdown_state:
        write_json(
            battle_directory / "showdown_states.json",
            [state.get("showdown_state") for state in manager.last_battle_turn_states],
        )


def write_failure_outputs(
    client: Client,
    *,
    include_events: bool = False,
    include_parser_state: bool = True,
    include_showdown_state: bool = False,
) -> Path | None:
    """Write battle_states.json for a battle that crashed.

    No BattleResult was produced on a failure, so the turn-start snapshots
    validated against Showdown's oracle while the battle was still running
    are taken from the live battle_manager state.
    """
    manager = client.battle_manager
    manager.last_battle_turn_states = list(manager.turn_start_states)

    write_battle_outputs(
        client,
        include_events=include_events,
        include_parser_state=include_parser_state,
        include_showdown_state=include_showdown_state,
    )
    return client.log_manager.latest_raw_log_path()


async def run_battle(
    client_1: Client,
    client_2: Client,
    fmt: str,
    team_generator: SampleTeamGenerator | None = None,
    *,
    include_events: bool = False,
    include_parser_state: bool = True,
    include_showdown_state: bool = False,
) -> SerializableObject | None:
    await asyncio.gather(
        client_1.ensure_connected(),
        client_2.ensure_connected(),
    )

    if client_1.username is None:
        raise RuntimeError("client not connected")

    if client_2.username is None:
        raise RuntimeError("client not connected")

    battle_waiter_1, battle_waiter_2 = None, None
    try:
        team_1: TeamSet | None = None
        team_2: TeamSet | None = None

        if team_generator is not None:
            team_1 = await team_generator.generate(
                fmt,
                lambda team: client_1.validate_team(
                    fmt,
                    team,
                ),
            )
            team_2 = await team_generator.generate(
                fmt,
                lambda team: client_2.validate_team(
                    fmt,
                    team,
                ),
            )

        await client_1.challenge(
            client_2.username,
            fmt,
            timeout=60,
            team=team_1,
        )
        await client_2.accept_challenge(
            client_1.username,
            team=team_2,
        )

        await asyncio.gather(
            client_1.battle_manager.room_ready.wait(),
            client_2.battle_manager.room_ready.wait(),
        )

        battle_waiter_1 = asyncio.create_task(client_1.wait_for_battle_end(timeout=300))
        battle_waiter_2 = asyncio.create_task(client_2.wait_for_battle_end(timeout=300))

        result_1, _ = await asyncio.gather(
            battle_waiter_1,
            battle_waiter_2,
        )
        await asyncio.to_thread(
            write_battle_outputs,
            client_2,
            include_events=include_events,
            include_parser_state=include_parser_state,
            include_showdown_state=include_showdown_state,
        )

        return asdict(result_1)

    except BaseException:
        waiters = [
            waiter
            for waiter in (battle_waiter_1, battle_waiter_2)
            if waiter is not None
        ]

        for waiter in waiters:
            waiter.cancel()

        if waiters:
            await asyncio.gather(
                *waiters,
                return_exceptions=True,
            )

        raise
