"""Validated labels, pure policy replay and group-isolated intent datasets."""

import copy
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .decision_points import ADMISSION_NOISE, builtin_registry
from .judge import (DecisionEngine, DecisionPolicy, LayaBooleanJudge, TypedJudgment,
                    _judgment_reason, _validate_answer)
from .judge_samples import canonical, digest


def read_jsonl(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError("Nonfinite JSON value")

    rows = []
    with Path(path).open(encoding="utf-8") as file:
        while True:
            line = file.readline(262_145)
            if not line:
                break
            if len(line.encode("utf-8")) > 262_144 or not line.endswith("\n"):
                raise ValueError("JSONL records must be bounded and newline terminated")
            if line.strip():
                rows.append(json.loads(line, object_pairs_hook=pairs, parse_constant=nonfinite))
    return rows


def resolve_sample(sample):
    if type(sample.get("schema_version")) is not int or sample["schema_version"] != 1:
        raise ValueError("Unsupported raw sample schema")
    for key in ("sample_id", "group_id"):
        if not isinstance(sample.get(key), str) or not sample[key].strip():
            raise ValueError("Missing sample identity/group")
    material = sample["decision"]
    if digest(material) != sample.get("input_digest"):
        raise ValueError("Sample input digest mismatch")
    spec = builtin_registry().resolve(material["point"])
    if material["version"] != spec.version:
        raise ValueError("Decision specification version changed")
    inputs = copy.deepcopy(material["inputs"])
    if spec.kind == "multi":
        questions = spec.questions_for(copy.deepcopy(inputs)) if spec.questions_for else spec.questions
        state = spec.build_state(copy.deepcopy(inputs)) if spec.build_state else inputs
    else:
        questions, state = (spec,), inputs
    if (canonical([asdict(question) for question in questions]) != canonical(material["questions"])
            or canonical(state) != canonical(material["state"])):
        raise ValueError("Captured questions/state do not match current specification")
    policy = sample["policy"]
    policy = DecisionPolicy(policy["mode"], tuple(policy["routes"]),
                            policy["min_confidence"], policy["timeout_seconds"])
    if policy.mode not in spec.allowed_modes:
        raise ValueError("Captured policy mode is invalid")
    return spec, questions, policy


def load_samples(path):
    samples = read_jsonl(path)
    identities = set()
    for sample in samples:
        resolve_sample(sample)
        if sample["sample_id"] in identities:
            raise ValueError("Duplicate sample ID")
        identities.add(sample["sample_id"])
    return samples


def label_templates(samples):
    return [{"schema_version": 1, "sample_id": sample["sample_id"],
             "input_digest": sample["input_digest"], "reviewed": False,
             "origin": "manual", "reviewer": "", "rationale": "",
             "answers": {question["id"]: None for question in sample["decision"]["questions"]}}
            for sample in samples]


def load_labels(path, samples):
    labels = {}
    cases = {sample["sample_id"]: sample for sample in samples}
    for row in read_jsonl(path):
        if set(row) != {"schema_version", "sample_id", "input_digest", "reviewed", "origin",
                        "reviewer", "rationale", "answers"}:
            raise ValueError("Invalid annotation fields")
        case_id = row["sample_id"]
        if case_id not in cases or case_id in labels:
            raise ValueError("Unknown/duplicate annotation sample ID")
        if type(row["schema_version"]) is not int or row["schema_version"] != 1:
            raise ValueError("Invalid annotation schema")
        if row["input_digest"] != cases[case_id]["input_digest"]:
            raise ValueError("Annotation belongs to different input")
        if (type(row["reviewed"]) is not bool or row["origin"] not in {"manual", "teacher", "synthetic"}
                or not isinstance(row["reviewer"], str) or not isinstance(row["rationale"], str)
                or row["reviewed"] and not row["reviewer"].strip()):
            raise ValueError("Invalid annotation provenance")
        _, questions, _ = resolve_sample(cases[case_id])
        if not isinstance(row["answers"], dict) or set(row["answers"]) != {q.id for q in questions}:
            raise ValueError("Annotations must include exactly the captured question IDs")
        for question in questions:
            _validate_answer(question, row["answers"][question.id])
        labels[case_id] = row
    return labels


def accepted_observations(sample):
    """Reapply the captured thresholds to available route replies. Never run a tool."""
    spec, questions, policy = resolve_sample(sample)
    observed = sample["observed"]
    if (observed["point"], observed["version"], observed["mode"]) != (spec.id, spec.version, policy.mode):
        raise ValueError("Observation does not belong to captured decision")
    accepted = {}
    if policy.mode == "off":
        return accepted
    for attempt in observed["attempts"]:
        details = attempt.get("questions", {}) if spec.kind == "multi" else {spec.id: attempt}
        for question in questions:
            if question.id in accepted:
                continue
            detail = details.get(question.id, {})
            if "answer" not in detail:
                continue
            judgment = TypedJudgment(detail["answer"], detail.get("confidence"), detail.get("probability"))
            if _judgment_reason(question, judgment, policy.min_confidence) == "accepted":
                accepted[question.id] = {**detail, "kind": question.kind, "backend": attempt["backend"]}
    return accepted


def replay_sample(sample, engine=None):
    """Recorded policy replay, or fresh inference using configured routes and captured inputs."""
    spec, questions, policy = resolve_sample(sample)
    if engine is not None:
        # Backend intrinsic limits still apply, including Laya shadow-only/intent-only.
        old = engine.policies.get(spec.id)
        engine.policies[spec.id] = DecisionPolicy("shadow", policy.routes, policy.min_confidence,
                                                policy.timeout_seconds)
        try:
            engine.decide(spec, copy.deepcopy(sample["decision"]["inputs"]))
            record = copy.deepcopy(engine.last_record)
        finally:
            if old is None:
                engine.policies.pop(spec.id, None)
            else:
                engine.policies[spec.id] = old
        captured = {**sample, "policy": {**sample["policy"], "mode": "shadow"}, "observed": record}
        accepted = accepted_observations(captured)
    else:
        record = sample["observed"]
        accepted = accepted_observations(sample)
    inputs = copy.deepcopy(sample["decision"]["inputs"])
    if spec.kind == "multi":
        judged = spec.aggregate(copy.deepcopy(accepted), inputs) if accepted else None
        fallback = spec.fallback(inputs)
    else:
        judged = accepted.get(spec.id, {}).get("answer")
        fallback = spec.fallback
    outcome = judged if policy.mode == "active" and judged is not None else fallback
    # Fresh inference remains shadow. Its hypothetical judged output is reported separately.
    if engine is None and canonical(outcome) != canonical(record["outcome"]):
        raise ValueError("Recorded outcome differs from current policy replay")
    return {"sample_id": sample["sample_id"], "answers": accepted, "judged": judged,
            "fallback": fallback, "observed_outcome": record["outcome"],
            "mode": record["mode"], "latency_ms": record["latency_ms"],
            "attempts": record["attempts"]}


def evaluate(samples, labels, engine=None):
    """Only reviewed, manually labeled questions enter independent metric denominators."""
    metrics, replays = {}, []
    for sample in samples:
        replay = replay_sample(sample, engine)
        replays.append(replay)
        annotation = labels.get(sample["sample_id"])
        if not annotation or not annotation["reviewed"] or annotation["origin"] != "manual":
            continue
        spec, questions, _ = resolve_sample(sample)
        for question in questions:
            truth = annotation["answers"][question.id]
            if truth is None:
                continue
            key = spec.id + "/" + question.kind + "/" + str(sample["decision"]["inputs"].get("tool", "-"))
            metric = metrics.setdefault(key, {"labeled": 0, "accepted": 0, "correct": 0,
                "abstained": 0, "false_allow": 0, "missed_violation": 0, "false_positive": 0,
                "false_negative": 0, "negative_labels": 0, "positive_labels": 0,
                "harmful_drop": 0, "important_chunks": 0, "absolute_error_sum": 0.0,
                "probability_count": 0, "brier_sum": 0.0, "kind": question.kind})
            metric["labeled"] += 1
            prediction = replay["answers"].get(question.id, {}).get("answer")
            if question.kind == "boolean":
                metric["positive_labels" if truth else "negative_labels"] += 1
                # Include finite probability replies even when the policy abstained.
                for attempt in reversed(replay["attempts"]):
                    detail = attempt.get("questions", {}).get(question.id, {}) if spec.kind == "multi" else attempt
                    probability = detail.get("probability")
                    if probability is not None:
                        metric["probability_count"] += 1
                        metric["brier_sum"] += (probability - int(truth)) ** 2
                        break
            if spec.id == "tool.admission" and truth in {"error", "result"}:
                metric["important_chunks"] += 1
            if prediction is None:
                metric["abstained"] += 1
                continue
            metric["accepted"] += 1
            metric["correct"] += int(prediction == truth)
            if question.kind == "boolean":
                metric["false_positive"] += int(prediction is True and truth is False)
                metric["false_negative"] += int(prediction is False and truth is True)
                if spec.id == "tool.intent":
                    metric["false_allow"] += int(prediction is True and truth is False)
                if spec.id == "tool.constraint":
                    metric["missed_violation"] += int(prediction is False and truth is True)
            elif question.kind == "score":
                metric["absolute_error_sum"] += abs(prediction - truth)
            elif spec.id == "tool.admission":
                metric["harmful_drop"] += int(truth in {"error", "result"} and prediction in ADMISSION_NOISE)
    for metric in metrics.values():
        metric["coverage"] = metric["accepted"] / metric["labeled"]
        metric["selective_accuracy"] = metric["correct"] / metric["accepted"] if metric["accepted"] and metric["kind"] != "score" else None
        metric["false_positive_rate"] = metric["false_positive"] / metric["negative_labels"] if metric["negative_labels"] else None
        metric["false_negative_rate"] = metric["false_negative"] / metric["positive_labels"] if metric["positive_labels"] else None
        metric["mean_absolute_error"] = metric["absolute_error_sum"] / metric["accepted"] if metric["accepted"] and metric["kind"] == "score" else None
        metric["brier"] = metric["brier_sum"] / metric["probability_count"] if metric["probability_count"] else None
        metric["harmful_drop_rate"] = metric["harmful_drop"] / metric["important_chunks"] if metric["important_chunks"] else None
    latencies = sorted(row["latency_ms"] for row in replays)
    return {"schema_version": 1, "mode": "fresh_shadow" if engine else "recorded",
            "samples": len(samples), "metrics": metrics,
            "latency_p50_ms": latencies[len(latencies) // 2] if latencies else None,
            "latency_p95_ms": latencies[min(len(latencies) - 1, int(len(latencies) * .95))] if latencies else None,
            "replays": replays}


def export_intent(samples, labels, seed="intent-v2"):
    """Whole groups plus duplicate-connected groups are kept in one partition."""
    parents = {sample["group_id"]: sample["group_id"] for sample in samples}

    def root(group):
        while parents[group] != group:
            parents[group] = parents[parents[group]]
            group = parents[group]
        return group

    by_digest = {}
    for sample in samples:
        # Duplicate detection ignores original aggregate-only inputs, which can differ by frame metadata.
        material = sample["decision"]
        fingerprint = digest({key: material[key] for key in ("point", "version", "questions", "state")})
        group = sample["group_id"]
        if fingerprint in by_digest:
            left, right = root(group), root(by_digest[fingerprint])
            parents[max(left, right)] = min(left, right)
        by_digest[fingerprint] = group
    partitions = {}
    for group in sorted(parents):
        bucket = int(digest({"seed": seed, "group": root(group)})[:8], 16) % 10
        partitions[group] = "train" if bucket < 7 else "validation" if bucket == 7 else "calibration" if bucket == 8 else "test"
    rows, seen, excluded = [], {}, Counter()
    for sample in sorted(samples, key=lambda row: row["sample_id"]):
        spec, _, _ = resolve_sample(sample)
        if spec.id != "tool.intent":
            excluded["other_point"] += 1
            continue
        annotation = labels.get(sample["sample_id"])
        if not annotation or not annotation["reviewed"] or annotation["answers"][spec.id] is None:
            excluded["unreviewed_or_unknown"] += 1
            continue
        split = partitions[sample["group_id"]]
        if split != "train" and annotation["origin"] != "manual":
            excluded["nonmanual_holdout"] += 1
            continue
        label = annotation["answers"][spec.id]
        fingerprint = digest({"state": sample["decision"]["state"], "question": spec.question})
        if fingerprint in seen:
            if seen[fingerprint] != label:
                raise ValueError("Conflicting labels for duplicate input")
            excluded["duplicate"] += 1
            continue
        seen[fingerprint] = label
        rows.append({"schema_version": 2, "point": spec.id, "version": spec.version,
                     "sample_id": sample["sample_id"], "input_digest": sample["input_digest"],
                     "group_id": sample["group_id"], "split": split, "origin": annotation["origin"],
                     "reviewer": annotation["reviewer"], "state": sample["decision"]["state"],
                     "question": spec.question, "criteria": LayaBooleanJudge.CRITERIA, "label": label})
    counts = Counter(row["split"] for row in rows)
    manifest = {"schema_version": 1, "seed": seed, "groups": partitions,
                "group_components": {group: root(group) for group in sorted(parents)},
                "counts": {split: counts[split] for split in ("train", "validation", "calibration", "test")},
                "excluded": dict(excluded), "dataset_digest": digest(rows),
                "ready_for_training": all(counts[split] > 0 for split in ("train", "validation", "calibration", "test"))}
    return rows, manifest


def training_partitions(rows):
    """Preflight before loading GPU weights or downloading a checkpoint."""
    versioned = any(row.get("schema_version") == 2 for row in rows)
    partitions = {name: [] for name in ("train", "validation", "calibration", "test")}
    groups, fingerprints = {}, {}
    for row in rows:
        if type(row.get("label")) is not bool or row.get("split") not in partitions:
            raise ValueError("Training rows require Boolean labels and known splits")
        if versioned:
            if (row.get("schema_version") != 2 or row.get("point") != "tool.intent"
                    or row.get("version") != builtin_registry().resolve("tool.intent").version):
                raise ValueError("Intent-v2 data cannot mix legacy rows or different specifications")
            group = row.get("group_id")
            if not isinstance(group, str) or not group.strip():
                raise ValueError("Intent-v2 rows require group IDs")
            if group in groups and groups[group] != row["split"]:
                raise ValueError("Task group leaked across dataset partitions")
            groups[group] = row["split"]
            fingerprint = digest({"state": row["state"], "question": row["question"]})
            if fingerprint in fingerprints:
                raise ValueError("Intent-v2 data contains duplicate inputs")
            fingerprints[fingerprint] = row["split"]
            if row["split"] != "train" and row.get("origin") != "manual":
                raise ValueError("Evaluation partitions require manual labels")
        partitions[row["split"]].append(row)
    required = partitions if versioned else ("train", "validation")
    if any(not partitions[name] for name in required):
        raise ValueError("Required training partitions are empty")
    return partitions, versioned


def validate_manifest(rows, manifest):
    if (manifest.get("schema_version") != 1 or manifest.get("dataset_digest") != digest(rows)
            or manifest.get("ready_for_training") is not True):
        raise ValueError("Intent-v2 data requires its unchanged, ready split manifest")
    components = {}
    for row in rows:
        group = row["group_id"]
        if manifest["groups"].get(group) != row["split"]:
            raise ValueError("Intent-v2 partitions differ from frozen manifest")
        component = manifest["group_components"][group]
        if component in components and components[component] != row["split"]:
            raise ValueError("Duplicate-connected group leaked across partitions")
        components[component] = row["split"]
