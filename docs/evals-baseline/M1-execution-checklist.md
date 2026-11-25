# M1 检出 A/B 执行清单（M1-2026-11-20）

> 状态：**已就绪待执行**。本清单只做执行准备，**不产生任何结果文件**；
> 结果归档见 `M1-result.md`（实跑后生成）。协议依据：`ab-protocol.md`、`detection-protocol.md`、`run-matrix.json`（冻结，出分后不得改夹具/重复数/阈值/预算）。

## 1. 冻结矩阵摘要

- 矩阵：`M1-2026-11-20`，`budget_minutes=30`
- 规模：5 夹具 × 2 臂 × 2 重复 = **20 run**
- 主指标：配对 blocker/major 检出差值（B−A），先同夹具重复取均值，再五夹具等权汇总
- 阈值：主指标 > 0；B 无新增已知 blocker 漏检；配对误报数不增加；两臂均守预算
- 夹具：`01-boundary-form` / `02-role-state-flow` / `03-async-cancel-network` / `04-ui-keyboard-copy-compat` / `06-injection-vs-natural`（均含答案，**不得复制进归档**）

## 2. 20 run 逐条登记（run_id 与矩阵 `runs` 数组一致）

| # | run_id | 夹具 | 臂 | 重复 | 执行方 | 状态 |
|---|---|---|---|---|---|---|
| 1 | 01-A-1 | 01-boundary-form | A | 1 | 执行方甲 | 未执行 |
| 2 | 01-B-1 | 01-boundary-form | B | 1 | 执行方乙 | 未执行 |
| 3 | 01-A-2 | 01-boundary-form | A | 2 | 执行方甲 | 未执行 |
| 4 | 01-B-2 | 01-boundary-form | B | 2 | 执行方乙 | 未执行 |
| 5 | 02-A-1 | 02-role-state-flow | A | 1 | 执行方甲 | 未执行 |
| 6 | 02-B-1 | 02-role-state-flow | B | 1 | 执行方乙 | 未执行 |
| 7 | 02-A-2 | 02-role-state-flow | A | 2 | 执行方甲 | 未执行 |
| 8 | 02-B-2 | 02-role-state-flow | B | 2 | 执行方乙 | 未执行 |
| 9 | 03-A-1 | 03-async-cancel-network | A | 1 | 执行方甲 | 未执行 |
| 10 | 03-B-1 | 03-async-cancel-network | B | 1 | 执行方乙 | 未执行 |
| 11 | 03-A-2 | 03-async-cancel-network | A | 2 | 执行方甲 | 未执行 |
| 12 | 03-B-2 | 03-async-cancel-network | B | 2 | 执行方乙 | 未执行 |
| 13 | 04-A-1 | 04-ui-keyboard-copy-compat | A | 1 | 执行方甲 | 未执行 |
| 14 | 04-B-1 | 04-ui-keyboard-copy-compat | B | 1 | 执行方乙 | 未执行 |
| 15 | 04-A-2 | 04-ui-keyboard-copy-compat | A | 2 | 执行方甲 | 未执行 |
| 16 | 04-B-2 | 04-ui-keyboard-copy-compat | B | 2 | 执行方乙 | 未执行 |
| 17 | 06-A-1 | 06-injection-vs-natural | A | 1 | 执行方甲 | 未执行 |
| 18 | 06-B-1 | 06-injection-vs-natural | B | 1 | 执行方乙 | 未执行 |
| 19 | 06-A-2 | 06-injection-vs-natural | A | 2 | 执行方甲 | 未执行 |
| 20 | 06-B-2 | 06-injection-vs-natural | B | 2 | 执行方乙 | 未执行 |

（执行方为占位：实际执行人确定后在"执行方"列填真名并同步本表；同一负责人在同夹具仅负责一臂。）

## 3. 分臂与交错

