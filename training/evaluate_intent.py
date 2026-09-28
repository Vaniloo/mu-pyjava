"""Compare actual serving predictions on frozen rows; never execute proposed tools."""

import argparse
import hashlib
import json
import math
import time
from collections import defaultdict
from pathlib import Path

from mupyjava.decision_points import TOOL_INTENT
from mupyjava.judge import LayaBooleanJudge, _laya_judgment
from mupyjava.judge_data import read_jsonl
from judge_pipeline import save_new


def metrics(rows, probabilities):
    if len(rows) != len(probabilities) or any(
            type(p) not in (float, int) or not math.isfinite(p) or not 0 <= p <= 1
            for p in probabilities):
        raise ValueError("Metrics require one finite probability per row")
    labeled = [(row["label"], probability) for row, probability in zip(rows, probabilities) if row["label"] is not None]
    accepted = [(truth, probability) for truth, probability in labeled if probability <= .2 or probability >= .8]
    negative = sum(truth is False for truth, _ in labeled)
    positive = sum(truth is True for truth, _ in labeled)
    false_allow = sum(truth is False and probability >= .8 for truth, probability in labeled)
    false_decline = sum(truth is True and probability <= .2 for truth, probability in labeled)
    unknown = [probability for row, probability in zip(rows, probabilities) if row["label"] is None]
    return {"n": len(rows), "labeled": len(labeled), "accepted": len(accepted),
        "negative_labels": negative, "positive_labels": positive,
        "coverage": len(accepted) / len(labeled) if labeled else None,
        "selective_accuracy": sum(truth == (p >= .8) for truth, p in accepted) / len(accepted) if accepted else None,
        "accuracy_at_0_5": sum(truth == (p >= .5) for truth, p in labeled) / len(labeled) if labeled else None,
        "false_allow": false_allow, "false_decline": false_decline,
        "false_allow_rate": false_allow / negative if negative else None,
        "false_decline_rate": false_decline / positive if positive else None,
        "brier": sum((p - int(truth)) ** 2 for truth, p in labeled) / len(labeled) if labeled else None,
        "unknown_cases": len(unknown), "unknown_non_abstention": sum(p <= .2 or p >= .8 for p in unknown)}


def coverage_inventory(rows):
    unknown = [row for row in rows if row["label"] is None]
    return {"families": len({row["group_id"] for row in rows if row.get("group_id")}),
            "family_metadata_missing": sum(not row.get("group_id") for row in rows),
            "distinct_requests": len({row["state"]["user_request"] for row in rows}),
            "unknown_families": len({row["group_id"] for row in unknown if row.get("group_id")}),
            "distinct_unknown_requests": len({row["state"]["user_request"] for row in unknown})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--split", default="test")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Evaluation output already exists")
    rows = [row for row in read_jsonl(args.data) if args.split == "all" or row.get("split") == args.split]
    if not rows:
        raise ValueError("No evaluation rows")
    if any(row.get("question", TOOL_INTENT.question) != TOOL_INTENT.question or
           row.get("label") is not None and type(row["label"]) is not bool for row in rows):
        raise ValueError("Evaluation expects current intent question and Boolean/unknown labels")
    judge = LayaBooleanJudge(str(args.checkpoint), device=args.device)
    started = time.perf_counter()
    probabilities = []
    for offset in range(0, len(rows), args.batch_size):
        batch = rows[offset:offset + args.batch_size]
        if hasattr(judge.agent, "predict_batch"):
            replies = judge.agent.predict_batch([row["state"] for row in batch], {"intent": {
                "type": "noul", "instructions": TOOL_INTENT.question, "criteria": judge.CRITERIA}},
                batch_size=args.batch_size)
            probabilities.extend(_laya_judgment(reply["answers"]["intent"]["noul"]).probability for reply in replies)
        else:
            probabilities.extend(judge.evaluate(TOOL_INTENT.question, row["state"]).probability for row in batch)
    if len(probabilities) != len(rows):
        raise ValueError("Evaluation prediction count mismatch")
    by_tool, by_language, by_scenario, by_family = (defaultdict(list) for _ in range(4))
    predictions = []
    for row, probability in zip(rows, probabilities):
        pair = (row, probability)
        by_tool[row["state"]["tool"]].append(pair)
        by_language[row.get("tags", {}).get("language", "unspecified")].append(pair)
        by_scenario[row.get("tags", {}).get("scenario", "unspecified")].append(pair)
        if row.get("group_id"):
            by_family[row["group_id"]].append(pair)
        predictions.append({"group_id": row.get("group_id"), "tags": row.get("tags", {}),
                            "sample_id": row.get("sample_id"), "state": row["state"],
                            "label": row["label"], "probability": probability,
                            "answer": _laya_judgment(probability).answer})

    def groups(values):
        return {key: metrics([pair[0] for pair in items], [pair[1] for pair in items]) for key, items in values.items()}

    report = {"checkpoint": str(args.checkpoint), "split": args.split,
              "dataset_file_sha256": hashlib.sha256(args.data.read_bytes()).hexdigest(),
              "checkpoint_sha256": hashlib.sha256((args.checkpoint / "model.safetensors").read_bytes()).hexdigest(),
              "evaluation_basis": sorted({row.get("origin", "legacy_regression") for row in rows}),
              "sequence_limit": judge.agent.cfg.get("max_len"), "batch_size": args.batch_size,
              "batch_amortized_ms": (time.perf_counter() - started) * 1000 / len(rows),
              "coverage_inventory": coverage_inventory(rows),
              "overall": metrics(rows, probabilities), "by_family": groups(by_family), "by_tool": groups(by_tool),
              "by_language": groups(by_language), "by_scenario": groups(by_scenario), "predictions": predictions}
    save_new(args.output, report)
    print(json.dumps({key: report[key] for key in ("checkpoint", "evaluation_basis", "overall")}), flush=True)


if __name__ == "__main__":
    main()
