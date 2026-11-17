# 命令速查与产物词表

路径判定与红线在 `../SKILL.md`；七步编排见 `playbooks/seven_step.md`、全部命令与开关在本文件与各编排卡；本文件装**全部命令、全部开关、产物与状态词表**，动手时按需查。

配套：机制编号 M1–M13 的判定口径见 `mechanisms.md`；用例执行率双口径见 `case-coverage.md`；台账列定义见 `issue_format.md`；字段与维度见 `framework.md`。

## 〇、退出码总表（1–5）

排查「为什么停在 exit N」先查本表；各码的判定细节以「出处」列为准。

| 退出码 | 触发条件 | 处置 | 出处 |
|---|---|---|---|
| **1** | 单跑 `artifact_readback.py` 且产物存在 error；`--strict`（或 `WJX_STRICT_READBACK=1`）连 warning 也算失败。经 `flow.py` 调用时统一升级为 2 | 按 error/warning 提示修正产物后重跑 | `mechanisms.md` M3 |
| **2** | 防呆门禁严格模式（`WJX_STRICT_COVERAGE` / `WJX_STRICT_CHECKKEY` / `WJX_STRICT_READBACK` / `WJX_STRICT_CONSISTENCY`）、配置矛盾、环境不存在、执行配置文件缺失、`--require-action` 且契约解析不出 action、回读存在 error | 按 stderr 点名修正配置 / 环境后再跑 | `mechanisms.md` M2 |
| **3** | R1 文档审查门禁：未找到审查产物、产物早于契约修改时间、`--doc-review` 指定文件不存在 | 先完成文档审查（或显式 `--skip-doc-review` 留痕）后重跑 | `SKILL.md` R1 · `doc_review.md` §4 |
| **4** | 语义层默认严格：无可认领的事实作业，只出提纲不生成用例（无人值守不降级） | 交作业文件（约定位置 / `--llm-facts` / `WJX_LLM_FACTS`）后重跑同一条命令 | `mechanisms.md` M11 |
| **5** | 语义比对不一致，或争议复核不全（漏条 / 拒收 / 证据依据不通过五道守卫） | 逐条填写争议单 `review_*.json` 后重跑同一条命令 | `mechanisms.md` M11 |

## 一、契约驱动主链路（`flow.py`）

```bash
python scripts/flow.py --contract <契约路径> --execute                       # 生成 → 执行 → 增强 → 回读自检
python scripts/flow.py --action 1000107 --execute                            # 从知识库按 action 取契约
python scripts/flow.py --contract <路径>                                     # 默认：先停下等智能体理解（默认严格语义），提纲落盘、不生成用例；交完作业重跑同一条命令才生成
python scripts/flow.py --contract <路径> --plan-only --profile plan           # 明确计划模式
python scripts/flow.py --contract <路径> --execute --profile discovery        # 发现档：放宽探测广度
python scripts/flow.py --contract <路径> --execute --dry-run                  # 列计划但不发请求，状态仍 planned
python scripts/flow.py --contract <路径> --execute --skip-doc-review          # 显式跳过文档审查门禁（留痕）
python scripts/flow.py --contract <路径> --plan-only --no-llm-facts           # 唯一显式关闭：纯机械，不出提纲、不用事实
python scripts/flow.py --contract <路径> --execute --llm-semantics             # 显式重申默认严格语义（加不加效果相同）
python scripts/flow.py --contract <路径> --execute --llm-facts facts.json      # 直接指定已交的作业（不是 merge 的 --out）
python scripts/flow.py --contract <路径> --execute --config <执行配置.json>
python scripts/flow.py --contract <路径> --exclude-params "token=abc123" --exclude-params corpid
                                                                             # 排除参数：不围绕它生成任何用例（可重复传）
python scripts/flow.py --contract <路径> --execute --adapter graphql          # GraphQL（HTTP 200+errors 判失败）
python scripts/flow.py --contract <路径> --execute --adapter grpc             # gRPC（需先 pip install grpcio grpcio-tools）
```

