# mu 与 mu-pyjava 对照

对照时间：2026-09-27，更新于 2026-09-28。mu 官方 `main`：[`2dfd59c`](https://github.com/qybaihe/mu/tree/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6)；本项目对照基线：`241a363`，本轮增加多处编辑以及读取/列目录操作接口。这里比较行为与架构，不以工具名称数量衡量完成度。

| 领域 | mu 当前实现 | mu-pyjava 当前实现 | 下一步 |
| --- | --- | --- | --- |
| 核心工具 | [8 类工具](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/tools/index.ts)：read、bash、powershell、edit、write、grep、find、ls；各有定义、展示器和扩展入口 | 10 个固定工具名；文本读写搜索、无 shell 命令、Git 检查和命令输出回读 | 将工具定义、执行、结果展示分层，并开放注册入口 |
| 操作接口 | [read](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/tools/read.ts)、[find](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/tools/find.ts)、[ls](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/tools/ls.ts)、[edit](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/tools/edit.ts) 与 [bash](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/tools/bash.ts) 分别声明可替换操作；部分搜索路径仍直接调用本机工具 | 读取、列目录、写入/编辑可注入操作；虚拟工作区已验证；搜索、Git 和命令仍绑定本机 | 继续 #4：抽出搜索与命令接口，完成替代操作的整段工作流 |
| 编辑语义 | [一次编辑多个互不重叠区段](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/tools/edit-diff.ts)；按原文匹配，保留 BOM/换行，另有模糊匹配回退及 diff/patch/首行元数据 | 本轮支持多区段原文匹配、重叠校验、BOM/CRLF 保留；审批前 diff，原子替换和变更事件 | 模糊匹配与标准 patch/导航元数据尚缺；需谨慎验证模糊匹配不会改错位置 |
| 读取与搜索 | 文本分页、图片附件与模型兼容提示；find/grep/ls 有结构化截断信息 | 文本分页、glob/grep/ls 限额和结构化 grep 结果；没有图片内容块 | 先完成操作接口，再补图片与统一截断元数据 |
| 命令 | bash 与 PowerShell 共用流式命令机制，支持取消、超时、输出归档和操作注入 | 无 shell 的进程运行，支持输出流、停止、超时、归档及回读；暂无 PowerShell | 抽出命令执行接口，再实现受权限门控的 shell/PowerShell |
| 权限 | [三种会话权限模式](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/kyrn-judge/src/extension/features/permissions.ts)、风险规则、会话授权与 Jev 判定 | 桌面逐次审批、同文件会话授权、受保护路径强制再问；`tool.intent` 只能否决，Laya 保持 shadow | 丰富风险规则和权限模式；独立校准后才考虑让 Jev 执行审批 |
| 会话与判断 | [树形 JSONL 会话](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/coding-agent/src/core/session-manager.ts) 支持分支/派生；[决策规格](https://github.com/qybaihe/mu/blob/2dfd59ca9cc71d45b289b0d91cc95254de73f3b6/packages/kyrn-judge/src/decision.ts) 支持 boolean/choice/score、逐点模式与路由 | 线性持久会话，可恢复已完成轮次；一个 boolean 决策点和全局模式 | 工具层稳定后再做分支会话、上下文预算和类型化判断 |

## 排序依据

1. **已完成本轮：多处编辑。** 它直接减少重复工具调用，仍沿用现有审批、版本复查和原子提交。
2. **正在推进：操作接口。** 读/列目录已抽出，接着是搜索与命令；保持当前工具参数和 Java 协议稳定。mu 的各工具接口提供了结构参考，但不能把“有接口”误记为已经有完整远程工作区。当前替代接口测试使用内存数据，没有连接 SSH。
3. **随后：结果与扩展。** 明确内容块、结构化 details、截断/续读协议，再开放自定义工具。图片和 PowerShell 需要相应的模型及平台支持。
4. **最后：判断与会话增强。** Jev 的既有误判先解决；不能因为 mu 有更多决策点就直接放开自动审批。

mu 的 `write` 目前调用文件写入操作，本项目的同目录临时文件加原子替换是额外的本地可靠性措施，不应为追求逐行一致而退回普通覆盖写入。
