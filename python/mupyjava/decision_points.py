"""Built-in action hooks. Only tool.intent inherits the legacy global mode."""

from .judge import DecisionPoint, DecisionRegistry


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


def builtin_registry() -> DecisionRegistry:
    registry = DecisionRegistry()
    for point in ACTION_POINTS:
        registry.register(point)
    return registry
