"""
Pokémon representation for team building, distinct from the in-battle representation.
"""

import re
from dataclasses import dataclass, field

from python_showdown.models.pokemon.status import EVs, IVs


@dataclass
class PokemonSet:
    species: str
    moves: list[str] = field(default_factory=list)

    nickname: str | None = None
    item: str | None = None
    ability: str | None = None
    nature: str | None = None
    evs: EVs = field(default_factory=lambda: EVs(0, 0, 0, 0, 0, 0))
    ivs: IVs = field(default_factory=IVs)

    gender: str | None = None
    shiny: bool = False
    level: int = 100
    happiness: int = 255
    pokeball: str | None = None

    hidden_power_type: str | None = None  # TODO implement

    def to_packed(self) -> str:
        name = self.nickname or self.species

        species = (
            ""
            if _pack_name(name) == _pack_name(self.species)
            else _pack_name(self.species)
        )

        return "|".join(
            [
                name,
                species,
                _pack_name(self.item),
                _pack_name(self.ability),
                ",".join(_pack_name(move) for move in self.moves),
                self.nature or "",
                self._packed_evs(),
                self.gender or "",
                self._packed_ivs(),
                "S" if self.shiny else "",
                str(self.level) if self.level != 100 else "",
                str(self.happiness) if self.happiness != 255 else "",
            ]
        )

    def _packed_evs(self) -> str:
        values = [
            self.evs.hp,
            self.evs.atk,
            self.evs.def_,
            self.evs.spa,
            self.evs.spd,
            self.evs.spe,
        ]

        packed = ",".join("" if value == 0 else str(value) for value in values)

        return "" if packed == ",,,,," else packed

    def _packed_ivs(self) -> str:
        values = [
            self.ivs.hp,
            self.ivs.atk,
            self.ivs.def_,
            self.ivs.spa,
            self.ivs.spd,
            self.ivs.spe,
        ]

        packed = ",".join("" if value == 31 else str(value) for value in values)

        return "" if packed == ",,,,," else packed

    def to_showdown(self) -> str:
        if self.nickname and self.nickname != self.species:
            first_line = f"{self.nickname} ({self.species})"
        else:
            first_line = self.species

        if self.gender:
            first_line += f" ({self.gender})"

        if self.item:
            first_line += f" @ {self.item}"

        lines = [first_line]

        if self.ability:
            lines.append(f"Ability: {self.ability}")

        if self.level != 100:
            lines.append(f"Level: {self.level}")

        if self.shiny:
            lines.append("Shiny: Yes")

        if self.happiness != 255:
            lines.append(f"Happiness: {self.happiness}")

        if self.pokeball:
            lines.append(f"Pokeball: {self.pokeball}")

        evs = _format_evs(self.evs)
        if evs:
            lines.append(f"EVs: {evs}")

        if self.nature:
            lines.append(f"{self.nature} Nature")

        ivs = _format_ivs(self.ivs)
        if ivs:
            lines.append(f"IVs: {ivs}")

        lines.extend(f"- {move}" for move in self.moves)

        return "\n".join(lines)


@dataclass
class TeamSet:
    pokemon: list[PokemonSet]

    def to_packed(self) -> str:
        return "]".join(pokemon.to_packed() for pokemon in self.pokemon)

    def to_showdown(self) -> str:
        return "\n\n".join(pokemon.to_showdown() for pokemon in self.pokemon)

    @classmethod
    def from_showdown(cls, text: str) -> TeamSet:
        blocks: list[list[str]] = []
        block: list[str] = []

        for raw_line in text.replace("\r\n", "\n").split("\n"):
            line = raw_line.strip()

            if line == "" or line == "---":
                if block:
                    blocks.append(block)
                    block = []
                continue

            # Showdown backup headers:
            # === [gen9ou] My Team ===
            if line.startswith("==="):
                continue

            block.append(line)

        if block:
            blocks.append(block)

        return cls([_parse_pokemon(block) for block in blocks])


def _parse_pokemon(lines: list[str]) -> PokemonSet:
    if not lines:
        raise ValueError("Empty Pokémon set")

    species, nickname, gender, item = _parse_header(lines[0])

    pokemon = PokemonSet(
        species=species,
        nickname=nickname,
        gender=gender,
        item=item,
    )

    for line in lines[1:]:
        if line.startswith("Ability: "):
            pokemon.ability = line[9:].strip()

        elif line.startswith("Gender: "):
            gender = line[8:].strip()

            if gender not in {"M", "F"}:
                raise ValueError(f"Invalid gender: {gender!r}")

            pokemon.gender = gender

        elif line.startswith("Level: "):
            pokemon.level = int(line[7:])

        elif line == "Shiny: Yes":
            pokemon.shiny = True

        elif line.startswith("Happiness: "):
            pokemon.happiness = int(line[11:])

        elif line.startswith("Pokeball: "):
            pokemon.pokeball = line[10:].strip()

        elif line.startswith("Hidden Power: "):
            pokemon.hidden_power_type = line[14:].strip()

        elif line.startswith("EVs: "):
            pokemon.evs = _parse_evs(line[5:])

        elif line.startswith("IVs: "):
            pokemon.ivs = _parse_ivs(line[5:])

        elif line.endswith(" Nature"):
            pokemon.nature = line[:-7].strip()

        elif line.startswith("- "):
            pokemon.moves.append(line[2:].strip())

        else:
            raise ValueError(f"Unsupported Showdown team line: {line!r}")

    return pokemon


