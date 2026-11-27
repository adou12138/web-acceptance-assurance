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
| `ready_consumed_ratio` | 100 turns 内消费的 ready 项 / 开工时 ready 总数 | `exploration_state.summarize()["ready"]`（行号随版本变化，以函数返回为准，当前约 `exploration_state.py:603`）+ 执行台账 |
| `agent_steps_per_batch` | 推进一批所需 Agent 步数，基线 vs 改造后 | **未运行**：执行台账没有步数事件，不得写 0 冒充已测 |
| `blocked_by_capability_rate` | 因通道缺失记 BLOCKED 的项占比及 `recovery` 完整率 | `runtime-capabilities.json` + 探索清单 |
| `auto_approval_ratio` | 走 `readonly_lead` 自动续批的判定次数 / 自动续批判定总次数 | `budget-consumption.json` 的 `auto_judgements` 事件（每次判定都记，含 `auto=false` 与 reasons）+ `auto_approval`（经 `amendment_class.classify()` + `is_blocking()` 双层凭据校验）；分母必须有独立判定记录，不能只靠 `auto_approval` 分子；编排期判定（`plan-*`）只保留最近一次，反复重排不虚增分母 |
| `budget_stop_compliance` | 停机记录含「已消耗量 + 剩余义务 + 影响」三要素的批次占比 | `batch-plan.stop_conditions`（三条收敛：`queue_empty` / `increment_zero` / `budget_exhausted`；一条安全联锁：`side_effect_pending`）+ `loop-log.jsonl` 的 `stop` 记录 |

## 3. 硬要求

1. 样本 <10 时只报计数与配对差值，**不报百分比**；
2. 结果与 E1 检出指标**不合并分母、不互相外推**；
3. 负结果如实写「本轮未验证自主性改造提升覆盖」，不得改指标、换样本或挑好看的一次重跑；
4. 指标缺失（旧批次产物无该字段）写「未运行（旧批次）」，**不得写 0 冒充已测**——与 ab-protocol「`0/40` 与 `null/40` 含义相反」同口径；
5. 结论由人按本协议解读，工具输出只产事实与派生指标，不得自动推断"改造有效/无效"。

## 4. 执行前置

- G1 集成完成（D 批代码冻结、全量测试绿）后才可正式计分——代码未冻结出分无法归因到版本；
- 改造前基线组可用 v1.48.6 行为（无 `runtime_probe`/`exploration_runner`，手工编排）在同类样本上留痕执行台账后作为对照臂。

## 5. `--loop` 常态化决策判据（S6）

第一轮原型只验停机机制，不验检出提升。只有同时满足以下全部条件，才可讨论默认开启；缺一项即维持人工轮次：

1. 三条收敛停机（`queue_empty` / `increment_zero` / `budget_exhausted`）各至少触发一次，且 gate 状态不参与停机判定；
2. `loop-state.json` 的 `n_zero_rounds` 全程未变（中途改值应被拒绝）；
3. `loop-log.jsonl` 中不存在「未批准的 scope_change 被执行」；`unregistered_expansion` 的每条都在 phase amendments 有登记，或计数为 0——`amendment_required` 只列待补项，loop 不自动登记；`--plan` 必填且 `surfaces_missing=false`，否则本条不可满足；
4. 循环产物无 `severity`/`verdict`/`accepted`（`products_invalid=false`）；出现判定字段的轮次记 `requires_human=true`、不参与 `increment_zero` 计数，且下一轮 loop 会被代码拒绝推进，须人工核对后带 `--resume-after-human` 才可继续；
5. 预算记录完整（三口径无「未申报」缺口，或缺口已单列披露），且 loop 每轮已把消耗写回账本（`recorded_through_seq` 前进；停机判定基于写回前累计，耗尽至多滞后一轮）；
6. 每轮 `new_case_ids` / `new_surf_ids` / `method_counts` / `new_fail_count` 有披露——只有执行数、没有新增格与新增 FAIL，不能回答「覆盖是否扩大、发现了什么」。

检出对照不在本判据内：若要做「有界循环 vs 人工轮次」的检出比较，另立 M2 矩阵并单独授权预算；在此之前任何文档不得写「提升检出」。