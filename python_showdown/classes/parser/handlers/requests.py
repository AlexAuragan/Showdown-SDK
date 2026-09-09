"""``|request|`` JSON payload parsing.

Extracted from :mod:`python_showdown.classes.parser.managers.battle` so the
battle manager only routes; all request-shape validation lives here.
"""

import json

from python_showdown.classes.parser.events.battle import (
    DecisionRequestEvent,
    TeamPreviewRequestEvent,
)
from python_showdown.classes.parser.models import (
    ProtocolMessage,
    RequestMove,
    RequestPokemon,
)
from python_showdown.models.pokemon.status import MajorStatus
from python_showdown.utils.serialization import (
    SerializableObject,
    expect_array,
    expect_bool,
    expect_int,
    expect_object,
    expect_optional_int,
    expect_string,
)


def _validate_keys(
    value: SerializableObject,
    *,
    allowed: set[str],
    name: str,
    required: set[str] | None = None,
) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"Unhandled {name} keys: {sorted(unknown)}")

    if required is not None:
        missing = required - set(value)
        if missing:
            raise ValueError(f"Missing {name} keys: {sorted(missing)}")


def parse_request_event(
    message: ProtocolMessage,
    *,
    player_id: str,
) -> DecisionRequestEvent | TeamPreviewRequestEvent:
    if message.command != "request":
        raise ValueError(f"Expected request message, got {message.command!r}")

    if not message.arguments:
        raise ValueError("Request message has no JSON payload")

    raw_payload = "|".join(message.arguments)

    try:
        decoded = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON request payload: {raw_payload!r}") from exc

    data = expect_object(decoded, name="request")

    # Team Preview is a different kind of decision request.
    if "teamPreview" in data:
        return _parse_request_team_preview_event(data, player_id=player_id)

    request_keys = {
        "active",
        "forceSwitch",
        "rqid",
        "side",
        "wait",
        "noCancel",
        "update",
    }

    _validate_keys(
        data,
        allowed=request_keys,
        required={"side"},
        name="request",
    )

    update = expect_bool(
        data.get("update", False),
        name="request['update']",
    )

    no_cancel = expect_bool(
        data.get("noCancel", False),
        name="request['noCancel']",
    )

    request_id = expect_int(
        data.get("rqid", 0),
        name="request['rqid']",
    )

    wait = expect_bool(
        data.get("wait", False),
        name="request['wait']",
    )

    raw_force_switch = expect_array(
        data.get("forceSwitch", []),
        name="request['forceSwitch']",
    )

    force_switch = tuple(
        expect_bool(
            value,
            name=f"request['forceSwitch'][{i}]",
        )
        for i, value in enumerate(raw_force_switch)
    )

    moves: list[RequestMove] = []

    trapped = False
    maybe_trapped = False
    maybe_locked = False
    maybe_disabled = False

    if "active" in data:
        active = expect_array(
            data["active"],
            name="request['active']",
        )

        if len(active) != 1:
            raise ValueError(f"Expected exactly one active Pokémon, got {len(active)}")

        active_request = expect_object(
            active[0],
            name="request['active'][0]",
        )

        active_keys = {
            "moves",
            "trapped",
            "maybeTrapped",
            "maybeLocked",
            "maybeDisabled",
        }

        _validate_keys(
            active_request,
            allowed=active_keys,
            required={"moves"},
            name="active request",
        )

        trapped = expect_bool(
            active_request.get("trapped", False),
            name="request['active'][0]['trapped']",
        )

        maybe_trapped = expect_bool(
            active_request.get("maybeTrapped", False),
            name="request['active'][0]['maybeTrapped']",
        )

        maybe_disabled = expect_bool(
            active_request.get("maybeDisabled", False),
            name="request['active'][0]['maybeDisabled']",
        )

        maybe_locked = expect_bool(
            active_request.get("maybeLocked", False),
            name="request['active'][0]['maybeLocked']",
        )

        raw_moves = expect_array(
            active_request["moves"],
            name="request['active'][0]['moves']",
        )

        move_keys = {
            "move",
            "id",
            "pp",
            "maxpp",
            "target",
            "disabled",
            "disabledSource",
        }

        for i, raw_move_value in enumerate(raw_moves):
            raw_move = expect_object(
                raw_move_value,
                name=f"request['active'][0]['moves'][{i}]",
            )

            _validate_keys(
                raw_move,
                allowed=move_keys,
                required={"move", "id"},
                name="move",
            )

            name = expect_string(
                raw_move["move"],
                name=f"move[{i}]['move']",
            )

            move_id = expect_string(
                raw_move["id"],
                name=f"move[{i}]['id']",
            )

            target = expect_string(
                raw_move.get("target", "normal"),
                name=f"move[{i}]['target']",
            )

            raw_disabled_source = raw_move.get(
                "disabledSource",
                "",
            )

            disabled_source = (
                expect_string(
                    raw_disabled_source,
                    name=f"move[{i}]['disabledSource']",
                )
                or None
            )

            required_move_state = {
                "pp",
                "maxpp",
                "disabled",
            }

            missing_move_state = required_move_state - set(raw_move)

            is_abbreviated_locked_move = (
                len(raw_moves) == 1 and missing_move_state == required_move_state
            )

            if is_abbreviated_locked_move or move_id in {"recharge", "struggle"}:
                curr_pp_value = raw_move.get("pp")
                max_pp_value = raw_move.get("maxpp")
                disabled_value = raw_move.get("disabled", False)

                curr_pp = (
                    None
                    if curr_pp_value is None
                    else expect_int(
                        curr_pp_value,
                        name=f"move[{i}]['pp']",
                    )
                )

                max_pp = expect_optional_int(
                    max_pp_value,
                    name=f"move[{i}]['maxpp']",
                )

                disabled = expect_bool(
                    disabled_value,
                    name=f"move[{i}]['disabled']",
                )

                if move_id in {"recharge", "struggle"}:
                    target = None

            else:
                if missing_move_state:
                    raise ValueError(
                        "Move is missing required keys: "
                        + f"{sorted(missing_move_state)}, move: {raw_move}"
                    )

                curr_pp = expect_int(
                    raw_move["pp"],
                    name=f"move[{i}]['pp']",
                )

                max_pp = expect_int(
                    raw_move["maxpp"],
                    name=f"move[{i}]['maxpp']",
                )

                disabled = expect_bool(
                    raw_move["disabled"],
                    name=f"move[{i}]['disabled']",
                )

            moves.append(
                RequestMove(
                    name=name,
                    id=move_id,
                    curr_pp=curr_pp,
                    max_pp=max_pp,
                    target=target,
                    disabled=disabled,
                    disabled_source=disabled_source,
                )
            )

    pokemon = _parse_request_pokemon(data, player_id=player_id)

    return DecisionRequestEvent(
        player_id=player_id,
        request_id=request_id,
        wait=wait,
        trapped=trapped,
        maybe_trapped=maybe_trapped,
        maybe_locked=maybe_locked,
        maybe_disabled=maybe_disabled,
        force_switch=force_switch,
        update=update,
        moves=tuple(moves),
        pokemon=pokemon,
        no_cancel=no_cancel,
    )


