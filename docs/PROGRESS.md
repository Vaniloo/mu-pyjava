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
- [x] Persist completed turns in a versioned local session journal, restore the newest session on restart, and show saved judge records in a dedicated Java tab.
- [x] Add a desktop Stop action, process-tree cancellation, live bounded command output and a full-output retrieval tool.
- [x] Show unified diffs before write/edit approval, recheck the approved revision, serialize same-path mutations, replace atomically, and record structured change events.
- [x] Compare mu's current tool and architecture contracts; extend edit to multiple disjoint replacements while preserving BOM and CRLF.
- [x] Route read/list through injectable operations; verify paging and multi-edit with an in-memory workspace that has no local target file.
- [ ] Collect independently labeled decisions for all mutating tools, including `edit_file`, and resolve confident false positives before enabling the trained judge.
- [ ] Add branch/fork sessions, provider-side HTTP request abort and background jobs after the single-path format is stable.
- [ ] Typed choice/score decisions, judge routing, and output admission.
- [ ] LSP diagnostics and project-specific build/test guidance.
- [ ] Robust packaging of Python and Java runtimes for each platform.
- [ ] Add GitHub Actions after a GitHub credential with workflow permission is available.
- [ ] Browser, sub-agents, and memory after the single-agent workflow is stable.

## Current boundaries

The echo adapter only checks wiring. An API-backed run needs a configured Chat Completions endpoint and model. The Java window is a development shell, not a packaged desktop release. The trained judge weights are kept on the lab host and are not bundled with this repository. No provider sign-in flow or automatic language-server installation exists yet.
