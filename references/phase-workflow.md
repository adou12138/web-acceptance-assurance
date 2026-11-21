# 三阶段执行闸门（规划 → 采集 → 裁决，跨会话）

把一次测试拆成三段执行，**阶段之间要停会话、换会话**，在新会话里由用户确认后继续。目的不是流程好看，而是三件实事：

1. 把"规划质量"和"判定质量"压在强推理模型，"点、填、读"交给快模型——**换会话才谈得上换模型**；
2. 用机械手段禁止采集环节越界下结论；
3. 让每个阶段的下游都拿到一份明确的交接包，而不是靠同一段上下文记忆滚动。

同会话内确认（legacy `approve`）保留兼容，但一律记 `same_session`，结论上限压"待核实"——同会话既换不了模型，也没有"人真正看过交接包"的独立环节。

## 0. 需求可疑点（先声明，再执行）

本方案落地时必须承认四件事，不能包装成"已全自动实现"：

| 疑点 | 事实 | 处置 |
|---|---|---|
| 模型路由不可由专家包强制 | 专家包是提示词 + 脚本资产，实际模型由会话/平台决定。脚本换不了模型 | 降级为**声明 + 核对**：每阶段必须声明实际模型类别，与路由表不符时结论上限压"待核实"，除非记录误配理由（`ack-routing`）。**不得宣称"已实现自动路由"** |
| 路由表依据的是外部对比数据 | "GPT-6.1-Sol medium 领先 33.5 分""GLM 约 2.3 倍速 / $0.41 每任务"等数值来自需求方陈述，本包无独立来源 | 只作为**选型建议**写进路由表，不作判据、不进报告结论 |
| 采集环节的"不判定"只能机械拦一部分 | 脚本可拦判定字段与 PASS/FAIL 枚举，但拦不住把判定写进自然语言观察文本（如"看起来功能正常"） | 键名/枚举机械拦截 + 文档纪律双管；**如实标注该盲区**，不宣称"已杜绝误判" |
| 阶段切分与自适应探索有张力 | 采集期发现新风险面时，严格执行“计划不可变”会扼杀探索 | 提供**修订申请**通道（`amend` → 用户批准），批准前不得出裁决；不许静默扩写计划 |
| “停会话、新开会话”不是脚本能执行的动作 | 脚本只能要求“换一个会话标识”，停会话与新开会话只能由人完成 | 交接包显式输出“操作步骤（必须由用户完成）”；`resume` 机械要求新会话标识且从未使用过；**如实声明**确认码防误操作不防伪造 |

## 1. 三阶段定义

| 阶段 | 名称 | 路由类别 | 执行会话 | 核心产出 | 进门条件 | 准出条件 |
|---|---|---|---|---|---|---|
| `plan` | 规划与用例生成 | `strong-reasoning`（建议 GPT-6.1-Sol medium） | S1（`init` 创建） | 需求评审结论、功能点/场景/用例、风险面与对抗 plan | 收到需求/网址（`init` 即放行） | 产物齐备、下一阶段已 submit、已 checkpoint |
| `execute` | 批量执行与状态采集 | `fast`（建议 GLM-5.3-Flash high） | **S2（新会话）** | 执行轨迹：每步页面快照、网络响应、控制台日志、元素状态、证据路径 | `plan` 已 done + 新会话 `resume` 成功 | 批次闭环、采集记录合规、下一阶段已 submit、已 checkpoint |
| `adjudicate` | 结果裁决与缺陷分类 | `strong-reasoning`（建议 GPT-6.1-Sol medium） | **S3（新会话）** | 产品缺陷/环境故障/测试步骤问题/需求问题的分类与证据等级 | `execute` 已 done + 新会话 `resume` 成功 | 裁决条目合规、缺陷登记、已 checkpoint |

阶段与既有工作流程的映射：阶段一 ↔ 工作流程 1–3；阶段二 ↔ 工作流程 3–4；阶段三 ↔ 工作流程 4–6。**步骤是动作清单，阶段是放行边界与会话边界**——动作可以在同一阶段内反复，跨阶段必须结束当前会话、由用户在新会话确认。

