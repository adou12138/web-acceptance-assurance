# 对抗事件与候选复核

定位：对抗批次产生的每个事件（探针执行、故障注入、时序扰动、权限挑战）先入**事件台账**，再从事件中产生**候选**（candidate）进入复核；复核通过的候选才升级为正式缺陷。事件与复核的单文件形态见 `schemas/adversarial-events.schema.json` 与 `schemas/finding-reviews.schema.json`；探针、预算与扰动纪律见 `adversarial-testing.md`，判据登记见 `oracle-contract.md`。

## 事件台账（EVT-）

- **单写者**：主会话统一记录事件台账；采集工具、子代理只提供原始观察，不直接写台账。事件为 JSONL 追加写入，每行一个事件对象。
- 每个事件必含：事件 ID（`EVT-` 前缀）、类型 `type`（probe / fault_injection / timing / permission）、`probe_ref`（指向执行的 `ADV-` 义务）、观察记录 `observation`、证据路径数组 `evidence_paths`、以及**必填布尔 `injected`**——区分自然暴露的故障与人工注入的扰动；注入事件还须能对应到扰动启停记录。
- 事件只记事实，不定缺陷：`observation` 写"观察到什么"，不写"这是产品 bug"。

## 候选状态机（candidate）

事件中值得追查的发现登记为候选，状态机单向推进：

```
raw → triaged → accepted / rejected / unresolved
```

- **raw**：刚登记，只有现象与引用的事件；
- **triaged**：完成聚类与最小化（见下节），写明竞争假设与证据；
- **accepted**：判据（ORC）成立、证据可复核、判定为产品侧问题——**只有 accepted 才可建立正式缺陷（BUG-）**；
- **rejected**：判定为误报、环境/数据问题或判据不成立，写明理由归档；
- **unresolved**：证据不足或判据缺失，暂时无法定性。

**候选与正式缺陷分离**：候选编号不进入 BUG- 台账，也不冒充缺陷计数。`unresolved` 候选**既不计有效缺陷、也不计误报**，保留在分母中并压低结论上限（见 `scripts/adversarial_gate.py`）。复核记录（`finding_id`、`verdict`、`reviewer`、`rationale`、`tie_to_bug` 可空）逐条落 `finding-reviews`，`accepted` 且 `tie_to_bug` 为空视为未闭环——必须补建 BUG 并关联后才能解除门禁影响。

**req_issue 类候选单独分账**：判据来源为 `req_issue`（需求可疑点，见 `oracle-contract.md`）的候选，accepted 后归入**需求问题清单**（关联 REQ-），不建产品 BUG-、不计有效缺陷数——需求问题与产品缺陷是两本账。同一发现若同时违背其它已确认规则，另建产品候选走正常流程；两类候选分别复核记录，不合并销号。

## 聚类与最小化

- **保留原失败与全部样本**：聚类不覆盖原始记录；重试波动保留每次结果，不撤销首败。
- **同文案不同原因不合并**：现象相似但触发路径、角色或状态不同的候选分别保留；文案相同不等于同根因。
- **无法证实同根因只标疑似重复**：没有共享组件、共享判据或可复现的共同条件的，只能标"疑似重复"并注明怀疑依据，不得直接合并销号。
- **最小化不得删除必要前置**：缩减复现步骤时保留必要角色、并发顺序与前置数据；删掉这些前置使问题消失，等于没有最小化。
- **缩减失败不撤销首败**：最小化复现失败时，原失败记录与候选保持原状态，另记缩减结果；不能以"缩不出来了"推翻首败。

## 与门禁的关系

事件与复核数据是对抗补充门禁（`scripts/adversarial_gate.py`）的输入：accepted 的 blocker/major 未闭环候选把结论上限压到"不通过"，存在 unresolved 候选压到"待核实"，必选高风险 SURF 无对应已执行探针同样压到"待核实"。复核 JSONL 为追加写入，同一候选多条记录时**判定只认最新一条**（追加顺序末条为准）——历史记录既不永久压上限，也不能盖住新裁定；`adversarial_gate` 与 `attack_coverage` 的折叠口径一致。报告呈现口径见 `output-templates.md` 的对抗门禁呈现。
