# E2 自主性对照协议

> 状态：**已就绪待执行**。本协议定义自主性改造（v1.49.0 D 批：`runtime_probe.py` 能力探测、`exploration_runner.py` 批次编排、交接包 `runtime_diagnostics`）的效果对照口径。
> 结果写入 `autonomy-result.md`（实跑后生成）。本协议**不新增 `blind_eval.py` 子命令**（该脚本九个子命令已冻结），只用既有产物派生指标。

## 1. 对照设计

- **自变量**：批次推进方式——「改造前手工编排」基线 vs「改造后 `exploration_runner.py` 编排」。
- **控制变量**：同一份脱敏需求样本、同一环境、同一授权与预算（与 `run-matrix.json rules.pairing` 同口径）。
- **执行方分臂与交错**：≥2 名执行方，交错顺序执行，执行方不得看到另一组的中间输出。
- **与 E1 的关系**：独立一组，**不占 20 run**，不进检出主指标分母；两组成果分别写入 README 有效性状态页，不合并分母、不互相外推。
- `comparable=false`（样本/环境/预算不一致）时不强行对比，只并排呈现计数。

## 2. 五指标口径与数据源（全部来自本版已有产物，不新造采集）

| 指标 | 口径 | 数据源 |
|---|---|---|
| `ready_consumed_ratio` | 100 turns 内消费的 ready 项 / 开工时 ready 总数 | `exploration_state.summarize()["ready"]`（`exploration_state.py:449`）+ 执行台账 |
| `agent_steps_per_batch` | 推进一批所需 Agent 步数，基线 vs 改造后 | 执行台账 step 记录 |
| `blocked_by_capability_rate` | 因通道缺失记 BLOCKED 的项占比及 `recovery` 完整率 | `runtime-capabilities.json` + 探索清单 |
| `auto_approval_ratio` | 走 `readonly_lead` 自动续批的批次 / 总批次 | `budget-consumption.json` 的 `auto_approval` 事件（经 `amendment_class.classify()` + `is_blocking()` 双层凭据校验） |
| `budget_stop_compliance` | 停机记录含「已消耗量 + 剩余义务 + 影响」三要素的批次占比 | `batch-plan.stop_conditions` + 停机记录 |

## 3. 硬要求

1. 样本 <10 时只报计数与配对差值，**不报百分比**；
2. 结果与 E1 检出指标**不合并分母、不互相外推**；
3. 负结果如实写「本轮未验证自主性改造提升覆盖」，不得改指标、换样本或挑好看的一次重跑；
4. 指标缺失（旧批次产物无该字段）写「未运行（旧批次）」，**不得写 0 冒充已测**——与 ab-protocol「`0/40` 与 `null/40` 含义相反」同口径；
5. 结论由人按本协议解读，工具输出只产事实与派生指标，不得自动推断"改造有效/无效"。

## 4. 执行前置

- G1 集成完成（D 批代码冻结、全量测试绿）后才可正式计分——代码未冻结出分无法归因到版本；
- 改造前基线组可用 v1.48.6 行为（无 `runtime_probe`/`exploration_runner`，手工编排）在同类样本上留痕执行台账后作为对照臂。