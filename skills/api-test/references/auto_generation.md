# 契约驱动的自动用例生成（知识 → 用例）

## 为什么需要

skill 的知识（31 维度框架、21 条缺陷模式、适用性矩阵、断言 helper）原本**全部以文档形式存在**，
每次都要靠人/agent 手工「读文档 → 设计用例 → 手写 Python `main()`」。
覆盖率因此取决于当次注意力——这正是"做少了却静默产出完整报告"的结构性根源。

本模块把文档知识变成**可执行规则**，让「契约 → 用例」自动化：

```
契约 → 字段语义推断 → 适用性自动判定 → 缺陷模式匹配 → 字段级变异 → 声明式用例 → 执行 + 门禁
```

## 模块与职责

| 模块 | 职责 |
|---|---|
| `contract_parser.py` | 解析 ShowDoc Markdown / OpenAPI / JSON 契约 → 统一 Contract；从备注抽约束（最长 N 字符、默认值、枚举、禁用词） |
| `field_semantics.py` | 字段名+类型+备注 → 语义标签（time/url/id/status/enum/page/size/amount/html/count/i18n…）→ 建议断言 helper |
| `applicability.py` | 自动判业务域/操作类型/是否国际版 → 各维度必测/选测/不适用 → **自动产出 COVERAGE_PLAN 与 N/A 证明** |
| `pattern_engine.py` | 缺陷模式清单 21 条中的 19 条编码为 `match(contract)` + `gen(contract)` 规则，自动匹配并生成对应用例；另 2 条「内容安全」与「组合非法」分别由安全载荷清单和组合生成器覆盖；引擎另含 UI1 / AA 两条清单外专项 |
| `case_signature.py` | 用例签名：`(维度, 参数, 期望)` 规范化后哈希，**判等而非打分**——判重、折叠、同请求不同期望的矛盾检测都走它（`case_generator` 与 `business_scenarios` 共用的单一来源） |
| `case_generator.py` | 编排以上 + 字段级变异（必填缺失/类型错/枚举越界/长度边界/内容注入）→ 声明式用例 JSON；默认写入共享资产归档；产物带 `duplicate_report` 与 `expectation_conflicts`，`--dedup` 才真折叠 |
| `case_runner.py` | 执行声明式用例，复用 `api_test_runner` 全部断言 helper，并接通覆盖率门禁（低层入口不自动负责资产归档） |
| `flow.py` | 一条命令打通生成/计划/执行/回归闭环；`--cases` 执行已保存用例，不重新生成 |
| `asset_runtime.py` | 统一运行目录、canonical cases hash、run manifest、执行结果与快照/审批边界 |
| `multi_interface.py` | 按 acceptance manifest 全量预检后逐接口计划或显式执行，并生成汇总 CSV |
| `report_enhance.py` | 台账聚类去重 + curl 复现 + 按维度修复建议 |
| `pattern_feedback.py` | 缺陷回流成模式候选（写 `references/pattern_candidates.log.md` 待评审池，运行时数据不入库；纪律见 `references/pattern_candidates.md`） |
| `pending_confirmation.py` | 把 `NEEDS_CONFIRMATION` 归因按维度聚成待确认清单（阻塞条数 + 解除条件 + impact）；这批不进缺陷台账，不单独落盘就会整体消失 |

## 用法

```bash
# 一条命令：默认归档到包外资产目录；--execute 才真实执行
python scripts/flow.py --contract <契约路径> --execute

# 从知识库按 action 取契约
python scripts/flow.py --action 1000107 --execute

# 只生成不执行（先审阅再跑；状态为 planned）
python scripts/flow.py --contract <路径>
python scripts/flow.py --contract <路径> --plan-only --profile plan

# 审阅后执行已保存用例：不重新生成，源文件不变
python scripts/flow.py --cases <已审定用例.json> --execute --out-dir <新空目录>

# 分步；--out 只能写入不存在的新文件
python scripts/case_generator.py --contract <路径> --out <新文件.json>
python scripts/case_runner.py --cases cases.json --dry-run     # 低层执行器仅列计划
python scripts/case_runner.py --cases cases.json --only B,H    # 只跑指定维度

# 显式批准正式用例（默认另存正式 suites，不改源）
python scripts/flow.py --approve-cases <src.json> --approve-by <人> [--suite-out <target.json>]

# 回归快照需显式晋升正式基线
python scripts/flow.py --promote-baseline <snapshot.json> <target.json> --approve-by <人>

# 多接口：全量预检 hash 后逐项执行；默认 plan，--execute 才真实请求
python scripts/multi_interface.py --manifest acceptance.json --out-dir batch [--execute]

# 配套接口：显式指定开启真往返 + 越权读面 + 交叉验证
python scripts/case_generator.py --contract <路径> --read-action <读接口action> \
    --read-params '{"id": 123}' --list-action <列表接口action>
# 关掉同级目录扫描自动推断配套接口（只用显式声明 / 命令行指定）
python scripts/case_generator.py --contract <路径> --no-auto-companion

# 仅改报告命名；不隔离全量产物，不能替代新的运行目录
python scripts/flow.py --contract <路径> --execute --out-prefix chain_
python scripts/case_runner.py --cases cases.json --out-prefix chain_
```

