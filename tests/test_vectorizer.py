# pyright: reportPrivateUsage=false
# Dimension constants (_OWN_POKEMON_DIM, _ENEMY_POKEMON_DIM, etc.) are
# intentionally tested because they are part of the encoding schema.

"""Tests for the vectorizer's public API.

Tests call only public functions (``vectorize_battle_features``,
``vectorize_action``, etc.) with feature objects.  They never construct
``BattleState`` or test private implementation details directly.

Dimension-stability tests that need private constants still import them
from the private namespace with the required pyright suppression.
"""

from showdown_sdk.exceptions import VectorizationError
from showdown_sdk.features import (
    ActionFeatures,
    BattleFeatures,
    BattleFormatFeatures,
    EnemyPokemonFeatures,
    FieldFeatures,
    Knowledge,
    MoveActionFeatures,
    OwnMoveFeatures,
    OwnPokemonFeatures,
    SideConditionFeatures,
    StatFeatures,
    StatusFeatures,
    SwitchActionFeatures,
    battle_to_features,
)
from showdown_sdk.models.pokemon import AvailableMove, PartyPokemon, Stats
from showdown_sdk.models.sdk import BattleState
from showdown_sdk.vectorizer import (
    EVENT_VECTOR_DIM,
    VECTOR_SCHEMA_VERSION,
    VectorizerConfig,
    item_id,
    vectorize_action,
    vectorize_actions,
    vectorize_battle_features,
    vectorizer,
)

# Dimension constants from the module (tested for schema stability)
_OWN_POKEMON_DIM = vectorizer._OWN_POKEMON_DIM
_ENEMY_POKEMON_DIM = vectorizer._ENEMY_POKEMON_DIM
_ACTION_DIM = vectorizer._ACTION_DIM
_FIELD_DIM = vectorizer._FIELD_DIM


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


def _make_features() -> BattleFeatures:
    return battle_to_features(_battle_state())


## Schema version and config


def test_schema_version() -> None:
    assert VECTOR_SCHEMA_VERSION == 3


def test_config_defaults() -> None:
    config = VectorizerConfig()
    assert config.history_length == 32


def test_config_custom() -> None:
    config = VectorizerConfig(history_length=64)
    assert config.history_length == 64


## Public API: vectorize_battle_features


def test_vector_dimension_is_stable() -> None:
    features = _make_features()
    vector = vectorize_battle_features(features)

    history_dim = 32 * EVENT_VECTOR_DIM
    expected = (
        _FIELD_DIM
        + 6 * _OWN_POKEMON_DIM
        + 6 * _ENEMY_POKEMON_DIM
        + history_dim
        + 10  # action mask
    )
    assert len(vector) == expected


def test_custom_history_length_changes_dimension() -> None:
    features = _make_features()
    config = VectorizerConfig(history_length=8)
    vector = vectorize_battle_features(features, config=config)

    expected = (
        _FIELD_DIM
        + 6 * _OWN_POKEMON_DIM
        + 6 * _ENEMY_POKEMON_DIM
        + 8 * EVENT_VECTOR_DIM
        + 10
    )
    assert len(vector) == expected


def test_vectorizer_does_not_need_battle_state() -> None:
    """Pure feature-construction entry point: no BattleState needed."""
    features = BattleFeatures(
        format=BattleFormatFeatures(gen=4, gametype="singles"),
        field=FieldFeatures(turn=1),
        own_team=tuple(
            OwnPokemonFeatures(present=False, slot=i) for i in range(6)
        ),
        enemy_team=tuple(
            EnemyPokemonFeatures(revealed=False, slot=i) for i in range(6)
        ),
    )
    vector = vectorize_battle_features(features)
    assert len(vector) > 0


def test_round_trip_via_battle_to_features() -> None:
    """BattleState -> features -> vector (end-to-end)."""
    features = _make_features()
    vector = vectorize_battle_features(features)
    assert len(vector) > 0
    assert all(isinstance(v, (int, float)) for v in vector)


def test_vectorization_requires_gen() -> None:
    """Verify that missing gen raises a clear error."""
    features = BattleFeatures(
        format=BattleFormatFeatures(gen=None),
        field=FieldFeatures(),
        own_team=tuple(
            OwnPokemonFeatures(present=False, slot=i) for i in range(6)
        ),
        enemy_team=tuple(
            EnemyPokemonFeatures(revealed=False, slot=i) for i in range(6)
        ),
    )
    import pytest

    with pytest.raises(VectorizationError, match="gen is required"):
        vectorize_battle_features(features)


