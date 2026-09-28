# Structured tool results

`WorkspaceTools.execute_result(name, arguments, **options)` returns a `ToolResult`. Each result has text content blocks, structured `details`, and `is_error`. `execute(...)` remains a compatible text-returning adapter. Both APIs raise validation/I/O exceptions and propagate cancellation; the agent converts expected tool failures into error results.

```json
{
  "version": 1,
  "content": [{"type": "text", "text": "Exit code: 0\nHELLO"}],
  "details": {"exit_code": 0, "output_bytes": 5, "truncated": false, "artifact_id": null, "mode": "bash"},
  "is_error": false
}
```

| Tool family | Details |
| --- | --- |
| Read text | `offset`, `output_lines`, `output_bytes`, `truncated`, `next_offset` |
| List/find/grep | `count`, `truncated`; grep also records `record_count` including context lines |
| Git inspection | Displayed `output_bytes` and `truncated` |
| Write/edit | `change`: path, operation, before/after hashes and byte counts, diff, edit count |
| Command/shell | `exit_code`, total `output_bytes`, `truncated`, `artifact_id`, `mode` |
| Output retrieval | `artifact_id`, `offset`, page `output_bytes`, `truncated`, `next_offset` |
| Expected failure | `error_type`, error text, `is_error=true` |

A nonzero command exit code sets `is_error=true` while retaining its exit code and captured output. Timeout is an error result in the agent and also emits a command lifecycle event. Cancellation interrupts the turn and emits a cancellation lifecycle event, without manufacturing a completed result.

The agent persists a versioned `tool.result` event with the tool-call ID for each completed/failed call. Java receives a readable `tool.detail` summary; these summaries are saved for history. The Chat Completions adapter continues receiving the textual tool response. The first version supports text blocks; image blocks and model-aware conversion remain future work.

## Shell tools

`bash` and `powershell` accept `command` (the complete script) and optional `timeout` (1–120 seconds, default 30). They share the command permission gate, fresh per-call approval, process cancellation, streaming, bounded tail and full-output retrieval. `--allow-command` is the explicit broad override for all three execution tools.

- Bash uses `--noprofile --norc -c` and supports normal pipelines, redirection, variables and multiline scripts.
- PowerShell prefers installed `pwsh`, falling back to `powershell`. It uses `-NoLogo -NoProfile -NonInteractive -Command` with a UTF-8 output prefix. Missing executables produce a clear tool error. Bash and PowerShell can each be supplied by an alternate `ShellOperations` backend.
- `run_command` retains its argument-vector execution semantics.

The workspace is the starting directory, not an operating-system sandbox. Scripts execute with the user's filesystem/network permissions. The desktop displays the full script before approval. Shell calls cannot inherit a write-only grant or a file session grant. The experimental trained judge remains shadow-only; its dataset does not yet cover these shell tools.

Verification: Bash pipeline/redirection, denied execution, background-child cancellation, timeout, large output retrieval, structured metadata and Java approval are tested. PowerShell argv/UTF-8 setup and alternate transport are tested. The native PowerShell test runs only when an executable is installed; it was skipped on the development Mac.
