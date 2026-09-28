# Task frames and multi-question action policies

This slice follows mu's separate question-set and outcome-policy model at
`2dfd59c`. It adds actual `tool.constraint` and `tool.risk` policy hooks alongside
the earlier project-specific review and score probes. All new judge points
default to **off**. Task-state infrastructure is available in every mode.

## Keeping user instructions

The first user message establishes a bounded goal. Each user turn updates a
branch-local frame revision and current-step view. The frame stores the goal,
verbatim hard constraints with source user-turn numbers, and open questions.
It does not contain a generated plan or acceptance checklist.

An explicit directive can add a constraint without a judge, for example:

```text
Constraint: do not modify the database schema.
约束：不要修改配置文件。
/constraint Keep generated files unchanged.
```

These must start a line outside a fenced code block. The whole directive line is
retained as user text; there is no broad heuristic extraction of ordinary
negative sentences. For natural-language classification, enable `task.frame`.
After the first turn, its choice answer can classify a message as new_task,
constraint, correction, subgoal, none or unclear. Active constraint/correction
keeps the user's message verbatim; new_task changes the goal and retains existing
constraints; unclear retains the goal and records an open question. Shadow only
records the classification. Explicit directives work independently of mode.

`task.frame` defaults to a 0.6 confidence threshold. None and unclear are real
policy answers for this point; other choice points keep their escape/abstention
behavior. Our classifier uses the existing answer/confidence envelope. Mu pools
choice distributions and can invoke a separate generative frame writer; neither
distribution pooling nor that writer is implemented here.

Limits: goal 600 characters, current step 300, each constraint 1,000 with a
partial-text marker, 32 stored constraints and eight open questions. Duplicate
constraint text is not added again. Exceeding the constraint count rejects that
update with an error; create a new conversation to reset constraints. A normal
"Continue" retains them. This is a bounded rules-based frame, not a full task
summary or independently calibrated understanding of instructions.

## Tool constraint policy

For every mutation with retained constraints, an enabled `tool.constraint`
builds one boolean question per constraint, named `constraint_0`, etc. Like mu's
constraint feature, it checks the newest six. All stored constraints remain in
the task note sent to the main model and in the Java Task history; older ones
are not checked by this judge hook. Each question includes the first 300
characters of its user instruction.

An accepted true answer adds its index to `broken`. False, missing, malformed
and uncertain answers do not. Active broken indexes veto before intent review,
approval or execution, returning the user's sentence in the tool error so the
model can choose another approach or ask about the instruction. A judge outage
falls back to `{"broken": []}` and still reaches the existing permission gate.
Shadow records what would have been blocked and does not veto.

The submitted `tool_call` description is bounded to 500 characters. Commands
include their command text. File mutations keep our established privacy boundary:
only path/size/edit metadata, not new file content, is sent to this judge.
Mu includes the beginning of new text. Consequently content-specific prohibitions
may be undecidable here even when the path is known. Custom mutation tools use
their argument metadata. This is not a guarantee of enforcing every user rule.

## Command risk policy

Conservative rules flag recursive/forced deletion, discarded Git work, forced
pushes, database drops/truncation, device overwrites, recursive permission
opening, downloaded scripts and elevated execution, including PowerShell forms.
Rules inspect both the written command and a simple dequoted variant. They are
heuristics: quoted examples can be flagged, and variables, encodings or complex
scripts can evade them. They do not sandbox command execution.

Only flagged command/shell calls invoke an enabled `tool.risk`. The specification
asks two boolean questions:

1. **destructive:** does the command delete data or make an irreversible change?
2. **requested:** did the latest user message ask for what it does?

The aggregation follows mu: accepted destructive=false or requested=true yields
allow; otherwise confirm. An outage or no accepted answers defaults to confirm.
An allow outcome continues to existing human/tool permissions and never grants
access. Shadow records outcomes without adding a confirmation.

Active confirm requires a fresh explicit confirmation. The desktop uses its
existing approval dialog with the risk reason and exact command, offering only
deny/once. A confirmation satisfies the subsequent ordinary approval check for
the same request, tool-call ID, tool name and argument digest exactly once,
avoiding a second dialog. Changed actions, another request, cancellation, session
switching and shutdown cannot reuse it. Library callers must pass both the risk
confirmation callback and any normal approval callback they require; a denial
from either stops execution. Workspace tool permissions still apply.

Explicitly enabling active `tool.risk` adds this gate even with `--allow-command`.
The noninteractive CLI cannot ask, so confirm prevents command start. Defaults
retain existing broad-access flag behavior. Mu's full permission mode differs:
this project has not reproduced its three permission modes or automatic Jev
approval. No trained judge is enabled for these new points.

## Question sets and cascades

