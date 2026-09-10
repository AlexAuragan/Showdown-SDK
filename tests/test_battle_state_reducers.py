# pyright: reportPrivateUsage=false
# Acceptable in a test suite

"""State/reducer tests for Transform, type overrides, and request trapping.

These tests apply events directly to a :class:`BattleState` and assert the
state before/after. They exercise the reducers in isolation; the vectorizer
is deliberately not involved here (it only consumes already-correct state).
"""

from showdown_sdk.classes.parser.events.battle import (
    DecisionRequestEvent,
    PokemonSwitchEvent,
    TransformEvent,
    TypeChangeEvent,
)
from showdown_sdk.classes.parser.fields import parse_pokemon_ident
from showdown_sdk.classes.parser.models import (
    EffectSource,
    RequestMove,
    RequestPokemon,
)
from showdown_sdk.classes.parser.reducers.battle import (
    _reduce_decision_request,
    _reduce_transform,
    reduce_battle_state,
)
from showdown_sdk.models.pokemon.pokemon import PartyPokemon
from showdown_sdk.models.pokemon.status import Stats
from showdown_sdk.models.sdk.battle_state import BattleState, SourceType


def make_battle_state() -> BattleState:
    battle_state = BattleState()
    battle_state.player_id = "p1"
    battle_state.format.gen = 4
    return battle_state


def make_party_pokemon(ident: str, species: str) -> PartyPokemon:
    return PartyPokemon(
        id=ident,
        lvl=50,
        details=f"{species}, L50",
        curr_hp=100,
        max_hp=100,
        stats=Stats(atk=10, def_=10, spa=10, spd=10, spe=10, max_hp=100),
        moves=["Transform"],
        base_ability="Limber",
        item="",
        pokeball="pokeball",
    )


def switch_in_enemy(
    battle_state: BattleState, ident_str: str, species: str
) -> None:
    reduce_battle_state(
        battle_state,
        PokemonSwitchEvent(
            pokemon=parse_pokemon_ident(ident_str),
            details=f"{species}, L70",
            level=70,
            curr_hp=100,
            max_hp=None,
            hp_is_percentage=True,
            major_status=None,
        ),
    )


def own_switch(battle_state: BattleState, ident_str: str, species: str) -> None:
    reduce_battle_state(
        battle_state,
        PokemonSwitchEvent(
            pokemon=parse_pokemon_ident(ident_str),
            details=f"{species}, L50",
            level=50,
            curr_hp=100,
            max_hp=None,
            hp_is_percentage=True,
            major_status=None,
        ),
    )


def transform_source() -> EffectSource:
    return EffectSource(type=SourceType.MOVE, name="Transform")


def type_change_source() -> EffectSource:
    return EffectSource(type=SourceType.ABILITY, name="Color Change")


def test_type_change_on_own_active_sets_type_override() -> None:
    battle_state = make_battle_state()
    battle_state.update_team([make_party_pokemon("p1: Kecleon", "Kecleon")])
    battle_state.set_active_pokemon("p1: Kecleon")

    event = TypeChangeEvent(
        source=type_change_source(),
        target=parse_pokemon_ident("p1a: Kecleon"),
        types=("Water",),
    )

    assert battle_state.active_pokemon.type_override is None
    reduce_battle_state(battle_state, event)

    assert battle_state.active_pokemon.type_override == event.types


def test_type_change_on_enemy_sets_enemy_type_override() -> None:
    battle_state = make_battle_state()
    battle_state.update_team([make_party_pokemon("p1: Ditto", "Ditto")])
    battle_state.set_active_pokemon("p1: Ditto")
    switch_in_enemy(battle_state, "p2a: Bob", "Mewtwo")

    enemy = battle_state.get_enemy_pokemon(battle_state.curr_enemy_pokemon)
    assert enemy is not None
    assert enemy.type_override is None

    event = TypeChangeEvent(
        source=type_change_source(),
        target=parse_pokemon_ident("p2a: Bob"),
        types=("Water", "Flying"),
    )

    reduce_battle_state(battle_state, event)

    assert enemy.type_override == ("Water", "Flying")


def test_own_transform_stores_target_species() -> None:
    battle_state = make_battle_state()
    battle_state.update_team([make_party_pokemon("p1: Ditto", "Ditto")])
    battle_state.set_active_pokemon("p1: Ditto")
    switch_in_enemy(battle_state, "p2a: Bob", "Gengar")

    enemy = battle_state.get_enemy_pokemon(battle_state.curr_enemy_pokemon)
    assert enemy is not None
    enemy.type_override = ("Dark", "Poison")

    event = TransformEvent(
        source=transform_source(),
        pokemon=parse_pokemon_ident("p1a: Ditto"),
        target=parse_pokemon_ident("p2a: Bob"),
    )

    _reduce_transform(battle_state, event)

    # The stored value is the target's effective species, not the protocol
    # ident (whose name may be a nickname).
    assert battle_state.active_pokemon.transformed_into == "Gengar"

    assert battle_state.active_pokemon.type_override == ("Dark", "Poison")


