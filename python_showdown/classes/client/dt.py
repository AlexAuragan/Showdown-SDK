from dataclasses import dataclass
from enum import IntFlag

class FormatFlag(IntFlag):
    RANDOM_TEAM = 1
    SEARCH = 2
    CHALLENGE = 4
    TOURNAMENT = 8
    LEVEL_50 = 16
    BEST_OF_DEFAULT = 64
    TERA_PREVIEW_DEFAULT = 128
    ITEM_CLAUSE_DEFAULT = 256

@dataclass
class Format:
    name: str
    flags: FormatFlag
    section: str
    column: int


    @property
    def uses_random_team(self) -> bool:
        return FormatFlag.RANDOM_TEAM in self.flags

    @property
    def can_search(self) -> bool:
        return FormatFlag.SEARCH in self.flags

    @property
    def can_challenge(self) -> bool:
        return FormatFlag.CHALLENGE in self.flags

    @property
    def can_tournament(self) -> bool:
        return FormatFlag.TOURNAMENT in self.flags