## 二、已审定用例 / 套件 / 基线

```bash
python scripts/flow.py --cases <已审定用例.json> --execute --out-dir <新空目录>  # 读既有用例，不重新生成
python scripts/flow.py --approve-cases <src.json> --approve-by <人> [--suite-out <target.json>]
python scripts/flow.py --contract <路径> --execute --retest <回归基线.json>     # 复测四态判定（基线须在本轮目录外）
python scripts/flow.py --promote-baseline <snapshot.json> <target.json> --approve-by <人>
```

## 三、生成与计划（单脚本入口）

```bash
python scripts/case_generator.py --contract <契约路径> --read-action <读接口action> --list-action <列表action>
                                                                             # 显式指定配套接口：开启真往返 + 越权读面 + 交叉验证
python scripts/case_generator.py --contract <契约路径> --out <新文件.json>      # --out 仅接受不存在的新文件
python scripts/dependency_scan.py --contract <契约路径>                        # 单独梳理契约显式声明的跨接口依赖
python scripts/plan_render.py --plan intelligent_test_plan.json --cases generated_cases.json --out plan.md
python scripts/multi_interface.py --manifest acceptance.json --out-dir batch [--execute]   # 多接口默认只做计划
```

## 四、执行与对账

```bash
python scripts/case_runner.py --cases cases.json --dry-run                    # 声明式用例执行器
python scripts/case_runner.py --cases cases.json --only B,H
python scripts/case_coverage.py --plan cases.json --results <产物>/api_test_report_full.json --out case_coverage.json
python scripts/case_coverage.py --plan cases.json --results <产物>/api_test_report_full.json \
    --expect-categories AF --exempt-categories "AF:本轮未启用大模型链路" --min-rate 0.8 --enforce
#   启用 AF 时按三族对账：AF 是通配口径（AF1 命中即算 AF 覆盖），只用 "AF" 的话
#   "8 条全 AF1、AF2/AF3 一条没有"也会显示 100%；拆成三族才能看见缺口：
python scripts/case_coverage.py --plan cases.json --results <产物>/api_test_report_full.json \
    --expect-categories AF1,AF2,AF3 --exempt-categories "AF2:该接口无业务规则约束" --min-rate 0.8 --enforce
#   口径提醒：rate 是严格真判定（只数 PASS/FAIL）、looseTotal 宽松（任一 result 即计）、
#   coverage_gate 认 PASS/FAIL/PRECONDITION_UNMET——三者不可混用，见 case-coverage.md
```

## 五、产物回读校验（交付前自检：编码/表头/列宽/编号/行数/证据链列）

```bash
python scripts/artifact_readback.py --dir .                # 自动识别目录内台账与同名前缀报告
python scripts/artifact_readback.py --issues api_test_issues.csv --report api_test_report_full.json
python scripts/artifact_readback.py --dir . --json readback.json   # 机器可读，供门禁消费
python scripts/artifact_readback.py --dir . --strict               # warning 也视为失败
```

## 六、语义层（让大模型读契约产出事实，机械校验后并入）

```bash
python scripts/llm_semantics.py --contract <契约路径> --mode prep --out facts_prompt.md
# 大模型按提示词产出 facts.json
python scripts/llm_semantics.py --contract <契约路径> --mode merge --facts facts.json --out contract_enriched.json --report verify.csv
python scripts/llm_semantics.py --contract <契约路径> --mode compare --facts pass1.json --facts2 pass2.json --out stability.json --report cmp.csv
python scripts/llm_semantics.py --contract <契约路径> --mode combine --facts shard1.json shard2.json --out facts.json
python scripts/flow.py --contract <契约路径> --llm-facts facts.json --plan-only
```

