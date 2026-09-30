# Scope triplet r4: prespecified capture

The `capture/` directory freezes 40 authored synthetic task families and 160
Agent-projected `tool.intent` states before any candidate-model inference. Each
family uses one identical proposed tool action with four latest-request conditions:
plain authorization, authorization plus an unrelated restriction, prohibition of
the action, and missing earlier context. The two positive requests should remain
positive; the prohibition should be negative; missing context should abstain.

Each of the five mutable tools has four training families, one validation family,
one calibration family, and two test families. Languages and restriction position
are distributed across splits. All mutations were stopped at the approval callback;
no command or file change was performed. `raw-samples.jsonl` and `contracts.json`
retain the actual projection and blocked tool calls. These scripted proposals are
controlled fixtures, not natural actor behavior or real-user telemetry.

The captured states passed exact historical-input exclusion and tool transport
checks. The plan and capture were committed as `4394be1` before review.

The `frozen/` directory contains 160/160 prediction-blind DeepSeek reviews in ten
16-case batches; all agreed with the authored hypotheses. The frozen training
dataset has 80 fresh training rows plus 382 historical rehearsal rows, 20
validation, 20 calibration, and 40 test rows. All five tools occur in every split.
The data audits found no exact protected or prior-training overlap and no whole-
family exclusions. Review used 22,972 provider tokens. Teacher agreement is not
independent human gold; no real-user project traces or candidate predictions were
used in this freeze.

## Prespecified training comparison

After a valid blind-review freeze, compare two `family_request` training arms from
identical copies of the retained mixed-r1 checkpoint. The intended weighting-pipeline
difference is historical rehearsal loss mass: 25% for
`intent-semantic-r4.json`, 50% for `intent-semantic-r4-balanced.json`. Use seed
20260927, batch 16, encoder learning rate 5e-6, head learning rate 2.5e-5,
at most three epochs, validation-only checkpoint selection and separate
calibration. Serve both with the existing 0.2/0.8 decision thresholds. Compare
the frozen new test, previously inspected restriction probe, captured intake,
and historical manual/challenge diagnostics on identical inputs. Retain the
uniform r4 design as a preflight control, not a third training arm. Neither
candidate is automatically eligible for promotion.
