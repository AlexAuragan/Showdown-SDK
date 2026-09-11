from typing import TYPE_CHECKING

from showdown_sdk.classes.parser.events.lobby import (
    FormatsEvent,
    LobbyEvent,
    NameTakenEvent,
    PrivateMessageEvent,
    TeamRejectedEvent,
    TeamValidEvent,
    UpdateUserEvent,
    UserNotFoundEvent,
)
from showdown_sdk.models.sdk import TeamRejectedError

if TYPE_CHECKING:
    from showdown_sdk.classes.client.client import Client


def apply_lobby_event(client: Client, event: LobbyEvent) -> None:
    match event:
        case UpdateUserEvent():
            _apply_update_user(client, event)

        case NameTakenEvent():
            client.ready.clear()
            raise RuntimeError(
                f"Username was rejected by the server: {event.raw}"
            )

        case FormatsEvent():
            client.formats = event.formats

        case PrivateMessageEvent():
            _apply_private_message(client, event)

        case TeamRejectedEvent():
            future = client.team_validation_future

            if future is not None and not future.done():
                future.set_exception(TeamRejectedError(reasons=event.reasons))

        case TeamValidEvent():
            future = client.team_validation_future

            if future is not None and not future.done():
                future.set_result(None)

        case UserNotFoundEvent():
            raise RuntimeError(f"User not found: {event.user}")

        case _:
            raise NotImplementedError(type(event))


def _apply_update_user(client: Client, event: UpdateUserEvent) -> None:
    client.named = event.named

    if not event.named:
        return

    expected = client.battle_manager.player_username

    if expected is None:
        client.username = event.username
        client.ready.set()
        return

    if event.username == expected:
        client.username = event.username
        client.ready.set()


def _apply_private_message(client: Client, event: PrivateMessageEvent) -> None:
    future = client.challenge_future
    challenged_user = client.challenged_user

    if future is None or future.done() or challenged_user is None:
        return

    if (
        client.username
        and event.sender.lower() == client.username.lower()
        and event.receiver.lower() == challenged_user.lower()
        and event.message.startswith("/challenge ")
    ):
        format_id = event.message.split("|", 1)[0].removeprefix("/challenge ")
        future.set_result(format_id)
