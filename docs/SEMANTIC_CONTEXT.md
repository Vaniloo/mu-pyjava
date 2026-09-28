# Semantic admission and source-grounded history summaries

This phase follows mu snapshot `2dfd59c`'s output-kind admission policy and
adds a bounded continuation summary when our context budget omits older turns.
Canonical messages, tool permissions and the trained Laya restrictions remain
the source of execution state. Request projections do not overwrite them.

## Output admission

`tool.admission` v3 is a dynamic choice-question specification. It defaults to
**off**, independently of `MU_JUDGE_MODE`. Each middle chunk is classified as
error, result, progress, warning, passing or unknown. Only accepted progress,
repeated-warning and passing classes are noise; the default confidence floor
is 0.9. Mixed chunks containing errors/results should keep those classes.
This follows mu's classification of what output is, rather than asking a judge
whether arbitrary text is relevant to a task. Classifier correctness is not
guaranteed by its confidence score.

The feature considers text at least 6,000 characters long, splits at line
boundaries with a 1,200-character bound, and considers at most 48 chunks.
Oversized lines are split without changing their text. First/last chunks are
never submitted. Reads, output-retrieval calls, writes/edits, image-bearing
results and error results pass through this semantic stage. Older journals
without explicit error metadata also pass through. Hard context limits can
still shorten any text with a full-text pointer, including error/source text.

Hosted typed routes answer batches of at most 16 chunks, further reduced to
keep serialized state within the kernel's 64 KiB bound. Routes resolve only
unanswered questions. Missing, malformed, uncertain and low-confidence answers
keep their individual chunks. A shared deadline covers every batch and route
for one tool output: `MU_ADMISSION_WAIT_SECONDS=4` by default, 0.01–30 allowed.
If the complete feature misses that deadline, it applies no semantic omissions.
Cancellation stops preflight. Late answers cannot update a returned plan.
HTTP/inference itself may continue until the backend's timeout.

Shadow records proposed omissions and sends the ordinary budget projection.
It is synchronous and uses the same bounded wait, unlike mu's background
shadow path. Completed classifications are cached in memory by text, producing
call and policy; a cache hit is recorded with the current tool-call ID. Cached
classifications are not persisted across restarts. Transient all-unknown or
timed-out plans are retried on a subsequent preflight.

Active omissions merge consecutive noise chunks into a marker with class,
original UTF-8 byte range and `read_tool_output` ID. The archive contains the
exact original returned text. Archive failure stops the shortened request;
forks copy referenced archives. For commands already truncated by the command
layer, this archives the returned preview; their separate full-stream output
ID continues to retrieve the full command output.

Example judge configuration, referenced by `MU_JUDGE_CONFIG`:

```json
{
  "version": 1,
  "backends": {
    "semantic": {
      "type": "model",
      "model": "your-classifier-model",
      "api_key_env": "MU_JUDGE_API_KEY"
    }
  },
  "points": {
    "tool.admission": {
      "mode": "shadow",
      "routes": ["semantic"],
      "min_confidence": 0.9,
      "timeout_seconds": 4
    }
  }
}
```

Use active only with a classifier evaluated for this task. The trained Laya
checkpoint and legacy YES/NO adapter cannot answer this point. This phase uses
local HTTP fixtures, not a new accuracy benchmark or a retrained judge.

## History summaries

`Agent` defaults to `MU_SUMMARY_MODE=extractive`. A summary is built only when
older whole turns are omitted. It retains at most 12 exact source excerpts,
including the first task and bounded recent user/assistant/tool evidence.
Each excerpt retains its role and source-message index. Long messages expose
separate exact head/tail excerpts; the system never joins them into an invented
sentence. These are historical statements, not proof that an assistant's claim
or old tool observation is still true. Inspect current workspace state before
depending on them. Retained task-frame constraints are carried separately.

`MU_SUMMARY_CHARS=2400` limits the rendered note, with 512–8,000 allowed. The
budget reserves at most 1,024 estimated input tokens (or one quarter of a smaller
input limit) during omission planning. It may omit additional older turns to
make space. Excerpts are reduced further to fit; if mandatory current input
cannot fit with any summary, the summary is removed before blocking the request.
The original journal keeps every message. Off mode retains the previous
whole-turn omission behavior. Bare `ContextBudget` library callers must pass a
`TaskSummaries` instance explicitly.

Optional model selection uses:

```sh
export MU_SUMMARY_MODE=model
export MU_SUMMARY_MODEL=your-summary-model
export MU_SUMMARY_WAIT_SECONDS=4
# Optional: MU_SUMMARY_API_BASE and MU_SUMMARY_API_KEY
# Otherwise the writer inherits MU_API_BASE and MU_API_KEY.
```

The writer receives bounded original excerpts, no tools, and at most 2,048
output tokens. It selects source indexes and quotes using a strict JSON
envelope. Quotes must occur verbatim in a submitted excerpt; invented text,
paraphrases, joined snippets, incorrect indexes, duplicate fields and malformed
outputs are rejected. A timeout, unavailable writer or invalid response falls
back to deterministic extraction. The one-worker limit prevents abandoned
writers accumulating across turns. Cancellation propagates without storing a
late writer result. This is grounded model selection, not mu's free-form
generative compaction/frame writer; semantic paraphrasing remains future work.

## Persistence and desktop

`context.budget` v1 adds optional admission counts/status and summary metadata.
The existing v1 `context.status` wire fields remain compatible. Full typed
answers are `judge.record` entries. `context.summary` schema 1 stores excerpts,
role/index provenance and a SHA-256 fingerprint of the source conversation
prefix, excluding derived system notes. Summary records contain original
conversation data and have the same privacy/storage requirements as journals.

On restart or session selection, only records whose source prefix and every
quote match the selected completed-message path are eligible for reuse.
Interrupted model/tool history is excluded; task-frame user constraints retain
their existing recovery semantics. Independent forks copy their selected
summary records and artifacts; sibling/new paths cannot reuse mismatching
evidence. No model or tool executes during restoration.

Java shows admission savings in **Context**, decisions in **Judgments** and
`summary.detail`/`history.summary` in **Task**. The process smoke path prints these
events too. GUI visual presentation has not been manually inspected in this phase.

## Validation and remaining parity

Tests cover exact Unicode chunking, endpoints/errors/results/unknowns, shadow,
partial invalid answers, low confidence, multi-route deadlines, cancellation,
archive failure, byte recovery, metadata-free API payloads, strict evidence,
writer failure/timeout, request fitting, source tampering, selected paths,
restart and independent forks. A local HTTP fixture runs actual Java/Python
processes through classification, recovery of omitted text, model selection,
restart and fork restoration without the original output source/archive.

Our budget still chooses older turns before shaping retained tool replies;
admission does not prevent every otherwise avoidable whole-turn omission.
Other remaining gaps include independent accuracy/savings/latency evaluation,
asynchronous shadow work, mu's test-log repetition strategy, choice-distribution
pooling, free-form summaries/frame generation, exact provider usage and HTTP
abort. This phase does not enable automatic approval or widen Laya's scope.
