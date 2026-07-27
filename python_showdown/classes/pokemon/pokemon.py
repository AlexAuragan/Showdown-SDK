from dataclasses import dataclass, field
from enum import Enum

from .stats import Stats, Status


class Unknown(Enum):
    VALUE = "unknown"


@dataclass
class PartyPokemon:
    id: str
    details: str
    active: bool
    lvl: int
    curr_hp: int
    max_hp: int
    stats: Stats
    moves: list[str]
    base_ability: str
    item: str
    pokeball: str
    status: Status

@dataclass
class EnemyPokemon:
    active: bool
    id: str | Unknown = Unknown.VALUE
    lvl: int | Unknown = Unknown.VALUE
    curr_hp_percent: int = 100
    fainted: bool = False
    base_ability: str | Unknown = Unknown.VALUE
    item: str | Unknown = Unknown.VALUE
    status: Status = field(default_factory=Status)
    learnt_moves: list[str | Unknown] = field(default_factory=lambda: [Unknown.VALUE] * 4)
    replaced_moves: dict[str | Unknown, str] = field(default_factory=dict)

    @property
    def available_moves(self):
        return [self.replaced_moves.get(move, move) for move in self.learnt_moves]

    def witness_move(self, move: str):
        if move in self.available_moves:
            return

        if Unknown.VALUE not in self.learnt_moves:
            raise ValueError(f"Witnessed move {move} for pokemon {self} but got already all the moves set")

        self.learnt_moves.remove(Unknown.VALUE)
        self.learnt_moves.append(move)
