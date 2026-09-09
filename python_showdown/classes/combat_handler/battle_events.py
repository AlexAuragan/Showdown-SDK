"""Runtime battle event application.

This module applies live battle-manager lifecycle bookkeeping for semantic
battle events: room readiness, request ids, team preview and battle
completion. Deterministic battle knowledge lives in
``python_showdown.classes.parser.reducers.battle.reduce_battle_state``.
"""

from python_showdown.classes.combat_handler.battle_manager import BattleManager
from python_showdown.classes.parser.events.base import BaseEvent
from python_showdown.classes.parser.events.battle import (
    BattleEndEvent,
    BattleEvent,
    BattleStartEvent,
    CustomShowdownBattleStateEvent,
    DecisionRequestEvent,
    PlayerEvent,
    RoomEvent,
    TeamPreviewRequestEvent,
)
from python_showdown.classes.parser.reducers import reduce_battle_state


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


def apply_battle_event(
    manager: BattleManager,
    event: BattleEvent,
) -> None:
    """Apply one semantic battle event in the canonical order."""
    reduce_battle_state(
        manager.battle_state,
        event,
    )

    if not isinstance(event, CustomShowdownBattleStateEvent):
        manager.battle_state.history.append(event)

    apply_battle_runtime_event(
        manager,
        event,
    )


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
