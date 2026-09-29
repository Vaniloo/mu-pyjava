# Focused boundary diagnostics

72 constructed cases, 12 bilingual families, 24 true / 24 false / 24 unknown.
Each family proposes one identical tool action under three English and three
Chinese requests. Four response-only file proposals, four installed-executable
prerequisites, four compound commands with an extra operation. Five actual intent
tools: edit_file, write_file, bash, run_command and powershell. Proposed commands
are data and are never executed, including installation, publication and deletion.

Frozen before mixed-r1/scope-r1 inference. These cases were designed after inspecting
scope-r1 failures, with conceptual overlap. They are controlled diagnostics, not
independent human gold or deployment error estimates. They must not select weights,
calibration or thresholds. `split=diagnostic` is rejected by training preflight.

The builder rejects exact overlap with the explicitly supplied prior input files.
For this freeze those are scope-r1's full 17,529-row dataset, the 24-case scope
challenge and the 20-case manual regression (17,573 distinct protected inputs).
This check does not establish absence of semantic overlap or all historical inputs.

Reproduce with `training/build_intent_boundary.py` and those three `--exclude-data`
arguments. `manifest.json` binds the row digest. See the milestone 19 results report
for serving comparison and intake limitations when complete.
