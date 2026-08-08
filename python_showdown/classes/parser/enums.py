from enum import Enum


class SourceType(str, Enum):
    MOVE = "move"
    ITEM = "item"
    ABILITY = "ability"
    STATUS = "status"
    WEATHER = "weather"
    TERRAIN = "terrain"
    SIDE_CONDITION = "side_condition"
    RECOIL = "recoil"
    UNKNOWN = "unknown"


class Weather(str, Enum):
    SUNNY_DAY = "SunnyDay"
    RAIN = "RainDance"
    HAIL = "Hail"
    SNOW = "Snow"
    CLEAR_SKY = "none"  # default
    SANDSTORM = "Sandstorm"


class Stat(str, Enum):
    ATK = "atk"
    DEF = "def"
    SPA = "spa"
    SPD = "spd"
    SPE = "spe"
    EVA = "evasion"
    ACC = "accuracy"


class SideCondition(str, Enum):
    SPIKES = "Spikes"
    TOXIC_SPIKES = "Toxic Spikes"
    STEALTH_ROCK = "Stealth Rock"
    REFLECT = "Reflect"
    SAFEGUARD = "Safeguard"
    LIGHT_SCREEN = "Light Screen"


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
    ATTRACT = "Attract"
    YAWN = "Yawn"
    TYPECHANGE = "typechange"
    PERISH_SONG = "perish"  # countdown tracked separately in `perish_count`
    FLASH_FIRE = "Flash Fire"  # ability-granted immunity flag
    WRAP = "Wrap"  # trapping moves such as Wrap, Bind, Clamp, etc.
    FLINCH = "Flinch"
    RECHARGE = "Recharge"
    FLY = "Fly"
    DIVE = "Dive"
    TUNNEL = "Tunnel"
    PROTECT = "Protect"
    NIGHTMARE = "Nightmare"
    ENDURE = "Endure"
    TRAPPED = "Trapped"
    WHIRLPOOL = "Whirlpool"
    ROOST = "Roost"
    TAUNT = "Taunt"
    FOCUS_PUNCH = "Focus Punch"
