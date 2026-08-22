# pyright: reportImportCycles=false
# The import cycle is for typing only, changing the project architecture would probably add more overhead.
import asyncio
from time import perf_counter

from python_showdown.classes.client.dt import BattleResult
from python_showdown.logger import LogManager
from python_showdown.models.sdk.battle_state import BattleState


class BattleManager:
    def __init__(self, username: str | None, log_manager: LogManager) -> None:
        self.player_username: str | None = username
        self._player_id: str | None = None
        self.oponent_id: str | None = None
        self.oponent_username: str | None = None

        self.room_id: str | None = None
        self.battle_state: BattleState = BattleState(self)
        self.log_manager: LogManager = log_manager
        self.battle_finished: asyncio.Future[BattleResult] | None = None

        self.battle_started_at: float | None = None
        self.request_id: int | None = None
        self.turn: int = 0

        self.room_ready: asyncio.Event = asyncio.Event()
        self._action_timeout_task: asyncio.Task[None] | None = None
        self.action_timeout_seconds: float = 5.0

        # Retry mechanic
        self.choice_rejected: bool = False
        self.retry_rqid: int | None = None
        self.retry_count: int = 0
        self.last_request_id: int | None = None

    def clear_player_id(self):
        self._player_id = None

    @property
    def player_id(self) -> str | None:
        return self._player_id

    @player_id.setter
    def player_id(self, value: str | None) -> None:
        if value is None:
            raise ValueError("Player id cannot be set to None")
        if self._player_id and value != self._player_id:
            raise RuntimeError(
                f"Player id already set, player_id: {self._player_id}, new value {value}",
            )
        self._player_id = value

    def start_action_timeout(self) -> None:
        if self.room_id is None:
            raise RuntimeError("No room currently set")

        self.cancel_action_timeout()
        self._action_timeout_task = asyncio.create_task(
            self._raise_on_action_timeout(turn=self.turn, room_id=self.room_id)
        )

    def cancel_action_timeout(self) -> None:
        task = self._action_timeout_task
        self._action_timeout_task = None

        if task is not None:
            task.cancel()

    async def _raise_on_action_timeout(
        self,
        turn: int,
        room_id: str,
    ) -> None:
        try:
            await asyncio.sleep(self.action_timeout_seconds)
        except asyncio.CancelledError:
            return

        error = TimeoutError(
            f"{self.player_username!r} did not act within {self.action_timeout_seconds:.1f}s on turn {turn} "
            + f"in room {room_id!r}. The battle request may not have been parsed."
            + f"choice_rejected={self.choice_rejected!r}, "
            + f"retry_count={self.retry_count!r}"
        )

        state = self.battle_state
        self.log_manager.errors.error(
            "Action timeout for %r: room=%r turn=%r request_id=%r "
            + "force_switch=%r team_size=%d available_moves=%d",
            self.player_username,
            room_id,
            turn,
            self.request_id,
            state.force_switch,
            len(state.team),
            len(state.available_moves),
            extra={"room_id": room_id},
        )

        battle_finished = self.battle_finished

        if battle_finished is not None and not battle_finished.done():
            battle_finished.set_exception(error)

    def reset(self):
        self._player_id = None
        self.request_id = None
        self.last_request_id = None
        self.choice_rejected = False
        self.retry_rqid = None
        self.retry_count = 0
        self.player_id = ""
        if self.room_id is not None:
            self.log_manager.close_room(self.room_id)
        self.room_id = None

    def finish_battle(
        self,
        winner: str | None,
    ) -> None:
        self.cancel_action_timeout()
        if self.room_id is None:
            raise RuntimeError("Battle room id not set")

        if self.battle_finished is None or self.battle_finished.done():
            self.reset()
            self.room_ready.clear()
            return

        duration = 0.0
        if self.battle_started_at is not None:
            duration = perf_counter() - self.battle_started_at

        self.battle_finished.set_result(
            BattleResult(
                room_id=self.room_id,
                winner=winner,
                move_count=self.turn,
                duration_seconds=duration,
            )
        )

        self.reset()
