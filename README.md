# mu-pyjava

A small Python + Java coding-agent foundation inspired by [mu](https://github.com/qybaihe/mu). Python owns the agent loop, tools, and decision points. Java provides a desktop window and launches the Python process. This is an independent prototype, not a feature-complete port of mu.

## Requirements

- Python 3.9 or newer
- JDK 17 or newer for the desktop window
- Git for the optional status and diff tools
- ripgrep (`rg`) for file discovery and content search
- A Chat Completions-compatible model endpoint for real model use

The Python and Java code uses their standard libraries; Git and ripgrep are optional external executables for their respective tools.

## Run the offline smoke test

From the repository root:

```sh
PYTHONPATH=python MU_MODEL_BACKEND=echo python3 -m mupyjava --prompt "hello"
mkdir -p java/out
javac -d java/out java/src/main/java/dev/mupyjava/*.java
MU_MODEL_BACKEND=echo java -cp java/out dev.mupyjava.Main --smoke "hello"
MU_MODEL_BACKEND=echo java -cp java/out dev.mupyjava.Main
```

The echo adapter repeats text. It verifies the CLI, Java window, and process protocol without making model calls.

## Connect a model

Set the model and endpoint in your shell, then run the same commands without `MU_MODEL_BACKEND=echo`:

```sh
export MU_MODEL="your-chat-completions-model"
export MU_API_KEY="your-key"
export MU_API_BASE="https://api.openai.com/v1"
PYTHONPATH=python python3 -m mupyjava --workspace . --prompt "Summarize this project"
```

The adapter uses the [Chat Completions function-call format](https://developers.openai.com/api/docs/guides/function-calling). Other endpoints may need a separate adapter. A model key is never stored in this repository.

The agent now has nine tools, following mu's basic read/find/grep/edit/write/command workflow and adding direct Git inspection:

| Read-only, always available | Desktop approval; CLI requires `--allow-write` | Desktop approval; CLI requires `--allow-command` |
| --- | --- | --- |
| `list_files`, `read_file`, `find_files`, `grep_files`, `git_status`, `git_diff` | `write_file`, `edit_file` | `run_command` |

`read_file` accepts `offset` and `limit` to page through large UTF-8 files, returning at most 50 KiB per call. `find_files` uses globs, and `grep_files` supports regex, literal matching, case-insensitive search and surrounding lines. Both use ripgrep and respect `.gitignore` in Git workspaces. Their JSON results include a `truncated` flag. `edit_file` replaces text only when the old text appears exactly once. File paths stay inside the selected workspace. Commands use an argument list without a shell, run inside the workspace, and have a configurable timeout of up to 120 seconds; only the first 12 KiB of output is retained.

The Java desktop now asks before each write, edit or command. A file action can be allowed once, allowed for the same file during this conversation, or denied; commands and protected files are approved one at a time. Denial returns a tool result to the model. Closing the backend denies pending requests. In noninteractive CLI mode, `--allow-write` and `--allow-command` still explicitly grant broad access for that process; passing those flags to the desktop also bypasses its approval dialog. The `--smoke` test path denies actions unless `--smoke-approval once` is supplied for an automated test.

The desktop now restores the latest conversation for the selected workspace on restart. **New session** starts an empty one; the **Judgments** tab shows saved verdicts, probabilities and fallback details. Sessions are local JSONL journals containing prompts, tool arguments and results. Set `MU_SESSION_DIR` to choose their storage directory. Interrupted turns are shown but excluded from restored model context, so tool actions are not replayed. See [session format and protocol](docs/SESSIONS.md).

## Decisions

`tool.intent` is the first decision point. Version 2 asks whether a proposed `write_file`, `edit_file`, or `run_command` action serves the latest user request. Read-only tools do not use this decision point. The default mode is `off`; `shadow` asks and records without changing behavior; `active` applies the answer. The desktop permission gate still applies after the judge decision. A separate judge model can be configured with `MU_JUDGE_MODEL`, `MU_JUDGE_MODE=shadow|active`, and an optional `--ledger path/to/ledger.jsonl` argument.

A fine-tuned Laya checkpoint can be loaded locally with `MU_JUDGE_LAYA_PATH=/path/to/checkpoint`, or reached through an SSH tunnel with `MU_JUDGE_LAYA_URL=http://127.0.0.1:18765`. The local option needs `laya==0.3.20` and its runtime dependencies in the Python environment. The experimental Laya backend supports `MU_JUDGE_MODE=shadow` only. Its training covered version 1's `write_file` and `run_command`; `edit_file` verdicts are exploratory until new data and evaluation cover them. The small evaluation found confident errors, so it cannot yet control tools. See [training results](training/results-lab.md) and [lab connection steps](training/README.md).

The intent question sends the latest request and tool metadata to the configured judge. For a write or edit, it sends the path and text sizes, not the file content. The ledger stores the verdict, probability, timing, and tool name, not the submitted state. Shadow verdicts appear as `judge` events in the CLI and desktop transcript.

## Tests

```sh
PYTHONPATH=python python3 -m unittest discover -s python/tests -v
mkdir -p java/out && javac -d java/out java/src/main/java/dev/mupyjava/*.java
MU_MODEL_BACKEND=echo java -cp java/out dev.mupyjava.Main --smoke "hello"
```

See [progress](docs/PROGRESS.md) for completed work and next milestones. The upstream mu repository uses an MIT license for its agent packages and Apache 2.0 for its desktop app; this repository contains newly written code and does not copy those files. A license for this repository has not been selected yet.

The [tool parity table](docs/TOOL_PARITY.md) tracks which mu behaviors have been reproduced and what remains.
