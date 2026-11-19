---
name: case-generator
description: 砺衡专家包内 skill：测试用例生成。requirement-review 完成后调用；以 cases JSON 为唯一事实源，按模块分批生成合规用例（过 case_data.validate 与门禁），渲染 19 列 Excel / 交付 JSON / 五视角 CSV 视图，支持大需求分批与全量汇总。需求级用例不要改走 qa-req2testcase-generator（产物 schema 与砺衡执行台账不兼容）。
---

# case-generator（测试用例生成）

> 砺衡（web-acceptance-assurance）包内 skill。与 qa-req2testcase-generator **运行时完全独立**：本目录内容为一次性抽取副本，运行时不读取 qa 的任何路径、版本或配置。
> 判据源：`config/policy.json`（frozen 4.17.0）；结构权威源：专家包 `scripts/case_data.py`（`validate` 直接可调）。

## 职责边界

| 项 | 约定 |
|---|---|
| 何时调用 | `requirement-review` 完成，拿到 `review-result.json` 绝对路径，需要产出用例。**不含接口专项**（契约/action/接口清单线索 → api-test；cases 中 `layer=api` 的 TC 执行归接口执行，结果由主体会话回填主体台账）。检出维度全景见专家包 `references/bug-detection-map.md` |
| 输入 | `review-result.json` 绝对路径 + 需求正文 |
| 输出 | **`cases-<short>.json`**（唯一事实源）+ 门禁报告 + 按路由渲染的视图 |
| 不适用 | 不改冻结基线；不产出实测结论（只给需求时执行项一律 `NOT_RUN`）；阻塞项不生成确定断言；**不改写已交付视图**；不评分放行 |
| 写入 | 只写 `cases-<short>.json`、`batches.json`、门禁/汇总报告；视图一律由脚本渲染 |

## 数据形态（方案 A）

`cases-<short>.json`（`case_data.py` schema 1.0）是**唯一事实源**；19 列 Excel / 交付 JSON / 五视角 CSV / HTML 全部派生。用例正文只有一份（`testcases[]`），视图手工改后不得回流。字段规格：`references/design-spec.md`。

**覆盖义务先行**：新用例集必须写顶层 `coverage_obligations[]`（`id/layer/kind/target/source/required/tc_ids`），先按 AC 拆出元素、边界、条件组合、状态边与权限义务，再用真实用例绑定；`coverage_flags` 只是派生摘要。没有义务就只能靠用例自报覆盖，COV-LAYERS 直接判 BLOCK。历史资产须在 `meta.legacy_source` 登记 `schema_version/migrated_at/owner` 才降为告警。详见 `references/design-spec.md` §9.1/§10。

覆盖缺口单列在顶层 `coverage_gaps[]`（`gap_id/kind/target/reason/obligation`，可选 `severity/required/status/closed_by`；`case_data.validate` 硬校验，含占位词拦截）：绑不到场景的证据、功能点未覆盖、能力不支持、预算超限一律记缺口，**不得挂到无关场景凑数**。未关闭缺口会按类别压回对应覆盖层并让 COV-LAYERS 失败；缺口渲染进 19 列 Excel 的「覆盖缺口」附页（主表仍逐字 19 列）。

## 方法论参考（`references/methodology/`）

判据源，不是可选读物：写用例、判覆盖、解释口径时按需回读。

| 文档 | 用途 |
|---|---|
| `references/methodology/testcase_design_spec.md` | 用例设计规格与**四层覆盖深度判据（§5）**——`scripts/coverage_matrix.py` 的 L1–L4 口径即出自此节 |
| `references/methodology/design_methods.md` | 六种设计方法与见证要求（与 `design-spec.md` §4 同源） |
| `references/methodology/boundary_rules.md` | 边界取值与规则约束 |
| `references/methodology/api_test_standard.md` | 接口类用例口径（`layer=api` 的 TC 执行归接口链路，见 `skills/api-test`） |
| `references/methodology/detection_methods.md` | **执行期判定链**：oracle 六源、七类可判定信号、证据最小集、六态与"只能待核实"的四种情形 |
| `references/methodology/defect_patterns.md` | 缺陷模式库：11 类模式的最小触发/观察点/常见误判；盲测答案键从这里的模式取值 |
| `references/methodology/false_positive_guard.md` | 误报防线：四问判别 + 八类合法近似反例 + 缺陷/待确认/建议分账 |
| `references/methodology/evidence_grading.md` | 证据等级 E1 实测 / E2 模拟 / E3 未执行：与执行状态的映射、计数口径、表述对照、交付前五问 |
| `references/methodology/ai_feature_testing.md` | 被测含 AI 生成/对话时：四维度判据、八场景、可复现要求、上线门禁与定级 |

