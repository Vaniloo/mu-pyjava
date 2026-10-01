"""Compare two serving reports on the external ScopeJudge bash projection."""

import argparse
import json
from collections import Counter
from pathlib import Path

from compare_intent import compare
from judge_pipeline import save_new
from mupyjava.judge_data import read_jsonl
from project_scopejudge import SOURCE_SHA256


def summarize(predictions):
    labeled = len(predictions)
    positive = sum(row["label"] is True for row in predictions)
    negative = labeled - positive
    false_allow = sum(row["label"] is False and row["answer"] is True
                      for row in predictions)
    false_decline = sum(row["label"] is True and row["answer"] is False
                        for row in predictions)
    return {"n": labeled, "in_scope": positive, "out_of_scope": negative,
            "correct_decisive": sum(row["answer"] is row["label"]
                                     for row in predictions),
            "false_allow": false_allow, "false_decline": false_decline,
            "abstain": sum(row["answer"] is None for row in predictions),
            "false_allow_rate": false_allow / negative if negative else None,
            "false_decline_rate": false_decline / positive if positive else None}


def analyze(data, projection_audit, token_audit, reports):
    validated = compare(data, "external_eval", reports)
    if (projection_audit["source_sha256"] != SOURCE_SHA256 or
            projection_audit["projection_sha256"] != validated["dataset_sha256"] or
            projection_audit["training_allowed"] is not False):
        raise ValueError("Projection does not match pinned external source")
    rows = read_jsonl(data)
    fit = token_audit["status"]
    if token_audit["dataset_sha256"] != validated["dataset_sha256"] or (
            set(fit) != {row["sample_id"] for row in rows}):
        raise ValueError("Token audit does not match projection")
    results = {}
    for name, report in reports.items():
        predictions = report["predictions"]
        if any(row["tags"].get("source") != "dreadnode/scopejudge" or
               row["tags"].get("projection") != "intent_plus_current_bash_only"
               for row in predictions):
            raise ValueError("Not a ScopeJudge projection")
        results[name] = {"checkpoint_sha256": report["checkpoint_sha256"],
                         "slices": {
            "all": summarize(predictions),
            "full_input": summarize([row for row in predictions
                                     if fit[row["sample_id"]]["fit"]]),
            "clipped_input": summarize([row for row in predictions
                                        if not fit[row["sample_id"]]["fit"]]),
            "unanimous_experts": summarize([row for row in predictions
                                           if row["tags"]["expert_unanimous"]]),
            "contested_experts": summarize([row for row in predictions
                                           if not row["tags"]["expert_unanimous"]]),
            "full_input_unanimous": summarize([row for row in predictions
                                                if fit[row["sample_id"]]["fit"] and
                                                row["tags"]["expert_unanimous"]]),
        }}
    names = list(reports)
    paired = {}
    if len(names) == 2:
        first, second = (reports[name]["predictions"] for name in names)
        by_label = {}
        for truth in (True, False):
            pairs = [(a, b) for a, b in zip(first, second) if a["label"] is truth]
            by_label[str(truth).lower()] = {
                "first_correct_second_wrong": sum(a["answer"] is truth and
                                                   b["answer"] is not truth for a, b in pairs),
                "first_wrong_second_correct": sum(a["answer"] is not truth and
                                                   b["answer"] is truth for a, b in pairs),
                "both_correct": sum(a["answer"] is truth and b["answer"] is truth
                                    for a, b in pairs),
            }
        paired = {"model_order": names, "by_label": by_label}
    family_counts = Counter(row["group_id"] for row in rows)
    return {"source": "dreadnode/scopejudge", "basis":
            "cross-domain intent-plus-current-bash proxy, not official benchmark score",
            "source_revision": projection_audit["source_revision"],
            "source_sha256": projection_audit["source_sha256"],
            "dataset_sha256": validated["dataset_sha256"],
            "token_fit_counts": token_audit["counts"],
            "task_families": len(family_counts),
            "distinct_requests": validated["coverage_inventory"]["distinct_requests"],
            "models": results, "paired": paired}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--projection-audit", type=Path, required=True)
    parser.add_argument("--token-audit", type=Path, required=True)
    parser.add_argument("--report", action="append", required=True, help="NAME=report.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reports = {}
    for entry in args.report:
        name, path = entry.split("=", 1)
        if name in reports:
            raise ValueError("Repeated report name")
        reports[name] = json.loads(Path(path).read_text())
    result = analyze(args.data, json.loads(args.projection_audit.read_text()),
                     json.loads(args.token_audit.read_text()), reports)
    save_new(args.output, result)
    print(json.dumps({name: model["slices"] for name, model in result["models"].items()}))


if __name__ == "__main__":
    main()
