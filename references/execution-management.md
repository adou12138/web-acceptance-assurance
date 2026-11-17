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

## 结果与证据

同一轮每TC保留所有attempt。已证实product失败优先保持FAIL，即使随后成功；未归因FAIL/PASS混合为BLOCKED并标flaky，保留首败。前置BLOCKED后条件恢复再PASS，不自动当flaky。新版本修复复测使用新轮次，旧失败仍留历史，缺陷关闭仍需要原场景和必需回归证据。

PASS/FAIL保存绝对本地证据路径和文件SHA-256；summary重读检查。外部链接请先保存关键内容或截图为本地文件并记录来源。证据丢失或变更的记录不能继续贡献干净通过。摘要/哈希链可发现意外损坏，不是防篡改签名或第三方证明。日志损坏/截断报错并停止写入，人工核对、备份后恢复，不静默丢弃尾行。

单写者约束由独占锁实现；每次写入flush/fsync。持久写中断、磁盘错误或操作系统异常仍可能需要恢复，不能保证零丢失。基线与日志含有限元信息，证据不得携带公开密钥或个人敏感数据，工具不是自动脱敏器。

## 进度、缺陷与基线变化

summary返回计划数、五态唯一TC计数、executed、execution_rate、pass_rate、flaky和证据问题、待测/待核对/复测/失效候选。执行率与通过率取0到1的小数，显示时转百分比；无已执行项pass_rate为null，显示N/A，不能写0%通过或100%通过。

有效PASS/FAIL attempt的唯一TC计入executed；波动仍算已执行但不计干净PASS；五态数加总必须等于当前计划TC数。设计/上下文变化时旧记录全量保守失效，摘要给compatible=false和待测清单，写入拒绝，必须新轮次。内容哈希保守，展示字段变化也可能触发新轮次，避免错误复用。

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
python scripts/execution_ledger.py begin <round目录> <cases.json> <context.json> ATT-001 TC-NAME-001 "打开本次隔离项目并保存名称"
python scripts/execution_ledger.py finish <round目录> <cases.json> <context.json> <result.json>
python scripts/execution_ledger.py summary <round目录> <cases.json> <context.json> --bugs <bugs.json>
python -m unittest discover -s tests -v
```

result.json必填字段：attempt_id、status、actual、evidence_paths（绝对本地路径数组）、assertions（check/actual/matched数组，与规格check一一对应）、cause、bug_ids、cleanup。PASS要求所有matched为true且cause=none；FAIL至少一个false；BLOCKED/SKIPPED可无断言与证据，但原因不得缺失。退出码0仅表示记录/摘要命令完成，输入/状态错误为2，**不是质量门禁退出码**。