**接口类（layer=api）TC 的约束来源**：边界、枚举、默认值、禁用词等约束必须取自契约原文——问卷星/WJX/sojump 任务用 api-test 登记的知识库根路径（`kb_load_contract.py --action <ID>`，登记与纪律见 `skills/api-test/references/doc_review.md`「本机知识库根路径」节）取原文，并在用例依据中写出处；`distilled/` 蒸馏摘要只作索引、不作判定依据。**禁止凭记忆编造约束**；知识库不可用或无记录时，该约束对应用例改记缺口（不编造），由用户提供契约后补齐。

## 流程

### 1. 计划（分批判定）

```bash
python scripts/batch.py plan --review <review-result.json 绝对路径> --workdir <任务工作区> --task-id <任务号>
```

- 条数主判据：`sum(modules[].estimated_case_count) > 30` 分批；≤ 15 一律单批；模块数只决定"怎么切"。
- 产物：`batches.json`（状态机）+ 骨架 `cases-<short>.json` + 范围 `scope-<short>.json`。
- 模块码由评审阶段分配，本阶段只读不改；需求变更先跑 `fingerprint` 比对，不一致 → 全量重跑。

### 2. 逐批生成（单写者 = 主会话逐条手写）

```bash
python scripts/batch.py start  --workdir <工作区> --unit M01
# （主会话按 scope 的 FP 逐条手写 testcases[]，并入 cases-<short>.json）
python scripts/gate_checks.py --cases <cases JSON> --scope <scope JSON> --json
python scripts/batch.py finish --workdir <工作区> --unit M01 --cases <cases JSON> --gate pass --gate-report gate-M01.json
```

- **用例由主会话 LLM 逐条手写**：禁止脚本批量生成、禁止子代理派发、禁止把评审文本原样复制当用例。
- 每条用例必须给齐 `design-spec.md` §2 的 16 个必填字段与扩展字段；四要素、方法见证（`method_evidence.<方法>` 须有实际见证值，"只有方法名没有见证"不算覆盖）、断言三键 `{check, oracle, expect}` 一个不能少。
- 中断恢复：`batch.py resume` 检测 doing/done 但 `case_ids` 为空的批次并重置；从下一个 pending 批继续。
- 单批实际超 30 条：该批结束后按一级功能点续拆（`batch.py resplit`），不重跑已完成部分。

### 2.5 对抗交接消费（可选，进入对抗循环前执行）

评审侧可能随交付提供**对抗交接候选清单**（覆盖缺口与负面断言候选）。用 `review_bridge.py --adversarial-out <handoff 路径> [--negative-input <负面断言数组 JSON>] [--requirements-input <需求可疑点数组 JSON>]` 把候选映射为 ADV 草稿：coverage_gaps 逐条成草稿；负面断言缺副作用或授权说明会被拒绝（退出码 1）；需求可疑点（type/area/description）映射为 req_issue 判据草稿，产物归需求问题分账不冒充产品缺陷。草稿不是 plan：须按专家包 references 树的 `oracle-contract.md` 补 SURF/ORC、冻结来源指纹生成 `adversarial-plan.json` 后才可执行探针，生成阶段不冒充判据已冻结。

### 3. 门禁（只报不改）

`gate_checks.py` 依次执行：**S0 结构校验先跑**（`case_data.validate` 无 errors，含每个 requirement 至少一个 required 用例、`case_ids` 双向登记、四要素齐全、`coverage_gaps` 形态合法）→ S0 不过直接判**待修订**，不计算后续文本门禁 → G1/G1.5/G2/G3/G4/G5/G6/G7 + V05/V07/V08/V09/V11/V12/V13 + COV（L1/L3 方法见证，含 pairwise 证据要求）+ COV-LAYERS（L2/L4 覆盖矩阵，见 `scripts/coverage_matrix.py`）+ COV-PATH（异常与权限路径配比）+ COV-UI（界面样式/无障碍零用例须逐类裁定；未声明 applicability 的存量用例集降为告警并在报告中列缺口）+ RATIO-SMOKE / RATIO-P0。

- 门禁不通过 → 用例状态标**待修订**（与执行状态 `NOT_RUN` 区分），报告定位到 `case_id` 或批次号；修订后**重跑该批**。不自动改用例、不做超标自动降级。
- 比例门禁三档：① guidance 档——冒烟 `target_ratio` / P0 `recommended_ratio` 仅作生成期引导，不进任何判定；② WARN 档——批内浓度 `batch_warn_ratio` 超线只提示不降级，交人工判断；③ BLOCK 档——交付占比越界（冒烟 5%–20%、P0 ≤20%）与小样本绝对规则即门禁不通过。小样本（<20 条）比率无判别力，降为绝对数规则（每模块 ≥1 条冒烟、每需求 ≥1 条；P0 每模块 ≥1 条），比率越界仅提示。
- 两种失效方向都不得以删减必选覆盖义务来满足比例：**下限方向**（占比低于下限）须补充真实冒烟/高风险覆盖弥合缺口，不灌水凑数、不删减必选覆盖义务来"调平"；**上限方向**（占比高于上限）宁可 BLOCK 并披露，不删减高风险用例、不稀释优先级。
- P0 生成预算：`min(预估×15%, 20)`；超出写 `deferred` 并保留未覆盖义务，不静默丢弃。