`DecisionSpec` has a static or input-dependent immutable tuple of typed questions,
a state builder, an aggregation function and a fallback function. A set contains
at most 32 distinct questions; empty sets use fallback without a backend call.
Question IDs belong to the parent spec and do not change the frozen registry.
Builders, policies and backend inputs receive isolated copies.

Named typed model backends can answer a set in one request:

```json
{"answers":{"destructive":{"probability":0.97},"requested":{"probability":0.08}}}
```

Boolean probability is P(true), mapped to true at p≥0.8, false at p≤0.2 and
unknown between them. Confidence is max(p, 1-p); the default minimum for both
action specs is 0.8. Choice/score answers use the existing answer/confidence
envelope. Responses reject duplicate or unknown IDs and malformed outer
envelopes. Within a valid envelope, a malformed or missing question can escalate
independently while accepted answers remain retained.

Routes ask only unresolved questions, in order. Partial accepted answers are
aggregated with unresolved answers treated as unknown by the built-in policies.
If nothing is accepted, the policy abstains or it fails, the declared fallback applies. Model
output grows with question count up to 2,048 tokens. Timeout, cancellation,
bounded workers, late-answer protection and Laya's intent-only/shadow-only guard
are shared with single-question decisions. This is synchronous shadow execution;
mu's background shadow checks, whole-feature wait limits, provider usage
accounting, capability-based routing and large-set request fan-out remain gaps.

## Configuration example

Use the same `MU_JUDGE_CONFIG` version-1 file documented in [JUDGING.md](JUDGING.md):

```json
{
  "version": 1,
  "default_mode": "off",
  "backends": {
    "semantic": {"type": "model", "model": "your-judge-model", "api_key_env": "MU_JUDGE_API_KEY"}
  },
  "points": {
    "task.frame": {"mode": "shadow", "routes": ["semantic"], "min_confidence": 0.6},
    "tool.constraint": {"mode": "shadow", "routes": ["semantic"], "min_confidence": 0.8},
    "tool.risk": {"mode": "shadow", "routes": ["semantic"], "min_confidence": 0.8}
  }
}
```

The model adapter supplies the question envelopes and asks for probabilities.
Neither Laya nor the legacy YES/NO `MU_JUDGE_MODEL` route can answer these new
specifications. Independently labeled evaluation remains necessary to judge
accuracy; the confidence number alone is not evidence of correctness.

## Storage, context and Java

`task.frame` session entries contain schema-1 frame snapshots after the source
user message. Restoration follows the selected ancestry; constraint text must
exist verbatim in its referenced user turn. Invalid snapshots cannot invent
constraints. Old journals can rebuild basic state from user messages and explicit
directives without making model calls. Selected completed-turn branches and
independent forks keep their own frames; a new conversation starts empty.

Constraints from an interrupted user turn are retained, matching the user's
recorded instructions, while its unfinished model/tool messages remain excluded
from restored chat context. Tools are never replayed and files are not rolled
back. Limits rejected during a turn do not prevent session recovery.

A derived task note is added before conversation messages in each model request
when needed. It remains mandatory when old turns are omitted by the context
budget; original canonical messages and tool archive source indexes are unchanged. All retained constraints are
included, with partial-text metadata. An oversized mandatory note can block a
request under the existing context overflow rules.

Java's Task tab receives `frame.detail` and restores `history.frame`. Its
Judgments tab restores schema-3 multi-question records as well as old records.
Multi records contain question IDs/types, accepted answers/probabilities/backends,
unresolved IDs, each route's asked IDs/reasons/timing, judged/applied outcomes and
fallback/source. Constraint records include frame revision and index offset.
The optional ledger still excludes raw state, question text and constraint
sentences; regular session journals retain user text as before. State-bearing
calibration replay remain future work. Semantic output-kind admission and grounded history excerpts are now implemented separately; see [SEMANTIC_CONTEXT.md](SEMANTIC_CONTEXT.md).

## Source anchors and validation

- [Constraint specification](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/kyrn-judge/src/decisions/tool-constraint.ts) and [constraint feature](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/kyrn-judge/src/extension/features/constraints.ts).
- [Risk specification](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/kyrn-judge/src/decisions/tool-risk.ts) and [command guard](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/kyrn-judge/src/extension/features/guard.ts).
- [Task classification](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/kyrn-judge/src/decisions/task-frame.ts) and [frame rules/storage](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/kyrn-judge/src/frame/frame.ts).

Tests cover dynamic/typed question sets, partial-answer routing, risk truth tables,
constraint veto/outage fallback, confidence/escape semantics, exact confirmation
binding, cancellation and late answers, frame provenance, omitted context,
branch/fork/new/interrupted recovery and strict configuration restrictions.
Actual Python and Java processes use a local HTTP fixture to verify constraint
retention across restart and fresh risk confirmation despite broad command
permission. No live model accuracy result or new training is claimed.
