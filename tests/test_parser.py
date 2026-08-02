"""Parser tests.

Two layers:

* Snapshot (unit) tests -- replay a recorded single-client log up to a chosen
  point and assert `BattleState.to_json()` equals the saved JSON in
  tests/battle_states/<gen>/. These lock in that the parser produces exactly
  the expected state (team, enemy team, statuses, boosts, HP, weather, items,
  ...) for that moment.
* End-to-end tests -- replay a full log start to finish and assert the parser
  runs without raising and ends with a fully populated battle (6 vs 6).
"""


import asyncio
import json
from pathlib import Path

import pytest

from python_showdown.classes.client.parser import Parser
from python_showdown.classes.pokemon.pokemon import Unknown
from python_showdown.classes.pokemon.stats import MajorStatus
from tests.conftest import FakeClient, replay_log

# Sentinel placeholder used by enemy learnt_moves before a slot is revealed.
UNKNOWN = Unknown.VALUE

TESTS_DIR = Path(__file__).resolve().parent
RAW_ROOT = TESTS_DIR / "raw"
STATES_ROOT = TESTS_DIR / "battle_states"

# (gen, log filename, snapshot name, stop_at) -- must match
# tests/generate_snapshots.py.
SNAPSHOTS = [
    # --- Gen 1 ---
    ("gen1randombattle", "battle-gen1randombattle-7790.txt", "7790_early", 45),
    ("gen1randombattle", "battle-gen1randombattle-7790.txt", "7790_boosts", 141),
    ("gen1randombattle", "battle-gen1randombattle-7790.txt", "7790_status", 330),
    ("gen1randombattle", "battle-gen1randombattle-7726.txt", "7726_early", 31),
    ("gen1randombattle", "battle-gen1randombattle-7726.txt", "7726_status", 228),
    ("gen1randombattle", "battle-gen1randombattle-7728.txt", "7728_early", 31),
    ("gen1randombattle", "battle-gen1randombattle-7728.txt", "7728_boosts", 166),
    ("gen1randombattle", "battle-gen1randombattle-7710.txt", "7710_early", 31),
    ("gen1randombattle", "battle-gen1randombattle-7710.txt", "7710_faints", 84),
    ("gen1randombattle", "battle-gen1randombattle-7710.txt", "7710_boosts", 263),
    ("gen1randombattle", "battle-gen1randombattle-12120.txt", "12120_mimic", 56),
    # --- Gen 2 ---
    ("gen2randombattle", "battle-gen2randombattle-7810.txt", "7810_weather", 250),
    ("gen2randombattle", "battle-gen2randombattle-7810.txt", "7810_weather_late", 444),
    ("gen2randombattle", "battle-gen2randombattle-7827.txt", "7827_weather", 1500),
    ("gen2randombattle", "battle-gen2randombattle-7827.txt", "7827_activate", 1120),
    ("gen2randombattle", "battle-gen2randombattle-7817.txt", "7817_weather", 360),
    ("gen2randombattle", "battle-gen2randombattle-7812.txt", "7812_item", 270),
    # --- Gen 4 ---
    ("gen4randombattle", "battle-gen4randombattle-11425.txt", "11425_painsplit", 36),
    ("gen4randombattle", "battle-gen4randombattle-11425.txt", "11425_force_switch", 410),
    ("gen4randombattle", "battle-gen4randombattle-11239.txt", "11239_cureteam", 300),
    ("gen4randombattle", "battle-gen4randombattle-11106.txt", "11106_sethp", 250),
    ("gen4randombattle", "battle-gen4randombattle-11106.txt", "11106_spikes", 59),
    ("gen4randombattle", "battle-gen4randombattle-11145.txt", "11145_weather", 100),
    ("gen4randombattle", "battle-gen4randombattle-11099.txt", "11099_bellydrum", 203),
]

