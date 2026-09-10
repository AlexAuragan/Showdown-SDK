"""Battle event reducers.

This module owns the deterministic consequences of semantic battle events:
``reduce_battle_state`` applies SDK knowledge/state changes. Live
battle-manager lifecycle handling lives in
``showdown_sdk.classes.combat_handler.battle_events``.

The parser event classes themselves remain immutable descriptions of what the
Showdown protocol reported.
"""

from showdown_sdk.classes.parser.events.base import BaseEvent
from showdown_sdk.classes.parser.events.battle import (
    AbilityEvent,
    BattleEndEvent,
    BattleEvent,
    BattleStartEvent,
    CantEvent,
    ClearAllBoostsEvent,
    ClearBoostsEvent,
    ClearNegativeBostsEvent,
    CopyBoostEvent,
    CustomShowdownBattleStateEvent,
    DamageEvent,
    DecisionRequestEvent,
    DesyncEvent,
    DetailsChangeEvent,
    FormeChangeEvent,
    GameGenEvent,
    GameTierEvent,
    GameTypeEvent,
    HealEvent,
    ItemEvent,
    MajorStatusEvent,
    MinorStatusActivationEvent,
    MinorStatusEvent,
    MoveActivationEvent,
    MoveCopiedEvent,
    MoveEvent,
    MovePrepareEvent,
    PartialTrapEvent,
    PerishCountEvent,
    PlayerEvent,
    PokemonSwitchEvent,
    RoomEvent,
    SetHpEvent,
    SideConditionEvent,
    SingleMoveEvent,
    StatChangeEvent,
    StatSetEvent,
    TeamCureEvent,
    TeamPreviewRequestEvent,
    TransformEvent,
    TurnEvent,
    TypeChangeEvent,
    UpkeepEvent,
    WeatherEvent,
)
from showdown_sdk.classes.parser.fields import parse_pokemon_details
from showdown_sdk.classes.parser.reducers.mechanics import (
    auto_reveal_source,
    clear_semi_invulnerable_status,
    clear_traps_sourced_by_side,
    copy_baton_pass_status,
    get_semi_invulnerable_status,
    ident_raw,
    ident_self_key,
    is_self,
    resolve_any_status,
    resolve_enemy,
    resolve_self,
    reveal_effect_source,
    sync_own_two_turn_status_from_request,
    sync_sticky_barb_from_damage,
)
from showdown_sdk.models.dex import dex, to_id
from showdown_sdk.models.pokemon.moves import AvailableMove
from showdown_sdk.models.pokemon.pokemon import (
    EnemyPokemon,
    PartyPokemon,
    Unknown,
)
from showdown_sdk.models.pokemon.status import (
    MajorStatus,
    MinorStatus,
    Stats,
    Status,
)
from showdown_sdk.models.sdk.battle_state import BattleState, SourceType


def _reduce_move_prepare(
    battle_state: BattleState, event: MovePrepareEvent
) -> None:
    minor = get_semi_invulnerable_status(event.move)
    if minor is None:
        return

    duration = dex.condition_duration("twoturnmove", gen=battle_state.gen)

    status = resolve_any_status(battle_state, event.pokemon)
    status.add_minor(minor, duration=duration)


def _reduce_move(battle_state: BattleState, event: MoveEvent) -> None:
    source_status = resolve_any_status(battle_state, event.source_pokemon)
    source_status.clear_single_move()

    if battle_state.gen_1_desync:
        battle_state.gen_1_desync = False
        return

    gen = battle_state.gen

    if event.source is not None:
        if (
            event.source.type == SourceType.MOVE
            and event.source.name == "Mirror Move"
        ):
            return

        if (
            event.source.type == SourceType.ABILITY
            and event.source.name == "Magic Bounce"
        ):
            return

        if event.source.name == event.move and dex.is_charge_move(
            event.move, gen=gen
        ):
            return

    enemy = resolve_enemy(battle_state, event.source_pokemon)
    if enemy is not None:
        enemy.witness_move(event.move)

    # Gen 1 does not emit an explicit `|-activate|...|move: Wrap`
    # when partial trapping starts, so it has to be inferred.
    if (
        gen == 1
        and event.success
        and event.does_hit
        and event.target_pokemon is not None
    ):
        volatile_status = dex.move_volatile_status(event.move, gen=gen)
        if volatile_status == MinorStatus.PARTIALLY_TRAPPED.value:
            duration = dex.condition_duration(
                MinorStatus.PARTIALLY_TRAPPED.value, gen=gen
            )

            target_status = resolve_any_status(
                battle_state, event.target_pokemon
            )

            target_status.add_minor(
                MinorStatus.PARTIALLY_TRAPPED, duration=duration
            )


