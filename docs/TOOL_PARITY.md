# Tool parity with mu

This project follows the tool workflow in [mu's coding-agent tool definitions](https://github.com/qybaihe/mu/tree/main/packages/coding-agent/src/core/tools). It is an independent Python implementation, not a source-level port. This table records behavior, not a count of tool names.

| mu capability | mu-pyjava today | Remaining gap |
| --- | --- | --- |
| `read`: text paging with line/byte limits | `read_file`: 1-based `offset`, `limit`, 50 KiB response limit | Images and model-aware image resizing |
| `find`: glob search with ignore rules | `find_files`: ripgrep-backed glob discovery, Git ignore rules, result limit | Broader ignore semantics and search on remote workspaces |
| `grep`: regex/literal, case folding, context, limits | `grep_files`: those options via ripgrep, structured path/line results | More complete output metadata and search result rendering |
| `ls` | `list_files`: one directory, 200 entries | Pagination and metadata |
| `edit`: targeted replacement | `edit_file`: one exact match, workspace path and size checks | Diff previews, file mutation queue, broader patch forms |
| `write` | `write_file`: bounded UTF-8 write inside workspace | Atomic replacement and rich change preview |
| `bash` / `powershell` | `run_command`: no-shell process, bounded output, timeout and process-group kill on Unix | Shell syntax, streamed output, cancellation from UI, PowerShell |
| Extensible tool definitions | Fixed built-in tool schemas | Custom tools and remote operations |

Additional read-only `git_status` and `git_diff` tools are provided so basic inspection does not require general command permission. Write and command access still uses process-wide flags; per-action approval is the next safety and usability milestone. The experimental Laya judge remains in shadow mode.

The new read/search behavior was checked in Python tests and in a temporary Git project with DeepSeek Flash: it located `src/Main.java:1` for a `TODO` followed by a number, excluded a Git-ignored Java file, and read lines 2050–2052 from a 2500-line text file by offset. This is a small workflow smoke test, not a complete compatibility suite.
