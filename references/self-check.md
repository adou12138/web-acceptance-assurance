# 自检清单（v1.55 R11：开工前 / 交付前）

> 来源：额度分配（web-quota-1037678）与访谈独立模块两轮任务的漏测归因。两轮的共同病灶
> 不是规则缺失，而是**规则在、没有任何东西在正确的时间问一遍**。本清单把既有规则的触发
> 点固定下来：开工前问一遍（决定要测什么、怎么判），交付前问一遍（决定结论能不能写）。
> 每问都指向包内已有规则；答不上来的项照既有口径压档或转待确认，不在清单里发明新判据。

## 开工前（建工作区/建台账时，逐问答）

1. **适用性九类都裁了吗？** `applicability` 九类（functional/security/performance/accessibility/compatibility/visual/reliability/usability/content）逐类给出 state 与 reason。声明 `applicable` 的 visual/usability/accessibility，用例里要有对应 `coverage_flags`（VISUAL/USABILITY/ACCESSIBILITY）——声明可测却没测会被 `coverage_matrix.py` 的 dimension_gaps 阻断（R1）。
2. **同类已有模块登记了吗？** 被测模块在同一产品里有"同类已有模块"（如统计分析之于交叉分析、AI 问卷之于点数扣除）时，把规则口径/样式口径/账号体系登记为第二 oracle 参照；不要求行为相同，但"不一致"必须是有据差异或已登记待确认（访谈任务：点数口径 0 条登记）。
3. **"实际值"的来源盘点了吗？** 凡"与实际不符"类风险面，先登记实际值取自哪个页面/接口，并声明 `oracle_scope`——同一后端的另一个视图不算独立（R2，额度任务 08 证据实证）。
4. **校验类规则标方向了吗？** 输入校验/上界/阈值类风险面在 plan SURF 声明 `direction`（increase/decrease/both/n-a），增减两个方向都要有探针（R3/P25，额度 #12 回收被伪上界拦截）。
5. **恶劣数据形状列了吗？** 长名称、空记录页签、多类型账户、大文件、重复输入——逐条标注"环境可构造/需申请/不可构造"，不可构造的进缺口清单（访谈：Excel 100 条、重复文件、800MB）。
6. **无判据的 UI 项列清单了吗？** 间距/图标/字号/列对齐/弹框尺寸够用性——开工时知道哪些项将"无判据可依"，交付时逐项进放弃清单（R4）。

## 交付前（出报告/收尾时，逐问答）

1. **台账/门禁产物都在吗？** 有执行类产物就必须有台账、attack_coverage、exploration gate——用 `scripts/gate_absent_check.py --dir <工作区> --enforce` 机械检测（R9）；缺席则首屏明写"未走门禁"并压上限。
2. **定性 UI 放弃项交了吗？** `--ui-handoff` 清单或 `not_applicable` 显式豁免；清单里每条要么有判据要么有 not_evaluable 理由（R4）。
3. **采集记录里有没有"写了但没进通道"的线索？** 线索键只认 `lead`；`new_lead` 等疑似键会被 scan-collection 出声（R6），当场改键名。
4. **阻断的面都安排替代通道了吗？** "留待手动核验"不算安排——每条 BLOCKED/SKIPPED 须有替代通道+责任人+状态，进 baseline `blocked_faces` 与报告首屏"已声明阻断"栏（R8）。
5. **报告里"已声明阻断"和"未覆盖"分栏了吗？** 两者禁止混列（R7）——读者把阻断当漏测、或把漏测当阻断，都是交付失败。
6. **九类逐类结论都写了吗？** 报告须按 applicability 九类逐类给结论（含"未测"类），缺类即交付不完整（R10）。
7. **跨视图互比跑了吗？** 平行子视图（页签/模块/资源类型）的量词单位、数值精度、术语——`copy_consistency.py --cross-view` 出观察候选，人工裁定（R5）。
8. **对每条"按设计拒绝"反向打过吗？** 校验规则的减少/回收/撤销方向有没有探针（R3/P25）。

## 交付前补问（v1.56 S 批次）

7. **持久化面零一致性路径？**（S2）涉及持久化写入的 SURF 声明 `persistence: true`；台账零条
   `data_consistency` 即缺口（`consistency_path_absent`）——流水页从未访问=0 条记录，旧判据照漏。
8. **多资源面逐轴铺了吗？**（S3）`resource_axes` 声明轴清单，执行记录用 `axis` 标注；
   任一轴零记录即缺口（`axis_uncovered`）。
9. **计划探针参数与实执行一致吗？**（S4）ADV 声明 `probe_inputs`（含边界 schema），
   记录用 `adv_id`+`actual_inputs` 对照；不满足即 `probe_input_mismatch`（F2 场景）。
10. **触发方式声明了吗？**（S5）`driver ∈ {ui_user_path, page_internal_fn, replay_or_stub}`；
    后两类不得计 PASS（落账强制 driver_justified）。
11. **跨视图互比按同语义量分组了吗？**（S6）`--cross-view` 须带 `groups`；无分组只披露
    （不同资源的单位差异是合法的，全语料比对是噪声源）。
12. **ui_handoff 豁免与事实一致吗？**（S7/S8）not_applicable 与 ui_content/usability/
    accessibility 采集记录矛盾即拒收；放弃项须给 criterion_source 或 not_evaluable_reason，
    且报告逐条列出。
13. **交付记录绑定台账了吗？**（S10）`delivery_ledger_binding.py --enforce`：交付 CSV 每条
    须能在 events.jsonl 找到 FINISH。
14. **恶劣数据形状登记了吗？**（S9）`init --data-shapes`；impossible 进 headline.uncovered。
