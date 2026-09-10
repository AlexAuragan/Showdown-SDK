from dataclasses import dataclass
from functools import cache
from typing import Literal

from showdown_sdk.models.dex import dex
from showdown_sdk.utils.serialization import (
    expect_int,
    expect_object,
    expect_string,
)


@dataclass(frozen=True)
class IdLimits:
    pokemon: int
    form: int
    move: int
    ability: int
    item: int


@cache
def pokemon_ids(gen: int) -> dict[str, tuple[int, int]]:
    """
    Build the generation-specific Pokémon ID mapping.

    Returns:
        {
            "bulbasaur": (1, 0),
            "castform": (351, 0),
            "castformsunny": (351, 1),
            ...
        }

    Form IDs are local to each base species:
        0 = base/cosmetic form
        1..N = meaningful alternate forms, in Dex order

    # ID limits by generation
    # gen | pokemon | form
    #   1 |     151 |    0
    #   2 |     251 |    0
    #   3 |     386 |    3
    #   4 |     493 |   16
    #   5 |     649 |   16
    #   6 |     721 |   17
    #   7 |     807 |   17
    #   8 |     898 |   17
    #   9 |    1025 |   17
    """
    species = dex.gen(gen).species

    entries = {
        species_id: expect_object(raw_species, name=f"species {species_id!r}")
        for species_id, raw_species in species.items()
    }

    form_ids: dict[str, int] = {}
    next_form_id: dict[str, int] = {}

    for species_id, entry in entries.items():
        forme = entry.get("forme")
        is_cosmetic = entry.get("isCosmeticForme") is True

        if not forme or is_cosmetic:
            continue

        base_species = expect_string(
            entry["baseSpecies"], name=f"species {species_id!r}.baseSpecies"
        )

        form_id = next_form_id.get(base_species, 1)
        form_ids[species_id] = form_id
        next_form_id[base_species] = form_id + 1

    output: dict[str, tuple[int, int]] = {}

    for species_id, entry in entries.items():
        species_num = expect_int(
            entry["num"], name=f"species {species_id!r}.num"
        )

        assert species_num > 0

        output[species_id] = (species_num, form_ids.get(species_id, 0))

    return output


@cache
def ordered_dex_ids(
    gen: int, table_name: Literal["moves", "abilities", "items"]
) -> dict[str, int]:
    """
    Assign 1-based IDs following the generation's Dex order.

    0 stays available to mean "no value" in the eventual vector.
    """
    table = dex.gen(gen).table(table_name)

    return {entry_id: index for index, entry_id in enumerate(table, start=1)}


@cache
def id_limits(gen: int) -> IdLimits:
    """
    # ID limits by generation
    # gen | pokemon | form | move | ability | item
    #   1 |     151 |    0 |  165 |       0 |    9
    #   2 |     251 |    0 |  251 |       0 |   62
    #   3 |     386 |    3 |  354 |      76 |  106
    #   4 |     493 |   16 |  467 |     123 |  210
    #   5 |     649 |   16 |  559 |     164 |  239
    #   6 |     721 |   17 |  618 |     191 |  284
    #   7 |     807 |   17 |  709 |     233 |  339
    #   8 |     898 |   17 |  665 |     267 |  354
    #   9 |    1025 |   17 |  685 |     310 |  249
    """
    ids = pokemon_ids(gen)

    max_pokemon = max(species_id for species_id, _ in ids.values())
    max_form = max(form_id for _, form_id in ids.values())

    generation = dex.gen(gen)

    return IdLimits(
        pokemon=max_pokemon,
        form=max_form,
        move=len(generation.moves),
        ability=len(generation.abilities),
        item=len(generation.items),
    )


def pokemon_id(name: str, gen: int) -> tuple[int, int]:
    """
    # ID limits by generation
    # gen | pokemon
    #   1 |     151
    #   2 |     251
    #   3 |     386
    #   4 |     493
    #   5 |     649
    #   6 |     721
    #   7 |     807
    #   8 |     898
    #   9 |    1025
    """
    entry = expect_object(dex.gen(gen).pokemon(name), name=f"pokemon {name!r}")
    species_id = expect_string(entry["id"], name=f"pokemon {name!r}.id")

    pokemon_id, form_id = pokemon_ids(gen)[species_id]

    limits = id_limits(gen)
    assert 1 <= pokemon_id <= limits.pokemon
    assert 0 <= form_id <= limits.form

    return pokemon_id, form_id


def move_id(name: str, gen: int) -> int:
    """
    # ID limits by generation
    # gen | move
    #   1 |  165
    #   2 |  251
    #   3 |  354
    #   4 |  467
    #   5 |  559
    #   6 |  618
    #   7 |  709
    #   8 |  665
    #   9 |  685
    """
    entry = expect_object(dex.gen(gen).move(name), name=f"move {name!r}")
    move_id = expect_string(entry["id"], name=f"move {name!r}.id")

    value = ordered_dex_ids(gen, "moves")[move_id]

    assert 1 <= value <= id_limits(gen).move
    return value


def ability_id(name: str, gen: int) -> int:
    """
    # ID limits by generation
    # gen | ability
    #   1 |       0
    #   2 |       0
    #   3 |      76
    #   4 |     123
    #   5 |     164
    #   6 |     191
    #   7 |     233
    #   8 |     267
    #   9 |     310
    """
    entry = expect_object(dex.gen(gen).ability(name), name=f"ability {name!r}")
    ability_id = expect_string(entry["id"], name=f"ability {name!r}.id")

    value = ordered_dex_ids(gen, "abilities")[ability_id]

    assert 1 <= value <= id_limits(gen).ability
    return value


def item_id(name: str, gen: int) -> int:
    """
    # ID limits by generation
    # gen | item
    #   1 |    9
    #   2 |   62
    #   3 |  106
    #   4 |  210
    #   5 |  239
    #   6 |  284
    #   7 |  339
    #   8 |  354
    #   9 |  249
    """
    entry = expect_object(dex.gen(gen).item(name), name=f"item {name!r}")
    item_id = expect_string(entry["id"], name=f"item {name!r}.id")

    value = ordered_dex_ids(gen, "items")[item_id]

    assert 1 <= value <= id_limits(gen).item
    return value


def _main() -> None:
    print("# ID limits by generation")
    print("# gen | pokemon | form | move | ability | item")

    for gen in dex.available_generations:
        limits = id_limits(gen)

        print(
            f"# {gen:>3} | "
            + f"{limits.pokemon:>7} | "
            + f"{limits.form:>4} | "
            + f"{limits.move:>4} | "
            + f"{limits.ability:>7} | "
            + f"{limits.item:>4}"
        )


if __name__ == "__main__":
    _main()
