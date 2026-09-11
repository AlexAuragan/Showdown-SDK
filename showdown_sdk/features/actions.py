"""Feature objects and converters for the current legal decision."""

from dataclasses import dataclass

from showdown_sdk.features.common import canonical, canonical_move
from showdown_sdk.models.pokemon import MinorStatus
from showdown_sdk.models.sdk import BattleState

## Data models


@dataclass(frozen=True)
class MoveActionFeatures:
    request_index: int
    name: str
    current_pp: int | None = None
    max_pp: int | None = None
    disabled: bool = False


@dataclass(frozen=True)
class SwitchActionFeatures:
    team_slot: int
    pokemon_id: str
    species: str


@dataclass(frozen=True)
class ActionFeatures:
    """One currently legal action: either a move or a switch, never both."""

    kind: str  # "move" | "switch"
    move: MoveActionFeatures | None = None
    switch: SwitchActionFeatures | None = None


## Feature builders


def available_actions_to_features(
    battle_state: BattleState,
) -> tuple[ActionFeatures, ...]:
    """Every action the client can legitimately send to Showdown right now."""
    actions: list[ActionFeatures] = []

    for index, move in enumerate(battle_state.available_moves):
        actions.append(
            ActionFeatures(
                kind="move",
                move=MoveActionFeatures(
                    request_index=index,
                    name=canonical_move(move.name or move.id),
                    current_pp=move.curr_pp,
                    max_pp=move.max_pp,
                    disabled=move.disabled,
                ),
            )
        )

    for index, pokemon in enumerate(battle_state.team):
        if not _switch_is_legal(battle_state, index):
            continue

        pokemon_id = pokemon.id
        assert isinstance(pokemon_id, str)

        actions.append(
            ActionFeatures(
                kind="switch",
                switch=SwitchActionFeatures(
                    team_slot=index,
                    pokemon_id=canonical(pokemon_id),
                    species=canonical(pokemon.details.split(",", 1)[0].strip()),
                ),
            )
        )

    return tuple(actions)


def battle_action_mask(battle_state: BattleState) -> tuple[bool, ...]:
    """Compatibility helper: the legacy ten-slot (4 moves + 6 switches) mask.

    Prefer ``available_actions_to_features``; the richer action objects are
    the canonical representation.
    """
    move_mask = [False, False, False, False]

    if not battle_state.force_switch:
        for i, move in enumerate(battle_state.available_moves):
            move_mask[i] = not move.disabled

    switch_mask = [False] * 6

    if battle_state.force_switch or battle_state.available_moves:
        for i in range(len(battle_state.team)):
            switch_mask[i] = _switch_is_legal(battle_state, i)

    return tuple(move_mask + switch_mask)


## Private helpers


def _switch_is_legal(battle_state: BattleState, index: int) -> bool:
    pokemon = battle_state.team[index]

    is_active = pokemon.id == battle_state.curr_pokemon

    has_decision = battle_state.force_switch or bool(
        battle_state.available_moves
    )

    trapped = (
        battle_state.active_pokemon.trapped
        or MinorStatus.TRAPPED in battle_state.active_pokemon.status.minor
        or MinorStatus.PARTIALLY_TRAPPED
        in battle_state.active_pokemon.status.minor
    )

    # maybe_trapped is informational only: Showdown still accepts a switch
    # while the active Pokémon is only "maybe" trapped.
    if not has_decision:
        return False

    if not battle_state.force_switch and trapped:
        return False

    return pokemon.curr_hp > 0 and not is_active


__all__ = [
    "ActionFeatures",
    "MoveActionFeatures",
    "SwitchActionFeatures",
    "available_actions_to_features",
    "battle_action_mask",
]