`contract_enriched.json` 是**合并后的契约**，只供审阅；`--llm-facts` 要的是模型产出的
`facts.json`（顶层 `fields`/`relations`/`unknowns`）。传错形状会被形状守卫拦下并报错——
误传时事实会一条都进不去，且产物看着完整。验收看 `llm_verify_report.fact_cases`
（真正参与执行的语义事实施例数），不要看 `fact_utilization.consumed`（它把「仅交叉校验」
的键也算作有消费方）。

**三种状态**（`flow.py` 侧）：

| 状态 | 命令 | 行为 |
|---|---|---|
| **默认 = 严格** | 不加开关 | **先比对、后生成**：没有可认领的事实时只出提纲并 **exit 4**（不生成用例）；交完作业再跑**一模一样的命令**才生成，事实参与本轮 |
| 显式重申 | `--llm-semantics` | 与默认等价，只为让意图写进命令里 |
| 纯机械 | `--no-llm-facts` | **唯一关闭入口**：不出提纲、不用事实，只用规则解析 |

**无人值守不降级**：定时任务 / 批量跑同样停在 exit 4，必须先由会话智能体或人工把作业文件
放到约定位置；脚本不会拿机械结果冒充「已经比对过」。`--cases`（执行已审套件）不重新
生成用例，走的是另一条路，不做抽取与比对（stderr 会点名）。

作业位置：`<契约目录>/llm_semantic_prep/facts_<契约文件名>_<action>.json`
（也认同目录的 `facts.json`）；`--llm-facts` 显式指定优先级最高；`WJX_LLM_FACTS`
环境变量同样算「已交作业」。事实路径指向不存在的文件、不是合法 JSON、或形状不对
（顶层不是对象 / 缺少 `fields`·`relations`·`unknowns`）时**直接拒绝**，不会「没比对就生成」；提纲分片时停下提示会写明「每片各交一份，先 `--mode combine`
合并到该文件」。第二次必须跑**一模一样的命令**（契约名与 action 决定作业文件名）。
事实被拒收会在 stderr 点名（并进产物的 `llm_verify_report.rejected`）。
`--no-llm-facts` 是完整关闭：显式 `--llm-facts` 与 `WJX_LLM_FACTS` 都不再读取；
与 `--llm-semantics` 同时给出时以关闭为准并出声。

**比对不一致怎么办（默认链路的第二次停下）**：交完作业后机器先比对，不一致的字段
会停下（**exit 5**）并写出 `llm_semantic_prep/dispute_<契约名>_<action>.md`。争议单里逐条
列出「机器读到的值 / 智能体写的值 / 机器判定 / **契约原文相关整段（带行号、未截断）**」。
会话智能体看完片段把判断写进 `review_<契约名>_<action>.json`（四选一：认机器的、改成
自己那个值并给原文依据、文档本身矛盾转未知项、整条删掉），再跑同一条命令；机器采纳后
产出 `facts_reviewed_<契约名>_<action>.json`，**再跑一遍完整校验**才生成用例。
五道守卫不许绕过：比对指纹（**事实文件 + 契约原文**）一致（改过作业**或换过契约**都要重做
复核，争议单也会重新贴出新原文）、**每条争议都必须给一条判断**（漏一条整轮不采纳）、
只准改争议字段、evidence 必须出自本条争议贴出的原文片段、修订值在 evidence 里找得到
依据。拒收会在 stderr 点名；只要有拒收或漏条，就重出争议单并停在 **exit 5**，不生成
用例——不允许「确认了一条、剩下的让机器自己扔掉」。无争议（或纯机械）不经过这一步。

## 七、AF 业务场景（默认参与；无真实场景则注明原因不参与）

AF 默认在「想参与」的集合里，但**参与条件是拿到真实业务场景**——`--af-cases` 传入
`business_scenarios --save` 的产物后才进覆盖率分母，用例直接并入（来源 `business_scenario`）。
没有就如实转不参与，N/A 证明写明「本轮没有真实业务场景可用（未提交 af_cases.json）」：
机械拼出的合法请求只验证字段能发，证明不了「用户实际怎么用」，拿它顶业务场景等于假覆盖。