## 2. 放行协议：停会话 → 新会话确认（机械，不靠口头）

状态机：`pending → awaiting_approval → approved → in_progress → done`，另有 `rejected`。会话维度另有两个字段：`active_session`（状态当前持有者）与 `awaiting_session_for`（交接未消费时指向待续跑阶段）。

### 2.1 一个阶段的标准动作序列

以阶段一为例（规划在会话 S1，采集在会话 S2，裁决在会话 S3）：

```bash
# ── 会话 S1：规划 ─────────────────────────────
python scripts/phase_gate.py init <task>/phase-state.json --task "<任务名>" \
  --session SESS-1 --model-class strong-reasoning --model-name "GPT-6.1-Sol medium"
# init 即放行规划阶段（用户下达任务＝同意规划），无需再 approve
python scripts/phase_gate.py start    <state> --phase plan  --session SESS-1
# …规划工作：评审、功能点、场景、用例、风险面…
python scripts/phase_gate.py complete <state> --phase plan  --session SESS-1
python scripts/phase_gate.py submit   <state> --phase execute --session SESS-1 \
  --artifact <task>/cases-xxx.json            # 说明下一步要采什么
python scripts/phase_gate.py checkpoint <state> --phase plan --session SESS-1
# → 生成 handoff/handoff-plan.md，打印确认文本；本会话到此结束

# ── 用户操作（必须由人完成）──────────────────
# 1. 结束 S1；
# 2. 新开会话 S2，把执行模型切换为 GLM-5.3-Flash high；
# 3. 把交接包里的确认文本原样发出，例如：
#    「续跑任务「X」的阶段 execute（批量执行与状态采集），确认码 19469EA8；
#      本会话执行模型 GLM-5.3-Flash high（fast）」

# ── 会话 S2：采集（只采集，不判定）──────────
python scripts/phase_gate.py resume <state> --phase execute --session SESS-2 \
  --user-confirm "<上面那段确认文本原样>" --model-class fast --model-name "GLM-5.3-Flash high"
python scripts/phase_gate.py start  <state> --phase execute --session SESS-2
# …执行与采集…
python scripts/phase_gate.py complete <state> --phase execute --session SESS-2 \
    --attack-coverage <attack_coverage.json>   # 采集收尾必交，缺失即压待核实
python scripts/phase_gate.py submit   <state> --phase adjudicate --session SESS-2
python scripts/phase_gate.py checkpoint <state> --phase execute --session SESS-2

# ── 会话 S3：裁决（强推理 + 诚实性规则）──────
python scripts/phase_gate.py resume <state> --phase adjudicate --session SESS-3 \
  --user-confirm "<交接包确认文本>" --model-class strong-reasoning
python scripts/phase_gate.py start  <state> --phase adjudicate --session SESS-3
python scripts/phase_gate.py complete <state> --phase adjudicate --session SESS-3
python scripts/phase_gate.py checkpoint <state> --phase adjudicate --session SESS-3

# 随时查看与终检（风险假设与回归影响面缺一即压上限）
python scripts/phase_gate.py status <state>
python scripts/phase_gate.py check-verdict <state> \
    --assumptions <assumptions.json> --regression <regression.json>
```

`assumptions.json`：`{"reviewed": true, "items": [{"id": "ASM-01", "assumption": "…", "basis": "…", "falsify_action": "…", "result": "confirmed|refuted|open"}]}`。空假设允许，但必须显式 `reviewed=true`——"没写"与"没有"不能混为一谈；`result=open` 即压上限。

`regression.json`：`{"changed_areas": ["…"], "candidates": [{"id": "RG-01", "basis": "依赖/旅程来源"}]}`，或 `{"changed_areas": [], "candidates": [], "no_impact_reason": "…"}`。有变更面却零候选、候选无依据，均压上限。

### 2.2 硬规则（全部机械校验，违反即 rc=2）

