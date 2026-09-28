# Mixed intent r1

Frozen controlled experiment, never independent human gold. Total9,465 exported rows:
train7,209; validation/calibration/test752 each, with176 duplicates excluded.
Each holdout has16 semantic families:4 file goals,4 exact command tasks and8 missing-antecedent
families (16 distinct unknown requests,160 unknown cases). Known holdouts have592 cases.

Import only prior train rows from synthetic-r2 and uncertainty-r1; canonicalize synthetic/
and natural/ family names together and force all imported families to train. Original v1
349 training states (including37 teacher candidates) stay verbatim. Other historical controlled
states use one injective path mapping, applied to user request and all arguments together.
This preserves path equality/mismatch, including hypothetical extension parameters. Changes
remain mechanically constructed labels, not new human review. Source round/sample/digest are
retained in tags. Prior validation/calibration/test rows are never imported.

Fresh goal cases include scoped prohibitions, explicit tool names, short clear requests,
read-only/revocation and mismatched actions. Shared argument signatures span true/false/null.
New command cases include explicitly requested publishing/deletion as intent=true as well as
unrequested/read-only proposals as false; intent necessity is separate from risk/permissions.
Dangerous examples are data only and are never executed. Historical extension samples stay
training-only; no new extension tool is installed.

`path-controls-v2.jsonl.gz` contains522 coherent path replacements,74 unknown. Request and
arguments are renamed together into unseen audit/ paths; parent identity/digest retained and
new input digest computed. The intervention split is deliberately invalid for training. It is
an invariance check on test parents, not an extra independent holdout. The v2 suffix is an input-
digest metadata correction made before inference, not an adaptive dataset change based on results.

Regenerate in a new directory:

```sh
PYTHONPATH=python python3 training/build_intent_mixed.py --output work/new-mixed-run
PYTHONPATH=python python3 training/intent_interventions.py \
  --data work/new-mixed-run/dataset/intent-v2.jsonl.gz \
  --output work/new-mixed-run/dataset/path-controls.jsonl.gz
```

UUIDs change on regeneration; use the published data/manifest for exact reproduction.
Structures and goal concepts still overlap prior synthetic scenarios. Old benchmarks are
inspected regressions; some old held goals appear in the other round's imported training.
The fresh holdout namespace is separate from all imported training families, but this is not
proof of broad real-world semantic independence. No default or active-mode promotion.
