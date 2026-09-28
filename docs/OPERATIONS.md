# Workspace operations

The agent uses fixed tool schemas and a permission gate. Workspace tools validate arguments, approve mutations, apply result limits and format model-visible results. Replaceable operations perform I/O. The Java protocol is unchanged.

```python
tools = WorkspaceTools(
    root,
    allow_write=True,
    allow_command=True,
    file_ops=backend,
    search_ops=backend,
    git_ops=backend,
    command_ops=backend,
)
```

An implementation may supply one or more operation groups. Omitted groups use local defaults; configuring an alternate file backend does not automatically move the other groups.

| Interface | Operations | Responsibility |
| --- | --- | --- |
| `FileOperations` | `exists`, `is_dir`, `list_dir`, `read_text`, `open_text`, `size`, `replace_text` | Supply text streams and directory entries; report exact UTF-8 text and byte size for mutation previews; commit replacements atomically |
| `SearchOperations` | `find`, `grep` | Discover workspace-relative paths; produce structured match/context records, honoring glob/ignore options and cancellation |
| `GitOperations` | `inspect` | Execute the supplied read-only Git argument list and return text; throw on failure |
| `CommandOperations` | `execute` | Run the supplied argument vector in the workspace, emit byte chunks, observe timeout/cancellation, stop descendants, and return an integer exit code |

Definitions live in `python/mupyjava/file_ops.py`, `search_ops.py` and `command_ops.py`. Local implementations use the filesystem, ripgrep, Git and processes respectively.

## Shared contracts

- File/Git/command paths use the agent's normalized absolute workspace namespace. Search output paths are relative to the workspace root. `find` returns `(paths, truncated)`; `grep` returns `(records, truncated)`, with each record carrying `path`, a positive `line`, `text`, and `kind` (`match` or `context`). Escaping result paths are rejected before presentation or reuse.
- `open_text` returns a context manager over text lines. The workspace tool owns paging, the 50 KiB response limit and continuation notices. `read_text` must preserve original newline bytes and BOM for mutation revision hashes. `list_dir` returns basenames, with `/` appended for directories.
- Search discovery should stop at 10,000 candidates; grep should respect its requested raw line bound. Tool code independently applies the public match, text and byte limits. The local adapter honors `.gitignore` through ripgrep.
- Command callbacks must be serial, contain bytes, and finish before `execute` returns or raises. The shared output collector retains a 12 KB tail, sends bounded updates, and stores the complete stream when truncated. `read_command_output` retrieves that local artifact even when the bytes came from an alternate backend.
- Cancellation raises `TurnCancelled`; timeout raises `subprocess.TimeoutExpired`; transport and I/O failures raise `OSError` or `ValueError`. Operations must settle outstanding I/O before returning. The tool boundary checks cancellation again after search, Git and command calls so a cancelled action cannot be reported as successful.
- Mutations still pass through the same approval/revision checks and per-path queue. `replace_text` must finish its atomic commit before returning. Cancellation around a completed atomic commit can leave a complete changed file; replay never repeats that side effect.

## Remote implementation requirements

This milestone provides interfaces and an in-memory alternate backend test; it does not ship an SSH transport. The test runs read → find → grep → edit → Git inspection → command verification without a local source file or subprocess, and exercises approval and full-output retrieval.

An actual remote backend must map the agent's workspace namespace onto a configured remote root, resolve remote symlinks under that root, and enforce containment on the remote host. The local path resolver alone cannot validate remote symlinks. Authentication, host verification, connection lifetime, reconnection, remote process-tree termination and atomic remote file replacement belong to that transport. A lost connection must report an uncertain operation as a failure and must not retry a mutation automatically.

Commands remain argument vectors with no shell interpretation. Bash/PowerShell syntax and a public custom-tool registry are separate parity milestones.