- **未 `resume` 不得 `start`**（规划阶段由 `init` 放行）。不允许"边做边等同意"。
- **前一阶段未 `done` 不得提交下一阶段**；`checkpoint` 必须先把下一阶段 `submit` 好（末阶段除外），否则交接包没有明确的下一步。
- **`resume` 是跨会话唯一入口**，且必须满足四条：`--session` 与交接会话**不同**；该标识**从未使用过**；`--user-confirm` 含交接确认码；交接包未被消费（`consumed=false`）。
- **交接点可以驳回**：用户在新会话不满意交接产物，可直接 `reject --session <新会话>`，无需先 resume。
- **状态单写者**：`active_session` 之外的会话调任何推进命令一律拒绝（"无权操作"）。会话交替改同一份状态被机械禁止。
- 放行人、放行时间、放行依据三者缺一，`approve` / `resume` 拒绝。
- 被驳回（`reject`）后须修订再 `submit`，驳回理由进 history，不得覆盖。
- **未批准的范围变更修订申请（`scope_change`）存在时，`start` / `checkpoint` / `resume` / `check-verdict` 一律阻断**；**只读观察（`readonly_lead`，凭据齐备）登记后即可继续**，不阻断也不压上限（判据见 2.3）。
- **交接产物必须真实存在**：`submit` 逐条校验 `--artifact` 为已存在的绝对路径；规划阶段交给执行阶段时还必须凑齐**需求评审结论 / 用例集 / 对抗 plan** 三件（文件名分别含 `review`、`cases`、`adversarial`）。状态流转不能代替这三件产物——曾经零产物加三个不存在的路径也能推进到执行阶段。

### 2.3 修订申请分级（`readonly_lead` / `scope_change`）

修订申请分两类，**处置完全相反**，判错就是"没批就写了"或"该批的不批"：

| 分类 | 含义 | 处置 |
|---|---|---|
| `readonly_lead` | 固定授权范围内的只读观察：已获授权、无业务状态变化、可凭计划与通道记录核对 | **登记后即可继续**，状态记 `approved_readonly`，不阻断推进、不压结论上限 |
| `scope_change` | 范围变更：新增写入、角色或权限扩展、环境变更、超预算、属性未知、凭据不全 | **先批后动**，状态记 `awaiting_approval`，未批准不得执行 |

判定只认**已登记事实**（申报字段本身），不认自然语言自述：

```bash
# 只读观察：五项凭据齐备 → 登记后继续
python scripts/phase_gate.py amend phase-state.json --phase plan --by tester \
  --note "计划外发现一个只读线索" --declaration readonly-lead.json
# readonly-lead.json：
# {"kind":"readonly_lead","scope":"查看已授权项目的列表页","environment":"sandbox",
#  "role":"editor","allowed_actions":["view_list","read_dom"],"no_state_change":true}
```

- `readonly_lead` 须同时具备 `scope` / `environment` / `role` / `allowed_actions` / `no_state_change` 五项凭据，**缺任一项从严归 `scope_change`**。
- 申报命中 `writes` / `role_change` / `environment_change` / `over_budget` / `unknown_attributes` / `incomplete_evidence` 任一信号 → 直接归 `scope_change`，**即使只读凭据齐备**。
- **`path_type`、HTTP 方法或"未看到写操作"单独都不是只读凭据**——凭"看着像只读"放行，等于把先批后动变成先做后说。
- 不传 `--declaration`（或声明里没有有效 `kind`）→ 从严按 `scope_change` 处理（保留旧行为，不放宽）。
- 分类契约的**单一事实源是 `scripts/amendment_class.py`**：`phase_gate` 与 `execution_ledger` 共用同一个 `is_blocking()` 判定函数，两端各判一份必然漂移（分类口径不同 = 放行口径不同）。口径是「**类别决定需不需要先批，状态决定批了没有**」。
- **只读修订 × 写台账交叉核对（N8）**：`readonly_lead` 修订可在 `linked_records` 显式登记关联的台账/写操作条目 ID；收尾复核时，仅对显式关联的条目核对写台账（`write_ops_ledger.py` 产物）中无 `caution`/`danger` 写记录。**默认不压档**：写台账未提供、或修订未显式关联条目时，只披露「未做机械对账」，不把结论压到待核实；时间窗/会话兜底关联仅作线索，不作压档依据。发现关联到写记录时，登记过程问题、压待核实并在首屏披露「只读申报与写记录不符」。