def _reduce_damage(battle_state: BattleState, event: DamageEvent) -> None:
    sync_sticky_barb_from_damage(battle_state, event)
    enemy = resolve_enemy(battle_state, event.target)
    if enemy is not None:
        if (
            event.source.type == SourceType.ITEM
            and event.source.name is not None
            and event.source.owner is None
        ):
            enemy.item = event.source.name

        enemy.curr_hp_percent = event.curr_hp
        if event.curr_hp == 0:
            enemy.fainted = True
        return

    own = resolve_self(battle_state, event.target)
    if own is not None:
        own.curr_hp = event.curr_hp


def _reduce_heal(battle_state: BattleState, event: HealEvent) -> None:
    cures_status = (
        event.source.type == SourceType.MOVE
        and event.source.name in {"Healing Wish", "Lunar Dance"}
    )

    enemy = resolve_enemy(battle_state, event.target)
    if enemy is not None:
        enemy.curr_hp_percent = event.curr_hp
        if cures_status:
            enemy.status.clear_all_major_status()
        if event.curr_hp == 0:
            enemy.fainted = True
        return

    own = resolve_self(battle_state, event.target)
    if own is not None:
        own.curr_hp = event.curr_hp
        if cures_status:
            own.major_status = None
            battle_state.active_pokemon.status.clear_all_major_status()


def _reduce_minor_status(
    battle_state: BattleState, event: MinorStatusEvent
) -> None:
    status = resolve_any_status(battle_state, event.target)

    if not event.started:
        status.remove_minor(event.effect)
        return

    duration: int | None = None

    if event.effect is MinorStatus.RECHARGE:
        condition_duration = dex.condition_duration(
            "mustrecharge", gen=battle_state.gen
        )

        if condition_duration is not None and condition_duration > 0:
            duration = condition_duration

    status.add_minor(event.effect, duration=duration)


def _reduce_major_status(
    battle_state: BattleState, event: MajorStatusEvent
) -> None:
    if event.status is MajorStatus.FAINT:
        clear_traps_sourced_by_side(battle_state, event.target.player)
        enemy = resolve_enemy(battle_state, event.target)
        if enemy is not None:
            enemy.reset_on_switch_in()
            enemy.status.clear_all_major_status()
            enemy.fainted = True
            enemy.active = False
            enemy.curr_hp_percent = 0
            return

        own = resolve_self(battle_state, event.target)
        if own is not None:
            own.curr_hp = 0
            own.major_status = None
        return

    status = resolve_any_status(battle_state, event.target)
    if event.applied:
        status.set_status(event.status)
        if battle_state.gen == 1 and event.status is MajorStatus.SLEEP:
            status.remove_minor(MinorStatus.RECHARGE)
    else:
        status.clear_status(event.status)


def _reduce_move_copied(
    battle_state: BattleState, event: MoveCopiedEvent
) -> None:
    own = resolve_self(battle_state, event.target)
    if own is not None:
        mimic_slots = [
            index
            for index, move in enumerate(own.moves)
            if to_id(move) == "mimic"
        ]
        if len(mimic_slots) != 1:
            raise RuntimeError(
                "Own Pokémon used Mimic but expected exactly one Mimic slot: "
                + f"{own.moves}"
            )
        own.moves[mimic_slots[0]] = event.copied_move
        return

    enemy = resolve_enemy(battle_state, event.target)
    if enemy is None:
        return

    if enemy.transformed_into is not None:
        for index, move in enumerate(enemy.temporary_moves):
            if to_id(move) == "mimic":
                enemy.temporary_moves[index] = event.copied_move
                return
        raise RuntimeError(
            "Transformed Pokémon used Mimic but Mimic is not present in "
            + f"temporary_moves: {enemy.temporary_moves}"
        )

    if to_id(event.copied_move) == "mimic":
        return

    if event.copied_move not in enemy.temporary_moves:
        enemy.temporary_moves.append(event.copied_move)
    if "Mimic" not in enemy.disabled_moves:
        enemy.disabled_moves.append("Mimic")


