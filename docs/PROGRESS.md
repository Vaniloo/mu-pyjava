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
- [x] Opt-in exact judge input sampling, reviewed annotation schema, offline/fresh-shadow replay and frozen group-isolated intent-v2 exports with separate calibration/test partitions.
- [x] Add parent-linked session trees, completed-turn selection, independent forks with copied output artifacts, selected-path restart and a Java session browser.
- [x] Add deterministic request/tool/image budgets, immutable canonical history, full-text admission archives, general retrieval and Java Context history/status.
- [x] Add default-off semantic output-kind admission and bounded source-grounded summaries of omitted turns, with optional model excerpt selection and selected-path restoration.
- [ ] Add provider usage calibration, free-form generative compaction, provider-side HTTP request abort and background jobs.
- [x] Typed boolean/choice/score registry, per-point modes and ordered backend routing, strict answers, bounded timeout/cancel and individual saved records.
- [x] Dynamic question sets, partial-answer cascading, constraint/risk policy aggregation, branch task frames and fresh risk confirmation with Java restoration.
- [ ] Generated frame writing/distribution pooling and independently calibrated decisions.
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

## Milestone 11 — dynamic policies and branch task frames

The kernel supports static/dynamic question sets and partial-answer cascades with pure outcome policies. Actual `tool.constraint` asks one question per newest user constraint; active confident violations veto with the user's sentence and outages fall through to existing permissions. `tool.risk` combines destructive/requested booleans for flagged commands and can require fresh confirmation; allow never grants permission. Confirmation is bound to the exact action and consumed once by the normal gate. Branch-local frames retain verbatim user constraints, source turns, goal/current step and open questions, including restart/fork/interrupted-user recovery. The derived request note retains all stored constraints under context omission; Java adds Task history and displays multi-question records. New judge points default off; Laya remains intent-only/shadow-only. Full validation: 140 tests, 139 passed and native PowerShell skipped. Local HTTP fixtures exercise actual Python/Java restart and forced risk confirmation. Generated frame writing, choice-distribution pooling, mu permission modes, asynchronous shadow checks, independent accuracy/calibration and semantic admission remain gaps. See [TASK_POLICIES.md](TASK_POLICIES.md).

## Milestone 12 — semantic output admission and history summaries

`tool.admission` v3 classifies bounded middle chunks through typed routes, retaining
errors/results/unknowns/endpoints and allowing confident progress/repeated-warning/
passing omissions only in active mode. A shared per-output deadline spans batches
and routes; timeout/cancel/invalid answers preserve fallbacks. Canonical text is
unchanged and every applied omission archives exact original bytes. Source reads,
error/image results and old unknown-status replies bypass semantic classification;
hard budgets still apply. The trained Laya remains intent-only/shadow-only.

When whole older turns leave the request, bounded exact evidence excerpts retain
task decisions/progress/observations with source roles/indexes and prefix fingerprints.
Default extraction has no extra model call; optional model selection rejects invented
facts and falls back on errors/timeouts. Selected-path restart/fork restoration checks
evidence without executing a model/tool. Java shows admission savings and Task summaries.
Actual Python/Java processes against local HTTP fixtures recover omitted text, generate
grounded summaries and restore independent forks without the original output source.
Full validation: **164 tests, 163 passed and native PowerShell skipped**; 24 new
admission/summary tests include the actual Java/Python HTTP-fixture flow.
See [SEMANTIC_CONTEXT.md](SEMANTIC_CONTEXT.md) for configuration and limits. Free-form
compaction/frame writing, asynchronous shadow, exact provider usage, mu test-log repetition
handling, independent accuracy/calibration and HTTP abort remain gaps.

## Milestone 13 — judge data and evaluation pipeline

Completed decisions can optionally save private raw inputs, resolved dynamic questions,
policy thresholds and observed route replies. Explicit task/repository groups and source
IDs are separate from reviewed labels; metadata ledgers keep only sample references.
The CLI creates pending annotations, validates provenance/input binding, replays pure
policies without tools, optionally runs fresh shadow inference, and exports frozen intent-v2
data with duplicate-connected groups in train/validation/calibration/test partitions.
Reports distinguish accepted errors, abstention and existing permission fallbacks, including
constraint misses, harmful admission classifications, Brier and score error.

