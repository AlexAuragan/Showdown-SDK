from dataclasses import dataclass

from python_showdown.models.pokemon.pokemon import EnemyPokemon, PartyPokemon
from python_showdown.models.pokemon.status import MajorStatus
from python_showdown.models.sdk.battle_state import BattleState, SourceType


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
        print(self.player, battle_state.player_id)
        if self.player == battle_state.player_id:
            return battle_state.get_pokemon(self.name)
        pokemon = battle_state.get_enemy_pokemon(self.name)
        assert isinstance(pokemon, EnemyPokemon)
        return pokemon


@dataclass(frozen=True)
class EffectSource:
    """What caused an effect, and the move action it belongs to when applicable."""

    type: SourceType
    name: str | None = None
    actor: PokemonIdent | None = None
    action_id: int | None = None


@dataclass(frozen=True)
class RequestMove:
    name: str
    id: str
    curr_pp: int | None  # No PP for Recharge or Struggle
    max_pp: int | None
    target: str | None
    disabled: bool


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
