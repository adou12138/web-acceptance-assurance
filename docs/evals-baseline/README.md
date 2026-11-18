# evals-baseline — 用例生成质量的机械基线

## 这是什么

`scripts/eval_cases.py` 对同一批 cases JSON 打分，把分数存成本目录的 JSON，作为**跨版本比对**的尺子。
用途：改门禁、改 prompt、改判据之后，跑一次比对，低于基线就说明"合法但更空洞"，需要回查。

```bash
# 打分并与基线比对（有退化退出码 1）
python scripts/eval_cases.py --cases <cases JSON> --out <报告>.csv --baseline docs/evals-baseline/fixture-12.json
# 重新存基线（仅在确认质量确实提升并评审通过后）
python scripts/eval_cases.py --cases <cases JSON> --save-baseline docs/evals-baseline/<name>.json
```

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

要点：`detection-fixtures/*.json` **含答案**，只能留在评测者手里；执行方只拿 `prepare` 产物。计分不读自报数字，分支分母只能来自答案键，证据路径必须真实存在。

## 边界（不得夸大）

- 这里全是**机械指标**，只能回答"结构是否更完整、方法是否更多样、断言是否更密"。
- **不能回答**"用例是否抓到了真缺陷"——那需要真实执行与缺陷证据，只能靠评审抽检。
- 基线相同不代表质量相同；基线下降一定要查，基线持平不等于没问题。
- `fixture-12.json` 基于 12 条样例用例（回收站需求），只作口径样例，不代表真实项目基线；真实项目基线按 `<name>-<日期>.json` 另存。
