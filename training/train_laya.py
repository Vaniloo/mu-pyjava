"""Fine-tune Laya multilingual on tool.intent and evaluate untouched cases."""

import argparse
import copy
import hashlib
import json
import random
from pathlib import Path

import torch
import torch.nn.functional as F
from huggingface_hub import snapshot_download
from safetensors.torch import load_file, save_file
from transformers import AutoTokenizer

from laya.agent import _fix_tokenizer_config
from laya.common import QTYPES, build_model, build_sequence
from mupyjava.judge_data import read_jsonl, training_partitions, validate_manifest
from mupyjava.judge_samples import digest
from mupyjava.judge import LayaBooleanJudge
from evaluate_intent import metrics as serving_metrics
from intent_targets import target_probability
from training_design import audit_design, design_weights, load_design, ValidationStop


def read_rows(path):
    return read_jsonl(path)


def prepare(rows, tokenizer, config, design=None):
    items = []
    weights = design_weights(rows, design) if design else [1.] * len(rows)
    for row, weight in zip(rows, weights):
        question = row.get("question", "Does the user's latest request clearly call for this tool action?")
        criteria = row.get("criteria", LayaBooleanJudge.CRITERIA)
        ids, markers = build_sequence(
            tokenizer,
            row["state"],
            {"t": "noul", "ins": question, "crit": criteria},
            config.get("max_len", 512),
            config.get("head_max_len", 256),
        )
        if len(markers) != 2:
            raise ValueError("Expected two markers for a yes/no decision")
        items.append({"ids": ids, "markers": markers, "label": target_probability(row["label"]), "weight": weight})
    return items


def collate(items, pad_id, device):
    width = max(len(item["ids"]) for item in items)
    ids = torch.full((len(items), width), pad_id, dtype=torch.long)
    attention = torch.zeros((len(items), width), dtype=torch.long)
    markers = torch.zeros((len(items), 2), dtype=torch.long)
    for index, item in enumerate(items):
        size = len(item["ids"])
        ids[index, :size] = torch.tensor(item["ids"])
        attention[index, :size] = 1
        markers[index] = torch.tensor(item["markers"])
    return (
        ids.to(device),
        attention.to(device),
        markers.to(device),
        torch.ones((len(items), 2), dtype=torch.bool, device=device),
        torch.full((len(items),), QTYPES["noul"], dtype=torch.long, device=device),
        torch.tensor([item["label"] for item in items], dtype=torch.float32, device=device),
        torch.tensor([item.get("weight", 1.) for item in items], dtype=torch.float32, device=device),
    )


@torch.no_grad()
def predict(model, items, pad_id, device, batch_size, temperature=1.0):
    model.eval()
    logits_all = []
    labels_all = []
    for offset in range(0, len(items), batch_size):
        batch = collate(items[offset:offset + batch_size], pad_id, device)
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"):
            logits, _ = model(*batch[:5])
        logits_all.append(logits.float().cpu())
        labels_all.append(batch[5].cpu())
    logits = torch.cat(logits_all)
    labels = torch.cat(labels_all)
    probabilities = torch.softmax(logits / temperature, dim=-1)[:, 1]
    rows = [{"label": None if label == .5 else bool(label)} for label in labels.tolist()]
    return logits, labels, serving_metrics(rows, probabilities.tolist())


def target_loss(logits, targets, weights=None):
    """Unknown is a uniform soft target, never coerced into a negative label."""
    distribution = torch.stack((1 - targets, targets), dim=-1)
    losses = F.cross_entropy(logits.float(), distribution, reduction="none")
    if weights is not None:
        if weights.shape != losses.shape or not torch.isfinite(weights).all() or (weights <= 0).any():
            raise ValueError("Loss weights must be finite positive values matching the batch")
        losses = losses * weights
    return losses.mean()


