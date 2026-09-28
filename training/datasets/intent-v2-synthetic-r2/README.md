# Corrected synthetic experiment r2

4,240 controlled bilingual rows, 56 scenario families: 2,500 train and 580 each for
validation, calibration and test. Command metadata uses the actual `timeout` schema.
These are synthetic labels; shared templates were inspected in r1. This is not a fresh
blind test. Use `--allow-synthetic-eval`; Laya remains shadow-only.

The manifest binds the decompressed JSONL rows. `ambiguity.jsonl` is a separate,
untrained set of missing-context inputs with null labels. See ../../results-intent-v2.md.
