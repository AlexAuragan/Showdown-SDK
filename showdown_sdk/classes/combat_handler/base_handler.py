from typing import Protocol

from showdown_sdk.models.sdk import BattleState


class BaseCombatHandler(Protocol):
    """A stateless AI policy: it never owns battle state, it only decides."""

    def select_top_actions(
        self, battle_state: BattleState
    ) -> list[tuple[str, int]]:
        # Rank every legal action best-first. When Showdown rejects the
        # top-ranked choice, the client sends the next ranked one against the
        # same request id instead of re-consulting the policy.
        ...

    @staticmethod
    def select_team_order() -> list[int]: ...