def _parse_header(
    line: str,
) -> tuple[str, str | None, str | None, str | None]:
    item = None

    if " @ " in line:
        line, item = line.rsplit(" @ ", 1)
        item = item.strip()

    gender = None

    if line.endswith(" (M)"):
        gender = "M"
        line = line[:-4]
    elif line.endswith(" (F)"):
        gender = "F"
        line = line[:-4]

    nickname = None

    if line.endswith(")") and " (" in line:
        nickname_part, species_part = line.rsplit(" (", 1)
        species = species_part[:-1].strip()
        nickname = nickname_part.strip()
    else:
        species = line.strip()

    if not species:
        raise ValueError("Pokémon species cannot be empty")

    return species, nickname, gender, item


def _pack_name(name: str | None) -> str:
    if not name:
        return ""

    # Matches Showdown Teams.packName:
    # strip everything except ASCII letters/digits.
    return re.sub(r"[^A-Za-z0-9]+", "", name)


_STAT_ATTRIBUTES = {
    "hp": "hp",
    "atk": "atk",
    "def": "def_",
    "spa": "spa",
    "spd": "spd",
    "spe": "spe",
}


def _parse_evs(text: str) -> EVs:
    evs = EVs(
        hp=0,
        atk=0,
        def_=0,
        spa=0,
        spd=0,
        spe=0,
    )

    for part in text.split("/"):
        value, stat = _parse_stat_part(part)
        setattr(evs, _STAT_ATTRIBUTES[stat], value)

    return evs


def _parse_ivs(text: str) -> IVs:
    ivs = IVs()

    for part in text.split("/"):
        value, stat = _parse_stat_part(part)
        setattr(ivs, _STAT_ATTRIBUTES[stat], value)

    return ivs


def _parse_stat_part(part: str) -> tuple[int, str]:
    match = re.fullmatch(
        r"\s*(\d+)\s+(HP|Atk|Def|SpA|SpD|Spe)\s*",
        part,
        flags=re.IGNORECASE,
    )

    if match is None:
        raise ValueError(f"Invalid stat specification: {part!r}")

    value = int(match.group(1))
    stat = match.group(2).lower()

    return value, stat


def _format_evs(evs: EVs) -> str:
    values = [
        ("HP", evs.hp),
        ("Atk", evs.atk),
        ("Def", evs.def_),
        ("SpA", evs.spa),
        ("SpD", evs.spd),
        ("Spe", evs.spe),
    ]

    return " / ".join(f"{value} {name}" for name, value in values if value != 0)


def _format_ivs(ivs: IVs) -> str:
    values = [
        ("HP", ivs.hp),
        ("Atk", ivs.atk),
        ("Def", ivs.def_),
        ("SpA", ivs.spa),
        ("SpD", ivs.spd),
        ("Spe", ivs.spe),
    ]

    return " / ".join(f"{value} {name}" for name, value in values if value != 31)


if __name__ == "__main__":
    pokemon = PokemonSet(
        species="Starmie",
        item="Leftovers",
        ability="Natural Cure",
        nature="Timid",
        evs=EVs(
            hp=4,
            atk=0,
            def_=0,
            spa=252,
            spd=0,
            spe=252,
        ),
        moves=[
            "Surf",
            "Thunderbolt",
            "Ice Beam",
            "Rapid Spin",
        ],
    ).to_showdown()

    # print(pokemon)
    team = TeamSet.from_showdown("""
        Granbull @ Leftovers
        Ability: Intimidate
        Gender: M
        Adamant Nature
        EVs: 252 HP / 56 Atk / 156 Def / 44 Spe
        - Body Slam
        - Bulk Up
        - Earthquake
        - Shadow Ball

        Kangaskhan @ Leftovers
        Ability: Early Bird
        Adamant Nature
        EVs: 248 HP / 24 Atk / 196 SpD / 40 Spe
        - Focus Punch
        - Rest
        - Return
        - Wish

        Lapras @ Leftovers
        Ability: Shell Armor
        Gender: F
        Modest Nature
        EVs: 80 HP / 56 Def / 252 SpA / 24 SpD / 96 Spe
        IVs: 0 Atk
        - Heal Bell
        - Hydro Pump
        - Ice Beam
        - Thunderbolt

        Nidoqueen @ Leftovers
        Ability: Poison Point
        Adamant Nature
        EVs: 80 HP / 248 Atk / 4 Def / 176 Spe
        - Earthquake
        - Shadow Ball
        - Sludge Bomb
        - Superpower

        Solrock @ Choice Band
        Ability: Levitate
        Shiny: Yes
        Adamant Nature
        EVs: 32 HP / 252 Atk / 224 Spe
        - Earthquake
        - Explosion
        - Rock Slide
        - Shadow Ball

        Tentacruel @ Leftovers
        Ability: Liquid Ooze
        Gender: F
        Timid Nature
        EVs: 252 SpA / 4 SpD / 252 Spe
        IVs: 0 Atk
        - Ice Beam
        - Rapid Spin
        - Surf
        - Toxic
""")
    print(team)
