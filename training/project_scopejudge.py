"""Audit ScopeJudge and project exact bash calls into an external-only intent probe.

This is a cross-domain transfer diagnostic, not a conversion to training gold.
No proposed command is executed. Input must be the pinned upstream train.jsonl.
"""

import argparse
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

from mupyjava.decision_points import TOOL_INTENT


SOURCE_REVISION = "b8b06a65a09e39a4fe1682ef56f48fd14ab74800"
SOURCE_SHA256 = "63a176fde1933464dc1cbfe3d5adcae8d4991ab0f93368903276c55a74efb527"
SOURCE_URL = (
    "https://huggingface.co/datasets/dreadnode/scopejudge/resolve/"
    + SOURCE_REVISION + "/train.jsonl"
)


def project(source_bytes, *, expected_sha256=SOURCE_SHA256,
            expected_trajectories=100, expected_calls=4897):
    """Return eligible rows and an audit; preserve upstream labels and votes."""
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    if source_hash != expected_sha256:
        raise ValueError("ScopeJudge source SHA-256 differs from pinned revision")
    projected = []
    excluded = Counter()
    source_tools = Counter()
    label_counts = Counter()
    seen = set()
    trajectory_count = 0
    source_calls = 0
    for line in source_bytes.splitlines():
        if not line.strip():
            continue
        trajectory = json.loads(line)
        trajectory_count += 1
        meta = trajectory["extra"]["scopejudge"]
        request = meta["user_intent"]
        calls = {}
        for step in trajectory["steps"]:
            for call in step.get("tool_calls") or []:
                key = (step["step_id"], call["tool_call_id"])
                if key in calls:
                    raise ValueError("Repeated source tool-call identifier")
                calls[key] = call
        labels = meta["labels"]
        if len(calls) != len(labels):
            raise ValueError("Every source call must have one expert label")
        for annotation in labels:
            key = (annotation["step_id"], annotation["tool_call_id"])
            global_key = (trajectory["session_id"], *key)
            if key not in calls or global_key in seen:
                raise ValueError("Missing or repeated source label")
            seen.add(global_key)
            source_calls += 1
            call = calls[key]
            votes = [annotation[f"reviewer_{index}"] for index in range(1, 6)]
            if any(type(vote) is not bool for vote in votes) or sum(votes) != annotation["votes"]:
                raise ValueError("Source reviewer votes are inconsistent")
            golden = "out_of_scope" if sum(votes) >= 3 else "in_scope"
            if golden != annotation["golden_label"]:
                raise ValueError("Source majority label is inconsistent")
            source_tools[call["function_name"]] += 1
            if call["function_name"] != "bash":
                excluded["different_tool"] += 1
                continue
            if not isinstance(request, str) or len(request) > 1000:
                excluded["request_over_agent_limit"] += 1
                continue
            arguments = call["arguments"]
            if not isinstance(arguments, dict) or set(arguments) - {"cmd", "timeout"}:
                excluded["unsupported_arguments"] += 1
                continue
            command = arguments.get("cmd")
            if not isinstance(command, str) or not command.strip():
                excluded["invalid_command"] += 1
                continue
            timeout = arguments.get("timeout")
            if timeout is not None and (type(timeout) is not int or not 1 <= timeout <= 120):
                excluded["invalid_timeout"] += 1
                continue
            state_args = {"command": command}
            if timeout is not None:
                state_args["timeout"] = timeout
            sample_id = f"{trajectory['session_id']}:{annotation['step_id']}:{annotation['tool_call_id']}"
            label_counts[golden] += 1
            projected.append({
                "schema_version": 2,
                "point": TOOL_INTENT.id,
                "version": TOOL_INTENT.version,
                "split": "external_eval",
                "origin": "scopejudge_expert_projection",
                "group_id": "scopejudge/" + meta["task_family"],
                "sample_id": sample_id,
                "question": TOOL_INTENT.question,
                "state": {"user_request": request, "tool": "bash", "arguments": state_args},
                "label": golden == "in_scope",
                "tags": {
                    "source": "dreadnode/scopejudge",
                    "source_trajectory": trajectory["session_id"],
                    "source_task_family": meta["task_family"],
                    "source_step_id": annotation["step_id"],
                    "source_tool_call_id": annotation["tool_call_id"],
                    "expert_out_of_scope_votes": sum(votes),
                    "expert_unanimous": sum(votes) in (0, 5),
                    "projection": "intent_plus_current_bash_only",
                },
            })
        if set(calls) != {(
            item["step_id"], item["tool_call_id"]
        ) for item in labels}:
            raise ValueError("Unlabeled source call")
    if trajectory_count != expected_trajectories or source_calls != expected_calls:
        raise ValueError("ScopeJudge source inventory changed")
    audit = {
        "source_url": SOURCE_URL,
        "source_revision": SOURCE_REVISION,
        "source_sha256": source_hash,
        "source_trajectories": trajectory_count,
        "source_calls": source_calls,
        "source_tools": dict(sorted(source_tools.items())),
        "projected_calls": len(projected),
        "projected_task_families": len({row["group_id"] for row in projected}),
        "projected_labels": dict(sorted(label_counts.items())),
        "projected_unanimous": sum(row["tags"]["expert_unanimous"] for row in projected),
        "excluded": dict(sorted(excluded.items())),
        "interpretation": "Cross-domain, request-and-call-only transfer probe; no trajectory history or original static policy. Expert source labels are not independent labels of the transformed local tool state.",
        "training_allowed": False,
    }
    return projected, audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.audit.exists():
        raise FileExistsError("Projection output already exists")
    rows, audit = project(args.source.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("wb") as file:
        with gzip.GzipFile(filename="", mode="wb", fileobj=file, mtime=0) as compressed:
            for row in rows:
                compressed.write((json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode())
    audit["projection_sha256"] = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: audit[key] for key in (
        "source_calls", "projected_calls", "projected_task_families",
        "projected_labels", "excluded", "projection_sha256"
    )}))


if __name__ == "__main__":
    main()
