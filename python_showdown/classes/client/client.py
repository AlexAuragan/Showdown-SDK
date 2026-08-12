import asyncio
from time import perf_counter

from websockets.asyncio.client import ClientConnection, connect

from python_showdown.classes.combat_handler.battle_manager import BattleManager
from python_showdown.classes.combat_handler.random_handler import RandomMoveCombatHandler
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
        self._username: str | None = None
        self.ready: asyncio.Event = asyncio.Event()
        self.formats: list[Format] = []
        self._receive_task: asyncio.Task[None] | None = None
        self.named: bool = False
        self.combat_handler: RandomMoveCombatHandler = combat_handler or RandomMoveCombatHandler()
        self.log_manager: LogManager = log_manager if log_manager is not None else LogManager()

        # --- challenge state ------
        self.challenge_future: (
            asyncio.Future[str] | None
        ) = None
        self.challenged_user: str | None = None

        self.battle_manager: BattleManager = BattleManager(self.username, self.log_manager)
        self.parser: Parser = Parser(self.battle_manager, self)

    @property
    def username(self) -> str | None:
        return self._username

    @username.setter
    def username(self, value: str) -> None:
        self._username = value
        self.battle_manager.player_username = value

    async def join_room(self, room_id: str) -> None:
        await self.send(f"/join {room_id}")

    async def act(self) -> None:
        if self.websocket is None:
            raise RuntimeError("Client is not connected")
        if self.battle_manager.room_id is None:
            raise RuntimeError("No battle room currently set")

        manager = self.battle_manager
        if manager.room_id is None:
            raise RuntimeError("room_id not set.")

        manager.start_action_timeout()
        action_type, action_info = self.combat_handler.select_action(manager.battle_state)
        manager.last_request_id = manager.request_id

        self.log_manager.battle.info(
            f"Sending /choose {action_type} {action_info}|{manager.request_id} in {manager.room_id} "
            f"[force_switch={manager.battle_state.force_switch} "
            f"moves={len(manager.battle_state.available_moves)}]",
            extra={"room_id": self.parser.last_message_room_id}
        )
        await self.send(f"/choose {action_type} {action_info}|{manager.request_id}", room_id=manager.room_id)

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
        room_id = room_id

        if self.websocket is None:
            raise RuntimeError("Client is not connected")

        self.log_manager.battle.debug(
            "<- %s|%s",
            room_id,
            command,
            extra={"room_id": room_id or None},
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
        manager = self.battle_manager

        if websocket is None:
            raise RuntimeError("Client is not connected")

        if manager.request_id is not None:
            if manager.room_id != self.parser.last_message_room_id:
                raise RuntimeError(
                    f"Battle room desync: "
                    f"manager={manager.room_id!r}, "
                    f"parser={self.parser.last_message_room_id!r}, "
                    f"rqid={manager.request_id!r}, "
                    f"turn={manager.turn}"
                )

            await self.act()

        try:
            async for payload in websocket:
                manager.choice_rejected = False
                self.parser.last_message_room_id = ""
                log_room_id = self.battle_manager.room_id # TEMP
                for raw_line in payload.splitlines():
                    if not raw_line:
                        continue

                    line = (
                        raw_line.decode()
                        if isinstance(raw_line, bytes)
                        else raw_line
                    )

                    #
                    if line.startswith(">"):
                        log_room_id = line[1:].strip()
                    log_trace(
                        self.log_manager.protocol,
                        "%s",
                        line,
                        extra={"room_id": log_room_id},
                    )

                    try:
                        events = self.parser.handle_line(
                            line,
                        )
                        for event in events:
                            if isinstance(event, TurnEvent):
                                manager.start_action_timeout()
                    except ObsoleteRequestIdError as e:
                        e.request_id = manager.request_id
                        manager.choice_rejected = False
                    except InvalidActionError as e:
                        self.log_manager.battle.info(
                            "InvalidActionError in %s: %s (rqid=%r last=%r)",
                            manager.room_id, e.message,
                            manager.request_id, self.parser.last_message_room_id,
                            extra={"room_id": self.parser.last_message_room_id},
                        )
                        manager.choice_rejected = True
                    except Exception:
                        self.log_manager.errors.exception(
                            "Error: Failed to handle protocol line: %r", line,
                            extra={"room_id": self.parser.last_message_room_id}
                        )

                        log_trace(
                            self.log_manager.protocol,
                            "%s",
                            line,
                            extra={"room_id": self.parser.last_message_room_id}
                        )
                        raise # TEMP
                        # return

                self.log_manager.battle.debug(
                    "FRAME %s: rqid=%r last=%r rejected=%r",
                    self.parser.last_message_room_id, manager.request_id,
                    manager.last_request_id, manager.choice_rejected,
                    extra={"room_id": self.parser.last_message_room_id},
                )

                if (
                    manager.request_id is None
                    and manager.choice_rejected
                    and manager.last_request_id is not None
                    and (manager.retry_rqid != manager.last_request_id
                         or manager.retry_count < 5)
                ):
                    if manager.retry_rqid != manager.last_request_id:
                        manager.retry_rqid = manager.last_request_id
                        manager.retry_count = 0
                    manager.retry_count += 1
                    self.log_manager.battle.info(
                        "RESTORE rqid=%r (retry %d) in %s",
                        manager.last_request_id, manager.retry_count, self.parser.last_message_room_id,
                        extra={"room_id": self.parser.last_message_room_id},
                    )
                    manager.request_id = manager.last_request_id

                if manager.request_id is not None:
                    self.log_manager.battle.debug(
                        "ACT on rqid=%r in %s", manager.request_id, self.parser.last_message_room_id,
                        extra={"room_id": self.parser.last_message_room_id},
                    )
                    try:
                        await self.act()
                    except Exception:
                        self.log_manager.errors.exception(
                            "act() failed for room=%r",
                            self.parser.last_message_room_id,
                            extra={"room_id": self.parser.last_message_room_id},
                        )
                    manager.request_id = None
                else:
                    self.log_manager.battle.debug(
                        "NO ACT in %s (rqid=None)", self.parser.last_message_room_id,
                        extra={"room_id": self.parser.last_message_room_id},
                    )

        except Exception:
            self.log_manager.errors.exception(
                "Receive loop failed", extra={"room_id": self.parser.last_message_room_id}
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
                battle_finished = manager.battle_finished
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

    async def wait_for_battle_end(
        self,
        timeout: float = 30,
    ) -> BattleResult:
        manager = self.battle_manager

        if manager.battle_finished is not None:
            raise RuntimeError("A battle is already being tracked")

        receive_task = self._receive_task
        if receive_task is None:
            raise RuntimeError("Receive task is not running")

        loop = asyncio.get_running_loop()

        manager.battle_finished = loop.create_future()
        manager.battle_started_at = perf_counter()
        manager.turn = 0

        await asyncio.wait_for(
            manager.room_ready.wait(),
            timeout=timeout,
        )

        if manager.room_id is None:
            raise RuntimeError("Battle room not set after room_ready")

        try:
            done, _ = await asyncio.wait(
                {manager.battle_finished, receive_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Websocket/parser loop died before the battle finished.
            if receive_task in done:
                if receive_task.cancelled():
                    raise RuntimeError(
                        f"Receive loop was cancelled during battle {manager.room_id!r}"
                    )

                error = receive_task.exception()

                if error is not None:
                    raise RuntimeError(
                        f"Receive loop crashed during battle {manager.room_id!r}"
                    ) from error

                raise RuntimeError(
                    f"Receive loop exited unexpectedly during battle {manager.room_id!r}"
                )

            if manager.battle_finished in done:
                return manager.battle_finished.result()

            raise TimeoutError(
                "Battle timed out: "
                f"room={manager.room_id!r}, "
                f"player={self.username!r}, "
                f"turn={manager.turn}, "
                f"player_id={manager.player_id!r}, "
                f"request_id={manager.request_id!r}, "
                f"last_request_id={manager.last_request_id!r}, "
                f"room_ready={manager.room_ready.is_set()}, "
                f"receive_task_done={receive_task.done()}, "
                f"team_size={len(manager.battle_state.team)}, "
                f"available_moves={len(manager.battle_state.available_moves)}, "
                f"force_switch={manager.battle_state.force_switch!r}"
            )

        except asyncio.CancelledError:
            raise RuntimeError(
                "Battle waiter was cancelled:\n"
                f"  player={self.username!r}\n"
                f"  room={manager.room_id!r}\n"
                f"  turn={manager.turn}\n"
                f"  player_id={manager.player_id!r}\n"
                f"  request_id={manager.request_id!r}\n"
                f"  last_request_id={manager.last_request_id!r}\n"
                f"  room_ready={manager.room_ready.is_set()}\n"
                f"  battle_finished_done="
                f"{manager.battle_finished.done() if manager.battle_finished else None}\n"
                f"  receive_task_done={receive_task.done()}\n"
                f"  receive_task_cancelled={receive_task.cancelled()}\n"
                f"  team_size={len(manager.battle_state.team)}\n"
                f"  available_moves={len(manager.battle_state.available_moves)}\n"
                f"  force_switch={manager.battle_state.force_switch!r}"
            ) from None

        finally:
            room_id = manager.room_id
            if room_id:
                try:
                    await asyncio.wait_for(
                        self._leave_battle_room(room_id),
                        timeout=10,
                    )
                except TimeoutError:
                    self.log_manager.errors.error(
                        "Timed out leaving battle room %r",
                        room_id,
                        extra={"room_id": room_id},
                    )

            manager.cancel_action_timeout()
            manager.battle_state.reset()
            manager.request_id = None
            manager.last_request_id = None
            manager.choice_rejected = False
            manager.retry_rqid = None
            manager.retry_count = 0
            manager.player_id = ""
            manager.battle_finished = None
            manager.battle_started_at = None
            manager.turn = 0
            manager.room_ready.clear()

            manager.room_id = None
            manager.room_ready.clear()

            if room_id:
                self.log_manager.close_room(manager.room_id)

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

        stale_room = self.battle_manager.room_id
        if not stale_room:
            return

        self.log_manager.battle.info(
            "Leaving stale battle room %r before starting a new session",
            stale_room,
            extra={"room_id": stale_room},
        )
        await self._leave_battle_room(stale_room)

        self.battle_manager.room_id = None
        self.battle_manager.room_ready.clear()
