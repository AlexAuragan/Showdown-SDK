from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from python_showdown.classes.client.dt import Format
from python_showdown.classes.parser.events.base import BaseEvent
from python_showdown.models.sdk.exceptions import TeamRejectedError

if TYPE_CHECKING:
    from python_showdown.classes.client.client import Client


class LobbyEvent(BaseEvent, metaclass=ABCMeta):
    @abstractmethod
    def update_client(self, client: Client):
        pass


@dataclass(frozen=True)
class UpdateUserEvent(LobbyEvent):
    """``|updateuser|<name>|<named>|...`` — the server confirming our identity.

    Sets ``client.named`` and, when the server accepted the chosen name, sets
    ``client.username`` to the server's canonical casing and releases
    ``client.ready`` — the signal ``Client.login()`` is waiting on.
    """

    username: str
    named: bool

    @override
    def update_client(self, client: Client) -> None:
        client.named = self.named

        if not self.named:
            return

        expected = client.battle_manager.player_username
        if expected is None:
            client.username = self.username
            client.ready.set()
            return
        if self.named and self.username == expected:
            client.username = self.username
            client.ready.set()


@dataclass(frozen=True)
class NameTakenEvent(LobbyEvent):
    """``|nametaken|...`` — the server rejected the requested username."""

    raw: str

    @override
    def update_client(self, client: Client) -> None:
        client.ready.clear()
        raise RuntimeError(f"Username was rejected by the server: {self.raw}")


@dataclass(frozen=True)
class FormatsEvent(LobbyEvent):
    """``|formats|...`` — the server's format list."""

    formats: list[Format]

    @override
    def update_client(self, client: Client) -> None:
        client.formats = self.formats


@dataclass(frozen=True)
class PrivateMessageEvent(LobbyEvent):
    """``|pm|<sender>|<receiver>|<message>`` — a private message.

    The challenger sees their own ``/challenge`` echoed back as a PM once the
    server creates the battle room; that echo resolves the future returned by
    ``Client.challenge()`` with the confirmed format id.
    """

    sender: str
    receiver: str
    message: str

    @override
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


@dataclass(frozen=True)
class TeamRejectedEvent(LobbyEvent):
    reasons: list[str]

    @override
    def update_client(self, client: Client):
        future = client.team_validation_future

        if future is not None and not future.done():
            future.set_exception(TeamRejectedError(reasons=self.reasons))


@dataclass(frozen=True)
class TeamValidEvent(LobbyEvent):
    @override
    def update_client(self, client: Client) -> None:
        future = client.team_validation_future

        if future is not None and not future.done():
            future.set_result(None)

@dataclass(frozen=True)
class UserNotFoundEvent(LobbyEvent):
    user: str

    @override
    def update_client(self, client: Client):
        raise RuntimeError("User not found")
