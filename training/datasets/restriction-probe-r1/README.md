# Frozen unrelated-restriction diagnostic

80 authored fixture states: ten fixed actions, English and Chinese, four conditions
per language. Plain and restricted both authorize the same action; restricted adds
only a prohibition on additional operations. Decline explicitly forbids executing
the action. Unknown omits a necessary earlier choice. All states are captured by
Agent using scripted proposals, with mutations blocked after sampling. This is not
natural actor behavior or real-user telemetry, and no action was executed.

All80 teacher reviews matched the authored hypotheses, with labels and candidate
predictions hidden. No family was quarantined. Full raw payload/schema/projection
and transport checks pass, with no exact overlap against registered historical
inputs and the inspected captured-r3 dataset/submitted cases. Different surface
forms do not establish semantic independence from previous diagnostics.

Compare retained mixed-r1, scope-r1, captured-r3 uniform and captured-r3 weighted,
using shipped calibration and unchanged .2/.8 thresholds. Primary paired measures:
plain allow lost after adding unrelated restrictions, new wrong declines, recovered
allows, and within-model probability changes. Also report refusal errors and missing-
context abstention. No training or model promotion. Do not train on the evaluated
probe and later call it an unseen test. Human gold and real-user telemetry: zero.

`cases.jsonl` is the frozen input; `manifest.json` binds its digest, `evidence.json.gz`
contains raw Agent samples/calls and the exact plan. `blind-reviews.json` retains
teacher review evidence. Ready-for-training is false.
