import logging
import re
import traceback
from collections.abc import Callable, Mapping
from copy import copy
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock, Thread
from time import monotonic
from types import TracebackType
from typing import override

TRACE = 5
FILE_IO_FLUSH_INTERVAL = 0.1
logging.addLevelName(TRACE, "TRACE")

type ExcInfo = (
    bool
    | BaseException
    | tuple[type[BaseException], BaseException, TracebackType | None]
    | tuple[None, None, None]
    | None
)


@dataclass(slots=True)
class _FileIOCommand:
    operation: Callable[[], None] | None
    done: Event | None = None
    error: BaseException | None = None


class FileIOWorker:
    """
    Single worker thread responsible for BattleFileHandler disk operations.

    Operations are processed FIFO, so a room close submitted after log writes
    cannot overtake those writes.
    """

    def __init__(self) -> None:
        self._queue: Queue[_FileIOCommand] = Queue()
        self._thread: Thread | None = None
        self._lock: Lock = Lock()
        self._accepting: bool = False
        self._errors: list[BaseException] = []
        self._flush_targets: dict[int, Callable[[], None]] = {}

    def register_flush_target(
        self,
        target_id: int,
        operation: Callable[[], None],
    ) -> None:
        with self._lock:
            self._flush_targets[target_id] = operation


    def unregister_flush_target(self, target_id: int) -> None:
        with self._lock:
            self._flush_targets.pop(target_id, None)


    def _flush_targets_sync(self) -> None:
        with self._lock:
            operations = tuple(self._flush_targets.values())

        for operation in operations:
            try:
                operation()
            except BaseException as error: # noqa: BLE001
                self._errors.append(error)
                traceback.print_exception(error)

    @property
    def is_running(self) -> bool:
        with self._lock:
            thread = self._thread
            return (
                self._accepting
                and thread is not None
                and thread.is_alive()
            )

    def start(self) -> None:
        with self._lock:
            if self._accepting:
                return

            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError(
                    "File I/O worker thread exists but is not accepting work"
                )

            self._errors.clear()
            self._accepting = True
            self._thread = Thread(
                target=self._run,
                name="python-showdown-file-io",
            )
            self._thread.start()

    def submit(self, operation: Callable[[], None]) -> None:
        with self._lock:
            if not self._accepting:
                raise RuntimeError("File I/O worker is not running")

            thread = self._thread
            if thread is None or not thread.is_alive():
                raise RuntimeError("File I/O worker thread is not alive")

            self._queue.put(_FileIOCommand(operation))

    def submit_and_wait(self, operation: Callable[[], None]) -> None:
        done = Event()
        command = _FileIOCommand(
            operation=operation,
            done=done,
        )

        with self._lock:
            if not self._accepting:
                raise RuntimeError("File I/O worker is not running")

            thread = self._thread
            if thread is None or not thread.is_alive():
                raise RuntimeError("File I/O worker thread is not alive")

            self._queue.put(command)

        done.wait()

        if command.error is not None:
            raise RuntimeError(
                "File I/O operation failed"
            ) from command.error

    def stop(self) -> None:
        with self._lock:
            if not self._accepting:
                return

            self._accepting = False
            thread = self._thread

            if thread is None:
                raise RuntimeError(
                    "File I/O worker is marked running without a thread"
                )

            # FIFO sentinel. Every operation submitted before this point will
            # finish before the worker exits.
            self._queue.put(_FileIOCommand(None))

        thread.join()

        with self._lock:
            self._thread = None

        if self._errors:
            raise RuntimeError(
                "File I/O worker encountered "
                + f"{len(self._errors)} error(s)"
            ) from self._errors[0]

    def _run(self) -> None:
        next_flush = monotonic() + FILE_IO_FLUSH_INTERVAL

        while True:
            timeout = max(0.0, next_flush - monotonic())

            try:
                command = self._queue.get(timeout=timeout)
            except Empty:
                self._flush_targets_sync()
                next_flush = monotonic() + FILE_IO_FLUSH_INTERVAL
                continue

            while True:
                if command.operation is None:
                    # Everything before the sentinel has already been processed.
                    # Flush the remaining buffered data before exiting.
                    self._flush_targets_sync()

                    if command.done is not None:
                        command.done.set()

                    return

                try:
                    command.operation()

                except BaseException as error: # noqa:BLE001
                    command.error = error
                    self._errors.append(error)

                    # Do not silently hide failures occurring on the worker.
                    traceback.print_exception(error)

                finally:
                    if command.done is not None:
                        command.done.set()

                now = monotonic()

                if now >= next_flush:
                    self._flush_targets_sync()
                    next_flush = now + FILE_IO_FLUSH_INTERVAL

                # Drain everything already waiting without going back through
                # Queue.get()'s blocking path for every individual log record.
                try:
                    command = self._queue.get_nowait()
                except Empty:
                    break

