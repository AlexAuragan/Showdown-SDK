"""Focused unit tests for `MoveEventBuilder`.

These exercise the move-event construction concern in isolation (no full log
replay), which is the testability win the builder extraction is meant to
deliver. They mirror the higher-level `test_move_history_records_*` snapshot
tests in test_parser.py but feed protocol lines directly into a
`MoveEventBuilder` driven by a minimal `FakeClient`, asserting the produced
`MoveEvent` fields.
"""
from python_showdown.classes.combat.move_builder import MoveEventBuilder
from python_showdown.classes.pokemon.pokemon import PartyPokemon, Stats, Status
from tests.conftest import FakeClient


def _client_with(enemy_id: str, enemy_hp: int, *, own_hp: int = 200) -> FakeClient:
    """A minimal client with one known enemy on the field and one own pokemon."""
    client = FakeClient()
    client.battle_player_id = "p1"
    client.room_id = client.active_battle_room = "battle-x"

    st = client.battle_state
    enemy = st.enemy_team[0]
    enemy.id = enemy_id
    enemy.active = True
    enemy.curr_hp_percent = enemy_hp

    own = PartyPokemon(
        id="p1: Arceus", details="Arceus, L69", active=True, lvl=69,
        curr_hp=own_hp, max_hp=280, stats=Stats(0, 0, 0, 0, 0, 0),
        moves=["earthquake", "recover", "judgment", "toxic"],
        base_ability="multitype", item="", pokeball="pokeball",
        status=Status(),
    )
    st.update_team([own])
    st.set_active_pokemon("p1: Arceus")
    return client


def _flush(builder: MoveEventBuilder, client: FakeClient) -> None:
    """Close the open move event by simulating a turn boundary."""
    builder.on_line(client, "|turn|99")  # type: ignore[arg-type]


def test_move_builder_records_damage_supereffective_and_critical():
    """A move reaching the enemy records the percent-point damage delta, the
    resulting HP, and the super-effective + critical flags on the MoveEvent."""
    client = _client_with("p2a: Venusaur", enemy_hp=100)
    builder = MoveEventBuilder()

    builder.on_line(client, "|move|p1a: Arceus|Earthquake|p2a: Venusaur")  # type: ignore[arg-type]
    builder.on_line(client, "|-supereffective|p2a: Venusaur")  # type: ignore[arg-type]
    builder.on_line(client, "|-crit|p2a: Venusaur")  # type: ignore[arg-type]
    builder.on_line(client, "|-damage|p2a: Venusaur|28/100")  # type: ignore[arg-type]
    _flush(builder, client)

    hist = client.battle_state.move_history
    assert len(hist) == 1
    ev = hist[0]
    assert ev.move == "Earthquake"
    assert ev.user == "p1a: Arceus"
    assert ev.target == "p2a: Venusaur"
    assert ev.user_side == "self"
    assert ev.hit is True
    assert ev.effectiveness == 2.0
    assert ev.is_critical is True
    assert ev.damage == 72          # 100 -> 28
    assert ev.resulting_hp == 28


def test_move_builder_skips_ability_driven_boost():
    """An `|-ability|...|boost` followed by `|-unboost|` must NOT append a
    StatChange to the open move event (the cross-line `_ability_boost_pending`
    suppression). The boost still mutates state via the per-line handler in the
    real parser; here we only assert it is absent from the move history."""
    client = _client_with("p2a: Foe", enemy_hp=100)
    builder = MoveEventBuilder()

    builder.on_line(client, "|move|p2a: Foe|Tackle|p1a: Arceus")  # type: ignore[arg-type]
    builder.on_line(client, "|-ability|p2a: Foe|Intimidate|boost")  # type: ignore[arg-type]
    builder.on_line(client, "|-unboost|p1a: Arceus|atk|1")  # type: ignore[arg-type]
    _flush(builder, client)

    ev = client.battle_state.move_history[0]
    assert ev.stat_changes == []     # ability-driven unboost not attributed to move


def test_move_builder_records_move_driven_setboost_inside_move():
    """`|-setboost|` inside a move cluster is recorded as a StatChange unless
    an adjacent `|-ability|...|boost` flagged it ability-driven (Belly Drum)."""
    client = _client_with("p2a: Azumarill", enemy_hp=100)
    builder = MoveEventBuilder()

    builder.on_line(client, "|move|p2a: Azumarill|Belly Drum|p2a: Azumarill")  # type: ignore[arg-type]
    builder.on_line(client, "|-setboost|p2a: Azumarill|atk|6")  # type: ignore[arg-type]
    _flush(builder, client)

    ev = client.battle_state.move_history[0]
    assert any(
        s.stat == "atk" and s.delta == 6 and s.target.startswith("p2a:")
        for s in ev.stat_changes
    )


def test_move_builder_miss_records_no_damage():
    """`|-miss|` (or `|-fail|`/`|-immune|`) sets hit=False; no damage/status."""
    client = _client_with("p2a: Foe", enemy_hp=100)
    builder = MoveEventBuilder()

    builder.on_line(client, "|move|p1a: Arceus|Toxic|p2a: Foe")  # type: ignore[arg-type]
    builder.on_line(client, "|-miss|p2a: Foe")  # type: ignore[arg-type]
    _flush(builder, client)

    ev = client.battle_state.move_history[0]
    assert ev.hit is False
    assert ev.damage is None
    assert ev.resulting_hp is None
    assert ev.statuses_inflicted == []
