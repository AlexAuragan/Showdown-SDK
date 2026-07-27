
import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from websockets.asyncio.client import ClientConnection, connect

from python_showdown.classes.combat.random import RandomMoveCombatHandler
from python_showdown.logger import LogManager, log_trace

from .parser import LogHandler
from .utils import Format


@dataclass(slots=True)
class BattleResult:
    room_id: str
    winner: str | None
    move_count: int
    duration_seconds: float

    @property
    def average_seconds_per_move(self) -> float:
        if self.move_count == 0:
            return 0.0

        return self.duration_seconds / self.move_count

class Client:
    def __init__(
        self,
        websocket_url: str,
        combat_handler: RandomMoveCombatHandler | None = None,
        log_manager: LogManager | None = None
    ) -> None:
        self.websocket_url: str = websocket_url
        self.websocket: ClientConnection | None = None
        self.username: str = None
        self.ready: asyncio.Event = asyncio.Event()
        self.formats: list[Format] = []
        self._receive_task: asyncio.Task[None] | None = None
        self.named: bool = False
        self.log_handler: LogHandler = LogHandler()
        self.combat_handler: RandomMoveCombatHandler = combat_handler or RandomMoveCombatHandler()
        self.log_manager: LogManager = log_manager if log_manager is not None else LogManager()

        self.challenges: dict[str, Any] = {}
        self.challenge_future: (
            asyncio.Future[str] | None
        ) = None
        self.challenged_user: str | None = None
        self.room_id: str = ""

        # The room of the battle we are currently driving.
        self.active_battle_room: str | None = None

        self.battle_finished: asyncio.Future[BattleResult] | None = None
        self.battle_started_at: float | None = None
        self.turn_count: int = 0
        self.battle_player_id: str = ""

        self._action_timeout_task: asyncio.Task[None] | None = None
        self.action_timeout_seconds: float = 5.0


    def start_action_timeout(self) -> None:
        self.cancel_action_timeout()

        turn = self.turn_count
        room_id = self.room_id

        self._action_timeout_task = asyncio.create_task(
            self._raise_on_action_timeout(
                turn=turn,
                room_id=room_id,
            )
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
            f"{self.username!r} did not act within {self.action_timeout_seconds:.1f}s on turn {turn} " +
            f"in room {room_id!r}. The battle request may not have been parsed."
        )

        # Dump the client's live state so the timeout is self-explanatory in the
        # logs instead of just surfacing as an opaque `await battle_waiter`.
        handler = self.combat_handler
        state = handler.battle_state
        self.log_manager.errors.error(
            "Action timeout for %r: room=%r turn=%r request_id=%r "
            "force_switch=%r team_size=%d available_moves=%d",
            self.username, room_id, turn,
            handler.request_id,
            state.force_switch,
            len(state.team),
            len(state.available_moves),
            extra={"room_id": room_id},
        )

        battle_finished = self.battle_finished

        if battle_finished is not None and not battle_finished.done():
            battle_finished.set_exception(error)

    async def join_room(self, room_id: str):
        await self.send(f"/join {room_id}")
        self.room_id = room_id

    async def act(self) -> None:
        if self.websocket is None:
            raise RuntimeError("Client is not connected")

        self.start_action_timeout()

        if self.battle_started_at is None:
            self.battle_started_at = perf_counter()

        action_type, action_info = self.combat_handler.select_action()

        self.log_manager.battle.info(
            f"Sending /choose {action_type} {action_info}|{self.combat_handler.request_id} in {self.room_id}",
            extra={"room_id": self.room_id}
        )
        await self.send(f"/choose {action_type} {action_info}|{self.combat_handler.request_id}", room_id=self.room_id)


    async def connect(self) -> None:
        if self.websocket is not None:
            raise RuntimeError("The client is already connected")

        self.websocket = await connect(self.websocket_url)
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def login(
        self,
        username: str,
        timeout: float = 10,
    ) -> None:
        if self.websocket is None:
            raise RuntimeError("Client is not connected")

        self.ready.clear()
        self.username = username
        await self.websocket.send(f"|/trn {username}")

        try:
            await asyncio.wait_for(
              self.ready.wait(),
              timeout=timeout
          )
        except TimeoutError as e:
           raise TimeoutError(f"Timed out while logging as user {username!r}") from e

    async def send(
        self,
        command: str,
        room_id: str = ""
    ) -> None:
        if self.websocket is None:
            raise RuntimeError("Client is not connected")
        self.log_manager.battle.debug(
            "<- %s|%s",
            room_id,
            command,
            extra={"room_id": room_id or self.room_id},
        )
        await self.websocket.send(f"{room_id}|{command}")

    async def close(self) -> None:
        websocket = self.websocket
        receive_task = self._receive_task

        self.websocket = None
        self._receive_task = None
        self.ready.clear()

        if receive_task is not None and receive_task is not asyncio.current_task():
            receive_task.cancel()
            try:
                await receive_task
            except asyncio.CancelledError:
                pass


        if websocket is not None:
            await websocket.close()

        if self.websocket is websocket:
            self.websocket = None

    async def _receive_loop(self) -> None:
        websocket = self.websocket

        if websocket is None:
            raise RuntimeError("Client is not connected")

        try:
            async for payload in websocket:
                for line in payload.splitlines():
                    if not line:
                        continue

                    log_trace(
                        self.log_manager.protocol,
                        "%s",
                        line,
                        extra={"room_id": self.room_id}
                    )

                    try:
                        await self.log_handler.handle_line(
                            self,
                            str(line),
                        )
                    except Exception as e:
                        self.log_manager.errors.exception(
                            "Error: Failed to handle protocol line: %r", line,
                            extra={"room_id": self.room_id}
                        )

                    if self.combat_handler.request_id is not None:
                        await self.act()
                        self.combat_handler.request_id = None


        except Exception as e:
            self.log_manager.errors.exception(
                "Receive loop failed", extra={"room_id": self.room_id}
            )
            raise e
        finally:
            try:
                await websocket.close()
            finally:
                if self.websocket is websocket:
                    self.websocket = None

                if self._receive_task is asyncio.current_task():
                    self._receive_task = None

                self.named = False
                self.ready.clear()
    async def challenge(
        self,
        user: str,
        format_name: str,
        timeout: float = 10,
    ) -> str:
        if self.websocket is None:
            raise RuntimeError("Client is not connected")

        if self.challenge_future is not None:
            raise RuntimeError("A challenge is already pending")

        loop = asyncio.get_running_loop()

        self.challenge_future = loop.create_future()
        self.challenged_user = user

        try:
            await self.send(
                f"/challenge {user}, {format_name}"
            )

            return await asyncio.wait_for(
                self.challenge_future,
                timeout=timeout,
            )

        except TimeoutError as error:
            raise TimeoutError(
                f"No challenge confirmation for {user!r}"
            ) from error

        finally:
            self.challenge_future = None
            self.challenged_user = None

    def finish_battle(
        self,
        winner: str | None
    ):
        self.cancel_action_timeout()
        # Clear tracked combat state so the same handler is fresh for the next
        # battle. Both clients reach here (both receive |win|/|tie|).
        self.combat_handler.reset()
        self.battle_player_id = ""
        self.active_battle_room = None

        if self.battle_finished is None or self.battle_finished.done():
            return

        duration = 0.0
        if self.battle_started_at is not None:
            duration = perf_counter() - self.battle_started_at

        self.battle_finished.set_result(
            BattleResult(
                room_id=self.room_id,
                winner=winner,
                move_count=self.turn_count,
                duration_seconds=duration
            )
        )

    async def wait_for_battle_end(
        self,
        timeout: float = 300,
    ) -> BattleResult:

        if self.battle_finished is not None:
            raise RuntimeError("A battle is already being tracked")

        loop = asyncio.get_running_loop()

        self.battle_finished = loop.create_future()
        self.battle_started_at = perf_counter()
        self.turn_count = 0

        try:
            return await asyncio.wait_for(
                self.battle_finished, timeout=timeout
            )
        finally:
            self.cancel_action_timeout()
            self.combat_handler.reset()
            self.battle_player_id = ""
            self.active_battle_room = None
            self.battle_finished = None
            self.battle_started_at = None
            self.turn_count = 0

    async def accept_challenge(self, challenger: str) -> None:
        if self.websocket is None:
            raise RuntimeError("Client is not connected")

        await self.send("/utm null")
        await self.send(f"/accept {challenger}")