def _parse_request_pokemon(
    data: SerializableObject,
    *,
    player_id: str,
) -> tuple[RequestPokemon, ...]:
    side = expect_object(data["side"], name="request['side']")

    side_keys = {"id", "name", "pokemon"}
    _validate_keys(
        side,
        allowed=side_keys,
        required=side_keys,
        name="request side",
    )

    side_id = expect_string(side["id"], name="request['side']['id']")

    if side_id != player_id:
        raise ValueError(f"Request player mismatch: {side_id=!r}, {player_id=!r}")

    raw_pokemon = expect_array(
        side["pokemon"],
        name="request['side']['pokemon']",
    )

    if len(raw_pokemon) > 6:
        raise ValueError(
            "Malformed request: expected at most 6 Pokémon, "
            + f"got {len(raw_pokemon)}"
        )

    pokemon: list[RequestPokemon] = []

    pokemon_keys = {
        "condition",
        "ident",
        "stats",
        "details",
        "active",
        "moves",
        "item",
        "pokeball",
        "baseAbility",
    }

    stats_keys = {"atk", "def", "spa", "spd", "spe"}

    for i, raw_value in enumerate(raw_pokemon):
        raw = expect_object(
            raw_value,
            name=f"request['side']['pokemon'][{i}]",
        )

        if set(raw) != pokemon_keys:
            missing = pokemon_keys - set(raw)
            unknown = set(raw) - pokemon_keys
            raise ValueError(
                "Unexpected Pokémon schema: "
                + f"missing={sorted(missing)}, "
                + f"unknown={sorted(unknown)}, "
                + f"pokemon={raw}"
            )

        stats = expect_object(
            raw["stats"],
            name=f"request['side']['pokemon'][{i}]['stats']",
        )

        if set(stats) != stats_keys:
            raise ValueError(f"Unexpected stats schema: {stats}")

        atk = expect_int(stats["atk"], name=f"pokemon[{i}].stats.atk")
        def_ = expect_int(stats["def"], name=f"pokemon[{i}].stats.def")
        spa = expect_int(stats["spa"], name=f"pokemon[{i}].stats.spa")
        spd = expect_int(stats["spd"], name=f"pokemon[{i}].stats.spd")
        spe = expect_int(stats["spe"], name=f"pokemon[{i}].stats.spe")

        condition = expect_string(
            raw["condition"],
            name=f"request['side']['pokemon'][{i}]['condition']",
        )

        if condition == "0 fnt":
            curr_hp = 0
            max_hp = None
            major_status = MajorStatus.FAINT
        else:
            try:
                curr_str, rest = condition.split("/", 1)
                curr_hp = int(curr_str)

                if " " in rest:
                    max_str, major_status_str = rest.split(" ", 1)
                    max_hp = int(max_str)
                    major_status = MajorStatus(major_status_str)
                else:
                    max_hp = int(rest)
                    major_status = None
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Invalid Pokémon condition: {condition!r}") from exc

        details = expect_string(
            raw["details"],
            name=f"request['side']['pokemon'][{i}]['details']",
        )

        clean_details = (
            details.replace(", shiny", "").replace(", M", "").replace(", F", "")
        )

        if ", L" in clean_details:
            try:
                level = int(clean_details.split(", L", 1)[1])
            except ValueError as exc:
                raise ValueError(
                    f"Invalid Pokémon level in details: {details!r}"
                ) from exc
        else:
            level = 100

        raw_pokemon_moves = expect_array(
            raw["moves"],
            name=f"request['side']['pokemon'][{i}]['moves']",
        )

        pokemon_moves = tuple(
            expect_string(
                move,
                name=f"request['side']['pokemon'][{i}]['moves'][{j}]",
            )
            for j, move in enumerate(raw_pokemon_moves)
        )

        ident = expect_string(
            raw["ident"],
            name=f"request['side']['pokemon'][{i}]['ident']",
        )

        active = expect_bool(
            raw["active"],
            name=f"request['side']['pokemon'][{i}]['active']",
        )

        base_ability = expect_string(
            raw["baseAbility"],
            name=f"request['side']['pokemon'][{i}]['baseAbility']",
        )

        item = expect_string(
            raw["item"],
            name=f"request['side']['pokemon'][{i}]['item']",
        )

        pokeball = expect_string(
            raw["pokeball"],
            name=f"request['side']['pokemon'][{i}]['pokeball']",
        )

        pokemon.append(
            RequestPokemon(
                ident=ident,
                details=details,
                level=level,
                active=active,
                atk=atk,
                def_=def_,
                spa=spa,
                spd=spd,
                spe=spe,
                moves=pokemon_moves,
                base_ability=base_ability,
                item=item,
                pokeball=pokeball,
                curr_hp=curr_hp,
                max_hp=max_hp,
                major_status=major_status,
            )
        )

    if sum(p.active for p in pokemon) > 1:
        raise ValueError("Request contains more than one active Pokémon")

    return tuple(pokemon)


