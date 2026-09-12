# pyright: reportImportCycles=false
# The import cycle is for typing only, changing the project architecture would probably add more overhead.
from abc import ABC
from dataclasses import dataclass

from showdown_sdk.classes.parser.models import (
    ProtocolAnnotation,
    ProtocolMessage,
)
from showdown_sdk.exceptions import UnhandledEventError
from showdown_sdk.utils import SerializableObject, to_serializable_object

## Events


@dataclass(frozen=True)
class BaseEvent(ABC):
    """A complete semantic event derived from one or more protocol messages."""

    def to_dict(self) -> SerializableObject:
        return {
            "event_type": self.__class__.__name__,
            **to_serializable_object(self),
        }


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
            action_id=action_id,
        )


@dataclass(frozen=True)
class DiscardedEvent(BaseEvent):
    """Optional marker for a deliberately ignored protocol message."""

    command: str
    reason: str | None = None


## Helpers


def unhandled_event(
    message: ProtocolMessage, action_id: int | None = None
) -> UnhandledEvent:
    raise UnhandledEventError(
        f"Unhandled event {message.command!r}: {message.raw!r}",
        command=message.command,
        raw=message.raw,
        action_id=action_id,
    )
    # return UnhandledEvent(message.command, message.arguments, message.annotations, message.raw, action_id)
