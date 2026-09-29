# Joint replacement-size follow-up

336 deduplicated states:112 positive,112 negative,112 unknown; two captured edit argument seeds,
two languages and four equivalent requests per label/language.288 pairs change old and new
replacement sizes **together**, at1,16,19,62,150,2048,65,536 bytes. This is a two-field extension
designed after inspecting the first single-field pass, not a blind or single-field experiment.

Shares48 anchors with [r1](../intent-ablation-r1/README.md). Synthetic scope labels are preserved;
no model prediction becomes truth and no action executes. All rows are interventions and the
manifest is explicitly not ready for training. Results are diagnostic, not independent gold.

Rebuild:`training/intent_ablation.py build --joint-bytes --output NEW_DIRECTORY`.
See the [report](../../results-intent-ablation.md).
