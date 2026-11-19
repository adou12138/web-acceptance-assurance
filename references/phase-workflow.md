# 三阶段执行闸门（规划 → 采集 → 裁决）

把一次测试拆成三段执行，**每段之间由用户放行**。目的不是流程好看，而是把"规划质量"和"判定质量"压在强推理环节，把"点、填、读"这类标准化动作交给快模型，并且用机械手段禁止采集环节越界下结论。

## 0. 需求可疑点（先声明，再执行）

本方案落地时必须承认四件事，不能包装成"已全自动实现"：

| 疑点 | 事实 | 处置 |
|---|---|---|
| 模型路由不可由专家包强制 | 专家包是提示词 + 脚本资产，实际模型由会话/平台决定。脚本换不了模型 | 降级为**声明 + 核对**：每阶段必须声明实际模型类别，与路由表不符时结论上限压"待核实"，除非记录误配理由（`ack-routing`）。**不得宣称"已实现自动路由"** |
| 路由表依据的是外部对比数据 | "GPT-6.1-Sol medium 领先 33.5 分""GLM 约 2.3 倍速 / $0.41 每任务"等数值来自需求方陈述，本包无独立来源 | 只作为**选型建议**写进路由表，不作判据、不进报告结论 |
| 采集环节的"不判定"只能机械拦一部分 | 脚本可拦判定字段与 PASS/FAIL 枚举，但拦不住把判定写进自然语言观察文本（如"看起来功能正常"） | 键名/枚举机械拦截 + 文档纪律双管；**如实标注该盲区**，不宣称"已杜绝误判" |
| 阶段切分与自适应探索有张力 | 采集期发现新风险面时，严格执行"计划不可变"会扼杀探索 | 提供**修订申请**通道（`amend` → 用户批准），批准前不得出裁决；不许静默扩写计划 |

## 1. 三阶段定义

| 阶段 | 名称 | 路由类别 | 核心产出 | 进门条件 | 准出条件 |
|---|---|---|---|---|---|
| `plan` | 规划与用例生成 | `strong-reasoning`（建议 GPT-6.1-Sol medium） | 需求评审结论、功能点/场景/用例、风险面与对抗 plan、放行申请 | 收到需求/网址 | 产物齐备且已提交放行 |
| `execute` | 批量执行与状态采集 | `fast`（建议 GLM-5.3-Flash high） | 执行轨迹：每步页面快照、网络响应、控制台日志、元素状态、证据路径 | `plan` 已 done 且本阶段已获放行 | 批次闭环、采集记录合规 |
| `adjudicate` | 结果裁决与缺陷分类 | `strong-reasoning`（建议 GPT-6.1-Sol medium） | 产品缺陷/环境故障/测试步骤问题/需求问题的分类与证据等级 | `execute` 已 done 且本阶段已获放行 | 裁决条目合规、缺陷登记 |

阶段与既有工作流程的映射：阶段一 ↔ 工作流程 1–3；阶段二 ↔ 工作流程 3–4；阶段三 ↔ 工作流程 4–6。**步骤是动作清单，阶段是放行边界**——动作可以来回，跨阶段必须经用户同意。

## 2. 放行协议（机械，不靠口头）

状态机：`pending → awaiting_approval → approved → in_progress → done`，另有 `rejected`。

```bash
# 阶段一产物就绪，申请放行（必须声明实际执行模型类别）
python scripts/phase_gate.py init  <task>/phase-state.json --task "<任务名>"
python scripts/phase_gate.py submit <state> --phase plan \
  --artifact <task>/review-result.json --artifact <task>/cases-xxx.json \
  --model-class strong-reasoning --model-name "<实际模型名>"
# 用户放行（放行人 + 依据缺一不可）
python scripts/phase_gate.py approve <state> --phase plan --by user --note "同意，进入采集"
python scripts/phase_gate.py start   <state> --phase plan
python scripts/phase_gate.py complete <state> --phase plan
# 采集、裁决同构；随时查看状态与下一步
python scripts/phase_gate.py status <state>
python scripts/phase_gate.py check-verdict <state>   # 未通过时 rc=1，结论上限"待核实"
```

硬规则：

- **未 `approve` 不得 `start`**，`start` 会直接报错（rc=2）；不允许"边做边等同意"。
- **前一阶段未 `done` 不得提交下一阶段**。
- 放行人、放行时间、放行依据三者缺一，`approve` 拒绝。
- 被驳回（`reject`）后须修订再 `submit`，驳回理由进 history，不得覆盖。
- 存在未批准的修订申请时，`check-verdict` 返回"待核实"。

### 2.1 防自授与防篡改（自攻发现的两条绕过路径）

实现完成后对闸门做了黑盒自攻，两条路径当时都成立，已修：

| 攻击 | 修复前 | 修复后 |
|---|---|---|
| 智能体以 `--by agent` 给自己放行 | 成功 → 人工闸门变自动放行 | 放行人命中智能体身份集合（`agent`/`assistant`/`bot`/`system`/`ai`/`expert`/`砺衡`/`助手`/`智能体`）即拒绝 |
| 直接改 `phase-state.json` 把三个阶段置 `done` | 成功 → 伪造全流程完成并给出"通过" | history 哈希链 + 状态摘要校验，`load` 时检出并报"阶段状态不可信"，拒绝沿用 |