def _reduce_minor_status_activation(
    battle_state: BattleState, event: MinorStatusActivationEvent
) -> None:

    status = resolve_any_status(battle_state, event.target)
    if event.effect is MinorStatus.CONFUSION:
        status.clear_single_move()
        return

    if event.effect is not MinorStatus.TRAPPED:
        return

    actor = event.source.actor
    if actor is None:
        raise RuntimeError("TRAPPED activation has no source actor")

    status.set_trapped(actor.player)


def _reduce_stat_change(
    battle_state: BattleState, event: StatChangeEvent
) -> None:
    if not event.success:
        return

    status = resolve_any_status(battle_state, event.target)
    for stat, delta in event.stat_changes:
        status.boost(stat, delta)


def _reduce_details_change(
    battle_state: BattleState, event: DetailsChangeEvent
) -> None:
    own = resolve_self(battle_state, event.pokemon)
    if own is not None:
        own.details = event.details
        own.lvl = event.level
        return

    enemy = resolve_enemy(battle_state, event.pokemon)
    if enemy is None:
        return

    enemy.lvl = event.level
    enemy.forme = event.details.split(",", 1)[0].strip()
    enemy.species = enemy.forme


def _reduce_team_cure(battle_state: BattleState, event: TeamCureEvent) -> None:
    if is_self(battle_state, event.actor):
        for pokemon in battle_state.team:
            pokemon.major_status = None
        battle_state.active_pokemon.status.major = None
        return

    for pokemon in battle_state.enemy_team:
        pokemon.status.clear_all_major_status()


def _reduce_clear_boosts(
    battle_state: BattleState, event: ClearBoostsEvent
) -> None:
    status = resolve_any_status(battle_state, event.target)
    status.reset_all_stages()


def _reduce_clear_all_boosts(battle_state: BattleState) -> None:
    battle_state.active_pokemon.status.reset_all_stages()
    for pokemon in battle_state.enemy_team:
        if pokemon.active:
            pokemon.status.reset_all_stages()


def _reduce_copy_boost(
    battle_state: BattleState, event: CopyBoostEvent
) -> None:
    user_status = resolve_any_status(battle_state, event.user)
    target_status = resolve_any_status(battle_state, event.target)
    user_status.copy_stat_changes(target_status)


def _reduce_clear_negative_boosts(
    battle_state: BattleState, event: ClearNegativeBostsEvent
) -> None:
    status = resolve_any_status(battle_state, event.target)
    status.clear_negative_stages()


def _reduce_set_hp(battle_state: BattleState, event: SetHpEvent) -> None:
    enemy = resolve_enemy(battle_state, event.target)
    if enemy is not None:
        enemy.curr_hp_percent = event.curr_hp
        if event.curr_hp == 0:
            enemy.fainted = True
        return

    own = resolve_self(battle_state, event.target)
    if own is not None:
        own.curr_hp = event.curr_hp


def _reduce_side_condition(
    battle_state: BattleState, event: SideConditionEvent
) -> None:
    side_conds = battle_state.side_conditions
    if event.side:
        if event.started:
            slot = side_conds.setdefault(event.side, {})
            slot[event.condition] = slot.get(event.condition, 0) + 1
        else:
            slot = side_conds.get(event.side)
            if slot is not None:
                slot.pop(event.condition, None)
        return

    field_conditions = side_conds.setdefault("field", {})
    if event.started:
        field_conditions[event.condition] = (
            field_conditions.get(event.condition, 0) + 1
        )
    else:
        field_conditions.pop(event.condition, None)


def _species_from_details(details: str) -> str | None:
    if not details.strip():
        return None
    species = details.split(",", 1)[0].strip()
    return species or None


def _transform_target_species(target: EnemyPokemon) -> str:
    """Return the target's current effective species/form."""
    if target.transformed_into is not None:
        return target.transformed_into

    if target.forme is not None:
        return target.forme

    if target.species is not None:
        return target.species

    raise RuntimeError(f"Transform target species is unknown for {target.id!r}")


def _own_species(own: PartyPokemon | None) -> str | None:
    if own is None:
        return None
    return _species_from_details(own.details)


