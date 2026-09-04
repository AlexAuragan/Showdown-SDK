from typing import TYPE_CHECKING

from python_showdown import SideCondition, Status
from python_showdown.models.dex import to_id
from python_showdown.models.pokemon.pokemon import Unknown
from python_showdown.models.pokemon.status import MajorStatus, MinorStatus
from python_showdown.utils.serialization import (
    Serializable,
    SerializableObject,
    expect_array,
    expect_bool,
    expect_int,
    expect_object,
    expect_string,
)

if TYPE_CHECKING:
    from python_showdown.models.sdk.battle_state import BattleState

def normalize_move_id(move: str) -> str:
    for prefix in ("hiddenpower", "return", "frustration"):
        if move.startswith(prefix):
            return prefix

    return move

def check_battle_state_against_showdown(battle_state: BattleState) -> None:
    if battle_state.custom_showdown_battlestate is None:
        return

    ref: SerializableObject = battle_state.custom_showdown_battlestate

    def same(path: str, actual: object, expected: object) -> None:
        if actual != expected:
            raise AssertionError(
                f"{path}: sdk={actual!r}, showdown={expected!r}"
            )

    def obj(value: Serializable) -> SerializableObject:
        return expect_object(value)

    def objs(value: Serializable) -> list[SerializableObject]:
        return [obj(item) for item in expect_array(value)]

    def strings(value: Serializable) -> list[str]:
        return [expect_string(item) for item in expect_array(value)]

    def major_value(status: MajorStatus | None) -> str | None:
        return None if status is None else status.value

    def check_status(
        path: str,
        status: Status,
        ref_pokemon: SerializableObject,
    ) -> None:
        boosts = obj(ref_pokemon["boosts"])

        same(f"{path}.atk_stage", status.atk_stage, expect_int(boosts["atk"]))
        same(f"{path}.def_stage", status.def_stage, expect_int(boosts["def"]))
        same(f"{path}.spa_stage", status.spa_stage, expect_int(boosts["spa"]))
        same(f"{path}.spd_stage", status.spd_stage, expect_int(boosts["spd"]))
        same(f"{path}.spe_stage", status.spe_stage, expect_int(boosts["spe"]))
        same(
            f"{path}.acc_stage",
            status.acc_stage,
            expect_int(boosts["accuracy"]),
        )
        same(
            f"{path}.eva_stage",
            status.eva_stage,
            expect_int(boosts["evasion"]),
        )

        raw_ref_major = expect_string(ref_pokemon["status"])

        # Showdown encodes a fainted Pokémon as status="fnt".
        # The SDK tracks fainting separately with Pokemon.fainted / HP=0,
        # not as Status.major.
        ref_major = (
            None
            if raw_ref_major in {"", MajorStatus.FAINT.value}
            else raw_ref_major
        )

        same(
            f"{path}.major",
            major_value(status.major),
            ref_major,
        )

        volatiles = obj(ref_pokemon["volatiles"])

        # These SDK minor statuses map directly to a Showdown volatile with
        # to_id(status.value).
        indirect_minors = {
            MinorStatus.RECHARGE,
            MinorStatus.PERISH_SONG,
            MinorStatus.FLY,
            MinorStatus.DIVE,
            MinorStatus.TUNNEL,
            MinorStatus.FLINCH,
            MinorStatus.REPEAT,
        }

        for minor in MinorStatus:
            if minor in indirect_minors:
                continue

            volatile_id = to_id(minor.value)
            same(
                f"{path}.minor[{minor.value}]",
                minor in status.minor,
                volatile_id in volatiles,
            )

        # Recharge has existed in two SDK representations. Validate their
        # logical meaning but reject having both set simultaneously.
        recharge_minor = MinorStatus.RECHARGE in status.minor

        if recharge_minor and status.must_recharge:
            raise AssertionError(
                f"{path}: recharge exists as both MinorStatus.RECHARGE "
                + "and must_recharge=True"
            )

        same(
            f"{path}.recharge",
            recharge_minor or status.must_recharge,
            "mustrecharge" in volatiles,
        )

        # Perish Song presence is reliable.
        #
        # We intentionally do not compare perish_count against Showdown's
        # internal volatile duration. The two counters do not have identical
        # semantics across every generation.
        ref_perish = "perishsong" in volatiles

        same(
            f"{path}.perish",
            MinorStatus.PERISH_SONG in status.minor,
            ref_perish,
        )

        if ref_perish:
            if status.perish_count is None:
                raise AssertionError(
                    f"{path}: perishsong active but perish_count=None"
                )
        else:
            same(f"{path}.perish_count", status.perish_count, None)

        # Showdown uses one generic twoturnmove volatile for Fly, Dive, Dig,
        # Sky Attack, Solar Beam, etc.
        two_turn_move: str | None = None

        if "twoturnmove" in volatiles:
            two_turn = obj(volatiles["twoturnmove"])

            if "move" in two_turn:
                two_turn_move = expect_string(two_turn["move"])

        same(
            f"{path}.minor[Fly]",
            MinorStatus.FLY in status.minor,
            two_turn_move == "fly",
        )
        same(
            f"{path}.minor[Dive]",
            MinorStatus.DIVE in status.minor,
            two_turn_move == "dive",
        )
        same(
            f"{path}.minor[Tunnel]",
            MinorStatus.TUNNEL in status.minor,
            two_turn_move == "dig",
        )

    # ------------------------------------------------------------------
    # Battle-level state
    # ------------------------------------------------------------------

    player_id = battle_state.player_id

    if player_id is None:
        raise AssertionError("BattleState has no player_id")

    ref_turn = expect_int(ref["turn"])

    # Using the manager directly here is intentional: this is validation code
    # and turn synchronization itself is one of the invariants being tested.
    same("turn", battle_state._manager.turn, ref_turn) # pyright:ignore[reportPrivateUsage]

    ref_game_type = expect_string(ref["gameType"])
    same("gameType", battle_state.gametype, ref_game_type)

    if ref_game_type != "singles":
        raise NotImplementedError(
            "Showdown oracle validator currently assumes singles"
        )

    format_data = obj(ref["formatData"])
    format_id = expect_string(format_data["id"])

    if battle_state.gen is not None and not format_id.startswith(f"gen{battle_state.gen}"):
            raise AssertionError(
                f"generation: sdk=gen{battle_state.gen}, "
                + f"showdown={format_id!r}"
            )

    request_state = expect_string(ref["requestState"])

    if request_state in {"move", "switch"}:
        same(
            "force_switch",
            battle_state.force_switch,
            request_state == "switch",
        )

    field = obj(ref["field"])

    same(
        "weather",
        to_id(battle_state.weather or ""),
        expect_string(field["weather"]),
    )

    # ------------------------------------------------------------------
    # Sides
    # ------------------------------------------------------------------

    sides = objs(ref["sides"])

    side_by_id = {
        expect_string(side["id"]): side
        for side in sides
    }

    if player_id not in side_by_id:
        raise AssertionError(
            f"Showdown has no player side {player_id!r}"
        )

    foe_ids = [
        side_id
        for side_id in side_by_id
        if side_id != player_id
    ]

    if len(foe_ids) != 1:
        raise AssertionError(
            f"Expected exactly one opposing side, got {foe_ids!r}"
        )

    foe_id = foe_ids[0]

    own_side = side_by_id[player_id]
    foe_side = side_by_id[foe_id]

    def ref_side_conditions(
        side: SerializableObject,
    ) -> dict[str, int]:
        output: dict[str, int] = {}

        for condition_id, raw_state in obj(
            side["sideConditions"]
        ).items():
            state = obj(raw_state)

            output[condition_id] = (
                expect_int(state["layers"])
                if "layers" in state
                else 1
            )

        return output

    def sdk_side_conditions(
        side_id: str,
    ) -> dict[str, int]:
        output: dict[str, int] = {}

        for condition, count in battle_state.side_conditions.get(
            side_id,
            {},
        ).items():
            # Showdown stores Trick Room on the field rather than on a side.
            if condition is SideCondition.TRICK_ROOM:
                continue

            output[to_id(condition.value)] = count

        return output

    same(
        f"side_conditions[{player_id}]",
        sdk_side_conditions(player_id),
        ref_side_conditions(own_side),
    )

    same(
        f"side_conditions[{foe_id}]",
        sdk_side_conditions(foe_id),
        ref_side_conditions(foe_side),
    )

    # Trick Room has an awkward representation in the SDK, but presence can
    # still be validated.
    pseudo_weather = obj(field["pseudoWeather"])

    sdk_trick_room = any(
        SideCondition.TRICK_ROOM in conditions
        for conditions in battle_state.side_conditions.values()
    )

    same(
        "trick_room",
        sdk_trick_room,
        "trickroom" in pseudo_weather,
    )

    # ------------------------------------------------------------------
    # Own team
    # ------------------------------------------------------------------
    #
    # Our own team is completely known, so these comparisons should be exact.

    own_ref_team = objs(own_side["pokemon"])

    same(
        "team.size",
        len(battle_state.team),
        len(own_ref_team),
    )

    own_active_slot_count = len(expect_array(own_side["active"]))
    for index, pokemon in enumerate(battle_state.team):
        ref_pokemon = own_ref_team[index]
        ref_set = obj(ref_pokemon["set"])

        name = expect_string(ref_set["name"])
        path = f"team[{index}]/{name}"

        same(
            f"{path}.id",
            pokemon.id,
            f"{player_id}: {name}",
        )

        ref_request_active = (
            expect_int(ref_pokemon["position"]) < own_active_slot_count
        )

        same(
            f"{path}.active",
            pokemon.active,
            ref_request_active,
        )

        same(
            f"{path}.lvl",
            pokemon.lvl,
            expect_int(ref_set["level"]),
        )

        same(
            f"{path}.details",
            pokemon.details,
            expect_string(ref_pokemon["details"]),
        )

        same(
            f"{path}.hp",
            pokemon.curr_hp,
            expect_int(ref_pokemon["hp"]),
        )

        same(
            f"{path}.max_hp",
            pokemon.max_hp,
            expect_int(ref_pokemon["maxhp"]),
        )

        ref_stats = obj(ref_pokemon["baseStoredStats"])

        same(
            f"{path}.stats.atk",
            pokemon.stats.atk,
            expect_int(ref_stats["atk"]),
        )
        same(
            f"{path}.stats.def",
            pokemon.stats.def_,
            expect_int(ref_stats["def"]),
        )
        same(
            f"{path}.stats.spa",
            pokemon.stats.spa,
            expect_int(ref_stats["spa"]),
        )
        same(
            f"{path}.stats.spd",
            pokemon.stats.spd,
            expect_int(ref_stats["spd"]),
        )
        same(
            f"{path}.stats.spe",
            pokemon.stats.spe,
            expect_int(ref_stats["spe"]),
        )

        same(
            f"{path}.stats.max_hp",
            pokemon.stats.max_hp,
            pokemon.max_hp,
        )

        ref_moves = [
            expect_string(slot["id"])
            for slot in objs(ref_pokemon["moveSlots"])
        ]

        same(
            f"{path}.moves",
            [normalize_move_id(move) for move in pokemon.moves],
            [normalize_move_id(move) for move in ref_moves],
        )

        same(
            f"{path}.base_ability",
            to_id(pokemon.base_ability),
            to_id(expect_string(ref_pokemon["baseAbility"])),
        )

        same(
            f"{path}.item",
            to_id(pokemon.item),
            to_id(expect_string(ref_pokemon["item"])),
        )

        ref_status = expect_string(ref_pokemon["status"]) or None
        ref_fainted = expect_bool(ref_pokemon["fainted"])

        same(
            f"{path}.major_status",
            major_value(pokemon.major_status),
            "fnt" if ref_fainted else ref_status,
        )

        # The default Poké Ball is not always serialized into `set`, but a
        # non-default ball can be checked when present.
        if "pokeball" in ref_set:
            same(
                f"{path}.pokeball",
                to_id(pokemon.pokeball),
                to_id(expect_string(ref_set["pokeball"])),
            )

    same(
        "own_side.pokemonLeft",
        sum(
            pokemon.curr_hp > 0
            for pokemon in battle_state.team
        ),
        expect_int(own_side["pokemonLeft"]),
    )

    # ------------------------------------------------------------------
    # Own active Pokémon
    # ------------------------------------------------------------------

    own_active = [
        pokemon
        for pokemon in own_ref_team
        if expect_bool(pokemon["isActive"])
    ]

    if len(own_active) > 1:
        raise AssertionError(
            f"Singles battle has {len(own_active)} active own Pokémon"
        )

    if len(own_active) == 1:
        ref_active = own_active[0]
        active_set = obj(ref_active["set"])
        active_name = expect_string(active_set["name"])

        same(
            "curr_pokemon",
            battle_state.curr_pokemon,
            f"{player_id}: {active_name}",
        )

        check_status(
            "curr_pokemon_status",
            battle_state.curr_pokemon_status,
            ref_active,
        )

        ref_enemy_move_slots: dict[str, list[SerializableObject]] = {}

        for slot in objs(ref_active["moveSlots"]):
            move_id = expect_string(slot["id"])
            ref_enemy_move_slots.setdefault(move_id, []).append(slot)

        seen_move_ids: dict[str, int] = {}

        for move in battle_state.available_moves:
            # A request can contain synthetic actions with no PP:
            #
            #   Fight
            #   Recharge
            #   Struggle
            #   phase 2 of some multi-turn moves
            #
            # Those are not necessarily entries in Pokemon.moveSlots.
            if move.curr_pp is None and move.max_pp is None:
                if move.id == "recharge":
                    same(
                        "available_moves.recharge",
                        "mustrecharge" in obj(
                            ref_active["volatiles"]
                        ),
                        True,
                    )

                continue

            matching_slots = ref_enemy_move_slots.get(move.id)
            if matching_slots is None:
                raise AssertionError(
                    f"available move {move.id!r} does not exist "
                    + "in Showdown moveSlots"
                )

            occurrence = seen_move_ids.get(move.id, 0)

            if occurrence >= len(matching_slots):
                raise AssertionError(
                    f"available move {move.id!r} occurrence {occurrence} "
                    + "does not exist in Showdown moveSlots"
                )

            ref_move = matching_slots[occurrence]
            seen_move_ids[move.id] = occurrence + 1

            same(
                f"available_moves[{move.id}].pp",
                move.curr_pp,
                expect_int(ref_move["pp"]),
            )

            same(
                f"available_moves[{move.id}].max_pp",
                move.max_pp,
                expect_int(ref_move["maxpp"]),
            )

            # Doesn't seem stable because of dynamically targeted moves (like curse)
            # same(
            #    f"available_moves[{move.id}].target",
            #    move.target,
            #    expect_string(ref_move["target"]),
            #)

            ref_slot_disabled = expect_bool(ref_move["disabled"])
            ref_pp = expect_int(ref_move["pp"])

            same(
                f"available_moves[{move.id}].disabled",
                move.disabled,
                ref_slot_disabled or ref_pp <= 0,
            )

    # ------------------------------------------------------------------
    # Opponent team
    # ------------------------------------------------------------------
    #
    # This is deliberately asymmetric with our own team.
    #
    # Showdown knows the entire opponent team. The SDK must NOT. Therefore we
    # only validate fields that the SDK claims to know.

    foe_ref_team = objs(foe_side["pokemon"])

    same(
        "enemy_team.size",
        len(battle_state.enemy_team),
        len(foe_ref_team),
    )

    same(
        "foe_side.pokemonLeft",
        sum(
            not pokemon.fainted
            for pokemon in battle_state.enemy_team
        ),
        expect_int(foe_side["pokemonLeft"]),
    )

    report_percentages = expect_bool(
        ref["reportPercentages"]
    )

    def ref_gender(
        ref_set: SerializableObject,
    ) -> str | None:
        value = ref_set["gender"]

        if value is None or value is False or value == "":
            return None

        return expect_string(value)

    for index, enemy in enumerate(battle_state.enemy_team):
        path = f"enemy_team[{index}]"

        # Unknown slots should remain completely untouched.
        if enemy.id is Unknown.VALUE:
            same(f"{path}.active", enemy.active, False)
            same(f"{path}.fainted", enemy.fainted, False)
            same(f"{path}.hp_percent", enemy.curr_hp_percent, 100)

            same(
                f"{path}.base_ability",
                enemy.base_ability,
                Unknown.VALUE,
            )

            same(
                f"{path}.current_ability",
                enemy.current_ability,
                Unknown.VALUE,
            )

            same(
                f"{path}.item",
                enemy.item,
                Unknown.VALUE,
            )

            same(
                f"{path}.temporary_moves",
                enemy.temporary_moves,
                [],
            )

            same(
                f"{path}.disabled_moves",
                enemy.disabled_moves,
                [],
            )

            same(
                f"{path}.transformed_into",
                enemy.transformed_into,
                None,
            )

            same(
                f"{path}.forme",
                enemy.forme,
                None,
            )

            continue

        nickname = enemy.id.split(": ", 1)[-1]

        candidates = [
            ref_enemy
            for ref_enemy in foe_ref_team
            if to_id(
                expect_string(
                    obj(ref_enemy["set"])["name"]
                )
            )
            == to_id(nickname)
        ]

        # Duplicate nickname/species: try public switch information.
        if len(candidates) > 1:
            candidates = [
                ref_enemy
                for ref_enemy in candidates
                if (
                    expect_int(
                        obj(ref_enemy["set"])["level"]
                    )
                    == enemy.lvl
                    and (
                        expect_string(ref_enemy["gender"]) or None
                    ) == enemy.gender
                    and expect_bool(
                        obj(ref_enemy["set"])["shiny"]
                    )
                    == enemy.shiny
                )
            ]

        # If we still cannot uniquely map this public Pokémon to a hidden
        # Showdown team slot, do not invent an identity.
        if len(candidates) != 1:
            continue

        ref_enemy = candidates[0]
        ref_set = obj(ref_enemy["set"])

        name = expect_string(ref_set["name"])
        path = f"{path}/{name}"

        same(
            f"{path}.active",
            enemy.active,
            expect_bool(ref_enemy["isActive"]),
        )

        same(
            f"{path}.lvl",
            enemy.lvl,
            expect_int(ref_set["level"]),
        )

        same(
            f"{path}.gender",
            enemy.gender or "N",
            expect_string(ref_enemy["gender"]) or "N",
        )

        same(
            f"{path}.shiny",
            enemy.shiny,
            expect_bool(ref_set["shiny"]),
        )

        same(
            f"{path}.fainted",
            enemy.fainted,
            expect_bool(ref_enemy["fainted"]),
        )

        # Opponent health is reported as ceil(exact_hp * 100 / max_hp).
        if report_percentages:
            hp = expect_int(ref_enemy["hp"])
            max_hp = expect_int(ref_enemy["maxhp"])

            if hp <= 0:
                hp_percent = 0
            else:
                hp_percent = (hp * 100 + max_hp - 1) // max_hp

                # HP Percentage Mod reserves 100/100 for actual full HP.
                if hp_percent == 100 and hp < max_hp:
                    hp_percent = 99

            same(
                f"{path}.hp_percent",
                enemy.curr_hp_percent,
                hp_percent,
            )

        check_status(
            f"{path}.status",
            enemy.status,
            ref_enemy,
        )

        # Hidden values are skipped until the SDK knows them.
        if enemy.base_ability is not Unknown.VALUE:
            same(
                f"{path}.base_ability",
                to_id(enemy.base_ability),
                to_id(
                    expect_string(
                        ref_enemy["baseAbility"]
                    )
                ),
            )

        if enemy.current_ability is not Unknown.VALUE:
            same(
                f"{path}.current_ability",
                to_id(enemy.current_ability),
                to_id(
                    expect_string(
                        ref_enemy["ability"]
                    )
                ),
            )

        if enemy.item is not Unknown.VALUE:
            ref_item = expect_string(ref_enemy["item"])

            same(
                f"{path}.item",
                "" if enemy.item is None else to_id(enemy.item),
                to_id(ref_item),
            )

        # Every learned/revealed base move must actually belong to that Pokémon's
        # hidden base move set. We intentionally do NOT demand that all four
        # hidden moves are known.
        ref_base_moves = {
            normalize_move_id(to_id(move))
            for move in strings(ref_set["moves"])
        }

        for move in enemy.learnt_moves:
            if move is Unknown.VALUE:
                continue

            if normalize_move_id(to_id(move)) not in ref_base_moves:
                raise AssertionError(
                    f"{path}.learnt_moves contains impossible "
                    + f"move {move!r}"
                )

        ref_move_slots: dict[str, list[SerializableObject]] = {}

        for slot in objs(ref_enemy["moveSlots"]):
            move_id = normalize_move_id(
                expect_string(slot["id"])
            )
            ref_move_slots.setdefault(move_id, []).append(slot)

        # Mimic/Transform temporary moves should exist in the simulator's
        # current move set.
        for move in enemy.temporary_moves:
            move_id = normalize_move_id(to_id(move))
            if move_id not in ref_move_slots:
                raise AssertionError(
                    f"{path}.temporary_moves contains impossible "
                    + f"move {move_id!r}"
                )

        ref_transformed = expect_bool(
            ref_enemy["transformed"]
        )

        same(
            f"{path}.transformed",
            enemy.transformed_into is not None,
            ref_transformed,
        )

        if ref_transformed:
            # Transform copies the target's current four moves.
            same(
                f"{path}.transformed_moves",
                {
                    normalize_move_id(to_id(move))
                    for move in enemy.temporary_moves
                },
                set(ref_move_slots),
            )
        else:
            # Forme changes are separate from Transform.
            species_state = obj(
                ref_enemy["speciesState"]
            )

            ref_current_species = expect_string(
                species_state["id"]
            )

            ref_base_species = to_id(
                expect_string(
                    ref_set["species"]
                )
            )

            expected_forme = (
                None
                if ref_current_species == ref_base_species
                else ref_current_species
            )

            same(
                f"{path}.forme",
                (
                    None
                    if enemy.forme is None
                    else to_id(enemy.forme)
                ),
                expected_forme,
            )

        # Only compare a disabled move when the original slot still exists.
        # Mimic can replace the Mimic slot entirely.
        for move in enemy.disabled_moves:
            move_id = normalize_move_id(to_id(move))
            matching_slots = ref_move_slots.get(move_id)

            if matching_slots is None:
                continue

            same(
                f"{path}.disabled[{move}]",
                any(
                    expect_bool(slot["disabled"])
                    for slot in matching_slots
                ),
                True,
            )

    # ------------------------------------------------------------------
    # Active opponent
    # ------------------------------------------------------------------

    foe_active = [
        pokemon
        for pokemon in foe_ref_team
        if expect_bool(pokemon["isActive"])
    ]

    if len(foe_active) > 1:
        raise AssertionError(
            f"Singles battle has {len(foe_active)} active foe Pokémon"
        )

    if len(foe_active) == 1:
        active_set = obj(foe_active[0]["set"])

        same(
            "curr_enemy_pokemon",
            battle_state.curr_enemy_pokemon,
            f"{foe_id}a: {expect_string(active_set['name'])}",
        )

    # ------------------------------------------------------------------
    # INTENTIONALLY NOT CHECKED
    # ------------------------------------------------------------------
    #
    # - Hidden opponent fields that are still Unknown.
    #   Showdown knows them, but requiring equality would make the SDK cheat.
    #
    # - Whether a *correct* known opponent field was legitimately revealed.
    #   This oracle can tell us "the value is true", but not "the client was
    #   allowed to know it". That needs validation against reveal/history events.
    #
    # - Exact completeness of available_moves.
    #   Battle.toJSON() does not include the current player |request| object.
    #   Synthetic choices such as Fight, Recharge, Struggle and phase-2
    #   multi-turn actions are not necessarily in Pokemon.moveSlots.
    #
    # - Exact Perish Song counter.
    #   Presence is checked, and a missing SDK count is rejected.
    #
    # - Terrain, most field pseudo-weather, and slotConditions.
    #   BattleState currently has nowhere to store them.
    #   Trick Room is checked because the SDK currently puts it in
    #   SideCondition.
    #
    # - Simulator-only internals:
    #   PRNG state, queue, event/effect state, effectOrder, attackedBy,
    #   lastDamage, inputLog, current choice internals, etc.
    #
    # - statusState / abilityState / itemState source metadata.
    #   These can contain historical metadata after the authoritative
    #   top-level value has changed.
    #
    # - Exact Transform target identity.
    #   We do validate that Transform is active and that the copied moves match.
