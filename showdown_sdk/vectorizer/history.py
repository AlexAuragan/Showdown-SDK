import os

from showdown_sdk.classes.parser import (
    AbilityEvent,
    BaseEvent,
    CantEvent,
    ClearAllBoostsEvent,
    ClearBoostsEvent,
    ClearNegativeBostsEvent,
    CopyBoostEvent,
    DamageEvent,
    DesyncEvent,
    DetailsChangeEvent,
    EffectSource,
    FormeChangeEvent,
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
    PokemonIdent,
    PokemonSwitchEvent,
    SetHpEvent,
    SideConditionEvent,
    SingleMoveEvent,
    StatChangeEvent,
    StatSetEvent,
    TeamCureEvent,
    TransformEvent,
    TurnEvent,
    TypeChangeEvent,
    WeatherEvent,
)
from showdown_sdk.models import to_id
from showdown_sdk.models.pokemon import (
    MajorStatus,
    MinorStatus,
    SideCondition,
    Stat,
    Weather,
)
from showdown_sdk.models.sdk import BattleState, SourceType
from showdown_sdk.vectorizer.utils import (
    ability_id,
    item_id,
    move_id,
    pokemon_id,
)

Vector = list[int | float]


## Constants

_HISTORY_LENGTH_ENV_VAR = "SHOWDOWN_SDK_VECTOR_HISTORY_LENGTH"
_DEFAULT_HISTORY_LENGTH = 32


def _load_history_length() -> int:
    """
    Read the history length once at import time so one process has one stable
    history-vector schema.

    Examples:
        SHOWDOWN_SDK_VECTOR_HISTORY_LENGTH=16
        SHOWDOWN_SDK_VECTOR_HISTORY_LENGTH=32
        SHOWDOWN_SDK_VECTOR_HISTORY_LENGTH=64
    """
    raw = os.environ.get(_HISTORY_LENGTH_ENV_VAR)

    if raw is None or not raw.strip():
        return _DEFAULT_HISTORY_LENGTH

    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{_HISTORY_LENGTH_ENV_VAR} must be a positive integer, got {raw!r}"
        ) from exc

    if value <= 0:
        raise ValueError(f"{_HISTORY_LENGTH_ENV_VAR} must be > 0, got {value}")

    return value


_HISTORY_LENGTH = _load_history_length()


# These IDs are SDK schema IDs, not Showdown/Dex IDs.
# Never reorder once training data exists.
_EVENT_TYPE_IDS: dict[type[BaseEvent], int] = {
    MoveEvent: 1,
    DamageEvent: 2,
    HealEvent: 3,
    PokemonSwitchEvent: 4,
    MajorStatusEvent: 5,
    MinorStatusEvent: 6,
    MinorStatusActivationEvent: 7,
    StatChangeEvent: 8,
    StatSetEvent: 9,
    ClearBoostsEvent: 10,
    ClearAllBoostsEvent: 11,
    CopyBoostEvent: 12,
    ClearNegativeBostsEvent: 13,
    TeamCureEvent: 14,
    WeatherEvent: 15,
    SideConditionEvent: 16,
    AbilityEvent: 17,
    ItemEvent: 18,
    TransformEvent: 19,
    TypeChangeEvent: 20,
    FormeChangeEvent: 21,
    MovePrepareEvent: 22,
    MoveCopiedEvent: 23,
    MoveActivationEvent: 24,
    SingleMoveEvent: 25,
    CantEvent: 26,
    PerishCountEvent: 27,
    PartialTrapEvent: 28,
    SetHpEvent: 29,
    DetailsChangeEvent: 30,
    DesyncEvent: 31,
}

_SOURCE_TYPE_IDS = {
    SourceType.MOVE: 1,
    SourceType.ITEM: 2,
    SourceType.ABILITY: 3,
    SourceType.STATUS: 4,
    SourceType.WEATHER: 5,
    SourceType.TERRAIN: 6,
    SourceType.SIDE_CONDITION: 7,
    SourceType.RECOIL: 8,
    SourceType.UNKNOWN: 9,
}

