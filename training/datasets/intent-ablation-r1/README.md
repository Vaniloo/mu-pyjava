# Intent ablation r1: inspected diagnostic controls

1,164 deduplicated states:388 positive,388 negative,388 unknown. Four file-action argument
seeds (three captured fixture states and one inspected manual state), two languages,96 distinct
request strings including only eight unknown strings. Constructed labels describe action scope,
not correctness of unseen file contents. Neither predictions nor approvals supply labels.

`controls.jsonl.gz` and `manifest.json` freeze inputs, source/file hashes and parent-child pairs.
Wording and coherent paths vary separately; byte sweeps change only one field at a time and
repeat under four equivalent requests per label/language. Edit count/fuzzy flags also vary.
All tool-argument signatures support true/false/null. No proposed tool is executed.

All rows are `split=intervention`, `origin=synthetic`; `ready_for_training=false`. These inspected
counterfactuals diagnose sensitivity and cannot estimate deployment accuracy. Pairs share parents,
so counts are not independent trials. Joint-size follow-up is a separate, post-inspection
[extension](../intent-ablation-joint-r1/README.md). The union has1,452 unique states.

Rebuild using `training/intent_ablation.py build --output NEW_DIRECTORY`. Evaluate with
`training/evaluate_intent.py --split all`. See the
[report](../../results-intent-ablation.md) for evidence, limitations and comparisons.
