"""Deterministic request projection; canonical history remains append-only."""

import copy
import hashlib
import json
import os
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from .capabilities import ModelCapabilities
from .model import prepare_messages


@dataclass(frozen=True)
class ContextSettings:
    max_tokens: int = 65_536
    reserve_tokens: int = 8_192
    tool_tokens: int = 8_192
    image_tokens: int = 4_096

    def __post_init__(self):
        for field in ("max_tokens", "reserve_tokens", "tool_tokens", "image_tokens"):
            value = getattr(self, field)
            minimum = 256 if field == "tool_tokens" else 1
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= 2_000_000:
                raise ValueError(f"{field} must be an integer from {minimum} to 2000000")
        if self.reserve_tokens >= self.max_tokens:
            raise ValueError("Context reserve must be smaller than the context window")

    @property
    def input_tokens(self):
        return self.max_tokens - self.reserve_tokens

    @classmethod
    def from_environment(cls):
        values = {}
        for field, name in (("max_tokens", "MU_CONTEXT_TOKENS"), ("reserve_tokens", "MU_RESPONSE_TOKENS"),
                            ("tool_tokens", "MU_TOOL_RESULT_TOKENS"), ("image_tokens", "MU_IMAGE_TOKENS")):
            raw = os.environ.get(name)
            if raw is not None:
                try:
                    values[field] = int(raw)
                except ValueError as error:
                    raise ValueError(name + " must be an integer") from error
        return cls(**values)


class ContextOverflow(ValueError):
    def __init__(self, record):
        self.record = record
        super().__init__("Latest request, tool arguments, images and tool definitions exceed the context budget. "
                         "Use a smaller request or fewer/smaller attachments, or increase MU_CONTEXT_TOKENS.")


@dataclass
class ContextProjection:
    messages: List[Dict[str, Any]]
    record: Dict[str, Any]
    artifacts: list


def validate_calls(calls):
    ids = []
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get("id"), str) or not call["id"]:
            raise ValueError("Tool calls need nonempty string IDs")
        ids.append(call["id"])
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate tool call IDs in one assistant response")
    return set(ids)


def validate_chain(messages):
    pending = set()
    for message in messages:
        role = message.get("role")
        if role == "tool":
            call_id = message.get("tool_call_id")
            if call_id not in pending:
                raise ValueError("Tool reply has no matching pending assistant call")
            pending.remove(call_id)
        else:
            if pending:
                raise ValueError("Assistant tool calls need replies before the next message")
            if role == "assistant":
                pending = validate_calls(message.get("tool_calls") or [])
    if pending:
        raise ValueError("Context ends with unanswered tool calls")


def format_context(record):
    if record.get("blocked"):
        return "Context budget exceeded; this model request was not sent."
    return (f"Context: about {record['estimated_tokens']} / {record['input_limit']} input tokens; "
            f"{record['reserve_tokens']} reserved for the answer. "
            f"{record['omitted_turns']} earlier turns omitted; "
            f"{len(record['shortened_tools'])} tool outputs shortened with full text available by ID.")


def context_status(record):
    return "\t".join(["v1", str(record["estimated_tokens"]), str(record["input_limit"]),
                     str(record["reserve_tokens"]), str(record["omitted_turns"]),
                     str(len(record["shortened_tools"])), "true" if record["blocked"] else "false"])


