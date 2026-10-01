"""Measure whether serving's 512-token Laya sequence contains a proposed action.

Uses the shipped checkpoint tokenizer and Laya's exact sequence builder. This
never loads model weights or executes tool actions.
"""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from transformers import AutoTokenizer

from laya.common import build_sequence, serialize_state
from mupyjava.decision_points import TOOL_INTENT
from mupyjava.judge import LayaBooleanJudge
from mupyjava.judge_data import read_jsonl


def audit(checkpoint, data):
    config = json.loads((checkpoint / "rl_agent_config.json").read_text())
    tokenizer = AutoTokenizer.from_pretrained(str(checkpoint / "tokenizer"))
    max_len = config.get("max_len", 512)
    head_max_len = config.get("head_max_len", 256)
    rooms = {}
    status = {}
    counts = Counter()
    for row in read_jsonl(data):
        sample_id = row["sample_id"]
        if sample_id in status:
            raise ValueError("Repeated sample ID")
        instructions = row.get("question", TOOL_INTENT.question)
        if not isinstance(instructions, str) or not instructions:
            raise ValueError("Intent question must be nonempty text")
        if instructions not in rooms:
            question = {"t": "noul", "ins": instructions,
                        "crit": LayaBooleanJudge.CRITERIA}
            empty_ids, _ = build_sequence(tokenizer, "", question, max_len,
                                          head_max_len, state_ids=[])
            rooms[instructions] = max(0, max_len - len(empty_ids))
        room = rooms[instructions]
        serialized = serialize_state(row["state"])
        expected_serialized_hash = row.get("serialized_state_sha256")
        if (expected_serialized_hash is not None and
                hashlib.sha256(serialized.encode("utf-8")).hexdigest() != expected_serialized_hash):
            raise ValueError("Ordered packet state differs from Laya serialization")
        state_ids = tokenizer(serialized.replace(tokenizer.mask_token, " "),
                              add_special_tokens=False)["input_ids"]
        fit = len(state_ids) <= room
        entry = {"state_tokens": len(state_ids), "room": room, "fit": fit}
        if row["state"].get("tool") == "bash":
            command_marker = '"command":'
            marker_at = serialized.find(command_marker)
            if marker_at < 0:
                raise ValueError("Bash projection lacks a command field")
            prefix_ids = tokenizer(serialized[:marker_at],
                                   add_special_tokens=False)["input_ids"]
            entry["command_visible"] = len(prefix_ids) < room
        status[sample_id] = entry
        counts["full" if fit else "clipped"] += 1
        if entry.get("command_visible") is False:
            counts["command_absent"] += 1
    result = {"dataset_sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
            "max_len": max_len, "head_max_len": head_max_len,
            "counts": dict(sorted(counts.items())),
            "status": status}
    if len(rooms) == 1:
        result["state_token_room"] = next(iter(rooms.values()))
    else:
        result["state_token_room_by_question"] = rooms
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Token audit already exists")
    result = audit(args.checkpoint, args.data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True) + "\n")
    print(json.dumps({k: result[k] for k in (
        "dataset_sha256", "max_len", "head_max_len", "state_token_room",
        "state_token_room_by_question", "counts") if k in result}))


if __name__ == "__main__":
    main()
