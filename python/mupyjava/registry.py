"""Trusted Python tool extensions with explicit effects and validated arguments."""

import copy
import importlib
import json
import math
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional

from .cancel import CancellationToken
from .capabilities import ModelCapabilities
from .tool_result import ToolResult


_SCHEMA_KEYS = {"type", "properties", "required", "additionalProperties", "items", "enum",
                "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems",
                "description", "title", "default"}
_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


def validate_schema(schema):
    """Fail closed on unsupported JSON Schema features rather than ignoring them."""
    if (not isinstance(schema, dict) or set(schema) - _SCHEMA_KEYS
            or not isinstance(schema.get("type"), str) or schema["type"] not in _TYPES):
        raise ValueError("Tool parameters use an unsupported schema")
    kind = schema["type"]
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise ValueError("enum must be a nonempty list")
    allowed = {"type", "description", "title", "default", "enum"}
    allowed |= {"properties", "required", "additionalProperties"} if kind == "object" else set()
    allowed |= {"items", "minItems", "maxItems"} if kind == "array" else set()
    allowed |= {"minLength", "maxLength"} if kind == "string" else set()
    allowed |= {"minimum", "maximum"} if kind in {"integer", "number"} else set()
    if set(schema) - allowed:
        raise ValueError("Schema constraints do not match their type")
    if kind == "object":
        props, required = schema.get("properties", {}), schema.get("required", [])
        if (not isinstance(props, dict) or any(not isinstance(key, str) for key in props)
                or not isinstance(required, list) or any(not isinstance(key, str) or key not in props for key in required)
                or not isinstance(schema.get("additionalProperties", True), bool)):
            raise ValueError("Invalid object schema")
        for child in props.values():
            validate_schema(child)
    if kind == "array":
        validate_schema(schema.get("items"))
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in schema and (isinstance(schema[key], bool) or not isinstance(schema[key], int) or schema[key] < 0):
            raise ValueError("Invalid length constraint")
    for key in ("minimum", "maximum"):
        if key in schema and (isinstance(schema[key], bool) or not isinstance(schema[key], (int, float))
                              or isinstance(schema[key], float) and not math.isfinite(schema[key])):
            raise ValueError("Invalid numeric constraint")
    for minimum, maximum in (("minimum", "maximum"), ("minLength", "maxLength"), ("minItems", "maxItems")):
        if minimum in schema and maximum in schema and schema[minimum] > schema[maximum]:
            raise ValueError("Schema bounds are reversed")
    json.dumps(schema, allow_nan=False)


def validate_arguments(value, schema, path="arguments"):
    kind = schema["type"]
    matches = {"object": isinstance(value, dict), "array": isinstance(value, list),
               "string": isinstance(value, str), "integer": isinstance(value, int) and not isinstance(value, bool),
               "number": isinstance(value, (int, float)) and not isinstance(value, bool),
               "boolean": isinstance(value, bool), "null": value is None}
    if not matches[kind]:
        raise ValueError(f"{path} must be {kind}")
    if "enum" in schema and not any(type(value) is type(item) and value == item for item in schema["enum"]):
        raise ValueError(f"{path} is not an allowed value")
    if kind == "object":
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"{path}.{key} is required")
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("Argument keys must be strings")
            if key in properties:
                validate_arguments(child, properties[key], path + "." + key)
            elif schema.get("additionalProperties") is False:
                raise ValueError(f"{path}.{key} is not allowed")
    elif kind == "array":
        for index, item in enumerate(value):
            validate_arguments(item, schema["items"], f"{path}[{index}]")
    if kind in {"integer", "number"}:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{path} must be finite")
        if (("minimum" in schema and value < schema["minimum"])
                or ("maximum" in schema and value > schema["maximum"])):
            raise ValueError(f"{path} is outside its bounds")
    if kind in {"string", "array"}:
        low, high = ("minLength", "maxLength") if kind == "string" else ("minItems", "maxItems")
        if (low in schema and len(value) < schema[low]) or (high in schema and len(value) > schema[high]):
            raise ValueError(f"{path} has an invalid length")


@dataclass(frozen=True)
class ToolContext:
    root: Path
    resolve_path: Callable[[str], Path]
    cancel: Optional[CancellationToken]
    on_update: Optional[Callable[[str], None]]
    on_artifact: Optional[Callable[[str], None]]
    output_dir: Path
    capabilities: ModelCapabilities

    def check_cancelled(self):
        if self.cancel is not None:
            self.cancel.raise_if_cancelled()


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict
    execute: Callable[[dict, ToolContext], ToolResult]
    effect: str = "mutation"

    def __post_init__(self):
        if not isinstance(self.name, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", self.name):
            raise ValueError("Invalid tool name")
        if not isinstance(self.description, str) or not self.description.strip() or not callable(self.execute):
            raise ValueError("Tools need a description and callable execute")
        if self.effect not in {"read", "mutation"}:
            raise ValueError("effect must be read or mutation")
        validate_schema(self.parameters)
        if self.parameters["type"] != "object":
            raise ValueError("Tool parameters must be an object schema")
        object.__setattr__(self, "parameters", copy.deepcopy(self.parameters))

    def schema(self):
        return {"type": "function", "function": {"name": self.name,
                "description": self.description, "parameters": copy.deepcopy(self.parameters)}}


class ToolRegistry:
    def __init__(self, reserved_names=()):
        self._reserved = frozenset(reserved_names)
        self._definitions = {}
        self._frozen = False

    def register(self, definition: ToolDefinition):
        if self._frozen:
            raise ValueError("The tool registry is frozen for this agent")
        if not isinstance(definition, ToolDefinition):
            raise ValueError("Register a ToolDefinition")
        if definition.name in self._reserved or definition.name in self._definitions:
            raise ValueError("Tool name is already registered: " + definition.name)
        # Keep callable identity: copying a bound method can clone its backend
        # state or fail on connection/thread locks. Only schema data is copied.
        self._definitions[definition.name] = replace(definition)

    def freeze(self):
        self._frozen = True

    def resolve(self, name):
        definition = self._definitions.get(name)
        return replace(definition) if definition is not None else None

    def schemas(self):
        return [definition.schema() for definition in self._definitions.values()]

    def load_module(self, name: str):
        if self._frozen:
            raise ValueError("The tool registry is frozen for this agent")
        if not re.fullmatch(r"[a-zA-Z_]\w*(\.[a-zA-Z_]\w*)*", name):
            raise ValueError("Pass a Python module name, not a path")
        previous = dict(self._definitions)
        try:
            module = importlib.import_module(name)
            register = getattr(module, "register_tools", None)
            if not callable(register):
                raise ValueError("Tool module must export register_tools(registry)")
            register(self)
        except Exception as error:
            self._definitions = previous
            raise ValueError("Could not load trusted tool module: " + name) from error
