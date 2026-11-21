# 执行管理、恢复与基线对比

## 目标与范围

按需为较多用例、跨会话测试或需交接的任务建立轻量执行工作区。已有规范目录可复用，不强制初始化整套QA项目。设计JSON仍是测试规格唯一源，日志记录执行事实，独立缺陷台账记录当前处理状态；报表是派生视图。沿用 AAA：前置与数据是准备、steps是执行、assertions是业务断言，不建立第二套用例模板。

本包的 `scripts/execution_ledger.py` 仅提供本地单写者台账、恢复候选与进度摘要，调用同包 `scripts/case_data.py` 校验输入设计；探索工作稿和派生视图另见 `exploration-runtime.md` 的 `scripts/exploration_state.py`，浏览器有限只读候选见 `scripts/browser_observer.js`。工作稿不直接回写或替代本台账，需按实际动作和完整断言写BEGIN/FINISH；`tests/test_execution_ledger.py` 包含正常、失败、规格变更、证据变化和命令行完整链路测试。工具不启动浏览器、不验证远程业务、不自动调度数周任务、不外发、不修改产品代码、不作发版裁决。时间由运行环境读取，不能编造记录。

## 工作区与基线

工作目录建议为需求交付目录下 `runs/<round-id>/`，父目录预先存在；每轮初始化一个不存在的新目录：

- `cases.snapshot.json`：冻结本轮设计，防止正在执行的步骤被静默更改。
- `baseline.json`：round_id、UTC创建时间、设计/环境哈希、计划TC、提测上下文和已知缺陷快照。
- `events.jsonl`：逐条 BEGIN/FINISH，带序号、时间、前一事件摘要和自身摘要。
- `.writer.lock`：写入期间的互斥锁，非业务数据；中断遗留时先检查原进程，不能自动删锁或绕过。

上下文JSON必填：target_url、environment、application_version、feature_flags对象、role_scope非空角色名数组、dependency_mode（real/sandbox/mock/replay）。账号口令、token、cookie不进入上下文。其他影响可重复性的配置、数据集标识、浏览器矩阵可补充，整体进入哈希。

应用版本未知时显式使用如 `unknown@observation-id`，记录观察批次；无法保证跨会话目标不变时新建轮次，不因网址相同自动沿用通过。角色范围指计划角色，不储存账号信息；实际attempt角色、建数、操作和清理细节保留在证据中。

known_issues来自独立缺陷台账，可以为空数组表示明确未有已知问题；未提供则为null表示未知，不能自动变成零缺陷。初始化不写示例通过记录、不插入示例BUG。已有目录拒绝覆盖；修复版本、规则或环境变化创建新轮次，保留旧事实对照。

## 每例执行与中断恢复

1. 读取当前设计与summary，核对版本、环境、角色、开关、目标及授权。
2. 执行前写 BEGIN：唯一attempt_id、TC ID、拟执行动作；确认日志落盘后再操作目标。此记录不是对外部系统的事务锁。
3. 按完整规格执行，保存脱敏业务观察证据；PASS/FAIL必须有本地证据文件及每个设计断言的check/actual/matched。工具核对check集合，不替代对证据内容的真实性审阅，也不自动解释断言语义。
4. 立即写 FINISH：status、actual、evidence_paths、assertions、cause、bug_ids、cleanup。失败归因为product须关联BUG；其他归因可为environment/data/automation/unknown，不能为提高通过率修改归因。
5. 更新独立缺陷台账后生成阶段摘要。相同attempt的同类同内容事件幂等返回；不同内容拒绝覆盖。重试使用新attempt ID。

NOT_RUN表示尚无attempt，不提交FINISH；BLOCKED/SKIPPED需在actual说明原因，不能冒充测试完成。自动化执行器没进入业务断言时记录BLOCKED及脚本归因，不能直接报产品FAIL。

恢复基于“当前计划TC集合－当前有效记录”及阻塞/失败集合，而不是从最后一条ID向后跑。summary分别给：ready（可开始的未测候选）、needs_reconcile（BEGIN后无FINISH）、retest_candidates（失败/阻塞/跳过候选）、stale_cases（当前规格或上下文不兼容）。这些是人工/专家决策输入，不是自动重放指令。

