# pyright: reportImportCycles=false
# The import cycle is for typing only, changing the project architecture would probably add more overhead.
import asyncio
from time import perf_counter

from python_showdown.classes.client.dt import BattleResult
from python_showdown.logger import LogManager
from python_showdown.models.sdk.battle_state import BattleState
from python_showdown.utils.serialization import SerializableObject


class BattleManager:
    def __init__(self, username: str | None, log_manager: LogManager) -> None:
        self.player_username: str | None = username
        self._player_id: str | None = None
        self.oponent_id: str | None = None
        self.oponent_username: str | None = None

        self._room_id: str | None = None
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

        self.requires_team_preview: bool = False
        self._turn_start_states: list[SerializableObject] = []
        self._last_turn_start_state_turn: int | None = None

        self.last_battle_events: list[SerializableObject] = []
        self.last_battle_turn_states: list[SerializableObject] = []

    def clear_player_id(self):
        self._player_id = None

    @property
    def room_id(self) -> str | None:
        out = self._room_id
        return out

    @room_id.setter
    def room_id(self, value: str | None):
        if value is None:
            raise ValueError("room_id set to None")
        self._room_id = value

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

    def clear_battle(self) -> None:
        """Discard state belonging to the current battle."""

        battle_finished = self.battle_finished

        if battle_finished is not None and not battle_finished.done():
            raise RuntimeError(
                "Cannot clear an active battle; use abandon_battle() instead"
            )

        self.cancel_action_timeout()

        room_id = self.room_id
        if room_id is not None:
            self.log_manager.close_room(room_id)

        self._room_id = None
        self._player_id = None

        self.oponent_id = None
        self.oponent_username = None

        self.request_id = None
        self.last_request_id = None

        self.choice_rejected = False
        self.retry_rqid = None
        self.retry_count = 0

        self.requires_team_preview = False

        self.turn = 0
        self._turn_start_states.clear()
        self._last_turn_start_state_turn = None
        self.room_ready.clear()

        self.battle_state.clear_battle()

    def clear_battle_tracking(self) -> None:
        """Release the completion tracking for a battle that has already finished."""

        battle_finished = self.battle_finished

        if battle_finished is not None and not battle_finished.done():
            raise RuntimeError("Cannot clear unfinished battle tracking")

        self.battle_finished = None
        self.battle_started_at = None

    def abandon_battle(self, error: BaseException | None = None) -> None:
        """Abort the current battle and release its battle-scoped state."""

        battle_finished = self.battle_finished

        if battle_finished is not None and not battle_finished.done():
            if error is None:
                error = RuntimeError(
                    f"Battle {self.room_id!r} was abandoned"
                )

            battle_finished.set_exception(error)

        self.clear_battle()

    def finish_battle(
        self,
        winner: str | None,
    ) -> None:
        self.cancel_action_timeout()

        if self.room_id is None:
            raise RuntimeError("Battle room id not set")

        if self.battle_finished is None:
            # This can happen when Showdown auto-rejoins an old room after login.
            return
            # raise RuntimeError("Battle ended without being tracked")

        if self.battle_finished.done():
            raise RuntimeError("Battle was already finished")

        duration = 0.0
        if self.battle_started_at is not None:
            duration = perf_counter() - self.battle_started_at

        self.last_battle_events = self.battle_state.history_json()
        self.last_battle_turn_states = list(self._turn_start_states)
        self.battle_finished.set_result(
            BattleResult(
                room_id=self.room_id,
                winner=winner,
                move_count=self.turn,
                duration_seconds=duration,
            )
        )

    def record_turn_start_state(self, request_id: int | None) -> None:
        if self.turn <= 0:
            return

        # A turn can receive more than one request, for example after an
        # update or choice retry. We only want the first state for the turn.
        if self.turn == self._last_turn_start_state_turn:
            return

        state = self.battle_state.to_dict()

        self._turn_start_states.append(
            {
                "turn": self.turn,
                "request_id": request_id,
                "state": state,
            }
        )

        self._last_turn_start_state_turn = self.turn
