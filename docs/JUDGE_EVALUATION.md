# Judge 采样、标注与回放（第 13 阶段）

此阶段为 intent v2 重训准备输入和评测流程。没有启动新训练，也没有新增真实场景准确率结论。现有 Laya 仍只能用于 `tool.intent` 的 shadow；类型化模型可评估其他决策点。

## 1. 开启采样

从仓库根目录运行；Java 启动的 Python 进程也继承这些设置：

```sh
export MU_JUDGE_SAMPLES=work/judge-samples.jsonl
export MU_JUDGE_SAMPLE_GROUP=my-repository/task-family-001
PYTHONPATH=python python3 -m mupyjava --workspace /path/to/project --server
```

`MU_JUDGE_SAMPLE_GROUP` 必须显式填写。同一个仓库中的相关任务、续做、分支和派生会话必须使用同一组；希望按仓库整体隔离时，直接使用仓库 ID。不要为每一条决策随机换组。分区只能利用提供的组关系和完全重复输入，无法自动识别未标明的相近模板或不同仓库之间的语义相似任务。

默认不采样。只记录实际调用并完成的决策；intent 在 off 模式下仍可采样，但不会调用模型。其他关闭的功能不会为了采样而额外运行。取消发生在完成前的决策不伪造结果，已完成决策保留。采样不记录“用户批准了，所以模型判断正确”这样的标签。

每条样本包含：

| 字段 | 用途 |
| --- | --- |
| `sample_id` / `input_digest` | 唯一引用 / 完整输入摘要，防止标签错配 |
| `group_id` / `source` | 数据隔离组；轮次、服务会话和请求来源 |
| `decision` | 父规格版本、解析后的动态问题、聚合所需原始输入、实际提交给后端的 state |
| `policy` | 当时的模式、路由、置信度门槛和等待上限 |
| `observed` | 预测、路由尝试、应用结果、耗时；不是标签 |

样本是完整的敏感输入快照，可能含用户文本、命令和工具输出。存到忽略的 `work/`；文件以 0600 创建，拒绝追加到其他用户可读文件或符号链接。没有自动脱敏：脱敏会改变待评估输入，应另建有独立摘要的派生数据。普通 ledger 和 session 的 judge.record 只新增样本 ID，不混入原始 state；session 本来就保存对话。state 上限 64 KiB，样本输入上限 192 KiB，完整行上限 256 KiB。保存失败仅记 `sample_failure` 的异常类别，不改变判断或工具权限。

DeepSeek 编码模型与 lab judge 的临时工作区实验也支持：

```sh
PYTHONPATH=python python3 training/probe_harness.py \
  --samples work/probe-samples.jsonl --sample-group synthetic/probe-family-001
```

该实验提示输入 API key。四个任务来自同一个模板族，应放在同一组；六个直接 backend 探针仅作为旧回归输出，不冒充 Agent 输入采样或独立测试集。

## 2. 标注

```sh
PYTHONPATH=python python3 training/judge_pipeline.py annotate \
  --samples work/judge-samples.jsonl --output work/annotations.jsonl
```

输出只建立空标签，`reviewed=false`。人工查看对应样本后填写每个问题的答案、`reviewer` 和 `rationale`，再设置 `reviewed=true`。布尔答案必须为 JSON true/false；choice 必须是声明的选项；score 必须在范围内；证据不足用 null。`task.frame` 的字符串 `none` 是真实类别，与 null 不同。

`origin` 取 manual、teacher 或 synthetic。DeepSeek 可生成候选答案，记录为 teacher；人工审核候选后仍保留 teacher 来源，不自动变成独立人工测试标签。未审核数据不能导出训练行，默认非 manual 数据只能进入训练分区；离线独立指标只统计已审核 manual 标签。

第 14 阶段增加显式 `--allow-synthetic-eval` 实验开关：允许 synthetic 标签参与合成评测/训练清单，指标与 manual 分开，清单标记 synthetic_experiment；teacher 仍不能进入评测分区。这个开关不改变默认规则或 Laya 的 shadow 限制。实际实验与已知弱点见 [results-intent-v2.md](../training/results-intent-v2.md)。

目前 intent 的 write/edit 输入主要是路径、字节数和编辑数量；不会新增文件正文。遇到正文内容、旧目标或“继续”所指任务无法从输入判断时，应标 null 并列为输入契约缺口，不能强迫模型猜测。推荐覆盖 edit/write、run_command/bash/powershell、自定义修改工具，中英文、Python/Java 等语言项目，以及只读要求、错误路径、无关命令、续做和硬约束。

## 3. 回放与比较

```sh
PYTHONPATH=python python3 training/judge_pipeline.py replay \
  --samples work/judge-samples.jsonl --labels work/annotations.jsonl \
  --output work/replay-v1.json
```

