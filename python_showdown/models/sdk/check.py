from python_showdown.models.sdk.battle_state import BattleState


def check_battle_state_against_showdown(battle_state: BattleState):
    if battle_state.custom_showdown_battlestate is None:
        return
