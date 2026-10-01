"""Evaluate a context-input pilot with actual Laya weights and exact token-fit evidence.

Only predictions are made. Proposed tool actions are never executed.
"""

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from evaluate_intent import metrics
from judge_pipeline import save_new
from mupyjava.intent_context import (CONTEXT_POINT, CONTEXT_QUESTION,
                                     CONTEXT_VERSION, serialized_state_digest)
from mupyjava.judge import LayaBooleanJudge, _laya_judgment
from mupyjava.judge_data import read_jsonl
from mupyjava.judge_samples import digest


def validated_rows(data, token_audit, split):
    rows = read_jsonl(data)
    dataset_hash = hashlib.sha256(data.read_bytes()).hexdigest()
    audit = json.loads(token_audit.read_text())
    if audit.get("dataset_sha256") != dataset_hash or len(audit.get("status", {})) != len(rows):
        raise ValueError("Token audit does not match candidate data")
    ids = set()
    for row in rows:
        sample_id = row["sample_id"]
        if sample_id in ids or sample_id not in audit["status"] or not audit["status"][sample_id]["fit"]:
            raise ValueError("Candidate has a duplicate or clipped input")
        ids.add(sample_id)
        if (row.get("schema_version") != 3 or row.get("point") != CONTEXT_POINT or
                row.get("version") != CONTEXT_VERSION or row.get("question") != CONTEXT_QUESTION or
                row.get("criteria") != LayaBooleanJudge.CRITERIA or
                "label" not in row or
                (type(row["label"]) is not bool and row["label"] is not None) or
                row.get("serialized_state_sha256") != serialized_state_digest(row["state"]) or
                row.get("input_digest") != digest({"point": CONTEXT_POINT,
                    "version": CONTEXT_VERSION, "question": CONTEXT_QUESTION, "state": row["state"]})):
            raise ValueError("Candidate row contract changed")
    selected = [row for row in rows if split == "all" or row.get("split") == split]
    if not selected:
        raise ValueError("No matching candidate rows")
    return selected, dataset_hash, hashlib.sha256(token_audit.read_bytes()).hexdigest()


def evaluate(checkpoint, data, token_audit, split, device, batch_size):
    if batch_size < 1:
        raise ValueError("Batch size must be positive")
    rows, dataset_hash, audit_hash = validated_rows(data, token_audit, split)
    judge = LayaBooleanJudge(str(checkpoint), device=device)
    audit = json.loads(token_audit.read_text())
    if (judge.agent.cfg.get("max_len") != audit.get("max_len") or
            judge.agent.cfg.get("head_max_len") != audit.get("head_max_len")):
        raise ValueError("Checkpoint sequence limits differ from token audit")
    probabilities = []
    question = {"intent": {"type": "noul", "instructions": CONTEXT_QUESTION,
                           "criteria": judge.CRITERIA}}
    for offset in range(0, len(rows), batch_size):
        batch = rows[offset:offset + batch_size]
        replies = judge.agent.predict_batch([row["state"] for row in batch], question,
                                            batch_size=batch_size)
        probabilities.extend(_laya_judgment(reply["answers"]["intent"]["noul"]).probability
                             for reply in replies)
    if len(probabilities) != len(rows):
        raise ValueError("Prediction count mismatch")
    by_variant, by_tool, by_split = (defaultdict(list) for _ in range(3))
    predictions = []
    for row, probability in zip(rows, probabilities):
        pair = (row, probability)
        by_variant[row["tags"]["context_variant"]].append(pair)
        by_tool[row["state"]["tool"]].append(pair)
        by_split[row["split"]].append(pair)
        predictions.append({"sample_id": row["sample_id"], "input_digest": row["input_digest"],
                            "label": row["label"], "probability": probability,
                            "answer": _laya_judgment(probability).answer})

    def grouped(groups):
        return {key: metrics([row for row, _ in pairs], [probability for _, probability in pairs])
                for key, pairs in sorted(groups.items())}

    from laya.common import QTYPES

    return {"schema_version": 1, "purpose": "synthetic_context_format_diagnostic",
            "checkpoint": str(checkpoint),
            "checkpoint_sha256": hashlib.sha256((checkpoint / "model.safetensors").read_bytes()).hexdigest(),
            "dataset_sha256": dataset_hash, "token_audit_sha256": audit_hash,
            "split": split, "thresholds": {"decline": .2, "allow": .8},
            "noul_temperature": judge.agent.temperature[QTYPES["noul"]],
            "overall": metrics(rows, probabilities), "by_variant": grouped(by_variant),
            "by_tool": grouped(by_tool), "by_split": grouped(by_split),
            "predictions": predictions,
            "independent_human_gold": 0, "real_user_project_traces": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--token-audit", type=Path, required=True)
    parser.add_argument("--split", default="all")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Evaluation output already exists")
    report = evaluate(args.checkpoint, args.data, args.token_audit,
                      args.split, args.device, args.batch_size)
    save_new(args.output, report)
    print(json.dumps({key: report[key] for key in ("checkpoint", "split", "overall", "by_variant")}))


if __name__ == "__main__":
    main()