def _reduce_switch(
    battle_state: BattleState, event: PokemonSwitchEvent
) -> None:
    gen = battle_state.gen

    # |replace| is Illusion ending, not a real switch.
    if event.command == "replace":
        if is_self(battle_state, event.pokemon):
            return

        details = parse_pokemon_details(event.details)

        species = _species_from_details(event.details)

        if species is None:
            raise RuntimeError(
                "Could not determine species from replace details: "
                + repr(event.details)
            )

        battle_state.witness_replace(
            ident_raw(event.pokemon),
            species=species,
            lvl=event.level,
            gender=details.gender,
            shiny=details.shiny,
        )

        enemy = battle_state.get_enemy_pokemon(
            battle_state.curr_enemy_pokemon, not_found_ok=True
        )

        if enemy is None:
            raise RuntimeError("Replaced enemy disappeared from battle state")

        enemy.status.major = event.major_status

        if event.hp_is_percentage:
            enemy.curr_hp_percent = event.curr_hp

        return

    if not event.baton_pass:
        clear_traps_sourced_by_side(battle_state, event.pokemon.player)

    if is_self(battle_state, event.pokemon):
        old_status = battle_state.active_pokemon.status

        new_status = Status(major=event.major_status)

        if event.baton_pass:
            copy_baton_pass_status(new_status, old_status, gen)

        battle_state.active_pokemon.clear()
        battle_state.set_active_pokemon(ident_self_key(event.pokemon))

        own = resolve_self(battle_state, event.pokemon)

        if own is not None:
            battle_state.active_pokemon.ability = own.base_ability

        else:
            battle_state.active_pokemon.ability = Unknown.VALUE

        if not battle_state.team:
            return

        battle_state.active_pokemon.status = new_status
        return

    passed_status: Status | None = None

    if event.baton_pass:
        outgoing = battle_state.get_enemy_pokemon(
            battle_state.curr_enemy_pokemon, not_found_ok=True
        )

        if outgoing is not None:
            passed_status = Status()

            copy_baton_pass_status(passed_status, outgoing.status, gen)

    details = parse_pokemon_details(event.details)

    species = _species_from_details(event.details)

    if species is None:
        raise RuntimeError(
            "Could not determine species from switch details: "
            + repr(event.details)
        )

    battle_state.witness_switch_in(
        ident_raw(event.pokemon),
        event.level,
        species=species,
        gender=details.gender,
        shiny=details.shiny,
    )

    enemy = battle_state.get_enemy_pokemon(
        battle_state.curr_enemy_pokemon, not_found_ok=True
    )

    if enemy is None:
        return

    enemy.reset_on_switch_in()
    enemy.status.major = event.major_status

    if event.hp_is_percentage:
        enemy.curr_hp_percent = event.curr_hp

    if passed_status is not None:
        copy_baton_pass_status(enemy.status, passed_status, gen)


def _reduce_transform(battle_state: BattleState, event: TransformEvent) -> None:
    source_status = resolve_any_status(battle_state, event.pokemon)

    target_status = resolve_any_status(battle_state, event.target)

    source_status.copy_stat_changes(target_status)

    if is_self(battle_state, event.pokemon):
        target = resolve_enemy(battle_state, event.target)

        if target is None:
            raise RuntimeError(
                f"Transform target {event.target} " + "not found in enemy team"
            )

        battle_state.active_pokemon.transformed_into = (
            _transform_target_species(target)
        )

        battle_state.active_pokemon.type_override = target.type_override

        if battle_state.gen > 2:
            battle_state.active_pokemon.ability = target.current_ability

        return

    enemy = resolve_enemy(battle_state, event.pokemon)

    if enemy is None:
        return

    own = resolve_self(battle_state, event.target)

    if own is None:
        raise RuntimeError(
            f"Transform target {event.target} " + "not found in own team"
        )

    copied_moves = list(own.moves)

    target_species = _own_species(own)

    if target_species is None:
        raise RuntimeError(
            f"Transform target species unknown for {event.target}"
        )

    # Transform copies explicit current typing as well.
    enemy.type_override = battle_state.active_pokemon.type_override

    if battle_state.gen > 2:
        enemy.current_ability = battle_state.active_pokemon.ability

    battle_state.witness_transform(
        ident_raw(event.pokemon), target_species, copied_moves
    )


