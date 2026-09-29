# Semantic pilot — milestone 21

Completed 2026-09-29. **Both prescribed training arms ran, but this experiment is
invalid for model-quality or promotion claims.** A post-training transport audit
found shell syntax/builtins assigned to the direct-argv `run_command` tool in 25
frozen rows. Data, labels and raw results remain unchanged for audit. Both new
checkpoints are diagnostic only; existing shadow configuration remains in place.

## What ran

The dataset and protocol were committed/pushed as `134c390` before model inference.
Both arms used the same retained mixed-r1 checkpoint copy and identical dataset:
520 training rows (136 new teacher requests, 384 historical synthetic rehearsal),
48 validation, 48 calibration and 48 test rows. Each heldout partition has eight
bilingual synthetic families, 16 positive, 16 negative and 16 insufficient-context
cases. There is **no independent human gold**. Topic/family separation and exact
exclusion do not prove semantic independence.

The teacher attempted 32 training families. Five incomplete/malformed families
were excluded; 27 families plus 24 authored heldout families entered prediction-
and label-blind review (350 cases, all reviewed). Eleven teacher families were
quarantined in full: ten contained duplicate literal `request` placeholders and
one had a label disagreement. No label was changed to match a judge prediction.
The initial API JSON-format error and all successful/failed generation attempts
are archived. A valid retry of one malformed family was deliberately not used.

Final new training coverage is only 16 families. Rehearsal selects distinct
requests within old family/label buckets, preserving original states, labels and
provenance. New data receives 75% of loss mass, rehearsal 25%. Uniform weighting
and family/label/request weighting use the same data; validation/calibration
weights happen to be identical here because heldout families are balanced.

Both use seed 20260927, batch 16, encoder LR 5e-6, head LR 2.5e-5, at most three
epochs, epoch-zero eligibility and patience one/minimum improvement .001.
Only validation selects weights; only calibration selects temperature.

| Training measure | Uniform | Family/request weighted |
|---|---:|---:|
| Initial validation loss | 1.9984 | 1.9984 |
| Epoch 1 validation loss | 1.0789 | 0.7171 |
| Epoch 2 validation loss | 1.2098 | 1.4250 |
| Completed / selected epoch | 2 / 1 | 2 / 1 |
| Calibrated temperature | 4.8958 | 2.6285 |
| Temperature bound hit | no | no |

Early stopping worked, but that alone does not establish better decisions.

## Raw serving results — compromised dataset, diagnostic only

All rows use actual serving and existing thresholds (decline <= .2, allow >= .8).
The test contains 32 known and 16 unknown cases; six rows have the transport issue.
The same frozen inputs and prediction ordering were verified before comparison.

| Measure | mixed-r1 | scope-r1 | Uniform | Weighted |
|---|---:|---:|---:|---:|
| Correct decisive / 32 | 19 | 23 | 8 | 9 |
| False allow / 16 | 3 | 4 | 2 | 3 |
| False decline / 16 | 4 | 4 | 3 | 1 |
| Known abstentions / 32 | 6 | 1 | 19 | 19 |
| Unknown abstentions / 16 | 13 | 16 | 16 | 16 |
| Correct pair ordering / 16 | 10 | 13 | 14 | 13 |
| Both sides correct and decisive / 16 | 4 | 7 | 0 | 0 |

Fewer decisive mistakes coincide with much less coverage. Pair ordering can look
better without either side reaching a usable decision. At the prespecified common
temperature 1.5331206321716309, uniform has 16 correct / 3 false allows / 3 false
declines / 10 known abstentions; weighted has 13 / 3 / 3 / 13. These controls do not
support an improvement over mixed-r1's 19 / 3 / 4 / 6.

Old regression results (also diagnostics, never selection gates):

| Set | Arm | Correct decisive | False allow | False decline | Known abstentions | Unknown abstentions |
|---|---|---:|---:|---:|---:|---:|
| Boundary 72 | Uniform | 22/48 | 3 | 2 | 21 | 24/24 |
| Boundary 72 | Weighted | 22/48 | 4 | 2 | 20 | 24/24 |
| Scope challenge 24 | Uniform | 9/20 | 1 | 1 | 9 | 4/4 |
| Scope challenge 24 | Weighted | 9/20 | 0 | 0 | 11 | 4/4 |
| Manual regression 20 | Uniform | 10/20 | 0 | 0 | 10 | n/a |
| Manual regression 20 | Weighted | 10/20 | 0 | 0 | 10 | n/a |

The new audit also flags **18 of the old boundary 72 cases**. Earlier reports are
preserved with a correction notice. Scope challenge and manual regression have
zero flags from this narrow transport check; that is not general label validation.

## Discovered data defect and fix

`run_command` uses `shlex.split` and a direct subprocess, without a shell. Thus
`command -v ...` is not a portable direct executable lookup, and `git ...; ...`
does not execute two commands as those synthetic labels assumed. This is a data
construction error; teacher agreement did not establish tool-contract correctness.
Seven training rows and six rows in each heldout partition were affected.

A new conservative offline audit rejects shell builtins and unquoted shell syntax
assigned to `run_command`. It permits normal argv and explicitly quoted scripts
passed to a shell. Both preparation/freezing and the opt-in training entry point
now check this before loading weights. It does not execute any supplied command,
assess safety or assert program availability. Next-collection templates now use
`bash` for these shell actions, and literal response placeholders are rejected.

**The frozen dataset was not repaired in place or reevaluated as a fresh test.**
Current code intentionally rejects retraining it. The original experiment is
reproducible from freeze commit `134c390`; the current templates and guards are a
subsequent correction. Existing retained weights/config hashes are unchanged.

## Artifacts and verification

- [Frozen inputs, review and protocol](datasets/intent-semantic-r2/)
- [Raw predictions, training logs and comparisons](results/intent-semantic-r2/)
- [Transport findings](results/intent-semantic-r2/tool-state-audit.json)
- [Serving comparison](results/intent-semantic-r2/comparison.json)
- [Shared-temperature control](results/intent-semantic-r2/shared-temperature.json)

Runtime: Python 3.10.20, Torch 2.11.0+cu130, Transformers 5.4.0; Laya revision
23a17522aa4942da6cce53a995a275760320b691. All 664 inputs fit 512 tokens (max 189).
235 software tests: 234 passed, native PowerShell skipped. Three real Torch CPU
runtime tests passed; Java compiled and echo smoke passed. Secrets scan clean.
Weights remain on lab under `work/intent-semantic-r2/{uniform,weighted}`, excluded
from Git. No service was launched or candidate promoted.

## Next experiment

First collect tool-contract-valid examples from actual harness decision states,
review the action as well as the request, and register this entire inspected pilot
as protected input before creating another split. Build genuinely new evaluation
families; changing these rows' tool names cannot make them unseen. Expand fresh
training coverage beyond the surviving 16 families. Only then repeat the same
controlled comparison. This run does not isolate model capacity from data quality,
and does not justify increasing parameter count yet.