def _parse_request_team_preview_event(
    data: SerializableObject,
    *,
    player_id: str,
) -> TeamPreviewRequestEvent:
    request_keys = {
        "teamPreview",
        "maxChosenTeamSize",
        "rqid",
        "side",
        "noCancel",
    }

    _validate_keys(
        data,
        allowed=request_keys,
        required={"teamPreview", "side"},
        name="team preview request",
    )

    team_preview = expect_bool(
        data["teamPreview"],
        name="request['teamPreview']",
    )

    if not team_preview:
        raise ValueError("Team preview request has teamPreview=false")

    raw_request_id = data.get("rqid")
    request_id = expect_optional_int(raw_request_id, name="request['rqid']")

    raw_max_chosen_team_size = data.get("maxChosenTeamSize")
    max_chosen_team_size = expect_optional_int(
        raw_max_chosen_team_size,
        name="request['maxChosenTeamSize']",
    )

    no_cancel = expect_bool(
        data.get("noCancel", False),
        name="request['noCancel']",
    )

    pokemon = _parse_request_pokemon(data, player_id=player_id)

    if max_chosen_team_size is not None and max_chosen_team_size > len(pokemon):
        raise ValueError(
            "maxChosenTeamSize cannot exceed the number of Pokémon: "
            + f"{max_chosen_team_size=} {len(pokemon)=}"
        )

    return TeamPreviewRequestEvent(
        player_id=player_id,
        request_id=request_id,
        pokemon=pokemon,
        max_chosen_team_size=max_chosen_team_size,
        no_cancel=no_cancel,
    )
