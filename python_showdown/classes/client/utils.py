from __future__ import annotations

import re
from itertools import groupby
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from python_showdown.classes.client.client import Client


from .dt import Format, FormatFlag

# `p1a: Miltank` -> `p1: Miltank`: strip the single slot letter that action
# lines carry but `|request|` side-level idents do not.
_SLOT_RE = re.compile(r"^(p\d+)[a-h](: )")


def split_protocol(
    line: str, prefix: str, *, min_parts: int, maxsplit: int = -1
) -> list[str]:
    """Split a protocol payload and fail loudly when required fields are missing."""
    payload = line.removeprefix(prefix)
    parts = payload.split("|", maxsplit) if maxsplit >= 0 else payload.split("|")
    if len(parts) < min_parts:
        raise RuntimeError(
            f"Malformed {prefix.rstrip('|')} message: expected at least "
            f"{min_parts} fields, got {len(parts)} in {line!r}"
        )
    return parts


def parse_format_entry(
    entry: str,
    section: str,
    column: int,
) -> Format:
    name, separator, raw_flags = entry.rpartition(",")

    if not separator:
        raise ValueError(f"Malformed format entry: {entry!r}")

    return Format(
        name=name,
        flags=FormatFlag(int(raw_flags, 16)),
        section=section,
        column=column,
    )

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
                    raise ValueError(
                        "Section marker has no section name"
                    )

                section = entries[index]
            else:
                # Preserve unknown metadata instead of pretending
                # it is a format.
                pass

        else:
            formats.append(
                parse_format_entry(
                    entry,
                    section=section,
                    column=column,
                )
            )

        index += 1

    return formats


def print_formats(formats: list[Format]) -> None:
    sorted_formats = sorted(
        formats,
        key=lambda format_: (
            format_.column,
            format_.section,
            format_.name,
        ),
    )

    for (column, section), section_formats in groupby(
        sorted_formats,
        key=lambda format_: (
            format_.column,
            format_.section,
        ),
    ):
        print()
        print(f"Column {column}: {section}")
        print("=" * (len(section) + 10))

        rows: list[list[str]] = []

        for format_ in section_formats:
            flags = FormatFlag(format_.flags)

            rows.append([
                format_.name,
                "Y" if FormatFlag.RANDOM_TEAM in flags else "",
                "Y" if FormatFlag.SEARCH in flags else "",
                "Y" if FormatFlag.CHALLENGE in flags else "",
                "Y" if FormatFlag.TOURNAMENT in flags else "",
                "Y" if FormatFlag.LEVEL_50 in flags else "",
                "Y" if FormatFlag.BEST_OF_DEFAULT in flags else "",
                "Y" if FormatFlag.TERA_PREVIEW_DEFAULT in flags else "",
                "Y" if FormatFlag.ITEM_CLAUSE_DEFAULT in flags else "",
            ])

        headers = [
            "Format",
            "Team",
            "Search",
            "Challenge",
            "Tour",
            "Lv50",
            "Bo",
            "Tera",
            "Items",
        ]

        _print_table(headers, rows)


def _print_table(
    headers: list[str],
    rows: list[list[str]],
) -> None:
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]

    separator = (
        "+-"
        + "-+-".join("-" * width for width in widths)
        + "-+"
    )

    def render_row(row: list[str]) -> str:
        values = [
            value.ljust(widths[index])
            for index, value in enumerate(row)
        ]

        return "| " + " | ".join(values) + " |"

    print(separator)
    print(render_row(headers))
    print(separator)

    for row in rows:
        print(render_row(row))

    print(separator)
