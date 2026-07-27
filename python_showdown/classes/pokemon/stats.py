from dataclasses import dataclass
from typing import Literal


@dataclass
class Stats:
    atk: int
    def_: int
    spa: int
    spd: int
    spe: int
    max_hp: int


@dataclass
class Status:
    # stats
    atk_stage: int = 0
    def_stage: int = 0
    spa_stage: int = 0
    spd_stage: int = 0
    spe_stage: int = 0
    eva_stage: int = 0
    acc_stage: int = 0

    # status
    is_para: bool = False
    is_psn: bool = False
    is_burn: bool = False
    is_frz: bool = False
    is_slp: bool = False
    is_conf: bool = False

    # special
    has_substitute: bool = False
    is_leech_seeded: bool = False
    must_recharge: bool = False

    # Stat boost stages range from -6 to +6.
    _MIN_STAGE = -6
    _MAX_STAGE = 6

    def reset_on_switch(self):
        # Volatile state clears on switch; major status conditions persist.
        self.atk_stage = 0
        self.def_stage = 0
        self.spa_stage = 0
        self.spd_stage = 0
        self.spe_stage = 0
        self.eva_stage = 0
        self.acc_stage = 0

        self.is_conf = False
        self.has_substitute = False
        self.is_leech_seeded = False
        self.must_recharge = False

    def _check_statuses(self):
        major = int(self.is_burn) + int(self.is_frz) + int(
            self.is_para
        ) + int(self.is_psn) + int(self.is_slp)
        if major > 1:
            raise ValueError(
                "Only one major status can be set between is_burn "
                f"({self.is_burn}), is_frz ({self.is_frz}), is_para "
                f"({self.is_para}), is_psn ({self.is_psn}), is_slp "
                f"({self.is_slp})."
            )

    def __post_init__(self):
        self._check_statuses()

    def set_status(self, status: Literal["par", "psn", "brn", "frz", "slp"]) -> None:
        """Apply a major status condition using the server's abbreviation.

        The server sends `|-status|<id>|par|psn|brn|frz|slp`. Confusion is not
        a major status and is handled separately via set_conf/clear_conf.
        """
        self.is_burn = False
        self.is_frz = False
        self.is_para = False
        self.is_slp = False

        match status:
            case "par":
                self.is_para = True
            case "psn":
                self.is_psn = True
            case "brn":
                self.is_burn = True
            case "frz":
                self.is_frz = True
            case "slp":
                self.is_slp = True

    def clear_status(self, status: Literal["par", "psn", "brn", "frz", "slp"]) -> None:
        match status:
            case "par":
                self.is_para = False
            case "psn":
                self.is_psn = False
            case "brn":
                self.is_burn = False
            case "frz":
                self.is_frz = False
            case "slp":
                self.is_slp = False

    def set_conf(self) -> None:
        self.is_conf = True

    def clear_conf(self) -> None:
        self.is_conf = False

    def boost(self, stat: Literal["atk", "def", "spa", "spd", "spe", "evasion", "accuracy"], n: int) -> None:
        self._adjust_stage(stat, n)

    def unboost(self, stat: Literal["atk", "def", "spa", "spd", "spe", "evasion", "accuracy"], n: int) -> None:
        self._adjust_stage(stat, -n)

    def _adjust_stage(self, stat: str, delta: int) -> None:
        attr = self._stage_attr(stat)
        current = getattr(self, attr)
        setattr(self, attr, self._clamp(current + delta))

    @staticmethod
    def _clamp(stage: int) -> int:
        return max(Status._MIN_STAGE, min(Status._MAX_STAGE, stage))

    @staticmethod
    def _stage_attr(stat: str) -> str:
        match stat:
            case "atk":
                return "atk_stage"
            case "def":
                return "def_stage"
            case "spa":
                return "spa_stage"
            case "spd":
                return "spd_stage"
            case "spe":
                return "spe_stage"
            case "evasion":
                return "eva_stage"
            case "accuracy":
                return "acc_stage"
            case _:
                raise ValueError(f"Unknown stat: {stat!r}")
