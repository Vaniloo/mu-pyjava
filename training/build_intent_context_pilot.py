"""Project frozen synthetic v2 rows into a context-input format pilot.

The follow-up variant is a constructed two-turn exchange. It does not create
human labels or real project traces, and its labels retain source provenance.
"""

import argparse
import copy
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

from judge_pipeline import save_new
from mupyjava.intent_context import (CONTEXT_POINT, CONTEXT_QUESTION,
                                     CONTEXT_VERSION, capture_context,
                                     context_state, serialized_state_digest)
from mupyjava.judge import LayaBooleanJudge
from mupyjava.judge_data import (read_jsonl, training_partitions,
                                validate_manifest)
from mupyjava.judge_samples import digest
from mupyjava.task_frame import TaskFrame


def project(row, variant):
    if variant not in {"direct", "followup"}:
        raise ValueError("Unknown context pilot variant")
    old = row["state"]
    prompt = old["user_request"]
    if (not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 1000
            or "label" not in row or
            (type(row["label"]) is not bool and row["label"] is not None)
            or not isinstance(old.get("tool"), str)
            or not isinstance(old.get("arguments"), dict)):
        raise ValueError("Pilot requires a complete labeled v2 request and action")
    frame = TaskFrame().advance(prompt)
    latest = prompt
    if variant == "followup":
        language = row.get("tags", {}).get("language", row.get("language", ""))
        latest = "继续当前任务。" if language == "zh" else "Continue the current task."
        frame = frame.advance(latest)
    v2_state = {"user_request": latest[:1000], "tool": old["tool"],
                "arguments": copy.deepcopy(old["arguments"])}
    state = context_state(v2_state, capture_context(latest, frame))
    candidate = {"point": CONTEXT_POINT, "version": CONTEXT_VERSION,
                 "question": CONTEXT_QUESTION, "state": state}
    return {"schema_version": 3, "point": CONTEXT_POINT, "version": CONTEXT_VERSION,
            "sample_id": f"{row['sample_id']}:{variant}",
            "source_sample_id": row["sample_id"], "source_input_digest": row.get("input_digest"),
            "input_digest": digest(candidate), "group_id": row["group_id"],
            "split": row.get("split", "diagnostic"), "origin": row.get("origin", "synthetic"),
            "tags": {**row.get("tags", {}), "context_variant": variant},
            "question": CONTEXT_QUESTION, "criteria": LayaBooleanJudge.CRITERIA,
            "state": state, "serialized_state_sha256": serialized_state_digest(state),
            "label": row["label"]}


def build(source, output):
    source_rows = read_jsonl(source)
    if not source_rows:
        raise ValueError("Empty source data")
    if all(row.get("schema_version") == 2 and
           row.get("split") in {"train", "validation", "calibration", "test"}
           for row in source_rows):
        frozen_manifest = source.with_name("manifest.json")
        if not frozen_manifest.exists():
            raise ValueError("Versioned source requires its frozen manifest")
        training_partitions(source_rows, allow_synthetic_eval=True, train_unknown=True)
        validate_manifest(source_rows, json.loads(frozen_manifest.read_text()),
                          allow_synthetic_eval=True, train_unknown=True)
    rows = [project(row, variant) for row in source_rows
            for variant in ("direct", "followup")]
    ids = [row["sample_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Repeated source sample ID")
    groups = {}
    for row in rows:
        group, split = row["group_id"], row["split"]
        if group in groups and groups[group] != split:
            raise ValueError("Task group leaked across splits")
        groups[group] = split
    counts = Counter(row["split"] for row in rows)
    manifest = {"schema_version": 1, "purpose": "synthetic_context_format_pilot",
                "source_path": str(source),
                "source_file_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "dataset_digest": digest(rows), "groups": groups,
                "counts": dict(sorted(counts.items())),
                "labels": {"true": sum(row["label"] is True for row in rows),
                           "false": sum(row["label"] is False for row in rows),
                           "unknown": sum(row["label"] is None for row in rows)},
                "independent_human_gold": 0, "real_user_project_traces": 0,
                "training_allowed_only_as_synthetic_experiment": True,
                "limitations": "Labels and action proposals are inherited from frozen constructed v2 rows. The follow-up is an authored continuation with no intervening events. This probes format adaptation, not real-user reliability."}
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    data = output / "cases.jsonl"
    # Keep the action first: Laya's serializer respects dict insertion order.
    descriptor = os.open(str(data), os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    manifest["dataset_file_sha256"] = hashlib.sha256(data.read_bytes()).hexdigest()
    save_new(output / "manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.source, args.output)
    print(json.dumps({key: result[key] for key in ("purpose", "counts", "labels",
                                               "independent_human_gold")}))


if __name__ == "__main__":
    main()