def test_enemy_transform_stores_own_species() -> None:
    battle_state = make_battle_state()
    battle_state.update_team(
        [
            make_party_pokemon("p1: Bob", "Bob"),
            make_party_pokemon("p1: Mewtwo", "Mewtwo"),
        ]
    )
    battle_state.set_active_pokemon("p1: Bob")
    switch_in_enemy(battle_state, "p2a: Ditto", "Ditto")

    enemy = battle_state.get_enemy_pokemon(battle_state.curr_enemy_pokemon)
    assert enemy is not None
    assert enemy.transformed_into is None
    assert enemy.type_override is None

    event = TransformEvent(
        source=transform_source(),
        pokemon=parse_pokemon_ident("p2a: Ditto"),
        target=parse_pokemon_ident("p1a: Bob"),
    )

    _reduce_transform(battle_state, event)

    assert enemy.transformed_into == "Bob"
    # type_override must not be copied from the transformed-into Pokémon.
    assert enemy.type_override is None


def test_own_switch_clears_transform_and_type_override() -> None:
    battle_state = make_battle_state()
    battle_state.update_team(
        [
            make_party_pokemon("p1: Ditto", "Ditto"),
            make_party_pokemon("p1: Dragonite", "Dragonite"),
        ]
    )
    battle_state.set_active_pokemon("p1: Ditto")

    battle_state.active_pokemon.transformed_into = "Gengar"
    battle_state.active_pokemon.type_override = ("Water", "Flying")
    battle_state.active_pokemon.trapped = True
    battle_state.active_pokemon.maybe_trapped = True

    own_switch(battle_state, "p1b: Dragonite", "Dragonite")

    assert battle_state.curr_pokemon == "p1: Dragonite"
    assert battle_state.active_pokemon.transformed_into is None
    assert battle_state.active_pokemon.type_override is None
    assert battle_state.active_pokemon.trapped is False
    assert battle_state.active_pokemon.maybe_trapped is False


def test_enemy_switch_clears_transform_and_type_override() -> None:
    battle_state = make_battle_state()
    battle_state.update_team([make_party_pokemon("p1: Ditto", "Ditto")])
    battle_state.set_active_pokemon("p1: Ditto")
    switch_in_enemy(battle_state, "p2a: Bob", "Mewtwo")

    enemy = battle_state.get_enemy_pokemon(battle_state.curr_enemy_pokemon)
    assert enemy is not None
    enemy.transformed_into = "Gengar"
    enemy.type_override = ("Water", "Flying")

    # Switching a second enemy in must reset the outgoing transformed enemy.
    switch_in_enemy(battle_state, "p2b: Ditto", "Snorlax")

    assert enemy.transformed_into is None
    assert enemy.type_override is None


def test_active_pokemon_state_to_dict_exposes_fields() -> None:
    battle_state = make_battle_state()
    battle_state.update_team([make_party_pokemon("p1: Ditto", "Ditto")])
    battle_state.set_active_pokemon("p1: Ditto")

    battle_state.active_pokemon.transformed_into = "Gengar"
    battle_state.active_pokemon.type_override = ("Water", "Flying")
    battle_state.active_pokemon.trapped = True
    battle_state.active_pokemon.maybe_trapped = False

    data = battle_state.active_pokemon.to_dict()

    assert data["transformed_into"] == "Gengar"
    assert data["type_override"] == ("Water", "Flying")
    assert data["trapped"] is True
    assert data["maybe_trapped"] is False


def make_decision_request_event(
    *, trapped: bool, maybe_trapped: bool
) -> DecisionRequestEvent:
    move = RequestMove(
        name="Tackle",
        id="tackle",
        curr_pp=10,
        max_pp=10,
        target="normal",
        disabled=False,
        disabled_source=None,
    )

    pokemon = RequestPokemon(
        ident="p1: Ditto",
        details="Ditto, L50",
        level=50,
        active=True,
        atk=10,
        def_=10,
        spa=10,
        spd=10,
        spe=10,
        moves=("transform",),
        base_ability="limber",
        item="",
        pokeball="pokeball",
        curr_hp=100,
        max_hp=100,
        major_status=None,
    )

    return DecisionRequestEvent(
        player_id="p1",
        request_id=1,
        wait=False,
        trapped=trapped,
        maybe_trapped=maybe_trapped,
        maybe_locked=False,
        maybe_disabled=False,
        update=False,
        force_switch=(False,),
        moves=(move,),
        pokemon=(pokemon,),
        no_cancel=False,
    )


def test_decision_request_persists_trapping_state() -> None:
    battle_state = make_battle_state()
    battle_state.update_team([make_party_pokemon("p1: Ditto", "Ditto")])
    battle_state.set_active_pokemon("p1: Ditto")

    event = make_decision_request_event(trapped=True, maybe_trapped=True)
    _reduce_decision_request(battle_state, event)

    assert battle_state.active_pokemon.trapped is True
    assert battle_state.active_pokemon.maybe_trapped is True

    # A following request overwrites the previous request-level truth.
    event = make_decision_request_event(trapped=False, maybe_trapped=False)
    _reduce_decision_request(battle_state, event)

    assert battle_state.active_pokemon.trapped is False
    assert battle_state.active_pokemon.maybe_trapped is False
