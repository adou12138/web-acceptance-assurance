# 风险面与判据登记（adversarial-plan 契约）

定位：对抗循环启动前，把"风险面—判据—对抗义务"登记为一份伴随契约文件 `adversarial-plan.json`（下称 plan）。plan 是对抗执行的唯一判据事实源：探针只能从 ADV 义务出发，事件（EVT）只引用 ADV，复核只引用事件产生的候选。字段形态见 `schemas/adversarial-plan.schema.json`，机器校验用 `scripts/adversarial_data.py`（`load` / `validate` / `fingerprint` 三个入口，标准库实现）。

## 三类条目

### SURF（风险面条目）

每个高风险交互或未证实假设登记一条风险面，字段：入口（entry）、角色（role）、状态（state）、数据（data）、依赖（dependencies）与**失效假设**（failure_hypothesis，写清"怀疑哪里会怎么坏"），并给风险等级（high/medium/low）。失效假设没有依据时不编造根因，写"假设待核验"并说明怀疑点。surfaces 不允许为空——没有风险面就不启用对抗循环，而不是空跑。

### ORC（判据条目）

每条判定依据登记一条：来源 `source` 只允许 REQ / AC / contract / invariant / req_issue 五类枚举（需求原文、验收标准、接口契约、明确业务不变量、需求可疑点），观测入口（observation_entry，写明从哪里观测到什么）与适用条件（applicable_condition）。模型自猜的规则不得登记为 ORC——先按 `adversarial-testing.md` 记"待核验"，确认后再入账。

### req_issue（需求可疑点判据）与需求批判

批判者不只批判实现，也批判需求本身。评审阶段产出的**需求可疑点**（遗漏 / 冲突 / 隐含假设 / 业务合理性存疑，见 `skills/requirement-review/SKILL.md` 的伴随文件约定）登记为 `source=req_issue` 的 ORC，驱动"挑战需求"类探针：验证产品在需求未定义、自相矛盾或违背业务常识之处的行为是否可辩护。纪律：

- req_issue 探针的产物**只能是"需求问题候选"或"待业务确认"事项**，不得直接判产品缺陷——实现按一条错误需求正确执行时，错的是需求不是实现；
- req_issue 候选 accepted 后归入**需求问题清单**（关联 REQ-，走需求问题分账），不建产品 BUG-；若实现同时违背了其它已确认规则（REQ/AC/contract/invariant 判据），另行走正常产品候选流程，两账不混；
- 需求可疑点未澄清前，相关功能点的"通过"结论保持"待业务确认"标注，不以需求原文单方判定。

### ADV（对抗义务条目）

每个待执行探针登记一条义务：必须关联至少一条 SURF（surface_ref）与一条 ORC（oracle_ref）、探针类型（probe_type：challenge / fault_injection / timing / permission）、预算（budget，探针数/时间盒/副作用配额任一口径）与状态（status：planned / executed / cancelled）。状态只能由主会话按事实更新；executed 必须有对应 EVT 事件佐证（见 `finding-triage.md`）。

## ATK（攻击假设条目）：与 SURF 并列的第二条轨道

攻击假设（`ATK-`）是**独立于 plan 的登记条目**，走自己的校验轨道，与需求来源的 SURF 平级并列，互不替代。

**为什么另立轨道**：SURF 要求"可证伪预测"（`failure_hypothesis` 写成"如果 X 则 Y 会坏"），是因为需求来源的风险面必须能被证伪。而攻击假设的价值恰恰在于**模糊与整体**——"这个功能大概会在某个地方不对劲"这种直觉，拆成单条预测就失去了攻击力。强迫 ATK 套用可证伪格式，等于消灭它。

**字段**（`scripts/attack_coverage.py --atk` 读取，数组或含 `assumptions`/`atk`/`entries` 数组的对象）：

| 字段 | 说明 |
|---|---|
| `id` | `ATK-` 前缀 + 稳定标识，跨轮次不变号 |
| `hypothesis` | 模糊假设。允许不精确，但不得为空 |
| `validation_path` | 验证路径：人工步骤、场景推演、代码走读均可，**不要求脚本探针** |
| `impact` | 影响面：功能 / 数据 / 权限 / 体验 / 其他 |
| `status` | 未执行 / 执行中 / 命中 / 未命中 / 无法验证 |
| `linked_tc` / `linked_ac` | 可选。关联则填 ID；**不关联不构成排除理由** |
| `severity` | 可选。blocker / major / minor；缺省按影响面推定（功能/数据/权限 → major，体验/其他 → minor），显式登记优先 |
| `note` | 可选。自由备注 |
| `verified_by` | 可选。这条结论（尤其「未命中」）是怎么得来的：`practical`（实操探针）/ `simulation`（场景推演）/ `code_walk`（代码走读）/ `unknown`。缺省 `unknown`——旧台账零改动兼容，不触发任何证据门禁 |
| `attempt_id` | 可选。`verified_by=practical` 且判「未命中」时的首选证据：绑定执行台账 FINISH 事件（见下） |
| `evidence` | 可选。降级证据路径：`[{"path","sha256"}]` 数组，落盘文件+哈希的完整引用（不是 `Path.exists()` 的放宽版）。仅当该 ATK 确实不依附任何执行时使用，`note` 须写明为何没有 `attempt_id` |

