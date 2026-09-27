# Sessions and desktop protocol

The Python engine owns conversation state. In server mode it keeps one active session per workspace and resumes the newest one when restarted. The Java desktop requests its history at startup, shows the restored conversation and judgments, and can start a fresh session with **New session**.

## Local journal

Sessions live outside the project workspace in the platform's application state directory. Set `MU_SESSION_DIR` or pass `--session-dir` to use another location. A SHA-256 digest of the resolved workspace path separates projects; each conversation is a UUID-named JSONL file. The directory is owner-only where supported, and a new journal file is created with owner-only permissions.

Every line is a version 1 event with `event_id`, `session_id`, `turn_id`, `tool_call_id`, timestamp, type and payload. The journal records model-visible messages, display events, approval requests/resolutions and judge records. It therefore contains the text of prompts, model tool arguments and tool results; keep the selected session directory private.

A turn enters the model context after `turn.completed` has been written. If the process stops mid-turn, startup appends `turn.interrupted` and restores only completed turns to the model. The desktop still shows the interrupted turn and a notice. Executed tools are never replayed from the journal. Session-scoped permission grants are cleared on backend restart and when a new session starts.

## Line protocol

The existing `EVENT` envelope carries Base64 UTF-8 text. Java sends `CHAT`, `APPROVAL`, `HISTORY` or `NEW` requests. `HISTORY` returns `session.info`, `history.transcript`, `history.judge` and `history.done` events. `NEW` starts an empty session and returns its ID. Tool approval requests and answers remain versioned separately; unknown or repeated approval IDs cannot resume an action.

This is the first durable session format. It supports latest-session resume and a new-session action; branch/fork, import/export and context compaction remain future work.
