# Typed decisions and backend routing

The Python kernel supports immutable, versioned `boolean`, `choice` and `score`
specifications. Each specification declares its fallback and allowed modes. The
agent registers its action points and freezes the registry before running tools;
conflicting revisions or unknown point IDs are rejected. Trusted Python clients
can register their own points before freezing a separate registry.

## Action hooks

| Point | Type | Default | Outcome applied by the agent |
| --- | --- | --- | --- |
| `tool.intent` v2 | boolean | Inherits `MU_JUDGE_MODE`, otherwise off | Active false vetoes the mutation. Fallback true continues to existing permission checks. |
| `tool.review` v1 | choice: continue / decline / unknown | off | Active decline vetoes. An escape answer or outage falls back to continue, preserving existing permission checks. |
| `tool.risk_score` v1 | score within 0–1 | off; only off/shadow allowed | Observational only. Fallback 0.5 is a placeholder, not a measured risk estimate. |

Hooks cover writes, edits, commands, shells and registered mutation tools. They
do not run for read-only calls. A veto stops later hooks for that action. File
content is replaced with path and size metadata for write/edit judgments; the
latest request is limited to 1,000 characters. Command and custom-tool arguments
are sent to the configured judge. This is limited evidence: there is no retained
task frame, complete file diff, extracted hard-constraint list or full dialogue
in the judgment input.

The two new probes are **mu-pyjava specifications**, not copies of mu's
`tool.constraint` or `tool.risk`. Mu asks a boolean question per hard constraint;
its risk specification combines two boolean questions into allow/confirm. Those
multi-question specifications and their task-frame integration remain future work.

## Configuration

The existing `MU_JUDGE_MODE` plus one of `MU_JUDGE_MODEL`, `MU_JUDGE_LAYA_PATH` or
`MU_JUDGE_LAYA_URL` still works for `tool.intent`. `MU_JUDGE_MODEL` retains the
legacy YES/NO/UNKNOWN adapter. Named `model` routes use the typed JSON adapter.

Set `MU_JUDGE_CONFIG` to the absolute path of a JSON file, for example:

```json
{
  "version": 1,
  "default_mode": "off",
  "backends": {
    "trained": {
      "type": "laya_http",
      "url": "http://127.0.0.1:18765",
      "timeout_seconds": 15
    },
    "semantic": {
      "type": "model",
      "model": "your-judge-model",
      "api_key_env": "MU_JUDGE_API_KEY",
      "timeout_seconds": 15
    }
  },
  "points": {
    "tool.intent": {
      "mode": "shadow",
      "routes": ["trained", "semantic"],
      "min_confidence": 0.8,
      "timeout_seconds": 4
    },
    "tool.review": {
      "mode": "shadow",
      "routes": ["semantic"],
      "min_confidence": 0.8,
      "timeout_seconds": 4
    },
    "tool.risk_score": {
      "mode": "shadow",
      "routes": ["semantic"],
      "min_confidence": 0.8,
      "timeout_seconds": 4
    }
  }
}
```

The model backend uses `MU_API_BASE` unless `api_base` is supplied; credentials
are read only from the named environment variable. With no `api_key_env`, it
uses `MU_API_KEY`. A configuration cannot contain a literal API key. Unknown
fields, duplicate JSON keys, nonfinite values, unsupported modes, unknown points
and missing routes fail at startup. Enabled points require a nonempty route
list. A legacy environment backend is named `default`; defining another backend
with that name at the same time is rejected. A global/default mode applies only
to points that inherit it: enabling `tool.intent` does not enable the new probes.

Backend types are `model`, `laya_http` and `laya` (the latter has `checkpoint`
and optional `device`). Laya HTTP remains loopback-only. Both Laya adapters are
restricted to `tool.intent` and off/shadow, including when created directly with
the Python API or selected in a cascade. The trained checkpoint has known
confident false positives and has not been independently calibrated for v2,
edits, shells or custom tools. No training or calibration is included in this slice.

## Validation and route behavior

Named model backends request exactly:

```json
{"answer": "continue", "confidence": 0.9}
```

`answer` must match the registered type, or be null to abstain. Booleans are
actual JSON booleans, choices must be declared literals and scores must be finite
and in range. Strings/numbers cannot masquerade as booleans; booleans cannot
masquerade as scores. No Markdown fences, extra fields or duplicate fields are
accepted. Output is capped at 256 model tokens. Each submitted state is bounded
to 64 KiB of serialized UTF-8 JSON.

Routes are tried in order. Invalid output, exceptions, timeouts, null/escape
answers and insufficient confidence advance to the next route. A confidence
threshold above zero also rejects answers without confidence. For Laya,
confidence is `max(p, 1-p)` with the existing p≤0.2 / p≥0.8 three-zone answer.
For a model, confidence is self-reported. Neither measure is an independent
accuracy guarantee. A confident wrong answer will **not** escalate automatically.

If no route produces an accepted answer, the declared fallback applies. Off
never calls a backend. Shadow records the accepted answer but applies the
fallback. Active applies an accepted answer; mutation hooks can only veto.
Human approvals, scoped grants, workspace containment and deterministic tool
permissions still run. The CLI's existing broad-access flags retain their meaning.

The policy timeout is per route, separate from the backend's HTTP timeout. Stop
interrupts the waiting turn within the polling interval, and late answers cannot
change its record or execute tools. Python cannot abort the underlying inference
or HTTP request; at most two outstanding workers are allowed per named backend.
Further calls to a busy backend fall through. A cancelled wait produces the
existing turn interruption event, not a fabricated completed judgment record.

## Records and Java history

Each completed decision produces a schema-v2 record with point/revision/type,
mode, accepted answer/confidence/probability, applied outcome/source, selected
backend, ordered attempt reasons/timings, fallback reason and total latency.
The optional JSONL ledger contains metadata, not state, file content, raw invalid
responses or exception messages. Ledger file errors do not alter tool behavior;
they are reported by `ledger_failure`. Normal session journals still contain
prompts, tool arguments and results, as before.

The server saves an explicit snapshot for each enabled hook. Java's existing
Judgments panel receives the full formatted record and restores it through
selected-path history after restart or fork. Old boolean records remain readable.
Live callbacks receive copies and cannot change the engine's record. JSONL
records do not contain enough input to rerun judgments: state-bearing replay and
independent label/calibration tooling remain future work.

## Upstream comparison and validation

Compared with mu at `2dfd59c`:

- [Decision specification and engine](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/kyrn-judge/src/decision.ts): typed specs, modes, routes and fallbacks inform this kernel. Dynamic question sets, policy aggregation, usage accounting, capability-based routing and judge batching are not reproduced.
- [Policies](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/kyrn-judge/src/policy.ts): three-zone booleans and escape options inform validation. Our scores use explicit numeric bounds; mu's scores use rubric levels.
- [Constraint](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/kyrn-judge/src/decisions/tool-constraint.ts) and [risk](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/kyrn-judge/src/decisions/tool-risk.ts): actual specifications remain gaps; the new probes do not count as their reproduction.

Tests cover typed validation, frozen registry conflicts, per-point modes,
low-confidence/escape/error/timeout cascades, isolated state, late answers,
cancellation before approval/execution, bounded workers, ledger failures,
strict configuration, non-bypassable Laya restrictions and the human/tool
permission floor. A local HTTP fixture verifies typed model requests through
the real Python server and Java client, including saved history on restart.
This verifies harness behavior; it does not measure real judge accuracy.
