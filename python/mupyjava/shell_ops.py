"""Shell transport with explicit Bash and PowerShell selection."""

import shutil
from pathlib import Path
from typing import Callable, Optional, Protocol

from .cancel import CancellationToken
from .command_ops import LocalCommandOperations


class ShellOperations(Protocol):
    def execute(self, shell: str, script: str, cwd: Path, timeout: int,
                cancel: Optional[CancellationToken], on_data: Callable[[bytes], None]) -> int: ...


class LocalShellOperations:
    def __init__(self):
        self.commands = LocalCommandOperations()

    @staticmethod
    def argv(shell: str, script: str):
        if shell == "bash":
            executable = shutil.which("bash")
            if executable is None:
                raise ValueError("Bash is unavailable; install bash or use run_command")
            return [executable, "--noprofile", "--norc", "-c", script]
        if shell == "powershell":
            executable = shutil.which("pwsh") or shutil.which("powershell")
            if executable is None:
                raise ValueError("PowerShell is unavailable; install pwsh or use run_command")
            prefix = "try { [Console]::OutputEncoding=[System.Text.Encoding]::UTF8 } catch {}\n"
            return [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", prefix + script]
        raise ValueError("Unsupported shell: " + shell)

    def execute(self, shell: str, script: str, cwd: Path, timeout: int,
                cancel: Optional[CancellationToken], on_data: Callable[[bytes], None]) -> int:
        if cancel is not None:
            cancel.raise_if_cancelled()
        return self.commands.execute(self.argv(shell, script), cwd, timeout, cancel, on_data)
