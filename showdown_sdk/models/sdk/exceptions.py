from typing import override


class TeamRejectedError(Exception):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__()
        self.reasons: list[str] = reasons

    @override
    def __str__(self) -> str:
        return "The team was rejected for the following reason(s):\n" + "\n".join(
            self.reasons
        )
