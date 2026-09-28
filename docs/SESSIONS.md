# Sessions and desktop protocol

The Python engine owns conversation state. In server mode it keeps one active conversation path per workspace. Restart restores the explicitly selected session and path; if the selector is missing or invalid, it resumes the newest valid journal. The Java desktop loads its conversation and judgments at startup.

## Desktop actions

- **New session** starts an empty conversation.
- **Saved conversations** lists up to 200 recently modified journals. Select a conversation to see its beginning and latest 200 completed turns, including turns on sibling paths.
- **Continue here** selects a completed turn or the beginning. The next prompt becomes a child of that point. Later turns on the old path remain available in the same journal.
- **Copy as new conversation** copies the selected prefix to a new journal, with independent event IDs and an origin reference. The copy becomes active.

Selecting history changes model context and displayed history. **Workspace files keep their current contents.** The system prompt reminds the model to inspect relevant files before changing them. Selection and copying clear remembered permission grants; explicit launch overrides (`--allow-write`, `--allow-command`) still apply. The browser also states these effects before either action. Running turns, including pending approvals, block new-session, select and fork requests. Finish or stop the turn first. The desktop clears its transcript and judgments only after the backend acknowledges a successful change; an invalid request leaves the current conversation visible.

Only the beginning and completed-turn boundaries are selectable. A partial assistant/tool sequence is never exposed as a resumable checkpoint. History can show an interrupted turn, but its messages do not enter restored model context and its tools are never executed during restoration, selection or copying.

## Local journal and ancestry

Sessions live outside the project workspace in the platform's application state directory. Set `MU_SESSION_DIR` or pass `--session-dir` to choose another location. A SHA-256 digest of the resolved workspace path separates projects; each conversation is a UUID-named JSONL file. Directories and files are owner-only where supported.

Every line is a version 1 event with `event_id`, `parent_id`, `session_id`, `turn_id`, `tool_call_id`, timestamp, type and payload. New events link to the active leaf. Old version 1 journals without `parent_id` are interpreted as a chronological chain without rewriting their entries. `events()` returns the entire append-only journal; `branch_events()`, model restoration and displayed history follow only the selected ancestry. The journal records model-visible messages (including image blocks), display events, approval requests/resolutions, structured tool results and judge records. It contains prompts, tool arguments and outputs, so keep its directory private.

An atomically replaced `.active.json` in the workspace catalog records the active session plus each journal's leaf, observed chronological tail and workspace notice. If a process stops after appending an event but before saving its cursor, startup follows new children of that selected leaf. Unrelated branches are excluded. Incomplete turns receive interruption records linked to their own path, with no action replay. This storage supports one backend writer per workspace; cross-process locking is not implemented.

## Independent copies and output artifacts

A fork preserves only the selected ancestry. Its header contains `forked_from: {session_id, point_id}`; copied events receive fresh IDs and parents, while their original turn and tool-call IDs remain meaningful within that new journal. Saved image blocks, judgment history and tool results are preserved.

Visible command-output artifacts are copied byte-for-byte to the new session's owner-only `.outputs` directory with the same retrieval IDs. The copy remains readable through `read_command_output` after the source outputs disappear. Missing artifacts fail the operation and remove its temporary journal and output directory; the selected session remains unchanged. Journal copying is staged as `.pending` until artifacts are ready. An abrupt exit can leave staging files, but they are excluded from browsing and resume. Cleanup of abandoned staging files is manual for now.

## Line protocol

The `EVENT` envelope carries Base64 UTF-8 text. In addition to `CHAT`, `APPROVAL` and `CANCEL`, Java sends these controls (fields are separated by tabs):

| Request | Response |
| --- | --- |
| `HISTORY request_id` | `session.info`, `history.transcript`, `history.judge`, `history.done` |
| `SESSIONS request_id` | `session.item` records, `session.list_done`, `done` |
| `POINTS request_id session_id` | `session.point` records, `session.points_done`, `done` |
| `SELECT request_id session_id point_id` | `session.reset`, selected history, `done` |
| `FORK request_id session_id point_id` | `session.reset`, copied history, `done` |
| `NEW request_id` | `session.reset`, empty history, `done` |

`session.info` contains the raw session UUID. Inner catalog records are separately versioned:

- `session.item`: `v1`, canonical session UUID, Base64 title, Base64 ISO timestamp, parent session UUID or empty.
- `session.point`: `v1`, canonical point UUID, Base64 title, Base64 ISO timestamp.

Session and point IDs are validated before opening any journal; a header must match the resolved workspace. Failed controls emit `error` and `done` (historical `HISTORY` succeeds with `history.done`); they do not emit `session.reset`. The Java browser associates list/point responses with their request IDs, so stale results cannot replace a newer selection. Prompt titles are rendered as plain text.

`CANCEL` addresses the active request ID; command output arrives as `tool.update`, and a stopped turn ends with `cancelled` then `done`. Unknown or repeated approval IDs cannot resume an action. Permission grants are cleared on restart and every path/session switch.

## Validation and remaining gaps

Tests cover sibling-path isolation, beginning checkpoints, old journal compatibility, selected-session restart, stale cursor recovery, interrupted branches, invalid IDs/workspaces, independent image/judgment/output copies, missing-artifact rollback, hidden interrupted forks, and the actual Java/Python catalog/select/fork/restart connection. A local HTTP model fixture verifies mutation grants are reset, busy switches are rejected and selection/forking never rerun file writes or commands.

Arbitrary-entry branch navigation, a graphical tree, pagination beyond the displayed limits, import/export, cross-process writers, background turns, context compaction and provider-side HTTP abort remain separate work.
