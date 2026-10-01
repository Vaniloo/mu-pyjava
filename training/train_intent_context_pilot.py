"""Run an isolated synthetic context-format adaptation from a retained Laya checkpoint.

This experiment is not an independent quality estimate and the output is never
wired to the serving judge. Validation selects weights; calibration fits only
temperature; the held-out synthetic test is reported after selection.
"""

import argparse
import copy
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

import torch
from laya.agent import _fix_tokenizer_config
from laya.common import QTYPES, build_model
from safetensors.torch import load_file, save_file
from transformers import AutoTokenizer

from mupyjava.intent_context import (CONTEXT_POINT, CONTEXT_QUESTION,
                                     CONTEXT_VERSION, serialized_state_digest)
from mupyjava.judge import LayaBooleanJudge
from mupyjava.judge_data import read_jsonl
from mupyjava.judge_samples import digest
from train_laya import (collate, fit_temperature, predict, prepare,
                        target_loss)


SPLITS = ("train", "validation", "calibration", "test")


def preflight(data, manifest_path, audit_path):
    rows = read_jsonl(data)
    manifest = json.loads(manifest_path.read_text())
    audit = json.loads(audit_path.read_text())
    data_hash = hashlib.sha256(data.read_bytes()).hexdigest()
    if (manifest.get("purpose") != "synthetic_context_format_pilot" or
            manifest.get("dataset_digest") != digest(rows) or
            manifest.get("dataset_file_sha256") != data_hash or
            manifest.get("independent_human_gold") != 0 or
            manifest.get("real_user_project_traces") != 0 or
            audit.get("dataset_sha256") != data_hash or
            len(audit.get("status", {})) != len(rows)):
        raise ValueError("Frozen synthetic dataset or token audit changed")
    groups, fingerprints = {}, set()
    counts = Counter()
    for row in rows:
        if (row.get("schema_version") != 3 or row.get("point") != CONTEXT_POINT or
                row.get("version") != CONTEXT_VERSION or row.get("question") != CONTEXT_QUESTION or
                row.get("criteria") != LayaBooleanJudge.CRITERIA or
                row.get("origin") not in {"synthetic", "teacher"} or
                "label" not in row or
                (type(row["label"]) is not bool and row["label"] is not None) or
                row.get("split") not in SPLITS or
                row.get("serialized_state_sha256") != serialized_state_digest(row["state"]) or
                row.get("input_digest") != digest({"point": CONTEXT_POINT,
                    "version": CONTEXT_VERSION, "question": CONTEXT_QUESTION,
                    "state": row["state"]}) or
                not audit["status"].get(row["sample_id"], {}).get("fit")):
            raise ValueError("Candidate row is invalid or clipped")
        if row["input_digest"] in fingerprints:
            raise ValueError("Duplicate candidate input")
        fingerprints.add(row["input_digest"])
        group = row["group_id"]
        if group in groups and groups[group] != row["split"]:
            raise ValueError("Task group leaked across partitions")
        groups[group] = row["split"]
        counts[row["split"]] += 1
    if (set(groups) != set(manifest["groups"]) or
            any(groups[group] != manifest["groups"][group] for group in groups) or
            dict(sorted(counts.items())) != manifest["counts"] or
            any(not counts[split] for split in SPLITS)):
        raise ValueError("Candidate split manifest changed or is incomplete")
    return {split: [row for row in rows if row["split"] == split] for split in SPLITS}, data_hash, audit


