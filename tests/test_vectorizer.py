# pyright: reportPrivateUsage=false
# pyright: reportPrivateLocalImportUsage=false
# Private vectorizer internals are intentionally tested because their
# dimensions and categorical ordering are part of the encoding schema.

import importlib
import os
from collections.abc import Iterator

import pytest

from showdown_sdk.models.pokemon.moves import AvailableMove
from showdown_sdk.models.pokemon.pokemon import PartyPokemon, Unknown
from showdown_sdk.models.pokemon.status import MinorStatus, Stats
from showdown_sdk.models.sdk.battle_state import BattleState
from showdown_sdk.vectorizer import vectorizer
from showdown_sdk.vectorizer.utils import item_id, pokemon_id

_FEATURE_ENV_VAR = "SHOWDOWN_SDK_VECTOR_FEATURES"


@pytest.fixture(autouse=True)
def _restore_vectorizer_environment(  # pyright:ignore[reportUnusedFunction]
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    original = os.environ.get(_FEATURE_ENV_VAR)

    yield

    if original is None:
        monkeypatch.delenv(_FEATURE_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(_FEATURE_ENV_VAR, original)

    importlib.reload(vectorizer)


def _reload_vectorizer(
    monkeypatch: pytest.MonkeyPatch, features: str | None
) -> None:
    if features is None:
        monkeypatch.delenv(_FEATURE_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(_FEATURE_ENV_VAR, features)

    importlib.reload(vectorizer)


def _party_pokemon(species: str, *, curr_hp: int = 300) -> PartyPokemon:
    match species:
        case "Pikachu":
            ability = "Static"
            item = "Light Ball"
            moves = ["thunderbolt", "quickattack", "thunderwave", "grassknot"]

        case "Venusaur":
            ability = "Overgrow"
            item = "Leftovers"
            moves = ["energyball", "sludgebomb", "sleeppowder", "synthesis"]

        case "Charizard":
            ability = "Blaze"
            item = "Leftovers"
            moves = ["flamethrower", "airslash", "roost", "dragonpulse"]

        case _:
            raise ValueError(f"Unsupported test Pokémon: {species!r}")

    return PartyPokemon(
        id=f"p1: {species}",
        lvl=100,
        details=f"{species}, L100",
        curr_hp=curr_hp,
        max_hp=300,
        stats=Stats(atk=100, def_=100, spa=100, spd=100, spe=100, max_hp=300),
        moves=moves,
        base_ability=ability,
        item=item,
        pokeball="pokeball",
    )


def _battle_state() -> BattleState:
    battle_state = BattleState()

    battle_state.player_id = "p1"
    battle_state.format.gen = 4
    battle_state.format.gametype = "singles"
    battle_state.turn = 3

    battle_state.update_team(
        [
            _party_pokemon("Pikachu"),
            _party_pokemon("Venusaur"),
            _party_pokemon("Charizard", curr_hp=0),
        ]
    )

    battle_state.set_active_pokemon("p1: Pikachu")

    battle_state.active_pokemon.ability = "Static"

    battle_state.witness_switch_in("p2a: Bob", lvl=100, species="Gyarados")

    battle_state.update_moves(
        [
            AvailableMove(
                name="Thunderbolt",
                id="thunderbolt",
                curr_pp=15,
                max_pp=24,
                target="normal",
                disabled=False,
            ),
            AvailableMove(
                name="Thunder Wave",
                id="thunderwave",
                curr_pp=20,
                max_pp=32,
                target="normal",
                disabled=True,
            ),
        ]
    )

    return battle_state


@pytest.mark.parametrize(
    ("features", "state_dimension"),
    [
        (None, 994),
        ("none", 994),
        ("types", 1222),
        ("type_matchups", 1222),
        ("base_stats", 1078),
        ("move_metadata", 1450),
        ("move_matchups", 1222),
        ("types,base_stats", 1306),
        ("types,type_matchups,base_stats,move_metadata,move_matchups", 2218),
        ("all", 2218),
    ],
)
def test_vector_dimension(
    monkeypatch: pytest.MonkeyPatch, features: str | None, state_dimension: int
) -> None:
    _reload_vectorizer(monkeypatch, features)

    battle_state = _battle_state()

    vector = vectorizer.vectorize_battle_state(battle_state)

    expected_dimension = state_dimension + vectorizer.HISTORY_DIM

    assert vectorizer._VECTOR_DIM == expected_dimension
    assert len(vector) == expected_dimension


def test_invalid_feature_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_FEATURE_ENV_VAR, "types,definitely_not_a_feature")

    with pytest.raises(
        ValueError, match="Unknown SHOWDOWN_SDK_VECTOR_FEATURES"
    ):
        importlib.reload(vectorizer)


def test_history_is_included_before_action_mask() -> None:
    battle_state = _battle_state()

    value = vectorizer.vectorize_battle_state(battle_state)

    history_start = (
        vectorizer._VECTOR_DIM
        - vectorizer.HISTORY_DIM
        - vectorizer._ACTION_MASK_DIM
    )

    history_end = vectorizer._VECTOR_DIM - vectorizer._ACTION_MASK_DIM

    assert value[history_start:history_end] == [0] * vectorizer.HISTORY_DIM

    assert value[-vectorizer._ACTION_MASK_DIM :] == (
        vectorizer._vectorize_action_mask(battle_state)
    )


def test_known_id_encoding() -> None:
    gen = 4

    assert vectorizer._vectorize_known_id(
        Unknown.VALUE, gen=gen, get_id=item_id
    ) == [0, 0]

    assert vectorizer._vectorize_known_id(None, gen=gen, get_id=item_id) == [
        1,
        0,
    ]

    leftovers = item_id("Leftovers", gen)

    assert vectorizer._vectorize_known_id(
        "Leftovers", gen=gen, get_id=item_id
    ) == [1, leftovers]


def test_canonical_pokemon_id_is_preserved() -> None:
    assert pokemon_id("Bulbasaur", 4) == (1, 0)

    assert vectorizer._vectorize_known_pokemon_id("Bulbasaur", gen=4) == [
        1,
        1,
        0,
    ]


def test_unknown_pokemon_encoding() -> None:
    assert vectorizer._vectorize_known_pokemon_id(Unknown.VALUE, gen=4) == [
        0,
        0,
        0,
    ]


def test_known_empty_pokemon_encoding() -> None:
    assert vectorizer._vectorize_known_pokemon_id(None, gen=4) == [1, 0, 0]


def test_move_aliases_are_canonicalized() -> None:
    assert vectorizer._canonical_move_name("Hidden Power Ice") == "hiddenpower"

    assert vectorizer._canonical_move_name("hiddenpowerice") == "hiddenpower"

    assert vectorizer._canonical_move_name("return102") == "return"

    assert vectorizer._canonical_move_name("frustration1") == "frustration"


def test_type_order_is_stable() -> None:
    assert vectorizer._TYPE_NAMES == (
        "Normal",
        "Fire",
        "Water",
        "Electric",
        "Grass",
        "Ice",
        "Fighting",
        "Poison",
        "Ground",
        "Flying",
        "Psychic",
        "Bug",
        "Rock",
        "Ghost",
        "Dragon",
        "Dark",
        "Steel",
        "Fairy",
    )


def test_type_vector_is_multi_hot() -> None:
    value = vectorizer._vectorize_types(("Water", "Flying"))

    assert len(value) == 19

    # Known.
    assert value[0] == 1

    # Water.
    assert value[3] == 1

    # Flying.
    assert value[10] == 1

    assert sum(value[1:]) == 2


def test_unknown_types_are_zeroed() -> None:
    value = vectorizer._vectorize_types(None)

    assert value == [0] * 19


def test_type_matchups_for_gyarados() -> None:
    value = vectorizer._vectorize_type_matchups(("Water", "Flying"), gen=4)

    assert value[0] == 1

    electric_index = 1 + vectorizer._TYPE_NAMES.index("Electric")

    ground_index = 1 + vectorizer._TYPE_NAMES.index("Ground")

    rock_index = 1 + vectorizer._TYPE_NAMES.index("Rock")

    assert value[electric_index] == 4.0
    assert value[ground_index] == 0.0
    assert value[rock_index] == 2.0


def test_base_stats_for_gyarados() -> None:
    assert vectorizer._vectorize_base_stats("Gyarados", gen=4) == [
        1,
        95,
        125,
        79,
        60,
        100,
        81,
    ]


def test_move_metadata_for_thunderbolt() -> None:
    value = vectorizer._vectorize_move_metadata("Thunderbolt", gen=4)

    assert value == [
        4,  # Electric
        2,  # Special
        95,  # base power
        0,  # not guaranteed hit
        100,  # accuracy
        0,  # priority
    ]


def test_move_matchup_for_thunderbolt_into_gyarados() -> None:
    value = vectorizer._vectorize_move_matchup(
        "Thunderbolt", target_types=("Water", "Flying"), gen=4
    )

    assert value == [1, 1, 4.0]


def test_move_matchup_with_unknown_target_types() -> None:
    value = vectorizer._vectorize_move_matchup(
        "Thunderbolt", target_types=None, gen=4
    )

    assert value == [0, 1, 0]


def test_status_move_has_no_type_chart_matchup() -> None:
    value = vectorizer._vectorize_move_matchup(
        "Thunder Wave", target_types=("Water", "Flying"), gen=4
    )

    assert value == [1, 0, 0]


def test_type_override_is_authoritative() -> None:
    value = vectorizer._current_types("Gyarados", ("Ghost",), gen=4)

    assert value == ("Ghost",)


def test_types_fall_back_to_species() -> None:
    value = vectorizer._current_types("Gyarados", None, gen=4)

    assert value == ("Water", "Flying")


def test_action_mask() -> None:
    battle_state = _battle_state()

    value = vectorizer._vectorize_action_mask(battle_state)

    assert value == [
        # Moves
        1,
        0,
        0,
        0,
        # Switches
        0,  # Pikachu: active
        1,  # Venusaur: available
        0,  # Charizard: fainted
        0,
        0,
        0,
    ]


def test_force_switch_disables_move_actions() -> None:
    battle_state = _battle_state()
    battle_state.force_switch = True

    value = vectorizer._vectorize_action_mask(battle_state)

    assert value == [0, 0, 0, 0, 0, 1, 0, 0, 0, 0]


def test_trapped_pokemon_cannot_switch() -> None:
    battle_state = _battle_state()

    battle_state.active_pokemon.status.add_minor(MinorStatus.TRAPPED)

    value = vectorizer._vectorize_action_mask(battle_state)

    assert value[:4] == [1, 0, 0, 0]

    assert value[4:] == [0, 0, 0, 0, 0, 0]


def test_enemy_slots_preserve_reveal_order() -> None:
    battle_state = _battle_state()

    battle_state.witness_switch_in("p2a: Zapdos", lvl=100, species="Zapdos")

    value = vectorizer._vectorize_enemy_team(battle_state)

    gyarados_id = pokemon_id("Gyarados", 4)

    zapdos_id = pokemon_id("Zapdos", 4)

    first_offset = 0
    second_offset = vectorizer._ENEMY_POKEMON_DIM

    assert value[first_offset : first_offset + 3] == [1, *gyarados_id]

    assert value[second_offset : second_offset + 3] == [1, *zapdos_id]


def test_unrevealed_enemy_slots_are_zero_padding() -> None:
    battle_state = _battle_state()

    value = vectorizer._vectorize_enemy_team(battle_state)

    first_padding_offset = vectorizer._ENEMY_POKEMON_DIM

    assert value[first_padding_offset:] == [0] * (
        5 * vectorizer._ENEMY_POKEMON_DIM
    )


def test_all_features_vectorize_real_battle_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reload_vectorizer(monkeypatch, "all")

    battle_state = _battle_state()

    value = vectorizer.vectorize_battle_state(battle_state)

    assert len(value) == 2218 + vectorizer.HISTORY_DIM
    assert len(value) == vectorizer._VECTOR_DIM
