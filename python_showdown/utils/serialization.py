from dataclasses import asdict, fields, is_dataclass
from enum import Enum
from typing import TypeGuard, cast

type SerializableScalar = str | int | float | bool | None
type Serializable = (
    SerializableScalar
    | list[Serializable]
    | dict[str, Serializable]
)
type SerializableObject = dict[str, Serializable]
type SerializableArray = list[Serializable]


def is_serializable(value: object) -> TypeGuard[Serializable]:
    """Return whether *value* is already valid JSON-shaped data.

    This is intentionally stricter than ``json.dumps``:
    mappings must use string keys, and tuples/sets/dataclasses/enums are not
    considered serialized until they pass through :func:`to_serializable`.
    """
    if isinstance(value, Enum):
        return False

    if value is None or isinstance(value, (str, int, float, bool)):
        return True

    if isinstance(value, list):
        values = cast(list[object], value)
        return all(is_serializable(item) for item in values)

    if isinstance(value, dict):
        values = cast(dict[object, object], value)
        return all(
            isinstance(key, str) and is_serializable(item)
            for key, item in values.items()
        )

    return False


def expect_serializable(value: object, *, name: str = "value") -> Serializable:
    """Validate JSON-shaped data and return it with a precise type."""
    if not is_serializable(value):
        raise TypeError(
            f"{name} must be JSON-serializable data, got {type(value).__name__}"
        )
    return value


def expect_object(value: Serializable, *, name: str = "value") -> SerializableObject:
    """Validate a JSON object (``dict[str, Serializable]``)."""
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be an object, got {type(value).__name__}")
    return value


def expect_array(value: Serializable, *, name: str = "value") -> SerializableArray:
    """Validate a JSON array (``list[Serializable]``)."""
    if not isinstance(value, list):
        raise TypeError(f"{name} must be an array, got {type(value).__name__}")
    return value


def expect_string(value: object, *, name: str = "value") -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string, got {type(value).__name__}")
    return value


def expect_int(value: object, *, name: str = "value") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    return value


def expect_float(value: object, *, name: str = "value") -> float:
    if not isinstance(value, float):
        raise TypeError(f"{name} must be a float, got {type(value).__name__}")
    return value


def expect_number(value: object, *, name: str = "value") -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}")
    return value


def expect_bool(value: object, *, name: str = "value") -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a bool, got {type(value).__name__}")
    return value


def expect_optional_string(value: object, *, name: str = "value") -> str | None:
    if value is None:
        return None
    return expect_string(value, name=name)


def expect_optional_int(value: object, *, name: str = "value") -> int | None:
    if value is None:
        return None
    return expect_int(value, name=name)


def expect_optional_float(value: object, *, name: str = "value") -> float | None:
    if value is None:
        return None
    return expect_float(value, name=name)


def expect_optional_bool(value: object, *, name: str = "value") -> bool | None:
    if value is None:
        return None
    return expect_bool(value, name=name)


def to_serializable(value: object, *, name: str = "value") -> Serializable:
    """Convert supported Python values to strictly JSON-shaped data."""
    if isinstance(value, Enum):
        return to_serializable(value.value, name=name)

    result: SerializableObject = {}
    if is_dataclass(value) and not isinstance(value, type):
        try:
            values = vars(value)
        except TypeError:
            # Slotted dataclasses have no __dict__.
            return to_serializable(asdict(value), name=name)

        for field in fields(value):
            key = field.name

            if key.startswith("_"):
                continue

            result[key] = to_serializable(
                values[key],
                name=f"{name}.{key}",
            )

        return result

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        values = cast(dict[object, object], value)

        for raw_key, item in values.items():
            if isinstance(raw_key, Enum):
                key_value = to_serializable(raw_key.value, name=f"{name} key")
                if not isinstance(key_value, str):
                    raise TypeError(
                        f"{name} enum key must serialize to str, "
                        + f"got {type(key_value).__name__}"
                    )
                key = key_value
            elif isinstance(raw_key, str):
                key = raw_key
            else:
                raise TypeError(
                    f"{name} keys must be strings, got {type(raw_key).__name__}"
                )

            if key.startswith("_"):
                continue

            result[key] = to_serializable(item, name=f"{name}.{key}")

        return result

    if isinstance(value, list):
        values = cast(list[object], value)
        return [
            to_serializable(item, name=f"{name}[{index}]")
            for index, item in enumerate(values)
        ]

    if isinstance(value, tuple):
        values = cast(tuple[object, ...], value)
        return [
            to_serializable(item, name=f"{name}[{index}]")
            for index, item in enumerate(values)
        ]

    if isinstance(value, (set, frozenset)):
        values = cast(set[object] | frozenset[object], value)
        serialized = [
            to_serializable(item, name=f"{name} item")
            for item in values
        ]
        return sorted(serialized, key=str)

    raise TypeError(f"{name} contains unsupported type {type(value).__name__}")


def to_serializable_object(
    value: object, *, name: str = "value"
) -> SerializableObject:
    """Convert *value* and require the result to be a JSON object."""
    serialized = to_serializable(value, name=name)
    if not isinstance(serialized, dict):
        raise TypeError(
            f"{name} must serialize to an object, got {type(serialized).__name__}"
        )
    return serialized


def to_serializable_array(
    value: object, *, name: str = "value"
) -> SerializableArray:
    """Convert *value* and require the result to be a JSON array."""
    serialized = to_serializable(value, name=name)
    if not isinstance(serialized, list):
        raise TypeError(
            f"{name} must serialize to an array, got {type(serialized).__name__}"
        )
    return serialized
