import json

import pytest

from showdown_sdk.classes.parser import EffectSource, PokemonIdent
from showdown_sdk.features import (
    FEATURE_SCHEMA_VERSION,
    Knowledge,
    battle_action_mask,
    battle_to_features,
    features_to_dict,
)
from showdown_sdk.models.pokemon import (
    AvailableMove,
    PartyPokemon,
    SideCondition,
    Stats,
)
from showdown_sdk.models.sdk import BattleState, SourceType


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
            item = ""
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


@pytest.fixture
def battle_state() -> BattleState:
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

    battle_state.witness_switch_in("p2a: Bob", lvl=88, species="Gyarados")

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


## Structural + JSON


def test_team_slots_are_always_six(battle_state: BattleState):
    features = battle_to_features(battle_state)

    assert len(features.own_team) == 6
    assert len(features.enemy_team) == 6

    assert features.own_team[0].present is True
    assert features.own_team[5].present is False

    assert features.enemy_team[0].revealed is True
    assert features.enemy_team[1].revealed is False


def test_json_serializable(battle_state: BattleState):
    features = battle_to_features(battle_state)

    encoded = json.dumps(features_to_dict(features))

    assert isinstance(encoded, str)


def test_schema_version(battle_state: BattleState):
    features = battle_to_features(battle_state)

    assert features.schema_version == FEATURE_SCHEMA_VERSION == 1


## Pokémon semantics


def test_active_own_pokemon_current_state(battle_state: BattleState):
    features = battle_to_features(battle_state)
    active = features.own_team[0]

    assert active.species == "pikachu"
    assert active.current_species == "pikachu"
    assert active.active is True
    assert features.own_active_slot == 0

    assert active.hp_ratio == pytest.approx(1.0)
    assert active.hp_current == 300
    assert active.hp_max == 300

    assert active.base_ability == "static"
    assert active.current_ability == "static"
    assert active.item == "lightball"

    assert active.moves[0].name == "thunderbolt"
    assert active.stats is not None and active.stats.speed == 100


def test_fainted_own_pokemon(battle_state: BattleState):
    features = battle_to_features(battle_state)

    assert features.own_team[2].fainted is True
    assert features.own_team[2].hp_ratio == 0.0


def test_empty_own_slot_has_no_known_values(battle_state: BattleState):
    empty = battle_to_features(battle_state).own_team[5]

    assert empty.present is False
    assert empty.species is None
    assert empty.hp_ratio is None
    assert all(not move.present for move in empty.moves)


## Unknown vs known-none vs known-value


def test_enemy_knowledge_tri_state(battle_state: BattleState):
    enemy = battle_state.enemy_team[-1]

    features = battle_to_features(battle_state)
    gyarados = features.enemy_team[0]

    assert gyarados.base_ability == Knowledge(known=False, value=None)
    assert gyarados.item == Knowledge(known=False, value=None)
    assert gyarados.level == 88
    assert gyarados.species == "gyarados"

    # Known absence: revealed to have no item.
    enemy.item = None
    gyarados = battle_to_features(battle_state).enemy_team[0]
    assert gyarados.item == Knowledge(known=True, value=None)

    # Known value.
    enemy.item = "Leftovers"
    gyarados = battle_to_features(battle_state).enemy_team[0]
    assert gyarados.item == Knowledge(known=True, value="leftovers")


def test_enemy_unknown_move_slots(battle_state: BattleState):
    moves = battle_to_features(battle_state).enemy_team[0].moves

    assert len(moves) == 4
    assert all(not move.known for move in moves)

    # Witness a move; unknown slots remain explicit.
    battle_state.witness_move("Waterfall")

    moves = battle_to_features(battle_state).enemy_team[0].moves
    # witness_move fills the last free slot.
    assert moves == (
        Knowledge(known=False, value=None),
        Knowledge(known=False, value=None),
        Knowledge(known=False, value=None),
        Knowledge(known=True, value="waterfall"),
    )


