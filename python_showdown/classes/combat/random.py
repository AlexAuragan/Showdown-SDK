from random import choice, random

from python_showdown.classes.combat.battle_state import BattleState


class RandomMoveCombatHandler:
    """A stateless AI policy: it never owns battle state, it only decides.
    """

    def __init__(self, switch_chance: float = 0.1) -> None:
        self.switch_chance = switch_chance

    def select_action(self, battle_state: BattleState) -> tuple[str, int]:
        team = battle_state.team
        switch_candidates = [
            pkmn for pkmn in team
            if pkmn.curr_hp > 0 and not pkmn.active
        ]
        if battle_state.force_switch and not switch_candidates:
            raise RuntimeError(
                f"No switch targets available in team: {team!r}"
            )
        if (battle_state.force_switch or random() <= self.switch_chance) and switch_candidates:
            chosen = choice(switch_candidates)
            # Use the party slot directly (1-indexed), matched by object
            # identity so stale or duplicate ids can't shift the index.
            slot = next(i for i, p in enumerate(team, start=1) if p is chosen)
            return "switch", slot

        moves = battle_state.available_moves
        usable = [move for move in moves if not move.disabled]
        if not usable:
            raise RuntimeError(f"No usable moves available: {moves!r}")
        chosen = choice(usable)
        slot = next(i for i, m in enumerate(moves, start=1) if m is chosen)
        return "move", slot