def fit_temperature(logits, labels, weights=None):
    log_temp = torch.zeros((), requires_grad=True)
    optimizer = torch.optim.LBFGS([log_temp], lr=0.1, max_iter=80)

    def closure():
        optimizer.zero_grad()
        loss = target_loss(logits / log_temp.exp(), labels, weights)
        loss.backward()
        return loss

    optimizer.step(closure)
    return float(log_temp.exp().clamp(0.5, 5.0).item())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--manual-eval", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="convaiinnovations/laya-multilingual")
    parser.add_argument("--revision", default="e4e9ddf21a7b1903b7acffd8814ad4307bf63a67")
    parser.add_argument("--init-checkpoint", type=Path, help="Continue post-training from a local checkpoint copy")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--allow-synthetic-eval", action="store_true", help="Allow explicitly marked synthetic experiments")
    parser.add_argument("--train-unknown", action="store_true", help="Train null labels toward a uniform Boolean distribution")
    parser.add_argument("--encoder-lr", type=float, default=2e-5)
    parser.add_argument("--head-lr", type=float, default=1e-4)
    parser.add_argument("--design", type=Path, help="Opt-in semantic weighting, protected-input preflight and epoch-zero early stopping")
    args = parser.parse_args()
    if args.epochs < 1 or args.batch_size < 1 or not 0 < args.encoder_lr <= 1e-2 or not 0 < args.head_lr <= 1e-2:
        raise ValueError("Invalid training parameters")
    if args.output.exists():
        raise FileExistsError("Checkpoint output already exists; choose a new directory")
    rows = read_rows(args.data)
    partitions, versioned = training_partitions(rows, args.allow_synthetic_eval, args.train_unknown)
    if versioned:
        manifest = json.loads(args.data.with_name("manifest.json").read_text(encoding="utf-8"))
        validate_manifest(rows, manifest, args.allow_synthetic_eval, args.train_unknown)
    design = load_design(args.design) if args.design else None
    if design and not versioned:
        raise ValueError("Training design requires a versioned frozen dataset")
    design_audit = audit_design(rows, design, Path(__file__).resolve().parents[1]) if design else None
    manual_rows = read_rows(args.manual_eval)
    if not manual_rows or any(type(row.get("label")) is not bool for row in manual_rows):
        raise ValueError("Manual regression set must contain Boolean labels")
    if versioned:
        regression = {digest({"state": row["state"], "question": row.get("question",
            "Does the user's latest request clearly call for this tool action?")}) for row in manual_rows}
        if any(digest({"state": row["state"], "question": row["question"]}) in regression for row in rows):
            raise ValueError("Intent-v2 data overlaps the previously inspected manual regression set")
    random.seed(20260927)
    torch.manual_seed(20260927)
    device = torch.device(args.device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable")

    source = str(args.init_checkpoint) if args.init_checkpoint else snapshot_download(args.model, revision=args.revision)
    _fix_tokenizer_config(source)
    config = json.loads((Path(source) / "rl_agent_config.json").read_text())
    # Export the same sequence bound used in training so serving uses it too.
    config["max_len"] = min(config.get("max_len", 1024), 512)
    tokenizer = AutoTokenizer.from_pretrained(str(Path(source) / "tokenizer"))
    model = build_model(config, encoder_dir=str(Path(source) / "encoder"))
    model.load_state_dict(load_file(str(Path(source) / "model.safetensors")), strict=True)
    model.to(device)
    model.encoder.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.head_checkpointing = True

    train = prepare(partitions["train"], tokenizer, config, design)
    validation = prepare(partitions["validation"], tokenizer, config, design)
    calibration = prepare(partitions["calibration"], tokenizer, config, design) if versioned else validation
    test = prepare(partitions["test"], tokenizer, config) if versioned else []
    manual = prepare(manual_rows, tokenizer, config)
    if not train or not validation or not manual:
        raise ValueError("Train, validation and manual evaluation must all be nonempty")
    baseline_logits, baseline_labels, baseline_validation = predict(model, validation, tokenizer.pad_token_id, device, args.batch_size)
    validation_weights = torch.tensor([item["weight"] for item in validation]) if design else None
    initial_validation_loss = float(target_loss(baseline_logits, baseline_labels, validation_weights))
    baseline_manual = predict(model, manual, tokenizer.pad_token_id, device, args.batch_size)[2]
    print("baseline", json.dumps({"validation": baseline_validation, "manual": baseline_manual}), flush=True)

    encoder = [parameter for name, parameter in model.named_parameters() if name.startswith("encoder.")]
    head = [parameter for name, parameter in model.named_parameters() if not name.startswith("encoder.")]
    optimizer = torch.optim.AdamW([
        {"params": encoder, "lr": args.encoder_lr},
        {"params": head, "lr": args.head_lr},
    ], weight_decay=0.01)
    stop = ValidationStop(initial_validation_loss, design["patience"], design["min_delta"]) if design else None
    best_loss = initial_validation_loss if design else float("inf")
    best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()} if design else None
    best_epoch = 0 if design else None
    history = []
    for epoch in range(args.epochs):
        random.Random(20260927 + epoch).shuffle(train)
        model.train()
        losses = []
        for offset in range(0, len(train), args.batch_size):
            batch = collate(train[offset:offset + args.batch_size], tokenizer.pad_token_id, device)
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=device.type == "cuda"):
                logits, _ = model(*batch[:5])
                loss = target_loss(logits, batch[5], batch[6] if design else None)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            losses.append((float(loss.detach()), len(batch[5])))
        val_logits, val_labels, val_metrics = predict(
            model, validation, tokenizer.pad_token_id, device, args.batch_size
        )
        val_loss = float(target_loss(val_logits, val_labels, validation_weights))
        print("epoch", epoch + 1, "train_loss", round(sum(loss * count for loss, count in losses) / sum(count for _, count in losses), 4),
              "validation_loss", round(val_loss, 4), "metrics", val_metrics, flush=True)
        improved, should_stop = stop.observe(epoch + 1, val_loss) if stop else (val_loss < best_loss, False)
        history.append({"epoch": epoch + 1, "validation_loss": val_loss, "selected": improved,
                        "training_loss": sum(loss * count for loss, count in losses) / sum(count for _, count in losses),
                        "validation_metrics": val_metrics})
        if improved:
            best_epoch = epoch + 1
            best_loss = val_loss
            best_state = {key: (value.detach() if design else value.detach().half()).cpu().clone() for key, value in model.state_dict().items()}

        if should_stop:
            print("early_stop", epoch + 1, "best_epoch", best_epoch, flush=True)
            break

    if best_state is None:
        raise RuntimeError("No checkpoint was selected")
    model.load_state_dict(best_state, strict=True)
    calibration_logits, calibration_labels, _ = predict(model, calibration, tokenizer.pad_token_id, device, args.batch_size)
    calibration_weights = torch.tensor([item["weight"] for item in calibration]) if design else None
    temperature = fit_temperature(calibration_logits, calibration_labels, calibration_weights)
    trained_validation = predict(model, validation, tokenizer.pad_token_id, device, args.batch_size, temperature)[2]
    trained_manual = predict(model, manual, tokenizer.pad_token_id, device, args.batch_size, temperature)[2]
    trained_test = predict(model, test, tokenizer.pad_token_id, device, args.batch_size, temperature)[2] if test else None

    args.output.mkdir(parents=True, exist_ok=True)
    save_file({key: value.contiguous() for key, value in best_state.items()}, str(args.output / "model.safetensors"))
    model.encoder.config.save_pretrained(str(args.output / "encoder"))
    tokenizer.save_pretrained(str(args.output / "tokenizer"))
    exported = copy.deepcopy(config)
    exported["fine_tuned"] = True
    exported["model_name"] = "mu-pyjava-tool-intent-laya"
    temps = list(exported.get("temperature", [1.0, 1.0, 1.0]))
    temps[QTYPES["noul"]] = temperature
    exported["temperature"] = temps
    exported.pop("temperature_by_options", None)
    (args.output / "rl_agent_config.json").write_text(json.dumps(exported, indent=2), encoding="utf-8")
    metrics = {
        "base_model": args.model,
        "base_revision": args.revision,
        "initialization": "local_checkpoint" if args.init_checkpoint else "pinned_hub_base",
        "initial_checkpoint_sha256": hashlib.sha256((Path(source) / "model.safetensors").read_bytes()).hexdigest(),
        "training_data_sha256": hashlib.sha256(args.data.read_bytes()).hexdigest(),
        "manual_eval_sha256": hashlib.sha256(args.manual_eval.read_bytes()).hexdigest(),
        "train_count": len(train),
        "validation_count": len(validation),
        "manual_count": len(manual),
        "calibration_count": len(calibration),
        "test_count": len(test),
        "calibration_split": "calibration" if versioned else "validation_legacy",
        "evaluation_basis": manifest.get("evaluation_basis", "manual_only") if versioned else "legacy",
        "baseline_validation": baseline_validation,
        "baseline_manual": baseline_manual,
        "trained_validation": trained_validation,
        "trained_manual": trained_manual,
        "trained_test": trained_test,
        "noul_temperature": temperature,
        "best_validation_loss": best_loss,
        "best_epoch": best_epoch,
        "initial_validation_loss": initial_validation_loss,
        "training_design": design_audit,
        "epoch_history": history,
        "completed_epochs": len(history),
        "selected_initial_weights": best_epoch == 0,
        "temperature_at_bound": temperature <= .5 or temperature >= 5.,
        "epochs": args.epochs,
        "encoder_lr": args.encoder_lr,
        "head_lr": args.head_lr,
        "unknown_target": "uniform_boolean_distribution" if args.train_unknown else None,
        "unknown_counts": {name: sum(row["label"] is None for row in part) for name, part in partitions.items()},
    }
    (args.output / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print("final", json.dumps(metrics), flush=True)


if __name__ == "__main__":
    main()
