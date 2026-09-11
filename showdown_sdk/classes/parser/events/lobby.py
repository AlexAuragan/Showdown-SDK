from dataclasses import dataclass

from showdown_sdk.classes.dt import Format
from showdown_sdk.classes.parser.events.base import BaseEvent


class LobbyEvent(BaseEvent):
    pass


@dataclass(frozen=True)
class UpdateUserEvent(LobbyEvent):
    username: str
    named: bool


@dataclass(frozen=True)
class NameTakenEvent(LobbyEvent):
    raw: str


@dataclass(frozen=True)
class FormatsEvent(LobbyEvent):
    formats: list[Format]


@dataclass(frozen=True)
class PrivateMessageEvent(LobbyEvent):
    sender: str
    receiver: str
    message: str


@dataclass(frozen=True)
class TeamRejectedEvent(LobbyEvent):
    reasons: list[str]


@dataclass(frozen=True)
class TeamValidEvent(LobbyEvent):
    pass


@dataclass(frozen=True)
class UserNotFoundEvent(LobbyEvent):
    user: str
