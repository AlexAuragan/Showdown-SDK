from dataclasses import dataclass, field
from enum import Enum


class MajorStatus(str, Enum):
    SLEEP = "slp"
    POISON = "psn"
    TOXIC = "tox"
    PARALYSIS = "par"
    BURN = "brn"
    FREEZE = "frz"
    FAINT = "fnt"


class MinorStatus(str, Enum):
    CONFUSION = "confusion"
    LEECH_SEED = "Leech Seed"
    SUBSTITUTE = "Substitute"
    ENCORE = "Encore"
    YAWN = "Yawn"
    TYPECHANGE = "typechange"
    PERISH_SONG = "perish"  # countdown tracked separately in `perish_count`
    FLASH_FIRE = "Flash Fire"  # ability-granted immunity flag
    # WRAP = "Wrap"  # trapping moves such as Wrap, Bind, Clamp, etc, should not be in here
    FLINCH = "Flinch"
    RECHARGE = "Recharge"
    FLY = "Fly"
    DIVE = "Dive"
    TUNNEL = "Tunnel"
    NIGHTMARE = "Nightmare"
    TRAPPED = "Trapped"
    REPEAT = "Repeat" # The pokémon must repeat its last move
    # WHIRLPOOL = "Whirlpool" Trapping move
    ROOST = "Roost"
    TAUNT = "Taunt"
    FOCUS_PUNCH = "Focus Punch"
    IMPRISON = "Imprison"
    FORESIGHT = "Foresight"
    MAGNET_RISE = "Magnet Rise"
    CURSE = "Curse"
    TORMENT = "Torment"
    # TODO
    # Magma storm sets "partiallytrapped" ,an effect actually made by "bind", "Wrap", etc. Currently we say that "wrap"
    # is the status, while arguably it's the source of the "trapped"/"partiallytrapped" status.
    # Gen 1 and 4 handle these differently, but it's the role of this SDK to unify those and treat edge cases and gen diffs.
    DESTINY_BOUND = "destinybond"
    PARTIALLY_TRAPPED = "partiallytrapped"
    ENDURE = "endure"
    ATTRACT = "attract"
    GRUDGE = "grudge"
    PROTECT = "protect"

