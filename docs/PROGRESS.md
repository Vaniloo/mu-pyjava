# Progress

This file records what the repository actually does. The goal is a Python agent engine with a Java desktop, inspired by [mu](https://github.com/qybaihe/mu).

## Milestone 0 — runnable foundation

- [x] Python package with a bounded model/tool loop.
- [x] Chat Completions model adapter and an explicit offline echo adapter.
- [x] Read/list tools; write and command tools gated by launch flags.
- [x] Find, literal grep, exact text edit, and read-only Git status/diff tools, with workspace path checks.
- [x] Paged UTF-8 reads, ripgrep-backed regex/context search and file discovery, and bounded command output with configurable timeout.
- [x] Versioned boolean decision point engine with off/shadow/active modes and optional JSONL ledger.
- [x] Java Swing window and a long-lived Python process connected through a line protocol.
- [x] Python unit tests, Java compilation, and cross-language smoke test.

## Next milestones

The ordered gap audit and acceptance criteria are in [NEXT_PHASE.md](NEXT_PHASE.md). Approval, restart recovery, cancellable streamed commands and safe text mutations are implemented; the next slice is broader operations and architecture parity.

- [x] Train and evaluate a Laya multilingual fine-tune for the first judge decision; add optional shadow-mode loading.
- [x] Connect a lab-hosted Laya checkpoint through a loopback HTTP service and SSH tunnel; show shadow verdicts and probabilities in transcripts and ledger.
- [x] Exercise the real Python agent and Java process protocol with DeepSeek Flash plus the lab judge in shadow mode.
- [x] Gate individual desktop write, edit and command calls through a versioned approval request/response protocol; deny pending actions on disconnect.
- [x] Persist completed turns in a versioned local session journal, restore the selected session on restart, and show saved judge records in a dedicated Java tab.
- [x] Add a desktop Stop action, process-tree cancellation, live bounded command output and a full-output retrieval tool.
- [x] Show unified diffs before write/edit approval, recheck the approved revision, serialize same-path mutations, replace atomically, and record structured change events.
- [x] Compare mu's current tool and architecture contracts; extend edit to multiple disjoint replacements while preserving BOM and CRLF.
- [x] Route read/list through injectable operations; verify paging and multi-edit with an in-memory workspace that has no local target file.
- [x] Extend operation injection to find/grep, read-only Git and streamed commands; verify a complete alternate-backend agent workflow with common approvals, output artifacts and cancellation.
- [x] Add approved Bash/PowerShell tools and unified content/details/error results, with saved Java metadata and shell lifecycle tests. Native PowerShell awaits an installed platform.
- [x] Add bounded image reading/conversion with explicit model capabilities, correct Chat Completions attachment ordering and completed-session restoration.
- [x] Add trusted custom-tool modules with validated schemas, fresh mutation approvals, judge/cancel/result handling and Java process coverage.
- [x] Add replayable standard patches, numbered display diffs and navigation metadata; implement explicit fuzzy normalization with fresh approvals, ambiguity checks, untouched-line preservation and Java history coverage.
- [ ] Collect independently labeled decisions for all mutating tools, including `edit_file`, and resolve confident false positives before enabling the trained judge.
- [x] Add parent-linked session trees, completed-turn selection, independent forks with copied output artifacts, selected-path restart and a Java session browser.
- [x] Add deterministic request/tool/image budgets, immutable canonical history, full-text admission archives, general retrieval and Java Context history/status.
- [ ] Add provider usage calibration, summarized/task-preserving compaction, semantic chunk admission, provider-side HTTP request abort and background jobs.
- [x] Typed boolean/choice/score registry, per-point modes and ordered backend routing, strict answers, bounded timeout/cancel and individual saved records.
- [ ] Mu's dynamic multi-question constraint/risk policies, independently calibrated decisions and semantic output admission.
- [ ] LSP diagnostics and project-specific build/test guidance.
- [ ] Robust packaging of Python and Java runtimes for each platform.
- [ ] Add GitHub Actions after a GitHub credential with workflow permission is available.
- [ ] Browser, sub-agents, and memory after the single-agent workflow is stable.

## Current boundaries

The echo adapter only checks wiring. An API-backed run needs a configured Chat Completions endpoint and model. The Java window is a development shell, not a packaged desktop release. The trained judge weights are kept on the lab host and are not bundled with this repository. No provider sign-in flow or automatic language-server installation exists yet.

## Milestone 8 — branch sessions and independent copies

Python journals now retain sibling paths and restore the selected ancestry. The Java browser can select a completed turn or copy its prefix; tools are never replayed, files keep their current state and permission grants reset. Old linear journals remain readable without rewriting. Forks include image blocks, judgments and independent output archives; incomplete copies stay outside the catalog. See [SESSIONS.md](SESSIONS.md) for the protocol, recovery behavior, display limits and remaining gaps.

## Milestone 9 — request budgets and tool text admission

Every model preflight now estimates the request including tool definitions/arguments and configured image costs, reserves the answer limit, omits earlier whole turns when needed and shortens tool text with archive pointers. Canonical history is preserved; impossible mandatory input blocks that request. The Java Context tab/status restores selected-path records, and archived text remains readable after independent forks. Full validation ran 95 tests (94 passed; native PowerShell skipped). The estimate is heuristic and whole-turn omission has no task summary; semantic Jev admission and generated compaction summaries remain gaps. See [CONTEXT.md](CONTEXT.md).

## Milestone 10 — typed judgment kernel and per-point routes

The kernel validates boolean/choice/score specifications and answers, freezes registered action points and applies independent modes, confidence thresholds, ordered backend routes and declared fallbacks. Invalid/uncertain/low-confidence answers, errors and timeouts fall through; cancellation stops the turn before approval or mutation, and late answers cannot change completed records. Laya is intrinsically shadow-only for `tool.intent`. Two optional project probes (`tool.review` and `tool.risk_score`) demonstrate choices and scores; they default to off and do not reproduce mu's multi-question risk/constraint policies. Each enabled action hook saves an explicit session record, and Java restores typed answers with route attempts on restart. Full validation ran 114 tests (113 passed; native PowerShell skipped), including real Python/Java processes against local HTTP fixtures. Judge accuracy, calibration/replay, task-frame retention, dynamic question sets and semantic admission remain gaps. See [JUDGING.md](JUDGING.md).