有未完成attempt时，先核对目标系统是否已产生实体、提交或异步动作，保存副作用与残留证据；能够还原事实则补原attempt的FINISH，否则保持BLOCKED，不新建attempt反复写入。日志的幂等只保证台账不重复，不能保证浏览器动作或接口调用恰好一次。

## 写操作台账（借鉴 figma-ui-parity 的 write_ops_ledger）

执行 caution/danger 类写操作（创建、修改、删除、状态变更、同步、支付、解绑、下线等）时逐条登记台账，每条必含：动作、目标对象、**可逆性 `reversible`（必填）**、前置证据与后置证据。收尾时三选一：① 按台账执行回滚/清理；② 回填已清理结果；③ 并入报告「待确认」并点名未清理项与不可恢复条数。**不登记等于环境残留不可追**；台账固定落工作区独立文件，不与执行台账混写。

## 对抗探针与扰动台账

对抗批次的扰动、注入与探针结果统一回到本台账体系登记：能力探测、预算停机与扰动启停纪律见 `adversarial-testing.md`；扰动残留按本文件「写操作台账」收尾，探针事件与候选复核另见 `finding-triage.md`。对抗不建立第二台账，不绕过单写者锁。

## 攻击面字段（每条 FINISH 必填）

`finish(..., attack={...})` 把攻击面字段写进 FINISH 事件，供 `attack_coverage.py` 直接消费。**不要另建台账**：自建 JSONL 会绕过单写者锁、证据 SHA-256 绑定与轮次一致性校验。

| 字段 | 取值 | 说明 |
|---|---|---|
| `path_type` | positive / boundary / exception / permission / combo / ui_content / usability / accessibility / data_consistency / regression | 本用例打的是哪个攻击维度；`positive` 只用于"确认功能可用" |
| `surf_id` | `SURF-…` | 对应的风险面，须在对抗 plan 的 surfaces 清单内 |
| `method` | 方法库稳定 ID（`probe-playbook.md` B 节） | 用了哪种方法；多样性只作披露，不作配额 |
| `hypothesis` | 自由文本 | **可证伪预测**："如果 <条件> 则 <可观察的坏结果>"，须含可核对的取值/对象/状态或明确预期方向；空话（如"输入不对就会坏"）不计入覆盖但保留披露。每个高风险风险面至少一条；写了要能被执行出来 |
| `hypothesis_outcome` | broken / held / blocked | 该预测是否被打破；缺省时按 status 推导（FAIL→broken、PASS→held、BLOCKED→blocked）。**broken 是产出不是违规**。**推导不出结果时为「缺结果」，不得计入 held**——没跑出结果不等于假设没被打破 |
| `oracle_source` | independent / self_attested | 判据来自独立通道还是被测物自己的回显 |
| `oracle_channel` | 自由文本 | `independent` 必填且要点名通道（网页端 DOM/接口响应/持久化读回）；只写"读回一致"不算 |
| `discovery_mode` | designed_probe / incidental / undetermined | 这条来自为失效假设设计的探针，还是碰巧撞见 |
| `probes` | 非负整数 | 本用例实际发出的探针数，用于算每百探针有效发现 |

收尾时执行 `python scripts/attack_coverage.py --ledger <工作区>/events.jsonl --plan <对抗plan> --residual-note "<未破的攻击面>" [--amend-note "<计划外风险面的纳入说明>"] [--atk-absent-reason "<本轮为何没有前置攻击假设>"] [--replay-note <不复打理由 JSON>]`，把结论交给 `phase_gate complete --phase execute --attack-coverage`。

**残余风险声明的两种入口**：

- `--residual-note "<自由文本>"`（旧文本入口）：判不了"这条声明说的是不是真的"，
  只判有没有引用已登记编号（`residual_note_checked`）。**结论达「通过」时不接受自由文本**
  （记 `residual_declaration_required_for_pass`）；结论已被其他缺口压到「待核实」时，文本入口保留可用。
