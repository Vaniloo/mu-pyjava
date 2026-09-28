# mu 与 mu-pyjava 对照

对照时间：2026-09-27，更新于 2026-09-28。mu 官方 `main`：[`2dfd59c`](https://github.com/qybaihe/mu/tree/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6)；本项目对照基线：`241a363`，现已增加多处编辑、文件/搜索/Git/命令操作接口，以及图片附件和自定义工具注册。这里比较行为与架构，不以工具名称数量衡量完成度。

| 领域 | mu 当前实现 | mu-pyjava 当前实现 | 下一步 |
| --- | --- | --- | --- |
| 核心工具 | [8 类工具](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/tools/index.ts)：read、bash、powershell、edit、write、grep、find、ls；各有定义、展示器和扩展入口 | 13 个内置工具名（含两个输出回读名称），加上显式注册的 Python 工具；文本/图片读写搜索、命令和输出回读；统一文本/图片内容块、详情和错误结构 | 热注册、扩展展示器与 MCP 工具发现 |
| 操作接口 | [read](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/tools/read.ts)、[find](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/tools/find.ts)、[ls](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/tools/ls.ts)、[edit](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/tools/edit.ts) 与 [bash](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/tools/bash.ts) 分别声明可替换操作；部分搜索路径仍直接调用本机工具 | 文件、搜索、Git 和命令均可注入操作；完整虚拟工作区代理流程已验证 | 实际远程传输尚缺；二进制读取与图片处理接口已可注入 |
| 编辑语义 | [一次编辑多个互不重叠区段](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/tools/edit-diff.ts)；按原文匹配，保留 BOM/换行，另有模糊匹配回退及 diff/patch/首行元数据 | 多区段原文匹配、BOM/CRLF、原子替换；标准 patch、带行号 diff 和定位元数据；显式启用的确定性模糊匹配，实际使用时强制逐次审批 | 默认精确匹配与 mu 自动回退不同；桌面编辑器/扩展展示器和远程传输尚缺 |
| 读取与搜索 | 文本分页、图片附件与模型兼容提示；find/grep/ls 有结构化截断信息 | 文本分页、搜索限额和结构化结果；PNG/JPEG/GIF/WebP/BMP 静态图片附件、缩放/转正/格式转换与文本模型省略提示 | 动画、自动模型能力发现、真实视觉 API 验证与桌面图片展示 |
| 命令 | bash 与 PowerShell 共用流式命令机制，支持取消、超时、输出归档和操作注入 | 直接进程与 Bash/PowerShell 工具，共用审批、流式输出、停止、超时、归档及结构化元数据 | 原生 PowerShell 测试需安装环境；实际远程传输尚缺 |
| 权限 | [三种会话权限模式](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/kyrn-judge/src/extension/features/permissions.ts)、风险规则、会话授权与 Jev 判定 | 桌面逐次审批、同文件会话授权、受保护路径强制再问；intent/constraint/review 只能否决；命令风险规则与双问题聚合可增加逐次确认，Laya 保持 shadow | mu 三种权限模式、完整命令解析与独立校准；当前判断不能自动授予权限 |
| 上下文 | [压缩](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/compaction/compaction.ts)结合用量、预算、近期历史和摘要；[工具输出准入](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/kyrn-judge/src/extension/features/admission.ts)保留首尾、归档分块并可用 Jev 判断 | 可配置输入/回答/工具/图片预算估算；整轮历史省略、首尾文本投影、全文归档回读和 Java 记录；保留原始历史；可选输出分类准入、带来源的历史摘录及模型选择 | 实际 tokenizer/用量校准、独立准入评估、生成式摘要、异步 shadow 与测试日志去重 |
| 会话与判断 | [树形 JSONL 会话](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/session-manager.ts) 支持分支/派生；[决策规格](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/kyrn-judge/src/decision.ts) 支持 boolean/choice/score、逐点模式与路由 | 树形持久会话、已完成轮次分支选择、独立派生及 Java 历史浏览；boolean/choice/score 注册、逐点模式、按序路由和回退；动态多问题约束/风险聚合、原文任务约束及 Java Task 历史；两个本项目探针仍默认关闭 | 生成式任务框架、选项分布聚合、mu 审批/权限模式、用量/能力路由、校准回放；任意条目导航和自动总结 |

## 排序依据

1. **已完成本轮：多处编辑。** 它直接减少重复工具调用，仍沿用现有审批、版本复查和原子提交。
2. **已完成接口阶段。** 读、列目录、写入、编辑、搜索、Git 和命令均可注入操作，完整虚拟工作区流程已通过审批和输出回读验证；当前工具参数和 Java 协议保持稳定。mu 的各工具接口提供了结构参考，但不能把“有接口”误记为已经有完整远程工作区。当前替代接口测试使用内存数据，没有连接 SSH；接口及传输要求见 [OPERATIONS.md](OPERATIONS.md)。
3. **结果结构与 Shell 已接入。** 已提供文本内容块、结构化 details、错误标记及截断/续读元数据，并支持 Bash/PowerShell 工具。图片内容块、模型能力适配与显式工具注册已接入，审批和取消仍有效；patch/导航元数据和显式模糊匹配也已接入，并通过 Git 回放和 Java 审批/恢复验证；实现边界见 [EDITING.md](EDITING.md)。分支会话已接入：可从已完成轮次继续、保留兄弟路径、独立复制输出归档，重启恢复选中路径；不会重放工具或回滚工作区。与 mu 的任意条目导航和完整树展示仍有差距，见 [SESSIONS.md](SESSIONS.md)。上下文预算、全文归档/回读和 Java 用量记录已接入；与 mu 的 Jev 分块语义筛选和自动总结仍有差距，见 [CONTEXT.md](CONTEXT.md)。逐点类型化判断与路由已接入，包括超时、取消、低置信度回退及 Java 历史；选项审查/风险评分是本项目探针，不能算作 mu 的约束/风险规格复现，见 [JUDGING.md](JUDGING.md)。动态多问题约束/风险策略和分支任务框架已接入，输入截断、文件内容省略和权限模式差异见 [TASK_POLICIES.md](TASK_POLICIES.md)。语义输出准入与带来源的任务历史摘录已接入，可选模型选择只保留原文；与 mu 的生成式摘要、异步 shadow 和测试日志去重仍有差距，见 [SEMANTIC_CONTEXT.md](SEMANTIC_CONTEXT.md)。PowerShell 原生测试仍需要安装环境。
4. **最后：判断与会话增强。** Jev 的既有误判先解决；不能因为 mu 有更多决策点就直接放开自动审批。

mu 的 `write` 目前调用文件写入操作，本项目的同目录临时文件加原子替换是额外的本地可靠性措施，不应为追求逐行一致而退回普通覆盖写入。

## 第 12 阶段补充

已接入 `tool.admission` v3，按输出类型判断中间分块；默认关闭，错误/结果/未知块与首尾保留，超时不应用语义省略。原始输出归档与回读不变。历史省略时可保留带来源的任务摘录，默认无需模型；可选模型仅选择可验证的原文。共七个注册决策点，Laya 仍只支持 intent 的 shadow。Java Context/Task、重启与派生均已接入。此处没有把模型置信度、流程测试或小样本训练准确率当成真实场景质量证据。

## 第 13 阶段补充

新增默认关闭的实际判断输入采样、人工标注格式、纯策略回放与新模型 shadow 比较。
intent v2 导出按相关任务/仓库组隔离，连接完全重复输入所在的组，分开训练、验证、校准和测试。
训练器验证冻结清单，v2 的温度不再在验证集上拟合。此阶段补的是评测和数据准备流程，
没有新增决策点、独立真实场景标签或重训结果；现有 Laya 的 shadow 限制仍有效。
下一步先审核真实调用与输入缺口，再重训；见 [JUDGE_EVALUATION.md](JUDGE_EVALUATION.md)。
