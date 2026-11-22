# evals-baseline — 用例生成质量的机械基线

## 这是什么

`scripts/eval_cases.py` 对同一批 cases JSON 打分，把分数存成本目录的 JSON，作为**跨版本比对**的尺子。
用途：改门禁、改 prompt、改判据之后，跑一次比对，低于基线就说明"合法但更空洞"，需要回查。

```bash
# 打分并与基线比对（有退化退出码 1）
python scripts/eval_cases.py --cases <cases JSON> --out <报告>.csv --baseline docs/evals-baseline/fixture-12.json
# 重新存基线（仅在确认质量确实提升并评审通过后）
python scripts/eval_cases.py --cases <cases JSON> --save-baseline docs/evals-baseline/<name>.json
# 条目级差异定位（结构 / 覆盖 / 断言，UTF-8 BOM）
python scripts/eval_cases.py --cases <cases JSON> --baseline <基线> --out-diff <差异>.csv
```

基线有两种格式：**新格式**（`case-eval-baseline/2`，如 `fixture-12-v2.json`）除指标外还固定了**输入指纹**
（用例文件 sha256）、**工具版本**与**条目级摘要**；**旧格式**（顶层即指标，如
`fixture-12.json`）仍可读，但不含条目级摘要、差异定位不可用。输入指纹不同或工具
换版时，输出标 `comparable=false` 并说明原因——**换题了不等于退化**。

差异定位把指标级差异落到条目：结构（新增/已修复的结构错误逐条列出）、覆盖（新增/
已闭合的缺口、缺层变化）、断言（哪条用例的断言数从几变到几）、用例（新增/删除）。
主 CSV 的「条目级定位」列给出对应条目，`--out-diff` 给出完整明细。

样本与基线的目录规范见 `samples/README.md`（含真实需求样本的接入与脱敏要求）。

## 指标与退化方向

| 指标 | 含义 | 退化判定 |
|---|---|---|
| `coverage_layers_missing` | 四层矩阵缺层数 | 变大即退化 |
| `feature_l1_ratio` | 有 L1 用例的功能点占比 | 变小即退化 |
| `method_variety` | 有见证的设计方法数 | 变小即退化 |
| `assertion_density` | 平均每用例断言数 | 变小即退化 |
| `vague_or_placeholder` | 空话/占位命中数 | 只记录，高值需人工看 |
| `smoke_ratio` / `p0_ratio` | 冒烟 / P0 占比 | 只记录，越界由 RATIO 门禁判 |
| `gaps_open` | 覆盖缺口条数 | 只记录；缺口是义务不是得分 |
| `structural_errors` | `case_data` 结构错误数 | 应为 0 |

## 盲测工具（`scripts/blind_eval.py`）

机械基线只说明"用例结构是否更完整"，不回答"能不能抓到缺陷"。盲测用另一条链，协议见 `detection-protocol.md`：

```bash
# 1) 评测者生成"只含题面"的运行目录（答案键不进去）
python scripts/blind_eval.py prepare --fixture detection-fixtures/01-boundary-form.json --out runs/01-A

# 2) 交付前自检：目录里混入答案文件 / 答案 ID / 声明看过答案，都会被拒
python scripts/blind_eval.py verify --run runs/01-A --fixture detection-fixtures/01-boundary-form.json

# 3) 执行方提交 findings.json 后计分（检出/误报/分支/证据/未测义务），写 score.json
python scripts/blind_eval.py score --run runs/01-A --fixture detection-fixtures/01-boundary-form.json \
  --findings runs/01-A/findings.json

# 4) 新旧两版对照，标出不可比风险（同执行方/协议变更/夹具不同）
python scripts/blind_eval.py compare --a runs/01-A/score.json --b runs/02-B/score.json
```

要点：`detection-fixtures/*.json` **含答案**，只能留在评测者手里；执行方只拿 `prepare` 产物。计分不读自报数字，分支分母只能来自答案键，证据路径必须真实存在。随包（插件/技能包/仓库副本）对外分发时必须排除答案夹具目录，对外归档只用 `--sanitized` 去敏件，详见 `detection-protocol.md` 的「随包分发隔离」。

## 冻结的运行矩阵（`run-matrix.json`）

`run-matrix.json` 是本轮实验的**预注册**：5 个夹具 × 2 臂 × 2 次重复 = 20 次运行，
逐条登记（夹具、臂、重复编号、run_id）、预算、规则与**冻结阈值**。出分后不得改
夹具、重复数、阈值或预算；如需变更，另立矩阵 ID 并同时披露。

```bash
# 单夹具配对评分（按夹具 + 重复编号配对，不是臂内聚合）
python scripts/blind_eval.py pair --matrix docs/evals-baseline/run-matrix.json \
  --fixture 01-boundary-form --results runs/ --fixtures-dir docs/evals-baseline/detection-fixtures

# 汇总：先夹具内取均值，再夹具间等权汇总；并给可复算 CSV
python scripts/blind_eval.py matrix --matrix docs/evals-baseline/run-matrix.json \
  --results runs/ --out-csv out/summary.csv

# 独立裁定：机械未匹配的发现逐条裁定（valid/false_positive/insufficient_evidence）
python scripts/blind_eval.py adjudicate --score runs/01-A/scores/01-A-1.json \
  --adjudication adjudication-01-A-1.json

# 归档：受限区完整件 / 常规去敏件（--sanitized 不含原始计分与答案 ID）
python scripts/blind_eval.py archive --matrix docs/evals-baseline/run-matrix.json \
  --results runs/ --out archive/M1 --sanitized
```

口径要点：

- **配对**按（夹具，重复编号），两臂用等价环境、相同授权、版本与重置方式，
  以及相同的时间与操作预算；臂内聚合会把不同环境的运行混在一起，配对差值就不再是
  「同一条件下换个做法」的差值。
- **无效配对**（夹具/协议指纹不同、预算不同、跨臂同一执行方、缺一侧 run）一律排除，
  原因逐条列在 `invalid_pairs`；**有效配对不足的夹具标为证据不足，不计入汇总分母**。
- **主指标** = 配对运行中命中冻结答案键的 blocker/major 缺陷数差值（B−A），
  先对同夹具重复取均值，再将各夹具等权汇总。
- **明细**按夹具给出新增命中、共同命中、A 有 B 无、两臂皆漏，以及「A 命中 B 未命中
  的 blocker」（新增 blocker 漏检）。
- **冻结阈值**由 `threshold_check` 逐项对照，只给事实；是否达标由人按
  `ab-protocol.md` 解读，工具不自动下「改造有效/无效」的结论。
- **归档分两层**：受限区完整件含原始计分与答案 ID；常规去敏件（`--sanitized`）
  不含答案 ID 与原始计分，只留汇总事实、去敏明细与 UTF-8 BOM 的可复算 CSV。

## 边界（不得夸大）

- 这里全是**机械指标**，只能回答"结构是否更完整、方法是否更多样、断言是否更密"。
- **不能回答**"用例是否抓到了真缺陷"——那需要真实执行与缺陷证据，只能靠评审抽检。
- **M10 的结构评分与 M9 的检出指标分账出具，两者不可互相替代**：结构更完整不等于
  抓到了更多真缺陷；M9 的检出收益也不能反推结构评分达标。
- 基线相同不代表质量相同；基线下降一定要查，基线持平不等于没问题。
- `fixture-12.json` 基于 12 条样例用例（回收站需求），只作口径样例，不代表真实项目基线；真实项目基线按 `<name>-<日期>.json` 另存。