class ContextBudget:
    def __init__(self, settings=None):
        self.settings = settings or ContextSettings()
        self._cache = OrderedDict()
        self._emitted = set()

    def begin_turn(self):
        self._emitted.clear()

    def estimate(self, messages, schemas, capabilities=ModelCapabilities()):
        wire = prepare_messages(messages, capabilities)
        images = 0
        for message in wire:
            if isinstance(message.get("content"), list):
                for block in message["content"]:
                    if block.get("type") == "image_url":
                        images += 1
                        # Base64 is transport data; vision tokens depend on the provider.
                        block["image_url"] = {"url": "[image]"}
        text_bytes = len(json.dumps({"messages": wire, "tools": schemas},
                                   ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        return (text_bytes + 3) // 4 + 16 * len(wire) + images * self.settings.image_tokens

    @staticmethod
    def _marker(artifact_id):
        return ("\n[Context budget: middle of tool text omitted. Full text id: " + artifact_id +
                "; read_tool_output(id, offset, limit) retrieves UTF-8 bytes.]\n")

    @classmethod
    def _clip(cls, text, maximum_bytes, artifact_id):
        data = text.encode("utf-8")
        if len(data) <= maximum_bytes:
            return text
        marker = cls._marker(artifact_id)
        available = max(0, maximum_bytes - len(marker.encode("utf-8")))
        head = (available + 1) // 2
        tail = available - head
        return (data[:head].decode("utf-8", "ignore") + marker +
                (data[-tail:].decode("utf-8", "ignore") if tail else ""))

    def prepare(self, messages, schemas, directory: Path, capabilities=ModelCapabilities(), cancel=None):
        if cancel is not None:
            cancel.raise_if_cancelled()
        validate_chain(messages)
        starts = [index for index, message in enumerate(messages) if message.get("role") == "user"]
        if not starts:
            raise ValueError("Context needs a user request")
        prefix = messages[:starts[0]]
        if any(message.get("role") == "system" for message in messages[starts[0]:]):
            raise ValueError("System instructions must precede the conversation")
        if not prefix or any(message.get("role") != "system" for message in prefix):
            raise ValueError("Context must start with system instructions")
        limit = self.settings.input_tokens
        before = self.estimate(messages, schemas, capabilities)
        # Remove whole older turns before shaping current tool replies. The
        # system prompt and latest user request/assistant arguments stay intact.
        omitted = 0
        projected = copy.deepcopy(messages)
        def choose(start):
            selected = copy.deepcopy(prefix + messages[start:])
            if omitted:
                selected[0]["content"] += (f"\nContext note: {omitted} earlier completed turns were omitted "
                    "from this request by the context budget. No summary was generated; inspect the workspace "
                    "when historical facts are needed. This does not authorize additional actions.")
            return selected
        for start in starts[1:]:
            if self.estimate(projected, schemas, capabilities) <= limit:
                break
            omitted += 1
            projected = choose(start)
        original_start = starts[omitted]
        candidates = {}
        root = directory.resolve()
        # Plan IDs and text first. An impossible request must not create archives.
        for index, message in enumerate(projected):
            if message.get("role") != "tool" or not isinstance(message.get("content"), str):
                continue
            text = message["content"]
            data = text.encode("utf-8")
            key = (str(root), hashlib.sha256(data).hexdigest())
            cached = self._cache.get(key)
            artifact_id = cached if cached and (root / (cached + ".log")).is_file() else str(uuid.uuid4())
            candidates[index] = {"text": text, "bytes": len(data), "key": key, "id": artifact_id,
                                 "call_id": message["tool_call_id"], "reason": "tool_limit"}
            message["content"] = self._clip(text, self.settings.tool_tokens * 4, artifact_id)
        estimated = self.estimate(projected, schemas, capabilities)
        while estimated > limit:
            reducible = [(len(projected[index]["content"].encode("utf-8")), index)
                         for index, candidate in candidates.items()
                         if len(projected[index]["content"].encode("utf-8")) > len(self._marker(candidate["id"]).encode("utf-8"))]
            if not reducible:
                break
            size, index = max(reducible)
            candidate = candidates[index]
            minimum = len(self._marker(candidate["id"]).encode("utf-8"))
            maximum = max(minimum, size - max(4, (estimated - limit) * 4))
            projected[index]["content"] = self._clip(candidate["text"], maximum, candidate["id"])
            candidate["reason"] = "request_limit"
            estimated = self.estimate(projected, schemas, capabilities)
        shortened = []
        for index, candidate in candidates.items():
            if projected[index]["content"] != candidate["text"]:
                shortened.append({"tool_call_id": candidate["call_id"], "artifact_id": candidate["id"],
                    "source_message_index": original_start + index - len(prefix), "source_bytes": candidate["bytes"],
                    "admitted_bytes": len(projected[index]["content"].encode("utf-8")), "reason": candidate["reason"]})
        record = {"version": 1, "estimator": "utf8-json/4+message-overhead+image-reserve",
                  "max_tokens": self.settings.max_tokens, "reserve_tokens": self.settings.reserve_tokens,
                  "input_limit": limit, "estimated_before": before, "estimated_tokens": estimated,
                  "omitted_turns": omitted, "shortened_tools": shortened, "blocked": estimated > limit}
        if record["blocked"]:
            raise ContextOverflow(record)
        artifacts = []
        pending_ids = set()
        for item in shortened:
            candidate = next(value for value in candidates.values() if value["id"] == item["artifact_id"])
            artifact_id = candidate["id"]
            target = root / (artifact_id + ".log")
            if not target.exists():
                root.mkdir(parents=True, exist_ok=True, mode=0o700)
                if os.name != "nt":
                    root.chmod(0o700)
                descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                try:
                    with os.fdopen(descriptor, "wb") as output:
                        data = candidate["text"].encode("utf-8")
                        for offset in range(0, len(data), 65_536):
                            if cancel is not None:
                                cancel.raise_if_cancelled()
                            output.write(data[offset:offset + 65_536])
                        output.flush()
                        os.fsync(output.fileno())
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
            self._cache[candidate["key"]] = artifact_id
            self._cache.move_to_end(candidate["key"])
            while len(self._cache) > 1024:
                self._cache.popitem(last=False)
            if artifact_id not in self._emitted and artifact_id not in pending_ids:
                artifacts.append((candidate["call_id"], artifact_id))
                pending_ids.add(artifact_id)
        self._emitted.update(pending_ids)
        return ContextProjection(projected, record, artifacts)
