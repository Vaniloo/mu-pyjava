# Progress

This file records what the repository actually does. The goal is a Python agent engine with a Java desktop, inspired by [mu](https://github.com/qybaihe/mu).

## Milestone 0 — runnable foundation

- [x] Python package with a bounded model/tool loop.
- [x] Chat Completions model adapter and an explicit offline echo adapter.
- [x] Read/list tools; write and command tools gated by launch flags.
- [x] Versioned boolean decision point engine with off/shadow/active modes and optional JSONL ledger.
- [x] Java Swing window and a long-lived Python process connected through a line protocol.
- [x] Python unit tests, Java compilation, and cross-language smoke test.

## Next milestones

- [x] Train and evaluate a Laya multilingual fine-tune for the first judge decision; add optional shadow-mode loading.
- [ ] Collect independently labeled real tool decisions and resolve confident false positives before enabling the trained judge.
- [ ] Per-action approval in the UI rather than process-wide write/command flags.
- [ ] Durable sessions and a user-facing judgment ledger panel.
- [ ] Typed choice/score decisions, judge routing, and output admission.
- [ ] LSP diagnostics and project-specific build/test guidance.
- [ ] Robust packaging of Python and Java runtimes for each platform.
- [ ] Add GitHub Actions after a GitHub credential with workflow permission is available.
- [ ] Browser, sub-agents, and memory after the single-agent workflow is stable.

## Current boundaries

The echo adapter only checks wiring. An API-backed run needs a configured Chat Completions endpoint and model. The Java window is a development shell, not a packaged desktop release. The trained judge weights are kept on the lab host and are not bundled with this repository. No provider sign-in flow or automatic language-server installation exists yet.