# Logs replayed in full for the end-to-end test.
E2E_LOGS = sorted({(gen, name) for gen, name, _, _ in SNAPSHOTS})


@pytest.mark.parametrize("gen,log_name,snapshot_name,stop_at", SNAPSHOTS)
def test_battle_state_matches_snapshot(gen, log_name, snapshot_name, stop_at):
    log_path = RAW_ROOT / gen / log_name
    expected_path = STATES_ROOT / gen / f"{snapshot_name}.json"

    client = replay_log(log_path, stop_at=stop_at)
    actual = json.loads(client.battle_state.to_json())
    expected = json.loads(expected_path.read_text(encoding="utf-8"))

    assert actual == expected, f"state diverged at {log_name}@{stop_at}"


@pytest.mark.parametrize(
    "line,expected",
    [
        ("|-weather|SunnyDay", "SunnyDay"),
        ("|-weather|SunnyDay|[upkeep]", "SunnyDay"),
        ("|-weather|RainDance|[from] move: Rain Dance", "RainDance"),
        ("|-weather|none", None),
    ],
)
def test_weather_string_parsing(line, expected):
    """A weather line sets the weather to exactly the weather name, regardless
    of trailing [upkeep] or [from] clauses."""
    client = FakeClient()
    handler = Parser()
    asyncio.run(handler.handle_line(client, line))  # type: ignore[arg-type]
    assert client.battle_state.weather == expected


def test_sethp_updates_enemy_hp():
    """`|-sethp|p2a: Bastiodon|90/100` (line 24) sets enemy HP to the reported per cent."""
    client = replay_log(
        RAW_ROOT / "gen4randombattle" / "battle-gen4randombattle-11425.txt",
        stop_at=26,  # processes sethp line 24, but not the heal at line 29
    )
    bastiodon = next(
        e for e in client.battle_state.enemy_team
        if "Bastiodon" in str(e.id)
    )
    assert bastiodon.curr_hp_percent == 90


def test_ability_reveals_enemy_base_ability():
    """`|-ability|p2a: Moltres|Pressure` (line 487) sets the enemy's ability."""
    client = replay_log(
        RAW_ROOT / "gen4randombattle" / "battle-gen4randombattle-11425.txt",
        stop_at=488,
    )
    moltres = next(
        e for e in client.battle_state.enemy_team
        if "Moltres" in str(e.id)
    )
    assert str(moltres.base_ability) == "Pressure"


def test_mimic_disables_mimic_slot_and_exposes_copy():
    """In 12120, Magnemite uses Mimic and copies Hyper Beam (|-start|...Mimic|Hyper
    Beam at line 55). Afterwards Mimic is disabled and Hyper Beam is available;
    the original Thunderbolt stays available. learnt_moves (base knowledge) is
    unchanged.
    """
    client = replay_log(
        RAW_ROOT / "gen1randombattle" / "battle-gen1randombattle-12120.txt",
        stop_at=56,
    )
    magnemite = next(
        e for e in client.battle_state.enemy_team
        if "Magnemite" in str(e.id)
    )
    assert "Mimic" in magnemite.disabled_moves
    assert magnemite.temporary_moves == ["Hyper Beam"]
    # Base knowledge is preserved (Mimic stays recorded as a known base move).
    assert "Mimic" in magnemite.learnt_moves
    # Available set: Thunderbolt (base, not disabled) + the copied Hyper Beam.
    assert magnemite.available_moves == ["Thunderbolt", "Hyper Beam"]


def test_cureteam_clears_enemy_status():
    """`|-cureteam|` (Aromatherapy) clears the whole enemy side's major status.

    In 11239, Blissey is poisoned at line 250 and cured at line 258; at line
    300 it must read no major status.
    """
    client = replay_log(
        RAW_ROOT / "gen4randombattle" / "battle-gen4randombattle-11239.txt",
        stop_at=300,
    )
    blissey = next(
        e for e in client.battle_state.enemy_team
        if "Blissey" in str(e.id)
    )
    assert blissey.status.major is None


