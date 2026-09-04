from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

from .status import Stats, Status


class Unknown(Enum):
    VALUE = "unknown"


@dataclass
class Pokemon:
    active: bool
    id: str | Unknown
    lvl: int
    status: Status = field(default_factory=Status, kw_only=True)


@dataclass
class PartyPokemon(Pokemon):
    details: str
    curr_hp: int
    max_hp: int
    stats: Stats
    moves: list[str]
    base_ability: str
    item: str
    pokeball: str



@dataclass
class EnemyPokemon(Pokemon):
    gender: str | None = None
    shiny: bool = False
    curr_hp_percent: int = 100
    fainted: bool = False
    base_ability: str | Unknown = Unknown.VALUE
    current_ability: str | Unknown = Unknown.VALUE
    item: str | Unknown | None = Unknown.VALUE
    learnt_moves: list[str | Unknown] = field(
        default_factory=lambda: [Unknown.VALUE] * 4
    )
    temporary_moves: list[str] = field(default_factory=list)
    disabled_moves: list[str] = field(default_factory=list)
    transformed_into: str | None = None
    # Current species form, relayed by |-formechange|. Only relabels the
    # species/type; never changes the move set. None = base form.
    forme: str | None = None

    @property
    def available_moves(self) -> Sequence[str | Unknown]:
        """The move set the pokemon can currently use.

        While transformed (Ditto), the base set is wholly replaced by the copied
        moves (`temporary_moves` only). Mimic disables its own slot but keeps the
        rest of the base set, so the copied move is added on top. Unknown
        placeholders never count as usable moves.
        """
        if self.transformed_into is not None:
            return self.temporary_moves
        base = [
            m
            for m in self.learnt_moves
            if m is not Unknown.VALUE and m not in self.disabled_moves
        ]
        return base + self.temporary_moves

    def witness_move(self, move: str) -> None:
        if move.lower() in ["struggle"]:
            return

        if move in self.learnt_moves or move in self.temporary_moves:
            return

        # While transformed, new moves are part of the copied set.
        if self.transformed_into is not None:
            if move not in self.temporary_moves:
                self.temporary_moves.append(move)
            return

        if Unknown.VALUE not in self.learnt_moves:
            raise ValueError(
                f"Witnessed move {move} for pokemon {self} "
                + "but all move slots are already filled"
            )

        self.learnt_moves.remove(Unknown.VALUE)
        self.learnt_moves.append(move)

    def reset_on_switch_in(self) -> None:
        # Volatile transform/mimic/form state clears on switch; the persistent
        # base moveset knowledge (learnt_moves) is kept.
        self.status.reset_on_switch()
        self.transformed_into = None
        self.temporary_moves = []
        self.disabled_moves = []
        self.forme = None
