from dataclasses import dataclass

from showdown_sdk.features.common import ParsedMoveName, canonical
from showdown_sdk.models import dex
from showdown_sdk.utils import expect_object


@dataclass(frozen=True)
class MoveMechanicsFeatures:
    move_type: str | None = None
    category: str | None = None
    base_power: int | None = None
    accuracy: float | None = None
    always_hits: bool = False
    priority: int = 0


def move_mechanics_to_features(
    move: ParsedMoveName, *, gen: int
) -> MoveMechanicsFeatures:
    if move.id in {"fight", "recharge"}:
            return MoveMechanicsFeatures()
    raw = expect_object(dex.gen(gen).move(move.id), name=f"move {move.id!r}")

    raw_type = raw.get("type")
    raw_category = raw.get("category")
    raw_power = raw.get("basePower")
    raw_accuracy = raw.get("accuracy")
    raw_priority = raw.get("priority")

    move_type = (
        move.hidden_power_type
        if move.hidden_power_type is not None
        else canonical(raw_type)
        if isinstance(raw_type, str)
        else None
    )

    base_power = (
        move.encoded_power
        if move.encoded_power is not None
        else raw_power
        if isinstance(raw_power, int) and not isinstance(raw_power, bool)
        else None
    )

    always_hits = raw_accuracy is True

    accuracy = (
        float(raw_accuracy)
        if isinstance(raw_accuracy, (int, float))
        and not isinstance(raw_accuracy, bool)
        else None
    )

    priority = (
        raw_priority
        if isinstance(raw_priority, int) and not isinstance(raw_priority, bool)
        else 0
    )

    return MoveMechanicsFeatures(
        move_type=move_type,
        category=canonical(raw_category)
        if isinstance(raw_category, str)
        else None,
        base_power=base_power,
        accuracy=accuracy,
        always_hits=always_hits,
        priority=priority,
    )