- `--residual-declaration <JSON 文件>`（结构化入口）：`{"items":[{"id":"SURF-01","status":"unbroken"}]}`，
  `status` 取 `unbroken`/`broken`/`not_verified`。逐条核对：编号须在已登记清单内，
  状态须与该编号的实际验证状态相符。不符分别记 `residual_ref_not_registered`
  （虚构编号）与 `residual_status_mismatch`（关联错误），两者都压结论上限。
  声明为 `not_verified` 是保守登记，如实登记不压上限。
  两者同时给时以 `--residual-declaration` 为准。
  报告首屏按 `residual_declaration` 的**实际条目**呈现三态
  （有未破项 / 无未破项 / 尚未验证），不得只写一句"已声明残余风险"。

**门禁考什么、不考什么**（口径容易搞错的地方）：

| 会压结论上限 | 只披露不压 |
|---|---|
| 缺 `path_type`、`method` 或 `path_type` 全为 positive | 攻击密度低于 0.30 |
| 声明 `independent` 却拿不出通道/证据 | 计划外风险面（`surfaces_beyond_plan` 计为产出） |
| 高风险风险面没有一条可证伪预测 | 方法多样性不足 |
| 一条独立判据都没有（绝对零，不是比例） | 风险面关联率偏低 |
| 失效假设缺结果（`hypotheses.no_result`） | 高风险面单一方法但未全 held、或尚未验证（`high_risk_surface_single_method` 提示 + 复测建议） |
| 高风险面写了预测但无可采信验证结果 | 可疑自然语言措辞（`suspicious_wording`） |
| 残余风险声明引用未登记编号 / 状态与实际不符 | ATK 跨轮持续未执行（`atk_stagnant`，需 `--atk-prev`） |
| 未提交 ATK（含提交为空）且未说明理由（`atk_absent_unexplained`） | 已说明理由的零 ATK 登记（`atk_not_submitted` 留痕） |
| 假设空话、不可核对（`hypothesis_vague`，不计覆盖） | 假设锐度存疑但有取值/对象/方向词时放行；**已 broken 的笼统假设只披露**（`hypothesis_vague_but_broken`，打破即产出） |
| 高风险面（含 `critical`）单一方法且结果全部 held，既未换方法复打也无点名不复打理由（`surface_unreplayed_unexplained`） | 已 broken 的面不触发本项 |
| 通过档残余声明为自由文本（`residual_declaration_required_for_pass`） | 待核实档保留文本残余声明入口 |
| 只读申报关联到 `caution`/`danger` 写记录（只读 × 写台账对账不符） | 写台账缺失或修订未显式关联条目：只披露未做机械对账 |

理由：配额能被标注操纵（改个 `discovery_mode` 就过关），而"如果 X 则 Y 会坏"凑不出来——写了就得能被执行。绝对值（一条都没有）同样凑不出来，比例能。

## 结果与证据

同一轮每TC保留所有attempt。已证实product失败优先保持FAIL，即使随后成功；未归因FAIL/PASS混合为BLOCKED并标flaky，保留首败。前置BLOCKED后条件恢复再PASS，不自动当flaky。新版本修复复测使用新轮次，旧失败仍留历史，缺陷关闭仍需要原场景和必需回归证据。

PASS/FAIL保存绝对本地证据路径和文件SHA-256；summary重读检查。外部链接请先保存关键内容或截图为本地文件并记录来源。证据丢失或变更的记录不能继续贡献干净通过。摘要/哈希链可发现意外损坏，不是防篡改签名或第三方证明。日志损坏/截断报错并停止写入，人工核对、备份后恢复，不静默丢弃尾行。

单写者约束由独占锁实现；每次写入flush/fsync。持久写中断、磁盘错误或操作系统异常仍可能需要恢复，不能保证零丢失。基线与日志含有限元信息，证据不得携带公开密钥或个人敏感数据，工具不是自动脱敏器。

## 进度、缺陷与基线变化

summary返回计划数、五态唯一TC计数、executed、execution_rate、pass_rate、flaky和证据问题、待测/待核对/复测/失效候选。执行率与通过率取0到1的小数，显示时转百分比；无已执行项pass_rate为null，显示N/A，不能写0%通过或100%通过。

