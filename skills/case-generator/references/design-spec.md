# 用例设计规格（19 列视图 + cases JSON 唯一事实源）

> 数据形态：方案 A。`cases-<short>.json`（schema 1.0，与专家包 `scripts/case_data.py` 契约一致）是**唯一事实源**；19 列 Excel / 交付 JSON / 五视角 CSV / HTML 全部是渲染视图。任何视图手工改后不得回流。
> 枚举权威源：`case_data.py` 硬编码常量；本文件与 `config/policy.json` 只存副本（`source_version: 4.17.0`，`frozen_at: 2026-11-16`）。冲突时以校验器为准。

## 1. 19 列定义与取值来源

列名与顺序逐字对齐抽取源 `export_excel.py:COLUMNS`（qa-req2testcase-generator 4.17.0）。渲染脚本不得引入额外判定。

| # | 列名 | JSON 取值来源 | 必填 | 合格判据 |
|---|---|---|---|---|
| 1 | 项目 | `testcases[].project`（扩展字段） | 是 | 需求/项目短名，非空 |
| 2 | 类型 | `testcases[].layer` | 是 | `ui/api/component/integration/e2e` 之一 |
| 3 | 用例编号 | `testcases[].id` | 是 | `^TC-[A-Z0-9]{2,8}-\d{3}$`，跨批全局唯一 |
| 4 | 需求 | **也取 `testcases[].project` 的值**（抽取源即如此，勿按列名直取） | 是 | 与第 1 列同值 |
| 5 | 优先级 | `testcases[].priority` | 是 | 仅 `P0/P1/P2`（qa 原 P3 已并入 P2） |
| 6 | 标题 | `testcases[].title` | 是 | 具体可辨认的操作目标，非空话 |
| 7 | 用例菜单 | `testcases[].menu_path`（扩展字段） | 是 | 菜单/页面路径，建议 `A→B→C` 格式 |
| 8 | 预置条件 | `testcases[].preconditions[]` 逐条换行合并 | 是 | 覆盖环境、账号、数据三要素 |
| 9 | 步骤 | `testcases[].steps[]` 合并为「序号. action → expected」 | 是 | 每步原子、具体到按钮/输入值 |
| 10 | 期望结果 | `testcases[].assertions[].expect` 合并 | 是 | **断言拆分只存在 JSON 中，Excel 是折叠视图** |
| 11 | 是否冒烟用例 | `testcases[].suites` 含 `smoke` 即为「是」 | 是 | 只输出「是/否」 |
| 12 | 创建者 | 固定 `AI` | 是 | 恒为 `AI` |
| 13 | 经办人 | `testcases[].assignee`（扩展字段） | 否 | 缺省留空 |
| 14 | 用例类型 | `testcases[].attributes[]` 逗号连接 | 是 | 校验器七类：functional/security/performance/accessibility/compatibility/visual/reliability |
| 15 | 测试类别 | `testcases[].suites[]` 逗号连接 | 是 | feature/smoke/regression/exploratory/e2e |
| 16 | 执行结果 | 生成阶段**留空**（执行事实在台账，不回写视图） | 否 | 恒空 |
| 17 | 截图 | 生成阶段**留空** | 否 | 恒空 |
| 18 | 测试用例集 | `testcases[].test_suite`（扩展字段） | 否 | 缺省留空 |
| 19 | 备注 | `testcases[].remarks`（扩展字段） | 否 | 不得承载门禁结论或未覆盖义务 |

文件名：`测试用例_{task_id}.xlsx`；sheet 名：`测试用例`；CSV 派生一律 `UTF-8-sig`、逗号分隔。

## 2. cases JSON 用例必填字段（case_data 硬校验）

缺任一项 `case_data.validate` 直接报 error，后续文本门禁不再计算（S0 先跑）。

```
id / title / feature_ids / acceptance_ids / layer / attributes / suites /
classification_reason / priority / required / role / preconditions / data /
steps / assertions / dependency_mode
```

条件必填：`flow_kind ∈ {workflow, stateful}` 时还必须给 `scenario_id + path_id`，且其 feature 必须属于该场景。

扩展字段（校验器对用例对象不拒未知键，直接写进 `testcases[]`，无需伴随元数据）：
`project / menu_path / case_class / smoke_source / method_evidence / coverage_flags / risk_flag / pci_flag / remarks / screenshot / test_suite / assignee`。

## 3. 四要素（acceptance.elements）

