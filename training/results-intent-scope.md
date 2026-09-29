# Scope paraphrase experiment — milestone 18

Status: candidate training in progress. No new checkpoint result or promotion yet.

## Frozen plan

Initialize from a separate mixed-r1 checkpoint copy;retain the original weights. Three epochs,
batch16,encoder LR5e-6,head LR2.5e-5,seed20260927,max sequence512. Select epoch using validation
soft cross-entropy only;fit temperature on separate calibration only. Test,challenge,manual and
old diagnostic results do not select weights or calibration.

[Dataset](datasets/intent-scope-r1/README.md):12,195 train,1,602 validation,1,602 calibration,
2,130 test. Rehearse all7,209 previous mixed train states unchanged and add4,986 new rows.
Whole file/command bilingual phrase families and file-goal families are isolated. Prerequisite
templates share structure with held-out executable goals. These are synthetic families with
conceptual/lexical overlap,not independent real-project conversations.

Exact old manual/control/holdout inputs and the new[24-case challenge](scope_challenge.jsonl)
are excluded from exports. Challenge covers multi-sentence instructions,quoted background,
revocations,different target scope,proposal-only requests,commands and prerequisites;10 positive,
10 negative,4 unknown. It was frozen before training and never becomes training/calibration data.

DeepSeek reviewed all24 inputs without construction labels or judge predictions:24 agreements.
This external-model check is teacher-origin evidence,not independent human gold. Existing
pending human-review packets remain unlabeled. No review changes training targets.

## Planned actual serving checks

Original Laya,v1,mixed and candidate use the current intent question,criteria and0.2/0.8 app
thresholds with each shipped calibration. Compare new test and challenge;retain old manual,
1,164 single-factor/336 joint diagnostic states,mixed test,synthetic-r2 test and uncertainty test
as inspected retention evidence. The old diagnosed phrases informed the new training design;
their improvement must not be presented as fresh blind generalization.

Follow valid candidate inference with the existing bounded DeepSeek shadow harness,retaining
actual arguments and constructed negative/unknown controls. Permission behavior stays unchanged.
Publish all metrics,predictions,hashes and live limitations when complete.

Five dataset guards pass:argument role balance/real command schema,whole template partitioning,
preservation of previous train provenance,protected-input exclusion,balanced new holdouts and
frozen-manifest validation. Full software gate pending.
