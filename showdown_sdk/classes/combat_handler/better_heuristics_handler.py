import asyncio
from typing import cast, override

from showdown_sdk.classes.combat_handler.base_handler import (
    AsyncBaseCombatHandler,
)
from showdown_sdk.classes.combat_handler.simple_heuristics_handler import (
    SimpleHeuristicsCombatHandler,
)
from showdown_sdk.classes.combat_handler.utils import (
    Action,
    active_enemy,
    active_own,
    hp_ratio,
    move_entries,
    require_singles,
    resolved_request_move,
    stage,
    switch_entries,
)
from showdown_sdk.features import (
    BattleFeatures,
    EnemyPokemonFeatures,
    OwnPokemonFeatures,
    battle_to_features,
)
from showdown_sdk.models import to_id
from showdown_sdk.models.sdk.battle_state import BattleState
from showdown_sdk.utils import SerializableObject


class BetterHeuristicsCombatHandler(SimpleHeuristicsCombatHandler):
    """
    SimpleHeuristics with conservative improvements for obviously wasteful
    actions.

    SimpleHeuristicsCombatHandler remains behavior-compatible with poke-env.
    """

    ENTRY_HAZARDS: frozenset[str] = frozenset(
        {"spikes", "stealthrock", "toxicspikes", "stickyweb"}
    )

    @override
    def select_top_actions(self, battle_state: BattleState) -> list[Action]:
        ranked = super().select_top_actions(battle_state)

        features = battle_to_features(battle_state)
        gen = require_singles(features)

        own_active = active_own(features)
        enemy_active = active_enemy(features)

        if own_active is None or enemy_active is None or features.force_switch:
            return ranked

        moves = move_entries(features)
        move_by_number = {move.request_index + 1: move for _, move in moves}

        switches = switch_entries(features)
        switch_matchups = {
            switch.team_slot: self._estimate_matchup(
                features.own_team[switch.team_slot], enemy_active, gen
            )
            for _, switch in switches
        }

        should_switch = self._should_switch_out(
            own_active, enemy_active, switches, switch_matchups, gen
        )

        preferred: list[Action] = []
        normal: list[Action] = []
        wasteful: list[Action] = []

        for action in ranked:
            action_type, action_number = action

            if action_type != "move":
                normal.append(action)
                continue

            move = move_by_number.get(action_number)

            if move is None:
                normal.append(action)
                continue

            move_id, move_data = resolved_request_move(battle_state, move, gen)

            if self._is_wasteful_move(
                move_data, own_active=own_active, enemy_active=enemy_active
            ):
                wasteful.append(action)
                continue

            if not should_switch and self._is_desirable_hazard(
                move_id, features, gen
            ):
                preferred.append(action)
                continue

            normal.append(action)

        return preferred + normal + wasteful

    def _is_wasteful_move(
        self,
        move_data: SerializableObject,
        *,
        own_active: OwnPokemonFeatures,
        enemy_active: EnemyPokemonFeatures,
    ) -> bool:
        category = to_id(cast("str", move_data.get("category", "")))
        target = to_id(cast("str", move_data.get("target", "")))

        if category != "status":
            return False

        if self._has_redundant_major_status(
            move_data, target=target, enemy_active=enemy_active
        ):
            return True

        if self._has_redundant_stat_drop(
            move_data, target=target, enemy_active=enemy_active
        ):
            return True

        return self._has_redundant_heal(
            move_data, target=target, own_active=own_active
        )

    def _has_redundant_major_status(
        self,
        move_data: SerializableObject,
        *,
        target: str,
        enemy_active: EnemyPokemonFeatures,
    ) -> bool:
        if target == "self":
            return False

        inflicted_status = move_data.get("status")

        return (
            isinstance(inflicted_status, str)
            and enemy_active.status.major is not None
        )

    def _has_redundant_stat_drop(
        self,
        move_data: SerializableObject,
        *,
        target: str,
        enemy_active: EnemyPokemonFeatures,
    ) -> bool:
        if target == "self":
            return False

        raw_boosts = move_data.get("boosts")

        if not isinstance(raw_boosts, dict):
            return False

        negative_boosts: list[str] = []

        for stat, amount in raw_boosts.items():
            if (
                isinstance(amount, int)
                and not isinstance(amount, bool)
                and amount < 0
            ):
                negative_boosts.append(to_id(stat))

        if not negative_boosts:
            return False

        return all(
            stage(enemy_active.status, stat) <= -6 for stat in negative_boosts
        )

    def _has_redundant_heal(
        self,
        move_data: SerializableObject,
        *,
        target: str,
        own_active: OwnPokemonFeatures,
    ) -> bool:
        if target != "self":
            return False

        raw_heal = move_data.get("heal")

        if not isinstance(raw_heal, list):
            return False

        if hp_ratio(own_active) < 1.0:
            return False

        return own_active.status.major is None

    def _is_desirable_hazard(
        self, move_id: str, features: BattleFeatures, gen: int
    ) -> bool:
        if move_id not in self.ENTRY_HAZARDS:
            return False

        n_opp_remaining_mons = 6 - sum(
            1 for mon in features.enemy_team if mon.revealed and mon.fainted
        )

        if n_opp_remaining_mons < 3:
            return False

        current_layers = {
            to_id(condition.name): condition.value
            for condition in features.field.enemy_side_conditions
        }

        existing = current_layers.get(move_id, 0)

        match move_id:
            case "spikes":
                max_layers = 1 if gen == 2 else 3
            case "toxicspikes":
                max_layers = 2
            case "stealthrock" | "stickyweb":
                max_layers = 1
            case _:
                return False

        return existing < max_layers


class AsyncBetterHeuristicsCombatHandler(
    BetterHeuristicsCombatHandler, AsyncBaseCombatHandler
):
    """Async variant of BetterHeuristics."""

    @override
    async def async_select_top_actions(
        self, battle_state: BattleState
    ) -> list[Action]:
        return await asyncio.to_thread(self.select_top_actions, battle_state)

    @override
    @staticmethod
    async def async_select_team_order() -> list[int]:
        return [1, 2, 3, 4, 5, 6]