```bash
# 三步产出真实业务场景
python scripts/business_scenarios.py --contract <契约路径> --prep --out prompt.md
python scripts/business_scenarios.py --contract <契约路径> --save --agent-output cases.json --out af_cases.json
# 生成时直接并入（推荐）
python scripts/flow.py --contract <契约路径> --af-cases af_cases.json ...
# 或对已生成套件合并
python scripts/business_scenarios.py --merge --from af_cases.json --into generated_cases.json
```

要强制关闭：`--exclude-dims AF`（给了场景也不参与，N/A 证明留痕「显式关闭」）。
`--cases` 执行已审套件时给 `--af-cases` 会被点名忽略（不重新生成，请走 `--merge`）。

**维度默认全开**：`applicability.judge()` 不再按操作类型预设「必测/选测/不适用」矩阵，
而是先假设 32 个维度全部参与，再逐条检查条件（有没有请求参数、几个参数、有没有
状态/分页/附件字段、是不是写操作、业务域能否构造请求）。条件不满足才标不适用，
理由必须点名具体缺什么并写进 `na_proofs`；报告里看不到 `no_feature` 这类机器代号。
选测档已取消（它不进分母，等于「声明了却不参与」）。
**生成后还会按运行时事实校准一次**：留在计划里却连一条可执行用例都没有的维度
（只有占位），转不参与并在 `na_proofs` 写明缺什么，占位用例保留作人工补充清单；
否则执行档每轮拿它们报假缺口，默认全开就成了默认必挂。
只有 `--exclude-dims A,B` 才能关闭特定维度：同时出覆盖率分母、出用例集合、在
N/A 证明里留痕；排除单字母 `A` 不会误吃 `AA/AE/AB/AC/AD/AF`。默认产物不新增
`dim_excluded` 键（多写一个空键也会让全部已批准套件的 `cases_hash` 失效）。
`--cases` 是执行已审套件、不重新生成用例，此时给了 `--exclude-dims` 会在 stderr
点名「本轮已忽略」，不会闷头不理（要排除维度请走契约生成并重新批准套件）。

## 八、运行实证依赖（候选 → 人工确认 → 才生效）

```bash
python scripts/precondition_scan.py --prep --report <产物>/api_test_report_full.json
python scripts/precondition_scan.py --auto --report <产物>/api_test_report_full.json --store runtime_dependencies.json
python scripts/precondition_scan.py --merge facts.json --report <产物>/api_test_report_full.json --store runtime_dependencies.json
python scripts/precondition_scan.py --confirm-all --store runtime_dependencies.json
python scripts/precondition_scan.py --markdown --store runtime_dependencies.json
```

## 九、业务流（问卷星专用闭环，独立入口）

```bash
python scripts/business_flow.py --kb <KB根目录> --query <业务关键词>            # 查业务流程候选（只生成计划，不发请求）
python scripts/business_flow.py --flow <流程.json> --environment '{"type":"test","write_enabled":true}'
python scripts/business_flow_executor.py（站点绑定入口，未随本包分发） --config <cfg> --env round1_local --dry-run --out <计划.json>
python scripts/business_flow_executor.py（站点绑定入口，未随本包分发） --config <cfg> --print-candidate <candidate.json>
python scripts/business_flow_executor.py（站点绑定入口，未随本包分发） --config <cfg> --cleanup-only --approval <approved.json> --manifest <out>.cleanup.jsonl --out <cleanup.json>
python scripts/business_flow_executor.py（站点绑定入口，未随本包分发） --config <cfg> --run-id <批次号> …      # 同一 run_id 的产物与清理台账互相关联
```

## 十、冒烟 / 深度流程

