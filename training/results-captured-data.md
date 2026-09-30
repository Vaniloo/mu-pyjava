# Captured workflow dataset — milestone 23

Completed 2026-09-30. Plan committed as `8eb1f28` before actor calls; data and
comparison protocol frozen as `93d28c2` before learned-judge inference.

42 distinct authored workflows were assigned to train/validation/calibration/test
as 24/6/6/6. Actual DeepSeek calls passed through Agent; all mutation proposals were
blocked at approval after sampling. Each task supplies its own permission, refusal
and missing-context request. Replays preserve original full arguments and obtain
the judge state from Agent; no manual byte-count fabrication or path/number sweeps.

| Partition | Planned families | Retained families | Fresh rows | Rehearsal rows |
|---|---:|---:|---:|---:|
| Train | 24 | 20 | 69 | 382 |
| Validation | 6 | 4 | 12 | 0 |
| Calibration | 6 | 4 | 12 | 0 |
| Test | 6 | 5 | 15 | 0 |

Final 490 rows include 108 fresh states in 33 families. Each fresh partition has
balanced true/false/null labels. No family was reassigned to compensate for missing
coverage. Exact exclusion includes the inspected semantic-r2 pilot and intake-r1.
Ready-for-training means technical guards passed, not reliable model behavior.

## Exclusions and review

Three tasks produced no captured mutation within the step limit (retention_doc,
route_table, ratio_zero). sum_bytes proposed bash redirection and then switched to
run_command with the same redirection; the transport guard excluded its whole
family. This caught an actual actor fallback error before dataset admission.

141 rows entered label/prediction-blind teacher review; 139 hypotheses agreed.
Whole-family exclusions: compile_python, perl_syntax and python_help had duplicate
inputs; ps_count had a label disagreement; ps_size had both disagreement and
repeated inputs. Labels were never changed to match a reviewer or candidate.
Two historical rehearsal rows with shell/direct-argv defects were also removed.
Rehearsal preserves historical synthetic provenance; its full original payloads
were not reconstructed. Full-call/projection checking applies to new captures.

One malformed review batch caused the first freeze attempt to fail with an empty
test partition. Before inference/freeze, a bounded same-input retry was made only
for that failed batch. Successful/disagreeing reviews were not retried. This is an
explicit recovery amendment to the initial quarantine-only procedure; both attempt
statuses and the missing-review failure are documented. No malformed response body
was retained by the client; only its ValueError category is available.

## Coverage and limitations

New training covers edit_file (27), write_file (18), run_command (9), bash (12), and
powershell (3). Validation lost run_command and powershell coverage; calibration has
no bash; test has no run_command. These gaps are recorded, not filled after seeing
results. The test contains only five families and ten known/five unknown examples.
Small changes in counts must not be treated as evidence of broad reliability.

Tasks, scope hypotheses and counterfactual requests are authored synthetic fixtures;
the actor and blind reviewer use DeepSeek Flash. No independent human gold or real
user telemetry exists. Different workflow IDs do not prove conceptual independence;
common request-scope patterns recur. Full action payloads support contract auditing,
but the judge itself still sees only path/size metadata for file mutations.

## Reproduction and safeguards

`collect_planned_harness.py` validates plan IDs/paths and collects per-task evidence;
`build_captured_dataset.py prepare` creates captured contrasts; `freeze` binds states
and splits to raw samples, applies whole-family quarantine, validates rehearsal,
then runs both design audits before writing any ready dataset. Plans are not derived
from observed model quality. The prepared case splits/state/digests cannot be edited
without rejection. Two end-to-end tests cover capture through freeze and tampering,
and reject path traversal/duplicate IDs/missing planned partitions.

Software verification: 243 tests, 242 passed and one native PowerShell skip.
Published raw evidence and labels were credential-pattern scanned, including gzip.
No task mutation was executed during collection. Training and later serving results
are a separate milestone; the prespecified recipe is in the dataset README.

[Dataset, manifest, reviews and raw capture evidence](datasets/intent-captured-r3/).
