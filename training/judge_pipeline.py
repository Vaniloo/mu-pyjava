"""Prepare annotations, replay decisions, and export frozen intent-v2 inputs."""

import argparse
import json
import os
from pathlib import Path

from mupyjava.judge_config import engine_from_environment
from mupyjava.judge_data import evaluate, export_intent, label_templates, load_labels, load_samples
from mupyjava.judge_samples import canonical


def save_new(path, value, jsonl=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation: never silently erase labels, split manifests or prior reports.
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        if jsonl:
            for row in value:
                file.write(canonical(row) + "\n")
        else:
            json.dump(value, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("annotate", "replay", "export"))
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fresh", action="store_true", help="Call configured judge routes; never run tools")
    parser.add_argument("--seed", default="intent-v2")
    parser.add_argument("--point", action="append", help="Limit replay/export to a parent point (repeatable)")
    args = parser.parse_args()
    if args.fresh and args.action != "replay":
        parser.error("--fresh is only valid with replay")
    samples = load_samples(args.samples)
    if args.action == "annotate":
        save_new(args.output, label_templates(samples), jsonl=True)
        print(json.dumps({"pending_annotations": len(samples)}))
        return
    if args.labels is None:
        parser.error("--labels is required for replay/export")
    labels = load_labels(args.labels, samples)
    if args.point:
        samples = [sample for sample in samples if sample["decision"]["point"] in args.point]
        if not samples:
            parser.error("No samples match --point")
    if args.action == "replay":
        env = dict(os.environ)
        env.pop("MU_JUDGE_SAMPLES", None)  # Replay inference must never resample itself.
        engine = engine_from_environment(environ=env) if args.fresh else None
        report = evaluate(samples, labels, engine)
        save_new(args.output, report)
        print(json.dumps({key: report[key] for key in ("mode", "samples", "metrics")}, ensure_ascii=False))
    else:
        rows, manifest = export_intent(samples, labels, args.seed)
        args.output.mkdir(parents=True, exist_ok=False)
        save_new(args.output / "intent-v2.jsonl", rows, jsonl=True)
        save_new(args.output / "manifest.json", manifest)
        print(json.dumps({key: manifest[key] for key in ("counts", "excluded", "ready_for_training")}))


if __name__ == "__main__":
    main()