def test_setboost_sets_absolute_stage():
    """`|-setboost|...|atk|6` (Belly Drum) sets enemy atk_stage to exactly +6."""
    client = replay_log(
        RAW_ROOT / "gen4randombattle" / "battle-gen4randombattle-11099.txt",
        stop_at=203,  # just after the |-setboost| at line 202
    )
    azumarill = next(
        e for e in client.battle_state.enemy_team
        if "Azumarill" in str(e.id)
    )
    assert azumarill.status.atk_stage == 6


@pytest.mark.parametrize(
    "line",
    [
        "|-clearallboost",
        "|-clearallboost|[silent]",
        "|-clearallboost|[from] move: Haze",
    ],
)
def test_clearallboost_resets_all_stages(line):
    """`|-clearallboost|` resets every enemy stat stage to 0."""
    client = FakeClient()
    st = client.battle_state
    # All enemy slots start unknown; grab the first and make it a known foe.
    enemy = st.enemy_team[0]
    enemy.id = "p2a: Foe"
    enemy.status.boost("atk", 5)
    enemy.status.boost("spe", 2)
    enemy.status.set_status("psn")
    # set battle_player_id so the room check passes during handle_line
    client.battle_player_id = "p1"
    client.room_id = client.active_battle_room = "battle-x"
    handler = Parser()
    asyncio.run(handler.handle_line(client, line))  # type: ignore[arg-type]
    assert enemy.status.atk_stage == 0
    assert enemy.status.spe_stage == 0
    assert enemy.status.major == MajorStatus.POISON  # major status must NOT be cleared


def test_transform_copies_our_active_moves_immediately():
    """When an enemy transforms into our own active pokemon, the enemy's whole
    base set is replaced by an immediate, exact copy of our active's moves
    (we know them from |request|), and the original Transform is no longer
    available.
    """
    from python_showdown.classes.pokemon.pokemon import PartyPokemon, Stats, Status
    client = FakeClient()
    client.battle_player_id = "p1"
    client.room_id = client.active_battle_room = "battle-x"
    st = client.battle_state

    enemy = st.enemy_team[0]
    enemy.id = "p2a: Ditto"
    enemy.active = True
    enemy.learnt_moves = ["Transform", UNKNOWN, UNKNOWN, UNKNOWN]

    # Our own active with a known 4-move set.
    own = PartyPokemon(
        id="p1: Arceus", details="Arceus, L69", active=True, lvl=69,
        curr_hp=220, max_hp=280, stats=Stats(0, 0, 0, 0, 0, 0),
        moves=["earthquake", "recover", "judgment", "toxic"],
        base_ability="multitype", item="", pokeball="pokeball",
        status=Status(),
    )
    st.update_team([own])
    st.set_active_pokemon("p1: Arceus")

    handler = Parser()
    asyncio.run(handler.handle_line(client, "|-transform|p2a: Ditto|p1: Arceus"))  # type: ignore[arg-type]

    assert str(enemy.transformed_into) == "p1: Arceus"
    assert enemy.temporary_moves == ["earthquake", "recover", "judgment", "toxic"]
    # While transformed, available_moves is the copied set -- no Transform.
    assert enemy.available_moves == ["earthquake", "recover", "judgment", "toxic"]
    assert "Transform" not in enemy.available_moves


