import json
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from showdown_sdk.models import to_id
from showdown_sdk.models.pokemon import (
    AvailableMove,
    EnemyPokemon,
    PartyPokemon,
    SideCondition,
    Status,
    Unknown,
)
from showdown_sdk.utils import SerializableObject, to_serializable_object

if TYPE_CHECKING:
    from showdown_sdk.classes.parser.events.base import BaseEvent


## Data models


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


@dataclass
class ActivePokemonState:
    pokemon_id: str = ""
    forme: str | None = None
    status: Status = field(default_factory=Status)
    transformed_into: str | None = None
    ability: str | Unknown = Unknown.VALUE
    type_override: tuple[str, ...] | None = None
    trapped: bool = False
    maybe_trapped: bool = False

    def clear(self):
        self.pokemon_id = ""
        self.forme = None
        self.status = Status()
        self.transformed_into = None
        self.ability = Unknown.VALUE
        self.type_override = None
        self.trapped = False
        self.maybe_trapped = False

    def to_dict(self):
        return {
            "pokemon_id": self.pokemon_id,
            "forme": self.forme,
            "status": asdict(self.status),
            "transformed_into": self.transformed_into,
            "ability": self.ability
            if self.ability is not Unknown.VALUE
            else None,
            "type_override": self.type_override,
            "trapped": self.trapped,
            "maybe_trapped": self.maybe_trapped,
        }


@dataclass
class BattleFormat:
    gen: int | None = None
    gametype: str | None = None
    tier: str | None = None

    def clear(self):
        self.gen = None
        self.gametype = None
        self.tier = None


## Public API


