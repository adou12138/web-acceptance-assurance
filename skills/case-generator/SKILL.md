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

### 3. 门禁（只报不改）

`gate_checks.py` 依次执行：**S0 结构校验先跑**（`case_data.validate` 无 errors，含每个 requirement 至少一个 required 用例、`case_ids` 双向登记、四要素齐全）→ S0 不过直接判**待修订**，不计算后续文本门禁 → G1/G1.5/G2/G3/G4/G5/G6/G7 + V05/V07/V08/V09/V11/V12/V13 + COV（四层覆盖/方法见证）+ COV-PATH（异常与权限路径配比）+ COV-UI（界面样式/无障碍零用例须逐类裁定；未声明 applicability 的存量用例集降为告警并在报告中列缺口）+ RATIO-SMOKE / RATIO-P0。

- 门禁不通过 → 用例状态标**待修订**（与执行状态 `NOT_RUN` 区分），报告定位到 `case_id` 或批次号；修订后**重跑该批**。不自动改用例、不做超标自动降级。
- 小样本例外：用例数 < 20 时比率门禁降为告警，改用绝对数（每模块 ≥1 条冒烟、每需求 ≥1 条；P0 同理）。
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
python scripts/render_csv.py --cases <JSON> --out <目录> --task-id <...> --verify
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