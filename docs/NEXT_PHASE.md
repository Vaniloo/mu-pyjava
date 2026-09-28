# Next phase: tool and architecture parity

Audit date: 2026-09-27. Compared with `qybaihe/mu` main at `2dfd59c`.
This is a behavior-oriented plan for the independent Python engine and Java desktop, not a source port. Upstream builds its judge extension on pi's agent/session runtime; we currently own only a small synchronous loop.

## Current state and gaps

| Area | mu behavior | mu-pyjava today | Gap |
| --- | --- | --- | --- |
| Tool access | Read, find, grep, list, edit, write, and shell/PowerShell have rich results, cancellation and operation interfaces; extensions add more tools. | Twelve built-ins plus registered Python tools cover text/image read/search, exact edit/write, Git inspection, direct process execution, Bash/PowerShell and full-output retrieval; results have versioned text/image content, details and error fields. Commands stream and can be stopped. File mutations have unified diff previews, revision checks, a per-path queue, atomic replacement and structured events. | Animated images, automatic model capability discovery, custom rendering hooks, native PowerShell validation and actual remote transport; file/search/Git/command interfaces are injectable. Tool-name counts hide these differences. |
| Permission boundary | Each tool call passes a permission gate. Modes and grants belong to a conversation; risky or protected operations are evaluated each time, with a human fallback. | The desktop now asks for each mutation, with a per-file grant where safe; CLI flags are explicit broad-access overrides. Active `tool.intent` can veto but cannot grant access; Laya remains shadow-only. | Configurable permission modes and more complete deterministic command-risk rules. |
| Session runtime | Append-only JSONL session tree, stable entries, resume/branch, tool events and cancellation. | Parent-linked append-only tree, selected-path restart, completed-turn selection, independent fork with artifacts, interruption markers and command lifecycle. | Arbitrary-entry tree navigation, cross-process locking and background tasks. |
| Context | Tool output is admitted in chunks and archived; stale context can be forgotten or compacted. | Tool result sent to the model is clipped at 60,000 characters; no archive or token budget. | Preserve complete output outside the prompt, expose retrieval, and add deterministic context budgeting before judge-led admission. |
| Judge | 35 decision points spanning input, context, tools, turns and teamwork; typed boolean/choice/score questions, routing/cascade, modes per point and ledger. | One Boolean `tool.intent` point, global off/shadow/active mode, optional Laya or LLM backend and JSONL ledger. | Typed registry, per-point policies and fallbacks, routing, calibration/replay and more decision points. The current Laya checkpoint has confident false positives, so it remains shadow-only. |
| Desktop/provider | mu desktop presents permissions, sessions and judgments, and bundles a runtime; the host supports multiple providers and model features. | Swing conversation and judgments tabs, approval dialog, session browser with selection/fork/restart, and one Chat Completions adapter. | Graphical tree, model settings, packaged runtime and provider streaming. |

## Implementation order

### 1. Establish the action boundary

Make the Python engine pause before each `write_file`, `edit_file` or `run_command`. Send Java a versioned `approval.request` with a unique tool-call ID, normalized workspace path or command arguments and an action preview. Accept `once`, a scoped `session` grant where safe, or `deny`; record the resolution. The desktop defaults to asking, while CLI launch flags remain explicit broad-access overrides for noninteractive use. With no interactive client, deny requests that cannot be approved. A denial becomes a tool result that tells the agent to continue without circumventing the decision. Revalidate the approved path immediately before execution.

Keep path containment and deterministic command-risk checks in Python. The judge may recommend an outcome only after those rules run; uncertainty or judge failure asks the user. Protected paths and flagged commands require a fresh answer, even after a session grant. Do not enable automatic Laya approval until independent labels show that its false-positive rate is acceptable for every mutating tool.

**Acceptance:** each action is shown before execution; deny leaves files untouched and prevents process start; answers are tied to the exact call ID; malformed, late and duplicate answers never execute a call; Java closing or disconnecting while approval is pending denies it; the existing read-only flow still works.

### 2. Make turns and tool calls recoverable

Introduce a versioned event envelope (`session_id`, `turn_id`, `tool_call_id`, `event_id`, type, payload) and append-only local session storage. Persist user/assistant/tool messages, approval requests and resolutions, and judge references. Restore a completed session after restart; mark any interrupted call as interrupted rather than replaying a side effect. Add cancel for an active turn and process group.

**Acceptance:** restart restores transcript and model context; a previously approved write/command is never rerun during replay; a cancelled command stops and emits a terminal event; the Java UI can show the pending action after a reconnect without approving it implicitly.

### 3. Close core tool-behavior gaps

Add a shell tool with streamed, bounded output and explicit timeout/cancel; retain full output in a temporary artifact with a retrieval pointer. Add diff previews and serialized per-file mutations, then atomic writes. Extend `read_file` to image attachments once the model adapter can carry multimodal content. Give each tool a pluggable operations interface so the same agent can run local or remote implementations. PowerShell is the Windows implementation of the command interface.

**Acceptance:** long output stays bounded in memory and remains retrievable; cancel kills the process tree; two edits to one file execute in order; failed writes do not leave a partial target; image reads are omitted clearly for text-only models; local and remote operations share the same result schema.

### 4. Expand the judgment kernel

Add typed question and answer schemas, per-point mode/routing and deterministic fallback. Implement `tool.risk`, `tool.constraint`, `tool.approval`, and `tool.admission` against the new action and output events. Validate with independently labeled examples, including `edit_file`, and replay ledger records before moving any point to active. Then add turn completion and context decisions.

**Acceptance:** every decision records spec/version, backend, answer, confidence, fallback reason and latency; missing or malformed answers follow the declared fallback; active decisions cannot bypass the deterministic permission floor; the trained Laya stays shadow-only until measured evidence supports a specific point.

## Immediate next slice

The approval gate, durable tree sessions, dedicated judgment tab, active-turn Stop action and streamed command output are implemented. Long output is retained behind an ID and can be paged back after restart. File edit supports multiple disjoint replacements with BOM/CRLF preservation; write/edit use approved diffs and atomic serialized replacement. File, search, Git and command operations are injectable; the full alternate-backend agent workflow is tested with shared permissions and output artifacts. Actual SSH transport remains separate. Bash/PowerShell and a common structured text-result contract are implemented. Image blocks, bounded model-aware conversion and explicit custom tool registration are now implemented. Standard patch/navigation metadata and explicit fuzzy normalization with fresh approval are also implemented; exported patches are replay-verified and Java restores location/mode summaries. Session selection/forking and the Java browser are now implemented, including old-format compatibility, copied output artifacts and branch-aware interruption recovery. Next: deterministic context budgets, followed by typed judge policies. See [EDITING.md](EDITING.md). Arbitrary-entry tree navigation and provider-side HTTP abort remain future work. See [SESSIONS.md](SESSIONS.md). See [MU_COMPARISON.md](MU_COMPARISON.md) and [OPERATIONS.md](OPERATIONS.md).

## Source anchors

- [mu turn and decision-point map](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/README.md)
- [mu permission gate](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/kyrn-judge/src/extension/features/permissions.ts)
- [mu core tools](https://github.com/qybaihe/mu/tree/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/tools)
- [mu session manager](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/session-manager.ts)
- [mu decision specification](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/kyrn-judge/src/decision.ts)