class BattleState:
    def __init__(self):
        self._player_id: str | None = None
        self.turn: int = 0
        self._team: list[PartyPokemon] = []
        # Enemy team starts as 6 unknown placeholders that get filled in as
        # the opponent switches pokemon in.
        self._enemy_team: list[EnemyPokemon] = [
            EnemyPokemon(id=Unknown.VALUE, lvl=100) for _ in range(6)
        ]
        self._curr_enemy_pokemon: str = ""
        self.active_pokemon: ActivePokemonState = ActivePokemonState()
        self._available_moves: list[AvailableMove] = []
        self.force_switch: bool = False
        self.weather: str | None = None
        self.side_conditions: dict[str, dict[SideCondition, int]] = {}

        self.gen_1_desync: bool = False  # Gen 1 can experience desync by design, this can mess up  # the witnessed moves

        self.history: list[BaseEvent] = []

        # format data
        self.format: BattleFormat = BattleFormat()

        self.custom_showdown_battlestate: SerializableObject | None = None

        self._enemy_pre_switch_snapshot: tuple[int, EnemyPokemon] | None = None

    @property
    def player_id(self) -> str | None:
        return self._player_id

    @player_id.setter
    def player_id(self, value: str) -> None:
        self._player_id = value

    @property
    def gen(self) -> int:
        gen = self.format.gen
        if gen is None:
            raise ValueError("gen not initialized yet")
        return gen

    def to_dict(self) -> SerializableObject:
        data = {
            "player_id": self._player_id,
            "team": self._team,
            "enemy_team": self._enemy_team,
            "active_pokemon": self.active_pokemon.to_dict(),
            "available_moves": self._available_moves,
            "force_switch": self.force_switch,
            "weather": self.weather,
            "side_conditions": self.side_conditions,
            "format": asdict(self.format),
        }

        out = to_serializable_object(data)
        return out

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict()
            # indent=2,
            # sort_keys=True,
        )

    def history_json(self) -> list[SerializableObject]:
        return [event.to_dict() for event in self.history]

    def get_pokemon(self, pokemon_id: str) -> PartyPokemon:
        for pokemon in self.team:
            if pokemon.id == pokemon_id:
                return pokemon
        raise ValueError(
            f"Pokemon with id {pokemon_id} not found in team {self.team}"
        )

    def get_curr_pokemon(self) -> PartyPokemon:
        return self.get_pokemon(self.curr_pokemon)

    def get_enemy_pokemon(
        self, pokemon_id: str, not_found_ok: bool = False
    ) -> EnemyPokemon | None:
        for pokemon in self.enemy_team:
            if pokemon.id == pokemon_id:
                return pokemon
        if not_found_ok:
            return None
        raise ValueError(
            f"Pokemon with id {pokemon_id} not found in enemy team {self.enemy_team}"
        )

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
        return self.active_pokemon.pokemon_id

    @property
    def curr_enemy_pokemon(self) -> str:
        return self._curr_enemy_pokemon

    def set_active_pokemon(self, pokemon_id: str) -> None:
        self.active_pokemon.pokemon_id = pokemon_id

    def clear_battle(self) -> None:
        """Discard all state learned during the current battle."""

        self._player_id = None
        self.turn = 0
        self._team = []
        self._enemy_team = [
            EnemyPokemon(id=Unknown.VALUE, lvl=100) for _ in range(6)
        ]

        self.active_pokemon.clear()
        self._curr_enemy_pokemon = ""
        self._available_moves = []

        self.force_switch = False
        self.weather = None
        self.side_conditions = {}

        self.gen_1_desync = False
        self.history = []
        self.custom_showdown_battlestate = None
        self._enemy_pre_switch_snapshot = None
        self.format.clear()

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
        species: str,
        gender: str | None = None,
        shiny: bool = False,
    ) -> None:
        self._enemy_pre_switch_snapshot = None

        # The previously-active enemy is no longer on the field.
        for p in self.enemy_team:
            if p.active:
                p.active = False
                p.reset_on_switch_in()

        pokemon = self.get_enemy_pokemon(
            pokemon_id=pokemon_id, not_found_ok=True
        )

        if pokemon is None:
            pokemon = EnemyPokemon(
                id=pokemon_id,
                lvl=lvl,
                active=True,
                species=species,
                gender=gender,
                shiny=shiny,
            )

            idx = None

            for i, p in enumerate(self.enemy_team):
                if p.id is Unknown.VALUE:
                    idx = i
                    break

            if idx is None:
                raise ValueError(
                    "The enemy party is full of known pokemon but we're trying "
                    + f"to add a new pokemon ({pokemon_id}); maybe a pokemon changed id?",
                    f"Pokemon: {self.enemy_team}",
                )

            self._enemy_team.pop(idx)
            self._enemy_team.append(pokemon)

        else:
            pokemon_index = self._enemy_team.index(pokemon)

            self._enemy_pre_switch_snapshot = (pokemon_index, deepcopy(pokemon))

            pokemon.active = True
            pokemon.lvl = lvl
            pokemon.gender = gender
            pokemon.shiny = shiny
            pokemon.species = species

        self._curr_enemy_pokemon = pokemon_id

    def witness_replace(
        self,
        pokemon_id: str,
        *,
        species: str,
        lvl: int,
        gender: str | None = None,
        shiny: bool = False,
    ) -> None:
        """
        Handle Showdown's |replace| message.

        This is not a switch. The currently-active public identity was an
        Illusion and is being corrected to the actual Pokémon identity.
        Therefore volatile/current battle state must not be reset.
        """

        current = self.get_enemy_pokemon(
            self._curr_enemy_pokemon, not_found_ok=True
        )

        if current is None or not current.active:
            raise RuntimeError(
                "Received replace but there is no active enemy Pokémon"
            )

        snapshot_info = self._enemy_pre_switch_snapshot

        # The snapshot can only ever apply to this one |replace|.
        self._enemy_pre_switch_snapshot = None

        # Normal case:
        #
        # The apparent Pokémon had never been seen before, so it did not
        # overwrite/reuse an existing enemy record. We can simply correct
        # that record in place.
        if snapshot_info is None:
            existing = self.get_enemy_pokemon(pokemon_id, not_found_ok=True)

            if existing is not None and existing is not current:
                raise RuntimeError(
                    "Illusion replacement resolved to an already-known enemy "
                    + f"Pokémon: {pokemon_id!r}"
                )

            current.id = pokemon_id
            current.species = species
            current.lvl = lvl
            current.gender = gender
            current.shiny = shiny

            self._curr_enemy_pokemon = pokemon_id
            return

        # Collision case:
        #
        # The apparent identity matched an already-known Pokémon. `current`
        # therefore contains a mixture of:
        #
        # - knowledge belonging to the real previously-known Pokémon
        # - battle state observed during the Illusion user's current appearance
        #
        # Restore the real Pokémon first.
        snapshot_index, original = snapshot_info

        if self._enemy_team[snapshot_index] is not current:
            raise RuntimeError(
                "Enemy switch snapshot no longer matches the active Pokémon"
            )

        # Work out which normal moves were learned during this particular
        # appearance rather than inherited from the impersonated Pokémon.
        original_move_ids = {
            to_id(move)
            for move in original.learnt_moves
            if move is not Unknown.VALUE
        }

        newly_observed_moves = [
            move
            for move in current.learnt_moves
            if move is not Unknown.VALUE
            and to_id(move) not in original_move_ids
        ]

        # Restore the genuine previously-known Pokémon to exactly the state it
        # had before the Illusion user appeared.
        original.active = False
        self._enemy_team[snapshot_index] = original

        # The actual Illusion user may itself have been revealed earlier.
        actual = self.get_enemy_pokemon(pokemon_id, not_found_ok=True)

        if actual is original:
            raise RuntimeError(
                "Illusion user and impersonated Pokémon have the same protocol "
                + f"id {pokemon_id!r}; BattleState currently requires unique "
                + "enemy Pokémon ids"
            )

        if actual is None:
            # The Illusion user is being revealed for the first time.
            #
            # Start from unknown identity-dependent information rather than
            # copying the impersonated Pokémon's known ability/item/moves.
            actual = EnemyPokemon(
                active=True,
                id=pokemon_id,
                lvl=lvl,
                gender=gender,
                shiny=shiny,
                curr_hp_percent=current.curr_hp_percent,
                fainted=current.fainted,
                status=deepcopy(current.status),
                temporary_moves=list(current.temporary_moves),
                disabled_moves=list(current.disabled_moves),
                transformed_into=current.transformed_into,
                type_override=current.type_override,
                forme=current.forme,
                species=species,
            )

            # Only preserve ability/item information if it changed during this
            # appearance. An unchanged value may simply have been inherited
            # from the impersonated Pokémon's old record.
            if current.base_ability != original.base_ability:
                actual.base_ability = current.base_ability

            if current.current_ability != original.current_ability:
                actual.current_ability = current.current_ability

            if current.item != original.item:
                actual.item = current.item

            for move in newly_observed_moves:
                actual.witness_move(move)

            unknown_index = None

            for i, pokemon in enumerate(self._enemy_team):
                if pokemon.id is Unknown.VALUE:
                    unknown_index = i
                    break

            if unknown_index is None:
                raise RuntimeError(
                    "Illusion revealed a new enemy Pokémon but no unknown "
                    + "enemy party slot remains"
                )

            self._enemy_team.pop(unknown_index)
            self._enemy_team.append(actual)

        else:
            actual.active = True
            actual.lvl = lvl
            actual.gender = gender
            actual.shiny = shiny
            actual.species = species

            actual.curr_hp_percent = current.curr_hp_percent
            actual.fainted = current.fainted
            actual.status = deepcopy(current.status)
            actual.temporary_moves = list(current.temporary_moves)
            actual.disabled_moves = list(current.disabled_moves)
            actual.transformed_into = current.transformed_into
            actual.type_override = current.type_override
            actual.forme = current.forme

            if current.base_ability != original.base_ability:
                actual.base_ability = current.base_ability

            if current.current_ability != original.current_ability:
                actual.current_ability = current.current_ability

            if current.item != original.item:
                actual.item = current.item

            for move in newly_observed_moves:
                actual.witness_move(move)

        self._curr_enemy_pokemon = pokemon_id

    def witness_transform(
        self,
        pokemon_id: str,
        target_species: str,
        copied_moves: list[str] | None = None,
    ) -> None:
        pokemon = self.get_enemy_pokemon(pokemon_id)
        assert pokemon is not None

        # `transformed_into` stores the target's effective species/form, not
        # the protocol ident (which may be a nickname).
        pokemon.transformed_into = target_species
        # The base moveset is wholly replaced by the copied set.
        if copied_moves is not None:
            pokemon.temporary_moves = list(copied_moves)
