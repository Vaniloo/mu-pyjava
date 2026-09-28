# Edit matching and change metadata

Implemented on 2026-09-28 against mu's [edit contract](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/tools/edit.ts) and [matching/diff utilities](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/tools/edit-diff.ts). This is an independent behavior implementation, with explicit differences below.

## Exact edits and opt-in normalization

`edit_file` accepts either `old_text`/`new_text` or a nonempty `edits` array; both forms can be combined. Every replacement matches the same original file. Matches must be unique and disjoint, including overlapping occurrences of one needle: `aa` in `aaa` is ambiguous and rejected. Edits that do not change content are rejected. Existing BOM and uniform LF/CRLF are preserved. Mixed line endings retain the earlier behavior of normalizing to the first LF/CRLF style; byte preservation for arbitrary mixed endings is not claimed.

Exact matching remains the default. Set a Boolean `allow_fuzzy=true` to permit a deterministic normalization fallback when exact text is absent:

```json
{
  "path": "src/Main.java",
  "allow_fuzzy": true,
  "edits": [{"old_text": "String name = \"old\";", "new_text": "String name = \"new\";"}]
}
```

The fallback uses NFKC, removes trailing whitespace per line, converts smart quotes and Unicode dashes, and converts special spaces. It does not use edit distance, guess spelling, change case, or ignore indentation. Empty normalized needles, missing/duplicate matches, changed line counts and overlapping replacements fail before commit.

If any replacement needs fallback, every replacement is matched in the normalized original view, with ambiguity checks in that view. Only touched line groups are rewritten from that view; untouched line contents keep their Unicode and trailing whitespace. Multiple edits on one line share a group. Replacement text itself keeps the supplied characters, with newline normalization as before.

**Normalization can change other characters on touched lines.** Approval shows the full actual diff, a normalization notice and affected original line ranges. Actual fuzzy edits require fresh desktop `once` approval even if the file already has a session grant; `session` is not offered. An `allow_fuzzy=true` call that succeeds exactly keeps normal exact-edit permissions. CLI `--allow-write` remains its existing broad write override. `tool.intent` receives the requested flag alongside existing size/path metadata; the trained Laya is still shadow-only and was not trained on this matching mode.

The exact prepared result is checked after approval and again under the file mutation lock before atomic replacement. Even a change from curly to ASCII quotes invalidates the source revision, despite an equivalent normalized view. Cancellation and denied approvals do not write. These checks do not extend to arbitrary custom-tool side effects.

## Result fields

Both `write_file` and `edit_file` emit the following additional fields in `tool.change` and `ToolResult.details.change`:

| Field | Meaning |
| --- | --- |
| `patch` | Standard unified patch of actual before/after text, with four context lines, quoted paths and missing-final-newline markers |
| `display_diff` | Display diff with old line numbers on removed/context lines and new line numbers on added lines |
| `first_changed_line` | 1-based navigation anchor in the new file; deletion at EOF anchors to an available editor line; `null` for identical text |
| `hunks` | Old/new start lines and line counts, using unified patch coordinates (zero start is possible for an empty side) |
| `used_fuzzy_match` | Whether fallback was actually used |
| `fuzzy_edit_indices` | Zero-based indices of replacements needing fallback |
| `normalized_line_ranges` | Inclusive 1-based original line groups rewritten from the normalized view |

The existing human approval `diff`, hashes, sizes, path and edit count remain. The patch has no human annotations mixed into it. It includes actual BOM/CRLF bytes rather than only the normalized matching view, so its content can be replayed independently. ASCII/Unicode/control characters in filenames use Git-compatible quoting. Creating an empty file has no textual patch/hunk; creation is represented by `existed=false` and the operation/path metadata. Patches describe text content, not file permission changes or binary edits.

The legacy `WorkspaceTools.execute(...)` returns its existing short success text. The agent additionally sends the navigation anchor and normalization summary to the model. Java displays and saves `path:line` and matching mode in readable result summaries; completed-session history restores them without rerunning the edit. There is no Java source editor or clickable navigation yet. Field names follow this project's snake_case/nested `change` contract, rather than copying mu's top-level `firstChangedLine` shape.

## Verification and gaps

The suite contains 74 tests: 73 passed and one native PowerShell test skipped on this Mac. Exported patches are checked and applied with Git, then compared byte-for-byte for normal edits, creation, deletion to empty content, final-newline changes, BOM/CRLF and quoted filenames. Tests cover multiple hunks/line shifts, EOF anchors, exact-default rejection, normalization, same-line and mixed exact/fuzzy batches, untouched duplicate line preservation, ambiguous/overlapping matches, existing-grant rejection, approval/execution revision checks and model-visible metadata. Java process tests cover denied/allowed fuzzy edits plus persisted patch/location/mode records.

Unlike mu's automatic fallback, this project defaults to exact matching and requires explicit opt-in plus fresh approval when fallback is used. Overlapping needle occurrences are rejected more strictly. Renderer layout and diff algorithm details are not identical, and Java has no source editor. Remote transport, native PowerShell validation, search/list parity, branching sessions and typed judge policies remain separate work.
