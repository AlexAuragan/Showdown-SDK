"""Lobby-scope protocol handling: login, format list, and challenges.

These messages are not part of any battle room; they arrive on the global
connection and mutate client *session* state (the ``ready`` flag, the
``named`` flag, the available ``formats``, and challenge futures) rather than
any ``BattleState``. They are produced as events so they flow through the same
``handle_message -> events -> update_client`` pipeline as battle messages, but
each event overrides ``update_client`` directly and never touches battle state.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from python_showdown.classes.client.dt import Format
from python_showdown.classes.client.utils import parse_formats
from python_showdown.classes.parser.events import BaseEvent, RoomEvent
from python_showdown.classes.parser.exceptions import WrongRoomException
from python_showdown.classes.parser.managers.base import MessageManager
from python_showdown.classes.parser.models import ProtocolMessage
from python_showdown.classes.parser.protocol import require_arguments

if TYPE_CHECKING:
    from python_showdown.classes.client.client import Client


@dataclass(frozen=True)
class UpdateUserEvent(BaseEvent):
    """``|updateuser|<name>|<named>|...`` — the server confirming our identity.

    Sets ``client.named`` and, when the server accepted the chosen name, sets
    ``client.username`` to the server's canonical casing and releases
    ``client.ready`` — the signal ``Client.login()`` is waiting on.
    """

    username: str
    named: bool

    def update_client(self, client: Client) -> None:
        client.named = self.named
        expected = client.username
        if expected is None:
            raise ValueError("No client username set, please set a username with client.username")
        if self.named and self.username.lower() == expected.lower():
            client.username = self.username
            client.ready.set()


@dataclass(frozen=True)
class NameTakenEvent(BaseEvent):
    """``|nametaken|...`` — the server rejected the requested username."""

    raw: str

    def update_client(self, client: Client) -> None:
        client.ready.clear()
        raise RuntimeError(f"Username was rejected by the server: {self.raw}")


@dataclass(frozen=True)
class FormatsEvent(BaseEvent):
    """``|formats|...`` — the server's format list."""

    formats: list[Format]

    def update_client(self, client: Client) -> None:
        client.formats = self.formats


@dataclass(frozen=True)
class PrivateMessageEvent(BaseEvent):
    """``|pm|<sender>|<receiver>|<message>`` — a private message.

    The challenger sees their own ``/challenge`` echoed back as a PM once the
    server creates the battle room; that echo resolves the future returned by
    ``Client.challenge()`` with the confirmed format id.
    """

    sender: str
    receiver: str
    message: str

    def update_client(self, client: Client) -> None:
        future = client.challenge_future
        challenged_user = client.challenged_user
        if future is None or future.done() or challenged_user is None:
            return
        if (
            client.username
            and self.sender.lower() == client.username.lower()
            and self.receiver.lower() == challenged_user.lower()
            and self.message.startswith("/challenge ")
        ):
            format_id = self.message.split("|", 1)[0].removeprefix("/challenge ")
            future.set_result(format_id)


class LobbyParser(MessageManager):
    """Handles non-battle session messages: room routing, login, formats, and challenges.

    Stateless on its own — the state it mutates lives on the ``Client`` via the
    events it produces. Kept as a distinct manager so the battle parser never
    has to know about session/lifecycle concerns. ``>roomid`` lines are routed
    here too: they set ``client.room_id`` (via :class:`RoomEvent`) and are
    validated against the active room, so they never pollute battle history.
    """

    def handle_message(
        self,
        client: Client,
        message: ProtocolMessage,
    ) -> list[BaseEvent]:
        command = message.command
        if command == "room":
            return self._handle_room(client, message)
        if command == "updateuser":
            return self._handle_update_user(message)
        if command == "nametaken":
            return [NameTakenEvent(message.raw)]
        if command == "formats":
            return self._handle_formats(message)
        if command == "pm":
            return self._handle_pm(message)
        return []

    @staticmethod
    def _handle_room(client: Client, message: ProtocolMessage) -> list[BaseEvent]:
        given_room_id = message.arguments[0].strip() if message.arguments else ""

        if not given_room_id:
            raise WrongRoomException(client.room_id, given_room_id)

        return [RoomEvent(room_id=given_room_id)]

    @staticmethod
    def _handle_update_user(message: ProtocolMessage) -> list[BaseEvent]:
        # |updateuser|<name>|<named 0/1>|<avatar>|...
        # Showdown prefixes user-facing name fields with a space (e.g.
        # '|updateuser| BOT1| 1| 0| '), so the name and the named flag must be
        # stripped before the named flag is compared to '1'.
        require_arguments(message, 2)
        username = message.arguments[0].strip()
        named = message.arguments[1].strip() == "1"
        return [UpdateUserEvent(username=username, named=named)]

    @staticmethod
    def _handle_formats(message: ProtocolMessage) -> list[BaseEvent]:
        # parse_formats operates on the raw line (|formats|<section>,<col>|...).
        return [FormatsEvent(formats=parse_formats(message.raw))]

    @staticmethod
    def _handle_pm(message: ProtocolMessage) -> list[BaseEvent]:
        # |pm|<sender>|<receiver>|<message>. Showdown prefixes the sender and
        # receiver names with a space ('|pm| BOT7| BOT8|/challenge ...'), so
        # they are stripped. The message body may itself contain '|', so rejoin
        # everything past the receiver to reconstruct it (left unstripped, like
        # the legacy parser, since the challenge echo starts with '/challenge').
        require_arguments(message, 3)
        sender = message.arguments[0].strip()
        receiver = message.arguments[1].strip()
        body = "|".join(message.arguments[2:])
        return [PrivateMessageEvent(sender=sender, receiver=receiver, message=body)]
