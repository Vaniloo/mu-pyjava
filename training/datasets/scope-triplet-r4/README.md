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
checks. The authored labels are hypotheses pending prediction-blind review. No
candidate prediction, training result, or independent human gold is present in this
freeze. A later dataset freeze must verify review digests and agreement, preserve
the family partitions, and report any exclusions before training.
