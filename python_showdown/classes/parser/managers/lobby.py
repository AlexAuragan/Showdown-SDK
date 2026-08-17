"""Lobby-scope protocol handling: login, format list, and challenges.

These messages are not part of any battle room; they arrive on the global
connection and mutate client *session* state (the ``ready`` flag, the
``named`` flag, the available ``formats``, and challenge futures) rather than
any ``BattleState``. They are produced as events so they flow through the same
``handle_message -> events -> update_client`` pipeline as battle messages, but
each event overrides ``update_client`` directly and never touches battle state.
"""

from typing import override

from python_showdown.classes.client.utils import parse_formats
from python_showdown.classes.combat_handler.battle_manager import BattleManager
from python_showdown.classes.parser.events import BaseEvent
from python_showdown.classes.parser.events.lobby import (
    FormatsEvent,
    NameTakenEvent,
    PrivateMessageEvent,
    UpdateUserEvent,
)
from python_showdown.classes.parser.managers.base import MessageParser
from python_showdown.classes.parser.models import ProtocolMessage
from python_showdown.classes.parser.protocol import require_arguments


class LobbyParser(MessageParser):
    """Handles non-battle session messages: room routing, login, formats, and challenges."""

    @override
    def handle_message(
        self,
        manager: BattleManager,
        message: ProtocolMessage,
    ) -> list[BaseEvent]:
        command = message.command

        if command == "updateuser":
            return self._handle_update_user(message)
        if command == "nametaken":
            return [NameTakenEvent(message.raw)]
        if command == "formats":
            return self._handle_formats(message)
        if command == "pm":
            return self._handle_pm(message)
        if command in ["customgroups", "challstr", "updatesearch"]:
            return []

        raise ValueError("Unhandled message", message)
        # return [] # TEMP

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
