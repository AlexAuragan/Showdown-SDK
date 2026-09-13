import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from urllib.request import Request, urlopen

from showdown_sdk.exceptions import TeamGenerationError, UnsupportedFeatureError
from showdown_sdk.models.sdk.pokemon_set import TeamSet
from showdown_sdk.utils import SerializableObject, expect_object


class BaseTeamGenerator(ABC):
    """
    A Pokemon Generator generates a TeamSet based on anything it wants.
    """

    BASE_URL: str = "https://play.pokemonshowdown.com/data/sets"

    def __init__(self, seed: int | None = None) -> None:
        # format -> species -> list of sets
        # a set is pokemon species with their abilities and moves
        self._cache: dict[str, dict[str, list[SerializableObject]]] = {}

    @abstractmethod
    async def generate(
        self,
        format_name: str,
        validator: Callable[[TeamSet], Awaitable[None]],
        max_attempts: int = 100,
    ) -> TeamSet:
        pass

    async def get_sets(
        self, format_name: str
    ) -> dict[str, list[SerializableObject]]:
        """Populate the cache of set data"""
        if format_name in self._cache:
            return self._cache[format_name]

        data = await asyncio.to_thread(self._fetch, format_name)

        sets: dict[str, list[SerializableObject]] = {}

        for source_name in ("dex", "stats"):
            source = data.get(source_name)

            if not isinstance(source, dict):
                continue

            for species, raw_sets in source.items():
                if not isinstance(raw_sets, dict):
                    continue

                species_sets = sets.setdefault(species, [])

                for raw_set in raw_sets.values():
                    if isinstance(raw_set, dict):
                        species_sets.append(raw_set)

        sets = {
            species: species_sets
            for species, species_sets in sets.items()
            if species_sets
        }

        if not sets:
            raise TeamGenerationError(
                f"No sets found for format {format_name!r}"
            )

        self._cache[format_name] = sets
        return sets

    def _fetch(self, format_name: str) -> SerializableObject:
        sets_format = format_name.split("@@@", 1)[0]
        url = f"{self.BASE_URL}/{sets_format}.json"

        request = Request(url, headers={"User-Agent": "python-showdown-sdk"})

        with urlopen(request, timeout=10) as response:
            raw = response.read()

        return expect_object(json.loads(raw))

    @staticmethod
    def generation(format_name: str) -> int:
        if (
            not format_name.startswith("gen")
            or len(format_name) < 4
            or not format_name[3].isdigit()
        ):
            raise TeamGenerationError(
                f"Cannot determine generation from {format_name!r}"
            )

        generation = int(format_name[3])

        if not 1 <= generation <= 5:
            raise UnsupportedFeatureError(
                "SampleTeamGenerator currently supports generations 1 through 5"
            )

        return generation
