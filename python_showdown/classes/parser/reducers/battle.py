"""Battle event reducers.

This module owns the consequences of semantic battle events.

- ``reduce_battle_state`` applies deterministic SDK knowledge/state changes.
- ``apply_battle_runtime_event`` applies live battle-manager lifecycle state.

The parser event classes themselves remain immutable descriptions of what the
Showdown protocol reported.
"""

from python_showdown.classes.combat_handler.battle_manager import BattleManager
from python_showdown.classes.parser.events.base import BaseEvent
from python_showdown.classes.parser.events.battle import (
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
from python_showdown.classes.parser.models import (
    EffectSource,
    PokemonIdent,
    RequestMove,
)
from python_showdown.models.dex import dex, to_id
from python_showdown.models.pokemon.moves import AvailableMove
from python_showdown.models.pokemon.pokemon import EnemyPokemon, PartyPokemon, Unknown
from python_showdown.models.pokemon.status import (
    MajorStatus,
    MinorStatus,
    Stats,
    Status,
)
from python_showdown.models.sdk.battle_state import BattleState, SourceType


def _clear_semi_invulnerable_status(status: Status) -> None:
    status.remove_minor(MinorStatus.FLY)
    status.remove_minor(MinorStatus.DIVE)
    status.remove_minor(MinorStatus.TUNNEL)


def get_semi_invulnerable_status(move_name: str) -> MinorStatus | None:
    move_id = to_id(move_name)

    match move_id:
        case "dig":
            return MinorStatus.TUNNEL
        case "dive":
            return MinorStatus.DIVE
        case "fly" | "bounce":
            return MinorStatus.FLY
        case _:
            return None


def _sync_sticky_barb_from_damage(
    battle_state: BattleState,
    event: DamageEvent,
) -> None:
    """Reconcile Gen 4's silent Sticky Barb contact transfer.

    Showdown does not emit an explicit |-item| / |-enditem| pair when
    Sticky Barb moves to an itemless contact attacker. The later residual
    damage line:

        |-damage|...|[from] item: Sticky Barb

    is therefore the first public confirmation of the new holder.
    """
    source = event.source

    if (
        source.type != SourceType.ITEM
        or source.owner is not None
        or source.name is None
        or to_id(source.name) != "stickybarb"
    ):
        return

    # Enemy is taking Sticky Barb damage: it is now the holder.
    enemy = _resolve_enemy(battle_state, event.target)
    if enemy is not None:
        if enemy.item is Unknown.VALUE:
            # We only learned the item; there is no evidence that it was
            # transferred from our active Pokémon.
            enemy.item = source.name
            return

        if enemy.item is None:
            # The enemy was known to be itemless and now holds
            # Sticky Barb. If our active Pokémon was its known holder, the
            # silent contact transfer moved it away from us.
            if battle_state.curr_pokemon:
                own = battle_state.get_curr_pokemon()
                if to_id(own.item) == "stickybarb":
                    own.item = ""

            enemy.item = source.name
            return

        if to_id(enemy.item) != "stickybarb":
            raise RuntimeError(
                "Sticky Barb damage contradicts the known enemy item: "
                + f"{enemy.item=}"
            )

        return

    # Our Pokémon is taking Sticky Barb damage: it is now the holder.
    own = _resolve_self(battle_state, event.target)
    if own is None:
        return

    if to_id(own.item) == "stickybarb":
        return

    # The request snapshot says we were itemless, so find the only known
    # active opposing Sticky Barb holder and clear it.
    if own.item == "":
        enemy_holders = [
            pokemon
            for pokemon in battle_state.enemy_team
            if (
                pokemon.active
                and pokemon.item is not Unknown.VALUE
                and pokemon.item is not None
                and to_id(pokemon.item) == "stickybarb"
            )
        ]

        if len(enemy_holders) == 1:
            enemy_holders[0].item = None

        own.item = source.name


def _reduce_move_prepare(
    battle_state: BattleState,
    event: MovePrepareEvent,
) -> None:
    minor = get_semi_invulnerable_status(event.move)
    if minor is None:
        return

    gen = battle_state.gen
    if gen is None:
        raise RuntimeError("gen is not set")

    condition = dex.gen(gen).conditions.get("twoturnmove")

    duration: int | None = None
    if isinstance(condition, dict):
        condition_duration = condition.get("duration")
        if isinstance(condition_duration, int):
            duration = condition_duration

    status = _resolve_any_status(
        battle_state,
        event.pokemon,
    )
    status.add_minor(minor, duration=duration)


def _sync_own_two_turn_status_from_request(
    battle_state: BattleState,
    moves: tuple[RequestMove, ...],
    wait: bool,
) -> None:
    if wait:
        return

    status = battle_state.curr_pokemon_status
    if len(moves) != 1:
        return

    move = moves[0]

    # Locked moves are sent without PP/maxPP.
    if move.curr_pp is not None or move.max_pp is not None:
        return

    gen = battle_state.gen
    if gen is None:
        raise RuntimeError("gen is not set")

    move_id = to_id(move.id)

    if move_id not in dex.get_charge_moves(gen):
        return

    minor = get_semi_invulnerable_status(move_id)
    if minor is not None and minor not in status.minor:
        status.add_minor(minor, duration=1)


def _showdown_volatile_id(effect: MinorStatus) -> str:
    match effect:
        case MinorStatus.RECHARGE:
            return "mustrecharge"
        case MinorStatus.PERISH_SONG:
            return "perishsong"
        case MinorStatus.FLY | MinorStatus.DIVE | MinorStatus.TUNNEL:
            return "twoturnmove"
        case MinorStatus.REPEAT:
            return "lockedmove"
        case _:
            return to_id(effect.value)


def _ident_raw(ident: PokemonIdent) -> str:
    """Reconstruct the protocol identifier string (`p2a: Magnemite`)."""
    if ident.slot is not None:
        return f"{ident.player}{ident.slot}: {ident.name}"
    return f"{ident.player}: {ident.name}"


def _copy_baton_pass_status(target: Status, source: Status, gen: int) -> None:
    target.copy_stat_changes(source)

    generation = dex.gen(gen)

    for effect in source.minor:
        volatile_id = _showdown_volatile_id(effect)

        if not generation.is_volatile_copyable(volatile_id):
            continue

        if effect is MinorStatus.TRAPPED:
            if source.trapped_by_side is None:
                raise RuntimeError("TRAPPED status has no trapping source")

            target.set_trapped(source.trapped_by_side)
            continue

        target.add_minor(effect)

        if effect is MinorStatus.PERISH_SONG:
            target.perish_count = source.perish_count


def _ident_self_key(ident: PokemonIdent) -> str:
    """The side-level identifier used by `PartyPokemon.id` (`p1: Miltank`)."""
    return f"{ident.player}: {ident.name}"


def _clear_traps_sourced_by_side(battle_state: BattleState, side: str) -> None:
    statuses = [battle_state.curr_pokemon_status]
    statuses.extend(pokemon.status for pokemon in battle_state.enemy_team)

    for status in statuses:
        if status.trapped_by_side == side:
            status.clear_trapped()


def _is_self(battle_state: BattleState, ident: PokemonIdent) -> bool:
    if not battle_state.player_id:
        raise ValueError("Battle State has no player_id.")
    return ident.player == battle_state.player_id


def _resolve_enemy(
    battle_state: BattleState,
    ident: PokemonIdent | None,
) -> EnemyPokemon | None:
    if (
        ident is None
        or not battle_state.player_id
        or ident.player == battle_state.player_id
    ):
        return None

    pokemon = battle_state.get_enemy_pokemon(
        _ident_raw(ident),
        not_found_ok=True,
    )
    if pokemon is not None:
        return pokemon

    if ident.slot is None:
        return battle_state.get_enemy_pokemon(
            f"{ident.player}a: {ident.name}",
            not_found_ok=True,
        )

    return None


def _resolve_self(
    battle_state: BattleState,
    ident: PokemonIdent | None,
) -> PartyPokemon | None:
    if (
        ident is None
        or not battle_state.player_id
        or ident.player != battle_state.player_id
    ):
        return None
    key = _ident_self_key(ident)
    return next((pokemon for pokemon in battle_state.team if pokemon.id == key), None)


def _resolve_any_status(battle_state: BattleState, ident: PokemonIdent) -> Status:
    if _is_self(battle_state, ident):
        return battle_state.curr_pokemon_status

    pokemon = _resolve_enemy(battle_state, ident)
    if pokemon is None:
        raise RuntimeError(
            f"Pokemon {ident} not found in enemy team {battle_state.enemy_team}"
        )
    return pokemon.status


def _parse_details(details: str) -> tuple[str | None, bool]:
    shiny = False
    gender: str | None = None
    if ", shiny" in details:
        shiny = True
        details = details.replace(", shiny", "")
    if ", M" in details:
        gender = "M"
        details = details.replace(", M", "")
    elif ", F" in details:
        gender = "F"
        details = details.replace(", F", "")
    return gender, shiny


def _reveal_effect_source(battle_state: BattleState, source: EffectSource) -> None:
    if source.owner is None or source.name is None:
        return

    enemy = _resolve_enemy(battle_state, source.owner)
    if enemy is None:
        return

    if source.type == SourceType.ITEM:
        if enemy.item is Unknown.VALUE:
            enemy.item = source.name
            return

        assert enemy.item == source.name, f"{enemy.item=}, {source.name=}"

    if source.type == SourceType.ABILITY:
        enemy.current_ability = source.name


def _auto_reveal_source(event: BattleEvent) -> EffectSource | None:
    """Return the source that the old BattleEvent base class auto-revealed.

    ItemEvent intentionally opts out, matching its previous override of
    ``update_battle_state``.
    """
    match event:
        case MoveEvent(source=source):
            return source
        case DamageEvent(source=source):
            return source
        case HealEvent(source=source):
            return source
        case MinorStatusEvent(source=source):
            return source
        case MajorStatusEvent(source=source):
            return source
        case MoveCopiedEvent(source=source):
            return source
        case MinorStatusActivationEvent(source=source):
            return source
        case StatChangeEvent(source=source):
            return source
        case TeamCureEvent(source=source):
            return source
        case ClearBoostsEvent(source=source):
            return source
        case ClearAllBoostsEvent(source=source):
            return source
        case CopyBoostEvent(source=source):
            return source
        case ClearNegativeBostsEvent(source=source):
            return source
        case SetHpEvent(source=source):
            return source
        case SideConditionEvent(source=source):
            return source
        case TransformEvent(source=source):
            return source
        case AbilityEvent(source=source):
            return source
        case StatSetEvent(source=source):
            return source
        case PerishCountEvent(source=source):
            return source
        case WeatherEvent(source=source):
            return source
        case SingleMoveEvent(source=source):
            return source
        case TypeChangeEvent(source=source):
            return source
        case FormeChangeEvent(source=source):
            return source
        case PartialTrapEvent(source=source):
            return source
        case _:
            return None


def _reduce_move(
    battle_state: BattleState,
    event: MoveEvent,
) -> None:
    source_status = _resolve_any_status(
        battle_state,
        event.source_pokemon,
    )
    source_status.clear_single_move()

    if battle_state.gen_1_desync:
        battle_state.gen_1_desync = False
        return

    gen = battle_state.gen
    if gen is None:
        raise RuntimeError("gen is not set")

    if event.source is not None:
        if event.source.type == SourceType.MOVE and event.source.name == "Mirror Move":
            return

        if (
            event.source.type == SourceType.ABILITY
            and event.source.name == "Magic Bounce"
        ):
            return

        if event.source.name == event.move and to_id(
            event.move
        ) in dex.get_charge_moves(gen):
            return

    enemy = _resolve_enemy(
        battle_state,
        event.source_pokemon,
    )
    if enemy is not None:
        enemy.witness_move(event.move)

    # Gen 1 does not emit an explicit `|-activate|...|move: Wrap`
    # when partial trapping starts, so it has to be inferred.
    #
    # Later generations do emit that activation, which becomes a
    # MinorStatusEvent. Inferring it here would incorrectly mark targets
    # as trapped when Wrap/Bind/etc. are blocked by Protect.
    if (
        gen == 1
        and event.success
        and event.does_hit
        and event.target_pokemon is not None
    ):
        move_data = dex.gen(gen).move(event.move)

        if (
            isinstance(move_data, dict)
            and move_data.get("volatileStatus") == MinorStatus.PARTIALLY_TRAPPED.value
        ):
            condition = dex.gen(gen).conditions[MinorStatus.PARTIALLY_TRAPPED.value]

            duration = (
                condition.get("duration") if isinstance(condition, dict) else None
            )

            target_status = _resolve_any_status(
                battle_state,
                event.target_pokemon,
            )

            target_status.add_minor(
                MinorStatus.PARTIALLY_TRAPPED,
                duration=duration if isinstance(duration, int) else None,
            )


def _reduce_damage(battle_state: BattleState, event: DamageEvent) -> None:
    _sync_sticky_barb_from_damage(battle_state, event)
    enemy = _resolve_enemy(battle_state, event.target)
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

    own = _resolve_self(battle_state, event.target)
    if own is not None:
        own.curr_hp = event.curr_hp


def _reduce_heal(battle_state: BattleState, event: HealEvent) -> None:
    cures_status = event.source.type == SourceType.MOVE and event.source.name in {
        "Healing Wish",
        "Lunar Dance",
    }

    enemy = _resolve_enemy(battle_state, event.target)
    if enemy is not None:
        enemy.curr_hp_percent = event.curr_hp
        if cures_status:
            enemy.status.clear_all_major_status()
        if event.curr_hp == 0:
            enemy.fainted = True
        return

    own = _resolve_self(battle_state, event.target)
    if own is not None:
        own.curr_hp = event.curr_hp
        if cures_status:
            own.major_status = None
            battle_state.curr_pokemon_status.clear_all_major_status()


def _reduce_minor_status(
    battle_state: BattleState,
    event: MinorStatusEvent,
) -> None:
    status = _resolve_any_status(battle_state, event.target)

    if not event.started:
        status.remove_minor(event.effect)
        return

    duration: int | None = None

    if event.effect is MinorStatus.RECHARGE:
        gen = battle_state.gen
        if gen is None:
            raise RuntimeError("gen is not set")

        condition = dex.gen(gen).conditions.get("mustrecharge")
        if isinstance(condition, dict):
            condition_duration = condition.get("duration")
            if isinstance(condition_duration, int) and condition_duration > 0:
                duration = condition_duration

    status.add_minor(event.effect, duration=duration)


def _reduce_major_status(battle_state: BattleState, event: MajorStatusEvent) -> None:
    if event.status is MajorStatus.FAINT:
        _clear_traps_sourced_by_side(battle_state, event.target.player)
        enemy = _resolve_enemy(battle_state, event.target)
        if enemy is not None:
            enemy.reset_on_switch_in()
            enemy.status.clear_all_major_status()
            enemy.fainted = True
            enemy.active = False
            enemy.curr_hp_percent = 0
            return

        own = _resolve_self(battle_state, event.target)
        if own is not None:
            own.curr_hp = 0
            own.major_status = None
        return

    status = _resolve_any_status(battle_state, event.target)
    if event.applied:
        status.set_status(event.status)
        if battle_state.gen == 1 and event.status is MajorStatus.SLEEP:
            status.remove_minor(MinorStatus.RECHARGE)
    else:
        status.clear_status(event.status)


def _reduce_move_copied(battle_state: BattleState, event: MoveCopiedEvent) -> None:
    own = _resolve_self(battle_state, event.target)
    if own is not None:
        mimic_slots = [
            index for index, move in enumerate(own.moves) if to_id(move) == "mimic"
        ]
        if len(mimic_slots) != 1:
            raise RuntimeError(
                "Own Pokémon used Mimic but expected exactly one Mimic slot: "
                + f"{own.moves}"
            )
        own.moves[mimic_slots[0]] = event.copied_move
        return

    enemy = _resolve_enemy(battle_state, event.target)
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
    battle_state: BattleState,
    event: MinorStatusActivationEvent,
) -> None:

    status = _resolve_any_status(battle_state, event.target)
    if event.effect is MinorStatus.CONFUSION:
        status.clear_single_move()
        return

    if event.effect is not MinorStatus.TRAPPED:
        return

    actor = event.source.actor
    if actor is None:
        raise RuntimeError("TRAPPED activation has no source actor")

    status = _resolve_any_status(battle_state, event.target)
    status.set_trapped(actor.player)


