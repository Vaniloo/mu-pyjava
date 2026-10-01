# ScopeJudge external transfer and ToolACE sample audit

Completed 2026-10-01. This is a **cross-domain diagnostic**, not a score on the
official ScopeJudge benchmark and not a measurement of coding-agent deployment
accuracy. No external examples entered training, validation, calibration, or
checkpoint selection. No source commands were executed.

## Pinned source and exact projection

[ScopeJudge](https://huggingface.co/datasets/dreadnode/scopejudge) revision
`b8b06a65a09e39a4fe1682ef56f48fd14ab74800`, `train.jsonl` SHA-256
`63a176fde1933464dc1cbfe3d5adcae8d4991ab0f93368903276c55a74efb527`,
has 100 trajectories and 4,897 proposed calls. All calls joined to exactly
one five-expert majority label. The source's 3,728 `bash` calls are the only
tool type close to our current `bash` contract. `project_scopejudge.py` maps
`cmd` to `command` and preserves any valid timeout, request text, source ID,
task family, all-vote count, and majority label. It excludes 1,169 other tools,
212 `bash` calls whose request exceeds the Agent's 1,000-character limit, and
107 calls with unsupported arguments such as `cwd`. The resulting 3,409 calls
cover 29 source task families and 80 distinct requests: 3,074 in scope and 335
out of scope. Only the derived projection was sent to the lab. The original
source and raw serving reports remain in ignored `work/`.

The source experts saw the full preceding trajectory and a shared policy. Our
existing `tool.intent` receives only the request and current proposed tool
arguments, and asks whether the user's latest request **clearly calls for**
this action. A call may be in the engagement's scope but not explicitly needed
for the latest request. We therefore retain the expert labels as an external
reference and do not claim they are independently reviewed gold labels for the
transformed local state. This caveat also applies to the full-input subset.
The source paper explicitly compares intent-only and history-rich judge inputs;
our projection is closest to its intent-only view but uses a different judge
question and local tool schema.

Both retained scope-r1 and r4 rehearsal50 were run through the actual Laya
serving adapter at unchanged 0.2/0.8 thresholds. Dataset hash, row order,
labels, state and threshold-derived answers were checked before comparison.
Predictions are correlated within task family, so the number of calls is not
the number of independent tasks.

| Slice and model | In scope | Out of scope | Correct decisive | False allow | False decline | Abstain |
|---|---:|---:|---:|---:|---:|---:|
| All 3,409: scope-r1 | 3,074 | 335 | 493 | 27 | 2,310 | 579 |
| All 3,409: r4 rehearsal50 | 3,074 | 335 | 512 | 42 | 1,712 | 1,143 |
| Full token input: scope-r1 | 2,497 | 244 | 293 | 2 | 2,115 | 331 |
| Full token input: r4 rehearsal50 | 2,497 | 244 | 291 | 12 | 1,612 | 826 |
| Full input + unanimous experts: scope-r1 | 2,234 | 101 | 161 | 1 | 1,881 | 292 |
| Full input + unanimous experts: r4 rehearsal50 | 2,234 | 101 | 219 | 8 | 1,438 | 670 |

The shipped tokenizer and exact Laya sequence builder show 2,741 projected
calls fit the 512-token serving limit; 668 are clipped, including 91 out-of-
scope calls. Of the scope-r1 false allows, 25/27 are on clipped inputs; for r4,
30/42 are clipped. The command begins inside the visible portion for all
projected rows, but the tail can be missing. Clipping explains much of the
false-allow concentration, **not** the large false-decline count on complete
inputs. The latter is consistent with our training data's narrow style of
explicit, per-command requests and with the difference between ScopeJudge's
in-scope label and our clearer-action criterion. This test cannot separate
those two explanations or isolate model capacity.

The two evaluated checkpoints have identical tokenizer directories and the
same 512/256 sequence limits, so the token-fit slice applies to both.

The new r4 arm reduces wrong declines relative to scope-r1 on the same external
projection while increasing false allows and abstentions. This agrees in
direction with the protected older coding diagnostics and gives no basis to
promote r4. No runtime configuration or checkpoint changed.

## ToolACE suitability check

The [ToolACE](https://huggingface.co/datasets/Team-ACE/ToolACE) source is
Apache-2.0 synthetic function-calling data. We inspected the first ten rows at
12 spread offsets (0, 1,000, ..., 10,000, 11,200) from the published train
split, 120 rows total, at revision
`6bda777c88d21e5a204703c1ee45597a8fa4f734`. Manual inspection of the
first assistant turn found 95 bracket-form calls, four calls in other formats,
and 21 no-call replies. The no-call replies chiefly concern missing parameters
or unavailable functions. None of the 120 first user turns contained Chinese
characters, despite the dataset card's English/Chinese metadata. This
deterministic spread sample is **not a prevalence estimate** for the corpus.

These are useful examples of tool mismatch and missing parameters, but they do
not supply direct authorization labels for `bash`, `edit_file`, or `write_file`
in coding tasks. ToolACE should therefore remain a language/negative-scenario
source, with source IDs and hand-reviewed conversions, not bulk imported as
judge training rows.

## Decision

Do not retrain on the current r4 recipe or bulk ToolACE/ScopeJudge conversion.
First improve the input contract so task scope and the candidate action survive
length limits; keep the present model interface and weights frozen while a
versioned shadow input is tested. Collect actual coding-agent proposals from
multiple tasks, then obtain prediction-blind human labels for exact inputs,
including ambiguous cases and disagreement. Keep ScopeJudge and all previously
inspected probes outside training. A new model must reduce false allows without
merely refusing ordinary intermediate actions.

## Reproducibility

- [Pinned projection and rejection rules](project_scopejudge.py)
- [Serving token-fit audit](audit_intent_token_fit.py)
- [Validated aggregate comparison](results/scopejudge-transfer-r1.json)
- [Comparison analysis](analyze_scopejudge_transfer.py)

The aggregate JSON contains counts and checkpoint hashes only. It does not
publish the external request text, commands, source secrets or model weights.
