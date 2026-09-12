from dataclasses import dataclass
from enum import IntFlag

from showdown_sdk.exceptions import MalformedProtocolError


class FormatFlag(IntFlag):
    RANDOM_TEAM = 1
    SEARCH = 2
    CHALLENGE = 4
    TOURNAMENT = 8
    LEVEL_50 = 16
    BEST_OF_DEFAULT = 64
    TERA_PREVIEW_DEFAULT = 128
    ITEM_CLAUSE_DEFAULT = 256


@dataclass
class Format:
    name: str
    flags: FormatFlag
    section: str
    column: int

    @property
    def uses_random_team(self) -> bool:
        return FormatFlag.RANDOM_TEAM in self.flags

    @property
    def can_search(self) -> bool:
        return FormatFlag.SEARCH in self.flags

    @property
    def can_challenge(self) -> bool:
        return FormatFlag.CHALLENGE in self.flags

    @property
    def can_tournament(self) -> bool:
        return FormatFlag.TOURNAMENT in self.flags


@dataclass(slots=True)
class BattleResult:
    room_id: str
    winner: str | None
    move_count: int
    duration_seconds: float

    @property
    def average_seconds_per_move(self) -> float:
        if self.move_count == 0:
            return 0.0

        return self.duration_seconds / self.move_count


## Format parsing


def parse_format_entry(entry: str, section: str, column: int) -> Format:
    name, separator, raw_flags = entry.rpartition(",")

    if not separator:
        raise MalformedProtocolError(f"Malformed format entry: {entry!r}")

    try:
        flags = FormatFlag(int(raw_flags, 16))
    except ValueError as error:
        raise MalformedProtocolError(
            f"Malformed format flags in entry: {entry!r}"
        ) from error

    return Format(name=name, flags=flags, section=section, column=column)


def parse_formats(line: str) -> list[Format]:

    entries = line.split("|")[2:]

    formats: list[Format] = []
    section = ""
    column = 0

    index = 0

    # First entry is currently protocol metadata such as ",LL".
    if entries and entries[0].startswith(","):
        index += 1

    while index < len(entries):
        entry = entries[index]

        if entry.startswith(","):
            marker = entry[1:]

            if marker.isdigit():
                column = int(marker)

                index += 1
                if index >= len(entries):
                    raise MalformedProtocolError(
                        "Section marker has no section name"
                    )

                section = entries[index]
            else:
                # Preserve unknown metadata instead of pretending
                # it is a format.
                pass

        else:
            formats.append(
                parse_format_entry(entry, section=section, column=column)
            )

        index += 1

    return formats
