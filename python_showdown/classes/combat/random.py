import random

from python_showdown.classes.combat.battle_state import BattleState
from python_showdown.classes.pokemon.moves import AvailableMove
from python_showdown.classes.pokemon.pokemon import PartyPokemon


class RandomMoveCombatHandler:
    def __init__(self):
        self.request_id: int | None = None
        self.battle_state = BattleState()

    def reset(self) -> None:
        self.battle_state.reset()
        self.request_id = None

    def select_action(self) -> tuple[str, int]:
        if self.battle_state.force_switch:
            team = self.battle_state.team
            candidates = [
                pkmn for pkmn in team
                if pkmn.curr_hp > 0 and not pkmn.active
            ]
            if not candidates:
                raise RuntimeError(
                    f"No switch targets available in team: {team!r}"
                )
            chosen = random.choice(candidates)
            # Use the party slot directly (1-indexed), matched by object
            # identity so stale or duplicate ids can't shift the index.
            slot = next(i for i, p in enumerate(team, start=1) if p is chosen)
            return "switch", slot

        moves = self.battle_state.available_moves
        usable = [move for move in moves if not move.disabled]
        if not usable:
            raise RuntimeError(f"No usable moves available: {moves!r}")
        chosen = random.choice(usable)
        slot = next(i for i, m in enumerate(moves, start=1) if m is chosen)
        return "move", slot

    def update(
        self,
        request_id: int | None,
        available_pokemons: list[PartyPokemon] | None = None,
        available_moves: list[AvailableMove] | None = None,
        force_switch: bool = False,
    ) -> None:
        if available_pokemons is not None:
            self.battle_state.update_team(available_pokemons)
            active = next((pkmn for pkmn in available_pokemons if pkmn.active), None)
            if active is not None:
                self.battle_state.set_active_pokemon(active.id)

        if available_moves is not None:
            self.battle_state.update_moves(available_moves)

        self.battle_state.force_switch = force_switch
        self.request_id = request_id