def _reduce_stat_change(battle_state: BattleState, event: StatChangeEvent) -> None:
    if not event.success:
        return

    status = _resolve_any_status(battle_state, event.target)
    for stat, delta in event.stat_changes:
        status.boost(stat, delta)


def _reduce_details_change(
    battle_state: BattleState,
    event: DetailsChangeEvent,
) -> None:
    own = _resolve_self(battle_state, event.pokemon)
    if own is not None:
        own.details = event.details
        own.lvl = event.level
        return

    enemy = _resolve_enemy(battle_state, event.pokemon)
    if enemy is None:
        return

    enemy.lvl = event.level
    enemy.forme = event.details.split(",", 1)[0].strip()


def _reduce_team_cure(battle_state: BattleState, event: TeamCureEvent) -> None:
    if _is_self(battle_state, event.actor):
        for pokemon in battle_state.team:
            pokemon.major_status = None
        battle_state.curr_pokemon_status.major = None
        return

    for pokemon in battle_state.enemy_team:
        pokemon.status.clear_all_major_status()


def _reduce_clear_boosts(battle_state: BattleState, event: ClearBoostsEvent) -> None:
    status = _resolve_any_status(battle_state, event.target)
    status.reset_all_stages()


def _reduce_clear_all_boosts(battle_state: BattleState) -> None:
    battle_state.curr_pokemon_status.reset_all_stages()
    for pokemon in battle_state.enemy_team:
        if pokemon.active:
            pokemon.status.reset_all_stages()


