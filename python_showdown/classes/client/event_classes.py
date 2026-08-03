
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from python_showdown.classes.pokemon.pokemon import Pokemon

if TYPE_CHECKING:
    from python_showdown.classes.client.client import Client


class Source(Enum):
    own_attack = "own_attack"
    enemy_attack = "enemy_attack"
    own_item = "own_item"
    enemy_item = "enemy_item"
    own_ability = "own_ability"
    enemy_ability = "enemy_ability"
    own_terrain = "own_terrain"
    enemy_terrain = "enemy_terrain"
    stats = "status"

class Target(Enum):
    active_pokemon = "active_pokemon"
    enemy_pokemon = "enemy_pokemon"

class Weather(Enum):
    sunny_day = "sunny_day"
    rain = "rain"
    hail = "hail"
    snow = "snow"
    clear_sky = "clear_sky" # default
    sandstorm = "sandstorm"

class Stat(Enum):
    atk = "atk"
    def_ = "def"
    spa = "spa"
    spd = "spd"
    spe = "spe"
    eva = "eva"
    acc = "acc"

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
    PERISH_SONG = "perish"        # countdown tracked separately in `perish_count`
    FLASH_FIRE = "Flash Fire"     # ability-granted immunity flag
    WRAP = "Wrap"                 # trapping moves (Wrap/Bind/Clamp/...)
    FLINCH = "Flinch"
    RECHARGE = "Recharge"
    FLY = "Fly"
    DIVE = "Dive"
    TUNNEL = "Tunnel"



@dataclass
class BaseEvent(ABC):
    # @abstractmethod # TODO, add later because every Event should be able to edit the battle state, much like git edits
    def update_battle_state(self, battle_state) -> None:
        pass

    def update_client(self, client: Client) -> None:
        self.update_battle_state(client.battle_state)
@dataclass
class MoveEvent(BaseEvent):
    move: str
    success: bool # The move did not fail
    does_hit: bool # The move did not miss
    source: Target
    target: Target


@dataclass
class DamageEvent(BaseEvent):
    source: Source
    target: Target
    curr_hp: int
    max_hp: int
    effectiveness: float
    crit: bool

@dataclass
class HealEvent(BaseEvent):
    source: Source
    target: Target
    curr_hp: int
    max_hp: int

@dataclass
class MinorStatusEvent(BaseEvent):
    source: Source
    target: Target
    effect: MinorStatus

@dataclass
class MajorStatusEvent(BaseEvent):
    source: Source
    target: Target
    status: MajorStatus

@dataclass
class StatChangeEvent(BaseEvent):
    source: Source
    Target: Target
    stat_changes: list[tuple[Stat, int]]

@dataclass
class PokemonSwitchEvent(BaseEvent):
    target: Target
    pokemon_id: str
    level: int
    curr_hp: int
    max_hp: int
    major_status: MajorStatus | None
    def update_battle_state(self, battle_state) -> None:
        if self.target is Target.active_pokemon:
            pass
        else:
            pass

@dataclass
class DiscardedEvent(BaseEvent):
    def update_battle_state(self, battle_state) -> None:
        return



@dataclass
class TurnEvent(BaseEvent):
    turn: int
    def update_battle_state(self, battle_state) -> None:
        return
