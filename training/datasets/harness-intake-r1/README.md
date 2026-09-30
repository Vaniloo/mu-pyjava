# Runtime-captured diagnostic intake

Collected 2026-09-29; reviewed/archived 2026-09-30. **Not a ready training dataset.**

- 12 authored fixture tasks, 11 actor-proposed mutating calls in 10 task families.
- Actor: DeepSeek Flash via the actual Agent/tool-schema path. Mutations were
  stopped at the existing approval callback after judge-state capture.
- 33 controlled request replays reuse the exact captured full call arguments.
  The actual Agent creates all 44 exported states; file byte metadata is not
  manually invented. Scope labels are authored and separately teacher-reviewed.
- Five tool kinds: edit_file, write_file, run_command, bash, powershell.
- 44/44 labels agreed in a label/prediction-blind teacher review; the reviewer is
  the same model family as the actor. This is not independent human gold.
- One actual proposed bash call violated an explicit no-command request. It was
  not executed. The prose-only and missing-context tasks proposed no mutations.
- Schema, UTF-8 projection and direct-argv/shell transport checks passed for all
  exported states. These checks do not prove program availability or patch quality.
- Zero overlap with the registered historical inputs. Same-family variants must
  remain together in any subsequent use. No train/validation/calibration/test split
  is assigned; no learned judge was run; no new checkpoint was trained.

`cases.jsonl` contains 11 observed proposals, 11 explicit allow, 11 explicit decline
and 11 missing-context controls. Labels: 21 true /12 false /11 null. The 33
counterfactual requests are authored controls, not naturally occurring user traces.
All labels have synthetic provenance. Independent real-user trace count: zero.

`capture-evidence.json.gz` preserves fixture definitions, actor messages/results,
raw sampler records and submitted cases. The capture-only judge always abstains;
its plumbing outputs are not model predictions or training labels. Audit and
review files bind the exported states to the source calls and review inputs.

See [collection report and limitations](../../results-harness-intake.md).
