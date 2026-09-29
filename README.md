# mu-pyjava

A small Python + Java coding-agent foundation inspired by [mu](https://github.com/qybaihe/mu). Python owns the agent loop, tools, and decision points. Java provides a desktop window and launches the Python process. This is an independent prototype, not a feature-complete port of mu.

Judge sampling, annotation, offline replay and intent-v2 training inputs: [evaluation workflow](docs/JUDGE_EVALUATION.md).

## Requirements

- Python 3.9 or newer
- JDK 17 or newer for the desktop window
- Git for the optional status and diff tools
- ripgrep (`rg`) for file discovery and content search
- A Chat Completions-compatible model endpoint for real model use

Text tools and the Java code use their standard libraries; Git and ripgrep are optional external executables for their respective tools. Image decoding/conversion uses optional Pillow: `python3 -m pip install -r python/requirements-images.txt`.

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

The agent has thirteen built-in tool names (including two names for output retrieval) and an explicit custom-tool registry, following mu's read/find/grep/edit/write/shell workflow and adding direct Git inspection and full command-output retrieval:

| Read-only, always available | Desktop approval; CLI requires `--allow-write` | Desktop approval; CLI requires `--allow-command` |
| --- | --- | --- |
| `list_files`, `read_file`, `find_files`, `grep_files`, `git_status`, `git_diff`, `read_command_output`, `read_tool_output` | `write_file`, `edit_file` | `run_command`, `bash`, `powershell` |

`read_file` accepts `offset` and `limit` to page through large UTF-8 files, returning at most 50 KiB per call. `find_files` uses globs, and `grep_files` supports regex, literal matching, case-insensitive search and surrounding lines. Both use ripgrep and respect `.gitignore` in Git workspaces. Their JSON results include a `truncated` flag. `edit_file` supports multiple unique, disjoint replacements against original text, preserving BOM/CRLF and committing atomically after diff approval. Changes include a replayable patch, numbered diff and first changed line. Optional `allow_fuzzy=true` enables Unicode/trailing-space normalization; actual fallback requires a fresh desktop approval, including for a previously granted file. File tool paths stay inside the selected workspace. `run_command` executes an argument vector; `bash` and `powershell` accept complete shell scripts with pipelines and redirection. All start in the workspace and have a configurable timeout of up to 120 seconds. Output streams live to the desktop. The final result keeps the last 12 KB; longer output receives an ID that `read_command_output` can page through by byte offset, including after restart.

The Java desktop now asks before each write, edit or command. A file action can be allowed once, allowed for the same file during this conversation, or denied; commands and protected files are approved one at a time. Denial returns a tool result to the model. Closing the backend denies pending requests. In noninteractive CLI mode, `--allow-write` and `--allow-command` still explicitly grant broad access for that process; passing those flags to the desktop also bypasses its approval dialog. Explicitly enabling active `tool.risk` adds a fresh risk confirmation even with `--allow-command`; the noninteractive CLI denies commands requiring that confirmation. The `--smoke` test path denies actions unless `--smoke-approval once` is supplied for an automated test.

The desktop restores the selected conversation path for the workspace on restart. **New session** starts an empty one. **Saved conversations** lets you continue from the beginning or a completed turn, or copy that path into an independent conversation. Sibling histories are retained; workspace files keep their current contents and remembered tool permissions are cleared on each switch; the **Judgments** tab shows saved verdicts, probabilities and fallback details. Sessions are local JSONL journals containing prompts, tool arguments and results. Set `MU_SESSION_DIR` to choose their storage directory. Interrupted turns are shown but excluded from restored model context, so tool actions are not replayed. See [session format and protocol](docs/SESSIONS.md).

**Stop** cancels the active turn. It terminates a running command and its process group, and discards a model response that arrives after cancellation. The underlying model HTTP request may remain open until its timeout; the agent does not use its late response. Command starts, completions, timeouts, cancellations and full-output IDs are recorded in the session journal.

## Context budgets

Before every model call, the engine reserves answer space, includes tool definitions/images in its estimate, and fits the request by omitting older complete turns and shortening tool text with explicit full-text retrieval IDs. Canonical session history is preserved. **Context** shows saved estimates and omissions in Java. Defaults are `MU_CONTEXT_TOKENS=65536`, `MU_RESPONSE_TOKENS=8192`, `MU_TOOL_RESULT_TOKENS=8192` and `MU_IMAGE_TOKENS=4096`. These are estimates and manually configured limits, not a discovered provider tokenizer/window. Mandatory input that cannot fit blocks that request. See [context behavior, configuration and limits](docs/CONTEXT.md).

Optional `tool.admission` v3 classifies middle chunks of long outputs and can omit confident progress/repeated-warning/passing logs while preserving error/result/unknown chunks and endpoints, with exact byte-range archive pointers. It defaults off and cannot use the trained Laya. When older turns are omitted, the agent now keeps a bounded source-grounded history summary (`MU_SUMMARY_MODE=extractive` by default); optional model mode selects exact excerpts and rejects invented text. **Task** restores these summaries on restart/selected paths/forks. See [semantic context configuration, fallbacks and mu differences](docs/SEMANTIC_CONTEXT.md).

## Decisions

`tool.intent` is the first decision point. Version 2 asks whether a proposed file mutation, command or shell action serves the latest user request. Read-only tools do not use this decision point. The default mode is `off`; `shadow` asks and records without changing behavior; `active` applies the answer. The desktop permission gate still applies after the judge decision. A separate judge model can be configured with `MU_JUDGE_MODEL`, `MU_JUDGE_MODE=shadow|active`, and an optional `--ledger path/to/ledger.jsonl` argument.

A fine-tuned Laya checkpoint can be loaded locally with `MU_JUDGE_LAYA_PATH=/path/to/checkpoint`, or reached through an SSH tunnel with `MU_JUDGE_LAYA_URL=http://127.0.0.1:18765`. The local option needs `laya==0.3.20` and its runtime dependencies in the Python environment. The experimental Laya backend supports `MU_JUDGE_MODE=shadow` only. Retained v1 covers `write_file` and `run_command`; later experimental checkpoints add `edit_file`, `bash`, `powershell` and explicit uncertainty examples. Scope-r1 reduces false allows on the broad frozen synthetic test, but focused reply-only/prerequisite diagnostics still show confident errors and no false-allow improvement. These checkpoints cannot yet control tools. See the [latest boundary diagnostics and blind review intake](training/results-intent-boundary.md), [scope training and calibration tradeoffs](training/results-intent-scope.md), [controlled diagnosis and original-base comparison](training/results-intent-ablation.md), [initial training results](training/results-lab.md) and [lab connection steps](training/README.md).

The intent question sends the latest request and tool metadata to the configured judge. For a write or edit, it sends the path and text sizes, not the file content. The ledger stores decision metadata, not the submitted state. Shadow verdicts appear as `judge` events in the CLI and desktop transcript.

New judge experiments use the [semantic training design](training/TRAINING_DESIGN.md): family-balanced weighting, bounded rehearsal, epoch-zero selection and protected diagnostics. The [same-information language-model comparison](training/results-training-design.md) measures semantic decisions and missing-context abstention separately.

The [first two-arm semantic pilot](training/results-intent-semantic.md) completed, but a post-training tool-contract audit invalidated its quality claims. Neither candidate is promoted; preparation/training now reject shell syntax assigned to direct `run_command`.

The kernel now also supports typed choices/scores, a frozen decision registry, per-point modes and ordered backend routes with confidence thresholds, timeout/cancel and declared fallbacks. Set `MU_JUDGE_CONFIG` to a JSON configuration for named routes. Optional `tool.review` (choice veto) and `tool.risk_score` (shadow observation) hooks default to off; these are project probes and do not reproduce mu's multi-question constraint/risk specifications. Laya remains restricted to shadow `tool.intent` even inside a route. Java restores individual records with backend attempts and fallback details. See [configuration, contracts and remaining gaps](docs/JUDGING.md).

Actual `tool.constraint` and `tool.risk` hooks now use dynamic/per-command question sets and outcome aggregation. Branch-local task frames retain verbatim user constraints; explicit `Constraint:`, `约束：` and `/constraint` lines work without a judge, while optional `task.frame` classifies natural-language updates. The Java **Task** tab restores these records, and the derived task note survives older-turn context omission. New judgment hooks default off; Laya cannot serve them. See [task policies, confirmation rules and reproduction limits](docs/TASK_POLICIES.md).

## Tests

```sh
PYTHONPATH=python python3 -m unittest discover -s python/tests -v
mkdir -p java/out && javac -d java/out java/src/main/java/dev/mupyjava/*.java
MU_MODEL_BACKEND=echo java -cp java/out dev.mupyjava.Main --smoke "hello"
```

See [progress](docs/PROGRESS.md) for completed work and next milestones. The upstream mu repository uses an MIT license for its agent packages and Apache 2.0 for its desktop app; this repository contains newly written code and does not copy those files. A license for this repository has not been selected yet.

The [tool parity table](docs/TOOL_PARITY.md) tracks which mu behaviors have been reproduced and what remains.
The [operation contracts](docs/OPERATIONS.md) describe local and alternate workspace implementations.

`read_file` also recognizes PNG/JPEG/GIF/WebP/BMP images by their bytes. Configure `MU_MODEL_SUPPORTS_IMAGES=true` for a vision endpoint; the default text-only mode returns a clear omission notice. Optional Pillow handles orientation, BMP conversion, transparency and bounded resizing. Image attachments survive completed-session restoration. The desktop displays image metadata; an image viewer is not implemented.

Custom Python modules export `register_tools(registry)` and are loaded explicitly through `--tool-module` or `MU_TOOL_MODULES`. The agent advertises their schemas and records their results. Custom mutations default to fresh desktop approval; CLI requires `--allow-custom-tools`, independently of the built-in write/command flags. A read-only `mupyjava.extensions.file_digest` example is included.

See [edit matching and patch fields](docs/EDITING.md), [images and custom tool setup](docs/IMAGES_AND_EXTENSIONS.md) and [structured results and shell behavior](docs/TOOL_RESULTS.md) for contracts, platform requirements and validation coverage.
