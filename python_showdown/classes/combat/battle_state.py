import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import TYPE_CHECKING

from python_showdown.classes.parser import SideCondition
from python_showdown.classes.pokemon.moves import AvailableMove
from python_showdown.classes.pokemon.pokemon import EnemyPokemon, PartyPokemon, Unknown

if TYPE_CHECKING:
    from python_showdown.classes.client.client import Client
def _json_default(obj):
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (set, frozenset)):
        # Status.minor is a set[MinorStatus]; sort by the enum's server token
        # for stable snapshot output.
        return sorted(o.value if isinstance(o, Enum) else o for o in obj)
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class BattleState:
    def __init__(self, client: Client):
        self._client = client
        self._team: list[PartyPokemon] = []
        # Enemy team starts as 6 unknown placeholders that get filled in as
        # the opponent switches pokemon in.
        self._enemy_team: list[EnemyPokemon] = [
            EnemyPokemon(active=False, id=Unknown.VALUE, lvl=100) for _ in range(6)
        ]
        self._curr_pokemon: str = ""
        self._curr_enemy_pokemon: str = ""
        self._available_moves: list[AvailableMove] = []
        self.force_switch: bool = False
        self.weather: str | None = None
        self.side_conditions: dict[str, dict[SideCondition, int]] = {}

    @property
    def player_id(self) -> str:
        return self._client.battle_player_id

    @player_id.setter
    def player_id(self, value: str) -> None:
        self._client.battle_player_id = value

    def to_json(self) -> str:
        return json.dumps(
            {
                "team": [asdict(p) for p in self._team],
                "enemy_team": [asdict(p) for p in self._enemy_team],
                "curr_pokemon": self._curr_pokemon,
                "curr_enemy_pokemon": self._curr_enemy_pokemon,
                "available_moves": [asdict(m) for m in self._available_moves],
                "force_switch": self.force_switch,
                "weather": self.weather,
                "side_conditions": self.side_conditions,
            },
            indent=2,
            sort_keys=True,
            default=_json_default,
        )

    def get_pokemon(self, pokemon_id: str) -> PartyPokemon:
        for pokemon in self.team:
            if pokemon.id == pokemon_id:
                return pokemon
        raise ValueError(f"Pokemon with id {pokemon_id} not found in team {self.team}")

    def get_enemy_pokemon(self, pokemon_id: str, not_found_ok: bool = False) -> EnemyPokemon | None:
        for pokemon in self.enemy_team:
            if pokemon.id == pokemon_id:
                return pokemon
        if not_found_ok:
            return None
        raise ValueError(f"Pokemon with id {pokemon_id} not found in enemy team {self.enemy_team}")

    @property
    def team(self) -> list[PartyPokemon]:
        return self._team

    @property
    def enemy_team(self) -> list[EnemyPokemon]:
        return self._enemy_team

    @property
    def available_moves(self) -> list[AvailableMove]:
        return self._available_moves

    @property
    def curr_pokemon(self) -> str:
        return self._curr_pokemon

    @property
    def curr_enemy_pokemon(self) -> str:
        return self._curr_enemy_pokemon

    def set_active_pokemon(self, pokemon_id: str) -> None:
        self._curr_pokemon = pokemon_id

    def reset(self) -> None:
        """Clear all tracked state so the same handler can drive a new battle."""
        self.player_id = ""
        self._team = []
        self._enemy_team = [EnemyPokemon(active=False, id=Unknown.VALUE, lvl=100) for _ in range(6)]
        self._curr_pokemon = ""
        self._curr_enemy_pokemon = ""
        self._available_moves = []
        self.force_switch = False
        self.side_conditions = {}

    def update_moves(self, moves: list[AvailableMove]) -> None:
        self._available_moves = moves

    def update_team(self, team: list[PartyPokemon]) -> None:
        self._team = team

    def witness_move(self, move: str) -> None:
        pokemon_name = self._curr_enemy_pokemon

        if move.lower() == "struggle":
            return

        pokemon = self.get_enemy_pokemon(pokemon_name)
        assert pokemon is not None
        pokemon.witness_move(move)

    def witness_switch_in(
        self,
        pokemon_id: str,
        lvl: int,
        gender: str | None = None,
        shiny: bool = False,
    ) -> None:
        # The previously-active enemy is no longer on the field.
        for p in self.enemy_team:
            if p.active:
                p.active = False

        pokemon = self.get_enemy_pokemon(pokemon_id=pokemon_id, not_found_ok=True)
        if pokemon is None:
            pokemon = EnemyPokemon(id=pokemon_id, lvl=lvl, active=True, gender=gender, shiny=shiny)
            idx = None
            for i, p in enumerate(self.enemy_team):
                if p.id is Unknown.VALUE:
                    idx = i
                    break
            if idx is None:
                raise ValueError(
                    "The enemy party is full of known pokemon but we're trying "
                    f"to add a new pokemon ({pokemon_id}); maybe a pokemon changed id?",
                    f"Pekemon: {self.enemy_team}"
                )
            self._enemy_team.pop(idx)
            self._enemy_team.append(pokemon)
        else:
            pokemon.active = True

        self._curr_enemy_pokemon = pokemon_id

    def witness_transform(
        self,
        pokemon_id: str,
        target_id: str,
        copied_moves: list[str] | None = None,
    ) -> None:
        pokemon = self.get_enemy_pokemon(pokemon_id)
        assert pokemon is not None

        pokemon.transformed_into = target_id
        # The base moveset is wholly replaced by the copied set.
        if copied_moves is not None:
            pokemon.temporary_moves = list(copied_moves)
