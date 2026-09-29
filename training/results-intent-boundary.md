# Blind intake and focused boundary diagnostics — milestone 19

Status: diagnostics frozen; actual serving comparison pending. No retraining or
checkpoint promotion. [Dataset](datasets/intent-boundary-r1/README.md) contains 72
constructed cases in 12 bilingual families, with one shared action under positive,
negative and missing-context requests. It is an inspected-failure diagnostic, not
fresh blind human gold. Compare mixed-r1 and scope-r1 with their shipped temperatures,
unchanged question/criteria and 0.2/0.8 thresholds; never execute proposed tools.

The new `intent_review.py` creates prediction-blind exact-input packets, empty
human labels and frozen provenance. The packet omits observed judgments, policy,
backend names and source metadata. An audit checks binding, label provenance,
collection attestations, duplicate conflicts and overlap with explicitly listed
prior input files. It creates neither training data nor model-quality metrics.

The published demonstration uses 15 already published constructed harness samples.
All labels are unreviewed and all groups are classified constructed_harness:
**0 reviewed real-project cases**. They cannot become real-project evidence just
because a human later labels the synthetic task. Human identity/collection
attestations are not independently verified by code. Private real-project records
remain opt-in and must not be committed automatically.

Tracking: [milestone 19](https://github.com/Vaniloo/mu-pyjava/issues/19).
