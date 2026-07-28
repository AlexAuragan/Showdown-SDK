import logging
import re
from pathlib import Path
from typing import Any

TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def log_trace(
    logger: logging.Logger,
    message: str,
    *args: Any,
    **kwargs: Any,
) -> None:
    logger.log(TRACE, message, *args, **kwargs)


class BattleFileHandler(logging.Handler):
    """
    Routes records containing `room_id` into one file per battle room.
    """

    def __init__(
        self,
        directory: Path,
        *,
        encoding: str = "utf-8",
    ) -> None:
        super().__init__()
        self.directory = directory
        self.encoding = encoding
        self._handlers: dict[str, logging.FileHandler] = {}

        self.directory.mkdir(parents=True, exist_ok=True)

    def emit(self, record: logging.LogRecord) -> None:
        room_id_value = record.__dict__.get("room_id")

        if not isinstance(room_id_value, str):
            return

        if not room_id_value.startswith("battle-"):
            return

        handler = self._handlers.get(room_id_value)

        if handler is None:
            handler = self._create_handler(room_id_value)
            self._handlers[room_id_value] = handler

        handler.emit(record)

    def _create_handler(
        self,
        room_id: str,
    ) -> logging.FileHandler:
        safe_room_id = re.sub(
            r"[^a-zA-Z0-9_.-]",
            "_",
            room_id,
        )

        path = self.directory / f"{safe_room_id}.txt"

        handler = logging.FileHandler(
            path,
            encoding=self.encoding,
        )
        handler.setLevel(self.level)
        handler.setFormatter(self.formatter)

        return handler

    def close(self) -> None:
        for handler in self._handlers.values():
            handler.close()

        self._handlers.clear()
        super().close()


class LogManager:
    def __init__(self, tag: str | None = None) -> None:
        suffix = f".{tag}" if tag else ""
        self.protocol = logging.getLogger(
            f"python_showdown.protocol{suffix}"
        )
        self.battle = logging.getLogger(
            f"python_showdown.battle{suffix}"
        )
        self.errors = logging.getLogger(
            f"python_showdown.errors{suffix}"
        )

        self._loggers = (
            self.protocol,
            self.battle,
            self.errors,
        )

        for logger in self._loggers:
            logger.handlers.clear()
            logger.propagate = False
            logger.setLevel(TRACE)

    def add_handler(
        self,
        handler: logging.Handler,
        *,
        loggers: str | tuple[logging.Logger, ...] | None = None,
    ) -> None:
        """Attach `handler` to the requested loggers.

        `loggers` selects which of the managed loggers the handler is
        attached to. Accepts one of the string names "protocol",
        "battle", "errors" or a tuple of the corresponding Logger
        objects. When omitted the handler is attached to all of them
        (the historical behaviour).
        """
        targets = self._resolve_loggers(loggers)
        for logger in targets:
            logger.addHandler(handler)

    def remove_handler(
        self,
        handler: logging.Handler,
        *,
        loggers: str | tuple[logging.Logger, ...] | None = None,
    ) -> None:
        targets = self._resolve_loggers(loggers)
        for logger in targets:
            logger.removeHandler(handler)

        handler.close()

    def _resolve_loggers(
        self,
        loggers: str | tuple[logging.Logger, ...] | None,
    ) -> tuple[logging.Logger, ...]:
        if loggers is None:
            return self._loggers

        by_name = {
            "protocol": self.protocol,
            "battle": self.battle,
            "errors": self.errors,
        }

        if isinstance(loggers, str):
            return (by_name[loggers],)

        return loggers

    def disable(self) -> None:
        for logger in self._loggers:
            logger.disabled = True

    def enable(self) -> None:
        for logger in self._loggers:
            logger.disabled = False

def create_console_handler(
    level: int = logging.INFO,
) -> logging.Handler:
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            "%(levelname)s %(message)s"
        )
    )
    return handler


def create_file_handler(
    path: Path,
    level: int = TRACE,
) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(
        path,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s "
            "%(message)s"
        )
    )
    return handler


def create_battle_file_handler(
    directory: Path,
    level: int = logging.INFO,
) -> logging.Handler:
    handler = BattleFileHandler(directory)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(message)s"
        )
    )
    return handler
