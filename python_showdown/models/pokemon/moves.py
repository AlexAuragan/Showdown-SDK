from dataclasses import dataclass


@dataclass
class AvailableMove:
    """In-combat move data."""
    name: str
    id: str
    curr_pp: int
    max_pp: int
    target: str
    disabled: bool