FILE_IO_WORKER = FileIOWorker()


def start_file_io_worker() -> None:
    FILE_IO_WORKER.start()


def stop_file_io_worker() -> None:
    FILE_IO_WORKER.stop()


def log_trace(
    logger: logging.Logger,
    message: str,
    *args: object,
    exc_info: ExcInfo = None,
    stack_info: bool = False,
    stacklevel: int = 1,
    extra: Mapping[str, object] | None = None,
) -> None:
    logger.log(
        TRACE,
        message,
        *args,
        exc_info=exc_info,
        stack_info=stack_info,
        stacklevel=stacklevel,
        extra=extra,
    )


class BattleFileHandler(logging.Handler):
    """
    Routes records containing `room_id` into one file per battle room.

    Actual file creation, writes and room closes happen on FILE_IO_WORKER.
    """

    def __init__(
        self,
        directory: Path,
        *,
        filename: str | None = None,
        encoding: str = "utf-8",
    ) -> None:
        super().__init__()

        self.directory: Path = directory
        self.filename: str | None = filename
        self.encoding: str = encoding

        # Accessed only by FILE_IO_WORKER.
        self._handlers: dict[str, logging.FileHandler] = {}

        self._battle_handler_closed: bool = False

        self.directory.mkdir(parents=True, exist_ok=True)

        # This intentionally remains event-loop-side state.
        #
        # run_pair() can ask for latest_raw_log_path() immediately after an
        # exception, before the queued record has physically reached disk.
        self.last_path: Path | None = None

    @override
    def emit(self, record: logging.LogRecord) -> None:
        if self._battle_handler_closed:
            raise RuntimeError("Attempted to emit through a closed handler")

        room_id_value = record.__dict__.get("room_id")

        if not isinstance(room_id_value, str):
            return

        if not room_id_value.startswith("battle-"):
            return

        # Update this synchronously. Code reporting a failed battle may query
        # the path before the worker has processed the corresponding record.
        self.last_path = self._path_for_room(room_id_value)

        # A LogRecord can be passed through multiple handlers after this emit()
        # returns. Give the worker its own shallow copy so later handlers cannot
        # mutate the record while it is waiting in the queue.
        queued_record = copy(record)

        FILE_IO_WORKER.submit(
            lambda: self._emit_sync(
                room_id_value,
                queued_record,
            )
        )

    def _emit_sync(
        self,
        room_id: str,
        record: logging.LogRecord,
    ) -> None:
        """
        Runs exclusively on FILE_IO_WORKER.
        """
        handler = self._handlers.get(room_id)

        if handler is None:
            handler = self._create_handler(room_id)
            self._handlers[room_id] = handler

        handler.emit(record)

    def close_room(self, room_id: str) -> None:
        """
        Queue the close after all previously queued writes.

        Because writes and closes use the same FIFO worker, this cannot close
        the room's file before an earlier record has been written.
        """
        if self._battle_handler_closed:
            return

        FILE_IO_WORKER.submit(
            lambda: self._close_room_sync(room_id)
        )

    def _close_room_sync(self, room_id: str) -> None:
        """
        Runs exclusively on FILE_IO_WORKER.
        """
        handler = self._handlers.pop(room_id, None)

        if handler is not None:
            handler.close()

    def clear_last_path(self) -> None:
        self.last_path = None

    def _path_for_room(self, room_id: str) -> Path:
        safe_room_id = re.sub(
            r"[^a-zA-Z0-9_.-]",
            "_",
            room_id,
        )

        if self.filename is None:
            return self.directory / f"{safe_room_id}.txt"

        battle_number = safe_room_id.rsplit("-", 1)[-1]
        battle_directory = self.directory / f"battle_{battle_number}"
        return battle_directory / self.filename

    def _create_handler(
        self,
        room_id: str,
    ) -> logging.FileHandler:
        path = self._path_for_room(room_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        handler = logging.FileHandler(
            path,
            encoding=self.encoding,
        )
        handler.setLevel(self.level)
        handler.setFormatter(self.formatter)

        return handler

    def _close_all_sync(self) -> None:
        """
        Runs either on FILE_IO_WORKER or after the worker has stopped.
        """
        first_error: BaseException | None = None

        for handler in self._handlers.values():
            try:
                handler.close()
            except Exception as error: # noqa: BLE001
                if first_error is None:
                    first_error = error

        self._handlers.clear()

        if first_error is not None:
            raise first_error

    @override
    def close(self) -> None:
        if self._battle_handler_closed:
            return

        self._battle_handler_closed = True

        try:
            if FILE_IO_WORKER.is_running:
                # Queueing this behind all previous writes guarantees that
                # nothing belonging to this handler is still pending when
                # close() returns.
                FILE_IO_WORKER.submit_and_wait(
                    self._close_all_sync
                )
            else:
                # This is safe after stop_file_io_worker(), because stop()
                # drains the FIFO queue before terminating the worker.
                self._close_all_sync()

        finally:
            super().close()


class LogManager:
    def __init__(self, tag: str | None = None) -> None:
        suffix = f".{tag}" if tag else ""

        self.protocol: logging.Logger = logging.getLogger(
            f"python_showdown.protocol{suffix}"
        )
        self.battle: logging.Logger = logging.getLogger(
            f"python_showdown.battle{suffix}"
        )
        self.errors: logging.Logger = logging.getLogger(
            f"python_showdown.errors{suffix}"
        )

        self._loggers: tuple[logging.Logger, ...] = (
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

        if not any(
            handler in logger.handlers
            for logger in self._loggers
        ):
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

    def close_room(self, room_id: str | None) -> None:
        """Close the per-room file handler for `room_id` on every
        `BattleFileHandler` attached to the managed loggers."""
        if room_id is None:
            raise RuntimeError("Tried to close a None room")

        seen: set[int] = set()

        for logger in self._loggers:
            for handler in logger.handlers:
                if id(handler) in seen:
                    continue

                seen.add(id(handler))

                if isinstance(handler, BattleFileHandler):
                    handler.close_room(room_id)

    def latest_raw_log_path(self) -> Path | None:
        for handler in self.protocol.handlers:
            if isinstance(handler, BattleFileHandler):
                return handler.last_path

        return None

    def clear_latest_raw_log_path(self) -> None:
        for handler in self.protocol.handlers:
            if isinstance(handler, BattleFileHandler):
                handler.clear_last_path()


def create_console_handler(
    level: int = logging.INFO,
) -> logging.Handler:
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("%(levelname)s %(message)s")
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
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        )
    )
    return handler


def create_battle_file_handler(
    directory: Path,
    level: int = logging.INFO,
    *,
    filename: str | None = None,
) -> logging.Handler:
    handler = BattleFileHandler(
        directory,
        filename=filename,
    )
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(message)s")
    )
    return handler