| 键 | 含义 | 合格判据 |
|---|---|---|
| scene | 场景 | 定位到具体页面或业务域 |
| object | 对象 | 定位到具体字段、功能或元素 |
| condition | 条件 | 前置、边界、角色或状态 |
| focus | 关注点 | 要验证的可观察行为 |

四键缺任意一个、为空话（整句等于「正常/成功/符合预期/无异常/功能可用」等）或含占位（TODO/TBD/待补充…）即校验失败。AC 的 description/oracle 与用例 assertions.check/oracle 同样禁止空话与占位。

## 4. 方法见证（六种设计方法）

只有方法名没有见证，不计入方法覆盖（G 系列与四层覆盖共用此判据）。

| 方法 | 见证类型 | 见证要求 |
|---|---|---|
| scenario 场景法 | scenario_path | 用例对应一条完整业务路径（主/分支/异常），有来源场景 |
| boundary 边界分析 | boundary_sets | 给出具体边界取值集合（最小、最大、越界），不能只写「边界条件」 |
| equivalence 等价类 | equivalence_classes | 给出有效类与无效类的具体取值或取值规则 |
| state_transition 状态迁移 | state_edges | 给出源状态、目标状态与触发条件的实际边 |
| decision_table 判定表 | decision_table | 给出条件项与动作项对应关系，覆盖条件组合 |
| pairwise Pairwise | pair_witness | 给出参数值对覆盖见证：参数、值域、组合与已覆盖参数对数量 |

**枚举陷阱（qa→本包映射）**：qa 的 `state_transition` 不是质量属性 → 写 `attributes=[functional]` + `method_evidence.state_transition`；qa 的 `api` 是测试层级 → 写 `layer=api` + `attributes=[functional]`。

## 5. 冒烟清单与三层比率

可标冒烟：页面入口能否正常打开、核心数据能否加载展示、核心操作能否完成（提交/查询/导出）。
禁标冒烟：异常场景（权限不足/数据为空/超时）、删除/禁用操作、次要字段验证、UI 细节核对。

| 比率 | 值 | 角色 |
|---|---|---|
| `target_ratio` | 10–15% | guidance，生成期引导，不计入门禁 |
| `batch_warn_ratio` | ≤30% | warn_only，超限只提示，不自动取消冒烟标记 |
| `delivery_ratio` | 5%~20% | gate，导出与全量汇总判定 |

小样本例外：用例数 < 20 时 `delivery_ratio` 与 P0 上限降为告警，改用绝对数（每模块 ≥1 条冒烟、每需求 ≥1 条）。P0：交付上限 20%（gate）、推荐 10–15%（guidance）、预算裁剪 `min(预估×15%, 20)`、批内 ≤35% 仅告警。去重：拒绝线 50%、告警线 15%，Jaccard 按 `title/steps/assertions.expect` 计算，**跨批按合并全集重算**。

## 6. 枚举定稿

- `priority`：P0 / P1 / P2
- `attributes`（七类）：functional / security / performance / accessibility / compatibility / visual / reliability
- `layer`（五类）：ui / api / component / integration / e2e
- `suites`（五类）：feature / smoke / regression / exploratory / e2e
- `dependency_mode`：real / sandbox / mock / replay

## 7. ID 规则

| 实体 | 模式 | 分配方 |
|---|---|---|
| 模块 | `^M\d{2}$`（M01） | **评审阶段分配，一经确定不得重排**；需求变更只追加不重编号 |
| 需求 | `^REQ-\d{2,}$` | 评审 |
| 功能点 | `^FP-\d{2,}$` | 评审 |
| 验收标准 | `^AC-\d{2,}$` | 评审 |
| 场景/路径 | `^SCN-\d{2,}$` / `^PATH-\d{2,}$` | 评审或生成 |
| 用例 | `^TC-[A-Z0-9]{2,8}-\d{3}$`（TC-M01-001） | 生成，跨批全局唯一，汇总校验重复 |

## 8. 双向一致性与视图门禁

- `required: true` 的用例必须以自身 `id` 出现在对应 `acceptance[].case_ids`；AC 的 `requirement_id` 必须属于该用例 feature 的 `requirement_ids`。手工补数据最先断在这里。
- 渲染出的 19 列 `case_id` 集合必须与 cases JSON **完全相等**，不一致即视图作废重渲（防手工改后回流）。

## 9. 覆盖矩阵与覆盖缺口

**四层覆盖**（`scripts/coverage_matrix.py`，判据见 `references/methodology/testcase_design_spec.md` §5 与 `config/policy.json` 的 `structure.coverage_layers`）：

