# 契约驱动自动生成 — 编排卡

> 本卡只做编排：步骤顺序、命令、门禁触发点、产物与出口。口径细则一律指向 `references/` 各文件，禁止在此复制定义。
> 判路来源：`SKILL.md` §0 行 2 / 3 / 6。红线编号（R1–R8）定义见 `SKILL.md` §1。

## 1. 前置门禁（R1，硬性）

1. 行为测试（生成/执行）前，契约必须先过文档审查。
2. `flow.py` 查找顺序：① 显式 `--doc-review <路径>` → ② 契约同目录 → ③ 当前目录（找 `api_test_doc_issues.csv` 或 `api_test_doc_review_report.md`）。
3. 阻断条件（exit 3）：未找到 / 产物早于契约最后修改时间 / `--doc-review` 指定文件不存在。
4. 确需跳过：`--skip-doc-review` 或 `WJX_SKIP_DOC_REVIEW=1`（计划留痕 skipped/stale）。口径唯一出处：`references/doc_review.md` §4。
5. 用户只要审查结论不执行 → 回 `SKILL.md` §0 行 1（独立模式），不走本卡。

## 2. 取契约

- 本地文件 / 粘贴正文 / URL 直接用；格式按内容自动分派：ShowDoc `.md`、OpenAPI 3.x / Swagger 2.0（`.json/.yaml/.yml`，支持 `$ref` 与嵌套递归展开）、极简 JSON 契约。
- 从知识库按 action / 关键词取：`python scripts/kb_load_contract.py --kb <KB_ROOT> --action <ID> [--endpoint] [--out contract.md]`
  - `--kb` 可省略：按 `KB_PATH` 回退（剥离适配：skill 同级知识库回退已移除——本包内无 `qa-req2testcase-generator/user_knowledge`，请显式传 `--kb` 或设 `KB_PATH`）。
  - `--out` 命中多页默认拒绝写出（exit 2 并列候选）；`--allow-merge` 合并文件仅供人读、**不可作为单契约输入**。
  - `--endpoint` 打印 `ENDPOINT: <url>`，执行期的 `WJX_BASE_URL` 必须用它（R7）。

## 3. 生成与执行（命令已经 `scripts/flow.py` argparse 核对）

| 目的 | 命令 |
|---|---|
| 只生成（文档审查门禁生效；默认先停下等智能体理解） | `python scripts/flow.py --contract <契约路径>` |
| 生成 → 执行 → 增强 → 回读自检 | `python scripts/flow.py --contract <契约路径> --execute` |
| 不传契约路径，按 action 从知识库取 | `python scripts/flow.py --action <ID> --execute` |
| 发现档（放宽探测广度） | 任一命令加 `--profile discovery` |
| 列计划不发请求 | 加 `--dry-run`（状态仍 planned） |
| 明确计划模式 | 加 `--plan-only --profile plan` |
| 直跑已审定用例（不重新生成） | `--cases <cases.json>` |
| 带上真实业务场景（AF 才参与；不给则 AF 注明原因不参与） | 加 `--af-cases <af_cases.json>` |
| 依赖梳理（契约静态扫描；已实跑验证） | `python scripts/dependency_scan.py --contract <契约路径> [--action <ID>] [--out deps.json]` |
| 前置未满足（运行实证双通道；须人工确认） | `python scripts/precondition_scan.py --prep` → 按提示交作业 → `--confirm <ID>` / `--confirm-all`（口径唯一出处：`mechanisms.md` §M13） |

高频开关：`--adapter rest|sojump|graphql|grpc` · `--only <维度,逗号分隔>` · `--config <执行配置JSON>`（写门禁/档位/限流，模板 `assets/intelligent_config.example.json`）· `--exclude-params`。

**维度默认全开**：32 个维度先全部参与，再逐条检查条件（无分页参数、无页面基线、
非国际版、业务域无可传字段等），条件不满足才不参与，理由逐条写进 `na_proofs`。
不再有「选测＝声明了却不参与」这一档。只有 `--exclude-dims A,B` 能关闭特定维度，
排除会同时出分母、出用例集合并在 N/A 证明留痕。AF 默认参与，但要有真实业务场景（`--af-cases`）才进计划，否则注明原因不参与。

## 4. 语义层（默认严格：读 → 比对 → 有争议再确认 → 生成）

