# Request budgets and tool text admission

The agent now prepares a bounded projection before every model request. The original `Agent.messages`, completed-session messages and full structured tool results remain intact. Projection never runs a stored tool or changes workspace files.

## Settings

The CLI and Java-launched backend read these environment variables at startup. Library callers can pass `ContextSettings` to `Agent`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `MU_CONTEXT_TOKENS` | 65536 | Configured context ceiling, including answer reserve |
| `MU_RESPONSE_TOKENS` | 8192 | Space reserved for the answer; sent as `max_tokens` by the Chat Completions adapter |
| `MU_TOOL_RESULT_TOKENS` | 8192 | Per-tool text allowance (four UTF-8 bytes per unit), before total-request fitting |
| `MU_IMAGE_TOKENS` | 4096 | Estimated charge for each image accepted by the model |

The default input limit is 57344 estimated tokens. Values must be integers; the response reserve must be smaller than the context ceiling. Tool allowances must be at least 256. No provider/model context window is inferred from its name. Set the ceiling to match the endpoint you use. The adapter applies the agent's response reserve on each request, including library-created adapters. Custom `ChatModel` implementations are responsible for limiting their own output; the agent still projects their inputs.

### Estimation limits

The estimator counts UTF-8 bytes of compact JSON containing model-visible messages and tool schemas, divides by four, adds 16 per prepared message, and charges the configured amount per image. It includes assistant tool arguments, system instructions, tool definitions, omission notices and retrieval pointers. Base64 image transport bytes are replaced with a placeholder before estimating; they are not counted as ordinary text. Text-only models include their image omission notice instead of a vision charge.

This is a deterministic heuristic, **not the endpoint's tokenizer or a guarantee that its actual token limit will accept the request**. Unicode/tokenizer differences and image sizing/detail can change provider usage. Provider usage-based calibration, automatic model discovery and endpoint-limit recovery remain future work.

## Projection order

1. Validate assistant/tool reply pairing. Reject duplicate IDs within an assistant batch before executing that batch; reject orphaned or missing replies before sending context.
2. Preserve the system prefix and latest user turn, including its assistant arguments and every tool reply. If the full request estimate is too large, remove older turns from oldest to newest as whole units. No tool reply is detached from its call.
3. Add an omission notice and reserve bounded space for source-grounded excerpts when the agent's summary mode is enabled. Canonical history still holds those turns.
4. Optionally classify middle chunks of retained eligible tool replies through `tool.admission`. Archive semantic omissions with exact byte pointers. Add source-grounded excerpts from omitted completed turns. Limit tool text to its per-tool allowance, keeping UTF-8-safe head and tail snippets. If the total still exceeds the input limit, reduce the largest remaining tool text deterministically until it fits or only retrieval markers remain; drop the optional summary if necessary.
5. If system instructions, latest request, assistant arguments, tool definitions, required reply markers and image charges still do not fit, block **that** model request. No prompt/argument/schema is silently cut, and requested vision attachments are not silently removed. Use a smaller request, smaller attachments or a larger configured budget.

The per-tool allowance limits raw UTF-8 text bytes; total-request fitting also counts JSON escaping and protocol metadata. Hard budget rules can shorten source reads and errors even though they bypass semantic classification. Source content in the middle can be omitted; its explicit marker tells the model how to read it back. Request smaller pages when retrieving a large archive under a small tool allowance. The semantic stage classifies output kind rather than task relevance; configuration and summary evidence rules are in [SEMANTIC_CONTEXT.md](SEMANTIC_CONTEXT.md).

The blocked request may follow tools already executed earlier in the turn. Such actions are recorded and not rolled back. In server mode the turn becomes interrupted, its messages are excluded from restored model context, and subsequent turns receive the existing workspace-inspection notice. A prompt that fails the initial preflight makes no model call and executes no tool.

## Full text archives

Every shortened tool reply gets a UUID-named, owner-only `.log` archive containing its complete tool text **before request shaping**. A middle-omission marker contains the ID and the `read_tool_output(id, offset, limit)` retrieval instruction. Head and tail are retained where the remaining budget allows them; the strict minimum is the retrieval marker.

`read_tool_output` is the general retrieval name; `read_command_output` remains a compatible name over the same storage. Both accept canonical UUID IDs and byte offsets, return at most 50000 bytes per call (default 12000), and expose `next_offset` for more output. A byte page can begin inside a UTF-8 character and then displays the existing replacement character behavior; choose aligned offsets when exact text matters. An archive contains only the text the tool actually returned: a paged file read archives that page, and an already truncated command result archives its preview/metadata. Commands' separate full-stream archive remains available through its original output ID.

Archives use the active session's `.outputs` directory. Their `tool.artifact` events are recorded on the conversation path using them, and independent forks copy referenced archives with the existing session mechanism. An in-memory content-hash cache reuses existing text archives within the same directory; a new turn re-registers each used ID on its path. Restart can create a new archive from canonical saved text without rereading the workspace or executing a command. Archive creation failure blocks sending a shortened request; cancellation deletes a partially written archive.

CLI prompt runs use the tools' output directory (normally the system temporary directory). Journals and archives contain source/tool data and need the same privacy as the workspace. There is no automatic archive retention/cleanup policy. Input budgeting does not bound the size of canonical history, journals or the process's memory.

## Records and desktop

Each model preflight emits a version 1 `context.budget` journal record with estimator name, configured ceiling/reserve/input limit, original/projected estimates, omitted-turn count, shortened-tool records and a blocked flag. Shortened records include tool-call ID, source message index, source/admitted byte counts, artifact ID and the reason (`tool_limit`, `request_limit`, `semantic`, or a combined semantic/budget reason). Optional admission/summary metadata records applied savings, timeouts, cache hits and included excerpt counts. These are request projections, not tool-execution events; full decisions are separate judge records. For a historical reply, its original call ID can appear in a projection recorded under the current turn.

Java receives:

- `context.detail`: readable estimated usage and omission counts, shown in the **Context** tab.
- `context.status`: versioned inner fields `v1`, estimated input tokens, input limit, response reserve, omitted turns, shortened outputs and `true|false` blocked, separated by tabs.
- `history.context`: saved projection summaries; history also returns the last `context.status` on the selected path.

The desktop shows approximate budget use and omission counts above the prompt. Session resets clear the Context tab and status only after successful acknowledgement; branch history restores its own records. CLI/smoke output also shows applied omissions and failures.

## mu comparison and validation

The source baseline is mu [`2dfd59c`](https://github.com/qybaihe/mu/tree/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6). Its [compaction implementation](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/compaction/compaction.ts) uses usage estimates, reserves and retained recent history with generated summaries. Its [admission feature](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/kyrn-judge/src/extension/features/admission.ts) archives omitted chunks, preserves boundaries, and can ask Jev about middle chunks while exempting source reads, images and errors from judgment.

The request-budget/archive/retrieval foundations now include optional typed semantic admission, retained task-frame constraints and bounded source-grounded summaries. Summaries select exact evidence rather than producing free-form paraphrases; older facts outside the bounded selection can still leave context. Provider usage calibration, independently evaluated classifiers, mu's asynchronous shadow/test-log strategies and generative compaction remain gaps. The trained Laya does not make admission decisions.

Tests verify whole-turn isolation, immutable canonical messages, UTF-8 archives and paging, multiple current tool replies, mandatory-input failures, schema/argument/image accounting, canceled/failed archive creation, malformed/duplicate call IDs, and reserved API output limits. Local HTTP fixtures recover a needle from omitted text, then restore and fork without rereading the source. Actual Java/Python process tests cover live shortened-output status, restored records and a blocked prompt with no endpoint call.
