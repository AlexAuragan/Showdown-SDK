from enum import Enum


class Weather(str, Enum):
    SUNNY_DAY = "SunnyDay"
    RAIN = "RainDance"
    HAIL = "Hail"
    SNOW = "Snow"
    CLEAR_SKY = "none"  # default
    SANDSTORM = "Sandstorm"


class SideCondition(str, Enum):
    SPIKES = "Spikes"
    TOXIC_SPIKES = "Toxic Spikes"
    STEALTH_ROCK = "Stealth Rock"
    REFLECT = "Reflect"
    SAFEGUARD = "Safeguard"
    LIGHT_SCREEN = "Light Screen"