**首屏事实（`headline`）**：summary 还输出 `headline`（`schema=ledger-headline/1`），含**完成范围**（计划/已执行/未执行/阻塞/跳过/跳过数与可写「只读范围内已完成」的标志）、**必查项状态**（三阶段闸门、对抗门禁、攻击面覆盖、证据链完整性、门禁输入齐备性，各为通过/受限/缺失/未知）、**限档原因**（逐条 code 与说明）三项事实，以及与顶层 `verdict_ceiling` **同值**的结论上限（上限只由 `_verdict_ceiling` 算一次，headline 消费该值，不重算）。报告首屏与交付模板都消费这一份事实（`render_report_html.py --summary <summary.json>`），不再各自推断——各推断一次必然漂移，漂移方向通常是报好看的那个。摘要缺 `headline` 字段（旧摘要）时，渲染器回退到推断口径并**在首屏显式标注**该事实，不得静默按推断口径出首屏。

有效PASS/FAIL attempt的唯一TC计入executed；波动仍算已执行但不计干净PASS；五态数加总必须等于当前计划TC数。设计/上下文变化时旧记录全量保守失效，摘要给compatible=false和待测清单，写入拒绝，必须新轮次。内容哈希保守，展示字段变化也可能触发新轮次，避免错误复用。

**基线晋升（OM5，借鉴 api-test M12 资产正式化）**：草稿/临时基线只作本轮参考；晋升为**回归基线**须同时满足：①本轮全部 TC 有有效 PASS/FAIL 记录且 open_bugs 状态已知（未知不得晋升）；②用户或负责人**显式批准**——批准动作在轮次记录留痕（批准人、时间、基线文件哈希），禁止工具自行批准；③旧基线原样留档（按 round 归档，不覆盖）；④晋升后的新基线**冻结只读**，后续轮次不得手工改写。未批准的基线不得作为“回归通过”依据。

独立bugs JSON为数组，字段id、severity（blocker/major/normal/minor）、status（open/in_progress/fixed_pending_deploy/fixed_pending_retest/reopened/closed）；可补复现步骤、预期实际、证据、TC/RUN、责任人、优先级、修复版本等完整缺陷字段。工具只对ID唯一性、严重度和状态做基础验证，不替代缺陷复现与关闭审查。priority与severity分开，既有P0/P1/P2仅作测试/处理优先级，不直接用备注字符串推断缺陷严重度。

未传bugs时open_bugs和missing_bug_ids未知；传入台账后按真实status计未关闭数量，分别输出与提测基线相比的new_ids、still_open_ids、closed_ids、missing_from_current。基线中有而当前缺失的问题不是已关闭，要追查缺漏。此差异是台账变化，不声称每个新增条目都由本次功能引入；原因需证据。原FAIL缺BUG当前记录给missing_bug_ids，不因已知BUG都关闭就自动放行。

阶段报告按需输出：本批计划/完成/失败/阻塞、未解决波动、基线版本与变化、已知/新发现问题、下一批候选及恢复条件。日报周报只有用户需要才生成，不创建定时任务。严重缺陷保留原例FAIL，停止会扩大破坏或依赖失败前置的操作，独立安全用例可继续；对外通知需明确授权。

进度工具**不计算AC/REQ满足率、不输出通过/不通过/待核实或Go/No-Go、不实施80%之类通用门禁**。验收结论由专家按 `execution-evidence.md` 对必要AC/TC、业务判据、数据真实性、未知缺陷状态和未测风险整体判断。open_bugs未知不能宣称零严重缺陷；所有TC PASS也不能替代未确认AC或业务签收。

## 截止快照与关闭核对

summary输出的是整轮当前记录，不具备as_of过滤；正式验收或历史截止报告按execution-evidence.md筛选原始事件、版本/上下文及缺陷历史。快照保留截止时间和每条纳入/排除依据，不修改events.jsonl，不以截止后的FINISH或BUG关闭状态美化此前结果。同版本新轮次不能消除未解决的原失败。

独立BUG台账中的closed是输入声明，不自动等于证据关闭。专家逐项关联BUG→原TC及必要回归TC→修复版本/已部署环境→符合截止点的RUN/attempt→真实业务断言及已审阅证据。未知版本、缺回归、错环境、证据变化或缺状态历史均列“关闭声明待核实”；不另造测试执行状态、不直接改写原台账，相关风险继续影响技术结论。当前工具的open_bugs/closed_ids只反映输入状态差异，不是上述内容核验或历史关闭证明。

## 命令与示例

脚本路径相对专家包根目录，其他工作区调用时换绝对路径。设计文件须先经过case_data校验。初始化不会安装依赖和测试框架，不自动运行任何业务动作。

