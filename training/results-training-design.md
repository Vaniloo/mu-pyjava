# Revised training design and capability baseline — milestone 20

**2026-09-29 correction:** milestone 21 found 18/72 boundary states assign shell syntax/builtins to direct `run_command`. The original numbers below remain historical diagnostics; they do not establish model quality on valid tool contracts. See [audit and correction](results-intent-semantic.md).


Completed 2026-09-29. The next training protocol is implemented in
[TRAINING_DESIGN.md](TRAINING_DESIGN.md), two executable design configurations and
the opt-in `train_laya.py --design` path. No new Laya candidate or full training
dataset was produced in this milestone. The impact of the new training design
on model quality remains unmeasured.

## Implemented changes

- Equal loss mass per semantic family, then label, then distinct request; metadata
  variants share their request's mass. Mean-one weights stay attached during shuffle.
- Prior training rehearsal receives 25% of training loss mass when present, fresh
  examples 75%. This is a prespecified starting setting, not a proven optimum.
- Explicit semantic families must remain within one partition. Each partition
  contains true/false contrast pairs with identical proposed tool arguments.
- Rehearsal preserves a registered, unambiguous old train input and label; historical
  train inputs cannot be called fresh. Registered old holdouts, manual/challenge,
  boundary/path/ablation and harness inputs cannot enter any new partition.
- Epoch 0 is eligible. One validation epoch without at least 0.001 improvement stops
  training; exact initial tensor values are restored if no epoch improves sufficiently.
- Separate calibration fits temperature under the selected arm's weighting. Metrics
  record weighted losses, validation behavior, completed/selected epochs, initial
  checkpoint selection, per-partition unknown entropy floors and calibration-bound hits.

The uniform control has the same input protection, rehearsal fraction, stopping and
initialization plan. Only within-stratum weighting differs. Weighting affects train,
validation and calibration objectives, so this is a weighting-pipeline comparison.
The original command without a design retains the historical training recipe.

The existing scope-r1 dataset correctly fails new-design preflight: **5,334** rows
are previously inspected protected inputs (its 1,602 validation + 1,602 calibration
+ 2,130 test rows). New data needs explicit semantic_family/rehearsal/contrast_pair
tags and a fresh frozen manifest. This failure is expected, not a completed new dataset.

## Same-information language-model baseline

The older DeepSeek review used boundary-specific instructions and 24 cases per
request. Its 72/72 label agreement was not a serving-equivalent capability score.
This new run uses **one case per request**, the exact frozen state and current Laya
question/Boolean criteria, with only JSON-output and insufficient-evidence/null
formatting instructions. It hides labels, prior predictions and all other cases,
and asks for no explanation. Temperature 0, thinking disabled, no retries or tool
execution. All 72 requests completed with valid Boolean/null replies.

The comparison reuses the unchanged milestone 19 Laya predictions after verifying
identical dataset hash and ordered sample/state/label identities. Laya answers use
its shipped calibration and 0.2/0.8 abstention thresholds; DeepSeek gives categorical
answers, without a comparable calibrated probability.

| Metric | mixed-r1 | scope-r1 | DeepSeek Flash |
|---|---:|---:|---:|
| Correct decisive / 48 known | 29 | 32 | **47** |
| False allow / 24 negatives | 6 | 7 | **1** |
| False decline / 24 positives | 7 | 6 | **0** |
| Known abstentions | 6 | 3 | **0** |
| Unknown abstentions / 24 | 22 | 24 | **5** |
| Correct including unknown abstentions / 72 | 51 | 56 | **52** |
| Provider failures | 0 | 0 | **0** |

The sole known DeepSeek error allows `command -v java javac` for the Chinese request
to write an explanation without starting a shell. Among the 24 missing-context
cases, DeepSeek gives 11 true, 8 false and only 5 null answers. It handles most
known semantic boundaries better here, while guessing most unknown cases.

This supports evaluating semantic accuracy and evidence sufficiency separately.
Escalating every Laya abstention to this API would not automatically improve behavior.
These are inspected synthetic diagnostics with correlated families; the aggregate
72-case score depends on the constructed class mix and is not deployment accuracy.
Architecture, pretraining, prompt/output format and calibration also differ. The
API's parameter count is not established, so this does **not** isolate model size
as the cause of the Laya failures. No language-model replies become human gold or
training labels automatically.

API usage: 12,858 prompt tokens + 360 completion tokens = **13,218 provider tokens**
for this baseline only. This does not describe the Codex conversation's token usage.
No credentials are included in artifacts.

## Validation

Eight new local tests cover request/family loss-mass invariance, rehearsal quota,
family and action-pair isolation, protected input exclusion, rehearsal provenance,
epoch-zero stopping and capability prompt/error semantics. Full Python/software
suite: **230 tests, 229 passed, one native PowerShell skip**.

Three additional tests ran on lab **CPU** with real Torch and no downloaded Laya
weights: weighted soft-target loss/gradients, weight binding after shuffle, and a
complete miniature training/calibration/export run that stops after one epoch and
restores exact initial tensors. The miniature fixture is software validation, not
a new trained judge or model-quality evidence. Existing Laya checkpoint files were
not loaded or changed by those tests.

[Artifacts](results/intent-design-r2/) contain the compressed API per-case responses,
input-validated capability comparison, old-data rejection and validation summary.
The next experiment uses two new dataset runs from identical retained mixed-r1
copies, with independent calibration and a prespecified shared-temperature inference
control. Real-project collection and blind human labels remain required for any
promotion; the current independent real-project count is still zero.

Tracking: [milestone 20](https://github.com/Vaniloo/mu-pyjava/issues/20).
