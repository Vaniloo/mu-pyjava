"""Prepare private, prediction-blind context-intent review packets from v2 samples.

This does not label, train or run any proposed action. The new input remains a
separate candidate; the serving tool.intent point and existing samples stay v2.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path

from intent_review import group_provenance
from judge_pipeline import save_new
from mupyjava.decision_points import TOOL_INTENT
from mupyjava.intent_context import (CONTEXT_POINT, CONTEXT_QUESTION,
                                     CONTEXT_VERSION, context_state, state_digest,
                                     serialized_state_digest)
from mupyjava.judge_data import load_samples
from mupyjava.judge_samples import digest


def intent_samples(paths):
    selected, hashes, ids = [], [], set()
    for path in paths:
        hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
        for sample in load_samples(path):
            if sample["decision"]["point"] != TOOL_INTENT.id:
                continue
            if sample["sample_id"] in ids:
                raise ValueError("Repeated intent sample ID")
            ids.add(sample["sample_id"])
            selected.append(sample)
    if not selected:
        raise ValueError("No tool.intent samples")
    return sorted(selected, key=lambda item: item["sample_id"]), sorted(hashes)


def save_ordered_packet(path, rows):
    # Laya serializes state dicts in insertion order. The generic JSONL writer
    # sorts keys, which would move the proposed action behind long request text.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(path), flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        for row in rows:
            line = json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
            if len(line.encode("utf-8")) > 262_144:
                raise ValueError("Context review row exceeds 256 KiB")
            file.write(line)


def prepare(paths, output, groups=None):
    samples, hashes = intent_samples(paths)
    groups = group_provenance(samples, groups)
    packet = []
    labels = []
    for sample in samples:
        evidence = sample.get("source", {}).get("intent_context")
        state = context_state(sample["decision"]["state"], evidence)
        candidate_digest = digest({"point": CONTEXT_POINT, "version": CONTEXT_VERSION,
                                   "question": CONTEXT_QUESTION, "state": state})
        packet.append({"schema_version": 1, "point": CONTEXT_POINT,
                       "version": CONTEXT_VERSION, "question": CONTEXT_QUESTION,
                       "sample_id": sample["sample_id"], "group_id": sample["group_id"],
                       "input_digest": candidate_digest,
                       "source_v2_input_digest": sample["input_digest"],
                       "state_digest": state_digest(state),
                       "serialized_state_sha256": serialized_state_digest(state),
                       "frame_version": evidence["task_frame"]["version"],
                       "frame_turn": evidence["task_frame"]["turn"],
                       "state": state})
        labels.append({"sample_id": sample["sample_id"], "point": CONTEXT_POINT,
                       "input_digest": candidate_digest,
                       "answer": None, "reviewed": False, "origin": "manual",
                       "reviewer": "", "rationale": ""})
    manifest = {"schema_version": 1, "purpose": "context_input_holdout_review_only",
                "source_file_sha256": hashes, "packet_digest": digest(packet),
                "groups": groups, "groups_digest": digest(groups),
                "samples": len(packet), "prediction_blind": True,
                "labels_filled": False, "training_allowed": False,
                "inference_allowed_without_token_fit": False,
                "limitations": "Task goal is an excerpt and may be stale; latest user request takes precedence. Source group kind is attested, not independently verified. Full state must fit the chosen tokenizer before model inference."}
    for row in packet:
        if len((json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")) > 262_144:
            raise ValueError("Context review row exceeds 256 KiB")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    save_ordered_packet(output / "packet.jsonl", packet)
    manifest["packet_file_sha256"] = hashlib.sha256((output / "packet.jsonl").read_bytes()).hexdigest()
    save_new(output / "labels.jsonl", labels, jsonl=True)
    save_new(output / "manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, action="append", required=True)
    parser.add_argument("--collections", type=Path,
                        help="Group provenance JSON as in intent_review.py")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    groups = json.loads(args.collections.read_text()) if args.collections else None
    result = prepare(args.samples, args.output, groups)
    print(json.dumps({key: result[key] for key in (
        "purpose", "samples", "prediction_blind", "training_allowed",
        "inference_allowed_without_token_fit"
    )}))


if __name__ == "__main__":
    main()
