from dataclasses import dataclass

from showdown_sdk.exceptions import BattleStateInvariantError
from showdown_sdk.models.pokemon import EnemyPokemon, MajorStatus, PartyPokemon
from showdown_sdk.models.sdk import BattleState, SourceType

## Data models


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

    def get(self, battle_state: BattleState) -> EnemyPokemon | PartyPokemon:
        if self.player == battle_state.player_id:
            return battle_state.get_pokemon(self.name)
        pokemon = battle_state.get_enemy_pokemon(self.name)
        if not isinstance(pokemon, EnemyPokemon):
            raise BattleStateInvariantError(
                f"Expected enemy Pokemon for {self.name!r}, got {pokemon!r}"
            )
        return pokemon


@dataclass(frozen=True)
class PokemonDetails:
    level: int
    gender: str | None
    shiny: bool


@dataclass(frozen=True)
class EffectSource:
    """What caused an effect, and the move action it belongs to when applicable."""

    type: SourceType
    name: str | None = None
    actor: PokemonIdent | None = None  # Pokemon causing the effect
    action_id: int | None = None
    owner: PokemonIdent | None = None  # Item/ability owner


@dataclass(frozen=True)
class RequestMove:
    name: str
    id: str
    curr_pp: int | None  # No PP for Recharge or Struggle
    max_pp: int | None
    target: str | None
    disabled: bool
    disabled_source: str | None  # Why the move was disabled


@dataclass(frozen=True)
class RequestPokemon:
    ident: str
    details: str
    level: int
    active: bool

    atk: int
    def_: int
    spa: int
    spd: int
    spe: int

    moves: tuple[str, ...]
    base_ability: str
    item: str
    pokeball: str

    curr_hp: int
    max_hp: int | None
    major_status: MajorStatus | None