The trainer rejects partition/input leaks and frozen-manifest drift before loading weights.
For v2 it selects on validation, fits temperature on calibration and evaluates test afterwards;
the old 20-case manual set is an inspected regression set. Runtime 0.2/0.8 metrics and matching
training/serving sequence bounds are recorded. Laya remains intrinsically intent/shadow-only.

Full validation: **187 tests, 186 passed and native PowerShell skipped**; 23 new tests include
actual Java/Python HTTP sampling, permission denial, restart/source identity and pipeline CLI.
No paid model call, independent new labels, GPU run or retraining occurred in this milestone.
Next: gather and review diverse real decisions, freeze an independent holdout, compare the
existing checkpoint, then train intent v2. See [JUDGE_EVALUATION.md](JUDGE_EVALUATION.md).
GitHub tracking: [milestone 13](https://github.com/Vaniloo/mu-pyjava/issues/13).

## Milestone 14 — broader synthetic intent post-training

Generated and froze 4,240 bilingual examples in 56 scenario families, with all eight actual
or hypothetical tool names in every partition: 2,500 train, 580 validation, 580 calibration,
580 test. Synthetic provenance is preserved; an explicit experiment flag permits these
labels in evaluation without calling them human gold. Duplicate/group checks remain active.

Lab continued three epochs from a copy of v1; the pinned hub base download stalled.
An initial timeout metadata mismatch was corrected and the experiment rerun; r1 artifacts
remain historical, r2 is current. Actual serving comparison improved synthetic 0.5 accuracy
from 421/580 to 579/580, while manual regression worsened from 18/20 to 16/20, with one
false allow, three false declines and one abstention. Missing-context probes worsened:
24/24 explicit v2 answers versus 20/24 for v1. The default model is not replaced.

A live DeepSeek coding-model run verified edit/document/readonly cases and original command
denial, but r2 incorrectly declined the actual edit in shadow. Six direct legacy probes were
all correct and missed this real failure. Both trial runs and their outcomes are retained. No training
sample was recast as independent real-world gold. Original checkpoint and default app settings
are preserved. Temporary service/tunnel were stopped after verification.

Full software regression: **192 tests, 191 passed, native PowerShell skipped**. Training data,
frozen manifest, inference predictions and detailed findings are published in
[results-intent-v2.md](../training/results-intent-v2.md). Next: task-aware intent input,
natural-language data/rehearsal, explicit missing-evidence targets and independently reviewed real harness evaluation.
GitHub tracking: [milestone 14](https://github.com/Vaniloo/mu-pyjava/issues/14).

## Milestone 15 — explicit uncertainty and natural intent training

Added opt-in null-label soft targets with a frozen-manifest guard, pipeline CLI export support,
matching training/serving criteria and separately denominated metrics. Generated 3,709 rows
in 57 bilingual goal/phrase families: 2,629 train and 360 each validation/calibration/test;
720 missing-context examples and all 349 original v1 training rows (teacher provenance retained).
Five actual intent-triggering tools; prior hypothetical extensions are not new implementations.

Lab trained from a v1 copy for three epochs, validation selected epoch 1. Actual serving gets
300/300 known new synthetic cases and 60/60 unknown abstentions. Manual regression is 17/20
at 0.5 (16 accepted correct, two false allows, one false decline, one abstention), below v1's
18/20. Prior r2 synthetic test regresses to 460/580; no default change or active promotion.

Live DeepSeek six-task harness recovered the previously rejected edit, completed Chinese
configuration mutation via shadow fallback and ran a real one-test unittest suite. Eight
actual intent snapshots: seven accepted, one abstention; command permissions still denied
both proposals in the disabled-command task. Read-only false allows persist in direct probes.
Original v1/r2 weight hashes unchanged; temporary serving/tunnel stopped. Software gate:
**197 tests, 196 passed, native PowerShell skipped**. See
[results-intent-uncertainty.md](../training/results-intent-uncertainty.md).
GitHub tracking: [milestone 15](https://github.com/Vaniloo/mu-pyjava/issues/15).

## Milestone 16 — mixed rehearsal and deconfounding controls

Canonicalized reused old/new goal families, imported only previous train partitions and retained
all349 original v1 states. Other controlled rehearsal states receive coherent injective path
renaming. Fresh argument signatures span true/false/null, with scoped prohibitions and short
clear requests. Frozen9,465 rows:7,209 train;752 each validation/calibration/test. Each holdout
has8 unknown families/16 distinct requests, addressing the prior one-family coverage weakness.

Added522 unseen-path controls, paired validation and family/request-count reporting. Lab trained
from a v1 copy; epoch3 selected, separate temperature calibration. New known592/592 and unknown
158/160 abstentions; old synthetic574/580(up from460), all60 old file positives now allowed.
Manual remains17/20, with1 false allow/1 false decline/2 abstentions. Path controls have0 known
errors but5 unknown answer flips. Laya remains shadow-only; no default replacement.

Six-task DeepSeek harness produced8 actual judgments(6 allow/1 decline/1 abstain). Tests ran,
but the first repair task hit the probe's step cap before a final summary; a focused max10-step
retry completed its summary. Controlled negatives against actual arguments still falsely allow
small file edits. Deduplicated metrics and both traces retained. Eight reviewer input packets
have no model predictions and no filled gold labels; independent human review remains pending.

Software gate:**204 tests,203 passed, native PowerShell skipped**. Original weights unchanged;
temporary service/tunnel stopped. See[results-intent-mixed.md](../training/results-intent-mixed.md).
GitHub:[milestone16](https://github.com/Vaniloo/mu-pyjava/issues/16).

## Milestone 17 — intent factor diagnosis and original-base comparison

Frozen1,164 balanced diagnostic states from four inspected file-action seeds, with wording,
coherent path, byte/count/fuzzy interventions. Separate post-inspection joint-size extension:
336 states,288 pairs. Their union has1,452 unique states;96 distinct requests in the primary
set include only eight unknown strings. Constructed labels are not independent human gold.

Recovered the pinned original Laya into a new lab directory, verified revision blob identities,
and compared original/v1/mixed actual serving under identical criteria and0.2/0.8 thresholds,
retaining shipped temperatures. Primary negative false allows:292/388,272/388,210/388;
unknown abstentions:249/388,19/388,388/388. Mixed still fails badly on diagnostic read-only cases.
Equivalent phrasing changes an edit from .9999 wrong allow to .0096 correct decline; joint
sizes1..65,536 yield0/288 mixed answer flips and preserve the inspected wrong allow. Wording
and path effects are observed; a unique internal cause is not established.

Retained hashes verified unchanged, all measured sequences fit512 tokens, predictions/paired
transitions/runtime provenance published. No training or promotion; pending independent labels
remain unfilled. Software gate:**210 tests,209 passed,1 native PowerShell skip**. See
[diagnosis and next training work](../training/results-intent-ablation.md).
GitHub:[milestone17](https://github.com/Vaniloo/mu-pyjava/issues/17).

## Milestone 18 — scope paraphrases (in progress)

Frozen12,195 train with7,209 prior train states unchanged and4,986 new rows;1,602 validation,
1,602 calibration,2,130 test. File/command phrase families and file goals stay in one partition;
prerequisite templates share structure with held-out executable goals.24 multi-sentence challenge
cases are protected from exports and independently reviewed by DeepSeek without labels or judge
predictions:24 agreements. This is teacher verification,not independent human gold.

Candidate initialized from a mixed-r1 copy,three epochs with predeclared validation-only epoch
selection and separate calibration. Original/v1/mixed serving baselines completed. Five dataset
guards pass;candidate serving,retention,live harness and full software gate are pending.
See[scope experiment](../training/results-intent-scope.md).
GitHub:[milestone18](https://github.com/Vaniloo/mu-pyjava/issues/18).
