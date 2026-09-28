"""Process transport separated from bounded output and artifact policy."""

import codecs
import os
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, List, Optional, Protocol

from .cancel import CancellationToken, TurnCancelled


class CommandOperations(Protocol):
    def execute(self, argv: List[str], cwd: Path, timeout: int,
                cancel: Optional[CancellationToken], on_data: Callable[[bytes], None]) -> int: ...


class LocalCommandOperations:
    def __init__(self, merge_stderr: bool = True):
        self.merge_stderr = merge_stderr

    def execute(self, argv: List[str], cwd: Path, timeout: int,
                cancel: Optional[CancellationToken], on_data: Callable[[bytes], None]) -> int:
        if cancel is not None:
            cancel.raise_if_cancelled()
        process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT if self.merge_stderr else subprocess.DEVNULL,
                                   start_new_session=os.name != "nt")
        errors = []

        def drain():
            try:
                while True:
                    chunk = os.read(process.stdout.fileno(), 4096)
                    if not chunk:
                        break
                    on_data(chunk)
            except Exception as error:
                errors.append(error)

        reader = threading.Thread(target=drain, daemon=True, name="mu-process-output")
        reader.start()
        deadline = time.monotonic() + timeout
        stopped = None
        try:
            while process.poll() is None or reader.is_alive():
                if cancel is not None and cancel.is_cancelled():
                    stopped = "cancel"
                    break
                if errors:
                    stopped = "output-error"
                    break
                if time.monotonic() >= deadline:
                    stopped = "timeout"
                    break
                time.sleep(0.05)
            if stopped is not None:
                self._kill_process_tree(process)
                process.wait()
        finally:
            reader.join(timeout=2)
            process.stdout.close()
        if reader.is_alive():
            raise OSError("Command output stream did not close")
        if errors:
            raise errors[0]
        if stopped == "cancel" or cancel is not None and cancel.is_cancelled():
            raise TurnCancelled("Turn cancelled")
        if stopped == "timeout":
            raise subprocess.TimeoutExpired(argv, timeout)
        return process.returncode

    @staticmethod
    def _kill_process_tree(process: subprocess.Popen) -> None:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            except OSError:
                pass
            if process.poll() is None:
                process.kill()


class CommandOutput:
    """Identical bounded output policy for local and alternate transports."""

    def __init__(self, directory: Path, on_update=None, on_artifact=None):
        self.directory = directory
        self.on_update = on_update
        self.on_artifact = on_artifact
        self.tail = bytearray()
        self.total = 0
        self.artifact_id = None
        self.artifact_file = None
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.pending = ""
        self.last_emit = 0.0

    def update(self, value: str) -> None:
        if self.on_update is not None and value:
            try:
                self.on_update(value)
            except Exception:
                pass

    def feed(self, chunk: bytes) -> None:
        if not isinstance(chunk, bytes):
            raise ValueError("Command operations must emit bytes")
        for start in range(0, len(chunk), 4096):
            self._feed_chunk(chunk[start:start + 4096])

    def _feed_chunk(self, chunk: bytes) -> None:
        if self.artifact_file is None and self.total + len(chunk) > 12_000:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name != "nt":
                self.directory.chmod(0o700)
            self.artifact_id = str(uuid.uuid4())
            descriptor = os.open(self.directory / (self.artifact_id + ".log"),
                                 os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            self.artifact_file = os.fdopen(descriptor, "wb")
            self.artifact_file.write(self.tail)
            if self.on_artifact is not None:
                try:
                    self.on_artifact(self.artifact_id)
                except Exception:
                    pass
        if self.artifact_file is not None:
            self.artifact_file.write(chunk)
        self.total += len(chunk)
        self.tail.extend(chunk)
        if len(self.tail) > 12_000:
            del self.tail[:-12_000]
        self.pending = (self.pending + self.decoder.decode(chunk))[-8_000:]
        now = time.monotonic()
        if self.pending and (self.last_emit == 0.0 or now - self.last_emit >= 0.1):
            self.update(self.pending)
            self.pending = ""
            self.last_emit = now

    def close(self) -> None:
        self.update(self.pending + self.decoder.decode(b"", final=True))
        self.pending = ""
        if self.artifact_file is not None:
            try:
                self.artifact_file.flush()
                os.fsync(self.artifact_file.fileno())
            finally:
                self.artifact_file.close()

    def result(self, exit_code: int) -> str:
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise ValueError("Command operations must return an integer exit code")
        output = bytes(self.tail).decode("utf-8", errors="replace")
        if self.artifact_id:
            output += "\n[Output truncated at 12 KB]\nFull output id: " + self.artifact_id
        return "Exit code: " + str(exit_code) + "\n" + output
