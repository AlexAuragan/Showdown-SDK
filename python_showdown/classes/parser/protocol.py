import re
from collections.abc import Sequence

from python_showdown.classes.parser.models import ProtocolAnnotation, ProtocolMessage

IGNORED_COMMANDS = {
    "",
    "title",
    "J",
    "L",
    "t:",
    "rule",
    "teamsize",
    "start",
    "updatesearch",
    "upkeep",
    "-anim",
    "-message",
    "challstr",
    "message",
    "bigerror",
    "expire",  # TODO for client
    "deinit",  # TODO for client
    "popup",
    "sentchoice",
}


# A move is complete once the next top-level action or phase starts.
MOVE_BOUNDARY_COMMANDS = {
    "move",
    "cant",
    "switch",
    "drag",
    "replace",
    "turn",
    "upkeep",
    "request",
    "win",
    "tie",
    "init",
    "player",
    "room",
}

ANNOTATION_PATTERN = re.compile(r"^\[(?P<name>[^\]]+)\](?:\s*(?P<value>.*))?$")
LEVEL_PATTERN = re.compile(r"(?:^|,\s*)L(?P<level>\d+)(?:,|$)")


def parse_protocol_message(line: str) -> ProtocolMessage:
    """Normalize one raw Pokémon Showdown protocol line without interpreting it."""

    raw = line.rstrip("\r\n")

    if raw.startswith(">"):
        return ProtocolMessage("room", (raw[1:],), (), raw)

    if not raw.startswith("|"):
        raise ValueError(f"Protocol line must start with '|' or '>': {raw!r}")

    parts = raw.split("|")
    command = parts[1] if len(parts) > 1 else ""
    fields: Sequence[str] = parts[2:] if len(parts) > 2 else ()
    arguments: list[str] = []
    annotations: list[ProtocolAnnotation] = []

    for field in fields:
        match = ANNOTATION_PATTERN.fullmatch(field)
        if match is None:
            arguments.append(field)
            continue

        value = match.group("value") or None
        annotations.append(ProtocolAnnotation(match.group("name"), value))

    return ProtocolMessage(command, tuple(arguments), tuple(annotations), raw)


def annotation_value(message: ProtocolMessage, name: str) -> str | None:
    for annotation in message.annotations:
        if annotation.name == name:
            return annotation.value
    return None


def has_annotation(
    message: ProtocolMessage,
    name: str,
) -> bool:
    return any(annotation.name == name for annotation in message.annotations)


def extract_protocol_line(line: str, *, has_log_timestamp: bool) -> str:
    """Strip a trailing newline and, for replayed log files, the leading timestamp.

    Live websocket lines are bare protocol payloads (``|...|`` or ``>roomid``).
    Raw log files recorded by the client are prefixed with a ``<seq> <time>``
    stamp followed by the protocol line, e.g. ``123 1700000000 |turn|1``.
    """
    line = line.rstrip("\r\n")
    if not has_log_timestamp:
        return line
    parts = line.split(" ", 2)
    if len(parts) != 3:
        raise ValueError(f"Invalid timestamped log line: {line!r}")
    return parts[2]


def is_ignored_message(message: ProtocolMessage) -> bool:
    return message.command in IGNORED_COMMANDS


def is_move_boundary(message: ProtocolMessage) -> bool:
    return message.command in MOVE_BOUNDARY_COMMANDS


def require_arguments(message: ProtocolMessage, count: int) -> None:
    if len(message.arguments) < count:
        raise ValueError(
            f"Expected {count} arguments for {message.command!r}, "
            + f"got {len(message.arguments)} in {message.raw!r}"
        )
