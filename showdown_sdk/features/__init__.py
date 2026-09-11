"""Stable, ML-friendly, JSON-serializable feature layer.

Boundary:

    BattleState / Pokemon / BaseEvent
            |
        to_features()
            |
    typed, JSON-serializable feature objects
            |
    ----------------------------------
    AI project
        vocabulary -> integer IDs
        tensors / embeddings / batching
        model / RL

``to_features()`` describes the information; the AI project decides how to
turn it into tensors. This layer never imports a numerical library,
truncates or pads sequences, and never assigns arbitrary categorical IDs.
"""

from showdown_sdk.features.actions import (
    ActionFeatures,
    MoveActionFeatures,
    SwitchActionFeatures,
    available_actions_to_features,
    battle_action_mask,
)
from showdown_sdk.features.battle import (
    FEATURE_SCHEMA_VERSION,
    BattleFeatures,
    BattleFormatFeatures,
    FieldFeatures,
    SideConditionFeatures,
    battle_to_features,
    features_to_dict,
)
from showdown_sdk.features.common import (
    JSONScalar,
    Knowledge,
    PokemonRefFeatures,
    StatFeatures,
    StatusFeatures,
    canonical,
    canonical_move,
)
from showdown_sdk.features.events import EventFeatures, history_to_features
from showdown_sdk.features.pokemon import (
    EnemyPokemonFeatures,
    OwnMoveFeatures,
    OwnPokemonFeatures,
    enemy_team_to_features,
    own_team_to_features,
)

__all__ = [
    "FEATURE_SCHEMA_VERSION",
    "ActionFeatures",
    "BattleFeatures",
    "BattleFormatFeatures",
    "EnemyPokemonFeatures",
    "EventFeatures",
    "FieldFeatures",
    "JSONScalar",
    "Knowledge",
    "MoveActionFeatures",
    "OwnMoveFeatures",
    "OwnPokemonFeatures",
    "PokemonRefFeatures",
    "SideConditionFeatures",
    "StatFeatures",
    "StatusFeatures",
    "SwitchActionFeatures",
    "available_actions_to_features",
    "battle_action_mask",
    "battle_to_features",
    "canonical",
    "canonical_move",
    "enemy_team_to_features",
    "features_to_dict",
    "history_to_features",
    "own_team_to_features",
]