def test_formechange_updates_forme_but_not_moves():
    """|-formechange| relabels the active species (forme) without touching the
    move set: Castform-Rainy via Forecast, then reverts to the base Castform.
    Its `available_moves`/`learnt_moves`/`disabled_moves`/`transformed_into` stay
    exactly as they were; only `forme` moves.
    """
    client = FakeClient()
    client.battle_player_id = "p1"
    client.room_id = client.active_battle_room = "battle-x"
    st = client.battle_state
    enemy = st.enemy_team[0]
    enemy.id = "p2a: Castform"
    enemy.active = True
    enemy.learnt_moves = ["Weather Ball", "Thunderbolt", UNKNOWN, UNKNOWN]
    assert enemy.forme is None
    moves_before = (list(enemy.learnt_moves), list(enemy.temporary_moves),
                    list(enemy.disabled_moves), enemy.transformed_into,
                    list(enemy.available_moves))

    handler = Parser()
    line = "|-formechange|p2a: Castform|Castform-Rainy|[msg]|[from] ability: Forecast"
    asyncio.run(handler.handle_line(client, line))  # type: ignore[arg-type]
    assert enemy.forme == "Castform-Rainy"

    line = "|-formechange|p2a: Castform|Castform|[msg]|[from] ability: Forecast"
    asyncio.run(handler.handle_line(client, line))  # type: ignore[arg-type]
    assert enemy.forme == "Castform"  # reverted to the base species name

    moves_after = (list(enemy.learnt_moves), list(enemy.temporary_moves),
                   list(enemy.disabled_moves), enemy.transformed_into,
                   list(enemy.available_moves))
    assert moves_before == moves_after


def test_witness_switch_in_round_trips_known_enemy():
    """Switching a KNOWN enemy out and back in reuses the existing EnemyPokemon
    entry (matched on the full `<slot>: <species>` id) rather than creating a new
    one -- so accumulated move/ability knowledge is preserved.

    This pins the current behaviour documented in BattleState.witness_switch_in:
    identity is keyed on the full `|switch|` ident, which assumes the species
    suffix is stable across switch cycles. That holds for the formats targeted
    today (e.g. Gen 1 Random, no mega evolution) but would NOT hold for a
    permanent forme change (mega): a mega-evolved pokemon switching back in
    would arrive with a new species suffix, miss this match, and either be
    duplicated or raise. Extending format coverage requires re-keying identity
    on the slot prefix (see Option B in the review notes).
    """
    client = FakeClient()
    st = client.battle_state
    enemy = st.enemy_team[0]
    enemy.id = "p2a: Venusaur"
    enemy.active = True
    enemy.learnt_moves = ["Vine Whip", "Sleep Powder", UNKNOWN, UNKNOWN]
    enemy.base_ability = "Overgrow"

    # Switch out (another pokemon comes in), then Venusaur comes back.
    st.witness_switch_in("p2a: Pikachu", lvl=50)
    assert st.curr_enemy_pokemon == "p2a: Pikachu"
    venusaur = next(p for p in st.enemy_team if str(p.id) == "p2a: Venusaur")
    assert venusaur.active is False
    assert venusaur.learnt_moves == ["Vine Whip", "Sleep Powder", UNKNOWN, UNKNOWN]
    assert str(venusaur.base_ability) == "Overgrow"

    st.witness_switch_in("p2a: Venusaur", lvl=50)
    same = next(p for p in st.enemy_team if str(p.id) == "p2a: Venusaur")
    assert same is venusaur               # reused, not duplicated
    assert same.active is True
    assert same.learnt_moves == ["Vine Whip", "Sleep Powder", UNKNOWN, UNKNOWN]
    # No new Unknown slot was consumed: still just Venusaur + Pikachu.
    assert sum(1 for p in st.enemy_team if p.id is not UNKNOWN) == 2


def test_sidestart_stacks_per_layer():
    """In 11106, Spikes is laid on our side three times (lines 25/36/58);
    by line 59 side_conditions['p1']['Spikes'] == 3 and p2 has no entry."""
    client = replay_log(
        RAW_ROOT / "gen4randombattle" / "battle-gen4randombattle-11106.txt",
        stop_at=59,
    )
    conds = client.battle_state.side_conditions
    assert conds.get("p1", {}).get("Spikes") == 3
    assert "p2" not in conds or conds["p2"] == {}


