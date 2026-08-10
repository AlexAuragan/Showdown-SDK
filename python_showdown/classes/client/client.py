import asyncio
from time import perf_counter

from websockets.asyncio.client import ClientConnection, connect

from python_showdown.classes.combat.random import RandomMoveCombatHandler
from python_showdown.classes.parser import Parser, TurnEvent
from python_showdown.classes.parser.exceptions import (
    InvalidActionError,
    ObsoleteRequestIdError,
)
from python_showdown.logger import LogManager, log_trace
from python_showdown.models.sdk.battle_state import BattleState

from .dt import BattleResult
from .utils import Format

STALE_ROOM_GRACE_PERIOD = 2.0  # seconds to let the server push any
                                    # auto-rejoin room state after login

class Client:
    """A single websocket session against a Pokémon Showdown server.

    For now can only handle one combat at the time.
    """

    def __init__(
        self,
        websocket_url: str,
        combat_handler: RandomMoveCombatHandler | None = None,
        log_manager: LogManager | None = None
    ) -> None:
        # --- session state (lives for the websocket connection) -----------
        self.websocket_url: str = websocket_url
        self.websocket: ClientConnection | None = None
        self.username: str | None = None
        self.ready: asyncio.Event = asyncio.Event()
        self.formats: list[Format] = []
        self._receive_task: asyncio.Task[None] | None = None
        self.named: bool = False
        self.parser: Parser = Parser(self)
        self.combat_handler: RandomMoveCombatHandler = combat_handler or RandomMoveCombatHandler()
        self.log_manager: LogManager = log_manager if log_manager is not None else LogManager()

        # --- challenge state ------
        self.challenge_future: (
            asyncio.Future[str] | None
        ) = None
        self.challenged_user: str | None = None

        # --- battle lifecycle state --------------------------------------
        self.room_id: str = ""                       # latest room redirected into
        self.active_battle_room: str | None = None   # the battle we are driving
        self.battle_finished: asyncio.Future[BattleResult] | None = None
        self.battle_started_at: float | None = None
        self.turn_count: int = 0
        self.battle_player_id: str = ""

        self._action_timeout_task: asyncio.Task[None] | None = None
        self.action_timeout_seconds: float = 5.0

        self.battle_state: BattleState = BattleState(self)
        # `|request|`'s rqid, set by the parser when a decision is requested and
        self.request_id: int | None = None
        self._last_request_id: int | None = None
        self._choice_rejected: bool = False
        self._retry_count: int = 0
        self._retry_rqid: int | None = None


    def start_action_timeout(self) -> None:
        self.cancel_action_timeout()

        turn = self.turn_count

        self._action_timeout_task = asyncio.create_task(
            self._raise_on_action_timeout(
                turn=turn,
                room_id=self.room_id,
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

        state = self.battle_state
        self.log_manager.errors.error(
            "Action timeout for %r: room=%r turn=%r request_id=%r "
            "force_switch=%r team_size=%d available_moves=%d",
            self.username, room_id, turn,
            self.request_id,
            state.force_switch,
            len(state.team),
            len(state.available_moves),
            extra={"room_id": room_id},
        )

        battle_finished = self.battle_finished

        if battle_finished is not None and not battle_finished.done():
            battle_finished.set_exception(error)

    async def join_room(self, room_id: str) -> None:
        await self.send(f"/join {room_id}")
        self.room_id = room_id

    async def act(self) -> None:
        if self.websocket is None:
            raise RuntimeError("Client is not connected")

        self.start_action_timeout()

        action_type, action_info = self.combat_handler.select_action(self.battle_state)
        self._last_request_id = self.request_id

        self.log_manager.battle.info(
            f"Sending /choose {action_type} {action_info}|{self.request_id} in {self.room_id} "
            f"[force_switch={self.battle_state.force_switch} "
            f"moves={len(self.battle_state.available_moves)}]",
            extra={"room_id": self.room_id}
        )
        await self.send(f"/choose {action_type} {action_info}|{self.request_id}", room_id=self.room_id)

    async def connect(self) -> None:
        if self.websocket is not None:
            raise RuntimeError("The client is already connected")

        self.websocket = await connect(
            self.websocket_url,
            ping_interval=20,
            ping_timeout=120,
        )
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
                timeout=timeout,
            )
        except TimeoutError as e:
            raise TimeoutError(f"Timed out while logging as user {username!r}") from e

        await self._leave_stale_rooms()

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
                self._choice_rejected = False

                for raw_line in payload.splitlines():
                    if not raw_line:
                        continue

                    line = (
                        raw_line.decode()
                        if isinstance(raw_line, bytes)
                        else raw_line
                    )


                    log_trace(
                        self.log_manager.protocol,
                        "%s",
                        line,
                        extra={"room_id": self.room_id}
                    )

                    try:
                        events = self.parser.handle_line(
                            line,
                        )
                        for event in events:
                            if isinstance(event, TurnEvent):
                                self.start_action_timeout()
                    except ObsoleteRequestIdError as e:
                        e.request_id = self.request_id
                        self._choice_rejected = False
                    except InvalidActionError as e:
                        self.log_manager.battle.info(
                            "InvalidActionError in %s: %s (rqid=%r last=%r)",
                            self.room_id, e.message,
                            self.request_id, self._last_request_id,
                            extra={"room_id": self.room_id},
                        )
                        self._choice_rejected = True
                    except Exception:
                        self.log_manager.errors.exception(
                            "Error: Failed to handle protocol line: %r", line,
                            extra={"room_id": self.room_id}
                        )

                self.log_manager.battle.debug(
                    "FRAME %s: rqid=%r last=%r rejected=%r",
                    self.room_id, self.request_id,
                    self._last_request_id, self._choice_rejected,
                    extra={"room_id": self.room_id},
                )

                if (
                    self.request_id is None
                    and self._choice_rejected
                    and self._last_request_id is not None
                    and (self._retry_rqid != self._last_request_id
                         or self._retry_count < 5)
                ):
                    if self._retry_rqid != self._last_request_id:
                        self._retry_rqid = self._last_request_id
                        self._retry_count = 0
                    self._retry_count += 1
                    self.log_manager.battle.info(
                        "RESTORE rqid=%r (retry %d) in %s",
                        self._last_request_id, self._retry_count, self.room_id,
                        extra={"room_id": self.room_id},
                    )
                    self.request_id = self._last_request_id

                if self.request_id is not None:
                    self.log_manager.battle.debug(
                        "ACT on rqid=%r in %s", self.request_id, self.room_id,
                        extra={"room_id": self.room_id},
                    )
                    try:
                        await self.act()
                    except Exception:
                        self.log_manager.errors.exception(
                            "act() failed for room=%r",
                            self.room_id,
                            extra={"room_id": self.room_id},
                        )
                    self.request_id = None
                else:
                    self.log_manager.battle.debug(
                        "NO ACT in %s (rqid=None)", self.room_id,
                        extra={"room_id": self.room_id},
                    )

        except Exception:
            self.log_manager.errors.exception(
                "Receive loop failed", extra={"room_id": self.room_id}
            )
            raise
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

                # If the socket died while a battle was being tracked, fail it
                # now instead of waiting for the 300s battle timeout.
                battle_finished = self.battle_finished
                if (
                    battle_finished is not None
                    and not battle_finished.done()
                ):
                    battle_finished.set_exception(
                        ConnectionError(
                            f"Connection to server lost for {self.username!r}"
                        )
                    )

    async def ensure_connected(self) -> None:
        """
        Reconnect and re-authenticate after a dropped websocket.
        """
        task = self._receive_task
        disconnected = (
            self.websocket is None
            or task is None
            or task.done()
        )
        if not disconnected:
            return

        # Retrieve the dead task's exception so asyncio does not log an
        # "exception was never retrieved" warning.
        if task is not None and task.done() and not task.cancelled():
            task.exception()

        await self.connect()

        if self.username is not None:
            # The server may briefly still hold the old name right after the
            # socket drops, which makes the first login time out; retry a couple
            # of times while it deregisters the previous session.
            for attempt in range(3):
                try:
                    await self.login(self.username)
                    return
                except TimeoutError:
                    if attempt == 2:
                        raise
                await asyncio.sleep(1.0)

    async def challenge(
        self,
        user: str,
        format_name: str,
        timeout: float = 10,
    ) -> str:
        await self.ensure_connected()

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

    async def _leave_battle_room(self, room_id: str) -> None:
        """Tell the server to drop us from a finished battle room.
        """
        try:
            # await self.send(f"/leave {room_id}")
            await self.send("/leave", room_id=room_id)
        except Exception:
            self.log_manager.errors.exception(
                "Failed to leave battle room %r", room_id,
                extra={"room_id": room_id},
            )

    def finish_battle(
        self,
        winner: str | None,
    ) -> None:
        self.cancel_action_timeout()

        battle_room = self.active_battle_room

        if self.battle_finished is None or self.battle_finished.done():
            self.battle_state.reset()
            self.request_id = None
            self._last_request_id = None
            self._choice_rejected = False
            self._retry_rqid = None
            self._retry_count = 0
            self.battle_player_id = ""
            self.room_id = ""
            if battle_room is not None:
                self.log_manager.close_room(battle_room)
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

        self.battle_state.reset()
        self.request_id = None
        self._last_request_id = None
        self._choice_rejected = False
        self._retry_rqid = None
        self._retry_count = 0
        self.battle_player_id = ""
        if battle_room is not None:
            self.log_manager.close_room(battle_room)

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
            if self.active_battle_room:
                try:
                    await asyncio.wait_for(
                        self._leave_battle_room(self.active_battle_room),
                        timeout=10
                    )
                except TimeoutError:
                    self.log_manager.errors.error(
                        "Timed out leaving battle room %r",
                        self.active_battle_room,
                        extra={"room_id": self.active_battle_room},
                    )

            self.cancel_action_timeout()
            self.battle_state.reset()
            self.request_id = None
            self._last_request_id = None
            self._choice_rejected = False
            self._retry_rqid = None
            self._retry_count = 0
            self.battle_player_id = ""
            self.battle_finished = None
            self.battle_started_at = None
            self.turn_count = 0

            if self.active_battle_room:
                self.log_manager.close_room(self.active_battle_room)

    async def accept_challenge(self, challenger: str) -> None:
        await self.ensure_connected()

        if self.websocket is None:
            raise RuntimeError("Client is not connected")

        await self.send("/utm null")
        await self.send(f"/accept {challenger}")

    async def _leave_stale_rooms(self, *, wait_for_autorejoin: bool = False) -> None:
        """Leave any battle room this Client is currently attached to that
        it did not itself start via `challenge()`/`accept_challenge()`.
        """
        if wait_for_autorejoin:
            await asyncio.sleep(STALE_ROOM_GRACE_PERIOD)

        stale_room = self.active_battle_room
        if not stale_room:
            return

        self.log_manager.battle.info(
            "Leaving stale battle room %r before starting a new session",
            stale_room,
            extra={"room_id": stale_room},
        )
        await self._leave_battle_room(stale_room)

        self.active_battle_room = None
        self.room_id = ""