def _reduce_ability(battle_state: BattleState, event: AbilityEvent) -> None:
    if not event.active:
        return

    if is_self(battle_state, event.pokemon):
        battle_state.active_pokemon.ability = event.ability
        return

    enemy = resolve_enemy(battle_state, event.pokemon)
    if enemy is None:
        return

    if enemy.transformed_into is not None:
        return

    enemy.current_ability = event.ability

    if event.context is not None and to_id(event.context) == "trace":
        enemy.base_ability = "Trace"
        return

    if event.reveals_base and enemy.base_ability is Unknown.VALUE:
        enemy.base_ability = event.ability


def _reduce_stat_set(battle_state: BattleState, event: StatSetEvent) -> None:
    status = resolve_any_status(battle_state, event.target)
    status.set_stage(event.stat, event.stage)


def _reduce_item(battle_state: BattleState, event: ItemEvent) -> None:

    if (
        not event.gained
        and event.pokemon is not None
        and to_id(event.item) == "powerherb"
    ):
        status = resolve_any_status(battle_state, event.pokemon)
        clear_semi_invulnerable_status(status)

    if event.gained:
        if event.previous_owner is not None:
            victim = resolve_enemy(battle_state, event.previous_owner)
            if victim is not None:
                victim.item = None

        enemy = resolve_enemy(battle_state, event.pokemon)
        if enemy is not None:
            enemy.item = event.item
        return

    enemy = resolve_enemy(battle_state, event.pokemon)
    if enemy is None:
        return
    if (
        enemy.item is not None
        and enemy.item is not Unknown.VALUE
        and enemy.item != event.item
    ):
        raise RuntimeError(
            "Item mismatch between protocol and battle state: "
            + f"{enemy.item=}, self.item={event.item!r}"
        )
    enemy.item = None


def _reduce_cant(battle_state: BattleState, event: CantEvent) -> None:
    status = resolve_any_status(battle_state, event.pokemon)

    clear_semi_invulnerable_status(status)
    status.clear_single_move()

    if (
        event.reason == "recharge"
        or battle_state.gen == 1
        and event.reason in {"flinch", "partiallytrapped"}
    ):
        status.remove_minor(MinorStatus.RECHARGE)


def _reduce_perish_count(
    battle_state: BattleState, event: PerishCountEvent
) -> None:
    status = resolve_any_status(battle_state, event.target)
    status.perish_count = event.count
    status.add_minor(MinorStatus.PERISH_SONG)


def _reduce_upkeep(battle_state: BattleState) -> None:
    battle_state.active_pokemon.status.clear_single_turn()
    battle_state.active_pokemon.status.tick_minor_durations()

    for pokemon in battle_state.enemy_team:
        if pokemon.active:
            pokemon.status.clear_single_turn()
            pokemon.status.tick_minor_durations()


def _reduce_weather(battle_state: BattleState, event: WeatherEvent) -> None:
    if not event.started:
        battle_state.weather = None
    else:
        battle_state.weather = event.weather.value


def _reduce_single_move(
    battle_state: BattleState, event: SingleMoveEvent
) -> None:
    match to_id(event.move):
        case "destinybond":
            effect = MinorStatus.DESTINY_BOUND
        case "grudge":
            effect = MinorStatus.GRUDGE
        case _:
            return

    status = resolve_any_status(battle_state, event.pokemon)
    status.add_minor(effect)


def _reduce_type_change(
    battle_state: BattleState, event: TypeChangeEvent
) -> None:
    status = resolve_any_status(battle_state, event.target)
    status.add_minor(MinorStatus.TYPECHANGE)

    if is_self(battle_state, event.target):
        battle_state.active_pokemon.type_override = event.types
        return

    enemy = resolve_enemy(battle_state, event.target)

    if enemy is not None:
        enemy.type_override = event.types


def _reduce_forme_change(
    battle_state: BattleState, event: FormeChangeEvent
) -> None:
    own = resolve_self(battle_state, event.pokemon)

    if own is not None:
        if own.id != battle_state.curr_pokemon:
            raise RuntimeError(
                "Received forme change for non-active own Pokémon: "
                + repr(own.id)
            )

        battle_state.active_pokemon.forme = event.forme
        return

    enemy = resolve_enemy(battle_state, event.pokemon)

    if enemy is not None:
        enemy.forme = event.forme


