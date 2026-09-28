"""Strict JSON judge configuration; secrets are resolved from environment names."""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from .decision_points import ACTION_POINTS, builtin_registry
from .judge import (DecisionEngine, DecisionPolicy, LayaBooleanJudge,
                    LayaHttpBooleanJudge, ModelBooleanJudge, ModelTypedJudge)
from .model import ChatCompletionsModel


def _object(value: Any, keys: set, label: str) -> dict:
    if not isinstance(value, dict) or set(value) - keys:
        raise ValueError("Invalid fields in " + label)
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected nonempty text for " + label)
    return value


def _pairs(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate judge configuration key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("Nonfinite judge configuration value")


def _model(descriptor: dict, env: Dict[str, str]) -> ChatCompletionsModel:
    key_env = _text(descriptor.get("api_key_env", "MU_API_KEY"), "api_key_env")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
        raise ValueError("api_key_env must be an environment variable name")
    timeout = descriptor.get("timeout_seconds", 15)
    # Reuse the numeric bounds enforced by the routing policy.
    DecisionPolicy("off", timeout_seconds=timeout)
    return ChatCompletionsModel(
        _text(descriptor.get("api_base", env.get("MU_API_BASE", "https://api.openai.com/v1")), "api_base"),
        env.get(key_env, ""), _text(descriptor.get("model"), "model"),
        timeout=timeout, max_output_tokens=256,
    )


def engine_from_environment(ledger: Optional[Path] = None,
                            environ: Optional[Dict[str, str]] = None) -> DecisionEngine:
    env = dict(os.environ if environ is None else environ)
    config = {}
    if env.get("MU_JUDGE_CONFIG"):
        with Path(env["MU_JUDGE_CONFIG"]).open("r", encoding="utf-8") as file:
            content = file.read(1_048_577)
        if len(content) > 1_048_576:
            raise ValueError("Judge configuration exceeds 1 MiB")
        config = _object(json.loads(content, object_pairs_hook=_pairs, parse_constant=_invalid_constant),
                         {"version", "default_mode", "backends", "points"}, "judge configuration")
        if type(config.get("version")) is not int or config["version"] != 1:
            raise ValueError("Judge configuration version must be 1")

    mode = config.get("default_mode", env.get("MU_JUDGE_MODE", "off"))
    DecisionPolicy(mode)  # Validate even when every individual point is disabled.
    descriptors = config.get("backends", {})
    if not isinstance(descriptors, dict):
        raise ValueError("Judge backends must be an object")
    policies = {}
    registry = builtin_registry()
    point_config = config.get("points", {})
    if not isinstance(point_config, dict):
        raise ValueError("Judge points must be an object")
    for point_id, value in point_config.items():
        point = registry.resolve(point_id)
        value = _object(value, {"mode", "routes", "min_confidence", "timeout_seconds"}, point_id)
        routes = value.get("routes", ["default"])
        if not isinstance(routes, list):
            raise ValueError("Judge routes must be an array")
        policy = DecisionPolicy(value.get("mode", point.default_mode or mode), tuple(routes),
                                value.get("min_confidence", 0.0), value.get("timeout_seconds", 4.0))
        if policy.mode not in point.allowed_modes:
            raise ValueError("Mode is not allowed for " + point_id)
        policies[point_id] = policy

    legacy = [env.get(name) for name in ("MU_JUDGE_MODEL", "MU_JUDGE_LAYA_PATH", "MU_JUDGE_LAYA_URL")]
    if sum(bool(value) for value in legacy) > 1:
        raise ValueError("Set only one legacy judge backend")
    if any(legacy) and "default" in descriptors:
        raise ValueError("Duplicate default judge backend")
    descriptors = dict(descriptors)
    if legacy[0]:
        descriptors["default"] = {"type": "legacy_model", "model": legacy[0]}
    elif legacy[1]:
        descriptors["default"] = {"type": "laya", "checkpoint": legacy[1],
                                  "device": env.get("MU_JUDGE_LAYA_DEVICE")}
    elif legacy[2]:
        descriptors["default"] = {"type": "laya_http", "url": legacy[2]}

    # Validate every route before importing/loading a local model.
    for name, descriptor in descriptors.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", name):
            raise ValueError("Invalid judge backend name")
        descriptor = _object(descriptor, {"type", "model", "api_base", "api_key_env", "timeout_seconds",
                                          "url", "checkpoint", "device"}, "backend " + name)
        kind = descriptor.get("type")
        allowed = {"model": {"type", "model", "api_base", "api_key_env", "timeout_seconds"},
                   "legacy_model": {"type", "model", "api_base", "api_key_env", "timeout_seconds"},
                   "laya_http": {"type", "url", "timeout_seconds"},
                   "laya": {"type", "checkpoint", "device"}}
        if not isinstance(kind, str) or kind not in allowed or set(descriptor) - allowed[kind]:
            raise ValueError("Invalid judge backend descriptor")
        if kind == "legacy_model" and (name != "default" or not legacy[0]):
            raise ValueError("legacy_model is reserved for MU_JUDGE_MODEL")
        if kind in {"model", "legacy_model"}:
            _model(descriptor, env)
        elif kind == "laya_http":
            DecisionPolicy("off", timeout_seconds=descriptor.get("timeout_seconds", 15))
            LayaHttpBooleanJudge(_text(descriptor.get("url"), "url"))
        else:
            _text(descriptor.get("checkpoint"), "checkpoint")
            if descriptor.get("device") is not None:
                _text(descriptor["device"], "device")

    for point in ACTION_POINTS:
        policy = policies.get(point.id, DecisionPolicy(point.default_mode or mode))
        if policy.mode != "off" and not policy.routes:
            raise ValueError("Enabled decision points require a backend route")
        for route in policy.routes:
            if route not in descriptors:
                if policy.mode == "off" and point.id not in policies:
                    continue
                raise ValueError("Unknown judge backend: " + route)
            kind = descriptors[route]["type"]
            if policy.mode != "off" and kind in {"laya", "laya_http"}:
                if policy.mode == "active":
                    raise ValueError("The experimental Laya judge is shadow-only until independently validated")
                if point.id != "tool.intent":
                    raise ValueError("Laya only supports tool.intent")
            if policy.mode != "off" and kind == "legacy_model" and point.kind != "boolean":
                raise ValueError("MU_JUDGE_MODEL only supports boolean points; configure a typed model route")

    backends = {}
    for name, descriptor in descriptors.items():
        kind = descriptor["type"]
        if kind in {"model", "legacy_model"}:
            adapter = ModelTypedJudge if kind == "model" else ModelBooleanJudge
            backends[name] = adapter(_model(descriptor, env))
        elif kind == "laya_http":
            backends[name] = LayaHttpBooleanJudge(descriptor["url"], descriptor.get("timeout_seconds", 15))
        else:
            backends[name] = LayaBooleanJudge(descriptor["checkpoint"], descriptor.get("device"))
    registry.freeze()
    return DecisionEngine(mode, ledger=ledger, backends=backends, policies=policies, registry=registry)