```bash
python scripts/smoke_runner.py --config <cfg> --cases <cases> [--hooks <hooks>] --out-dir reports
WJX_BASE_URL=https://实际地址/实际路径 python scripts/api_test_runner.py      # 深度流程（自写用例）
```

## 十一、其余常用开关

细则见 `mechanisms.md` 对应小节；未标注归属的属 `flow.py`。

| 开关 | 作用 | 归属 |
|---|---|---|
| `--dedup` | 真折叠重复用例（默认只统计） | `case_generator` |
| `--exclude-config` | 排除参数配置文件（四级回退） | `flow.py` |
| `--envelope-keys` / `--no-inject` | 覆写信封注入键 / 全不注入 | `flow.py` |
| `--payload-hook <py>` | 动态签名钩子，按最终业务参数现算 | `flow.py` |
| `--require-action` | 契约解析不出 action 时升级为 exit 2 阻断；默认只告警并随 `parse_warnings` 留痕 | `flow.py` |
| `--prep-dir <目录>` | 语义 prep 提示词输出目录，默认契约同目录 `llm_semantic_prep/` | `flow.py` |
| `--excerpt-limit` | 语义层分片阈值 | `llm_semantics` |
| `--feedback` | 把本轮「缺陷」回流为模式候选（写 `pattern_candidates.log.md` 待评审池，运行时数据不入库） | `smoke_runner` |
| `--project` / `--version` | 资产归档分层：项目名 / 版本目录 | `flow.py` |

> `--version` 在本 skill 里**只有"资产版本目录"一个含义**（`flow.py` 的归档分层），没有"打印 skill 版本"的功能；要查版本看 `SKILL.md` 的 frontmatter。

## 十二、产物与状态词表（交付时要对上这些名字）

| 类别 | 名字 |
|---|---|
| **运行目录必备** | `generated_cases.json` · `plan.md` · `run_manifest.json`；真实执行另写 `executed_cases.json` |
| **基线与复测** | `regression_baseline.json`（`snapshot: true` 的未批准快照）· `retest_verdicts.json` · 套件路径 `<资产根>/<project>/suites/<interface>/<suite>/<hash>/cases.json` |
| **自检与依赖** | `artifact_readback.json`（`flow.py` 执行后自动跑）· `runtime_dep_contracts.json`（执行前就位的依赖契约）· `pending_confirmations.json` · `source_run/run_manifest.json`（基线晋升的核验对象） |
| **台账配对** | 按前缀配对，不按时间：`X_issues.csv` ↔ `X_report_full.json`；多接口批次汇总 `multi_interface_summary.csv`，冻结输入 `inputs/entry-###/{cases,config,baseline}.json` |
| **状态词** | `run_manifest.status`：`planned` / `running` / `completed` / `partial` / `failed`；复测 verdict（`retest_verdicts.json`）：`FIXED` / `STILL_FAILING` / `REGRESSED` / `BLOCKED_ENV`；基线可信度另标 `STALE_BASELINE`（契约/用例内容变过） / `UNBASELINED`（无快照，不得声称复测通过）；判重矛盾组标 `CONFLICT` |
| **用例命名前缀** | 机械探测 `B-MISS-*` / `B-NULL-*` / `B-OVER-*` / `B-TYPE-*` / `B-LEN-*` / `G-*`；待人工 `TODO-*`；越权矩阵权限行 `A08` |
| **回读故障码** | `MISSING` / `UNREADABLE` / `EMPTY` / `DECODE` / `NO_BOM` / `HEADER_MISMATCH` / `ROW_WIDTH` / `BLANK_ID` / `DUP_ID` / `ROW_COUNT` / `EV_COL_MISSING` / `NO_ARTIFACTS` / `NO_LEDGER` 等（全表与读法见 `mechanisms.md` M3） |
| **环境变量** | `WJX_API_TEST_ASSET_ROOT`（资产根）· `WJX_PROJECT_NAME`（项目名）· `WJX_AF_MAX_CASES`（AF 条数上限，默认 8） |
