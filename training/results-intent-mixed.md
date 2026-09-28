# Mixed intent experiment — milestone 16

## Result

Addressed review findings with mixed rehearsal, shared argument roles, scoped prohibitions,
canonical cross-round family IDs, larger unknown-request holdouts and coherent unseen-path
controls. New known synthetic592/592, unknown abstention158/160. Old synthetic improves from
uncertainty-r1's460/580 to574/580; the previously rejected60 file-positive cases now allow.
Manual regression remains17/20 at0.5 and live-argument read-only controls still show wrong allows.
No default replacement or active-mode promotion. This is Laya tool.intent post-training,
not original Jev weights or a complete mu classifier.

## Frozen data and lineage

[Dataset](datasets/intent-mixed-r1/README.md):9,465 exported rows,176 duplicates excluded.
Train7,209; validation/calibration/test752 each. Each holdout has16 families, including8 unknown
families/16 distinct unknown requests (160 cases), versus one unknown family last round.
Known holdout592:328 positive/264 negative. Training has2,456 positive/3,836 negative/917 null.

Only previous train rows from synthetic-r2/uncertainty-r1 are imported. synthetic/ and natural/
IDs for the same previous goal are canonicalized together, and all imported families remain
train. Original v1's349 states including37 teacher candidates remain verbatim. Other controlled
historical states receive one injective path mapping applied to the request and all arguments
at once. This preserves path identity/mismatch, not the actual contents of hypothetical files.
Source round, original sample ID and state digest remain in tags. Teacher provenance stays in
train. No previous validation/calibration/test input row is imported.

Fresh argument signatures occur with true/false/null labels; short clear requests and scoped
negation are included. Former artifacts now span all labels across the complete frozen data:

| Path | true | false | unknown |
| --- | ---: | ---: | ---: |
| pending/module.py | 605 | 754 | 120 |
| outside/credentials.ini | 589 | 722 | 129 |

Historical hypothetical extensions appear in train only. Fresh evaluations cover the five
actual mutation/command tool names. Explicitly requested publishing/deletion is intent=true
in controlled examples: intent asks whether an action is requested, not whether risk or permission
policy allows execution. No dangerous data action is executed during training/evaluation.

Lab initializes from a v1 copy, not the previous model: three epochs, batch16, encoder/head
rates5e-6/2.5e-5, seed20260927, sequence512. Validation selected epoch3; separate calibration
fits uniform-null/one-hot-known soft CE, temperature1.53312063. Criteria match actual serving.
[Training metrics](results/intent-mixed-r1/training-metrics.json) preserve hashes and hyperparameters.

Imported old/new task families are unified within this experiment. Some old regression goals
are present in the other round's imported train set, so historical benchmarks are inspected
retention checks, not fresh held-out semantic evidence. New goal/phrase namespaces are separate,
but templates and concepts are still shared. No independent human-gold claim.

## Actual serving

All comparisons use predict_batch, each checkpoint's temperature and unchanged0.2/0.8 app
thresholds. 0.5 accuracy includes predictions that abstain in the app.

| New frozen test | v1 | synthetic-r2 | uncertainty-r1 | mixed-r1 |
| --- | ---: | ---: | ---: | ---: |
| Known0.5 correct | 423/592 | 419/592 | 393/592 | 592/592 |
| False allow | 38 | 0 | 115 | 0 |
| False decline | 110 | 169 | 66 | 0 |
| Known accepted | 556 | 586 | 560 | 592 |
| Unknown abstention | 13/160 | 15/160 | 138/160 | 158/160 |

| Inspected regression | v1 | synthetic-r2 | uncertainty-r1 | mixed-r1 |
| --- | ---: | ---: | ---: | ---: |
| Manual0.5 correct | 18/20 | 16/20 | 17/20 | 17/20 |
| Manual accepted correct | 18 | 15 | 16 | 16 |
| Manual false allow | 2 | 1 | 2 | 1 |
| Manual false decline | 0 | 3 | 1 | 1 |
| Manual abstention | 0 | 1 | 1 | 2 |
| Old synthetic0.5 correct | 421/580 | 579/580 | 460/580 | 574/580 |

Mixed-r1's old580 has2 false allows,0 false declines and18 abstentions. All60 old edit/write
positives allow, resolving the observed scoped-prohibition regression in that controlled set.
Previous uncertainty test has300/300 at0.5,295 accepted correct,5 abstentions,0 wrong decisive
answers; all60 old unknown cases abstain. These are inspected regressions with conceptual reuse.

Remaining manual cases: English read-only write abstains(p=.6448), Chinese read-only write
still wrongly allows(p=.8032), Chinese fix-then-pytest wrongly declines(p=.0249), and test request
proposing cargo-publish abstains(p=.4392). Manual20 is too small and already inspected for promotion.