| 层 | 机械判据 | 判在 |
|---|---|---|
| L1 主流程 | 每个功能点至少一条 `coverage_flags` 含 `L1` 的用例 | COV（S0 保证结构） |
| L2 业务分层 | `applicability.functional.state == applicable` 时，每个功能点至少一条含 `L2` 的用例；非 applicable 记 `not_required` 并回写状态与理由 | COV-LAYERS |
| L3 设计方法 | `method_evidence` 中有见证的方法数 ≥ `min_witnessed_methods`（默认 2），且不得有"只有名字"的方法 | COV |
| L4 探索性 | `suites` 含 `exploratory` 的用例数与占比，只统计不计入已覆盖 | COV-LAYERS |

- 矩阵产物 `coverage-matrix.json` 由 `coverage_matrix.py` 生成；`gate_checks.py` 把同一份矩阵嵌进报告 `coverage_matrix` 字段，**报告数字与脚本输出必须同源**。
- `missing_layers` 非空、义务未绑定、或已登记缺口未关闭时：**新用例集**（已声明 `applicability`）→ COV-LAYERS 判 BLOCK；未声明 `applicability` → 降 WARN 并列入覆盖缺口。**不允许**用"没有 `coverage_flags`"推断存量——历史资产必须按 §10 显式登记后才降为 WARN。

### 9.1 覆盖义务 `coverage_obligations[]`（新用例集必填）

覆盖不能由用例自己声明。生成前先把需求拆成义务，再让每条义务绑定真实用例；`coverage_flags` 只是义务满足后的派生摘要。

| 键 | 要求 |
|---|---|
| `id` | 批内唯一稳定 ID（如 `OBL-01`），报告与缺口按它定位 |
| `layer` | `L1`（正向主流程）或 `L2`（元素/边界/组合）；L3 方法见证、L4 探索预算不属义务 |
| `kind` | `path` / `field_boundary` / `element` / `combination` / `state_edge` / `permission` / `data` / `copy` / `compatibility` / `security` |
| `target` | 落到元素、字段边界、状态边或条件组合，不得写"待补充"等占位 |
| `source` | 引用存在的 `REQ`/`FP`/`AC`，标明义务来自哪条规则 |
| `required` | 严格布尔；`true` 时未绑定任何有效用例即判该层未覆盖 |
| `tc_ids` | 实际覆盖该义务的用例 ID 数组；引用的用例必须存在，且 `coverage_flags` 声明同一层 |

判定口径：L1/L2 以"每条必选义务都有有效 `tc_ids`、且标记口径无未覆盖功能点"为准；删标记或删义务都不能提高覆盖——两者不一致时 `case_data` 在 S0 直接报错。

## 10. 历史资产登记（`meta.legacy_source`）

v1.26.0 之前生成的 cases 没有覆盖义务。这类资产必须显式登记来源，才能按存量处理：

| 键 | 要求 |
|---|---|
| `schema_version` | 生成时的 cases schema 版本 |
| `migrated_at` | `YYYY-MM-DD`，登记日期 |
| `owner` | 负责迁移的人或角色 |

登记后 COV-LAYERS 降为 WARN 并在交付摘要列出"未迁移义务"；未登记的新旧不明资产一律 BLOCK。登记不等于免检，下一轮补义务后应删除该字段。

**覆盖缺口 `coverage_gaps[]`**（顶层可选数组，`case_data.validate` 硬校验）：

| 键 | 要求 |
|---|---|
| `gap_id` | `^GAP-\d{2,}$`，批内唯一 |
| `kind` | `scene_unbound` / `element_uncovered` / `capability_unsupported` / `method_unwitnessed` / `budget_exceeded` / `data_unavailable` |
| `target` | 缺口指向的功能点、元素、场景或方法，可定位 |
| `reason` | 为什么补不上（缺材料/缺环境/缺实现），不得写"待补充"等占位 |
| `obligation` | 补齐动作或责任方，含占位词即校验失败 |
| `severity` / `required` | 可选；`required=true` 的缺口参与覆盖判定，不得只在附页出现 |
| `status` | 可选：`open` / `in_progress` / `closed`（缺省 open） |
| `closed_by` | 关闭证据对象（补测用例 ID、复测记录或产品确认）；有它才算关闭 |

缺口进 19 列 Excel 的「覆盖缺口」附页与门禁报告，**不因主表齐全而消失**；未关闭缺口按类别压回对应层（`element_uncovered`→L2、`feature_uncovered`→L1、`method_unwitnessed`→L3），矩阵与 COV-LAYERS 同时报出，`obligation` 未闭环的必须在交付摘要中列为未覆盖义务。