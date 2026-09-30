# Frozen runtime-captured synthetic experiment

Plan committed before collection: `8eb1f28`, 42 workflow families (24 train /6
validation /6 calibration /6 test). No learned judge has run at freeze time.

After whole-family exclusions: 33 families, 108 fresh rows. Partitions: 69 fresh
train +382 historical rehearsal; 12 validation; 12 calibration; 15 test. Total 490.
The original assignments were retained. Two rehearsal rows with transport defects
were removed. Fresh rows pass full-call schema and actual Agent projection checks. Transport
checks, protected-input exclusion, contrast pairs and both design audits pass.
Historical rehearsal retains its original synthetic provenance and metadata;
full original call payloads were not reconstructed for those old rows.

Three tasks yielded no sampled mutation; one task had a transport mismatch. Five
more families were excluded for duplicates and/or teacher label disagreement.
Teacher review agreed on 139/141 submitted hypotheses; no labels were changed.
One malformed review batch received one bounded same-input retry before freeze;
the initial failure and protocol amendment are documented in review-attempts.json.

These are authored fixture tasks with a DeepSeek actor and authored request replays,
reviewed by the same model family. Human gold and real-user telemetry remain zero.
The 15-row test (five scenario families) is too small for broad reliability claims.
Ready means technical preflight passed, not production readiness.

## Prescribed comparison after freeze

Use the identical retained mixed-r1 checkpoint copy for both arms. Same seed
20260927, batch 16, encoder LR 5e-6, head LR 2.5e-5, maximum three epochs; epoch
zero eligible, patience one, min_delta .001. Only the r3 design's weighting differs.
Rehearsal receives 25% loss mass. Validation selects weights, calibration selects
temperature. Existing serving thresholds .2/.8 stay fixed. Evaluate the frozen test
and retained diagnostics after selection, including a prespecified new-test common
temperature control of 1.5331206321716309. No deployment promotion or threshold tuning.

See intake-audit.json for exclusions and provenance, manifest.json for identity,
design-audits.json for protection and weighting checks, and capture-evidence.json.gz
for raw Agent samples and actor trajectories. Reviewer inputs were label/prediction
blind. Sampling backend always abstained and supplied no learned predictions.
