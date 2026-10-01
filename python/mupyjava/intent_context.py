"""Source-grounded context for a future, separately reviewed tool-intent input."""

import copy
import hashlib
import json


CONTEXT_SCHEMA = 1
CONTEXT_POINT = "tool.intent.context"
CONTEXT_VERSION = 1
CONTEXT_QUESTION = (
    "Does the user's current request, considered with the recorded task goal and "
    "verbatim user constraints, clearly call for this proposed tool action? "
    "Treat the goal excerpt as background, not extra permission. If decisive "
    "context is missing, abstain."
)


def capture_context(prompt, frame):
    """Attach private source evidence to an unchanged v2 judge sample."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Current user message is required")
    return {"schema": CONTEXT_SCHEMA, "latest_user_message": prompt,
            "latest_user_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "task_frame": copy.deepcopy(frame.payload())}


def context_state(v2_state, evidence):
    """Build the full candidate input; token-fit is checked before any inference."""
    if not isinstance(v2_state, dict) or not isinstance(evidence, dict) or (
            evidence.get("schema") != CONTEXT_SCHEMA):
        raise ValueError("Missing versioned context evidence")
    prompt = evidence.get("latest_user_message")
    frame = evidence.get("task_frame")
    if (not isinstance(prompt, str) or not prompt.strip() or
            evidence.get("latest_user_sha256") != hashlib.sha256(prompt.encode("utf-8")).hexdigest() or
            not isinstance(frame, dict) or frame.get("schema") != 1 or
            not isinstance(frame.get("goal"), str) or len(frame["goal"]) > 600 or
            not isinstance(frame.get("constraints"), list) or
            len(frame["constraints"]) > 32 or
            type(frame.get("turn")) is not int or frame["turn"] < 1 or
            type(frame.get("version")) is not int or frame["version"] < 1 or
            frame.get("current_subgoal") not in ("", prompt.strip()[:300]) or
            (frame["current_subgoal"] == "" and frame["goal"] != prompt.strip()[:600]) or
            v2_state.get("user_request") != prompt[:1000] or
            not isinstance(v2_state.get("tool"), str) or
            not isinstance(v2_state.get("arguments"), dict)):
        raise ValueError("Context evidence does not match the current v2 decision")
    constraints = []
    for item in frame["constraints"]:
        if (not isinstance(item, dict) or
                set(item) != {"text", "source_turn", "partial"} or
                not isinstance(item["text"], str) or
                not 1 <= len(item["text"]) <= 1000 or
                type(item["source_turn"]) is not int or
                not 1 <= item["source_turn"] <= frame["turn"] or
                type(item["partial"]) is not bool):
            raise ValueError("Invalid recorded user constraint")
        constraints.append({"text": item["text"], "source_turn": item["source_turn"],
                            "partial": item["partial"]})
    # Put the proposed action first. The exact state is rejected for inference
    # if it exceeds the serving tokenizer's budget; do not silently clip rules.
    return {"tool": v2_state["tool"],
            "arguments": copy.deepcopy(v2_state["arguments"]),
            "latest_user_message": prompt,
            "user_constraints": constraints,
            "task_goal_excerpt": frame["goal"]}


def state_digest(state):
    text = json.dumps(state, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def serialized_state_digest(state):
    """Hash ordered JSON; the tokenizer audit checks it against Laya's serializer."""
    text = json.dumps(state, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
