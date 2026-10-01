"""Private, conditional undo receipts for local UTF-8 file mutations."""

import difflib
import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path


MAX_BEFORE_BYTES = 8_000_000


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class FileRecovery:
    def __init__(self, workspace: Path, output_root: Path):
        self.workspace = workspace.resolve(strict=True)
        self.output_root = Path(output_root)

    @property
    def directory(self):
        directory = self.output_root / "recovery"
        if self.output_root.is_symlink() or directory.is_symlink():
            raise ValueError("Recovery storage cannot be a symbolic link")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            directory.chmod(0o700)
        if directory.resolve().parent != self.output_root.resolve():
            raise ValueError("Recovery storage moved outside its output root")
        return directory

    def _path(self, recovery_id):
        if not isinstance(recovery_id, str):
            raise ValueError("Recovery ID must be a canonical UUID")
        try:
            canonical = str(uuid.UUID(recovery_id))
        except ValueError as error:
            raise ValueError("Recovery ID must be a canonical UUID") from error
        if canonical != recovery_id:
            raise ValueError("Recovery ID must be a canonical UUID")
        return self.directory / (recovery_id + ".json")

    def stage(self, target: Path, change: dict):
        """Durably save original text before the local atomic replacement."""
        relative = target.relative_to(self.workspace).as_posix()
        if relative != change["path"]:
            raise ValueError("Recovery path differs from prepared change")
        existed = target.exists()
        if existed != change["existed"]:
            raise ValueError("File existence changed before recovery snapshot")
        if existed:
            with target.open("r", encoding="utf-8", newline="") as source:
                before = source.read()
        else:
            before = ""
        size = len(before.encode("utf-8"))
        if size > MAX_BEFORE_BYTES:
            raise ValueError("Original file exceeds the recoverable 8 MB limit")
        if (existed and _hash(before) != change["before_sha256"] or
                not existed and change["before_sha256"] is not None):
            raise ValueError("File changed before recovery snapshot")
        before_mode = stat.S_IMODE(target.stat().st_mode) if existed else None
        recovery_id = str(uuid.uuid4())
        receipt = {"schema_version": 1, "id": recovery_id,
                   "workspace_sha256": hashlib.sha256(str(self.workspace).encode()).hexdigest(),
                   "path": relative, "existed": existed,
                   "before_sha256": change["before_sha256"],
                   "after_sha256": change["after_sha256"],
                   "before_mode": before_mode,
                   "after_mode": before_mode if existed else 0o600,
                   "before_text": before}
        path = self._path(recovery_id)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(str(path), flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(receipt, file, ensure_ascii=False, allow_nan=False)
            file.flush()
            os.fsync(file.fileno())
        return recovery_id

    def _load(self, recovery_id):
        path = self._path(recovery_id)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(str(path), flags)
        with os.fdopen(descriptor, "r", encoding="utf-8") as file:
            info = os.fstat(file.fileno())
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077
                    or info.st_size > MAX_BEFORE_BYTES * 6):
                raise ValueError("Recovery receipt must be a private regular file")
            receipt = json.load(file)
        if (receipt.get("schema_version") != 1 or receipt.get("id") != recovery_id or
                receipt.get("workspace_sha256") != hashlib.sha256(str(self.workspace).encode()).hexdigest() or
                not isinstance(receipt.get("path"), str) or not receipt["path"] or
                type(receipt.get("existed")) is not bool or
                not isinstance(receipt.get("before_text"), str) or
                not isinstance(receipt.get("after_sha256"), str) or
                re.fullmatch(r"[0-9a-f]{64}", receipt["after_sha256"]) is None or
                type(receipt.get("after_mode")) is not int or
                not 0 <= receipt["after_mode"] <= 0o7777 or
                receipt["existed"] and (
                    receipt.get("before_sha256") != _hash(receipt["before_text"]) or
                    type(receipt.get("before_mode")) is not int or
                    not 0 <= receipt["before_mode"] <= 0o7777) or
                not receipt["existed"] and (
                    receipt.get("before_sha256") is not None or
                    receipt.get("before_mode") is not None or receipt["before_text"] != "")):
            raise ValueError("Invalid or changed recovery receipt")
        target = self.workspace / receipt["path"]
        if target.resolve() != target or self.workspace not in target.parents:
            raise ValueError("Recovery target was redirected")
        return receipt, target, path

    def preview(self, recovery_id):
        receipt, target, _ = self._load(recovery_id)
        if not target.is_file():
            raise ValueError("Recovery target no longer exists as a file")
        with target.open("r", encoding="utf-8", newline="") as source:
            current = source.read()
        if (_hash(current) != receipt["after_sha256"] or
                stat.S_IMODE(target.stat().st_mode) != receipt["after_mode"]):
            raise ValueError("Recovery target changed after the recorded write")
        restored = receipt["before_text"] if receipt["existed"] else ""
        diff = "\n".join(difflib.unified_diff(
            current.splitlines(), restored.splitlines(), lineterm="",
            fromfile="a/" + receipt["path"],
            tofile="b/" + receipt["path"] if receipt["existed"] else "/dev/null"))
        if diff:
            diff += "\n"
        return {"path": receipt["path"], "operation": "restore_file_change",
                "recovery_id": recovery_id, "existed_before_write": receipt["existed"],
                "before_sha256": receipt["after_sha256"],
                "after_sha256": receipt["before_sha256"],
                "before_bytes": len(current.encode("utf-8")),
                "after_bytes": len(restored.encode("utf-8")) if receipt["existed"] else 0,
                "diff": diff}

    def restore(self, recovery_id):
        receipt, target, path = self._load(recovery_id)
        change = self.preview(recovery_id)
        if receipt["existed"]:
            from .file_ops import LocalFileOperations
            LocalFileOperations().replace_text(target, receipt["before_text"])
        else:
            target.unlink()
        os.replace(path, path.with_suffix(".restored.json"))
        return change
