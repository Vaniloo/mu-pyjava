# Tool-intent judge experiment

TypeSafe Jev is a hosted model and mu does not provide its weights. This experiment fine-tunes the Apache-2.0 [Laya multilingual checkpoint](https://huggingface.co/convaiinnovations/laya-multilingual) for the first mu-pyjava decision point, `tool.intent`.

`build_data.py` generates bilingual synthetic train and validation examples. `deepseek_reviewed.jsonl` contains 37 additional training candidates generated with DeepSeek Flash and manually screened for three ambiguous or mismatched examples; the label distribution is 29 positive / 8 negative. The API key is never stored. `manual_eval.jsonl` contains 20 separately written cases and must not be used for training or temperature calibration. These small sets are sufficient to test the pipeline, not to establish broad reliability. The model should stay in shadow mode until it is assessed against real, independently labeled decisions.

On a CUDA host with Python 3.10+, PyTorch, Transformers, Safetensors, Hugging Face Hub and `laya==0.3.20` installed:

```sh
python training/build_data.py work/tool-intent.jsonl --augment training/deepseek_reviewed.jsonl
PYTHONPATH=python python training/train_laya.py \
  --data work/tool-intent.jsonl \
  --manual-eval training/manual_eval.jsonl \
  --output work/checkpoints/tool-intent
```

The script pins the base checkpoint revision, reports baseline and fine-tuned results, and saves `metrics.json` with dataset hashes and runtime abstention/false-allow metrics. Legacy v1 data fits temperature on validation; intent-v2 exports require separate calibration and test partitions plus the frozen manifest. Checkpoints are intentionally excluded from Git.

## Intent-v2 data and evaluation

Opt-in exact-input sampling, annotation templates, tool-free recorded/fresh-shadow replay and group-isolated exports are implemented. Follow [JUDGE_EVALUATION.md](../docs/JUDGE_EVALUATION.md). Predictions and permission approvals never become gold labels automatically. No new training or independently measured accuracy is included in milestone 13.

Milestone 14 completed broader synthetic post-training and actual v1/v2 serving comparison,
plus a live DeepSeek harness run. See [results-intent-v2.md](results-intent-v2.md). Frozen
compressed data and prediction reports are included; experimental synthetic evaluation
requires `--allow-synthetic-eval`. The new checkpoint remains shadow-only and shows
overconfidence on missing-context inputs.

## Connect the lab checkpoint to the app

Run the service on the lab host with the Python environment used for training. It listens on lab loopback only:

```sh
PYTHONPATH=python python training/serve_laya.py \
  --checkpoint work/checkpoints/tool-intent --device cuda --port 28765
```

Forward that port over SSH from the desktop machine:

```sh
ssh -N -L 18765:127.0.0.1:28765 lab
```

Then run the Python CLI or Java desktop with `MU_JUDGE_MODE=shadow`, `MU_JUDGE_LAYA_URL=http://127.0.0.1:18765`, and optionally `--ledger work/judge-ledger.jsonl`. The Java desktop displays each judge event in its transcript. For a reproducible temporary-workspace exercise using DeepSeek Flash as the coding model, run `PYTHONPATH=python python training/probe_harness.py`. That script prompts for the API key and saves only the synthetic task transcript and verdicts under the ignored `work/` directory.

## Natural requests and explicit uncertainty

Milestone 15 adds null-target training, old v1 training rehearsal and an extended live harness.
See [results-intent-uncertainty.md](results-intent-uncertainty.md) for improvements and regressions.
The model remains shadow-only. Frozen data are under `datasets/intent-uncertainty-r1`.

```sh
PYTHONPATH=python python3 training/train_laya.py \
  --data training/datasets/intent-uncertainty-r1/intent-v2.jsonl.gz \
  --manual-eval training/manual_eval.jsonl --output work/checkpoints/uncertainty-new \
  --init-checkpoint work/checkpoint-copy --epochs 3 --batch-size 8 \
  --encoder-lr 0.00001 --head-lr 0.00005 --train-unknown --allow-synthetic-eval
```

`work/checkpoint-copy` must be a separate local copy of the retained v1 checkpoint. Exporting
reviewed null labels from raw samples uses `training/judge_pipeline.py export --include-unknown`.
`probe_harness.py --extended` also exercises a Chinese configuration edit and an authorized
local unit-test command in temporary fixtures, while keeping the original denied-command task.

## Mixed rehearsal and coherent path controls

Milestone16 addresses dataset shortcuts and restores scoped file modifications. See
[results-intent-mixed.md](results-intent-mixed.md); manual/live negative failures still prevent
promotion. Frozen data and coherent path controls are in `datasets/intent-mixed-r1`.

```sh
PYTHONPATH=python python3 training/train_laya.py \
  --data training/datasets/intent-mixed-r1/intent-v2.jsonl.gz \
  --manual-eval training/manual_eval.jsonl --output work/checkpoints/mixed-new \
  --init-checkpoint work/checkpoint-copy --epochs 3 --batch-size 16 \
  --encoder-lr 0.000005 --head-lr 0.000025 --train-unknown --allow-synthetic-eval
```

Evaluate controls with `evaluate_intent.py --data .../path-controls-v2.jsonl.gz --split all`.
The controls preserve request/action path relations and share parents with test: they are not
extra independent holdout examples. `intent_interventions.paired_metrics` checks aligned parent
predictions and checkpoint identity. Full reports publish family and distinct-request counts.

For live fixtures, `probe_harness.py --extended --counterfactuals --samples work/new-samples.jsonl`
also checks synthetic prohibited/unknown requests against actual argument metadata, without
executing them. Focus a retry with `--task fix_then_test --max-steps 10`. Raw labels are never
filled from model predictions. Pending independent reviewer packets are retained separately.

## False-allow diagnosis and original-base comparison

Milestone17 adds frozen single-factor controls and a separately identified joint-size follow-up.
The original pinned Laya, v1 and mixed-r1 now use the same actual serving criteria and thresholds.
Equivalent read-only wording changes answers dramatically; small replacement size is insufficient
to explain the inspected edit failures. See [results-intent-ablation.md](results-intent-ablation.md).
These correlated synthetic interventions are diagnostic regressions, not training exports or
independent deployment benchmarks. No new weights or active promotion.

```sh
PYTHONPATH=python python3 training/intent_ablation.py build --output work/ablation-new
PYTHONPATH=python python3 training/intent_ablation.py build --joint-bytes --output work/joint-new
```