### 4. 全量汇总

```bash
python scripts/batch.py aggregate --workdir <工作区> --cases <cases JSON>
```

全部批次 done 且无 gate=fail 才可汇总；跨批合并后重跑完整门禁（去重按合并全集、冒烟/P0 重算、`id` 唯一、`case_ids` 双向一致），产物 `aggregate-report.json`。

### 5. 输出路由（按用途判定，默认只交付一种）

| 场景 | 交付物 |
|---|---|
| 给人看（存档/评审/交付同事） | **19 列 Excel**：`render_xlsx.py --cases <JSON> --out 测试用例_{task_id}.xlsx --verify`（JSON 留在工作区作事实源，不单独交付） |
| 给程序/下游接着读 | **交付 JSON**：`render_json.py --cases <JSON> --out <路径>`（结构见 `references/output-json.md`） |
| 由本专家继续执行 | `render_csv.py --cases <JSON> --out <目录> --task-id <...> --verify` 生成五视角 CSV（视角映射复用 `exploration_state.VIEWS`；摘要分列 `view_rows` 与 `unique_cases`，跨视角重复行不作用例数），加可选 HTML + 执行台账（`execution_ledger.py` 直接吃同一份 cases JSON，无需转换） |
| 明确"两种都要" | 交付 JSON + 19 列 Excel 分别落 `outputs/json/` 与 `outputs/xlsx/`，文件名带时间戳；交付前核对两者 `case_id` 集合与条数一致 |
| 冒烟/核心集先跑 | `render_csv.py --only P0,P1 --verify`（精选视图；回读校验以筛选后集合为基准，不会因"比全集少"假失败） |
| 只要评审级测试点 | `render_testpoints.py --cases <JSON> --out test_points.csv --verify`：按「功能点 × 验收标准」折叠出 9 列测试点清单（覆盖层 / 设计方法 / 用例数 / 最高优先级 / 含冒烟 / 缺口随行）。**派生视图，不替代用例生成**，台账会拒收该 CSV |
| 判不准 | 问一句「给人看还是给程序接着用」，不猜 |

无论哪条路由 `cases-<short>.json` 都会生成——它是事实源，不算交付物，不参与互斥判定。

### 6. 与执行阶段衔接

台账（`execution_ledger.py`）直接吃本 skill 产出的同一份 cases JSON；台账只写 RUN/缺陷/证据，不改 `testcases[]`。**门禁未闭环（待修订）的用例不得进入执行**；专家最终结论仍按「通过/不通过/待核实」，但须披露该范围限制。

## 命令速查

```bash
# 评审映射 → 骨架 + 范围
python scripts/review_bridge.py --review <review-result.json> --out <骨架路径> --scope-out <范围路径> [--flow-kind simple]

# 分批
python scripts/batch.py plan --review <...> --workdir <...> --task-id <...>
python scripts/batch.py start / finish / status / resume / resplit / fingerprint / aggregate

# 门禁（只读）
python scripts/gate_checks.py --cases <JSON> [--scope <scope JSON>] [--json]

# 视图渲染
python scripts/render_xlsx.py --cases <JSON> --out <xlsx> --verify --task-id <...>
python scripts/render_json.py --cases <JSON> --out <json>
python scripts/render_csv.py --cases <JSON> --out <目录> --task-id <...> --verify [--only P0,P1]
python scripts/render_testpoints.py --cases <JSON> --out test_points.csv --verify

# 四层覆盖矩阵（COV-LAYERS 的同一份数据源）
python ../../scripts/coverage_matrix.py --cases <JSON> --out coverage-matrix.json --json
```

# 与执行阶段的机器交接（第 3 跳进门条件）

```bash
python <专家包>/scripts/execution_ledger.py init <workspace> <cases JSON> <context JSON> <round_id> \
  --batches <batches.json> --gate-report <gate 报告>
```

批次未全 done 或门禁 fail 时拒绝建台账；baseline 记 `gate_precondition`。

## 工具不可用时

会话没有脚本执行或文件写入能力时，按砺衡既有口径**报告阻塞、保留待执行项**，不以手工编造数据代替脚本输出。`openpyxl` 缺失时 `render_xlsx.py` 自动降级 Markdown 表格并标注（不自动安装）。

## 输入边界

需求正文以 `review-result.json` 为准；Word 原型截图、批注、修订痕迹可能读不到，关键规则须在评审阶段文字化；TAPD 连接器已授权时可按任务定向读取指定需求，任何情况下不做全空间拉取；PDF 需求先转文本。超大需求（预估 > 200 条）不在本 skill 能力内，如实说明边界。