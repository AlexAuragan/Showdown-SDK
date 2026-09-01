import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from urllib.request import Request, urlopen

from python_showdown.models.pokemon.status import EVs, IVs
from python_showdown.models.sdk.exceptions import TeamRejectedError
from python_showdown.models.sdk.pokemon_set import PokemonSet, TeamSet
from python_showdown.utils.serialization import (
    Serializable,
    SerializableObject,
    expect_array,
    expect_object,
)


class SampleTeamGenerator:
    BASE_URL: str = "https://play.pokemonshowdown.com/data/sets"

    def __init__(self, seed: int | None = None) -> None:
        self._random: random.Random = random.Random(seed)

        # format -> species -> list of sets
        self._cache: dict[
            str,
            dict[str, list[SerializableObject]],
        ] = {}

    async def _generate(
        self,
        format_name: str,
        team_size: int = 6,
    ) -> TeamSet:
        sets = await self._get_sets(format_name)

        species = list(sets)

        if len(species) < team_size:
            raise ValueError(
                f"Format {format_name!r} only has "
                + f"{len(species)} species with available sets"
            )

        selected_species = self._random.sample(
            species,
            k=team_size,
        )

        pokemon: list[PokemonSet] = []

        for species_name in selected_species:
            available_sets = sets[species_name]

            selected_set = self._random.choice(available_sets)

            pokemon.append(
                self._build_pokemon(
                    species_name,
                    selected_set,
                    format_name,
                )
            )

        return TeamSet(pokemon)

    async def generate(
        self,
        format_name: str,
        validator: Callable[[TeamSet], Awaitable[None]],
        max_attempts: int = 100,
    ) -> TeamSet:
        last_error: TeamRejectedError | None = None

        for _ in range(max_attempts):
            team = await self._generate(format_name)

            try:
                await validator(team)
            except TeamRejectedError as error:
                last_error = error
                continue

            return team

        raise RuntimeError(
            "Could not generate a valid team for "
            + f"{format_name!r} after {max_attempts} attempts"
        ) from last_error

    async def _get_sets(
        self,
        format_name: str,
    ) -> dict[str, list[SerializableObject]]:
        if format_name in self._cache:
            return self._cache[format_name]

        data = await asyncio.to_thread(
            self._fetch,
            format_name,
        )

        sets: dict[str, list[SerializableObject]] = {}

        # Showdown currently exposes sources such as:
        #
        # {
        #     "dex": {...},
        #     "stats": {...},
        # }
        #
        # Merge them to maximize variety.
        for source_name in ("dex", "stats"):
            source = data.get(source_name)

            if not isinstance(source, dict):
                continue

            for species, raw_sets in source.items():
                if not isinstance(raw_sets, dict):
                    continue

                species_sets = sets.setdefault(
                    species,
                    [],
                )

                for raw_set in raw_sets.values():
                    if isinstance(raw_set, dict):
                        raw_set = expect_object(raw_set)
                        species_sets.append(raw_set)

        sets = {
            species: species_sets
            for species, species_sets in sets.items()
            if species_sets
        }

        if not sets:
            raise ValueError(f"No sets found for format {format_name!r}")

        self._cache[format_name] = sets
        return sets

    def _fetch(
        self,
        format_name: str,
    ) -> SerializableObject:
        url = f"{self.BASE_URL}/{format_name}.json"

        request = Request(
            url,
            headers={
                "User-Agent": "python-showdown-sdk",
            },
        )

        with urlopen(request, timeout=10) as response:
            raw = response.read()

        return expect_object(json.loads(raw))

    def _build_pokemon(
        self,
        species: str,
        data: SerializableObject,
        format_name: str,
    ) -> PokemonSet:
        generation = self._generation(format_name)

        moves = self._moves(data.get("moves"))

        evs = self._evs(
            data.get("evs"),
            generation,
        )

        ivs = self._ivs(data.get("ivs"))

        return PokemonSet(
            species=species,
            moves=moves,
            item=self._string_choice(data.get("item")),
            ability=self._string_choice(data.get("ability")),
            nature=self._string_choice(data.get("nature")),
            evs=evs,
            ivs=ivs,
            gender=self._string_choice(data.get("gender")),
            shiny=data.get("shiny") is True,
            level=self._integer(
                data.get("level"),
                default=100,
            ),
            happiness=self._integer(
                data.get("happiness"),
                default=255,
            ),
            hidden_power_type=self._string_choice(data.get("hpType")),
        )

    def _moves(
        self,
        value: Serializable,
    ) -> list[str]:

        value = expect_array(value)
        moves: list[str] = []

        for move in value:
            selected = self._string_choice(move)

            if selected is None:
                raise ValueError(f"Invalid move choice: {move!r}")

            moves.append(selected)

        if not 1 <= len(moves) <= 4:
            raise ValueError(f"Invalid number of moves: {moves!r}")

        return moves

    def _evs(
        self,
        value: Serializable,
        generation: int,
    ) -> EVs:
        # Showdown's set importer fills Gen 1/2 EVs to
        # maximum when the source omits them.
        if generation <= 2:
            defaults = {
                "hp": 252,
                "atk": 252,
                "def": 252,
                "spa": 252,
                "spd": 252,
                "spe": 252,
            }
        else:
            defaults = {
                "hp": 0,
                "atk": 0,
                "def": 0,
                "spa": 0,
                "spd": 0,
                "spe": 0,
            }

        if isinstance(value, dict):
            value = expect_object(value)
            for stat in defaults:
                stat_value = value.get(stat)

                if isinstance(stat_value, int):
                    defaults[stat] = stat_value

        # Showdown's importer uses one Speed EV when a
        # Gen 3+ source set completely omits EVs.
        elif generation >= 3:
            defaults["spe"] = 1

        return EVs(
            hp=defaults["hp"],
            atk=defaults["atk"],
            def_=defaults["def"],
            spa=defaults["spa"],
            spd=defaults["spd"],
            spe=defaults["spe"],
        )

    def _ivs(
        self,
        value: Serializable,
    ) -> IVs:
        values = {
            "hp": 31,
            "atk": 31,
            "def": 31,
            "spa": 31,
            "spd": 31,
            "spe": 31,
        }

        if isinstance(value, dict):
            value = expect_object(value)
            for stat in values:
                stat_value = value.get(stat)

                if isinstance(stat_value, int):
                    values[stat] = stat_value

        return IVs(
            hp=values["hp"],
            atk=values["atk"],
            def_=values["def"],
            spa=values["spa"],
            spd=values["spd"],
            spe=values["spe"],
        )

    def _string_choice(
        self,
        value: Serializable,
    ) -> str | None:
        if isinstance(value, str):
            return value

        if isinstance(value, list):
            choices = [choice for choice in value if isinstance(choice, str)]

            if choices:
                return self._random.choice(choices)

        return None

    @staticmethod
    def _integer(
        value: Serializable,
        default: int,
    ) -> int:
        if isinstance(value, int) and not isinstance(
            value,
            bool,
        ):
            return value

        return default

    @staticmethod
    def _generation(format_name: str) -> int:
        if (
            not format_name.startswith("gen")
            or len(format_name) < 4
            or not format_name[3].isdigit()
        ):
            raise ValueError(f"Cannot determine generation from {format_name!r}")

        generation = int(format_name[3])

        if not 1 <= generation <= 5:
            raise ValueError(
                "SampleTeamGenerator currently supports generations 1 through 5"
            )

        return generation
