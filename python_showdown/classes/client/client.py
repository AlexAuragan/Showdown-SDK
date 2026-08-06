import asyncio
from time import perf_counter

from websockets.asyncio.client import ClientConnection, connect

from python_showdown.classes.combat.battle_state import BattleState
from python_showdown.classes.combat.random import RandomMoveCombatHandler
from python_showdown.logger import LogManager, log_trace

from .dt import BattleResult
from .parser import Parser
from .utils import Format


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
        self.parser: Parser = Parser()
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
        self.battle_player_id: str = ""             # our side id ("p1"/"p2")

        self._action_timeout_task: asyncio.Task[None] | None = None
        self.action_timeout_seconds: float = 5.0

        self.battle_state: BattleState = BattleState()
        # `|request|`'s rqid, set by the parser when a decision is requested and
        # consumed/cleared by `act()`. None = no pending decision.
        self.request_id: int | None = None
        # rqid of the most recently sent `/choose`; reused by `retry_action`
        # when the server rejects the choice with `|error|[Invalid choice]`.
        self._last_request_id: int | None = None
        # Set by the parser's `|error|` handler when a `/choose` was rejected;
        # the receive loop re-draws a random move and resends, then clears it.
        self.pending_choice_retry: bool = False

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
            f"Sending /choose {action_type} {action_info}|{self.request_id} in {self.room_id}",
            extra={"room_id": self.room_id}
        )
        await self.send(f"/choose {action_type} {action_info}|{self.request_id}", room_id=self.room_id)

    async def retry_action(self) -> None:
        """Re-draw a random move and resend after the server rejected a choice.
        """
        if self.websocket is None:
            raise RuntimeError("Client is not connected")

        self.start_action_timeout()

        action_type, action_info = self.combat_handler.select_action(self.battle_state)
        rqid = self._last_request_id

        self.log_manager.battle.info(
            f"Retrying /choose {action_type} {action_info}|{rqid} in {self.room_id} "
            f"(previous choice was rejected)",
            extra={"room_id": self.room_id}
        )
        await self.send(f"/choose {action_type} {action_info}|{rqid}", room_id=self.room_id)

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
                        self.parser.handle_line(
                            self,
                            str(line),
                        )
                    except Exception:
                        self.log_manager.errors.exception(
                            "Error: Failed to handle protocol line: %r", line,
                            extra={"room_id": self.room_id}
                        )

                    if self.request_id is not None:
                        try:
                            await self.act()
                        except Exception:
                            self.log_manager.errors.exception(
                                "act() failed for room=%r",
                                self.room_id,
                                extra={"room_id": self.room_id},
                            )
                        self.request_id = None

                    if self.pending_choice_retry:
                        self.pending_choice_retry = False
                        try:
                            await self.retry_action()
                        except Exception:
                            self.log_manager.errors.exception(
                                "retry_action() failed for room=%r",
                                self.room_id,
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

        if battle_room is not None and battle_room:
            asyncio.create_task(self._leave_battle_room(battle_room))

        if self.battle_finished is None or self.battle_finished.done():
            self.battle_state.reset()
            self.request_id = None
            self._last_request_id = None
            self.pending_choice_retry = False
            self.battle_player_id = ""
            self.active_battle_room = None
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
        self.pending_choice_retry = False
        self.battle_player_id = ""
        self.active_battle_room = None
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
            leftover_room = self.active_battle_room
            if leftover_room:
                asyncio.create_task(self._leave_battle_room(leftover_room))

            self.cancel_action_timeout()
            self.battle_state.reset()
            self.request_id = None
            self._last_request_id = None
            self.pending_choice_retry = False
            self.battle_player_id = ""
            self.active_battle_room = None
            self.battle_finished = None
            self.battle_started_at = None
            self.turn_count = 0

    async def accept_challenge(self, challenger: str) -> None:
        await self.ensure_connected()

        if self.websocket is None:
            raise RuntimeError("Client is not connected")

        await self.send("/utm null")
        await self.send(f"/accept {challenger}")
