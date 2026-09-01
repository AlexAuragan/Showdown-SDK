import json
import unicodedata
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Self, override

from python_showdown.utils.serialization import Serializable, expect_object

_DATASETS = frozenset(
    {
        "moves",
        "abilities",
        "items",
        "species",
        "types",
        "natures",
        "conditions",
        "learnsets",
    }
)


def to_id(value: str) -> str:
    """Normalize a human-readable Pokémon/Showdown name to a lookup ID."""
    normalized = unicodedata.normalize("NFKD", value).lower()
    return "".join(
        character
        for character in normalized
        if character.isascii() and character.isalnum()
    )


@dataclass(slots=True)
class DexTable(Mapping[str, Serializable]):
    """Lazy mapping over one generated JSON dataset."""

    path: Path
    _data: dict[str, Serializable] | None = field(default=None, init=False, repr=False)

    def _load(self) -> dict[str, Serializable]:
        if self._data is None:
            if not self.path.is_file():
                raise FileNotFoundError(
                    f"Dex data file does not exist: {self.path}. "
                    + "Run `python dex/download.py` first."
                )

            with self.path.open("r", encoding="utf-8") as file:
                data = expect_object(json.load(file), name=str(self.path))

            self._data = data

        return self._data

    @override
    def __getitem__(self, key: str) -> Serializable:
        data = self._load()

        if key in data:
            return data[key]

        normalized = to_id(key)
        if normalized in data:
            return data[normalized]

        raise KeyError(key)

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(self._load())

    @override
    def __len__(self) -> int:
        return len(self._load())

    @override
    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False

        data = self._load()
        return key in data or to_id(key) in data

    def refresh(self) -> None:
        """Forget the in-memory copy so the next access rereads the JSON file."""
        self._data = None


@dataclass(slots=True)
class GenerationDex:
    """Lazy access to all datasets for one Pokémon generation."""

    number: int
    root: Path
    _tables: dict[str, DexTable] = field(default_factory=dict, init=False, repr=False)
    _metadata: Serializable | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValueError(f"Generation must be positive, got {self.number}")

        directory = self.directory
        if not directory.is_dir():
            raise FileNotFoundError(
                f"Generation {self.number} has not been downloaded: {directory}. "
                + f"Run `python dex/download.py --gens {self.number}` first."
            )

    @property
    def directory(self) -> Path:
        return self.root / f"gen{self.number}"

    def table(self, name: str) -> DexTable:
        if name not in _DATASETS:
            valid = ", ".join(sorted(_DATASETS))
            raise KeyError(f"Unknown Dex dataset {name!r}. Expected one of: {valid}")

        table = self._tables.get(name)
        if table is None:
            table = DexTable(self.directory / f"{name}.json")
            self._tables[name] = table
        return table

    @property
    def moves(self) -> DexTable:
        return self.table("moves")

    @property
    def abilities(self) -> DexTable:
        return self.table("abilities")

    @property
    def items(self) -> DexTable:
        return self.table("items")

    @property
    def species(self) -> DexTable:
        return self.table("species")

    @property
    def types(self) -> DexTable:
        return self.table("types")

    @property
    def natures(self) -> DexTable:
        return self.table("natures")

    @property
    def conditions(self) -> DexTable:
        return self.table("conditions")

    @property
    def learnsets(self) -> DexTable:
        return self.table("learnsets")

    @property
    def metadata(self) -> Serializable:
        if self._metadata is None:
            path = self.directory / "metadata.json"
            with path.open("r", encoding="utf-8") as file:
                data = expect_object(json.load(file))
            self._metadata = data
        return self._metadata

    def move(self, name: str) -> Serializable:
        return self.moves[name]

    def ability(self, name: str) -> Serializable:
        return self.abilities[name]

    def item(self, name: str) -> Serializable:
        return self.items[name]

    def pokemon(self, name: str) -> Serializable:
        return self.species[name]

    def learnset(self, name: str) -> Serializable:
        return self.learnsets[name]

    def refresh(self) -> None:
        for table in self._tables.values():
            table.refresh()
        self._metadata = None


@dataclass(slots=True, init=False)
class Dex:
    """Singleton entry point for generation-aware Pokémon Showdown data."""

    root: Path
    _generations: dict[int, GenerationDex]
    _metadata: Serializable | None

    _instance: ClassVar[Self | None] = None
    _initialized: ClassVar[bool] = False

    def __new__(cls) -> Self:
        if cls._instance is None:
            cls._instance = object.__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        cls = type(self)
        if cls._initialized:
            return

        self.root = Path("dex_data")
        self._generations = {}
        self._metadata = None
        cls._initialized = True

    @property
    def available_generations(self) -> tuple[int, ...]:
        generations: list[int] = []
        for path in self.root.glob("gen*"):
            if not path.is_dir():
                continue

            suffix = path.name[3:]
            if suffix.isdigit():
                generations.append(int(suffix))

        return tuple(sorted(generations))

    @property
    def latest_generation(self) -> int:
        generations = self.available_generations
        if not generations:
            raise FileNotFoundError(
                f"No generated Dex data found under {self.root}. "
                + "Run `python dex/download.py` first."
            )
        return generations[-1]

    @property
    def latest(self) -> GenerationDex:
        return self.gen(self.latest_generation)

    @property
    def metadata(self) -> Serializable:
        if self._metadata is None:
            path = self.root / "metadata.json"
            if not path.is_file():
                raise FileNotFoundError(
                    f"Dex metadata does not exist: {path}. "
                    + "Run `python dex/download.py` first."
                )

            with path.open("r", encoding="utf-8") as file:
                data = expect_object(json.load(file))

            self._metadata = data

        return self._metadata

    def gen(self, number: int | None = None) -> GenerationDex:
        if number is None:
            number = self.latest_generation

        generation = self._generations.get(number)
        if generation is None:
            generation = GenerationDex(number=number, root=self.root)
            self._generations[number] = generation
        return generation

    def move(self, name: str, *, gen: int | None = None) -> Serializable:
        return self.gen(gen).move(name)

    def ability(self, name: str, *, gen: int | None = None) -> Serializable:
        return self.gen(gen).ability(name)

    def item(self, name: str, *, gen: int | None = None) -> Serializable:
        return self.gen(gen).item(name)

    def pokemon(self, name: str, *, gen: int | None = None) -> Serializable:
        return self.gen(gen).pokemon(name)

    def learnset(self, name: str, *, gen: int | None = None) -> Serializable:
        return self.gen(gen).learnset(name)

    def refresh(self) -> None:
        """Drop all cached JSON while preserving the singleton itself."""
        for generation in self._generations.values():
            generation.refresh()
        self._generations.clear()
        self._metadata = None


dex = Dex()
