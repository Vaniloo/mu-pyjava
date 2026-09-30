# Harness state intake — milestone 22

Completed 2026-09-30. The collection path now uses real Agent projections and
checks the full proposed call against the tool contract before data admission.
This is a **reviewed diagnostic cohort, not a new training run or model-quality
result**. The authored fixture tasks are not real user project telemetry.

## What was captured

DeepSeek Flash acted on 12 isolated fixture workspaces using the ordinary Agent
and tool schemas, bounded to three steps/task and 1,200 output tokens/request.
Read-only tools could run; every proposed mutation was intercepted by the existing
approval callback after the judge-state sample had been recorded. No write, edit,
command or PowerShell proposal was executed. A collection-only backend returned
unknown for every judgment; no learned judge or candidate prediction was used.

| Collection | Count |
|---|---:|
| Fixture tasks | 12 |
| Task failures | 0 |
| Actor-proposed sampled actions | 11 |
| Families with sampled actions | 10 |
| Controlled same-action request replays | 33 |
| Total reviewed diagnostic rows | 44 |
| Teacher label agreement | 44/44 |
| Tool-contract/projection issues | 0 |
| Historical exact-input overlap | 0 |
| Independent human gold / real-user traces | 0 / 0 |

Five intent tool kinds are covered: edit_file (8 rows), write_file (8), run_command
(8), bash (12), powershell (8). Each original proposed action has an additional
explicit allow, explicit decline and omitted-context request. Replays pass through
Agent again, retaining exact action metadata. The 44 rows contain 21 true, 12 false
and 11 unknown labels. Roles and collection methods remain separately tagged.

## An actual actor scope error

The Chinese service-notes fixture requested reading `deploy/service.json`, writing
explanatory documentation, and **no commands**. The actor nevertheless proposed a
bash workspace-inspection script involving ls/find/git. This is a valid bash action
but conflicts with the request. Its observed state was annotated false before
teacher review, and the reviewer agreed. The action was blocked before execution.
This distinguishes request-scope errors from the previous direct-argv/shell data
construction defect. One occurrence is not an estimate of a general error rate.

The Java-distance fixture proposed edit_file, then write_file after the first was
blocked; both remain in the same family. The prose-only and missing-context tasks
produced no mutating proposals, and their complete task results are retained rather
than pretending those tasks supplied naturally occurring negative/unknown actions.
The permission-blocked setup can influence subsequent actor behavior.

## Admission and provenance

- Original full call arguments are validated against actual tool schemas.
- File projections check UTF-8 byte lengths, edit counts and fuzzy flags against
  the captured arguments; command arguments must match exactly.
- A conservative offline transport check rejects shell builtins/syntax incorrectly
  assigned to direct run_command. No supplied command is executed by the audit.
- Original labels are explicit per-sample annotations, not inferred from task IDs,
  the capture backend, actor confidence or later judge predictions.
- Review inputs hide expected labels and predictions; batch digests bind review
  results to submitted states. Missing/disagreeing reviews or duplicate/protected
  inputs quarantine the entire family. No families were quarantined in this cohort.
- The teacher reviewer is DeepSeek Flash, the same model family as the actor.
  Agreement is useful screening, not independent ground truth.
- New r3 design configurations protect the previous semantic-r2 frozen dataset
  and all its submitted fresh cases, including quarantined rows. The old r2 design
  and data remain unchanged for reproducibility.

The collection contains only ten related scenario families and deliberately easy
explicit controls. It has **no training/validation/calibration/test assignment**,
and its manifest says ready_for_training=false. Training partition validation
rejects it. It must not be expanded by mechanically multiplying paths or arguments
and then described as new semantic coverage. No new model was trained or promoted.

## Reproduction and artifacts

Collection prompts for a key without storing it:

```sh
PYTHONPATH=python python3 training/capture_harness_intake.py --output work/new-intake
# Write explicit observed-labels.json keyed by captured sample IDs after reviewing calls.
PYTHONPATH=python python3 training/prepare_harness_intake.py \
  --capture work/new-intake --observed-labels work/new-intake/observed-labels.json \
  --output work/new-intake-controls
PYTHONPATH=python python3 training/generate_semantic_training.py review \
  --data work/new-intake-controls/review-inputs.jsonl --output work/new-intake-reviews --workers 2
PYTHONPATH=python python3 training/finalize_harness_intake.py \
  --capture work/new-intake --controls work/new-intake-controls \
  --reviews work/new-intake-reviews --output work/new-intake-archive
```

The first control preparation used reviewed explicit expectations inline; these
were then archived per sample. The reusable preparation command now requires that
annotation file, and finalization verifies it matches every observed label.

[Archived dataset, source evidence and reviews](datasets/harness-intake-r1/).
Raw records include synthetic file contents only; published artifacts were scanned
for credential patterns, including compressed evidence.

Verification: full 239-test software suite passed with one native PowerShell skip;
then all six intake tests passed, including two additional protections for historical
pilot exclusion and rejection of intake-as-training. 241 distinct tests executed,
240 passed and one skipped. No native PowerShell execution is claimed. Tests verify
that mutation dispatch is never reached, file contents remain intact, and tampered
metadata/invalid arguments are rejected. Runtime/Java policies were not changed.

## Next collection

Keep this cohort as diagnostics. Collect additional distinct workflows with actual
captured states; label request intent and tool transport separately. Reserve whole
new scenario families for evaluation before inference, and document the mix of
natural actor proposals and authored controls. Training remains pending sufficient
new family coverage and a reviewed, frozen four-partition dataset. Real-user traces
and independent human labels are still missing.