_MAJOR_STATUS_IDS = {
    status: index for index, status in enumerate(MajorStatus, start=1)
}
_MINOR_STATUS_IDS = {
    status: index for index, status in enumerate(MinorStatus, start=1)
}
_WEATHER_IDS = {
    weather: index for index, weather in enumerate(Weather, start=1)
}
_SIDE_CONDITION_IDS = {
    condition: index for index, condition in enumerate(SideCondition, start=1)
}

_STAT_ORDER = (
    Stat.ATK,
    Stat.DEF,
    Stat.SPA,
    Stat.SPD,
    Stat.SPE,
    Stat.EVA,
    Stat.ACC,
)
_STAT_INDEX = {stat: index for index, stat in enumerate(_STAT_ORDER)}

# Match the current-state vectorizer's type order.
_TYPE_NAMES = (
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


_IDENTITY_DIM = 3
_AFFECTED_SIDE_DIM = 2
_SPECIES_DIM = 3
_KNOWN_ID_DIM = 2
_STAT_PAYLOAD_DIM = 1 + len(_STAT_ORDER) + len(_STAT_ORDER)
_HP_PAYLOAD_DIM = 5
_LEVEL_PAYLOAD_DIM = 2
_KNOWN_NUMBER_DIM = 2
_EVENT_FLAGS_DIM = 10
_TYPES_DIM = 1 + len(_TYPE_NAMES)

# Event row:
#   present                                      1
#   event_type_id                                1
#   turn                                         1
#   action_id: known, value                      2
#   source_type_id                               1
#   actor identity                               3
#   target identity                              3
#   affected side: known, side                   2
#   species: known, national dex id, form id     3
#   move: known, id                              2
#   ability: known, id                           2
#   item: known, id                              2
#   major status: known, id                      2
#   minor status: known, id                      2
#   weather: known, id                           2
#   side condition: known, id                    2
#   stat payload: mode, 7 masks, 7 values       15
#   HP payload                                   5
#   level: known, value                          2
#   hit count: known, value                      2
#   effectiveness: known, value                  2
#   perish count: known, value                   2
#   event flags                                  10
#   type change: known, 18 type bits             19
_HISTORY_EVENT_DIM = (
    1
    + 1
    + 1
    + 2
    + 1
    + _IDENTITY_DIM
    + _IDENTITY_DIM
    + _AFFECTED_SIDE_DIM
    + _SPECIES_DIM
    + 7 * _KNOWN_ID_DIM
    + _STAT_PAYLOAD_DIM
    + _HP_PAYLOAD_DIM
    + _LEVEL_PAYLOAD_DIM
    + _KNOWN_NUMBER_DIM
    + _KNOWN_NUMBER_DIM
    + _KNOWN_NUMBER_DIM
    + _EVENT_FLAGS_DIM
    + _TYPES_DIM
)

assert _HISTORY_EVENT_DIM == 88

HISTORY_DIM = _HISTORY_LENGTH * _HISTORY_EVENT_DIM


## Vectorization


def _canonical_move_name(name: str) -> str:
    value = to_id(name)

    if value.startswith("hiddenpower"):
        return "hiddenpower"

    if value.startswith("return") and value[6:].isdigit():
        return "return"

    if value.startswith("frustration") and value[11:].isdigit():
        return "frustration"

    return value


def _vectorize_move(value: str | None, *, present: bool, gen: int) -> Vector:
    if not present:
        return [0, 0]

    if value is None or value == "":
        return [1, 0]

    canonical = _canonical_move_name(value)

    # Synthetic Showdown action rather than a Dex move.
    if canonical == "recharge":
        return [1, 0]

    return [1, move_id(canonical, gen)]


def _vectorize_ability(value: str | None, *, present: bool, gen: int) -> Vector:
    if not present:
        return [0, 0]

    if value is None or value == "":
        return [1, 0]

    return [1, ability_id(value, gen)]


def _vectorize_item(value: str | None, *, present: bool, gen: int) -> Vector:
    if not present:
        return [0, 0]

    if value is None or value == "":
        return [1, 0]

    return [1, item_id(value, gen)]


def _vectorize_species(value: str | None, *, present: bool, gen: int) -> Vector:
    if not present:
        return [0, 0, 0]

    if value is None or value == "":
        return [1, 0, 0]

    species_id, form_id = pokemon_id(value, gen)
    return [1, species_id, form_id]


def _vectorize_enum_id[T](
    value: T | None, *, present: bool, ids: dict[T, int]
) -> Vector:
    if not present:
        return [0, 0]

    if value is None:
        return [1, 0]

    return [1, ids[value]]


def _relative_side(player: str, battle_state: BattleState) -> int:
    """
    1 = us
    2 = opponent

    Side 3 is reserved by history events for field/both.
    """
    player_id = battle_state.player_id

    if player_id is None:
        raise ValueError("player_id not initialized yet")

    if player == player_id:
        return 1

    if player in {"p1", "p2"} and player_id in {"p1", "p2"}:
        return 2

    raise ValueError(
        f"Cannot resolve side {player!r} relative to player {player_id!r}"
    )


def _ident_raw(ident: PokemonIdent) -> str:
    if ident.slot is None:
        return f"{ident.player}: {ident.name}"

    return f"{ident.player}{ident.slot}: {ident.name}"


def _ident_self_key(ident: PokemonIdent) -> str:
    return f"{ident.player}: {ident.name}"


def _vectorize_identity(
    ident: PokemonIdent | None, battle_state: BattleState
) -> Vector:
    """
    [0, 0, 0] -> no identity in this event field
    [1, 1, N] -> own Pokémon, 1-based request/team slot N
    [1, 2, N] -> enemy Pokémon, 1-based stable reveal slot N
    """
    if ident is None:
        return [0, 0, 0]

    side = _relative_side(ident.player, battle_state)

    if side == 1:
        key = _ident_self_key(ident)

        for index, pokemon in enumerate(battle_state.team, start=1):
            if pokemon.id == key:
                return [1, side, index]

        raise ValueError(f"Own history identity {key!r} is not in the team")

    key = _ident_raw(ident)

    # Enemy party slots are based on stable reveal order, not the physical
    # location of Unknown placeholders inside BattleState.enemy_team.
    known_enemies = [
        pokemon
        for pokemon in battle_state.enemy_team
        if isinstance(pokemon.id, str)
    ]

    for index, pokemon in enumerate(known_enemies, start=1):
        if pokemon.id == key:
            return [1, side, index]

    raise ValueError(
        f"Enemy history identity {key!r} " + "is not in the revealed enemy team"
    )


def _vectorize_types(value: tuple[str, ...] | None, *, present: bool) -> Vector:
    if not present:
        return [0] * _TYPES_DIM

    types = set(value or ())

    # Gen 1-4 can represent an effectively typeless Pokémon. "???" therefore
    # maps to a known type payload with no standard type bits set.
    invalid = types - set(_TYPE_NAMES) - {"???"}

    if invalid:
        raise ValueError(f"Unexpected history type(s): {sorted(invalid)!r}")

    vector: Vector = [1]
    vector.extend(int(type_name in types) for type_name in _TYPE_NAMES)

    assert len(vector) == _TYPES_DIM
    return vector


def _species_from_details(details: str) -> str | None:
    if not details.strip():
        return None

    species = details.split(",", 1)[0].strip()
    return species or None


def _major_status_from_name(name: str) -> MajorStatus | None:
    normalized = to_id(name.removeprefix("move: "))

    for status in MajorStatus:
        if to_id(status.value) == normalized:
            return status

    return None


def _minor_status_from_name(name: str) -> MinorStatus | None:
    normalized = to_id(name.removeprefix("move: "))

    for status in MinorStatus:
        if to_id(status.value) == normalized:
            return status

    return None


def _weather_from_name(name: str) -> Weather | None:
    normalized = to_id(name)

    for weather in Weather:
        if to_id(weather.value) == normalized:
            return weather

    return None


def _side_condition_from_name(name: str) -> SideCondition | None:
    normalized = to_id(name.removeprefix("move: "))

    for condition in SideCondition:
        if to_id(condition.value) == normalized:
            return condition

    return None


def _source_actor(source: EffectSource) -> PokemonIdent | None:
    if source.actor is not None:
        return source.actor

    return source.owner


def _vectorize_history_event(
    event: BaseEvent, *, turn: int, battle_state: BattleState
) -> Vector:
    event_type_id = _EVENT_TYPE_IDS.get(type(event))

    if event_type_id is None:
        raise ValueError(f"Unsupported history event: {type(event).__name__}")

    gen = battle_state.gen

    source: EffectSource | None = None
    action_id: int | None = None
    actor: PokemonIdent | None = None
    target: PokemonIdent | None = None
    affected_side: int | None = None

    species_present = False
    species: str | None = None

    move_present = False
    move: str | None = None

    ability_present = False
    ability: str | None = None

    item_present = False
    item: str | None = None

    major_status_present = False
    major_status: MajorStatus | None = None

    minor_status_present = False
    minor_status: MinorStatus | None = None

    weather_present = False
    weather: Weather | None = None

    side_condition_present = False
    side_condition: SideCondition | None = None

    # 0 = no stat payload
    # 1 = relative delta
    # 2 = absolute stage
    stat_mode = 0
    stat_known = [0] * len(_STAT_ORDER)
    stat_values = [0] * len(_STAT_ORDER)

    hp_present = False
    curr_hp = 0
    max_hp: int | None = None
    hp_is_percentage = False

    level: int | None = None
    hit_count: int | None = None
    effectiveness: float | None = None
    perish_count: int | None = None

    success = 0
    does_hit = 0
    crit = 0
    started = 0
    applied = 0
    active = 0
    gained = 0
    consumed = 0
    baton_pass = 0
    upkeep = 0

    types_present = False
    types: tuple[str, ...] | None = None

    match event:
        case MoveEvent():
            source = event.source
            action_id = event.action_id
            actor = event.source_pokemon
            target = event.target_pokemon

            move_present = True
            move = event.move

            success = int(event.success)
            does_hit = int(event.does_hit)
            hit_count = event.hit_count

        case DamageEvent():
            source = event.source
            target = event.target

            hp_present = True
            curr_hp = event.curr_hp
            max_hp = event.max_hp
            hp_is_percentage = event.hp_is_percentage

            # Effectiveness is meaningful for move damage, not poison,
            # weather, recoil, item damage, etc.
            effectiveness = (
                event.effectiveness
                if event.source.type is SourceType.MOVE
                else None
            )

            crit = int(event.crit)

        case HealEvent():
            source = event.source
            target = event.target

            hp_present = True
            curr_hp = event.curr_hp
            max_hp = event.max_hp
            hp_is_percentage = event.hp_is_percentage

        case MinorStatusEvent():
            source = event.source
            target = event.target

            minor_status_present = True
            minor_status = event.effect
            started = int(event.started)

        case MajorStatusEvent():
            source = event.source
            target = event.target

            major_status_present = True
            major_status = event.status
            applied = int(event.applied)

        case MoveCopiedEvent():
            source = event.source
            target = event.target

            move_present = True
            move = event.copied_move

        case MinorStatusActivationEvent():
            source = event.source
            target = event.target

            minor_status_present = True
            minor_status = event.effect

        case StatChangeEvent():
            source = event.source
            target = event.target

            success = int(event.success)

            if event.success:
                stat_mode = 1

                for stat, delta in event.stat_changes:
                    index = _STAT_INDEX[stat]

                    stat_known[index] = 1
                    stat_values[index] += delta

        case MovePrepareEvent():
            actor = event.pokemon

            move_present = True
            move = event.move

        case TeamCureEvent():
            source = event.source
            actor = event.actor

            affected_side = _relative_side(event.side, battle_state)

        case ClearBoostsEvent():
            source = event.source
            target = event.target

        case ClearAllBoostsEvent():
            source = event.source

        case CopyBoostEvent():
            source = event.source
            actor = event.user
            target = event.target

        case ClearNegativeBostsEvent():
            source = event.source
            target = event.target

        case SetHpEvent():
            source = event.source
            target = event.target

            hp_present = True
            curr_hp = event.curr_hp
            max_hp = event.max_hp
            hp_is_percentage = event.hp_is_percentage

        case SideConditionEvent():
            source = event.source

            # 1 = us
            # 2 = opponent
            # 3 = field / both
            affected_side = (
                3
                if event.side is None
                else _relative_side(event.side, battle_state)
            )

            side_condition_present = True
            side_condition = event.condition
            started = int(event.started)

        case PokemonSwitchEvent():
            actor = event.pokemon

            species_present = True
            species = _species_from_details(event.details)

            level = event.level

            hp_present = True
            curr_hp = event.curr_hp
            max_hp = event.max_hp
            hp_is_percentage = event.hp_is_percentage

            # None here means the switch explicitly showed no major status,
            # so this is known-none rather than an unused field.
            major_status_present = True
            major_status = event.major_status

            baton_pass = int(event.baton_pass)

        case TransformEvent():
            source = event.source
            actor = event.pokemon
            target = event.target

        case AbilityEvent():
            source = event.source
            actor = event.pokemon

            ability_present = True
            ability = event.ability
            active = int(event.active)

        case StatSetEvent():
            source = event.source
            target = event.target

            stat_mode = 2

            index = _STAT_INDEX[event.stat]

            stat_known[index] = 1
            stat_values[index] = event.stage

        case MoveActivationEvent():
            actor = event.pokemon

            move_present = True
            move = event.move

        case ItemEvent():
            source = event.source

            actor = event.previous_owner
            target = event.pokemon

            item_present = True
            item = event.item

            gained = int(event.gained)
            consumed = int(event.consumed)

        case CantEvent():
            actor = event.pokemon

            # CantEvent.move=None is meaningful: Showdown did not associate
            # the cant with a particular attempted move.
            move_present = True
            move = event.move

        case PerishCountEvent():
            source = event.source
            target = event.target

            perish_count = event.count

        case WeatherEvent():
            source = event.source

            weather_present = True
            weather = event.weather

            started = int(event.started)
            upkeep = int(event.upkeep)

        case SingleMoveEvent():
            source = event.source
            actor = event.pokemon

            move_present = True
            move = event.move

        case TypeChangeEvent():
            source = event.source
            target = event.target

            types_present = True
            types = event.types

        case FormeChangeEvent():
            source = event.source
            actor = event.pokemon

            species_present = True
            species = event.forme

        case PartialTrapEvent():
            source = event.source
            target = event.target

            move_present = True
            move = event.move

            started = int(event.started)

        case DetailsChangeEvent():
            actor = event.pokemon

            species_present = True
            species = _species_from_details(event.details)

            level = event.level

        case DesyncEvent():
            pass

        case _:
            raise AssertionError(f"Unhandled mapped history event: {event!r}")

    if source is not None:
        if action_id is None:
            action_id = source.action_id

        if actor is None:
            actor = _source_actor(source)

        # Preserve causal information on effect events.
        # Explicit event payload always wins over EffectSource.
        if source.name is not None:
            if source.type in {SourceType.MOVE, SourceType.RECOIL}:
                if not move_present:
                    move_present = True
                    move = source.name

            elif source.type is SourceType.ABILITY:
                if not ability_present:
                    ability_present = True
                    ability = source.name

            elif source.type is SourceType.ITEM:
                if not item_present:
                    item_present = True
                    item = source.name

            elif source.type is SourceType.STATUS:
                if not major_status_present and not minor_status_present:
                    source_major = _major_status_from_name(source.name)

                    if source_major is not None:
                        major_status_present = True
                        major_status = source_major
                    else:
                        source_minor = _minor_status_from_name(source.name)

                        if source_minor is not None:
                            minor_status_present = True
                            minor_status = source_minor

            elif source.type is SourceType.WEATHER:
                if not weather_present:
                    source_weather = _weather_from_name(source.name)

                    if source_weather is not None:
                        weather_present = True
                        weather = source_weather

            elif (
                source.type is SourceType.SIDE_CONDITION
                and not side_condition_present
            ):
                source_condition = _side_condition_from_name(source.name)

                if source_condition is not None:
                    side_condition_present = True
                    side_condition = source_condition

    vector: Vector = [
        1,
        event_type_id,
        turn,
        int(action_id is not None),
        action_id if action_id is not None else 0,
        (_SOURCE_TYPE_IDS[source.type] if source is not None else 0),
    ]

    vector.extend(_vectorize_identity(actor, battle_state))
    vector.extend(_vectorize_identity(target, battle_state))

    if affected_side is None:
        vector.extend([0, 0])
    else:
        vector.extend([1, affected_side])

    vector.extend(_vectorize_species(species, present=species_present, gen=gen))
    vector.extend(_vectorize_move(move, present=move_present, gen=gen))
    vector.extend(_vectorize_ability(ability, present=ability_present, gen=gen))
    vector.extend(_vectorize_item(item, present=item_present, gen=gen))
    vector.extend(
        _vectorize_enum_id(
            major_status, present=major_status_present, ids=_MAJOR_STATUS_IDS
        )
    )
    vector.extend(
        _vectorize_enum_id(
            minor_status, present=minor_status_present, ids=_MINOR_STATUS_IDS
        )
    )
    vector.extend(
        _vectorize_enum_id(weather, present=weather_present, ids=_WEATHER_IDS)
    )
    vector.extend(
        _vectorize_enum_id(
            side_condition,
            present=side_condition_present,
            ids=_SIDE_CONDITION_IDS,
        )
    )

    vector.append(stat_mode)
    vector.extend(stat_known)
    vector.extend(stat_values)

    if hp_present:
        vector.extend(
            [
                1,
                curr_hp,
                int(max_hp is not None),
                max_hp if max_hp is not None else 0,
                int(hp_is_percentage),
            ]
        )
    else:
        vector.extend([0] * _HP_PAYLOAD_DIM)

    vector.extend([int(level is not None), level if level is not None else 0])

    vector.extend(
        [int(hit_count is not None), hit_count if hit_count is not None else 0]
    )

    vector.extend(
        [
            int(effectiveness is not None),
            (effectiveness if effectiveness is not None else 0),
        ]
    )

    vector.extend(
        [
            int(perish_count is not None),
            perish_count if perish_count is not None else 0,
        ]
    )

    vector.extend(
        [
            success,
            does_hit,
            crit,
            started,
            applied,
            active,
            gained,
            consumed,
            baton_pass,
            upkeep,
        ]
    )

    vector.extend(_vectorize_types(types, present=types_present))

    assert len(vector) == _HISTORY_EVENT_DIM

    return vector


## Public API


def vectorize_history(battle_state: BattleState) -> Vector:
    """
    Vectorize the last N meaningful semantic battle events.

    Events are chronological (oldest -> newest) and left-padded with zero rows.

    TurnEvent does not consume a history slot; it timestamps following events.

    N is configured by SHOWDOWN_SDK_VECTOR_HISTORY_LENGTH and defaults to 32.
    """
    events: list[tuple[int, BaseEvent]] = []

    turn = 0

    for event in battle_state.history:
        if isinstance(event, TurnEvent):
            turn = event.turn
            continue

        if type(event) not in _EVENT_TYPE_IDS:
            continue

        events.append((turn, event))

    events = events[-_HISTORY_LENGTH:]

    vector: Vector = [0] * (
        (_HISTORY_LENGTH - len(events)) * _HISTORY_EVENT_DIM
    )

    for event_turn, event in events:
        vector.extend(
            _vectorize_history_event(
                event, turn=event_turn, battle_state=battle_state
            )
        )

    assert len(vector) == HISTORY_DIM

    return vector


__all__ = ["vectorize_history"]