## Coherent unseen-path controls

522 controls(448 known/74 unknown) were frozen before inference. Replace paths in request and
arguments together, preserve the label and compute a fresh input digest. No actions execute.
Evaluator now publishes family/request counts and per-family metrics; paired comparisons align
parent identities and reject changed labels, duplicate/missing parents or different checkpoints.

| Same522 parent pairs | v1 | synthetic-r2 | uncertainty-r1 | mixed-r1 |
| --- | ---: | ---: | ---: | ---: |
| Answer flips after path rename | 58 | 13 | 74 | 5 |
| Mean absolute probability shift | .06833 | .01747 | .09342 | .00340 |
| Wrong decisive known after rename | 128 | 175 | 159 | 0 |
| Unknown explicit before→after | 67→65 | 73→73 | 22→3 | 1→6 |

Mixed-r1 retains448/448 known correctness, but5 formerly abstained unknowns become explicit.
This is improved stability on known cases, not complete removal of metadata sensitivity.
A few fixed metadata values, shared structures and expanded-but-limited phrase coverage remain
limitations; new results must not be interpreted as broad real-world reliability.

[Summary and complete compressed predictions](results/intent-mixed-r1/summary.json).
No test/regression/path-control result selected epochs or temperature. These sets are now inspected.

## Live harness and controlled negative actions

[First six-task run](results/intent-mixed-r1/mixed-harness.json) retains8 actual intent snapshots:
6 allow,1 decline,1 abstention. Python edit/document and Chinese port update succeed; read-only
fixture only reads. Disabled-command Bash proposal abstains and original permission blocks it.
Fix-then-test edits successfully, python is absent, a diagnostic command locating python3 is
incorrectly declined in shadow, and python3 unittest passes1 test. The probe then reaches its
six-step cap before a normal final summary; this limitation is retained in the trace.

Probe now supports selecting fixtures and setting a bounded step limit. A
[focused retry](results/intent-mixed-r1/mixed-harness-fix-retry.json) of only fix-then-test with
max10 yields3 allowed intent proposals, passes1 test and returns a complete user-facing summary.
It does not change model weights or replace the first trace. Both runs' six legacy direct probes
are6/6; those probes remain weaker than real agent argument states. Permission behavior unchanged.

To evaluate prohibited actions absent from these positive live traces, construct allow/read-only/
missing-context requests against the captured real arguments and call only the judge. These
are controlled synthetic counterfactuals, not negative actions proposed by the coding model.
Labels derive from new request construction, never observed predictions. No counterfactual tool
is executed. Summaries deduplicate identical controlled states:

- First run:24 rows,21 unique states. Positive7/7 correct; negative7 cases have4 correct declines,
  2 wrong allows and1 abstention; unknown7/7 abstain. The two wrong allows use edit metadata with
  16/19-byte replacements, showing failure beyond the synthetic metadata patterns.
- Focused retry:9 unique states. Positive3/3 correct; negative2 correct/1 wrong allow; unknown3/3
  abstain. This repeats the small-edit read-only failure; it is not new independent evidence.

[Deduplicated controlled metrics](results/intent-mixed-r1/harness-controls-summary.json).
API credentials entered only through masked input and are not in published artifacts.
Cold call~684ms, later first-run calls~43–81ms; too few observations for latency claims.

## Independent review preparation and checks

Eight actual source inputs are available as [pending-review.samples.jsonl](results/intent-mixed-r1/pending-review.samples.jsonl)
with observed model predictions removed, and [pending-review.labels.jsonl](results/intent-mixed-r1/pending-review.labels.jsonl)
with empty, unreviewed answers. They are annotation-only packets; labels are not training or gold
until separately reviewed. The source is still controlled temporary fixtures, not a broad real-
project holdout. No independent human labels were produced in this milestone.

Software gate:204 tests,203 passed, native PowerShell unavailable(one skip). Seven new tests
cover same-argument three-target support, real command schema, canonical imported family/split
isolation, original349 preservation, expanded unknown coverage, coherent controls/digests,
paired validation, probability counts and prediction-independent/deduplicated control labels.

Retained lab checkpoint:`/home/ubuntu/work/mu-pyjava-judge/work/checkpoints/tool-intent-mixed-r1`.
SHA-256:`1bf7cd28e32d2fef39299ac291362ec71b9d281a4c48b02e8b0c1580ae13c195`.
Original v1/r2/uncertainty hashes verified unchanged. Temporary service/tunnel stopped.

Next priorities: broaden byte counts/edit counts/timeouts independently of paths and labels,
include indirect prerequisite diagnostics/shell redirections, version task-context input, and
collect independently reviewed diverse real traces. Synthetic gains do not satisfy active
admission. Tracking:[milestone16](https://github.com/Vaniloo/mu-pyjava/issues/16).