## 参数速查

| 参数 | 归属 | 说明 |
|---|---|---|
| `--out-prefix` | `flow.py` / `case_runner.py` | 仅改变报告文件名前缀（默认 `api_test`）。它不隔离 generated_cases、manifest、快照等全量产物，也不防止同目录资产覆盖；应使用新的 `--out-dir`/运行目录。台账与报告仍按前缀配对 |
| `--read-action` | `case_generator.py` | 配套读接口 action：给了才能做真往返（读→原样回写→再回读）与越权读面（A03/A07） |
| `--read-params` | `case_generator.py` | 读接口所需参数，JSON 字符串（如 `'{"id": 123}'`）；带资源标识才能构造 A07 横向越权读 |
| `--list-action` | `case_generator.py` | 配套列表接口 action：用于「创建后验列表出现 / 计数 +1」交叉验证 |
| `--no-auto-companion` | `case_generator.py` | 关闭同级目录扫描自动推断配套接口（只用契约显式声明 / 命令行指定） |
| `--dedup` | `case_generator.py` | 折叠「同维度+同参数+同期望」的重复用例；**默认只统计不折叠**，因为折叠会改变用例总数、连带改动历史基线的覆盖率口径 |
| `--out` | `case_generator.py` | 显式输出到新文件；文件已存在即拒绝覆盖；省略时归档到共享运行目录 |
| `--cases` | `flow.py` | 读取已保存用例并执行/计划；与 `--contract`/`--action` 互斥，不重新生成 |
| `--out-dir` / `--out` | `flow.py` | 显式新建或空输出目录；非空目录拒绝覆盖；省略时按项目/接口/版本/唯一批次归档 |
| `--approve-cases` / `--approve-by` / `--suite-out` | `flow.py` | 显式批准正式套件；批准人必填，默认独立归档到正式 suites，`--suite-out` 可指定另存路径，禁止覆盖源文件，拒绝 TODO/人工待补/禁用/凭据 |
| `--promote-baseline SNAPSHOT TARGET` | `flow.py` | 将未批准 snapshot 显式晋升为正式基线；需 `--approve-by`，只接受逐条实际执行且全 PASS、无覆盖缺口的快照 |
| `--manifest` / `--execute` | `multi_interface.py` | 按 acceptance manifest 全量预检后逐接口计划或执行；默认计划，`--execute` 才发请求 |
| `--out-dir` / `--out` | `multi_interface.py` | 新空批次目录与 UTF-8-sig 汇总 CSV；每项使用独立子目录，禁止覆盖 |

资产默认根目录由 `WJX_API_TEST_ASSET_ROOT` 覆盖；默认结构为
`<cwd>/api-tests/<project>/runs/<interface>/<可选version>/<unique>/`。`flow.py` 的
`--out-prefix` 仅是报告文件名前缀，不是产物隔离措施；`case_runner.py`/`smoke_runner.py` 等低层入口不自动归档。

## 运行资产、快照与状态

统一运行时 `scripts/asset_runtime.py` 负责安全路径、唯一运行目录、canonical JSON hash 和 manifest。
接口标识优先 `action`；REST 无 action 时按 `METHOD + path`（含稳定 hash）区分接口。每次真实 flow
运行在本轮目录生成 `executed_cases.json`，并把结果计数、逐条结果和产物路径写入 `run_manifest.json`。
`planned` 只代表计划/生成/dry-run，不能当作 `completed`。`completed` 表示本轮声明范围完整跑完且全通过；
`partial` 表示 `max_cases` 截断或 `smoke` 等档位收窄，执行干净但不构成全量验收（多接口汇总同样不把它算通过）；
`failed` 表示失败、覆盖缺口、回读错误或执行不完整。`--only` 收窄覆盖率分母，被排除维度记录在 `coverage_excluded`。