**诚实边界**：哈希链能检出**朴素手工改写**，检不出"拿脚本按格式重算整条链"的有意伪造——本包不掌握私钥，做不到不可否认签名。因此真正的控制点是：放行人必须是人工身份、放行记录必须进报告供人核对、被检出的状态不得被"修好再用"。不宣称"防篡改已完备"。

## 3. 阶段二禁令：只采集，不判定

采集方输出**执行轨迹**，不输出结论。记录形态（JSONL，一行一条）：

```json
{"id": "OBS-1", "tc_ref": "TC-1", "collection_state": "collected",
 "model_class": "fast",
 "action_sequence": [{"step": 1, "action": "click", "target": "保存"}],
 "observed": {"page": "...", "dom": "...", "network": [{"status": 500, "url": "..."}], "console": []},
 "evidence_paths": ["evidence/step1.png"], "new_lead": null}
```

机械禁令（`python scripts/phase_gate.py scan-collection <records.jsonl>`，有违规 rc=1）：

- 禁判定字段：`verdict` / `judgement` / `conclusion` / `severity` / `defect` / `classification` / `root_cause` / `pass_fail`。
- `collection_state` 只许 `collected` / `blocked` / `not_run` / `skipped` / `partial`；出现 `PASS` / `FAIL` / `通过` 即拒绝。
- `collected` 必须附 `evidence_paths`，否则裁决阶段无法复核。
- 新发现的风险线索写 `new_lead`，**不得**自行扩写计划或改判用例——走 `amend` 修订申请。

**已知盲区**：自然语言观察文本里的判定（"功能看起来正常"）拦不住。发现此类记录按流程缺陷处理，并计入本批次的过程问题。

## 4. 阶段三纪律：只依据实际观察裁决

裁决必须给出：分类、证据引用、依据类型、证据等级。合法依据类型只有观测类：
`observed_ui` / `observed_dom` / `observed_network` / `observed_console` / `observed_screenshot` / `observed_api_response`。

`python scripts/phase_gate.py check-adjudication <findings.json>` 机械拒绝：

| 违规码 | 触发条件 | 为什么 |
|---|---|---|
| `no_evidence_refs` | 无证据引用 | 无证据即意见，不是缺陷 |
| `no_observed_basis` | 依据里没有 `observed_*` | 仅凭需求推断或设计稿想象，等于自证 |
| `forbidden_source` | `assumed` / `inferred_from_design` / `script_inferred` / `fabricated` / `script_completed` / `expected_only` | 推断与伪造不得入账 |
| `script_completed_behavior` | `page_behavior_completed_by_script=true` | **禁止用脚本补全或伪造缺失的页面行为**——页面没做的事不能替它做完再报成功 |
| `defect_grade_too_low` | 产品缺陷用 E2/E3 证据 | 模拟/静态扫描只能记"待核实" |
| `defect_not_observed` | 产品缺陷 `page_observed != true` | 页面实际没呈现，就不能判成页面缺陷 |

**诚实性规则（裁决阶段必读，直接写进裁决记录与报告）**：

1. 只依据页面实际呈现的状态判定；页面缺失、加载失败、被拦截、超时，一律按"未观察到"记账，不得推定行为。
2. 禁止用脚本、桩、请求重放补全缺失的页面行为后计为通过。
3. 证据等级必须写实际值（E1 真实执行 / E2 模拟 / E3 仅资产）；E2/E3 不得支撑"产品缺陷"。
4. 采集轨迹与裁决结论冲突时，以轨迹为准，冲突本身作为过程问题登记。

## 5. 结论上限的合并顺序

阶段闸门只约束**顺序与合规**，不产生业务结论。最终技术结论按最严合并：

```
execution_ledger.verdict_ceiling
  = strictest(台账事实上限, adversarial_ceiling, phase_ceiling)
```

`phase_ceiling = "待核实"` 当且仅当：任一阶段未 done、存在未批准修订、或存在未确认的路由误配。`execution_ledger init --phase-state <state>` 会把这三项写入 `baseline.gate_precondition`，不传该参数时保持旧行为（结构不变，便于存量批次复跑）。

## 6. 常见错误与处置

| 错误 | 后果 | 正确做法 |
|---|---|---|
| 采集阶段顺手写"通过/失败" | 快模型的误判直接变成结论，方案的核心收益被抵消 | 只记 `observed` 与 `collection_state`，判定留到裁决阶段 |
| 用户没同意就开始下一阶段 | 阶段边界失效，等于退回单段执行 | 先 `submit` → 等 `approve` → 再 `start` |
| 路由不符但不说 | 假设（规划强模型）不成立，结论可信度虚高 | `ack-routing` 记录误配理由，并在报告"范围限制"中披露 |
| 用"页面应该有"填缺失证据 | 盲测里"帮页面把测试做完"的同型错误 | 记 `blocked` / `not_run`，在报告中列缺口 |
| 阶段完成但台账不传 `--phase-state` | 阶段闸门形同虚设 | 建台账时显式传状态文件，报告引用 `phase_state` 字段 |
