from python_showdown.classes.parser.events import BaseEvent
from python_showdown.classes.parser.reducers import reduce_battle_state
from python_showdown.models.sdk.battle_state import BattleState


class BattleStateHandler:
    """Applies semantic events onto a BattleState.

    The handler exposes two complementary entry points so the parser can
    either rebuild a fresh battle state from a whole event list, or apply
    individual events onto an existing state incrementally.
    """

    def __init__(self, player_id: str = "") -> None:
        self.player_id: str = player_id

    def apply_events(self, events: list[BaseEvent]) -> BattleState:
        """Build a brand new BattleState with `events` applied in order."""
        battle_state = BattleState()
        battle_state.player_id = self.player_id
        for event in events:
            self.apply_event(battle_state, event)
        return battle_state

    @staticmethod
    def apply_event(battle_state: BattleState, event: BaseEvent) -> None:
        """Apply a single event onto an existing BattleState."""
        reduce_battle_state(battle_state, event)
