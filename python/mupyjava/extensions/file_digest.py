"""Example read-only extension: SHA-256 for a bounded workspace file."""

import hashlib

from mupyjava.registry import ToolDefinition
from mupyjava.tool_result import ToolResult


def digest(arguments, context):
    path = context.resolve_path(arguments["path"])
    hasher = hashlib.sha256()
    total = 0
    with path.open("rb") as source:
        while True:
            context.check_cancelled()
            chunk = source.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 8 * 1024 * 1024:
                raise ValueError("Digest example accepts files up to 8 MiB")
            hasher.update(chunk)
    return ToolResult.from_text(hasher.hexdigest(), {"path": str(path.relative_to(context.root)),
                                                    "source_bytes": total, "sha256": hasher.hexdigest()})


def register_tools(registry):
    registry.register(ToolDefinition(
        "file_digest", "Calculate SHA-256 for a workspace file up to 8 MiB.",
        {"type": "object", "properties": {"path": {"type": "string", "minLength": 1}},
         "required": ["path"], "additionalProperties": False}, digest, effect="read"))
