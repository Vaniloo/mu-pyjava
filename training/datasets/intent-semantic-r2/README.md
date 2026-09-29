# Archived pilot — invalid for quality claims

Do not train a replacement judge from this dataset. The original frozen manifest
records the checks available at commit `134c390`; a later transport audit found 25
inconsistent tool states. Current opt-in training preflight intentionally rejects
this immutable archive. No rows were corrected in place after inference.

See [results and correction](../../results-intent-semantic.md) and
[affected sample IDs](../../results/intent-semantic-r2/tool-state-audit.json).
