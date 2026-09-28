# Intent uncertainty r1

Frozen before model comparison: 3,709 bilingual synthetic/rehearsal rows in 57 families.
Train 2,629, validation/calibration/test 360 each. Unknowns 540/60/60/60 respectively.
Retains all 349 v1 training rows; 37 teacher candidates retain teacher provenance and only
enter train. Inspected manual20 remains separate. Five actual intent-triggering tools.

Natural requests omit tool names. Negatives include wrong paths, advice/revocation, publication,
deletion, remote writes and compound commands. Null means the latest request lacks its
antecedent; no previous conversation is provided to the judge. Training targets null at p=.5
with explicit opt-in and a manifest marker. Group isolation covers bilingual goal/phrase
families and all variants. Shared structural templates remain an experimental limitation.

Regenerate:

```sh
PYTHONPATH=python python3 training/build_intent_uncertainty.py --output work/new-uncertainty-run
```

Snapshot UUIDs change on regeneration; do not substitute a regenerated file under the frozen
manifest. No real-harness or independently human-reviewed holdout claim.
