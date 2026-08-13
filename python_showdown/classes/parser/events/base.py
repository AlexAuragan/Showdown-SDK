from abc import ABC
from dataclasses import dataclass

from python_showdown.classes.parser.models import ProtocolAnnotation, ProtocolMessage


class BaseEvent(ABC):
    """A complete semantic event derived from one or more protocol messages."""

def unhandled_event(message: ProtocolMessage, action_id: int | None = None) -> UnhandledEvent:
    raise ValueError("Unhandled event:", message)
    # return UnhandledEvent(message.command, message.arguments, message.annotations, message.raw, action_id)


@dataclass(frozen=True)
class UnhandledEvent(BaseEvent):
    """A valid protocol message whose semantic reducer is not implemented yet."""

    command: str
    arguments: tuple[str, ...]
    annotations: tuple[ProtocolAnnotation, ...]
    raw: str
    action_id: int | None = None

    @staticmethod
    def from_message(message: ProtocolMessage, action_id: int | None = None):
        return UnhandledEvent(
            command=message.command,
            arguments=message.arguments,
            annotations=message.annotations,
            raw=message.raw,
            action_id=action_id
        )


@dataclass(frozen=True)
class DiscardedEvent(BaseEvent):
    """Optional marker for a deliberately ignored protocol message."""

    command: str
    reason: str | None = None
