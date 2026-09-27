# mu-pyjava

A small Python + Java coding-agent foundation inspired by [mu](https://github.com/qybaihe/mu). Python owns the agent loop, tools, and decision points. Java provides a desktop window and launches the Python process. This is an independent prototype, not a feature-complete port of mu.

## Requirements

- Python 3.9 or newer
- JDK 17 or newer for the desktop window
- Git for the optional status and diff tools
- A Chat Completions-compatible model endpoint for real model use

The prototype uses only the Python and Java standard libraries.

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

| Read-only, always available | Requires `--allow-write` | Requires `--allow-command` |
| --- | --- | --- |
| `list_files`, `read_file`, `find_files`, `grep_files`, `git_status`, `git_diff` | `write_file`, `edit_file` | `run_command` |

`find_files` uses globs; `grep_files` searches literal text and returns file names and line numbers. `edit_file` replaces text only when the old text appears exactly once. Search results and file edits are limited in size, and file paths stay inside the selected workspace. Commands use an argument list without a shell, run inside the workspace, and have a 30-second timeout. Write and command flags grant broad access during that run; per-action approval is a planned milestone.

## Decisions

`tool.intent` is the first decision point. Version 2 asks whether a proposed `write_file`, `edit_file`, or `run_command` action serves the latest user request. Read-only tools do not use this decision point. The default mode is `off`; `shadow` asks and records without changing behavior; `active` applies the answer. Explicit tool permission flags remain mandatory in every mode. A separate judge model can be configured with `MU_JUDGE_MODEL`, `MU_JUDGE_MODE=shadow|active`, and an optional `--ledger path/to/ledger.jsonl` argument.

A fine-tuned Laya checkpoint can instead be loaded with `MU_JUDGE_LAYA_PATH=/path/to/checkpoint` and optionally `MU_JUDGE_LAYA_DEVICE=cpu|cuda`. Install `laya==0.3.20` and its runtime dependencies in the Python environment first. The experimental Laya backend currently supports `MU_JUDGE_MODE=shadow` only. Its training covered version 1's `write_file` and `run_command`; `edit_file` verdicts are exploratory until new data and evaluation cover them. The small evaluation found confident errors, so it cannot yet control tools. See [training results](training/results-lab.md).

The intent question sends the latest request and tool metadata to the configured judge. For a write, it sends the path and content size, not the file content. The ledger stores verdict metadata, not the submitted state.

## Tests

```sh
PYTHONPATH=python python3 -m unittest discover -s python/tests -v
mkdir -p java/out && javac -d java/out java/src/main/java/dev/mupyjava/*.java
MU_MODEL_BACKEND=echo java -cp java/out dev.mupyjava.Main --smoke "hello"
```

See [progress](docs/PROGRESS.md) for completed work and next milestones. The upstream mu repository uses an MIT license for its agent packages and Apache 2.0 for its desktop app; this repository contains newly written code and does not copy those files. A license for this repository has not been selected yet.
