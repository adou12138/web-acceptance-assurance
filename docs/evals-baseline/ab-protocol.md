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

## 正式矩阵与配对口径

正式实验用**冻结的运行矩阵** `docs/evals-baseline/run-matrix.json`：5 个夹具 × 2 臂 × 2 次重复 = 20 次运行，逐条登记（夹具、臂、重复编号、run_id）、预算、规则与冻结阈值。出分后不得改矩阵；如需变更，另立矩阵 ID 并同时披露。

配对键是**（夹具，重复编号）**，不是臂内聚合：

- 同一夹具的两臂使用等价环境、相同授权、版本及重置方式，以及相同的时间与操作预算；
- 同夹具的四次运行须为四个独立执行实例，至少由两名执行方分臂负责；
  同一负责人在同夹具仅负责一臂；无法隔离同臂重复时视为技术复跑并披露，不当独立重复；
- 盲测：计分时仅使用匿名臂编号；执行方不得看到同夹具其他运行的输出。

汇总口径：主指标 = 配对运行中满足证据要求并命中冻结答案键的 **blocker/major 缺陷数差值（B−A）**，**先对同夹具重复取均值，再将五个夹具等权汇总**。

无效配对（夹具/协议指纹不同、预算不同、跨臂同一执行方、缺一侧 run）一律排除，原因逐条列在 `invalid_pairs`；**有效配对不足的夹具标为证据不足，不计入汇总分母**——拿一半运行算出来的均值描述的是那一半，不是这个夹具。

派生指标同理**不许拿 0 顶替"没测"**。`summarize_matrix` 输出的 `known_near_miss_hits` / `known_near_miss_rate` / `insufficient_evidence` 在无法统计时给 `null`，并在 `false_positive.not_measured` 里逐项写明原因（如"某 run 的 score.json 缺 `near_miss_hits` 字段"、"N 个 run 缺 adjudication.json"）与 `*_measured`/`*_unmeasured_runs` 计数。**`0 / 40` 与 `null / 40` 含义相反**：前者是"已知反例零误报"，后者是"这一项没测"，任何人不得把后者读成前者。

## 独立裁定

机械评分只认词元命中；未命中的发现可能是措辞差异、也可能是答案键漏了、也可能是无效发现——三选一属人工判断，工具不猜。用 `adjudicate` 子命令逐条裁定，取值 `valid` / `false_positive` / `insufficient_evidence`，每条写明理由与 `root_cause`（同根因的重复发现按根因归并）。

裁定必须**逐条覆盖**该次计分的全部未匹配发现：漏一条即拒——否则误报率没有可复算的分母。机械已命中的发现不得被裁定为无效。

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
