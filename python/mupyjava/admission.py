"""Bounded output-kind admission; archives and hard budgets belong to context.py."""

import copy
import hashlib
import json
import math
import os
import time
from collections import OrderedDict
from dataclasses import dataclass

from .decision_points import TOOL_ADMISSION


@dataclass(frozen=True)
class AdmissionSettings:
    min_chars: int = 6000
    chunk_chars: int = 1200
    max_chunks: int = 48
    batch_chunks: int = 16
    wait_seconds: float = 4.0

    def __post_init__(self):
        for name, low, high in (("min_chars", 1000, 200000), ("chunk_chars", 256, 1200),
                                ("max_chunks", 3, 48), ("batch_chunks", 1, 32)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError("Invalid admission " + name)
        if (type(self.wait_seconds) not in (int, float) or not math.isfinite(self.wait_seconds)
                or not 0.01 <= self.wait_seconds <= 30):
            raise ValueError("Admission wait_seconds must be between 0.01 and 30")

    @classmethod
    def from_environment(cls):
        return cls(wait_seconds=float(os.environ.get("MU_ADMISSION_WAIT_SECONDS", "4")))


def chunk_lines(text, size):
    """Keep exact text and line boundaries; split an oversized line to bound judge input."""
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        if current and len(current) + len(line) > size:
            chunks.append(current)
            current = ""
        while len(line) > size:
            chunks.append(line[:size])
            line = line[size:]
        current += line
    if current:
        chunks.append(current)
    return chunks


@dataclass
class AdmissionPlan:
    chunks: list
    outcomes: list
    record: dict

    def render(self, artifact_id):
        if not self.record["applied_drop_chunks"]:
            return "".join(self.chunks)
        pieces, offset, run_start, kinds = [], 0, None, set()
        for index, chunk in enumerate(self.chunks):
            drop = 0 < index < len(self.chunks) - 1 and self.outcomes[index - 1]["drop"]
            if drop:
                if run_start is None:
                    run_start = offset
                kinds.add(self.outcomes[index - 1]["kind"])
            else:
                if run_start is not None:
                    pieces.append(f"\n[Semantic admission: {'/'.join(sorted(kinds))}; UTF-8 bytes "
                        f"{run_start}..{offset} omitted. Full text id: {artifact_id}; "
                        "read_tool_output(id, offset, limit) retrieves the original bytes.]\n")
                    run_start, kinds = None, set()
                pieces.append(chunk)
            offset += len(chunk.encode("utf-8"))
        return "".join(pieces)


class OutputAdmission:
    PASS_THROUGH = {"read_file", "edit_file", "write_file", "read_tool_output", "read_command_output"}

    def __init__(self, judge, settings=None):
        self.judge = judge
        self.settings = settings or AdmissionSettings()
        self._cache = OrderedDict()

    def plan(self, text, tool_call, *, is_error=False, has_images=False, cancel=None, on_judgment=None):
        policy = self.judge.policy_for(TOOL_ADMISSION)
        name = tool_call.get("name", "")
        if (policy.mode == "off" or name in self.PASS_THROUGH or is_error or has_images
                or not self.settings.min_chars <= len(text) <= self.settings.chunk_chars * self.settings.max_chunks):
            return None
        if cancel is not None:
            cancel.raise_if_cancelled()
        chunks = chunk_lines(text, self.settings.chunk_chars)
        if not 3 <= len(chunks) <= self.settings.max_chunks:
            return None
        call = name + ": " + str(tool_call.get("description", ""))[:400]
        key = hashlib.sha256(json.dumps([call, text, policy.mode, policy.routes, policy.min_confidence],
                                       ensure_ascii=False).encode("utf-8")).hexdigest()
        if key in self._cache:
            result = copy.deepcopy(self._cache[key])
            result.record["cached"] = True
            result.record["tool_call_id"] = tool_call.get("id")
            result.record["latency_ms"] = 0.0
            self._cache.move_to_end(key)
            return result
        middle = chunks[1:-1]
        outcomes = []
        proposed = []
        start = time.monotonic()
        deadline = start + self.settings.wait_seconds
        cursor = 0
        while cursor < len(middle) and time.monotonic() < deadline:
            batch, size = [], 0
            for chunk in middle[cursor:cursor + self.settings.batch_chunks]:
                length = len(json.dumps(chunk, ensure_ascii=False).encode("utf-8"))
                if batch and size + length > 48_000:
                    break
                batch.append(chunk)
                size += length
            latest = []
            outcomes.extend(self.judge.decide(TOOL_ADMISSION, {"tool": name, "call": call, "chunks": batch},
                cancel=cancel, on_record=lambda record: latest.append(record), deadline=deadline))
            record = latest[0]
            proposed.extend(record.get("judged") or [{"kind": "unknown", "drop": False} for _ in batch])
            if on_judgment is not None:
                on_judgment({**record, "tool_call_id": tool_call.get("id"), "chunk_offset": cursor + 1})
            cursor += len(batch)
        if cancel is not None:
            cancel.raise_if_cancelled()
        expired = cursor != len(middle) or time.monotonic() >= deadline
        if expired:
            outcomes = [{"kind": "unknown", "drop": False} for _ in middle]
        dropped = [index for index, outcome in enumerate(outcomes) if outcome["drop"]]
        record = {"tool_call_id": tool_call.get("id"), "tool": name, "mode": policy.mode,
            "status": "timeout" if expired else "completed", "cached": False,
            "chunks": len(chunks), "proposed_drop_chunks": sum(item["drop"] for item in proposed),
            "applied_drop_chunks": len(dropped),
            "omitted_bytes": sum(len(middle[index].encode("utf-8")) for index in dropped),
            "latency_ms": round((time.monotonic() - start) * 1000, 1)}
        result = AdmissionPlan(chunks, outcomes, record)
        # Cache only completed plans. Transient outages and timeouts get a fresh
        # attempt in a later preflight; completed accepted decisions never mutate history.
        if not expired and any(item["kind"] != "unknown" for item in proposed):
            self._cache[key] = copy.deepcopy(result)
            while len(self._cache) > 128:
                self._cache.popitem(last=False)
        return result
