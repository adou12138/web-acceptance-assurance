# 检出效果评测协议（v1.27.0）

目的：回答机械基线回答不了的问题——**同条件旧版/新版是否真的多发现问题、是否少误报、是否把该跑的分支跑完**。
本协议只定义"怎么测才可信"；未真实执行前结论保持"效果待验证"，工具产出不合格即判 fail，不产出"质量已达标"的自证。

## 一、答案隔离（先解决"执行方看得见答案"）

早期夹具把 `hidden_answers` 和题面放在同一个 JSON 里，执行方一打开就看见答案，盲测名存实亡。现在强制分离：

| 角色 | 拿到什么 | 绝不能拿到 |
|---|---|---|
| 评测者 | `docs/evals-baseline/detection-fixtures/*.json`（含答案、匹配规则、合法反例） | — |
| 执行方 | `blind_eval.py prepare` 生成的运行目录：`spec.json`（场景/输入/必选分支/交付格式）+ 空 `evidence/` | 答案文件、答案 ID、匹配规则 |

```bash
# 评测者侧：生成只含题面的运行目录
python scripts/blind_eval.py prepare \
  --fixture docs/evals-baseline/detection-fixtures/01-boundary-form.json \
  --out runs/01-A --run-id baseline-01

# 交付前自检：目录里混入答案/答案 ID/声明看过答案，都会被拒
python scripts/blind_eval.py verify --run runs/01-A --fixture <同一夹具>
```

`run.json` 记录 `fixture_sha256`、`protocol_sha256`、预算与 `executor_saw_answers=false`。执行方必须保持该字段为 false；改成 true 的运行不得计入盲测（计分直接拒绝）。

## 二、运行治理（让"A/B 对比"真的可比）

1. **同题面、同预算、同工具**：两组只允许版本不同；预算写进 `run.json`，超预算的结论单列。
2. **至少两次独立复跑**：执行方标识不同；同一执行方跑两次只能算一次（存在学习效应），`compare` 会告警。
3. **交错顺序**：先 A 后 B、再 B 后 A 各一轮，抵消"先跑的人更熟悉页面"的偏差。
4. **协议冻结**：`protocol_sha256` 写进每条结果；改判据即产生新哈希，旧结果不得与新结果混算（`compare` 直接标不可比）。
5. **留出场景一次性**：`99-holdout.json` 只在准出时跑一遍；跑过即退役并新生成一份，避免被针对性优化。

## 三、计分（只认可复核输入）

```bash
python scripts/blind_eval.py score --run runs/01-A \
  --fixture docs/evals-baseline/detection-fixtures/01-boundary-form.json \
  --findings runs/01-A/findings.json
```

执行方提交 `findings.json`：`branches`（每条必选分支的 EXECUTED/BLOCKED+原因）、`findings[]`（现象/观察/严重性/证据路径）、`obligations_untested`、`invalid_actions`。

计分规则（机械、可复算）：

- **检出**：按答案里的 `match_all`/`match_any` 词元匹配现象文本，同一答案只计一次，按 blocker/major/normal 分账。
- **误报**：命中 `legit_near_misses` 的记为误报；未命中任何答案也非合法反例的，计入 `unmatched_findings`（可能是真缺陷，不直接算误报，但需人工复核）。
- **分支**：分母只能来自答案键；出现题面之外的分支名（改名/缩分母）直接报错。
- **证据**：每条检出必须有真实存在且非空的证据文件，否则记 `evidence_issues`；`evidence_grade` 非 `E1`（模拟/未执行）的不计检出，见 `skills/case-generator/references/methodology/evidence_grading.md`。
- **未测义务 / 无效动作**：如实计数，进入判定。

产出 `score.json`，含 `verdict`：仅当 **blocker/major 全检出 + 误报 0 + 必选分支 100% 执行 + 检出全有证据 + 未测义务 0 + 无效动作 0** 才是 `pass`；有合理 BLOCKED 且显式传 `--allow-blocked` 时最多 `conditional`。

## 四、对照

```bash
python scripts/blind_eval.py compare --a runs/01-A/score.json --b runs/02-B/score.json
```

输出检出/分支/误报差值与风险提示：同执行方（学习效应）、协议哈希不同（判据变了）、夹具哈希不同（不是同一题）。**不可比的结果不做结论**。

## 五、五个场景与准出条件

1. `01-boundary-form.json`：边界表单（空/0/1/上界/上界+1、非法值、重复提交、失败恢复）。
2. `02-role-state-flow.json`：多角色审批状态流（越权、状态残留、状态边遍历）。
3. `03-async-cancel-network.json`：异步取消/重复点击/5xx/断网/读回一致性。
4. `04-ui-keyboard-copy-compat.json`：焦点顺序、未渲染变量、术语、内核矩阵。
5. `99-holdout.json`：留出场景，不参与开发，只在准出跑一次。

准出：dev 四场景与留出场景**分别** `pass`，且留出场景的 blocker/major 全检出；任一项 fail 或 `conditional` 即不准出。不允许"合并分母""挑有利场景"报结果。

## 六、反作弊与真实性

- 答案键只在评测者机器上；运行目录里出现答案文件或答案 ID，`verify` 直接拒。
- 计分不读执行方自报的"检出率"，只从 `findings.json` 与证据文件复算。
- 分支名必须来自题面，改分母/改名直接报错。
- 证据可以是截图、请求响应、读回结果，但必须能被第三方打开复核；伪造路径会被 `evidence_issues` 抓住。
- 工具本身不访问被测网站，也不替执行方"补"任何结论。

## 七、真实项目的逃逸缺陷回流

夹具只能证明"这几类缺陷能不能被抓到"，不能代表真实项目。上线后按以下方式补强：

1. 每个真实逃逸缺陷登记：需求、版本、漏测原因（无用例/用例空话/分支没跑/环境缺失/证据未核）、补的回归。
2. 同类逃逸累计 2 次以上，就沉淀为新的 `detection-fixtures` 场景（含合法近似反例），并同步 `match` 规则。
3. 用过的留出场景退役，换新场景，避免"针对已知答案调优"。

## 八、边界

受控预埋缺陷只计评测检出，不计真实项目 bug；一次夹具结果不外推到所有网站。真实"减少 bug"仍需项目级逃逸记录与版本对照。
