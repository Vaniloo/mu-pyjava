# Open datasets for `tool.intent`: source review

Reviewed 2026-10-01. Our label asks whether **this proposed tool call, with
these arguments, follows the current user's request**. Generic function-call
training often asks the agent to generate a call, and task-level abstention
benchmarks ask whether the whole workflow should proceed. Those are related
tasks, not automatic Boolean labels for this judge.

| Source | Published scope and license | Fit and initial use |
|---|---|---|
| [ScopeJudge](https://huggingface.co/datasets/dreadnode/scopejudge) | 100 complete trajectories with 4,897 candidate calls, each labeled in/out of scope by five security experts; MIT. | Closest pre-execution call-level labels. Reserve task families for independent evaluation first. Offensive-security tools, long transcripts, and contested labels need an explicit projection into our current state before any training sample is valid. |
| [ToolACE](https://huggingface.co/datasets/Team-ACE/ToolACE) | 11.3k English/Chinese synthetic function-calling conversations; Apache 2.0. The system prompt covers unusable functions and missing parameters, and the conversations include no-call answers. | Candidate source of varied positive/no-call language after manual conversion and exact tool-schema checks. It does not directly label a proposed `bash`, `edit_file`, or `write_file` action as authorized. It is synthetic, so it cannot alone solve the provenance problem. |
| [AgentAbstain](https://huggingface.co/datasets/antiquality/agentabstain) | 263 act/abstain task pairs (526 rows) in 42 sandbox environments, with eight abstention scenarios; CC BY 4.0. | Strong held-out paired evaluation and boundary taxonomy. Its task-level label may depend on environment state, tool failure, or a conflict absent from our judge input. Preserve the official test split as evaluation rather than copying it into training. |
| [AuthBench](https://github.com/evolvent-ai/Authbench) | 120 coding/terminal tasks with file-level read, write, execute permission gold and sensitive paths; MIT. | Useful coding-domain permission challenge. A file-level policy cannot be naively equated with whether one proposed call follows a request; construct and independently review concrete call pairs. |
| [BFCL relevance/irrelevance](https://github.com/ShishirPatil/gorilla/blob/main/berkeley-function-call-leaderboard/bfcl_eval/data/README.md) | Function choice and no-call relevance benchmarks across several languages; official repo and data are Apache 2.0. | Useful external tool-selection check and source of no-call formulations, but relevance does not cover explicit authorization revocation or side-effect scope. Keep benchmark samples out of training if used as a benchmark. |
| [AgentJudgeBench](https://huggingface.co/datasets/ServiceNow-AI/AgentJudgeBench) | 3,808 base workflow records with tool schemas, expected sequences, multiple generator outputs, and programmatic scores; Apache 2.0. | Lower priority: good for whole-sequence tool selection/argument quality; its scores and LLM-judge verdicts are not per-proposed-call authorization truth. |

## Pilot protocol

1. Select **ScopeJudge as an external evaluation pilot**, with a small,
   trajectory-stratified sample. Preserve the original five votes and majority
   label; do not collapse non-unanimous calls into unquestioned gold. Determine
   which user request, transcript span, tool name, and arguments are visible to
   our current judge. Mark cases unprojectable when decisive context is missing.
2. Use ToolACE only for **candidate training material**, first hand-reviewing a
   small English/Chinese batch of actual no-call and call turns. Construct
   proposed local-tool actions with verified argument transport and independent
   labels. Record source row IDs, transformation, license and family. Do not
   infer authorization from the assistant's output alone.
3. Add independently authored coding requests and actual Agent projections to
   cover the r4 failure forms: explain-only with an embedded concrete command,
   read-only requests paired with write proposals, inspect-one/edit-another,
   revoked consent, and allowed actions with unrelated restrictions. Keep all
   variants of one task in the same split, and keep existing inspected probes
   outside training.
4. Run the retained scope-r1 and any new candidate against untouched internal
   diagnostics plus external held-out cases. Report false allows, false declines,
   known abstentions, unknown abstentions and reviewer disagreement separately.

No external dataset has been imported or used to train a checkpoint in this
review. The current r4 candidates remain shadow-only.

The first [ScopeJudge transfer diagnostic](results-scopejudge-transfer-r1.md)
and spread ToolACE sample audit are complete. They strengthen the case for
independent coding-task labels and a richer, length-checked judge input before
another training run; they do not turn cross-domain scores into deployment
claims. No external records were used for training.
