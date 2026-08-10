from dataclasses import dataclass, field

from python_showdown.models.pokemon.status import MajorStatus, MinorStatus


@dataclass
class StatChange:
    """A stat-stage change caused directly by a move (not an ability)."""
    target: str
    stat: str
    delta: int

# TODO Move this
@dataclass
class MoveEvent:
    """A single resolved move and its coarse outcome, recorded in the battle
    state history so a caller can answer "did the last enemy move hit, was it
    super-effective, how much did it do, what status did it inflict?" without
    re-reading the raw logs.

    Units: `damage` and `resulting_hp` use whichever scale we can observe for
    the target -- absolute HP for our own pokemon, percentage points for the
    enemy (HP Percentage Mod hides their absolute HP). `resulting_hp` is set
    only when the move did damage.
    """
    turn: int
    move: str
    user: str
    target: str
    user_side: str  # "self" | "enemy"
    hit: bool = True
    damage: int | None = None
    resulting_hp: int | None = None
    effectiveness: float = 1.0
    failed: bool = False
    is_critical: bool = False
    statuses_inflicted: list[MajorStatus | MinorStatus] = field(default_factory=list)
    stat_changes: list[StatChange] = field(default_factory=list)