① 第一次跑 `flow.py` 自动出抽取提纲并停下（exit 4）→ ② 会话智能体/人工按提纲写出
`facts.json` 到约定位置 → ③ 重跑同一条命令，机器把两边理解逐条比对：一致就直接生成，
不一致则写出争议单再停一次（exit 5）→ ④ 智能体依据争议单里的原文整段写 `review_*.json`
→ ⑤ 重跑同一条命令，机器采纳有原文依据的修订、对修订后的事实**再跑一遍完整校验**，
然后才生成用例。（独立核对时可用 `llm_semantics --mode merge --facts ... --report verify.csv`。）

**三种状态，由开关决定**（开关细节见 `references/quick_ref.md` §六）：

| 状态 | 开关 | 顺序 |
|---|---|---|
| **默认 = 严格** | 无 | 先出提纲并 **exit 4**（不生成）→ 交作业（`facts_<契约名>_<action>.json`）→ 重跑才生成，事实参与本轮 |
| 显式重申 | `--llm-semantics` | 与默认等价 |
| 纯机械 | `--no-llm-facts` | **唯一关闭入口**：不出提纲、不用事实 |

默认严格补充：`WJX_LLM_FACTS` 环境变量也算已交作业；事实文件不存在 / 不是合法 JSON /
形状不对时直接拒绝，不会「没比对就生成」；提纲分片时按提示先 `--mode combine` 合并到作业文件；
第二次必须跑**一模一样的命令**。**无人值守（定时/批量）同样停在 exit 4，不自动降级**——
必须由会话智能体或人事先把作业文件放好。`--cases` 执行已审套件时不重新生成，不做抽取与比对。

**比对不一致时多一轮（exit 5）**：机器先把两边对不上的字段连**契约原文相关整段**写成
`dispute_<契约名>_<action>.md`，停下不生成用例；会话智能体看完片段把判断写进
`review_<契约名>_<action>.json`，**有几条争议就写几条判断**（漏一条整轮不采纳、重出争议单）。
判断只有四种：认机器的值、改成自己的值（必须附原文里逐字复制的依据且值在其中找得到）、
文档矛盾转未知项、整条删掉。机器会重新校验修订后的事实；比对指纹（事实 + 契约）不符、
换过契约、漏复核某条争议、夹带非争议字段、证据不在片段里、值无依据一律拒收并出声，
重出争议单停在 exit 5，不许带着没确认的争议生成用例。
细节见 `references/quick_ref.md` §六、`references/mechanisms.md` §M11。

**两个文件不能混**：`facts.json` 是模型产出的**事实**（顶层 `fields`/`relations`/`unknowns`），喂 `--facts` 与 `--llm-facts`；`contract_enriched.json` 是 merge 输出的**合并后契约**，只供审阅，喂给 `--llm-facts` 会被形状守卫拦下（否则事实一条都进不去，产物却看着完整）。口径见 `references/mechanisms.md` §M11。

## 5. 回归复测变体

`python scripts/flow.py --contract <契约路径> --execute --retest <回归基线.json>`——复测四态判定 `FIXED / STILL_FAILING / REGRESSED / BLOCKED_ENV`（基线须在本轮目录外；基线产出与晋升见 `mechanisms.md` §M12）。

## 6. 多接口批次变体

`python scripts/multi_interface.py --manifest <batch.json> --out-dir <dir> [--execute]`——默认只做计划，`--execute` 才真实执行；`--out` 汇总 CSV（utf-8-sig）。

## 7. 门禁命中点与产物

- R4 覆盖率门禁（收尾）：只认 `PASS`/`FAIL`/`PRECONDITION_UNMET`；缺口记 `COV-*` 两级（逐条 + 维度）进台账。
- R6 拒绝类断言：`assert_validation` 必配 `err_exact`。
- 产物（运行目录必备）：`generated_cases.json` · `plan.md` · `run_manifest.json`；真实执行另写 `executed_cases.json`；回归另写 `retest_verdicts.json`。状态词 `planned/running/completed/partial/failed`，词表见 `references/quick_ref.md`。
- 执行完自动跑 `artifact_readback`（M3）：存在 error → 标记 failed 并 exit 2。
- 归档、套件批准、基线晋升：`mechanisms.md` §M12。

## 8. 出口

- 写操作 / 清理 / 发现档正式化：受 R2 与 `SKILL.md` §2 决策点表约束，逐次取得本次授权。
- 报告出现待确认 / 环境风险条目 → `mechanisms.md` §M9；需按维度深挖单接口 → 回 `SKILL.md` §0 行 5（七步深度）。