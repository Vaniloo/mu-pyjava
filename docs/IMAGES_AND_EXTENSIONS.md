# Images and tool extensions

Implemented on 2026-09-28 against mu's image/read and extension contracts. The twelve built-in tools remain available; trusted Python modules can add definitions for the same agent and Java desktop.

## Image reads

Install the optional image decoder in the Python environment used by the CLI/desktop:

```sh
python3 -m pip install -r python/requirements-images.txt
export MU_MODEL_SUPPORTS_IMAGES=true
```

Set that flag only for an endpoint/model that accepts image inputs. The default is `false`, including for unrecognized model names and the echo adapter. No provider-name guessing or automatic model discovery occurs. Existing text tools still use only the standard library.

`read_file` detects PNG, JPEG, GIF, WebP and BMP from their bytes, regardless of filename extension. Text keeps its existing paging behavior; `offset`/`limit` are rejected for images. Binary reads use the injected `FileOperations.read_bytes(path, limit, cancel)` with a 32-byte probe and a bounded source read. There is no local fallback when an alternate backend supplies binary data. Text-only legacy backends without this method still work for text.

The default `PillowImageOperations` verifies the decoded format, applies EXIF orientation, converts BMP to PNG, preserves transparency, and shrinks images to fit 2000×2000 and 4.5 MiB of base64 data. It never enlarges an image. Limits can be reduced through `ModelCapabilities`; source files are limited to 20 MiB and 25 million pixels before pixel decoding. Size pressure progressively reduces dimensions. `ImageOperations` is injectable for alternate processors.

Text-only models get a readable omission result without an image block. A missing Pillow installation or animated GIF/PNG/WebP gets an explicit omission reason. Corrupt data and source/attachment limits become tool errors. Animation is not reproduced; PNG/JPEG/static GIF/WebP/BMP have local coverage. Pixel decoding is bounded but cooperative cancellation is checked around decoder calls, not inside them.

Results contain a text description plus an `ImageContent(data, mime_type)` block and metadata: source/output MIME, original/final dimensions, processed/omitted flags and byte size. Java shows readable size/omission summaries, not an image viewer. The version 1 journal stores image blocks as base64 in results and model messages; it contains the actual image and can grow substantially. Restoration reuses the saved attachment without rereading or rerunning a tool. Changing to a text-only model preserves stored images and omits them only from outgoing requests.

For Chat Completions, `tool` messages only accept text. Our adapter keeps those replies and adds one attachment context message **after all consecutive tool replies**, identifying the originating call. Images use `image_url` data URLs in that message. The internal `image_blocks` field never goes over the provider wire. This follows the official [Chat message schema](https://developers.openai.com/api/reference/resources/chat) and [vision input guide](https://developers.openai.com/api/docs/guides/images-vision). Provider compatibility beyond that schema still needs live validation; the tests use a local HTTP endpoint.

## Register custom tools

Load modules explicitly; there is no workspace directory scanning:

```sh
PYTHONPATH=python python3 -m mupyjava --workspace . \
  --tool-module mupyjava.extensions.file_digest --prompt "Calculate README.md's SHA-256"
```

The shipped `file_digest` example is read-only, bounds source files to 8 MiB, validates workspace paths, observes cancellation and returns structured hash metadata. It uses the local filesystem. For the desktop, set `MU_TOOL_MODULES=mupyjava.extensions.file_digest` before launch. Multiple modules use a comma-separated list; CLI `--tool-module` can be repeated. Java retains the repository's Python path first and appends an explicitly configured `PYTHONPATH` for modules outside the repository. Installed modules work through the selected Python environment.

Every module exports `register_tools(registry)`:

```python
from mupyjava.registry import ToolDefinition
from mupyjava.tool_result import ToolResult

def count(arguments, context):
    context.check_cancelled()
    return ToolResult.from_text(str(arguments["value"] * 2), {"count": 1})

def register_tools(registry):
    registry.register(ToolDefinition(
        name="double_value",
        description="Double a positive integer.",
        parameters={"type": "object", "properties": {
            "value": {"type": "integer", "minimum": 1}},
            "required": ["value"], "additionalProperties": False},
        execute=count,
        effect="read",
    ))
```

`ToolDefinition` has a name, description, parameter schema, handler and effect. Effect defaults to `mutation`; only explicitly declared `read` tools skip mutation approval. Parameters use a supported JSON Schema subset: object/properties/required/boolean additionalProperties, homogeneous arrays/items, string/integer/number/boolean/null types, enum and numeric/length bounds. Description/title/default are annotations. References, composition, type unions, patterns and other unsupported keywords are rejected during registration. Arguments are validated before judge/approval and again before execution. Built-in/duplicate names are rejected. Registration takes a snapshot and freezes when an `Agent` is constructed, so an approval cannot target a replaced definition. Failed module registration is rolled back.

`ToolContext` provides the root, confined `resolve_path`, cancellation token/check, update/artifact callbacks, output directory and model capabilities. Callbacks must finish before the handler returns. A handler returns `ToolResult`; exceptions or invalid return values become structured failures. Cancellation raises `TurnCancelled`, drops a late return and records a terminal cancellation event. Handlers must check cancellation during ongoing work and stop their own child processes/I/O; the framework cannot terminate arbitrary Python code.

Custom mutations pass `tool.intent` and the desktop's approval gate. Java shows the registered description and exact JSON arguments. They require a fresh `once` approval; file session grants and `--allow-write`/`--allow-command` cannot authorize them. CLI requires `--allow-custom-tools` or a programmatic `allow_custom=True`. This explicit broad override is not passed by Java. Active judge decisions can veto; the experimental Laya checkpoint remains shadow-only and was not trained on these custom tools.

Loading a module executes trusted Python code at startup. Effect declarations and handlers must be trustworthy: registration/approval are invocation controls, not an operating-system sandbox. Custom side effects do not automatically gain built-in diff/revision/atomic-write guarantees; handlers must implement their own semantics.

## Verification and remaining gaps

Tests cover byte-based format detection, BMP conversion, orientation, resizing, transparency and encoded size limits, malformed/animated inputs, omissions, alternate binary/processor backends, batched HTTP message ordering and image restoration after switching models. Custom tests cover validation, duplicate names, fixed definitions, module loading, permission floors, judge veto, fresh approvals, error normalization, streamed updates and cancellation. The Java process test denies/allows a loaded custom mutation, records its result, and sends/restores an image attachment through the same provider adapter.

Full suite: 66 tests, 65 passed, one native PowerShell test skipped on this Mac. Vision tests need the optional decoder. No paid provider vision call, real SSH backend, native PowerShell platform run, hot registration, MCP discovery, image viewer or upstream custom rendering hooks have been validated/shipped. Next tool slice: patch/navigation metadata and cautious fuzzy-edit compatibility; next architecture slice: session branches and typed judge policies.
