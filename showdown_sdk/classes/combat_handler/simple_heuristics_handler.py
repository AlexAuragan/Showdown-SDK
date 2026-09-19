from typing import cast, override

from showdown_sdk.classes.combat_handler.base_handler import BaseCombatHandler
from showdown_sdk.classes.combat_handler.utils import (
    Action,
    PokemonFeatures,
    SwitchEntry,
    accuracy,
    active_enemy,
    active_own,
    base_stat,
    expected_hits,
    fallback_legal_actions,
    hp_ratio,
    move_entries,
    pokemon_types,
    require_singles,
    resolved_request_move,
    stage,
    switch_entries,
    type_multiplier,
)
from showdown_sdk.exceptions import CombatHandlerError
from showdown_sdk.features import (
    EnemyPokemonFeatures,
    OwnPokemonFeatures,
    battle_to_features,
)
from showdown_sdk.models import to_id
from showdown_sdk.models.sdk import BattleState
from showdown_sdk.utils import SerializableObject


class SimpleHeuristicsCombatHandler(BaseCombatHandler):
    """
    Port of poke-env's SimpleHeuristicsPlayer.
    Mostly AI Generated
    """

    ENTRY_HAZARDS: frozenset[str] = frozenset(
        {"spikes", "stealthrock", "toxicspikes"}
    )
    ANTI_HAZARDS_MOVES: frozenset[str] = frozenset({"rapidspin", "defog"})

    SPEED_TIER_COEFFICIENT: float = 0.1
    HP_FRACTION_COEFFICIENT: float = 0.4
    SWITCH_OUT_MATCHUP_THRESHOLD: float = -2.0

    @override
    def select_top_actions(self, battle_state: BattleState) -> list[Action]:
        features = battle_to_features(battle_state)
        gen = require_singles(features)

        own_active = active_own(features)
        enemy_active = active_enemy(features)

        if own_active is None or enemy_active is None:
            return fallback_legal_actions(features)

        moves = move_entries(features)
        switches = switch_entries(features)

        switch_matchups = {
            switch.team_slot: self._estimate_matchup(
                features.own_team[switch.team_slot], enemy_active, gen
            )
            for _, switch in switches
        }

        if features.force_switch:
            if not switches:
                raise CombatHandlerError("Forced switch with no legal switch")
            return self._rank_switches(switches, switch_matchups)

        should_switch = self._should_switch_out(
            own_active, enemy_active, switches, switch_matchups, gen
        )

        physical_ratio = self._stat_estimation(own_active, "atk", gen) / max(
            self._stat_estimation(enemy_active, "def", gen), 1e-12
        )

        special_ratio = self._stat_estimation(own_active, "spa", gen) / max(
            self._stat_estimation(enemy_active, "spd", gen), 1e-12
        )

        own_types = pokemon_types(own_active, gen)
        enemy_types = pokemon_types(enemy_active, gen)

        n_remaining_mons = sum(
            1 for mon in features.own_team if mon.present and not mon.fainted
        )
        n_opp_remaining_mons = 6 - sum(
            1 for mon in features.enemy_team if mon.revealed and mon.fainted
        )

        own_side_conditions = {
            to_id(condition.name)
            for condition in features.field.own_side_conditions
        }
        enemy_side_conditions = {
            to_id(condition.name)
            for condition in features.field.enemy_side_conditions
        }

        ranked: list[tuple[tuple[float, ...], Action]] = []

        tactical_move_slots: set[int] = set()
        setup_move_slots: set[int] = set()

        if not should_switch:
            for order, (_, move) in enumerate(moves):
                move_id, move_data = resolved_request_move(
                    battle_state, move, gen
                )

                sets_hazard = (
                    n_opp_remaining_mons >= 3
                    and move_id in self.ENTRY_HAZARDS
                    and move_id not in enemy_side_conditions
                )

                removes_hazards = (
                    bool(own_side_conditions)
                    and move_id in self.ANTI_HAZARDS_MOVES
                    and n_remaining_mons >= 2
                )

                if sets_hazard or removes_hazards:
                    tactical_move_slots.add(move.request_index)

            for order, (_, move) in enumerate(moves):
                move_id, move_data = resolved_request_move(
                    battle_state, move, gen
                )
                if self._is_desirable_setup(
                    move_data, own_active, enemy_active, gen
                ):
                    setup_move_slots.add(move.request_index)

        for order, (_, move) in enumerate(moves):
            move_id, move_data = resolved_request_move(battle_state, move, gen)

            attack_score = self._attack_score(
                move_id,
                move_data,
                own_types=own_types,
                enemy_types=enemy_types,
                physical_ratio=physical_ratio,
                special_ratio=special_ratio,
                gen=gen,
            )

            if should_switch:
                tier = 10.0
                score = attack_score
            elif move.request_index in tactical_move_slots:
                tier = 50.0
                score = -float(order)
            elif move.request_index in setup_move_slots:
                tier = 40.0
                score = -float(order)
            else:
                tier = 30.0
                score = attack_score

            ranked.append(
                ((tier, score, -float(order)), ("move", move.request_index + 1))
            )

        for order, (_, switch) in enumerate(switches):
            matchup = switch_matchups.get(switch.team_slot, float("-inf"))
            # poke-env only chooses a voluntary switch ahead of moves when
            # _should_switch_out() is true.
            tier = 60.0 if should_switch else 20.0
            ranked.append(
                (
                    (tier, matchup, -float(order)),
                    ("switch", switch.team_slot + 1),
                )
            )

        if not ranked:
            raise CombatHandlerError("No legal actions available")

        ranked.sort(key=lambda item: item[0], reverse=True)
        return [action for _, action in ranked]

    def _rank_switches(
        self, switches: list[SwitchEntry], switch_matchups: dict[int, float]
    ) -> list[Action]:
        ranked = sorted(
            switches,
            key=lambda entry: (
                switch_matchups.get(entry[1].team_slot, float("-inf")),
                -entry[1].team_slot,
            ),
            reverse=True,
        )
        return [("switch", switch.team_slot + 1) for _, switch in ranked]

    def _should_switch_out(
        self,
        active: OwnPokemonFeatures,
        opponent: EnemyPokemonFeatures,
        switches: list[SwitchEntry],
        switch_matchups: dict[int, float],
        gen: int,
    ) -> bool:
        if not switches:
            return False

        # Same gate as poke-env: only switch voluntarily if at least one
        # available switch has a positive estimated matchup.
        if not any(score > 0 for score in switch_matchups.values()):
            return False

        status = active.status

        if status.defense_stage <= -3 or status.special_defense_stage <= -3:
            return True

        attack = active.stats.attack if active.stats is not None else None
        special_attack = (
            active.stats.special_attack if active.stats is not None else None
        )

        if (
            status.attack_stage <= -3
            and attack is not None
            and special_attack is not None
            and attack >= special_attack
        ):
            return True

        if (
            status.special_attack_stage <= -3
            and attack is not None
            and special_attack is not None
            and attack <= special_attack
        ):
            return True

        return (
            self._estimate_matchup(active, opponent, gen)
            < self.SWITCH_OUT_MATCHUP_THRESHOLD
        )

    def _estimate_matchup(
        self, mon: PokemonFeatures, opponent: PokemonFeatures, gen: int
    ) -> float:
        mon_types = pokemon_types(mon, gen)
        opponent_types = pokemon_types(opponent, gen)

        offense = max(
            (type_multiplier(t, opponent_types, gen) for t in mon_types),
            default=1.0,
        )

        defense = max(
            (type_multiplier(t, mon_types, gen) for t in opponent_types),
            default=1.0,
        )

        score = offense - defense

        mon_speed = base_stat(mon, "spe", gen)
        opponent_speed = base_stat(opponent, "spe", gen)

        if mon_speed > opponent_speed:
            score += self.SPEED_TIER_COEFFICIENT
        elif opponent_speed > mon_speed:
            score -= self.SPEED_TIER_COEFFICIENT

        score += hp_ratio(mon) * self.HP_FRACTION_COEFFICIENT
        score -= hp_ratio(opponent) * self.HP_FRACTION_COEFFICIENT

        return score

    def _stat_estimation(
        self, mon: PokemonFeatures, stat: str, gen: int
    ) -> float:
        """Mirror poke-env's SimpleHeuristicsPlayer._stat_estimation.

        The formula is intentionally behavior-compatible with poke-env,
        including its treatment of a +1 stage.
        """
        stg = stage(mon.status, stat)

        if stg > 1:
            boost = (2 + stg) / 2
        else:
            boost = 2 / (2 - stg)

        base = base_stat(mon, stat, gen)
        return ((2 * base + 31) + 5) * boost

    def _is_desirable_setup(
        self,
        move_data: SerializableObject,
        active: OwnPokemonFeatures,
        opponent: EnemyPokemonFeatures,
        gen: int,
    ) -> bool:
        if hp_ratio(active) != 1.0:
            return False

        if self._estimate_matchup(active, opponent, gen) <= 0:
            return False

        if to_id(cast("str", move_data.get("target", ""))) != "self":
            return False

        raw_boosts = move_data.get("boosts")
        if not isinstance(raw_boosts, dict) or not raw_boosts:
            return False

        boosts: dict[str, int] = {}
        for key, value in raw_boosts.items():
            if isinstance(value, int) and not isinstance(value, bool):
                boosts[to_id(key)] = value

        if sum(boosts.values()) < 2:
            return False

        positive_stats = [stat for stat, amount in boosts.items() if amount > 0]

        if not positive_stats:
            return False

        return min(stage(active.status, stat) for stat in positive_stats) < 6

    def _attack_score(
        self,
        move_id: str,
        move_data: SerializableObject,
        *,
        own_types: list[str],
        enemy_types: list[str],
        physical_ratio: float,
        special_ratio: float,
        gen: int,
    ) -> float:
        base_power = _number(move_data.get("basePower"), default=0.0)
        move_type = cast("str", move_data.get("type", ""))
        category = cast("str", move_data.get("category", ""))

        stab = (
            1.5
            if any(
                to_id(move_type) == to_id(own_type) for own_type in own_types
            )
            else 1.0
        )

        ratio = (
            physical_ratio if to_id(category) == "physical" else special_ratio
        )

        return (
            base_power
            * stab
            * ratio
            * accuracy(move_data.get("accuracy"))
            * expected_hits(move_id, move_data.get("multihit"))
            * type_multiplier(move_type, enemy_types, gen)
        )

    @classmethod
    @override
    def select_team_order(cls) -> list[int]:
        return [1, 2, 3, 4, 5, 6]


def _number(value: object, *, default: float) -> float:
    """Extract a number, defaulting if absent or not numeric."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default
