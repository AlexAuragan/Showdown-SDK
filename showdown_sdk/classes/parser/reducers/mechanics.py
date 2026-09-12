from showdown_sdk.classes.parser.events.battle import (
    AbilityEvent,
    BattleEvent,
    ClearAllBoostsEvent,
    ClearBoostsEvent,
    ClearNegativeBoostsEvent,
    CopyBoostEvent,
    DamageEvent,
    FormeChangeEvent,
    HealEvent,
    MajorStatusEvent,
    MinorStatusActivationEvent,
    MinorStatusEvent,
    MoveCopiedEvent,
    MoveEvent,
    PartialTrapEvent,
    PerishCountEvent,
    SetHpEvent,
    SideConditionEvent,
    SingleMoveEvent,
    StatChangeEvent,
    StatSetEvent,
    TeamCureEvent,
    TransformEvent,
    TypeChangeEvent,
    WeatherEvent,
)
from showdown_sdk.classes.parser.models import (
    EffectSource,
    PokemonIdent,
    RequestMove,
)
from showdown_sdk.exceptions import BattleStateInvariantError
from showdown_sdk.models import dex, to_id
from showdown_sdk.models.pokemon import (
    EnemyPokemon,
    MinorStatus,
    PartyPokemon,
    Status,
    Unknown,
)
from showdown_sdk.models.sdk import BattleState, SourceType

## Semi-invulnerable status


def clear_semi_invulnerable_status(status: Status) -> None:
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


def sync_sticky_barb_from_damage(
    battle_state: BattleState, event: DamageEvent
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
    enemy = resolve_enemy(battle_state, event.target)
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
            raise BattleStateInvariantError(
                "Sticky Barb damage contradicts the known enemy item: "
                + f"{enemy.item=}"
            )

        return

    # Our Pokémon is taking Sticky Barb damage: it is now the holder.
    own = resolve_self(battle_state, event.target)
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


def sync_own_two_turn_status_from_request(
    battle_state: BattleState, moves: tuple[RequestMove, ...], wait: bool
) -> None:
    if wait:
        return

    status = battle_state.active_pokemon.status
    if len(moves) != 1:
        return

    move = moves[0]

    # Locked moves are sent without PP/maxPP.
    if move.curr_pp is not None or move.max_pp is not None:
        return

    if not dex.is_charge_move(move.id, gen=battle_state.gen):
        return

    minor = get_semi_invulnerable_status(move.id)
    if minor is not None and minor not in status.minor:
        status.add_minor(minor, duration=1)


## Reducer helpers


def showdown_volatile_id(effect: MinorStatus) -> str:
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


def ident_raw(ident: PokemonIdent) -> str:
    """Reconstruct the protocol identifier string (`p2a: Magnemite`)."""
    if ident.slot is not None:
        return f"{ident.player}{ident.slot}: {ident.name}"
    return f"{ident.player}: {ident.name}"


def copy_baton_pass_status(target: Status, source: Status, gen: int) -> None:
    target.copy_stat_changes(source)

    for effect in source.minor:
        volatile_id = showdown_volatile_id(effect)

        if not dex.is_volatile_copyable(volatile_id, gen=gen):
            continue

        if effect is MinorStatus.TRAPPED:
            if source.trapped_by_side is None:
                raise BattleStateInvariantError(
                    "TRAPPED status has no trapping source"
                )

            target.set_trapped(source.trapped_by_side)
            continue

        target.add_minor(effect)

        if effect is MinorStatus.PERISH_SONG:
            target.perish_count = source.perish_count


def ident_self_key(ident: PokemonIdent) -> str:
    """The side-level identifier used by `PartyPokemon.id` (`p1: Miltank`)."""
    return f"{ident.player}: {ident.name}"


def clear_traps_sourced_by_side(battle_state: BattleState, side: str) -> None:
    statuses = [battle_state.active_pokemon.status]
    statuses.extend(pokemon.status for pokemon in battle_state.enemy_team)

    for status in statuses:
        if status.trapped_by_side == side:
            status.clear_trapped()


## Pokémon resolution


def is_self(battle_state: BattleState, ident: PokemonIdent) -> bool:
    if not battle_state.player_id:
        raise BattleStateInvariantError("Battle State has no player_id.")
    return ident.player == battle_state.player_id


def resolve_enemy(
    battle_state: BattleState, ident: PokemonIdent | None
) -> EnemyPokemon | None:
    if (
        ident is None
        or not battle_state.player_id
        or ident.player == battle_state.player_id
    ):
        return None

    pokemon = battle_state.get_enemy_pokemon(
        ident_raw(ident), not_found_ok=True
    )
    if pokemon is not None:
        return pokemon

    if ident.slot is None:
        return battle_state.get_enemy_pokemon(
            f"{ident.player}a: {ident.name}", not_found_ok=True
        )

    return None


def resolve_self(
    battle_state: BattleState, ident: PokemonIdent | None
) -> PartyPokemon | None:
    if (
        ident is None
        or not battle_state.player_id
        or ident.player != battle_state.player_id
    ):
        return None
    key = ident_self_key(ident)
    return next(
        (pokemon for pokemon in battle_state.team if pokemon.id == key), None
    )


def resolve_any_status(
    battle_state: BattleState, ident: PokemonIdent
) -> Status:
    if is_self(battle_state, ident):
        return battle_state.active_pokemon.status

    pokemon = resolve_enemy(battle_state, ident)
    if pokemon is None:
        raise BattleStateInvariantError(
            f"Pokemon {ident} not found in enemy team {battle_state.enemy_team}"
        )
    return pokemon.status


## Source reveal


def reveal_effect_source(
    battle_state: BattleState, source: EffectSource
) -> None:
    if source.owner is None or source.name is None:
        return

    enemy = resolve_enemy(battle_state, source.owner)
    if enemy is None:
        return

    if source.type == SourceType.ITEM:
        if enemy.item is Unknown.VALUE:
            enemy.item = source.name
            return

        if enemy.item != source.name:
            raise BattleStateInvariantError(
                f"Item source mismatch: {enemy.item=}, {source.name=}"
            )

    if source.type == SourceType.ABILITY:
        enemy.current_ability = source.name


def auto_reveal_source(event: BattleEvent) -> EffectSource | None:
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
        case ClearNegativeBoostsEvent(source=source):
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
