# Tool parity with mu

This project follows the tool workflow in [mu's coding-agent tool definitions](https://github.com/qybaihe/mu/tree/main/packages/coding-agent/src/core/tools). It is an independent Python implementation, not a source-level port. This table records behavior, not a count of tool names.

The current behavior and architecture comparison is in [MU_COMPARISON.md](MU_COMPARISON.md).

| mu capability | mu-pyjava today | Remaining gap |
| --- | --- | --- |
| `read`: text paging with line/byte limits | `read_file`: 1-based text paging, 50 KiB response limit; typed image attachments, explicit model capabilities, orientation, resizing and BMP conversion | Animated images, automatic capability discovery, vision-provider live validation and desktop image viewer |
| `find`: glob search with ignore rules | `find_files`: ripgrep-backed glob discovery, Git ignore rules, result limit | Broader ignore semantics and search on remote workspaces |
| `grep`: regex/literal, case folding, context, limits | `grep_files`: those options via ripgrep, structured path/line results | More complete output metadata and search result rendering |
| `ls` | `list_files`: one directory, 200 entries | Pagination and metadata |
| `edit`: targeted replacement | `edit_file`: multiple unique, non-overlapping replacements against original content; BOM/CRLF preservation, unified diff approval, revision check and atomic replacement | Fuzzy matching fallback, standard patch/navigation details and remote workspace support |
| `write` | `write_file`: bounded UTF-8 write; unified diff approval, revision check, serialized atomic replacement preserving existing permission bits | Richer metadata and remote workspace support |
| `bash` / `powershell` | Dedicated Bash/PowerShell tools and direct `run_command`; common approval, streaming, timeout/cancel, process-tree kill, output artifacts and structured exit metadata | Actual remote transport; native PowerShell validation on an installed platform |
| Extensible tool definitions | Twelve built-ins plus explicit Python-module registration, validated schemas, shared approval/judge/cancel/result handling; injectable operations | Hot registration, custom rendering hooks, MCP discovery and actual remote transport |

Additional read-only `git_status`, `git_diff` and `read_command_output` tools are provided so inspection does not require general command permission. The Java desktop asks for each write, edit and command and can stop an active turn; CLI flags remain explicit broad-access overrides for noninteractive use. The experimental Laya judge remains in shadow mode.

File changes emit structured `tool.change` records (path, operation, byte counts, hashes, diff and edit count) into the session journal and a readable change event to Java. Approval compares the source revision again before execution, and the mutation queue checks it while holding the path lock. Injectable operations cover read/list/write/edit, find/grep, Git and streamed command execution. A virtual backend verifies the full agent workflow without a local target file or subprocess, including permissions, cancellation, result-path containment and output retrieval. SSH transport is not implemented yet; see [operation contracts](OPERATIONS.md).

The new read/search behavior was checked in Python tests and in a temporary Git project with DeepSeek Flash: it located `src/Main.java:1` for a `TODO` followed by a number, excluded a Git-ignored Java file, and read lines 2050–2052 from a 2500-line text file by offset. This is a small workflow smoke test, not a complete compatibility suite.

All tools expose versioned content/details/error results; the agent journals them and Java displays readable metadata. See [TOOL_RESULTS.md](TOOL_RESULTS.md). Image content blocks and explicit custom tool registration are implemented; see [IMAGES_AND_EXTENSIONS.md](IMAGES_AND_EXTENSIONS.md) for supported behavior and limits.
