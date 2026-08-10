from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from python_showdown.classes.client.battle_manager import BattleManager
from python_showdown.classes.parser.events import BaseEvent
from python_showdown.classes.parser.models import ProtocolMessage

if TYPE_CHECKING:
    from python_showdown.classes.client.client import Client


class MessageManager(ABC):
    """A scoped handler for a subset of Pokémon Showdown protocol messages.

    The top-level :class:`Parser` is an aggregator: it routes each incoming
    :class:`ProtocolMessage` to the manager responsible for that message's
    scope (battle, lobby, ...). Each manager owns its own state and produces
    the semantic events for its scope; the aggregator then applies those events
    onto the client via ``event.update_client(client)``.

    Subclasses implement :meth:`handle_message`, which consumes one protocol
    message and returns the events it produced (possibly none, e.g. while a
    multi-message group such as a ``|move|`` block is still being accumulated).
    """

    @abstractmethod
    def handle_message(
        self,
        manager: BattleManager,
        message: ProtocolMessage,
    ) -> list[BaseEvent]:
        """Consume one protocol message and return the events it produced."""