执行结束生成 `regression_baseline.json`，其中 `snapshot: true` 表示未批准快照；`--retest` 的基线必须
位于本轮输出目录外。正式化必须由 `--promote-baseline <snapshot> <target> --approve-by <人>` 完成，且要求
快照中的每条用例实际执行、全部 PASS、无覆盖缺口；源快照不原地改写。entry 的 `case_hash` 绑定完整用例，历史 cases 可推导并核对摘要；当前内容变化为 `STALE_BASELINE`，无历史完整内容证明的旧基线在统一 flow 中为 `UNBASELINED`，不得假判 FIXED。

多接口 `acceptance.json` 使用 `entries`：`cases` 为相对 manifest 文件路径，`cases_hash` 为去掉 approval
后 canonical JSON 的 SHA-256，`config`/`baseline`/`only` 可选。工具先全量预检路径、JSON、hash 和重复引用，
任意错误均不执行任何 entry；默认仅计划，只有 `--execute` 才真实执行。汇总 CSV 用 UTF-8-sig，状态来自
各子目录 `run_manifest.json`，`notRun` 不等于通过。预检缓存已验证字节，首个请求前整体冻结到 `inputs/entry-###/{cases,config,baseline}.json` 并复核摘要；冻结失败零请求且留整批 notRun CSV，子项准备异常不丢此前结果。`mode` 支持 `acceptance` 与 `regression`。

```json
{
  "mode": "acceptance",
  "entries": [
    {"cases": "../suites/interface/cases.json", "cases_hash": "替换为该用例文档的canonical哈希", "config": "../exec.json"}
  ]
}
```

正式用例审批输出中的 `approval.cases_hash` 可直接填入清单；也可调用
`asset_runtime.cases_hash(json.load(...))` 计算。原文件格式或缩进变化不影响 canonical 哈希。
晋升基线必须保留源运行目录，核验 `source_run/run_manifest.json` 为 completed、无 findings，
且快照哈希、用例编号和逐条实际 PASS 结果一致。

## 覆盖率门禁如何被激活

`applicability.py` 自动推导必测维度 → 写入用例文件 `coverage_plan` →
`case_runner.py` 自动装进 `api_test_runner.COVERAGE_PLAN` → 收尾 `coverage_gate()` 比对
「声明必测维度」vs「实际执行触及维度」，缺口即记【缺陷(覆盖率缺口)】入台账。

缺口在两个粒度产生，缺一不可：

| 粒度 | 产生点 | 覆盖的情形 |
|---|---|---|
| 逐条 | `case_runner._skip_uncovered()` | 用例已生成却未执行（`needs=manual`、检查命中 `MANUAL_CHECKS`、无基线可比的 AB 项） |
| 维度 | `api_test_runner.coverage_gate()` | 整个必测维度没有任何 `PASS`/`FAIL`/`PRECONDITION_UNMET` 用例 |

维度级只回答"这个维度有没有人跑过"：同维度只要有一条用例跑过，它就判"已覆盖"，
其余被跳过的检查全部静默——**局部漏跑只能靠逐条级留痕**。

设 `WJX_STRICT_COVERAGE=1` 可把缺口升级为 `exit(2)` 硬拦截。

> **只锁必测**：`coverage_plan` 只含必测维度；选测维度放 `optional_plan`（建议，不进硬门禁），
> 否则选测项没用例会刷出一堆假警报。

## 已知边界（诚实声明）

- 契约解析依赖文档规范：表格须含「参数/类型/说明/必需/备注」列；示例须为 ```json 块。
  解析不出来时会退化（字段为空），此时应改用声明式手写用例或人工补契约。
- 多数 `check` 已实现为可自动执行：`http_semantics` / `roundtrip` / `pagination` / `uniqueness` /
  `terminal_repeat` / `ui_parity` / `transport_contract` / `enum_exhaustive` / `concurrency` /
  `rate_limit`。后四者所需输入均已具备——响应头由 `call()` 旁路记录（`LAST_RESPONSE_HEADERS` /
  `call_meta()`）、枚举全集由契约解析随用例下发、并发用线程池、限流连发观测。
  发请求类检查的次数受硬上限约束（`CONCURRENCY_MAX_TIMES` / `RATE_LIMIT_MAX_TIMES`），
  防配置写错打爆服务。
- 留在 `MANUAL_CHECKS` 的只有三类**确实拿不到输入或观测通道**的检查：
  `event_delivery`（推送日志在接口响应之外）、`timezone`（需页面时间显示值对照）、
  `batch_partial`（单条接口不命中，属条件 N/A）。执行时跳过，但**跳过前先记 `COV-*` 覆盖缺口入台账**
  ——结果行上的 `SKIPPED` 不进台账，只有台账会被交付与复盘看到。
- 生成用例是**起点不是终点**：P0 用例可直接跑，仍标 `needs=manual` 的项须由测试人员补齐后再执行。
