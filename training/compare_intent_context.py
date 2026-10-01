"""Verify matching context-pilot predictions and publish aggregate counts only."""

import argparse
import json
from collections import Counter
from pathlib import Path

from evaluate_intent import metrics
from evaluate_intent_context import validated_rows
from judge_pipeline import save_new
from mupyjava.judge import _laya_judgment


def compare(data, token_audit, split, reports):
    rows, data_hash, audit_hash = validated_rows(data, token_audit, split)
    models = {}
    for name, report in reports.items():
        predictions = report.get("predictions", [])
        if (report.get("purpose") != "synthetic_context_format_diagnostic" or
                report.get("dataset_sha256") != data_hash or
                report.get("token_audit_sha256") != audit_hash or
                report.get("split") != split or len(predictions) != len(rows)):
            raise ValueError("Report is not bound to the same frozen candidate inputs")
        for row, predicted in zip(rows, predictions):
            if (predicted.get("sample_id") != row["sample_id"] or
                    predicted.get("input_digest") != row["input_digest"] or
                    predicted.get("label") is not row["label"] or
                    _laya_judgment(predicted["probability"]).answer is not predicted.get("answer")):
                raise ValueError("Prediction changed its input, label or threshold answer")
        calculated = metrics(rows, [item["probability"] for item in predictions])
        if calculated != report.get("overall"):
            raise ValueError("Report metrics disagree with probabilities")
        by_variant = {}
        for variant in ("direct", "followup"):
            pairs = [(row, prediction) for row, prediction in zip(rows, predictions)
                     if row["tags"]["context_variant"] == variant]
            if pairs:
                by_variant[variant] = metrics([row for row, _ in pairs],
                                              [prediction["probability"] for _, prediction in pairs])
        if by_variant != report.get("by_variant"):
            raise ValueError("Report variant metrics disagree with probabilities")
        false_allow_families = Counter(row["group_id"] for row, prediction in zip(rows, predictions)
                                       if row["label"] is False and prediction["answer"] is True)
        models[name] = {"checkpoint_sha256": report["checkpoint_sha256"],
                        "noul_temperature": report["noul_temperature"],
                        "overall": calculated, "by_variant": by_variant,
                        "correct_decisive": sum(row["label"] is not None and
                            prediction["answer"] is row["label"] for row, prediction in zip(rows, predictions)),
                        "known_abstention": sum(row["label"] is not None and
                            prediction["answer"] is None for row, prediction in zip(rows, predictions)),
                        "unknown_abstention": sum(row["label"] is None and
                            prediction["answer"] is None for row, prediction in zip(rows, predictions)),
                        "false_allow_families": dict(sorted(false_allow_families.items()))}
    return {"schema_version": 1, "purpose": "synthetic_context_format_comparison",
            "dataset_sha256": data_hash, "token_audit_sha256": audit_hash,
            "split": split, "rows": len(rows), "models": models,
            "independent_human_gold": 0, "real_user_project_traces": 0,
            "limitations": "Projected constructed inputs and inherited synthetic labels; not an independent reliability estimate."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--token-audit", type=Path, required=True)
    parser.add_argument("--split", default="all")
    parser.add_argument("--report", action="append", required=True, help="NAME=report.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reports = {}
    for item in args.report:
        name, path = item.split("=", 1)
        if name in reports:
            raise ValueError("Repeated model name")
        reports[name] = json.loads(Path(path).read_text())
    result = compare(args.data, args.token_audit, args.split, reports)
    save_new(args.output, result)
    print(json.dumps({name: {"correct_decisive": model["correct_decisive"],
                             "false_allow": model["overall"]["false_allow"],
                             "false_decline": model["overall"]["false_decline"],
                             "known_abstention": model["known_abstention"]}
                      for name, model in result["models"].items()}))


if __name__ == "__main__":
    main()
