"""Targeted text replacement and portable patch/navigation metadata."""

import difflib
import unicodedata
from bisect import bisect_right


_TRAILING = "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_TRANSLATION = str.maketrans({
    **dict.fromkeys("\u2018\u2019\u201a\u201b", "'"),
    **dict.fromkeys("\u201c\u201d\u201e\u201f", '"'),
    **dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-"),
    **dict.fromkeys("\u00a0\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000", " "),
})


def normalize_fuzzy(text):
    text = unicodedata.normalize("NFKC", text)
    return "\n".join(line.rstrip(_TRAILING) for line in text.split("\n")).translate(_TRANSLATION)


def lf_lines(text):
    """Split only on LF, preserving endings and a missing terminal newline."""
    if not text:
        return []
    parts = text.split("\n")
    return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _unique_index(content, needle, label):
    if not needle:
        raise ValueError(label + " must not be empty after normalization")
    start = content.find(needle)
    # Also detect overlapping occurrences, e.g. 'aa' in 'aaa'.
    if start < 0 or content.find(needle, start + 1) >= 0:
        raise ValueError(label + " must occur exactly once")
    return start


def apply_edits(content, edits, allow_fuzzy=False):
    """Apply against original LF text; fuzzy normalization touches whole lines."""
    if not isinstance(allow_fuzzy, bool):
        raise ValueError("allow_fuzzy must be a boolean")
    normalized_edits = []
    fuzzy_indices = []
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise ValueError(f"edits[{index}] must be an object")
        old, new = edit.get("old_text"), edit.get("new_text")
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise ValueError("old_text must be nonempty and new_text must be text")
        old = old.replace("\r\n", "\n").replace("\r", "\n")
        new = new.replace("\r\n", "\n").replace("\r", "\n")
        label = "old_text" if len(edits) == 1 else f"edits[{index}].old_text"
        if content.find(old) >= 0:
            _unique_index(content, old, label)
        elif allow_fuzzy:
            fuzzy_indices.append(index)
        else:
            raise ValueError(label + " must occur exactly once")
        normalized_edits.append((old, new, label))

    base = normalize_fuzzy(content) if fuzzy_indices else content
    matches = []
    for index, (old, new, label) in enumerate(normalized_edits):
        needle = normalize_fuzzy(old) if fuzzy_indices else old
        start = _unique_index(base, needle, label)
        matches.append((start, len(needle), new, index))
    matches.sort()
    for left, right in zip(matches, matches[1:]):
        if left[0] + left[1] > right[0]:
            raise ValueError(f"edits[{left[3]}] and edits[{right[3]}] overlap")

    def replace(text, replacements, offset=0):
        for start, length, new, _ in reversed(replacements):
            local = start - offset
            text = text[:local] + new + text[local + length:]
        return text

    ranges = []
    if fuzzy_indices:
        original_lines, base_lines = lf_lines(content), lf_lines(base)
        if len(original_lines) != len(base_lines):
            raise ValueError("Fuzzy normalization changed the line count")
        starts = []
        total = 0
        for line in base_lines:
            starts.append(total)
            total += len(line)
        groups = []
        for match in matches:
            start, length, _, _ = match
            first = bisect_right(starts, start) - 1
            last = bisect_right(starts, start + length - 1)
            if groups and first < groups[-1][1]:
                groups[-1][1] = max(groups[-1][1], last)
                groups[-1][2].append(match)
            else:
                groups.append([first, last, [match]])
        pieces, cursor = [], 0
        for first, last, group in groups:
            pieces.append("".join(original_lines[cursor:first]))
            pieces.append(replace("".join(base_lines[first:last]), group, starts[first]))
            ranges.append({"start_line": first + 1, "end_line": last})
            cursor = last
        pieces.append("".join(original_lines[cursor:]))
        updated = "".join(pieces)
    else:
        updated = replace(content, matches)
    if updated == content:
        raise ValueError("Edits did not change the file")
    return updated, {"used_fuzzy_match": bool(fuzzy_indices), "fuzzy_edit_indices": fuzzy_indices,
                     "normalized_line_ranges": ranges}


def _quote_path(path):
    data = path.encode("utf-8")
    if all(33 <= byte <= 126 and byte not in {34, 92} for byte in data):
        return path
    escapes = {9: "\\t", 10: "\\n", 13: "\\r", 34: '\\"', 92: "\\\\"}
    return '"' + "".join(escapes.get(byte, chr(byte) if 32 <= byte <= 126 else f"\\{byte:03o}")
                         for byte in data) + '"'


def _range(start, end):
    count = end - start
    line = start + 1 if count else start
    return str(line) if count == 1 else f"{line},{count}"


def change_metadata(path, before, after, existed=True):
    old, new = lf_lines(before), lf_lines(after)
    matcher = difflib.SequenceMatcher(None, old, new)
    groups = list(matcher.get_grouped_opcodes(4))
    patch, display, hunks = [], [], []
    first_changed = None
    width = len(str(max(len(old), len(new), 1)))
    if groups:
        patch.extend(["--- " + (_quote_path("a/" + path) if existed else "/dev/null") + "\n",
                      "+++ " + _quote_path("b/" + path) + "\n"])
    for group_index, group in enumerate(groups):
        _, i1, _, j1, _ = group[0]
        _, _, i2, _, j2 = group[-1]
        old_count, new_count = i2 - i1, j2 - j1
        hunks.append({"old_start": i1 + 1 if old_count else i1, "old_lines": old_count,
                      "new_start": j1 + 1 if new_count else j1, "new_lines": new_count})
        patch.append(f"@@ -{_range(i1, i2)} +{_range(j1, j2)} @@\n")
        if group_index:
            display.append(" ...")
        for tag, a1, a2, b1, b2 in group:
            if tag != "equal" and first_changed is None:
                first_changed = min(b1 + 1, max(1, len(after.split("\n"))))
            sections = []
            if tag == "equal":
                sections.append((" ", a1, old[a1:a2]))
            if tag in {"delete", "replace"}:
                sections.append(("-", a1, old[a1:a2]))
            if tag in {"insert", "replace"}:
                sections.append(("+", b1, new[b1:b2]))
            for prefix, start, lines in sections:
                for number, line in enumerate(lines, start + 1):
                    patch.append(prefix + line)
                    display.append(f"{prefix}{number:>{width}} " + line.rstrip("\r\n"))
                    if not line.endswith("\n"):
                        patch.extend(["\n", "\\ No newline at end of file\n"])
                        display.append("\\ No newline at end of file")
    return {"patch": "".join(patch), "display_diff": "\n".join(display),
            "first_changed_line": first_changed, "hunks": hunks}
