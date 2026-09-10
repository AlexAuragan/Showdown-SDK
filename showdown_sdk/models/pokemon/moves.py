from dataclasses import dataclass


@dataclass
class AvailableMove:
    """In-combat move data."""

    name: str
    id: str
    curr_pp: int | None
    max_pp: int | None
    target: str | None
    disabled: bool
