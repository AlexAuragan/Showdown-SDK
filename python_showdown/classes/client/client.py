# pyright: reportImportCycles=false
# The import cycle is for typing only, changing the project architecture would probably add more overhead.
import asyncio
from time import perf_counter

from websockets.asyncio.client import ClientConnection, connect

from python_showdown.classes.combat_handler.battle_manager import BattleManager
from python_showdown.classes.combat_handler.random_handler import (
    RandomMoveCombatHandler,
)
from python_showdown.classes.parser.events.base import DiscardedEvent, UnhandledEvent
from python_showdown.classes.parser.events.battle import (
    BattleEvent,
    CustomShowdownBattleStateEvent,
)
from python_showdown.classes.parser.events.lobby import LobbyEvent
from python_showdown.classes.parser.exceptions import (
    InvalidActionError,
    ObsoleteRequestIdError,
)
from python_showdown.classes.parser.parser import Parser
from python_showdown.logger import LogManager, log_trace
from python_showdown.models.sdk.pokemon_set import TeamSet

from .dt import BattleResult, Format

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
        log_manager: LogManager | None = None,
    ) -> None:
        # --- session state (lives for the websocket connection) -----------
        self.websocket_url: str = websocket_url
        self.websocket: ClientConnection | None = None
        self._username: str | None = None
        self.ready: asyncio.Event = asyncio.Event()
        self.formats: list[Format] = []
        self._receive_task: asyncio.Task[None] | None = None
        self.named: bool = False
        self.combat_handler: RandomMoveCombatHandler = (
            combat_handler or RandomMoveCombatHandler()
        )
        self.log_manager: LogManager = (
            log_manager if log_manager is not None else LogManager()
        )

        # --- challenge state ------
        self.challenge_future: asyncio.Future[str] | None = None
        self.challenged_user: str | None = None

        self.battle_manager: BattleManager = BattleManager(
            self.username, self.log_manager
        )
        self.parser: Parser = Parser(self.battle_manager, self)
        self.team_validation_future: asyncio.Future[None] | None = None
        self._pending_state_request_id: int | None = None

    @property
    def username(self) -> str | None:
        return self._username

    @username.setter
    def username(self, value: str) -> None:
        self._username = value
        self.battle_manager.player_username = value

    async def join_room(self, room_id: str) -> None:
        await self.send(f"/join {room_id}")

    async def upload_team(self, team: TeamSet | None = None) -> None:
        packed_team = "null" if team is None else team.to_packed()
        await self.send(f"/utm {packed_team}")

    async def validate_team(
        self,
        format_name: str,
        team: TeamSet,
        timeout: float = 10,
    ) -> None:
        await self.ensure_connected()

        if self.websocket is None:
            raise RuntimeError("Client is not connected")

        if self.team_validation_future is not None:
            raise RuntimeError("A team validation is already pending")

        loop = asyncio.get_running_loop()
        self.team_validation_future = loop.create_future()

        try:
            await self.send(f"/utm {team.to_packed()}")
            await self.send(f"/vtm {format_name}")

            await asyncio.wait_for(
                self.team_validation_future,
                timeout=timeout,
            )
        finally:
            self.team_validation_future = None

    async def act(self) -> None:
        if self.websocket is None:
            raise RuntimeError("Client is not connected")
        if self.battle_manager.room_id is None:
            raise RuntimeError("No battle room currently set")

        manager = self.battle_manager
        if manager.room_id is None:
            raise RuntimeError("room_id not set.")

        manager.start_action_timeout()
        action_type, action_info = self.combat_handler.select_action(
            manager.battle_state
        )
        manager.last_request_id = manager.request_id

        self.log_manager.battle.info(
            f"Sending /choose {action_type} {action_info}|{manager.request_id} in {manager.room_id} "
            + f"[force_switch={manager.battle_state.force_switch} "
            + f"moves={len(manager.battle_state.available_moves)}]",
            extra={"room_id": self.parser.last_message_room_id},
        )
        await self.send(
            f"/choose {action_type} {action_info}|{manager.request_id}",
            room_id=manager.room_id,
        )
        self.battle_manager.cancel_action_timeout()

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
            print(
                f"{self.username=}, {self.battle_manager.player_id=}, {self.battle_manager.room_id=}, {self.parser.last_message_room_id=}"
            )
            raise TimeoutError(f"Timed out while logging as user {username!r}") from e
        await self._leave_stale_rooms(wait_for_autorejoin=True)

    async def send(self, command: str, room_id: str = "") -> None:

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

        if manager.request_id is not None and manager.room_id != self.parser.last_message_room_id:
                raise RuntimeError(
                    "Battle room desync: "
                    + f"manager={manager.room_id!r}, "
                    + f"parser={self.parser.last_message_room_id!r}, "
                    + f"rqid={manager.request_id!r}, "
                    + f"turn={manager.turn}"
                )

            # await self.act()

        try:
            async for payload in websocket:
                received_custom_state = False
                manager.choice_rejected = False
                self.parser.last_message_room_id = ""
                log_room_id = self.battle_manager.room_id  # TEMP
                for raw_line in payload.splitlines():
                    if not raw_line:
                        continue

                    line = (
                        raw_line.decode() if isinstance(raw_line, bytes) else raw_line
                    )

                    if line.startswith(">"):
                        log_room_id = line[1:].strip()
                    log_trace(
                        self.log_manager.protocol,
                        "%s",
                        line,
                        extra={"room_id": log_room_id},
                    )

                    try:
                        from python_showdown.classes.parser.events.battle import (
                            TurnEvent,
                        )

                        events = self.parser.handle_line(
                            line,
                        )
                        self.battle_manager.battle_state.history.extend(events)

                        for event in events:
                            if isinstance(event, CustomShowdownBattleStateEvent):
                                received_custom_state = True
                            if isinstance(event, BattleEvent):
                                event.update_manager(self.battle_manager)
                            elif isinstance(event, LobbyEvent):
                                event.update_client(self)
                            elif isinstance(event, DiscardedEvent):
                                pass
                            elif isinstance(event, UnhandledEvent):
                                print(event.raw)
                                print(type(event))
                                print(event)
                                raise NotImplementedError(event.raw)
                            else:
                                raise NotImplementedError(type(event))
                            if isinstance(event, TurnEvent):
                                manager.start_action_timeout()
                    except ObsoleteRequestIdError as e:
                        e.request_id = manager.request_id
                        manager.choice_rejected = False
                    except InvalidActionError as e:
                        self.log_manager.battle.info(
                            "InvalidActionError in %s: %s (rqid=%r last=%r)",
                            manager.room_id,
                            e.message,
                            manager.request_id,
                            self.parser.last_message_room_id,
                            extra={"room_id": self.parser.last_message_room_id},
                        )
                        manager.choice_rejected = True
                    except Exception:
                        print(self.log_manager.latest_raw_log_path())
                        self.log_manager.errors.exception(
                            "Error: Failed to handle protocol line: %r",
                            line,
                            extra={"room_id": self.parser.last_message_room_id},
                        )

                        log_trace(
                            self.log_manager.protocol,
                            "%s",
                            line,
                            extra={"room_id": self.parser.last_message_room_id},
                        )
                        raise  # TEMP

                self.log_manager.battle.debug(
                    "FRAME %s: rqid=%r last=%r rejected=%r",
                    self.parser.last_message_room_id,
                    manager.request_id,
                    manager.last_request_id,
                    manager.choice_rejected,
                    extra={"room_id": self.parser.last_message_room_id},
                )

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
                    self.log_manager.battle.info(
                        "RESTORE rqid=%r (retry %d) in %s",
                        manager.last_request_id,
                        manager.retry_count,
                        self.parser.last_message_room_id,
                        extra={"room_id": self.parser.last_message_room_id},
                    )
                    manager.request_id = manager.last_request_id

                if manager.requires_team_preview:
                    if manager.room_id is None:
                        raise ValueError("room_id is None")
                    team_order: list[str] = [str(idx) for idx in self.combat_handler.select_team_order()]
                    await self.send("/choose team " + ",".join(team_order), room_id=manager.room_id)
                    manager.requires_team_preview = False
                elif received_custom_state:
                    pending_request_id = self._pending_state_request_id
                    if pending_request_id is None:
                        raise RuntimeError(
                            "Received Showdown battle state without a pending request"
                        )

                    if manager.request_id != pending_request_id:
                        raise RuntimeError(
                            "Battle state synchronization failed: "
                            + f"requested rqid={pending_request_id}, "
                            + f"current rqid={manager.request_id}"
                        )

                    # NOW the SDK and Showdown snapshot belong to the same decision point.
                    manager.record_turn_start_state(pending_request_id)

                    self._pending_state_request_id = None

                    await self.act()
                    manager.request_id = None

                elif manager.request_id is not None:
                    if self._pending_state_request_id is not None:
                        raise RuntimeError(
                            "Received another decision while waiting for Showdown state"
                        )

                    self._pending_state_request_id = manager.request_id
                    await self.get_custom_showdown_battle_state()
                elif manager.request_id is not None:
                    self.log_manager.battle.debug(
                        "ACT on rqid=%r in %s",
                        manager.request_id,
                        self.parser.last_message_room_id,
                        extra={"room_id": self.parser.last_message_room_id},
                    )
                    try:
                        await self.act()
                    except Exception:  # noqa: BLE001 # This is intentional
                        self.log_manager.errors.exception(
                            "act() failed for room=%r",
                            self.parser.last_message_room_id,
                            extra={"room_id": self.parser.last_message_room_id},
                        )
                    manager.request_id = None

                else:
                    self.log_manager.battle.debug(
                        "NO ACT in %s (rqid=None)",
                        self.parser.last_message_room_id,
                        extra={"room_id": self.parser.last_message_room_id},
                    )

        except Exception:
            self.log_manager.errors.exception(
                "Receive loop failed",
                extra={"room_id": self.parser.last_message_room_id},
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
                if battle_finished is not None and not battle_finished.done():
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
        disconnected = self.websocket is None or task is None or task.done()

        if not disconnected:
            return

        if task is not None and task.done() and not task.cancelled():
            task.exception()

        username = self.username

        for attempt in range(3):
            await self.connect()

            try:
                if username is not None:
                    await self.login(username)

                task = self._receive_task
                if self.websocket is not None and task is not None and not task.done():
                    return

            except TimeoutError:
                if attempt == 2:
                    raise

            await self.close()

            if attempt < 2:
                await asyncio.sleep(1.0)

        raise ConnectionError(f"Failed to reconnect client {username!r}")

    async def challenge(
        self,
        user: str,
        format_name: str,
        team: TeamSet | None = None,
        timeout: float = 10,
    ) -> str:
        await self.ensure_connected()

        if self.websocket is None:
            raise RuntimeError("Client is not connected")

        if self.challenge_future is not None:
            raise RuntimeError("A challenge is already pending")

        await self.upload_team(team)
        loop = asyncio.get_running_loop()

        self.challenge_future = loop.create_future()
        self.challenged_user = user

        try:
            await self.send(f"/challenge {user}, {format_name}")

            return await asyncio.wait_for(
                self.challenge_future,
                timeout=timeout,
            )

        except TimeoutError as error:
            raise TimeoutError(f"No challenge confirmation for {user!r}") from error

        finally:
            self.challenge_future = None
            self.challenged_user = None

    async def _leave_battle_room(self, room_id: str) -> None:
        """Tell the server to drop us from a finished battle room."""
        try:
            # await self.send(f"/leave {room_id}")
            await self.send("/leave", room_id=room_id)
        except Exception:  # noqa: BLE001 # This is intentional
            self.log_manager.errors.exception(
                "Failed to leave battle room %r",
                room_id,
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

        battle_completed = False

        try:
            await asyncio.wait_for(
                manager.room_ready.wait(),
                timeout=timeout,
            )

            if manager.room_id is None:
                raise RuntimeError("Battle room not set after room_ready")

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
                result = manager.battle_finished.result()
                battle_completed = True
                return result

            raise TimeoutError(
                "Battle timed out: "
                + f"room={manager.room_id!r}, "
                + f"player={self.username!r}, "
                + f"turn={manager.turn}, "
                + f"player_id={manager.player_id!r}, "
                + f"request_id={manager.request_id!r}, "
                + f"last_request_id={manager.last_request_id!r}, "
                + f"room_ready={manager.room_ready.is_set()}, "
                + f"receive_task_done={receive_task.done()}, "
                + f"team_size={len(manager.battle_state.team)}, "
                + f"available_moves={len(manager.battle_state.available_moves)}, "
                + f"force_switch={manager.battle_state.force_switch!r}"
            )

        except asyncio.CancelledError as error:
            error.add_note(
                "Battle waiter was cancelled:\n"
                + f"  player={self.username!r}\n"
                + f"  room={manager.room_id!r}\n"
                + f"  turn={manager.turn}\n"
                + f"  player_id={manager.player_id!r}\n"
                + f"  request_id={manager.request_id!r}\n"
                + f"  last_request_id={manager.last_request_id!r}\n"
                + f"  room_ready={manager.room_ready.is_set()}\n"
                + "  battle_finished_done="
                + f"{manager.battle_finished.done()}"
                + "\n"
                + f"  receive_task_done={receive_task.done()}\n"
                + f"  receive_task_cancelled={receive_task.cancelled()}\n"
                + f"  team_size={len(manager.battle_state.team)}\n"
                + f"  available_moves={len(manager.battle_state.available_moves)}\n"
                + f"  force_switch={manager.battle_state.force_switch!r}"
            )
            raise

        finally:
            room_id = manager.room_id
            battle_finished = manager.battle_finished

            if not battle_finished.done():
                battle_finished.cancel()

            try:
                if battle_completed:
                    # Normal completed battle: the server battle is over, so we can
                    # keep the websocket alive and simply leave the room.
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
                else:
                    # Failed/aborted battle: the server may still be sending
                    # messages for this room. Stop the receive loop before
                    # destroying the battle/parser state.
                    await self.close()

            finally:
                self.parser.battle.reset()
                manager.clear_battle()
                manager.clear_battle_tracking()
    async def accept_challenge(
        self, challenger: str, team: TeamSet | None = None
    ) -> None:
        await self.ensure_connected()

        if self.websocket is None:
            raise RuntimeError("Client is not connected")

        await self.upload_team(team)
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

        self.battle_manager.abandon_battle()
        self.battle_manager.room_ready.clear()


    async def get_custom_showdown_battle_state(self):
        if self.battle_manager.room_id is None:
            return
        return await self.send("/requeststate", self.battle_manager.room_id)
