from typing import override


## Base errors
class ShowdownSDKError(Exception):
    """Base class for exceptions intentionally raised by Showdown SDK."""


class BattleReproductionError(ShowdownSDKError):
    """Base class for SDK battle failures that should dump reproduction context."""


## Protocol / Parsing
class ProtocolError(BattleReproductionError):
    """Base class for failures while interpreting Pokémon Showdown protocol data."""


class MalformedProtocolError(ProtocolError):
    """Raised when a Showdown protocol message is missing or contains invalid fields."""

    def __init__(
        self,
        message: str = "",
        *,
        raw: str | None = None,
        command: str | None = None,
    ) -> None:
        super().__init__(message)
        self.raw: str | None = raw
        self.command: str | None = command


class UnsupportedProtocolError(ProtocolError):
    """Raised when Showdown sends valid protocol behavior the SDK does not support yet."""

    def __init__(
        self,
        message: str = "",
        *,
        command: str | None = None,
        raw: str | None = None,
    ) -> None:
        super().__init__(message)
        self.command: str | None = command
        self.raw: str | None = raw


class ParserStateError(BattleReproductionError):
    """Raised when the parser reaches an impossible or incomplete internal state."""

    def __init__(self, message: str = "", *, state: str | None = None) -> None:
        super().__init__(message)
        self.state: str | None = state


class UnhandledEventError(BattleReproductionError):
    """Raised when a semantic event has no handler, reducer, or feature conversion."""

    def __init__(
        self,
        message: str = "",
        *,
        event_type: str | None = None,
        command: str | None = None,
        raw: str | None = None,
        action_id: int | None = None,
    ) -> None:
        super().__init__(message)
        self.event_type: str | None = event_type
        self.command: str | None = command
        self.raw: str | None = raw
        self.action_id: int | None = action_id


class EventInvariantError(BattleReproductionError):
    """Raised when a semantic event contains internally contradictory information."""

    def __init__(
        self, message: str = "", *, event_type: str | None = None
    ) -> None:
        super().__init__(message)
        self.event_type: str | None = event_type


## Battle state
class BattleStateInvariantError(BattleReproductionError):
    """Raised when battle information would make the SDK's BattleState inconsistent."""


class BattleStateMismatchError(BattleReproductionError):
    """Raised when the SDK BattleState disagrees with Showdown's authoritative state."""

    def __init__(
        self,
        message: str = "",
        *,
        path: str | None = None,
        sdk_value: object = None,
        showdown_value: object = None,
    ) -> None:
        super().__init__(message)
        self.path: str | None = path
        self.sdk_value: object = sdk_value
        self.showdown_value: object = showdown_value


class BattleSyncError(BattleReproductionError):
    """Raised when room, request, parser, client, or battle-manager state becomes desynchronized."""


## Features
class FeatureExtractionError(BattleReproductionError):
    """Raised when BattleState cannot be converted into a valid feature representation."""


class VectorizationError(BattleReproductionError):
    """Raised when feature vectors violate the configured vector schema or dimensions."""

    def __init__(
        self,
        message: str = "",
        *,
        expected_dim: int | None = None,
        actual_dim: int | None = None,
    ) -> None:
        super().__init__(message)
        self.expected_dim: int | None = expected_dim
        self.actual_dim: int | None = actual_dim


## Runtime
class ClientStateError(ShowdownSDKError):
    """Raised when a Client operation is attempted in an invalid session state."""


class BattleLifecycleError(ClientStateError):
    """Raised when battle creation, tracking, completion, or cleanup occurs out of order."""


class CombatHandlerError(ShowdownSDKError):
    """Raised when a combat handler cannot produce a usable action for a decision."""


class SDKTimeoutError(ShowdownSDKError):
    """Raised when an SDK-managed operation exceeds its allowed waiting time."""


## Server errors
class ServerResponseError(ShowdownSDKError):
    """Base class for explicit negative or exceptional responses from Showdown."""


class TeamRejectedError(ServerResponseError):
    """Raised when Showdown rejects a submitted team during validation."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__()
        self.reasons: list[str] = reasons

    @override
    def __str__(self) -> str:
        return (
            "The team was rejected for the following reason(s):\n"
            + "\n".join(self.reasons)
        )


class UsernameRejectedError(ServerResponseError):
    """Raised when Showdown refuses the username requested during login."""

    def __init__(self, message: str = "", *, raw: str | None = None) -> None:
        super().__init__(message)
        self.raw: str | None = raw


class UserNotFoundError(ServerResponseError):
    """Raised when Showdown reports that a requested user does not exist."""

    def __init__(self, message: str = "", *, user: str | None = None) -> None:
        super().__init__(message)
        self.user: str | None = user


class InvalidActionError(ServerResponseError):
    """Raised when Showdown rejects the action submitted for the current request."""

    def __init__(self, category: str, message: str, *args: object) -> None:
        super().__init__(*args)
        self.category: str = category
        self.message: str = message

    @override
    def __str__(self) -> str:
        return f"Last action was invalid: [{self.category}] {self.message}"


class ObsoleteRequestIdError(ServerResponseError):
    """Raised when an action refers to a request ID that Showdown no longer accepts."""

    def __init__(self, *args: object, request_id: int | None = None) -> None:
        self.request_id: int | None = request_id
        super().__init__(*args)

    @override
    def __str__(self) -> str:
        return (
            "Action id sent refers an invalid or outdated request id: "
            + f"{self.request_id}"
        )


## SDK Specific erros
class DexDataError(ShowdownSDKError):
    """Raised when required Dex data is missing, malformed, or incompatible with SDK expectations."""

    def __init__(
        self,
        message: str = "",
        *,
        path: object | None = None,
        key: str | None = None,
    ) -> None:
        super().__init__(message)
        self.path: object | None = path
        self.key: str | None = key


class LoggingError(ShowdownSDKError):
    """Raised when the SDK logging subsystem enters an invalid state or cannot perform I/O."""


class TeamGenerationError(ShowdownSDKError):
    """Raised when the SDK cannot generate a valid team from its team-generation source."""


class UnsupportedFeatureError(ShowdownSDKError):
    """Raised when the caller requests behavior the SDK deliberately does not support."""
