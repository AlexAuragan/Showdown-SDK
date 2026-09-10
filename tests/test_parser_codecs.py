# pyright: reportPrivateUsage=false
# Acceptable in a test suite

import pytest

from showdown_sdk.classes.parser.fields import (
    parse_condition,
    parse_move_origin,
    parse_pokemon_details,
    parse_pokemon_ident,
)
from showdown_sdk.classes.parser.models import (
    PokemonIdent,
    ProtocolAnnotation,
    ProtocolMessage,
)
from showdown_sdk.models.pokemon.status import MajorStatus, Stat, Status
from showdown_sdk.models.sdk.battle_state import SourceType


def test_parse_pokemon_ident() -> None:
    assert parse_pokemon_ident("p1a: Azumarill") == PokemonIdent(
        player="p1",
        slot="a",
        name="Azumarill",
    )

    assert parse_pokemon_ident("p2: Snorlax") == PokemonIdent(
        player="p2",
        slot=None,
        name="Snorlax",
    )


def test_pokemon_ident_from_str_uses_canonical_parser() -> None:
    value = "p1a: Azumarill"

    assert PokemonIdent.from_str(value) == parse_pokemon_ident(value)


def test_parse_pokemon_ident_rejects_invalid_slot() -> None:
    with pytest.raises(ValueError):
        parse_pokemon_ident("p1z: Azumarill")


def test_parse_condition_with_hp() -> None:
    condition = parse_condition("123/300")

    assert condition.current_hp == 123
    assert condition.max_hp == 300
    assert condition.status is None


def test_parse_condition_with_status() -> None:
    condition = parse_condition("123/300 par")

    assert condition.current_hp == 123
    assert condition.max_hp == 300
    assert condition.status is MajorStatus.PARALYSIS


def test_parse_condition_fainted() -> None:
    condition = parse_condition("0 fnt")

    assert condition.current_hp == 0
    assert condition.max_hp is None
    assert condition.status is MajorStatus.FAINT


def test_parse_pokemon_details_defaults() -> None:
    details = parse_pokemon_details("Snorlax")

    assert details.level == 100
    assert details.gender is None
    assert details.shiny is False


def test_parse_pokemon_details_complete() -> None:
    details = parse_pokemon_details("Azumarill, L83, F, shiny")

    assert details.level == 83
    assert details.gender == "F"
    assert details.shiny is True


def test_parse_pokemon_details_male() -> None:
    details = parse_pokemon_details("Machamp, L74, M")

    assert details.level == 74
    assert details.gender == "M"
    assert details.shiny is False


def test_parse_move_origin_none() -> None:
    message = ProtocolMessage(
        command="move",
        arguments=("p1a: Mew", "Psychic", "p2a: Mewtwo"),
        annotations=(),
        raw="|move|p1a: Mew|Psychic|p2a: Mewtwo",
    )

    assert parse_move_origin(message) is None


def test_parse_move_origin_ability() -> None:
    message = ProtocolMessage(
        command="move",
        arguments=("p1a: Oricorio", "Revelation Dance", "p2a: Pikachu"),
        annotations=(
            ProtocolAnnotation(
                name="from",
                value="ability: Dancer",
            ),
        ),
        raw="",
    )

    source = parse_move_origin(message)

    assert source is not None
    assert source.type is SourceType.ABILITY
    assert source.name == "Dancer"


def test_parse_move_origin_mirror_move() -> None:
    message = ProtocolMessage(
        command="move",
        arguments=("p1a: Pidgeot", "Thunderbolt", "p2a: Gyarados"),
        annotations=(
            ProtocolAnnotation(
                name="from",
                value="Mirror Move",
            ),
        ),
        raw="",
    )

    source = parse_move_origin(message)

    assert source is not None
    assert source.type is SourceType.MOVE
    assert source.name == "Mirror Move"


def test_copy_stat_changes() -> None:
    source = Status(
        atk_stage=1,
        def_stage=2,
        spa_stage=3,
        spd_stage=4,
        spe_stage=5,
        eva_stage=6,
        acc_stage=-1,
    )
    target = Status()

    target.copy_stat_changes(source)

    for stat in Stat:
        assert target._get_stage(stat) == source._get_stage(stat)


def test_clear_negative_stages() -> None:
    status = Status(
        atk_stage=-1,
        def_stage=2,
        spa_stage=-3,
        spd_stage=4,
        spe_stage=-5,
        eva_stage=6,
        acc_stage=-2,
    )

    status.clear_negative_stages()

    assert status.atk_stage == 0
    assert status.def_stage == 2
    assert status.spa_stage == 0
    assert status.spd_stage == 4
    assert status.spe_stage == 0
    assert status.eva_stage == 6
    assert status.acc_stage == 0


def test_reset_all_stages() -> None:
    status = Status(
        atk_stage=1,
        def_stage=2,
        spa_stage=3,
        spd_stage=4,
        spe_stage=5,
        eva_stage=6,
        acc_stage=-1,
    )

    status.reset_all_stages()

    for stat in Stat:
        assert status._get_stage(stat) == 0