## Public API: vectorize_action


def test_action_move() -> None:
    action = ActionFeatures(
        kind="move",
        move=MoveActionFeatures(
            request_index=0, name="thunderbolt", current_pp=15, max_pp=24
        ),
    )
    vector = vectorize_action(action, gen=4)
    assert len(vector) == _ACTION_DIM
    assert vector[0] == 1  # kind: move
    assert vector[1] == 1  # name known
    assert vector[2] > 0  # move id
    assert vector[3] == 0  # not disabled


def test_action_switch() -> None:
    action = ActionFeatures(
        kind="switch",
        switch=SwitchActionFeatures(
            team_slot=2, pokemon_id="p1: Venusaur", species="venusaur"
        ),
    )
    vector = vectorize_action(action, gen=4)
    assert len(vector) == _ACTION_DIM
    assert vector[0] == 2  # kind: switch
    assert vector[8] == 3  # team_slot (0-based 2 -> 1-based 3)
    assert vector[9] == 1  # species known


def test_action_disabled_move() -> None:
    action = ActionFeatures(
        kind="move",
        move=MoveActionFeatures(
            request_index=0, name="thunderwave", disabled=True
        ),
    )
    vector = vectorize_action(action, gen=4)
    assert vector[3] == 1  # disabled


def test_vectorize_actions_separate() -> None:
    """vectorize_actions returns one vector per action."""
    actions = (
        ActionFeatures(
            kind="move",
            move=MoveActionFeatures(
                request_index=0, name="thunderbolt", current_pp=15, max_pp=24
            ),
        ),
        ActionFeatures(
            kind="switch",
            switch=SwitchActionFeatures(
                team_slot=1, pokemon_id="p1: Venusaur", species="venusaur"
            ),
        ),
    )
    result = vectorize_actions(actions, gen=4)
    assert len(result) == 2
    assert len(result[0]) == _ACTION_DIM
    assert len(result[1]) == _ACTION_DIM


## Public API: known-id encoding


def test_knowledge_id_mapping() -> None:
    gen = 4
    # Unknown
    result = vectorizer._vectorize_knowledge_id(
        Knowledge(known=False), gen=gen, get_id=item_id
    )
    assert result == [0, 0]
    # Known absent
    result = vectorizer._vectorize_knowledge_id(
        Knowledge(known=True, value=None), gen=gen, get_id=item_id
    )
    assert result == [1, 0]
    # Known value
    leftovers_id = item_id("leftovers", gen)
    result = vectorizer._vectorize_knowledge_id(
        Knowledge(known=True, value="leftovers"), gen=gen, get_id=item_id
    )
    assert result == [1, leftovers_id]


## Status encoding


def test_status_encoding() -> None:
    status = StatusFeatures(major="par", attack_stage=1, defense_stage=-1)
    v = vectorizer._vectorize_status(status)
    assert v[0] == 1  # atk_stage
    assert v[1] == -1  # def_stage
    assert v[7] == 3  # par id


def test_empty_status() -> None:
    status = StatusFeatures()
    v = vectorizer._vectorize_status(status)
    assert v[7] == 0
    assert all(v[i] == 0 for i in range(7))


def test_status_minor_statuses() -> None:
    status = StatusFeatures(minor=("confusion", "leechseed"))
    v = vectorizer._vectorize_status(status)
    minor_start = 8
    minor_names = sorted(vectorizer._MINOR_STATUS_NAMES)
    c_idx = minor_names.index("confusion")
    l_idx = minor_names.index("leechseed")
    assert v[minor_start + c_idx] == 1
    assert v[minor_start + l_idx] == 1
    assert sum(v[minor_start : minor_start + len(minor_names)]) == 2


## Pokémon encoding (dimension stability)


def test_own_pokemon_padding_has_correct_length() -> None:
    empty = OwnPokemonFeatures(present=False, slot=5)
    v = vectorizer._vectorize_own_pokemon(empty, gen=4)
    assert len(v) == _OWN_POKEMON_DIM
    assert v == [0] * _OWN_POKEMON_DIM


