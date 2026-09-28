"""Small replaceable file backend and serialized atomic mutations."""

import os
import stat
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import ContextManager, Iterable, Iterator, List, Optional, Protocol

from .cancel import CancellationToken


class FileOperations(Protocol):
    def read_text(self, path: Path) -> str: ...
    def open_text(self, path: Path, cancel: Optional[CancellationToken] = None) -> ContextManager[Iterable[str]]: ...
    def is_dir(self, path: Path) -> bool: ...
    def list_dir(self, path: Path, cancel: Optional[CancellationToken] = None) -> List[str]: ...
    def exists(self, path: Path) -> bool: ...
    def size(self, path: Path) -> int: ...
    def replace_text(self, path: Path, content: str) -> None: ...


class LocalFileOperations:
    def read_text(self, path: Path) -> str:
        with path.open("r", encoding="utf-8", newline="") as source:
            return source.read()

    def open_text(self, path: Path, cancel: Optional[CancellationToken] = None) -> ContextManager[Iterable[str]]:
        if cancel is not None:
            cancel.raise_if_cancelled()
        return path.open(encoding="utf-8")

    def is_dir(self, path: Path) -> bool:
        return path.is_dir()

    def list_dir(self, path: Path, cancel: Optional[CancellationToken] = None) -> List[str]:
        entries = []
        for child in path.iterdir():
            if cancel is not None:
                cancel.raise_if_cancelled()
            entries.append(child.name + ("/" if child.is_dir() else ""))
        return entries

    def exists(self, path: Path) -> bool:
        return path.exists()

    def size(self, path: Path) -> int:
        return path.stat().st_size

    def replace_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".mupyjava-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as output:
                if path.exists():
                    os.fchmod(output.fileno(), stat.S_IMODE(path.stat().st_mode))
                output.write(content.encode("utf-8"))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            if os.name != "nt":
                try:
                    directory = os.open(path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                except OSError:
                    # Some filesystems cannot sync directories; the replace already committed.
                    pass
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class FileMutationQueue:
    """FIFO per-path tickets; unrelated files can be changed in parallel."""

    def __init__(self):
        self._condition = threading.Condition()
        self._tickets = {}

    @contextmanager
    def hold(self, path: Path) -> Iterator[None]:
        key = str(path)
        with self._condition:
            next_ticket, serving = self._tickets.get(key, (0, 0))
            ticket = next_ticket
            self._tickets[key] = (next_ticket + 1, serving)
            while self._tickets[key][1] != ticket:
                self._condition.wait()
        try:
            yield
        finally:
            with self._condition:
                next_ticket, serving = self._tickets[key]
                if serving + 1 == next_ticket:
                    del self._tickets[key]
                else:
                    self._tickets[key] = (next_ticket, serving + 1)
                self._condition.notify_all()