@dataclass
class Status:
    # Stat boost stages (-6..+6).
    atk_stage: int = 0
    def_stage: int = 0
    spa_stage: int = 0
    spd_stage: int = 0
    spe_stage: int = 0
    eva_stage: int = 0
    acc_stage: int = 0

    # At most one major status condition at a time (None = healthy).
    major: MajorStatus | None = None

    # Volatile status effects applied via |-start|; any subset may be active.
    minor: set[MinorStatus] = field(default_factory=set)

    # Perish Song countdown (3..0); None = not active. Stored separately from
    # `minor` because the server sends `perish3`/`perish2`/`perish1`/`perish0`.
    perish_count: int | None = None

    # Set via |-mustrecharge| and consumed by the next `|cant|recharge`.
    must_recharge: bool = False

    _MIN_STAGE: int = -6
    _MAX_STAGE: int = 6

    def reset_on_switch(self):
        # Volatile state clears on switch; major status conditions persist.
        self.atk_stage = 0
        self.def_stage = 0
        self.spa_stage = 0
        self.spd_stage = 0
        self.spe_stage = 0
        self.eva_stage = 0
        self.acc_stage = 0

        self.minor.clear()
        self.perish_count = None
        self.must_recharge = False

    def set_status(self, status: MajorStatus | str) -> None:
        """Apply a major status condition.

        Accepts the server's abbreviation (`par`/`psn`/`brn`/`frz`/`slp`/`tox`)
        either as a `MajorStatus` member or its raw string value. Confusion is
        NOT a major status; it is handled via `minor`.
        """
        self.major = status if isinstance(status, MajorStatus) else MajorStatus(status)

    def clear_status(self, status: MajorStatus | str) -> None:
        """Clear a major status condition if it matches the one currently set."""
        current = self.major
        if current is None:
            return
        token = status if isinstance(status, MajorStatus) else MajorStatus(status)
        if current == token:
            self.major = None

    def clear_all_major_status(self) -> None:
        """Clear the major status condition regardless of which one it is.

        Used by Aromatherapy/Heal Bell (`|-cureteam|`). Stages and volatile
        effects (substitute, leech seed, ...) are NOT touched.
        """
        self.major = None

    def add_minor(self, status: MinorStatus) -> None:
        self.minor.add(status)

    def remove_minor(self, status: MinorStatus) -> None:
        self.minor.discard(status)

    def has_minor(self, status: MinorStatus) -> bool:
        return status in self.minor

    def boost(self, stat: Stat, n: int) -> None:
        self._adjust_stage(stat, n)

    def unboost(self, stat: Stat, n: int) -> None:
        self._adjust_stage(stat, -n)

    def reset_all_stages(self) -> None:
        """Clear every stat stage to 0 (e.g. |-clearallboost|, Haze)."""
        self.atk_stage = 0
        self.def_stage = 0
        self.spa_stage = 0
        self.spd_stage = 0
        self.spe_stage = 0
        self.eva_stage = 0
        self.acc_stage = 0
    @staticmethod
    def _clamp(stage: int) -> int:
        return max(Status._MIN_STAGE, min(Status._MAX_STAGE, stage))

    @staticmethod
    def _stage_attr(stat: Stat) -> str:
        match stat:
            case stat.ATK:
                return "atk_stage"
            case stat.DEF:
                return "def_stage"
            case stat.SPA:
                return "spa_stage"
            case stat.SPD:
                return "spd_stage"
            case stat.SPE:
                return "spe_stage"
            case stat.EVA:
                return "eva_stage"
            case stat.ACC:
                return "acc_stage"

    def set_stage(self, stat: Stat, n: int) -> None:
        """Set a stage absolutely (e.g. Belly Drum sets atk to +6)."""
        self._set_stage(stat, self._clamp(n))

    def _adjust_stage(self, stat: Stat, delta: int) -> None:
        current = self._get_stage(stat)
        self._set_stage(stat, self._clamp(current + delta))

    def _get_stage(self, stat: Stat) -> int:
        match stat:
            case Stat.ATK:
                return self.atk_stage
            case Stat.DEF:
                return self.def_stage
            case Stat.SPA:
                return self.spa_stage
            case Stat.SPD:
                return self.spd_stage
            case Stat.SPE:
                return self.spe_stage
            case Stat.EVA:
                return self.eva_stage
            case Stat.ACC:
                return self.acc_stage

    def _set_stage(self, stat: Stat, stage: int) -> None:
        match stat:
            case Stat.ATK:
                self.atk_stage = stage
            case Stat.DEF:
                self.def_stage = stage
            case Stat.SPA:
                self.spa_stage = stage
            case Stat.SPD:
                self.spd_stage = stage
            case Stat.SPE:
                self.spe_stage = stage
            case Stat.EVA:
                self.eva_stage = stage
            case Stat.ACC:
                self.acc_stage = stage
class Stat(str, Enum):
    ATK = "atk"
    DEF = "def"
    SPA = "spa"
    SPD = "spd"
    SPE = "spe"
    EVA = "evasion"
    ACC = "accuracy"


@dataclass
class Stats:
    atk: int
    def_: int
    spa: int
    spd: int
    spe: int
    max_hp: int


@dataclass
class IVs:
    hp: int = 31
    atk: int = 31
    def_: int = 31
    spa: int = 31
    spd: int = 31
    spe: int = 31


@dataclass
class EVs:
    hp: int
    atk: int
    def_: int
    spa: int
    spd: int
    spe: int

    @staticmethod
    def from_pokemon(pokemon_id: str): #pyright:ignore[reportUnusedParameter]
        # TODO implement
        return EVs(100, 100, 100, 100, 100, 100)