默认完全离线，不运行工具或模型：从保存的路由回复按原来的阈值重新筛选答案，再调用当前可信规格的纯聚合策略。输入摘要、动态问题、构造的 state 或规格版本不一致即拒绝回放；最终应用结果必须与原记录一致。布尔概率沿用 0.2/0.8 弃权区间，并应用保存的置信度门槛；不会用 0.5 硬分类替代上线规则。

配置需要比较的 judge 后端，使用相同路由名称，再追加 `--fresh` 可做新推理。新推理统一 shadow，不执行工具，不自行追加样本。只评估 Laya 时追加 `--point tool.intent`；其他类型仍由支持相应规格的模型处理。Laya 的 active/其他决策点限制仍有效。

报告按父决策点、类型和工具统计人工标签数、回答覆盖率、弃权、回答后的正确率、布尔误判及 Brier、score 绝对误差、约束漏判、准入错误/结果块误丢弃、延迟 P50/P95。无分母的指标为 null。intent 的 `false_allow` 指明确错误的 true 预测；弃权时的 fallback=true 只是继续到原权限判断，不能算自动授权。`judged`、`observed_outcome` 和 `fallback` 分开报告。

准入指标只评价单次分类决策，不等于整段输出最终被省略的比例；功能级截止时间、首尾保护、归档回读和缓存仍需要 harness 端评测。记录耗时不包含样本落盘开销；fresh 的模型调用计入新决策耗时。报告拒绝覆盖现有文件。

## 4. 导出并冻结 intent v2

```sh
PYTHONPATH=python python3 training/judge_pipeline.py export \
  --samples work/judge-samples.jsonl --labels work/annotations.jsonl \
  --output work/intent-v2-round1
```

生成 `intent-v2.jsonl` 和 `manifest.json`，默认以固定种子按组哈希分到 train/validation/calibration/test，预期比例 70/10/10/10；实际比例取决于组数和组大小。相同模型输入出现于不同组时，先连接这些组，再分区；完全重复行去重，冲突标签拒绝。样本顺序不影响导出。增添数据要导出到新目录；新数据可能连接旧组并改变分区，不得把新导出继续称为旧的盲测。

四个分区必须都有数据才会标记 `ready_for_training=true`。此标记只代表结构完整，**不代表数据质量或数量足够**。开始重训前仍需审查各工具/语言和负例覆盖、输入缺口、相近模板隔离以及独立测试集大小；可从此前建议的 1000–3000 条审核训练样本、300–500 条独立测试样本起步，再按实际误判补充。

CUDA 环境重训命令：

```sh
PYTHONPATH=python python3 training/train_laya.py \
  --data work/intent-v2-round1/intent-v2.jsonl \
  --manual-eval training/manual_eval.jsonl \
  --output work/checkpoints/tool-intent-v2
```

训练前验证分区、重复输入、版本和冻结清单摘要，拒绝与旧 manual_eval 重叠。validation 只选 checkpoint；calibration 只拟合温度；test 在选择与校准结束后评估。旧 manual_eval 已被查看，只算回归集。旧格式数据仍保留验证集拟合温度的 v1 路径，metrics 明确标注 `validation_legacy`。导出的 checkpoint 固定训练与服务相同的 512 token 序列上限；JSON 采样完整不代表所有输入都会被 tokenizer 保留。当前训练器仅训练布尔 intent，不训练 choice、score、准入或摘要。

## 验证边界

第 13 阶段包含输入一致性、隐私分离、取消、拒绝写入、标签绑定、组/重复隔离、清单冻结、实际阈值、部分路由、choice/score、准入误丢弃、CLI 及真实 Java/Python HTTP 测试。测试模型和人工标签都是夹具，不作为新模型质量证据。没有调用付费 API、lab GPU 或执行新的训练。

## Explicit uncertainty training (milestone 15)

`export_intent(..., include_unknown=True)` preserves reviewed null answers and records
`unknown_target: uniform_boolean_distribution` in the frozen manifest. Training requires
both this marker and `--train-unknown`; default Boolean-only preflight still rejects null
labels. Null targets use `[0.5, 0.5]` soft cross entropy, known false/true use one-hot targets.
Validation checkpoint selection and separate calibration use this same loss. This encourages
uncertainty; it does not create a third semantic class or an independent evidence guarantee.
Known-label accuracy/coverage and unknown non-abstention are reported with separate denominators.

`build_intent_uncertainty.py` freezes bilingual goal/phrase families and retains only the
349 original v1 training rows (including 37 teacher candidates) in train. Prior v1 validation
and the inspected 20-case manual regression are excluded from training. The new experiment
covers five actual intent-triggering tools; previous hypothetical extension examples are not
newly implemented tools. Every synthetic evaluation still needs `--allow-synthetic-eval`;
teacher candidates cannot enter validation/calibration/test. No default or active-mode change.
