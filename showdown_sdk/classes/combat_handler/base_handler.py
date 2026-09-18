import asyncio
from typing import Protocol, override

from showdown_sdk.classes.combat_handler.utils import Action
from showdown_sdk.models.sdk import BattleState


class BaseCombatHandler(Protocol):
    """A stateless AI policy: it never owns battle state, it only decides."""

    def select_top_actions(self, battle_state: BattleState) -> list[Action]:
        ...

    @classmethod
    def select_team_order(cls) -> list[int]:
        ...

class AsyncBaseCombatHandler(BaseCombatHandler, Protocol):
    async def async_select_top_action(self, battle_state: BattleState) -> list[Action]:
        ...

    @staticmethod
    async def async_select_team_order() -> list[int]:
        ...

    @override
    def select_top_actions(self, battle_state: BattleState) -> list[Action]:
        return asyncio.run(self.async_select_top_action(battle_state))

    @override
    @classmethod
    def select_team_order(cls) -> list[int]:
        return asyncio.run(cls.async_select_team_order())
