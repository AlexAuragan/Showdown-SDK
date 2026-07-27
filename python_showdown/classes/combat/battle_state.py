from python_showdown.classes.pokemon.moves import AvailableMove
from python_showdown.classes.pokemon.pokemon import EnemyPokemon, PartyPokemon, Unknown


class BattleState:
    def __init__(self):
        self._team: list[PartyPokemon] = []
        # Enemy team starts as 6 unknown placeholders that get filled in as
        # the opponent switches pokemon in.
        self._enemy_team: list[EnemyPokemon] = [
            EnemyPokemon(active=False) for _ in range(6)
        ]
        self._curr_pokemon: str = ""
        self._curr_enemy_pokemon: str = ""
        self._available_moves: list[AvailableMove] = []
        self.force_switch: bool = False

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
        assert len(self._team) <=6, self._team
        return self._team

    @property
    def enemy_team(self) -> list[EnemyPokemon]:
        assert len(self._enemy_team) <= 6
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
        self._team = []
        self._enemy_team = [EnemyPokemon(active=False) for _ in range(6)]
        self._curr_pokemon = ""
        self._curr_enemy_pokemon = ""
        self._available_moves = []
        self.force_switch = False

    def update_moves(self, moves: list[AvailableMove]) -> None:
        self._available_moves = moves

    def update_team(self, team: list[PartyPokemon]) -> None:
        self._team = team

    def witness_move(self, move: str) -> None:
        pokemon_name = self._curr_enemy_pokemon

        if move.lower() in ["struggle"]:
            return

        pokemon = self.get_enemy_pokemon(pokemon_name)
        assert pokemon is not None
        pokemon.witness_move(move)

    def witness_switch_in(self, pokemon_id: str, lvl: int) -> None:
        # The previously-active enemy is no longer on the field.
        for p in self.enemy_team:
            if p.active:
                p.active = False

        pokemon = self.get_enemy_pokemon(pokemon_id=pokemon_id, not_found_ok=True)
        if pokemon is None:
            pokemon = EnemyPokemon(id=pokemon_id, lvl=lvl, active=True)
            idx = None
            for i, p in enumerate(self.enemy_team):
                if p.id == Unknown.VALUE:
                    idx = i
                    break
            if idx is None:
                raise ValueError(
                    "The enemy party is full of known pokemon but we're trying "
                    f"to add a new pokemon ({pokemon_id}); maybe a pokemon changed id?"
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
    ) -> None:
        pokemon = self.get_enemy_pokemon(pokemon_id)
        assert pokemon is not None

        index = self.enemy_team.index(pokemon)

        _target_slot, target_species = target_id.split(": ", 1)
        player_slot = pokemon_id.split(": ", 1)[0]

        transformed_id = f"{player_slot}: {target_species}"

        transformed = EnemyPokemon(
            id=transformed_id,
            lvl=pokemon.lvl,
            active=True,
        )

        transformed.curr_hp_percent = pokemon.curr_hp_percent
        transformed.status = pokemon.status

        self._enemy_team[index] = transformed
        self._curr_enemy_pokemon = transformed_id
