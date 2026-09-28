"""Built-in action hooks. Only tool.intent inherits the legacy global mode."""

from .judge import DecisionPoint, DecisionRegistry, DecisionSpec


TOOL_INTENT = DecisionPoint(
    "tool.intent", 2,
    "Does the user's latest request clearly call for this tool action?", True,
)

# These are mu-pyjava probes, not copies of mu's multi-question tool.risk
# and per-constraint tool.constraint specifications.
TOOL_REVIEW = DecisionPoint(
    "tool.review", 1,
    "Review the proposed action against the latest user request. Choose decline only for a clearly "
    "forbidden or unrelated action; continue for a relevant action; unknown if evidence is insufficient. "
    "Continuing never grants permission and still requires the existing action approval.",
    "continue", kind="choice", choices=("continue", "decline", "unknown"), default_mode="off",
)

TOOL_RISK_SCORE = DecisionPoint(
    "tool.risk_score", 1,
    "Estimate the potential destructive impact of this action, from 0 (no meaningful destructive "
    "impact) to 1 (severe irreversible damage). Use null if the supplied metadata is insufficient. "
    "This score is observational and cannot authorize or reject execution.",
    0.5, kind="score", default_mode="off", allowed_modes=("off", "shadow"),
)

ACTION_POINTS = (TOOL_INTENT, TOOL_REVIEW, TOOL_RISK_SCORE)


TASK_FRAME = DecisionPoint(
    "task.frame", 1,
    "What does user_message change in task_frame? Choose new_task for a different task, constraint "
    "for a new hard rule, correction for a correction to the approach, subgoal for the next step, "
    "none for chat/questions/go-ahead, or unclear when the effect is ambiguous.",
    "none", kind="choice", choices=("new_task", "constraint", "correction", "subgoal", "none", "unclear"),
    default_mode="off", min_confidence=0.6, escape_answers=True,
)


def _constraint_questions(inputs):
    constraints = inputs["constraints"]
    if (not isinstance(constraints, list) or len(constraints) > 6
            or any(not isinstance(text, str) or not text.strip() for text in constraints)):
        raise ValueError("Constraint check requires at most six verbatim instructions")
    return tuple(DecisionPoint(f"constraint_{index}", 1,
        'Does tool_call go against this instruction from the user? ' + repr(text[:300]), False)
        for index, text in enumerate(constraints))


def _constraint_state(inputs):
    return {"tool_call": inputs["tool"] + ": " + inputs["call"][:500]}


def _constraint_policy(answers, inputs):
    return {"broken": [index for index in range(len(inputs["constraints"]))
                       if answers.get(f"constraint_{index}", {}).get("answer") is True]}


def _constraint_fallback(inputs):
    return {"broken": []}


TOOL_CONSTRAINT = DecisionSpec("tool.constraint", 1, (), _constraint_policy, _constraint_fallback,
                               questions_for=_constraint_questions, build_state=_constraint_state, default_mode="off")


def _risk_state(inputs):
    return {"command": inputs["command"][:400], "user_message": inputs["user_request"][:400],
            "flag": inputs["flag"]}


def _risk_policy(answers, inputs):
    return "allow" if (answers.get("destructive", {}).get("answer") is False
                        or answers.get("requested", {}).get("answer") is True) else "confirm"


def _risk_fallback(inputs):
    return "confirm"


TOOL_RISK = DecisionSpec("tool.risk", 1, (
    DecisionPoint("destructive", 1, "Does command delete data or make a change that cannot be undone?", False),
    DecisionPoint("requested", 1, "Did user_message ask for what command does?", False),
), _risk_policy, _risk_fallback, build_state=_risk_state, default_mode="off")

ADMISSION_KINDS = ("error", "result", "progress", "warning", "passing", "unknown")
ADMISSION_NOISE = ("progress", "warning", "passing")


def _admission_questions(inputs):
    chunks = inputs["chunks"]
    if (not isinstance(chunks, list) or not 1 <= len(chunks) <= 32
            or any(not isinstance(chunk, str) or not 1 <= len(chunk) <= 1200 for chunk in chunks)):
        raise ValueError("Admission requires 1..32 bounded text chunks")
    return tuple(DecisionPoint(f"chunk_{index}", 1,
        f"Classify chunks[{index}] as error (any failure or stack trace), result (data, search results or "
        "file contents), progress (downloads/build status), warning (repeated warnings/deprecations), "
        "passing (passed tests/checks), or unknown. A mixed chunk containing an error or result must "
        "keep that class. This is output-kind classification, not an assessment of task relevance.",
        "unknown", kind="choice", choices=ADMISSION_KINDS) for index in range(len(chunks)))


def _admission_state(inputs):
    return {"call": inputs["call"][:400], "chunks": inputs["chunks"]}


def _admission_policy(answers, inputs):
    return [{"kind": answers.get(f"chunk_{index}", {}).get("answer", "unknown"),
             "drop": answers.get(f"chunk_{index}", {}).get("answer") in ADMISSION_NOISE}
            for index in range(len(inputs["chunks"]))]


def _admission_fallback(inputs):
    return [{"kind": "unknown", "drop": False} for _ in inputs.get("chunks", [])]


TOOL_ADMISSION = DecisionSpec("tool.admission", 3, (), _admission_policy, _admission_fallback,
    questions_for=_admission_questions, build_state=_admission_state, default_mode="off", min_confidence=0.9)

BUILTIN_POINTS = (*ACTION_POINTS, TASK_FRAME, TOOL_CONSTRAINT, TOOL_RISK, TOOL_ADMISSION)


def builtin_registry() -> DecisionRegistry:
    registry = DecisionRegistry()
    for point in BUILTIN_POINTS:
        registry.register(point)
    return registry
