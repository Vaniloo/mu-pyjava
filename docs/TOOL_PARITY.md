# Tool parity with mu

This project follows the tool workflow in [mu's coding-agent tool definitions](https://github.com/qybaihe/mu/tree/main/packages/coding-agent/src/core/tools). It is an independent Python implementation, not a source-level port. This table records behavior, not a count of tool names.

The current behavior and architecture comparison is in [MU_COMPARISON.md](MU_COMPARISON.md).

| mu capability | mu-pyjava today | Remaining gap |
| --- | --- | --- |
| `read`: text paging with line/byte limits | `read_file`: 1-based `offset`, `limit`, 50 KiB response limit | Images and model-aware image resizing |
| `find`: glob search with ignore rules | `find_files`: ripgrep-backed glob discovery, Git ignore rules, result limit | Broader ignore semantics and search on remote workspaces |
| `grep`: regex/literal, case folding, context, limits | `grep_files`: those options via ripgrep, structured path/line results | More complete output metadata and search result rendering |
| `ls` | `list_files`: one directory, 200 entries | Pagination and metadata |
| `edit`: targeted replacement | `edit_file`: multiple unique, non-overlapping replacements against original content; BOM/CRLF preservation, unified diff approval, revision check and atomic replacement | Fuzzy matching fallback, standard patch/navigation details and remote workspace support |
| `write` | `write_file`: bounded UTF-8 write; unified diff approval, revision check, serialized atomic replacement preserving existing permission bits | Richer metadata and remote workspace support |
| `bash` / `powershell` | `run_command`: no-shell process, live bounded updates, timeout, UI cancellation, process-tree kill, full-output artifact and paged retrieval | Shell syntax, PowerShell, remote execution and richer command metadata |
| Extensible tool definitions | Fixed built-in tool schemas; injectable operations for read/list/write/edit | Custom tools and full remote operations |

Additional read-only `git_status`, `git_diff` and `read_command_output` tools are provided so inspection does not require general command permission. The Java desktop asks for each write, edit and command and can stop an active turn; CLI flags remain explicit broad-access overrides for noninteractive use. The experimental Laya judge remains in shadow mode.

File changes emit structured `tool.change` records (path, operation, byte counts, hashes, diff and edit count) into the session journal and a readable change event to Java. Approval compares the source revision again before execution, and the mutation queue checks it while holding the path lock. The operations interface covers read/list/write/edit; search, Git and commands still use the local workspace directly. A virtual backend verifies read/list/edit without a local target file; SSH transport is not implemented yet.

The new read/search behavior was checked in Python tests and in a temporary Git project with DeepSeek Flash: it located `src/Main.java:1` for a `TODO` followed by a number, excluded a Git-ignored Java file, and read lines 2050–2052 from a 2500-line text file by offset. This is a small workflow smoke test, not a complete compatibility suite.