def test_unrevealed_enemy_slot(battle_state: BattleState):
    unrevealed = battle_to_features(battle_state).enemy_team[3]

    assert unrevealed.revealed is False
    assert unrevealed.species is None
    assert unrevealed.level is None
    assert unrevealed.hp_ratio is None
    assert unrevealed.item == Knowledge(known=False, value=None)
    assert all(not move.known for move in unrevealed.moves)


## Actions


def test_available_actions_match_legality(battle_state: BattleState):
    features = battle_to_features(battle_state)

    thunderbolt = next(
        action.move
        for action in features.available_actions
        if action.move and action.move.name == "thunderbolt"
    )
    assert thunderbolt.disabled is False
    assert thunderbolt.current_pp == 15

    # Disabled moves are excluded; available_actions only contains
    # actions the client can *legitimately* send.
    thunder_wave = next(
        (
            action.move
            for action in features.available_actions
            if action.move and action.move.name == "thunderwave"
        ),
        None,
    )
    assert thunder_wave is None

    # Own switch options: Venusaur only; Charizard is fainted and the
    # active Pikachu cannot switch into itself.
    switches = [
        action.switch
        for action in features.available_actions
        if action.kind == "switch" and action.switch is not None
    ]
    slots = {entry.team_slot for entry in switches}
    assert slots == {1}

    assert {action.kind for action in features.available_actions} <= {
        "move",
        "switch",
    }


def test_action_mask_compatibility(battle_state: BattleState):
    mask = battle_action_mask(battle_state)

    assert len(mask) == 10
    assert mask[0] is True  # Thunderbolt
    assert mask[1] is False  # Thunder Wave disabled
    assert mask[4] is False  # own slot 0: active Pikachu
    assert mask[5] is True  # Venusaur, healthy and benched
    assert mask[6] is False  # fainted Charizard


def test_force_switch_has_no_move_actions(battle_state: BattleState):
    battle_state.force_switch = True
    battle_state.update_moves([])

    features = battle_to_features(battle_state)

    assert all(action.kind == "switch" for action in features.available_actions)
    assert features.force_switch is True


## Field / format


def test_field_features(battle_state: BattleState):
    battle_state.weather = "RainDance"
    battle_state.side_conditions["p1"] = {
        SideCondition.STEALTH_ROCK: 2,
        SideCondition.SPIKES: 1,
    }

    features = battle_to_features(battle_state)

    assert features.field.weather == "raindance"
    assert features.field.turn == 3
    assert features.format.gen == 4
    assert features.format.gametype == "singles"

    conditions = {
        entry.name: entry.value for entry in features.field.own_side_conditions
    }
    assert conditions == {"stealthrock": 2, "spikes": 1}


def test_clear_weather_is_none(battle_state: BattleState):
    features = battle_to_features(battle_state)

    assert features.field.weather is None


## History


def test_history_is_ordered_sequence(battle_state: BattleState):
    from showdown_sdk.classes.parser.events.battle import (
        DamageEvent,
        MoveEvent,
        TurnEvent,
    )

    battle_state.history = [
        TurnEvent(turn=1),
        MoveEvent(
            action_id=0,
            move="Thunderbolt",
            source_pokemon=PokemonIdent(player="p1", slot="a", name="Pikachu"),
            target_pokemon=PokemonIdent(player="p2", slot="a", name="Bob"),
            success=True,
            does_hit=True,
        ),
        TurnEvent(turn=2),
        DamageEvent(
            source=EffectSource(type=SourceType.MOVE),
            target=PokemonIdent(player="p2", slot="a", name="Bob"),
            curr_hp=100,
            max_hp=300,
            hp_is_percentage=False,
            effectiveness=0.5,
        ),
    ]

    features = battle_to_features(battle_state)
    history = features.history

    assert len(history) == 2  # TurnEvents timestamp, not occupy slots

    move, damage = history

    assert move.event_type == "move"
    assert move.turn == 1
    assert move.move == "thunderbolt"
    assert move.source is not None
    assert move.source.side == "self"
    assert move.source.species == "pikachu"
    assert move.target is not None
    assert move.target.side == "opponent"

    assert damage.event_type == "damage"
    assert damage.turn == 2
    assert damage.hp_ratio == pytest.approx(100 / 300)
    assert damage.effectiveness == 0.5
