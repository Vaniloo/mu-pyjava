# Tool-intent judge experiment

TypeSafe Jev is a hosted model and mu does not provide its weights. This experiment fine-tunes the Apache-2.0 [Laya multilingual checkpoint](https://huggingface.co/convaiinnovations/laya-multilingual) for the first mu-pyjava decision point, `tool.intent`.

`build_data.py` generates bilingual synthetic train and validation examples. `deepseek_reviewed.jsonl` contains 37 additional training candidates generated with DeepSeek Flash and manually screened for three ambiguous or mismatched examples; the label distribution is 29 positive / 8 negative. The API key is never stored. `manual_eval.jsonl` contains 20 separately written cases and must not be used for training or temperature calibration. These small sets are sufficient to test the pipeline, not to establish broad reliability. The model should stay in shadow mode until it is assessed against real, independently labeled decisions.

On a CUDA host with Python 3.10+, PyTorch, Transformers, Safetensors, Hugging Face Hub and `laya==0.3.20` installed:

```sh
python training/build_data.py work/tool-intent.jsonl --augment training/deepseek_reviewed.jsonl
python training/train_laya.py \
  --data work/tool-intent.jsonl \
  --manual-eval training/manual_eval.jsonl \
  --output work/checkpoints/tool-intent
```

The script pins the base checkpoint revision, reports baseline and fine-tuned results, fits yes/no temperature on validation data, and saves `metrics.json` with dataset hashes. Checkpoints are intentionally excluded from Git.