```bash
python scripts/execution_ledger.py init <新round目录> <cases.json> <context.json> ROUND-001 --bugs <bugs.json>
python scripts/execution_ledger.py init <新round目录> <cases.json> <context.json> ROUND-001 --batches <batches.json> --gate-report <gate-<unit>.json> --adversarial-gate <adv-gate.json>
python scripts/execution_ledger.py init <新round目录> <cases.json> <context.json> ROUND-001 --adversarial-waiver "纯只读小任务，无写操作"
python scripts/execution_ledger.py begin <round目录> <cases.json> <context.json> ATT-001 TC-NAME-001 "打开本次隔离项目并保存名称"
python scripts/execution_ledger.py finish <round目录> <cases.json> <context.json> <result.json>
python scripts/execution_ledger.py summary <round目录> <cases.json> <context.json> --bugs <bugs.json>
python -m unittest discover -s tests -v
```

**第 2→3 跳机械校验**：传入 `--batches` 与 `--gate-report` 时，以下情形一律拒绝初始化（退出码 2，不保留空工作区）：

- 批次存在未完成状态或 `gate` 非 `pass`/`waived`；批次未登记 `case_ids`（空批次不得计入闭环）；`case_ids` 并集与设计用例不一致（有未登记或设计外 ID）。
- 门禁报告 `status` 非 `pass`/`warning`；`checks` 缺失或为空；检查项缺 `id` 或状态不在 `passed`/`warning`/`failed`；存在 `failed`；缺 `S0` 通过项；缺 `counts.testcases`；覆盖条数与设计条数不一致。

即仅有 `{"status": "pass"}` 或只有 `status`+空 `checks` 的最小载荷不足以解锁执行。校验通过后 `baseline.gate_precondition` 记录 `batches_case_count`、`gate_report_status` 与 `gate_report_case_count`。

**不传** `--batches` / `--gate-report` 时**不拒绝建账**（退出码 0），但缺口逐项记入 `baseline.missing_inputs`（顶层，与 `gate_precondition` 分开——后者是门禁判定结果），并把 `summary` 的 `verdict_ceiling` 压"待核实"；`summary.missing_input_reasons` 给出逐项可读原因，报告须写明未做跳间校验。走三阶段流程时（提供 `--phase-state`），跳间校验与攻击覆盖由 `phase_gate` 承担，台账不重复计同一职责的缺失。

**对抗门禁进门条件（必选）**：台账同时记录对抗结论并**机械压制结论上限**——`init` 未提供 `--adversarial-gate` 且未豁免时，`baseline.gate_precondition.adversarial_state="missing"` 并把 `adversarial_ceiling` 置“待核实”；提供 `--adversarial-waiver <理由>` 则记 `waived` 并留痕（仅限纯只读小任务）。传入 `--adversarial-gate`（`scripts/adversarial_gate.py` 的输出，门禁默认启用）时校验其 `verdict_ceiling`/`adversarial_enabled`/`reasons` 字段；若显示对抗未启用且无豁免，同样压"待核实"。`summary` 输出的 `verdict_ceiling` 由此机械派生（存在 FAIL → 不通过；存在 BLOCKED/NOT_RUN/SKIPPED 或未执行 → 待核实；再与对抗上限取更严者），不由人工宣称。

result.json必填字段：attempt_id、status、actual、evidence_paths（绝对本地路径数组）、assertions（check/actual/matched数组，与规格check一一对应）、cause、bug_ids、cleanup。PASS要求所有matched为true且cause=none；FAIL至少一个false；BLOCKED/SKIPPED可无断言与证据，但原因不得缺失。退出码0仅表示记录/摘要命令完成，输入/状态错误为2，**不是质量门禁退出码**。

可选字段 `evidence_grade`（E1/E2/E3）登记该条结果的证据等级：未提供时按旧格式兼容处理（不校验）；status=PASS 且 evidence_grade 为 E2/E3 时台账拒收该条（ValueError："PASS 须 E1 证据，E2/E3 请记 BLOCKED 或降级"），须改记 BLOCKED 或补齐 E1 证据后重写；E2/E3 配合非 PASS 状态正常入账，等级随 FINISH 事件留痕。