def _reduce_decision_request(
    battle_state: BattleState, event: DecisionRequestEvent
) -> None:
    battle_state.player_id = event.player_id

    available_moves = [
        AvailableMove(
            name=move.name,
            id=move.id,
            curr_pp=move.curr_pp,
            max_pp=move.max_pp,
            target=move.target,
            disabled=move.disabled,
        )
        for move in event.moves
    ]

    previous_active = next(
        (
            pokemon
            for pokemon in battle_state.team
            if pokemon.id == battle_state.curr_pokemon
        ),
        None,
    )
    previous_base_ability = (
        previous_active.base_ability
        if previous_active is not None
        else Unknown.VALUE
    )

    available_pokemons: list[PartyPokemon] = []
    for pokemon in event.pokemon:
        max_hp = pokemon.max_hp
        if max_hp is None:
            existing = next(
                (p for p in battle_state.team if p.id == pokemon.ident), None
            )
            max_hp = (
                existing.max_hp
                if existing is not None and existing.max_hp > 0
                else 0
            )

        stats = Stats(
            atk=pokemon.atk,
            def_=pokemon.def_,
            spa=pokemon.spa,
            spd=pokemon.spd,
            spe=pokemon.spe,
            max_hp=max_hp,
        )

        available_pokemons.append(
            PartyPokemon(
                id=pokemon.ident,
                details=pokemon.details,
                lvl=pokemon.level,
                stats=stats,
                moves=list(pokemon.moves),
                base_ability=pokemon.base_ability,
                item=pokemon.item,
                pokeball=pokemon.pokeball,
                major_status=pokemon.major_status,
                curr_hp=pokemon.curr_hp,
                max_hp=max_hp,
            )
        )

    battle_state.update_team(available_pokemons)

    active = next(
        (
            pokemon
            for pokemon in available_pokemons
            if pokemon.id == battle_state.curr_pokemon
        ),
        None,
    )
    if active is not None:
        battle_state.set_active_pokemon(str(active.id))
        battle_state.active_pokemon.status.major = active.major_status

        # Persist request-level trapping state; each request overwrites it.
        battle_state.active_pokemon.trapped = event.trapped
        battle_state.active_pokemon.maybe_trapped = event.maybe_trapped

        if (
            previous_active is not None
            and previous_active.id == active.id
            and previous_base_ability != active.base_ability
            and battle_state.active_pokemon.ability == previous_base_ability
            and not battle_state.active_pokemon.transformed_into
        ) or (
            battle_state.active_pokemon.ability is Unknown.VALUE
            and not battle_state.active_pokemon.transformed_into
        ):
            battle_state.active_pokemon.ability = active.base_ability

    if battle_state.gen == 1 and not event.wait:
        has_recharge_request = any(
            move.id == "recharge" for move in available_moves
        )
        if has_recharge_request:
            battle_state.active_pokemon.status.add_minor(MinorStatus.RECHARGE)
        else:
            battle_state.active_pokemon.status.remove_minor(
                MinorStatus.RECHARGE
            )

    sync_own_two_turn_status_from_request(battle_state, event.moves, event.wait)
    battle_state.update_moves(available_moves)
    battle_state.force_switch = any(event.force_switch)


def _reduce_game_type(battle_state: BattleState, event: GameTypeEvent) -> None:
    if event.type not in event.IMPLEMENTED_TYPES:
        raise NotImplementedError(
            f"Gametype not implemented yet: {event.type} not in "
            + f"{event.IMPLEMENTED_TYPES}"
        )
    battle_state.format.gametype = event.type


def _reduce_game_gen(battle_state: BattleState, event: GameGenEvent) -> None:
    if event.gen <= 0:
        raise ValueError("Pokemon gen must be between 1 and 9")
    if event.gen > event.LAST_IMPLEMENTED_GEN:
        raise NotImplementedError(
            f"Only gen up to {event.LAST_IMPLEMENTED_GEN} was implemented"
        )
    battle_state.format.gen = event.gen


