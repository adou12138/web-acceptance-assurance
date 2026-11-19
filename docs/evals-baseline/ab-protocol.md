# A/B 盲测执行协议（对抗改造效果实证）

定位：回答"对抗改造后，同一执行环境是否多发现了有效缺陷、误报是否可控、成本是否可承受"。本协议是 `scripts/blind_eval.py` 的 `ab` 子命令的使用纪律。**工具只产事实与派生指标；"改造有效/无效"的结论由人按本协议解读，不得由工具输出自动推断。**

## 为什么需要 A/B

对抗改造（v1.33.0→v1.36.0）在结构与门禁上已实证落地，但"改造后多发现多少有效缺陷"在此前四轮审核中始终是未验证主张。本协议把该主张变成可执行实验：同任务、同夹具、同预算下对照改造前后的表现。

## 实验设计

1. **两臂定义**：
   - **A 臂（基线）**：改造前行为。用不含对抗元素的既有执行路径（无对抗 plan、无探针义务、普通验收式执行）；
   - **B 臂（对抗）**：改造后行为。完整执行对抗循环（风险面登记、probe-playbook 探针、SCN-ADV 场景、候选复核）。
2. **同任务同夹具**：两臂使用**同一批** `detection-fixtures` 夹具与同一预算口径（`budget.minutes`）。夹具哈希与协议哈希写入每份 score.json，`ab` 子命令自动校验一致性——哈希不一致即 comparable=false，不得解读。
3. **执行方独立**：两臂不得同一执行方（学习效应）；条件不允许时至少交错顺序（A1→B1→A2→B2…）并在结果中声明顺序。`ab` 子命令检测到跨臂同执行方会输出警告。
4. **多 run**：每臂至少 2 个 run（不同 run 可用不同夹具，但夹具哈希须臂内一致；或同夹具多次独立运行）。单 run 对照用 `compare` 子命令即可，不适用本协议。

## 执行步骤

```
# 1. 两臂各建运行目录并执行（执行方只见题面）
python scripts/blind_eval.py prepare --fixture <夹具> --out runs/ab/armA/run01
python scripts/blind_eval.py prepare --fixture <夹具> --out runs/ab/armB/run01

# 2. 各臂独立执行（A 臂用基线路径，B 臂用对抗循环），产 findings.json

# 3. 计分
python scripts/blind_eval.py score --run runs/ab/armA/run01 --fixture <夹具> --findings runs/ab/armA/run01/findings.json
python scripts/blind_eval.py score --run runs/ab/armB/run01 --fixture <夹具> --findings runs/ab/armB/run01/findings.json

# 4. A/B 聚合对照
python scripts/blind_eval.py ab --arm-a runs/ab/armA --arm-b runs/ab/armB
```

## 指标与解读纪律

`ab` 输出四块：`arm_a`/`arm_b`（按臂聚合的原始计数）、`delta`（B−A 差值）、`derived`（派生指标）、`warnings`+`comparable`（可比性）。

| 指标 | 口径 | 解读纪律 |
|---|---|---|
| `detected.by_severity` | 分级检出计数 | **blocker/major 的增量**是主要收益指标；normal 增量只作覆盖参考 |
| `delta.false_positives` | 合法近似反例被误记为缺陷的计数增量 | 对抗强度上升通常伴随误报压力，误报增量须与候选复核（finding-triage）改进动作并提 |
| `derived.precision_proxy` | detected/(detected+false_positives)，**近似口径** | 样本小（分母 < 10）时只报计数不报比例，不得写"精确率提升 X%" |
| `derived.effective_findings_per_100_probes` | detected_total/probes×100 | 成本收益口径；无 cost 数据时为 NA，不编造 |
| `derived.reproduction_rate` | k/n 复现率 | 两臂各自报，样本 < 5 次有效尝试时只报 k/n 不报百分比 |
| `delta.by_severity` | 分级差值 | 差值方向 ≠ 结论：还须看 warnings 与误报差值 |

**禁止的解读**：
- 单 run 结果升格为"改造全面有效"；
- 样本 < 10 时给出百分比表述；
- comparable=false 时强行对比；
- 把误报增量省略不提，只报检出增量。

**报告呈现**：任何引用 A/B 数据的报告必须同时给出：两臂 run 数、夹具/协议哈希是否一致、全部 warnings 原文、派生指标的 NA 项。缺任何一项视为夸大表述。

## 诚实披露边界

- 首次真实 A/B 执行前，本协议是"已就绪待执行"状态——不得在任何报告写"已验证改造提升检出能力"；
- A/B 只覆盖夹具所代表的缺陷类别（表单边界、误报近似等），不外推为全类型缺陷的检出能力；
- 被测网站的真实任务（非夹具）A/B 另记，与本协议夹具结果分账，不得混算。
