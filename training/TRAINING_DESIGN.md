# Revised intent training design

Update after milestones 21–22: the first pilot was rejected for transport-inconsistent data. See [pilot correction](results-intent-semantic.md) and [runtime capture intake](results-harness-intake.md). Use the r3 design copies for the next collection; they additionally exclude the inspected r2 pilot. The protocol below records the original prescribed comparison. The [captured r3 dataset](datasets/intent-captured-r3/README.md) is now frozen, and [both training arms completed](results-captured-training.md) without a promotion.


Milestone 20 changes the next experiment from expanding template rows to testing
semantic generalization and evidence sufficiency. The implementation is opt-in
through `train_laya.py --design`; the original recipe remains reproducible without
that option. No new Laya candidate has been trained by this design-change milestone.

## 1. Capability check before another candidate

Use exactly the same captured state, current question and Boolean criteria for
retained Laya checkpoints and a language-model baseline. Each API request contains
one case, with only JSON-output/insufficient-evidence instructions added. Labels,
other cases, model predictions, rationale requests and failure-specific coaching
are absent. API errors are reported separately, never counted as successful unknown
abstentions. API categorical answers have no comparable calibrated probability.

```sh
PYTHONPATH=python python3 training/compare_language_judge.py \
  --data training/datasets/intent-boundary-r1/cases.jsonl \
  --output work/capability-new.json
```

The completed baseline is in [results-training-design.md](results-training-design.md).
It compares model/prompt systems; architecture, pretraining and output mechanisms
also differ, and DeepSeek parameter count is not established. It cannot isolate
parameter count as the cause. Prior inspected diagnostics inform design and remain
regressions, not fresh selection data. Repeated runs must not be cherry-picked.

## 2. Data unit: semantic family and authorization reversal

Each new versioned row must declare:

```json
"tags": {
  "semantic_family": "one-underlying-task-and-scope-family",
  "rehearsal": false,
  "contrast_pair": "one-language-and-one-action-pair"
}
```

A contrast pair has two distinct requests, true/false labels and an identical tool
plus arguments. Both rows remain in the same semantic family and partition.
Translations, near-paraphrases, paths, numeric variants and authorization reversals
of the same task belong to the same curator-assigned family. Every partition must
contain authorization pairs. Unknown rows normally omit contrast_pair and include
requests whose missing history actually prevents judging the supplied action.

Prioritize response-only delivery versus disk changes, necessary diagnostic
prerequisites versus unrelated execution, command explanation versus execution,
latest permission revocation and compound-command scope. Include natural implicit
requests as well as explicit tool/command mentions, in positive and negative roles.
Do not multiply easy templates just to reach a row target. Report families,
distinct requests, tool/language coverage and label provenance alongside row counts.

Rehearsal rows use rehearsal=true, remain in train and preserve an unambiguous
previously registered training state and label. Previous training inputs cannot be
renamed as fresh data. The new design checks semantic_family separately from the
existing dataset group/duplicate split guard. Curator IDs and pair shape checks do
not prove semantic diversity or label correctness; independent review is still needed.

## 3. Weighting and the controlled comparison

Freeze one new dataset before running two arms from identical copies of mixed-r1,
with seed 20260927, batch 16, encoder LR 5e-6, head LR 2.5e-5 and at most 3 epochs.
These are starting settings, not proven optima.

| Arm | Design | Weighting within fresh/rehearsal strata |
|---|---|---|
| Control | intent-semantic-r2-uniform.json | Equal weight per row |
| Candidate | intent-semantic-r2.json | Equal mass per semantic family, then label, then distinct request |

Both arms reserve **25% of training loss weight for rehearsal**, 75% for fresh
examples when both exist. This is loss mass, not a row-count or minibatch quota.
Fresh-only datasets use 100% fresh mass; rehearsal-only data is rejected. The 25%
choice is prespecified for this experiment and must not be tuned on test results.
Argument variants share their request's mass, preventing a hundred variants from
receiving a hundred times that request's total weight. Mean-one weights preserve
the overall loss scale. Shuffle whole prepared examples so weights remain attached.

The current loss is soft cross-entropy: false/true use one-hot targets; unknown uses
[0.5,0.5]. Pairing is a data invariant, not an additional ranking loss. Avoid changing
weighting, architecture and several loss functions simultaneously in the first
comparison. Weighting also defines each arm's validation/calibration objective;
this compares weighting pipelines, not only one training-gradient change.

## 4. Selection, calibration and evaluation

- Include the **initial checkpoint as epoch 0**. Keep its exact tensor values if no
  trained epoch improves validation weighted loss by at least 0.001.
- Stop after **one non-improving epoch**. Log completed epochs, selected epoch,
  weighted losses, standard validation metrics and the unknown-target entropy floor.
- Select weights using validation only. Fit temperature using the separate calibration
  partition and the same arm's weighting. A fit at 0.5/5.0 is recorded as a bound hit.
- Evaluate selected/calibrated models once on the new test set. Also run the already
  inspected diagnostics and old regressions, without selecting weights or thresholds.
- Predeclare a shared-temperature inference comparison at mixed-r1's retained
  temperature to distinguish weight effects from calibration effects.

Always report false allows, false declines, correct decisive answers, known
abstentions and unknown abstentions with their denominators. Include per-family,
tool and language results. Report both members of contrast pairs, especially cases
where forbidden actions score above their authorized counterpart. Better known-case
accuracy cannot compensate automatically for confidently guessing missing context.

Models remain shadow-only. A training improvement alone is not a deployment gate;
new opt-in real-project traces with prediction-blind human labels are needed for
that decision. The current independently reviewed real-project count remains zero.

## 5. Protected inputs and runnable preflight

Both design files explicitly register historical held-out datasets, manual/challenge
sets, boundary/path/ablation diagnostics and published harness samples. Their exact
inputs are barred from every new train/validation/calibration/test partition.
Registered earlier train inputs are available only as labeled rehearsal. The audit
records source hashes; missing registered files fail. Add newly inspected inputs
before freezing the next experiment. Exact matching does not detect paraphrases.

New experiments need a new dataset/manifest with the semantic/rehearsal/pair tags.
The existing scope-r1 dataset is intentionally rejected because its previously
inspected holdouts cannot become a fresh experiment's holdouts.

```sh
PYTHONPATH=python python3 training/training_design.py \
  --data work/new-intent/dataset/intent-v2.jsonl \
  --design training/designs/intent-semantic-r2.json \
  --allow-synthetic-eval --train-unknown \
  --output work/new-intent/design-audit.json

PYTHONPATH=python python3 training/train_laya.py \
  --data work/new-intent/dataset/intent-v2.jsonl \
  --manual-eval training/manual_eval.jsonl \
  --init-checkpoint /path/to/mixed-r1-copy \
  --output work/checkpoints/intent-semantic-r2 \
  --design training/designs/intent-semantic-r2.json \
  --epochs 3 --batch-size 16 --encoder-lr 0.000005 --head-lr 0.000025 \
  --allow-synthetic-eval --train-unknown
```

For the control arm, use the uniform design and a separate checkpoint output. The
synthetic-eval switch explicitly identifies a synthetic experiment; it does not
convert generated labels or collection provenance to independent human gold.