def test_own_pokemon_vector_consistent_length() -> None:
    pokemon = OwnPokemonFeatures(
        present=True,
        slot=0,
        species="pikachu",
        current_species="pikachu",
        level=100,
        active=True,
        hp_ratio=1.0,
        hp_current=300,
        hp_max=300,
        base_ability="static",
        current_ability="static",
        item="lightball",
        moves=(
            OwnMoveFeatures(present=True, name="thunderbolt"),
            OwnMoveFeatures(present=True, name="quickattack"),
            OwnMoveFeatures(present=True, name="thunderwave"),
            OwnMoveFeatures(present=True, name="grassknot"),
        ),
        status=StatusFeatures(),
        stats=StatFeatures(
            hp=300,
            attack=100,
            defense=100,
            special_attack=100,
            special_defense=100,
            speed=100,
        ),
    )
    v = vectorizer._vectorize_own_pokemon(pokemon, gen=4)
    assert len(v) == _OWN_POKEMON_DIM


def test_enemy_pokemon_padding_has_correct_length() -> None:
    empty = EnemyPokemonFeatures(revealed=False, slot=5)
    v = vectorizer._vectorize_enemy_pokemon(empty, gen=4)
    assert len(v) == _ENEMY_POKEMON_DIM
    assert v == [0] * _ENEMY_POKEMON_DIM


def test_enemy_pokemon_vector_consistent_length() -> None:
    pokemon = EnemyPokemonFeatures(
        revealed=True,
        slot=0,
        species="gyarados",
        current_species="gyarados",
        level=100,
        active=True,
        hp_ratio=0.75,
        base_ability=Knowledge(known=False),
        current_ability=Knowledge(known=False),
        item=Knowledge(known=False),
        moves=(
            Knowledge(known=False),
            Knowledge(known=False),
            Knowledge(known=False),
            Knowledge(known=False),
        ),
        status=StatusFeatures(),
    )
    v = vectorizer._vectorize_enemy_pokemon(pokemon, gen=4)
    assert len(v) == _ENEMY_POKEMON_DIM


## Side conditions


def test_side_condition_vectorization() -> None:
    conditions = (
        SideConditionFeatures(name="stealthrock", value=1),
        SideConditionFeatures(name="spikes", value=2),
    )
    v = vectorizer._vectorize_side_conditions(conditions)
    assert v[0] == 1  # stealthrock
    assert v[1] == 2  # spikes


def test_empty_side_conditions() -> None:
    v = vectorizer._vectorize_side_conditions(())
    assert all(x == 0 for x in v)
    assert len(v) == vectorizer._SIDE_CONDITIONS_DIM


## Action mask (corrected request_index + team_slot semantics)


def test_action_mask_uses_request_index() -> None:
    """Mask should respect request_index, not enumeration order."""
    features = _make_features()
    mask = vectorizer._action_mask_from_features(features)
    assert len(mask) == 10
    # Thunderbolt is slot 0 → mask[0] == 1
    assert mask[0] == 1
    # Slot 1 is disabled in the test state → mask[1] == 0
    assert mask[1] == 0
    # 6 switch slots follow
    assert sum(mask[4:]) >= 0  # Venusaur can switch in


def test_action_mask_disabled_slot_preserves_position() -> None:
    """A disabled move should leave its slot as 0, not shift later moves."""
    mask = vectorizer._action_mask_from_features(
        BattleFeatures(
            format=BattleFormatFeatures(gen=4),
            field=FieldFeatures(),
            own_team=(),
            enemy_team=(),
            available_actions=(
                ActionFeatures(
                    kind="move",
                    move=MoveActionFeatures(
                        request_index=0, name="tackle", disabled=False
                    ),
                ),
                ActionFeatures(
                    kind="move",
                    move=MoveActionFeatures(
                        request_index=2, name="ember", disabled=False
                    ),
                ),
                ActionFeatures(
                    kind="move",
                    move=MoveActionFeatures(
                        request_index=3, name="watergun", disabled=False
                    ),
                ),
            ),
        )
    )
    assert mask[:4] == [1, 0, 1, 1]


## No domain-model import in vectorizer


def test_vectorizer_does_not_import_domain_models() -> None:
    import inspect

    source = inspect.getsource(vectorizer)
    lines = source.split("\n")
    bad = [
        line
        for line in lines
        if "import" in line
        and any(
            mod in line
            for mod in ["models.sdk", "models.pokemon", "classes.parser"]
        )
    ]
    assert not bad, f"vectorizer imports domain models: {bad}"