def _reduce_copy_boost(battle_state: BattleState, event: CopyBoostEvent) -> None:
    user_status = _resolve_any_status(battle_state, event.user)
    target_status = _resolve_any_status(battle_state, event.target)
    user_status.copy_stat_changes(target_status)


def _reduce_clear_negative_boosts(
    battle_state: BattleState,
    event: ClearNegativeBostsEvent,
) -> None:
    status = _resolve_any_status(battle_state, event.target)
    status.clear_negative_stages()


def _reduce_set_hp(battle_state: BattleState, event: SetHpEvent) -> None:
    enemy = _resolve_enemy(battle_state, event.target)
    if enemy is not None:
        enemy.curr_hp_percent = event.curr_hp
        if event.curr_hp == 0:
            enemy.fainted = True
        return

    own = _resolve_self(battle_state, event.target)
    if own is not None:
        own.curr_hp = event.curr_hp


def _reduce_side_condition(
    battle_state: BattleState,
    event: SideConditionEvent,
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
        field_conditions[event.condition] = field_conditions.get(event.condition, 0) + 1
    else:
        field_conditions.pop(event.condition, None)


def _reduce_switch(battle_state: BattleState, event: PokemonSwitchEvent) -> None:
    gen = battle_state.gen
    if gen is None:
        raise RuntimeError("gen is not set")

    if not event.baton_pass and event.command != "replace":
        _clear_traps_sourced_by_side(battle_state, event.pokemon.player)

    if _is_self(battle_state, event.pokemon):
        old_status = battle_state.curr_pokemon_status
        new_status = Status(major=event.major_status)

        if event.baton_pass:
            _copy_baton_pass_status(new_status, old_status, gen)

        battle_state.set_active_pokemon(_ident_self_key(event.pokemon))
        battle_state.curr_pokemon_transformed = False

        own = _resolve_self(battle_state, event.pokemon)
        if own is not None:
            battle_state.curr_pokemon_ability = own.base_ability
        else:
            battle_state.curr_pokemon_ability = Unknown.VALUE

        if not battle_state.team:
            return

        battle_state.curr_pokemon_status = new_status
        return

    passed_status: Status | None = None
    if event.baton_pass:
        outgoing = battle_state.get_enemy_pokemon(
            battle_state.curr_enemy_pokemon,
            not_found_ok=True,
        )
        if outgoing is not None:
            passed_status = Status()
            _copy_baton_pass_status(passed_status, outgoing.status, gen)

    gender, shiny = _parse_details(event.details)
    level = event.level

    battle_state.witness_switch_in(
        _ident_raw(event.pokemon),
        level,
        gender=gender,
        shiny=shiny,
    )

    enemy = battle_state.get_enemy_pokemon(
        battle_state.curr_enemy_pokemon,
        not_found_ok=True,
    )
    if enemy is None:
        return

    enemy.reset_on_switch_in()
    if passed_status is not None:
        _copy_baton_pass_status(enemy.status, passed_status, gen)


def _reduce_transform(battle_state: BattleState, event: TransformEvent) -> None:
    source_status = _resolve_any_status(battle_state, event.pokemon)
    target_status = _resolve_any_status(battle_state, event.target)
    source_status.copy_stat_changes(target_status)

    if _is_self(battle_state, event.pokemon):
        if battle_state.gen is None:
            raise RuntimeError("gen is not set")

        if _is_self(battle_state, event.pokemon):
            battle_state.curr_pokemon_transformed = True

            if battle_state.gen > 2:
                target = _resolve_enemy(battle_state, event.target)
                if target is None:
                    raise RuntimeError(
                        f"Transform target {event.target} not found in enemy team"
                    )

                battle_state.curr_pokemon_ability = target.current_ability

            return

    enemy = _resolve_enemy(battle_state, event.pokemon)
    if enemy is None:
        return

    copied_moves: list[str] | None = None
    own = _resolve_self(battle_state, event.target)
    if own is not None:
        copied_moves = list(own.moves)

        if battle_state.gen is None:
            raise RuntimeError("gen is not set")
        if battle_state.gen > 2:
            enemy.current_ability = battle_state.curr_pokemon_ability

    battle_state.witness_transform(
        _ident_raw(event.pokemon),
        _ident_raw(event.target),
        copied_moves,
    )


def _reduce_ability(battle_state: BattleState, event: AbilityEvent) -> None:
    if not event.active:
        return

    if _is_self(battle_state, event.pokemon):
        battle_state.curr_pokemon_ability = event.ability
        return

    enemy = _resolve_enemy(battle_state, event.pokemon)
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
    status = _resolve_any_status(battle_state, event.target)
    status.set_stage(event.stat, event.stage)


def _reduce_item(battle_state: BattleState, event: ItemEvent) -> None:

    if (
        not event.gained
        and event.pokemon is not None
        and to_id(event.item) == "powerherb"
    ):
        status = _resolve_any_status(
            battle_state,
            event.pokemon,
        )
        _clear_semi_invulnerable_status(status)

    if event.gained:
        if event.previous_owner is not None:
            victim = _resolve_enemy(battle_state, event.previous_owner)
            if victim is not None:
                victim.item = None

        enemy = _resolve_enemy(battle_state, event.pokemon)
        if enemy is not None:
            enemy.item = event.item
        return

    enemy = _resolve_enemy(battle_state, event.pokemon)
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
    status = _resolve_any_status(battle_state, event.pokemon)

    _clear_semi_invulnerable_status(status)
    status.clear_single_move()

    if (
        event.reason == "recharge"
        or battle_state.gen == 1
        and event.reason in {"flinch", "partiallytrapped"}
    ):
        status.remove_minor(MinorStatus.RECHARGE)


def _reduce_perish_count(battle_state: BattleState, event: PerishCountEvent) -> None:
    status = _resolve_any_status(battle_state, event.target)
    status.perish_count = event.count
    status.add_minor(MinorStatus.PERISH_SONG)


def _reduce_upkeep(battle_state: BattleState) -> None:
    battle_state.curr_pokemon_status.clear_single_turn()
    battle_state.curr_pokemon_status.tick_minor_durations()

    for pokemon in battle_state.enemy_team:
        if pokemon.active:
            pokemon.status.clear_single_turn()
            pokemon.status.tick_minor_durations()


def _reduce_weather(battle_state: BattleState, event: WeatherEvent) -> None:
    if not event.started:
        battle_state.weather = None
    else:
        battle_state.weather = event.weather.value


def _reduce_single_move(battle_state: BattleState, event: SingleMoveEvent) -> None:
    match to_id(event.move):
        case "destinybond":
            effect = MinorStatus.DESTINY_BOUND
        case "grudge":
            effect = MinorStatus.GRUDGE
        case _:
            return

    status = _resolve_any_status(battle_state, event.pokemon)
    status.add_minor(effect)


def _reduce_type_change(battle_state: BattleState, event: TypeChangeEvent) -> None:
    status = _resolve_any_status(battle_state, event.target)
    status.add_minor(MinorStatus.TYPECHANGE)


def _reduce_forme_change(battle_state: BattleState, event: FormeChangeEvent) -> None:
    enemy = _resolve_enemy(battle_state, event.pokemon)
    if enemy is not None:
        enemy.forme = event.forme


def _reduce_decision_request(
    battle_state: BattleState,
    event: DecisionRequestEvent,
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
        previous_active.base_ability if previous_active is not None else Unknown.VALUE
    )

    available_pokemons: list[PartyPokemon] = []
    for pokemon in event.pokemon:
        max_hp = pokemon.max_hp
        if max_hp is None:
            existing = next(
                (p for p in battle_state.team if p.id == pokemon.ident),
                None,
            )
            max_hp = (
                existing.max_hp if existing is not None and existing.max_hp > 0 else 0
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
                active=pokemon.active,
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
        (pokemon for pokemon in available_pokemons if pokemon.active),
        None,
    )
    if active is not None:
        battle_state.set_active_pokemon(str(active.id))
        battle_state.curr_pokemon_status.major = active.major_status

        if (
            previous_active is not None
            and previous_active.id == active.id
            and previous_base_ability != active.base_ability
            and battle_state.curr_pokemon_ability == previous_base_ability
            and not battle_state.curr_pokemon_transformed
        ) or (
            battle_state.curr_pokemon_ability is Unknown.VALUE
            and not battle_state.curr_pokemon_transformed
        ):
            battle_state.curr_pokemon_ability = active.base_ability

    if battle_state.gen == 1 and not event.wait:
        has_recharge_request = any(move.id == "recharge" for move in available_moves)
        if has_recharge_request:
            battle_state.curr_pokemon_status.add_minor(MinorStatus.RECHARGE)
        else:
            battle_state.curr_pokemon_status.remove_minor(MinorStatus.RECHARGE)

    _sync_own_two_turn_status_from_request(
        battle_state,
        event.moves,
        event.wait,
    )
    battle_state.update_moves(available_moves)
    battle_state.force_switch = any(event.force_switch)


def _reduce_game_type(battle_state: BattleState, event: GameTypeEvent) -> None:
    if event.type not in event.IMPLEMENTED_TYPES:
        raise NotImplementedError(
            f"Gametype not implemented yet: {event.type} not in "
            + f"{event.IMPLEMENTED_TYPES}"
        )
    battle_state.gametype = event.type


def _reduce_game_gen(battle_state: BattleState, event: GameGenEvent) -> None:
    if event.gen <= 0:
        raise ValueError("Pokemon gen must be between 1 and 9")
    if event.gen > event.LAST_IMPLEMENTED_GEN:
        raise NotImplementedError(
            f"Only gen up to {event.LAST_IMPLEMENTED_GEN} was implemented"
        )
    battle_state.gen = event.gen


def _reduce_game_tier(_battle_state: BattleState, event: GameTierEvent) -> None:
    if event.tier not in event.IMPLEMENTED_TIERS:
        raise NotImplementedError(
            f"Game tier not implemented yet: {event.tier} not in "
            + f"{event.IMPLEMENTED_TIERS}"
        )


def _reduce_partial_trap(battle_state: BattleState, event: PartialTrapEvent) -> None:
    status = _resolve_any_status(battle_state, event.target)
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

    source = _auto_reveal_source(event)
    if source is not None:
        _reveal_effect_source(battle_state, source)

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


def _apply_room_event(manager: BattleManager, event: RoomEvent) -> None:
    if not manager.room_id:
        manager.room_id = event.room_id
        manager.room_ready.set()

    if manager.room_id != event.room_id:
        raise RuntimeError(
            "Room id changed during battle",
            manager.room_id,
            event.room_id,
        )


def _apply_decision_request(
    manager: BattleManager,
    event: DecisionRequestEvent,
) -> None:
    new_id = None if event.wait else event.request_id
    manager.log_manager.battle.debug(
        "|request| update_manager: setting request_id=%r (was %r, wait=%s, "
        + "force_switch=%s, rqid=%r)",
        new_id,
        manager.request_id,
        event.wait,
        event.force_switch,
        event.request_id,
        extra={"room_id": manager.room_id},
    )

    manager.request_id = new_id
    manager.choice_rejected = False
    manager.retry_rqid = None
    manager.retry_count = 0

    if not event.wait:
        manager.last_request_id = None


def apply_battle_runtime_event(manager: BattleManager, event: BaseEvent) -> None:
    """Apply live battle-manager effects for one event.

    Deterministic battle knowledge belongs in ``reduce_battle_state``. This
    function only owns room/battle lifecycle and request bookkeeping that needs
    BattleManager/session context.
    """
    if not isinstance(event, BattleEvent):
        return

    match event:
        case BattleEndEvent():
            if manager.room_id == event.room_id:
                manager.finish_battle(event.winner)
        case RoomEvent():
            _apply_room_event(manager, event)
        case BattleStartEvent():
            manager.room_id = event.room_id
            manager.room_ready.set()
        case PlayerEvent():
            if event.name == manager.player_username:
                manager.battle_state.player_id = event.slot
        case DecisionRequestEvent():
            _apply_decision_request(manager, event)
        case TeamPreviewRequestEvent():
            manager.requires_team_preview = True
        case _:
            return