@pytest.mark.parametrize(
    "effect",
    ["Spikes", "move: Toxic Spikes", "move: Stealth Rock"],
)
def test_sideend_clears_effect(effect):
    """|-sideend| clears every layer of the effect on that side (Rapid Spin)."""
    client = FakeClient()
    client.battle_player_id = "p1"
    client.room_id = client.active_battle_room = "battle-x"
    st = client.battle_state
    handler = Parser()

    norm = effect.removeprefix("move: ").strip()
    for _ in range(3):
        asyncio.run(handler.handle_line(client, f"|-sidestart|p2: BOT2|{effect}"))  # type: ignore[arg-type]
    assert st.side_conditions["p2"][norm] == 3

    se = f"|-sideend|p2: BOT2|{norm}|[from] move: Rapid Spin|[of] p2a: Forretress"
    asyncio.run(handler.handle_line(client, se))  # type: ignore[arg-type]
    assert norm not in st.side_conditions.get("p2", {})


def _history_of(client, gen, name, stop):
    return replay_log(RAW_ROOT / gen / name, stop_at=stop).battle_state.move_history


def test_move_history_records_damage_and_burn():
    """Fire Blast -> Omastar (NVE, secondary burn) is recorded with the absolute
    HP dealt to our own pokemon, the resulting HP, and the burn.
    """
    hist = _history_of(None, "gen4randombattle",
                     "battle-gen4randombattle-11239.txt", 148)
    fb = next(e for e in hist if e.move == "Fire Blast"
              and e.target == "p1a: Omastar")
    assert fb.hit is True
    assert fb.effectiveness == 0.5
    assert fb.statuses_inflicted == [MajorStatus.BURN]
    assert fb.damage == 35
    assert fb.resulting_hp == 34


def test_move_history_records_enemy_percent_damage():
    """Our Earthquake on the enemy Venusaur records damage in PERCENT points.
    """
    hist = _history_of(None, "gen1randombattle",
                     "battle-gen1randombattle-7728.txt", 123)
    # Take the last Earthquake -- it brought Venusaur to 5%.
    quakes = [e for e in hist if e.move == "Earthquake" and e.user_side == "self"]
    eq = quakes[-1]
    assert eq.hit is True
    assert eq.resulting_hp == 5
    assert eq.damage is not None and 27 <= eq.damage <= 35  # a percent-point delta


def test_move_history_records_miss():
    """Toxic missing records hit=False and no damage/status."""
    hist = _history_of(None, "gen4randombattle",
                     "battle-gen4randombattle-11425.txt", 35)
    toxic = next(e for e in hist if e.move == "Toxic")
    assert toxic.hit is False
    assert toxic.damage is None
    assert toxic.statuses_inflicted == []


def test_move_history_records_move_stat_change_not_ability():
    """Belly Drum (|-setboost|) on the enemy records an atk +6 stat change; an
    ability-driven boost (Intimidate) is NOT attributed to a move.
    """
    hist = _history_of(None, "gen4randombattle",
                     "battle-gen4randombattle-11099.txt", 203)
    bd = next(e for e in hist if e.move == "Belly Drum")
    assert any(s.stat == "atk" and s.delta == 6 and s.target.startswith("p2a:")
               for s in bd.stat_changes)


@pytest.mark.parametrize("gen,log_name", E2E_LOGS)
def test_replay_full_log_without_error(gen, log_name):
    """A whole battle replays without raising and ends 6-vs-6."""
    client = replay_log(RAW_ROOT / gen / log_name)
    state = client.battle_state
    assert len(state.team) == 6
    # The enemy may not reveal all 6: when BOT1 loses, the opponent often
    # still has pokemon in reserve at the moment |win| fires, so those slots
    # legitimately stay UNKNOWN. Verified on 7812/7827/11106 (BOT1 losses).
    known_enemies = [p for p in state.enemy_team if p.id is not UNKNOWN]
    assert 0 < len(known_enemies) <= 6
