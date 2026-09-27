# First lab judge run (2026-09-27)

Base: Apache-2.0 `convaiinnovations/laya-multilingual` at revision `e4e9ddf21a7b1903b7acffd8814ad4307bf63a67`, fine-tuned for the `tool.intent` binary question. Training ran on one RTX PRO 6000 Blackwell GPU for three epochs; the best validation checkpoint was epoch 2. The run used 349 training examples, including 37 manually screened DeepSeek Flash candidates. The 84 validation examples come from the same templates as most training examples, with different files and commands. A separate 20-case hand-written set was not used for training or temperature fitting.

| Set | Base accuracy | Fine-tuned accuracy | Fine-tuned false positives | Fine-tuned false negatives |
| --- | ---: | ---: | ---: | ---: |
| Template validation (84) | 40/84 | 84/84 | 0 | 0 |
| Hand-written evaluation (20) | 8/20 | 18/20 | 1 | 1 |

The two remaining errors were both high confidence: a proposed `cargo publish` was accepted for a request to run `cargo test`, and a proposed write to `src/config.py` was accepted for a Chinese read-only request. A later smoke probe also accepted a write to `README.md` for an English read-only request. The checkpoint therefore remains experimental. The application restricts it to shadow mode; do not use it to authorize file changes or commands. The hand-written evaluation is too small and has been inspected after this run, so a fresh, real-world set is needed for any later promotion decision.

Lab checkpoint: `/home/ubuntu/work/mu-pyjava-judge/work/checkpoints/tool-intent` (not in Git). `model.safetensors` is 614 MiB on disk, SHA-256 `eefb252d471a4b924311e4977ce431090c50d3d5160f611de4608456d9ad59eb`. `metrics.json` and tokenizer/config files are in the same directory. Training dataset SHA-256: `244db0205392a51d7b6ac4841c36d66d82e20b826edbba8eb196c2e9526b8aaa`.

Reproduce the training input and run with the commands in [README](README.md). The Laya inference dependency is optional for the main Python/Java prototype and is loaded only when `MU_JUDGE_LAYA_PATH` is set.