未批准的范围变更在 `start` / `checkpoint` / `resume` / `check-verdict` 四处一致阻断；`approve-amendment` 批准后即放行（`resume` 也不例外——不得借跨会话绕过先批后动）。

### 2.4 防自授、防篡改与防蒙混（自攻发现，均已修）

| 攻击 | 修复前 | 修复后 |
|---|---|---|
| 智能体以 `--by agent` 给自己放行 | 成功 → 人工闸门变自动放行 | 放行人命中智能体身份集合（`agent`/`assistant`/`bot`/`system`/`ai`/`expert`/`砺衡`/`助手`/`智能体`）即拒绝 |
| 直接改 `phase-state.json` 把三个阶段置 `done` | 成功 → 伪造全流程完成并给出"通过" | history 哈希链 + 状态摘要（含 `sessions`/`active_session`/`awaiting_session_for`）校验，`load` 时报"阶段状态不可信" |
| 同会话换个会话标识假装跨会话 | 旧实现根本没有会话概念 | `resume` 要求新标识 ≠ 交接会话且从未使用过；同会话 `approve` 记 `same_session`，结论上限只能"待核实" |
| 交接后在旧会话继续推进 | — | `active_session` 单写者校验 + 交接未消费时旧会话 `start` 被拒 |

**诚实边界（必须写进报告，不得夸大）**：

- 确认码存在状态文件里，能读文件的智能体理论上可以自行填入。它防的是**误操作与蒙混**，不是防有意伪造——那等同伪造哈希链，脚本在无签名密钥前提下无法证明"这行字确实是人打的"。真正的控制点是：**停会话与新开会话这两个动作只能由人做**，放行记录进报告供人核对，被检出的状态不得"修好再用"。
- 哈希链能检出朴素手工改写，检不出"拿脚本按格式重算整条链"的伪造。
- 本包不能切换模型，只核对声明的模型类别（见第 5 节）。

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
- 新发现的风险线索写 `new_lead`，**不得**自行扩写计划或改判用例——走 `amend` 修订申请。申请按 **2.3** 分级：固定授权范围内的只读观察（`readonly_lead`，五项凭据齐备）登记后即可继续；写入、扩权、换环境、超预算、属性未知、凭据不全一律 `scope_change`，先批后动。

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

`phase_ceiling = "待核实"` 当且仅当：任一阶段未 done、存在**未批准的范围变更修订**（`scope_change`；只读观察 `readonly_lead` 不计）、存在未确认的路由误配、存在**同会话放行**（`approval.mode = same_session`）、交接未消费（`awaiting_session_for` 非空）、执行阶段收尾未交攻击覆盖结论、执行阶段收尾未交探索清单 gate 结论、风险假设未清算或有开放项、回归影响面未评估。

### 5.0 授权模式决定的天花板（开工前先说清，别让用户反复收到「待核实」）

「通过」在有些授权组合下**结构性不可达**，这是数学结果而非执行不力。压上限的典型缺口是 `surface_without_hypothesis`——落在写操作、权限、数据一致性三类风险面，都必须有写授权或第二个账号才可能构造探针。开工前按下表声明本轮天花板，并在报告首屏解释成因：

| 授权组合 | 可达到的结论上限 | 说明 |
|---|---|---|
| 只读 + 单账号 | **待核实** | 写/权限/一致性三类风险面无法构造探针，跑再多只读探针也抬不起来 |
| 只读 + 多账号 | **待核实** | 跨账号隔离可测，但写操作与状态迁移仍不可达 |
| 写授权 + 单账号 | **待核实** | 写与状态可测，跨账号/租户隔离仍是缺口 |
| 写授权 + 多账号 + 可回滚数据 | **可能通过** | 三类风险面均可构造探针；仍须过攻击覆盖与对抗门禁 |