def _reduce_game_tier(battle_state: BattleState, event: GameTierEvent) -> None:
    if event.tier not in event.IMPLEMENTED_TIERS:
        raise NotImplementedError(
            f"Game tier not implemented yet: {event.tier} not in "
            + f"{event.IMPLEMENTED_TIERS}"
        )
    battle_state.format.tier = event.tier


def _reduce_partial_trap(
    battle_state: BattleState, event: PartialTrapEvent
) -> None:
    status = resolve_any_status(battle_state, event.target)
    if event.started:
        status.minor.add(MinorStatus.PARTIALLY_TRAPPED)
    else:
        status.minor.remove(MinorStatus.PARTIALLY_TRAPPED)


def reduce_battle_state(battle_state: BattleState, event: BaseEvent) -> None:
    """Apply one semantic event to SDK battle state.

    Every BattleEvent is handled explicitly. Adding a new BattleEvent without
    wiring a reducer therefore fails loudly instead of silently doing nothing.
    """
    if not isinstance(event, BattleEvent):
        return

    source = auto_reveal_source(event)
    if source is not None:
        reveal_effect_source(battle_state, source)

    match event:
        case MoveEvent():
            _reduce_move(battle_state, event)
        case DamageEvent():
            _reduce_damage(battle_state, event)
        case HealEvent():
            _reduce_heal(battle_state, event)
        case MinorStatusEvent():
            _reduce_minor_status(battle_state, event)
        case MajorStatusEvent():
            _reduce_major_status(battle_state, event)
        case MoveCopiedEvent():
            _reduce_move_copied(battle_state, event)
        case MinorStatusActivationEvent():
            _reduce_minor_status_activation(battle_state, event)
        case StatChangeEvent():
            _reduce_stat_change(battle_state, event)
        case MovePrepareEvent():
            _reduce_move_prepare(battle_state, event)
        case MoveActivationEvent():
            return
        case TeamCureEvent():
            _reduce_team_cure(battle_state, event)
        case ClearBoostsEvent():
            _reduce_clear_boosts(battle_state, event)
        case ClearAllBoostsEvent():
            _reduce_clear_all_boosts(battle_state)
        case CopyBoostEvent():
            _reduce_copy_boost(battle_state, event)
        case ClearNegativeBostsEvent():
            _reduce_clear_negative_boosts(battle_state, event)
        case SetHpEvent():
            _reduce_set_hp(battle_state, event)
        case SideConditionEvent():
            _reduce_side_condition(battle_state, event)
        case PokemonSwitchEvent():
            _reduce_switch(battle_state, event)
        case TransformEvent():
            _reduce_transform(battle_state, event)
        case AbilityEvent():
            _reduce_ability(battle_state, event)
        case StatSetEvent():
            _reduce_stat_set(battle_state, event)
        case ItemEvent():
            _reduce_item(battle_state, event)
        case CantEvent():
            _reduce_cant(battle_state, event)
        case PerishCountEvent():
            _reduce_perish_count(battle_state, event)
        case TurnEvent():
            battle_state.turn = event.turn
        case UpkeepEvent():
            _reduce_upkeep(battle_state)
        case WeatherEvent():
            _reduce_weather(battle_state, event)
        case BattleEndEvent() | RoomEvent() | PlayerEvent():
            return
        case BattleStartEvent():
            battle_state.clear_battle()
        case SingleMoveEvent():
            _reduce_single_move(battle_state, event)
        case TypeChangeEvent():
            _reduce_type_change(battle_state, event)
        case FormeChangeEvent():
            _reduce_forme_change(battle_state, event)
        case DesyncEvent():
            battle_state.gen_1_desync = True
        case DecisionRequestEvent():
            _reduce_decision_request(battle_state, event)
        case GameTypeEvent():
            _reduce_game_type(battle_state, event)
        case GameGenEvent():
            _reduce_game_gen(battle_state, event)
        case GameTierEvent():
            _reduce_game_tier(battle_state, event)
        case PartialTrapEvent():
            _reduce_partial_trap(battle_state, event)
        case TeamPreviewRequestEvent():
            battle_state.player_id = event.player_id
        case DetailsChangeEvent():
            _reduce_details_change(battle_state, event)
        case CustomShowdownBattleStateEvent():
            battle_state.custom_showdown_battlestate = event.content
        case _:
            raise NotImplementedError(
                f"No BattleState reducer for {type(event).__name__}"
            )
