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
    TeamRejectedEvent,
    TeamValidEvent,
    UpdateUserEvent,
    UserNotFoundEvent,
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
        if command in ["customgroups", "challstr", "updatesearch", "clearpoke", "poke", "teampreview"]:
            return []
        if command == "popup":
            return self._handle_popup(message)

        raise ValueError("Unhandled message", message)
        # return [] # TEMP

    def _handle_popup(self, message: ProtocolMessage) -> list[BaseEvent]:
        if "- This format requires you to use your own team." in message.arguments:
            raise ValueError("No team provided but a team is required", message)
        if "Your selected format is invalid:" in message.arguments:
            raise ValueError("Invalid battle format", message.arguments)
        if "Your team was rejected for the following reasons:" in message.arguments:
            return [
                TeamRejectedEvent(
                    reasons=[arg for arg in message.arguments if arg.startswith("- ")]
                )
            ]
        if any("Your team is valid for" in arg for arg in message.arguments):
            return [TeamValidEvent()]
        for arg in message.arguments:
            if "The user" in arg and "was not found." in arg:
                user = arg.split("'")[1]
                return [UserNotFoundEvent(user)]
        if any(
            'You tried to send "/leave"' in arg
            and "you were not in that room" in arg
            for arg in message.arguments
        ):
            return []
        raise NotImplementedError(message)

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