写授权会带来副作用，仍走「执行前必问矩阵」逐次确认，不因本表存在而自动获得。

### 5.1 两套状态枚举的映射（口径不同，不得混用）

| 采集记录 `collection_state` | 执行台账 `status` | 语义 |
|---|---|---|
| `collected` | `PASS` / `FAIL`（**裁决阶段**才能定） | 已观察到结果，采集阶段不写判定 |
| `partial` | 不直接对应，按实际观察记 `PASS`/`FAIL`/`BLOCKED` | 部分完成，缺的部分须补或登记缺口 |
| `blocked` | `BLOCKED` | 被前置条件阻塞 |
| `skipped` | `SKIPPED` | 主动跳过并写明理由，不计覆盖 |
| `not_run` | `NOT_RUN` | 未执行 |

规则：采集阶段只允许左列；右列只能由裁决阶段依据证据落定。两套枚举**不互相推导**——`collected` 不是 `PASS`。`execution_ledger init --phase-state <state>` 会把这几项写入 `baseline.gate_precondition`；不传该参数时**不拒绝建账**，但该缺口逐项记入 `baseline.missing_inputs` 并把 `summary` 的 `verdict_ceiling` 压"待核实"（`gate_precondition` 结构不变，便于存量批次复跑）。

## 6. 常见错误与处置

| 错误 | 后果 | 正确做法 |
|---|---|---|
| 采集阶段顺手写“通过/失败” | 快模型的误判直接变成结论，方案的核心收益被抵消 | 只记 `observed` 与 `collection_state`，判定留到裁决阶段 |
| 在同一个会话里连续做两个阶段 | 换不了模型，规划/裁决质量前提不成立 | 每阶段结束 `checkpoint` → 停会话 → 新会话 `resume` |
| 交接后忘了把确认文本发回新会话 | `resume` 因缺确认码被拒（rc=2），流程停住 | 打开 `handoff/handoff-<phase>.md`，把 `text` 代码块里那段原样发出 |
| 用同一个会话标识接两个阶段 | 被拒：“必须换会话”或“已使用过” | 每个阶段用新的会话标识（SESS-1/2/3…） |
| 路由不符但不说 | 假设（规划强模型）不成立，结论可信度虚高 | `ack-routing` 记录误配理由，并在报告“范围限制”中披露 |
| 用“页面应该有”填缺失证据 | 盲测里“帮页面把测试做完”的同型错误 | 记 `blocked` / `not_run`，在报告中列缺口 |
| 阶段完成但台账不传 `--phase-state` | 阶段闸门形同虚设 | 建台账时显式传状态文件，报告引用 `phase_state` 字段 |
| 交接产物写占位路径（`D:/不存在/review-result.json`） | 规划阶段等于没做，却显示"已放行" | `submit` 会逐条校验文件存在且为绝对路径，缺三件直接拒 |
| 风险假设只在报告里写"应该没问题" | 假设既不验证也不登记缺口 | 写进 `assumptions.json`，`result=open` 的项会压上限并要求复测或转为风险条目 |
| 有变更但不列回归候选 | 破坏旧功能无机械要求 | 交 `regression.json`：候选带 `basis`，或写 `no_impact_reason` |
| 计划外发现一律按"要扩范围"走审批 | 如实登记只读线索比先做后说更糟，登记行为反被惩罚 | 按 2.3 分级：只读观察（`readonly_lead`，凭据齐备）登记后即可继续；确有写入/扩权/换环境才走 `scope_change` 先批后动 |
| 只读观察只写 `path_type=positive` 或"看着像只读"就放行 | 凭据不足的只读申报放行 = 先批后动变先做后说 | `readonly_lead` 须五项凭据齐备；`path_type`、HTTP 方法、"未看到写操作"单独都不算凭据，缺任一项从严归 `scope_change` |
