"""Shared feature-layer primitives.

The feature layer is the stable, ML-facing boundary of the SDK:

    domain state -> to_features() -> typed feature objects

It deliberately knows nothing about tensors, embeddings, or normalization;
see the module docstring of ``showdown_sdk.features`` for the contract.
"""

from collections.abc import Callable
from dataclasses import dataclass

from showdown_sdk.models import to_id

JSONScalar = str | int | float | bool | None

SIDE_SELF = "self"
SIDE_OPPONENT = "opponent"


## Data models


@dataclass(frozen=True)
class Knowledge[T]:
    """A value whose observability status is part of the data itself.

    - ``Knowledge(known=False, value=None)`` -> unknown.
    - ``Knowledge(known=True, value=None)`` -> known to be absent.
    - ``Knowledge(known=True, value=x)`` -> known value.
    """

    known: bool
    value: T | None = None


@dataclass(frozen=True)
class StatFeatures:
    hp: int | None = None
    attack: int | None = None
    defense: int | None = None
    special_attack: int | None = None
    special_defense: int | None = None
    speed: int | None = None


@dataclass(frozen=True)
class StatusFeatures:
    major: str | None = None
    attack_stage: int = 0
    defense_stage: int = 0
    special_attack_stage: int = 0
    special_defense_stage: int = 0
    speed_stage: int = 0
    accuracy_stage: int = 0
    evasion_stage: int = 0
    minor: tuple[str, ...] = ()
    perish_count: int | None = None
    must_recharge: bool = False


@dataclass(frozen=True)
class PokemonRefFeatures:
    """A stable reference to a Pokémon involved in a history event."""

    side: str  # "self" | "opponent"
    slot: int | None = None
    pokemon_id: str | None = None
    species: str | None = None


## Helpers


def known_none() -> Knowledge[str]:
    return Knowledge(known=True, value=None)


def unknown() -> Knowledge[str]:
    return Knowledge(known=False, value=None)


def knowledge(value: str | None) -> Knowledge[str]:
    """Convert ``Unknown VALUE``-style tri-state input into ``Knowledge``.

    ``None`` means known absence; a non-empty string is a known value.
    Values are canonicalized so knowledge is comparable across spellings.
    """
    if value is None or value == "":
        return known_none()
    return Knowledge(known=True, value=canonical(value))


def canonical(name: str) -> str:
    """Canonical Showdown ID for a species/item/ability/status name."""
    return to_id(name)


def canonical_move(name: str) -> str:
    """Canonical move ID, collapsing Hidden Power / Return / Frustration."""
    value = to_id(name)

    if value.startswith("hiddenpower"):
        return "hiddenpower"

    if value.startswith("return") and value[6:].isdigit():
        return "return"

    if value.startswith("frustration") and value[11:].isdigit():
        return "frustration"

    return value


def hp_ratio(curr_hp: int, max_hp: int | None) -> float | None:
    if max_hp is None or max_hp <= 0:
        return None
    return curr_hp / max_hp


def apply(fn: Callable[[str], str], value: str | None) -> str | None:
    return None if value is None else fn(value)
