"""Explicitly enabled, private raw decision samples; predictions are never labels."""

import copy
import hashlib
import json
import os
import stat
import threading
import uuid
from dataclasses import asdict
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def private_append(path, row):
    """Do not follow symlinks or append sensitive samples to publicly readable files."""
    path = Path(path)
    payload = (canonical(row) + "\n").encode("utf-8")
    if len(payload) > 262_144:
        raise ValueError("Judge sample exceeds 256 KiB")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(path), flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("Judge sample files must be private regular files (0600)")
        with os.fdopen(descriptor, "ab", closefd=False) as file:
            file.write(payload)
    finally:
        os.close(descriptor)


class JudgeSampler:
    def __init__(self, path, group_id):
        if not isinstance(group_id, str) or not group_id.strip() or len(group_id) > 256:
            raise ValueError("Raw judge sampling requires an explicit group ID")
        self.path = Path(path)
        self.group_id = group_id
        self.context = {}
        self._lock = threading.Lock()

    def begin_turn(self):
        self.context["turn_id"] = uuid.uuid4().hex

    def snapshot(self, point, inputs, state, questions, policy):
        # Full policy input is required for aggregation; state is the exact builder output.
        material = {"point": point.id, "version": point.version,
                    "questions": [asdict(question) for question in questions],
                    "inputs": copy.deepcopy(inputs), "state": copy.deepcopy(state)}
        if len(canonical(state).encode("utf-8")) > 65_536:
            raise ValueError("Judge state exceeds 64 KiB")
        sample = {"schema_version": 1, "sample_id": uuid.uuid4().hex,
                  "input_digest": digest(material), "group_id": self.group_id,
                  "source": copy.deepcopy(self.context), "decision": material,
                  "policy": asdict(policy)}
        if len(canonical(sample).encode("utf-8")) > 196_608:
            raise ValueError("Judge sample input exceeds 192 KiB")
        return sample

    def write(self, sample, record):
        with self._lock:
            private_append(self.path, {**sample, "observed": copy.deepcopy(record)})
