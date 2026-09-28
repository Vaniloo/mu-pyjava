"""Versioned structured tool results; model text remains compatible."""

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Tuple


@dataclass(frozen=True)
class TextContent:
    text: str

    def __post_init__(self):
        if not isinstance(self.text, str):
            raise ValueError("Text content must be a string")

    def to_payload(self):
        return {"type": "text", "text": self.text}


@dataclass(frozen=True)
class ToolResult:
    content: Tuple[TextContent, ...]
    details: Dict[str, Any] = field(default_factory=dict)
    is_error: bool = False

    def __post_init__(self):
        if not self.content or any(not isinstance(block, TextContent) for block in self.content):
            raise ValueError("Tool results need text content blocks")
        if not isinstance(self.details, dict) or not isinstance(self.is_error, bool):
            raise ValueError("Invalid tool result metadata")
        json.dumps(self.details, allow_nan=False)
        object.__setattr__(self, "content", tuple(self.content))
        object.__setattr__(self, "details", copy.deepcopy(self.details))

    @classmethod
    def from_text(cls, text: str, details=None):
        return cls((TextContent(text),), details or {})

    @classmethod
    def failed(cls, error: Exception):
        return cls((TextContent("Tool error: " + str(error)),), {"error_type": type(error).__name__}, True)

    @property
    def text(self) -> str:
        return "\n".join(block.text for block in self.content)

    def to_payload(self):
        return {"version": 1, "content": [block.to_payload() for block in self.content],
                "details": copy.deepcopy(self.details), "is_error": self.is_error}


def format_result_details(tool: str, result: dict) -> str:
    """Readable desktop summary while preserving full details in the journal."""
    details = result.get("details", {})
    if result.get("is_error"):
        return tool + ": " + details.get("error_type", "Error")
    parts = []
    if "exit_code" in details:
        parts.append("exit code " + str(details["exit_code"]))
    if "output_bytes" in details:
        parts.append(str(details["output_bytes"]) + " bytes")
    if "count" in details:
        parts.append(str(details["count"]) + " results")
    if details.get("truncated"):
        parts.append("output limited")
    if details.get("next_offset") is not None:
        parts.append("continue at " + str(details["next_offset"]))
    if details.get("artifact_id"):
        parts.append("full output " + details["artifact_id"])
    return tool + ": " + " · ".join(parts) if parts else ""
