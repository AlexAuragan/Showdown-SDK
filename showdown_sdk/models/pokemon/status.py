from dataclasses import dataclass, field
from enum import Enum
from typing import cast

from showdown_sdk.exceptions import DexDataError
from showdown_sdk.models import dex

## Data models


class Stat(str, Enum):
    ATK = "atk"
    DEF = "def"
    SPA = "spa"
    SPD = "spd"
    SPE = "spe"
    EVA = "evasion"
    ACC = "accuracy"


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
    REPEAT = "Repeat"  # The pokémon must repeat its last move
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

    # GEN 1 only
    REFLECT = "reflect"
    LIGHT_SCREEN = "light screen"


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
    def from_pokemon(pokemon_id: str, gen: int):
        pokemon_data = dex.gen(gen).pokemon(pokemon_id)
        if not isinstance(pokemon_data, dict):
            raise DexDataError(
                f"Expected dex data for {pokemon_id!r} to be a dict, "
                + f"got {type(pokemon_data).__name__}",
                key=pokemon_id,
            )
        evs = pokemon_data["baseStats"]
        if not isinstance(evs, dict):
            raise DexDataError(
                f"Expected baseStats for {pokemon_id!r} to be a dict, "
                + f"got {type(evs).__name__}",
                key=pokemon_id,
            )
        evs = cast(dict[str, int], evs)
        return EVs(
            hp=evs["hp"],
            atk=evs["atk"],
            def_=evs["def"],
            spa=evs["spa"],
            spd=evs["spd"],
            spe=evs["spe"],
        )


## Public API


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

    # Volatiles that the SDK inferred itself (no protocol message tells us),
    # keyed to the number of turns they remain active.
    _minor_durations: dict[MinorStatus, int] = field(default_factory=dict)

    # Set via |-mustrecharge| and consumed by the next `|cant|recharge`.
    must_recharge: bool = False

    trapped_by_side: str | None = None

    _MIN_STAGE: int = -6
    _MAX_STAGE: int = 6

    def set_trapped(self, source_side: str) -> None:
        self.minor.add(MinorStatus.TRAPPED)
        self.trapped_by_side = source_side

    def clear_trapped(self) -> None:
        self.minor.discard(MinorStatus.TRAPPED)
        self.trapped_by_side = None

    def reset_on_switch(self) -> None:
        # Volatile state clears on switch; major status conditions persist.
        self.reset_all_stages()

        self.minor.clear()
        self._minor_durations.clear()
        self.perish_count = None
        self.must_recharge = False
        self.trapped_by_side = None

    def set_status(self, status: MajorStatus | str) -> None:
        """Apply a major status condition.

        Accepts the server's abbreviation (`par`/`psn`/`brn`/`frz`/`slp`/`tox`)
        either as a `MajorStatus` member or its raw string value. Confusion is
        NOT a major status; it is handled via `minor`.
        """
        self.major = (
            status if isinstance(status, MajorStatus) else MajorStatus(status)
        )

    def clear_status(self, status: MajorStatus | str) -> None:
        """Clear a major status condition if it matches the one currently set."""
        current = self.major
        if current is None:
            return
        token = (
            status if isinstance(status, MajorStatus) else MajorStatus(status)
        )
        if current == token:
            self.major = None

    def clear_all_major_status(self) -> None:
        """Clear the major status condition regardless of which one it is.

        Used by Aromatherapy/Heal Bell (`|-cureteam|`). Stages and volatile
        effects (substitute, leech seed, ...) are NOT touched.
        """
        self.major = None

    def add_minor(
        self, status: MinorStatus, *, duration: int | None = None
    ) -> None:
        self.minor.add(status)

        if duration is not None:
            self._minor_durations[status] = max(duration, 1)
        else:
            self._minor_durations.pop(status, None)

    def remove_minor(self, status: MinorStatus) -> None:
        self.minor.discard(status)
        self._minor_durations.pop(status, None)

    def tick_minor_durations(self) -> None:
        """Decrement turn-limited minor statuses; drop the expired ones.

        Showdown silently removes partially trapped (no |-end| line, at least
        in Gen 1), so the SDK must expire volatiles it inferred itself.
        """
        for effect, remaining in list(self._minor_durations.items()):
            if remaining <= 1:
                self.remove_minor(effect)
            else:
                self._minor_durations[effect] = remaining - 1

    def has_minor(self, status: MinorStatus) -> bool:
        return status in self.minor

    def boost(self, stat: Stat, n: int) -> None:
        self._adjust_stage(stat, n)

    def unboost(self, stat: Stat, n: int) -> None:
        self._adjust_stage(stat, -n)

    def copy_stat_changes(self, source: Status) -> None:
        for stat in Stat:
            self._set_stage(stat, source._get_stage(stat))

    def reset_all_stages(self) -> None:
        """Clear every stat stage to 0 (e.g. |-clearallboost|, Haze)."""
        for stat in Stat:
            self._set_stage(stat, 0)

    def clear_negative_stages(self) -> None:
        for stat in Stat:
            self._set_stage(stat, max(self._get_stage(stat), 0))

    @staticmethod
    def _clamp(stage: int) -> int:
        return max(Status._MIN_STAGE, min(Status._MAX_STAGE, stage))

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

    def clear_single_turn(self) -> None:
        self.minor.difference_update(
            {
                MinorStatus.ENDURE,
                MinorStatus.PROTECT,
                MinorStatus.ROOST,
                MinorStatus.FOCUS_PUNCH,
            }
        )

    def clear_single_move(self) -> None:
        self.minor.difference_update(
            {MinorStatus.DESTINY_BOUND, MinorStatus.GRUDGE}
        )