**校验规则**：

- `status=未执行` **不触发**结论上限——允许假设长期悬置。这是对低质量假设的**有意让步**：代价是低质量 ATK 也能存续，靠披露与抽样复核间接施压。
- `status=命中` 且 `impact` 属功能/数据/权限 → 升级为严重度复核候选；**尚未裁定闭环时结论上限压"待核实"**。
- 其余状态（执行中 / 未命中 / 无法验证 / 体验类命中）只作披露，不压上限。
- **「未命中」的证据等级门禁（M1）**：判「未命中」且 `verified_by` 为 `simulation`/`code_walk`（推演/走读）且 `impact` 属功能/数据/权限时，须 `--replay-note` 点名该 ATK 并引用既有观察（解释为什么不复打），或补一条实操探针——纯脑内推演不得冒充否定结论的证据。`verified_by=practical` 判「未命中」时**不可用 `--replay-note` 豁免**（一句话解释证明不了"做了实操"），须满足其一：① `attempt_id` 绑定台账 FINISH——该 attempt 已登记、status 为 PASS/FAIL、`evidence_grade` 为 E1（旧格式未设按历史兼容处理，降级为 notice 披露）、`linked_tc` 非空时 FINISH 的 `case_id` 必须与之匹配（防止拿任意 E1 记录挂攻击面）、证据文件读时哈希复核通过；② `evidence` 落盘文件+哈希核验通过。两者皆无 → gap（`atk_practical_negative_unproven`）。体验类「未命中」不受此约束——不扩大义务。
- **闭环的判定与缺陷复核同一口径**（见 `finding-triage.md`）：`rejected`（判为误报）已定性，闭环；`accepted` **且已关联 BUG**（`tie_to_bug` 非空）闭环；`accepted` 但未建 BUG、或 `unresolved`（未裁定）、或缺 verdict，**均视为未闭环**，仍压"待核实"。只有一条复核记录不等于已裁定。复核 JSONL 为追加写入，同一候选可能有多条记录——**判定只认每条候选的最新一条记录**（追加顺序末条为准）：历史记录不得永久压上限（先 accepted 未闭环、补建 BUG 后追加关联的候选应解除），也不得被旧裁定盖住（翻案后的新未闭环记录照压）；`adversarial_gate` 与 `attack_coverage` 两侧折叠口径一致。
- **ATK 存续不依赖事后 amend 纳入计划**：登记时即为合法条目。计划外的 ATK 不计入 `surfaces_beyond_plan`（那是需求轨道的指标）。
- 字段缺失、ID 前缀非法、枚举越界 → 报缺口，不默认通过。

ATK 与 SURF 的产物都可以进入第 4 类交付物（见 `output-templates.md`）；ATK 的复核记录走同一套候选状态机（见 `finding-triage.md`），`finding_id` 用 `ATK-` 编号。

## 冻结来源指纹

plan 头部 `source_fingerprints` 必须登记两枚 sha256：

- `material`：本次需求材料（需求文档/正文文件）的内容指纹；
- `cases_json`：cases JSON 的内容指纹。

指纹在 plan 定稿时计算并冻结；需求或用例资产变更后指纹失配，须重建或显式重新登记 plan，不得沿用旧 plan 挑战新版本。`scripts/adversarial_data.py` 的 `validate` 支持传入实际文件路径复核指纹一致性，失配报错。

## 风险面不可用单条 PASS 抵消

一条 SURF 覆盖缺口不能被另一条 SURF 的通过掩盖：SURF-001 的探针未执行或被阻塞时，不得以"SURF-002 已挑战通过"为由把结论上限恢复为通过。对抗补充门禁（`scripts/adversarial_gate.py`）按条目逐条核算：必选高风险 SURF 无对应已执行探针即压低上限；同理，风险面登记后不得通过删除 SURF 条目来"清零"未覆盖项，取消探针只能把 ADV 置 cancelled 并写明理由，SURF 本身保留在账上。

## 与既有资产的关系

plan 只登记对抗专用的风险面与判据，不复制 cases JSON 的用例内容；判据与既有 REQ/AC/R-xx 用引用关联（ORC.source + source_ref），不另立编号体系。plan 是只读输入：门禁脚本只读不改；更新由主会话按单写者纪律改写 plan 文件本身。