def run(args):
    if args.output.exists():
        raise FileExistsError("Candidate checkpoint output already exists")
    if args.epochs < 1 or args.batch_size < 1:
        raise ValueError("Invalid training schedule")
    partitions, data_hash, audit = preflight(args.data, args.manifest, args.token_audit)
    source = args.init_checkpoint
    _fix_tokenizer_config(str(source))
    config = json.loads((source / "rl_agent_config.json").read_text())
    if (config.get("max_len") != audit.get("max_len") or
            config.get("head_max_len") != audit.get("head_max_len")):
        raise ValueError("Checkpoint limits differ from token audit")
    random.seed(20261001)
    torch.manual_seed(20261001)
    device = torch.device(args.device)
    if device.type != "cuda" or not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for this pilot")
    tokenizer = AutoTokenizer.from_pretrained(str(source / "tokenizer"))
    model = build_model(config, encoder_dir=str(source / "encoder"))
    model.load_state_dict(load_file(str(source / "model.safetensors")), strict=True)
    model.to(device)
    model.encoder.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.head_checkpointing = True
    prepared = {split: prepare(partitions[split], tokenizer, config) for split in SPLITS}
    shipped_temp = list(config.get("temperature", [1., 1., 1.]))[QTYPES["noul"]]
    initial_val_logits, initial_val_labels, _ = predict(
        model, prepared["validation"], tokenizer.pad_token_id, device, args.batch_size)
    initial_loss = float(target_loss(initial_val_logits, initial_val_labels))
    initial_test = predict(model, prepared["test"], tokenizer.pad_token_id,
                           device, args.batch_size, shipped_temp)[2]
    print("baseline", json.dumps({"validation_loss": initial_loss,
                                  "test_shipped_temperature": initial_test}), flush=True)

    encoder = [parameter for name, parameter in model.named_parameters() if name.startswith("encoder.")]
    head = [parameter for name, parameter in model.named_parameters() if not name.startswith("encoder.")]
    optimizer = torch.optim.AdamW([
        {"params": encoder, "lr": args.encoder_lr},
        {"params": head, "lr": args.head_lr}], weight_decay=.01)
    best_loss, best_epoch = initial_loss, 0
    best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
    history = []
    for epoch in range(args.epochs):
        random.Random(20261001 + epoch).shuffle(prepared["train"])
        model.train()
        losses = []
        for offset in range(0, len(prepared["train"]), args.batch_size):
            batch = collate(prepared["train"][offset:offset + args.batch_size],
                            tokenizer.pad_token_id, device)
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                logits, _ = model(*batch[:5])
                loss = target_loss(logits, batch[5])
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.)
            optimizer.step()
            losses.append((float(loss.detach()), len(batch[5])))
        val_logits, val_labels, val_metrics = predict(model, prepared["validation"],
            tokenizer.pad_token_id, device, args.batch_size)
        val_loss = float(target_loss(val_logits, val_labels))
        mean_train = sum(loss * n for loss, n in losses) / sum(n for _, n in losses)
        selected = val_loss < best_loss - .001
        history.append({"epoch": epoch + 1, "training_loss": mean_train,
                        "validation_loss": val_loss, "validation_metrics": val_metrics,
                        "selected": selected})
        print("epoch", epoch + 1, "train_loss", round(mean_train, 5),
              "validation_loss", round(val_loss, 5), "selected", selected, flush=True)
        if selected:
            best_loss, best_epoch = val_loss, epoch + 1
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
    model.load_state_dict(best_state, strict=True)
    cal_logits, cal_labels, _ = predict(model, prepared["calibration"],
                                       tokenizer.pad_token_id, device, args.batch_size)
    temperature = fit_temperature(cal_logits, cal_labels)
    trained_test = predict(model, prepared["test"], tokenizer.pad_token_id,
                           device, args.batch_size, temperature)[2]
    args.output.mkdir(parents=True, exist_ok=False)
    save_file({key: value.contiguous() for key, value in best_state.items()},
              str(args.output / "model.safetensors"))
    model.encoder.config.save_pretrained(str(args.output / "encoder"))
    tokenizer.save_pretrained(str(args.output / "tokenizer"))
    exported = copy.deepcopy(config)
    exported["fine_tuned"] = True
    exported["model_name"] = "mu-pyjava-intent-context-synthetic-pilot"
    temperatures = list(exported.get("temperature", [1., 1., 1.]))
    temperatures[QTYPES["noul"]] = temperature
    exported["temperature"] = temperatures
    exported.pop("temperature_by_options", None)
    (args.output / "rl_agent_config.json").write_text(json.dumps(exported, indent=2))
    report = {"schema_version": 1, "purpose": "synthetic_context_format_adaptation",
              "source_checkpoint_sha256": hashlib.sha256((source / "model.safetensors").read_bytes()).hexdigest(),
              "dataset_sha256": data_hash, "token_audit_sha256": hashlib.sha256(args.token_audit.read_bytes()).hexdigest(),
              "counts": {split: len(partitions[split]) for split in SPLITS},
              "initial_validation_loss": initial_loss, "best_validation_loss": best_loss,
              "best_epoch": best_epoch, "shipped_source_temperature": shipped_temp,
              "calibrated_temperature": temperature, "initial_test": initial_test,
              "trained_test": trained_test, "epoch_history": history,
              "independent_human_gold": 0, "real_user_project_traces": 0,
              "candidate_inference_only": True, "active_serving": False}
    (args.output / "metrics.json").write_text(json.dumps(report, indent=2))
    print("final", json.dumps({key: report[key] for key in (
        "best_epoch", "initial_validation_loss", "best_validation_loss",
        "calibrated_temperature", "initial_test", "trained_test")}), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--token-audit", type=Path, required=True)
    parser.add_argument("--init-checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--encoder-lr", type=float, default=5e-6)
    parser.add_argument("--head-lr", type=float, default=2.5e-5)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
