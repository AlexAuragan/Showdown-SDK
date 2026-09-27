import asyncio
from typing import cast, override

from showdown_sdk.classes.combat_handler.base_handler import (
    AsyncBaseCombatHandler,
    BaseCombatHandler,
)
from showdown_sdk.classes.combat_handler.utils import (
    Action,
    move_entries,
    request_move_data,
    require_singles,
    switch_entries,
)
from showdown_sdk.exceptions import CombatHandlerError
from showdown_sdk.features.battle import battle_to_features
from showdown_sdk.models.sdk import BattleState
from showdown_sdk.utils import SerializableObject


class MaxBasePowerCombatHandler(BaseCombatHandler):
    """SSDK equivalent of poke-env's MaxBasePowerPlayer for singles."""

    @override
    def select_top_actions(self, battle_state: BattleState) -> list[Action]:
        features = battle_to_features(battle_state)
        gen = require_singles(features)

        ranked: list[tuple[tuple[float, ...], Action]] = []

        for order, (_, move) in enumerate(move_entries(features)):
            move_data: SerializableObject = request_move_data(
                battle_state, move, gen
            )
            raw_base = move_data.get("basePower", 0)
            base_power = float(cast("int | float | None", raw_base) or 0.0)
            ranked.append(
                (
                    (2.0, base_power, -float(order)),
                    ("move", move.request_index + 1),
                )
            )

        for order, (_, switch) in enumerate(switch_entries(features)):
            ranked.append(
                (
                    (1.0, -float(switch.team_slot), -float(order)),
                    ("switch", switch.team_slot + 1),
                )
            )

        if not ranked:
            raise CombatHandlerError("No legal actions available")

        ranked.sort(key=lambda item: item[0], reverse=True)
        return [action for _, action in ranked]

    @classmethod
    @override
    def select_team_order(cls) -> list[int]:
        return [1, 2, 3, 4, 5, 6]


class AsyncMaxBasePowerCombatHandler(
    MaxBasePowerCombatHandler, AsyncBaseCombatHandler
):
    """Async variant of :class:`MaxBasePowerCombatHandler`.

    Reuses the synchronous decision logic but exposes the async combat
    handler interface, running the work off the event loop.
    """

    @override
    async def async_select_top_actions(
        self, battle_state: BattleState
    ) -> list[Action]:
        return await asyncio.to_thread(self.select_top_actions, battle_state)

    @override
    @staticmethod
    async def async_select_team_order() -> list[int]:
        return [1, 2, 3, 4, 5, 6]
