from dataclasses import dataclass

from python_showdown.classes.parser.enums import SourceType


@dataclass(frozen=True)
class ProtocolAnnotation:
    """An annotation such as `[from] psn`, `[of] p1a: Pikachu`, or `[silent]`."""

    name: str
    value: str | None = None


@dataclass(frozen=True)
class ProtocolMessage:
    """A normalized, lossless Pokémon Showdown protocol line."""

    command: str
    arguments: tuple[str, ...]
    annotations: tuple[ProtocolAnnotation, ...]
    raw: str


@dataclass(frozen=True)
class PokemonIdent:
    """A protocol Pokémon reference such as `p1a: Poliwhirl` or `p1: Snorlax`."""

    player: str
    slot: str | None
    name: str


@dataclass(frozen=True)
class EffectSource:
    """What caused an effect, and the move action it belongs to when applicable."""

    type: SourceType
    name: str | None = None
    actor: PokemonIdent | None = None
    action_id: int | None = None