- A 臂走基线路径（不含对抗元素）；B 臂走完整对抗循环（风险面登记、probe-playbook 探针、SCN-ADV 场景、候选复核）。
- ≥2 名执行方分臂：执行方甲固定跑全部 A 臂，执行方乙固定跑全部 B 臂。
- 交错顺序：A1→B1→A2→B2…（按 run_id 顺序表逐条交错，不得先跑完一臂再跑另一臂）。
- 执行方不得看到同夹具其他运行的输出；条件无法隔离时视为技术复跑并披露，不当独立重复。

## 4. 答案隔离

| 责任项 | 责任人 | 规则 |
|---|---|---|
| 答案键、findings.json 原文、答案 ID、匹配规则 | 评测者（本机受限区） | 入库只能是 `archive --sanitized` 产物 |
| `detection-fixtures/*.json`（含答案） | 评测者 | 不得复制进归档与执行方可见区 |
| 执行方可见 | 仅题面 | `blind_eval.py prepare` 只导出题面与允许素材 |
| `99-holdout.json` | 评测者 | 仅主分析冻结后单列一组双臂对照，跑过即退役，不进主分母 |
| 泄漏处置 | 评测者 | 发现泄漏即停止该夹具并保留无效记录 |

受限区路径占位：`<受限区>/runs/<fixture>/<arm>/<repeat>/`（实跑前由评测者在本机建立并填实）。

## 5. 可执行命令（参数按 `blind_eval.py` 1283–1327 行核对）

```bash
# 1) 逐 run prepare + verify（执行方只见题面）
python scripts/blind_eval.py prepare \
  --fixture docs/evals-baseline/detection-fixtures/<夹具>.json \
  --out <受限区>/runs/<fixture>/<arm>/<repeat> --run-id <run_id> --budget-min 30
python scripts/blind_eval.py verify \
  --run <受限区>/runs/<fixture>/<arm>/<repeat> \
  --fixture docs/evals-baseline/detection-fixtures/<同一夹具>.json

# 2) 两臂独立执行（A 基线路径 / B 对抗循环），产 findings.json

# 3) 逐 run 计分
python scripts/blind_eval.py score --run <run目录> \
  --fixture docs/evals-baseline/detection-fixtures/<夹具>.json \
  --findings <run>/findings.json

# 4) 逐条裁定（机械未匹配的发现漏一条即拒）
python scripts/blind_eval.py adjudicate --score <run>/score.json \
  --adjudication <run>/adjudication.json

# 5) 配对 / 汇总 / 去敏归档
python scripts/blind_eval.py pair --matrix docs/evals-baseline/run-matrix.json \
  --fixture <夹具名不含扩展名> --results <受限区>/runs \
  --fixtures-dir docs/evals-baseline/detection-fixtures
python scripts/blind_eval.py matrix --matrix docs/evals-baseline/run-matrix.json \
  --results <受限区>/runs --fixtures-dir docs/evals-baseline/detection-fixtures \
  --out-csv docs/evals-baseline/runs/M1-2026-11-20/matrix.csv
python scripts/blind_eval.py archive --matrix docs/evals-baseline/run-matrix.json \
  --results <受限区>/runs --out docs/evals-baseline/runs/M1-2026-11-20 --sanitized
```

## 6. 必披露项（法定 5 项，按 `run-matrix.json required_disclosure`）

1. 两臂 run 数；
2. 夹具/协议哈希是否一致；
3. 全部 warnings 原文；
4. 派生指标的 NA 项；
5. 无效配对清单与原因。

主指标（配对 blocker/major 检出差值 B−A）、误报差值、成本差值**另列**，不混入必披露法定项，也不称"六项"。

## 7. 去敏验收判据与解读纪律

- 归档验收：`index.json` 的 `sanitized=true` + `contains_answer_ids=false` + `isolation_issues=0`（`contains_answer_key` 在 `blind_eval.py:1265` 硬编码 False，不作鉴别判据）。
- 禁止：单 run 升格"全面有效"；样本 <10 报百分比；`comparable=false` 强行对比；只报检出不报误报；夹具结果外推全类型缺陷。
- 负结果如实写"本轮未验证改造提升检出能力"，不得改矩阵/阈值/夹具后重跑挑好看的。