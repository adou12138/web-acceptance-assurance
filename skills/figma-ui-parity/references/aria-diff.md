# B4 可访问性树 diff（借鉴 Argos）

## 为什么需要

清单 7.19 把"可访问性"当手检项，但像素 diff 抓不出**语义层**回归：缺 `aria-label`、role 错、焦点顺序乱、只读控件无 name。Argos 把可访问性树当文本快照做 diff，能确定性地发现这类问题。

## 引擎

`scripts/capture_aria.js`（复用 `playwright-core` 解析，走有头/持久 profile 同 `run_official_extraction.cjs`）：

```bash
# 抓取当前页可访问性快照
node scripts/capture_aria.js --url "https://目标页" \
  --profile "./.vp_profile_devtest" --out "./parity-test/aria.json"

# 两次运行结构 diff（role + name 树）
node scripts/capture_aria.js --diff ./parity-test/aria_last.json ./parity-test/aria_this.json
```

输出：`{ aCount, bCount, added[], removed[], summary }`。`added` = 本次多出（或 name 变了）的可访问节点；`removed` = 上次有本次无。

## 集成到清单 7.19

- 每次运行在维度 7.19 之前抓一次 `aria.json`，存入工作目录。
- 若上次要跑过（有 `aria_last.json`），自动 `--diff` 产出 a11y 结构变化，写入报告「可访问性」章节。
- 首次运行无 baseline → 仅存快照、标注"首次，无对比基线"。
- 具体断言（icon-only 按钮命名、Tab 顺序、焦点可见性）仍由清单 7.19 的规则判定，`capture_aria` 负责"结构是否变了"的确定性结论。

落账通道：把 diff 结果存成 `parity-test/aria_diff.json`（首次只有快照则存 `aria.json`），管线在 `--report` 分支自动交给 `struct_checks.py --aria-diff`，产出 7.19 条目：

| 输入 | 判定 | 理由 |
|---|---|---|
| diff 的 `added` 与 `removed` 都为空，且 `aCount`、`bCount` 均 > 0 | `pass` | 两次都快照到节点、结构确定性未变 |
| 任一非空 | `pending` | 纯装饰节点进出可访问性树通常无害，也可能是无障碍回归，必须人判 |
| `added`/`removed` 为空但节点数为 0 或缺失 | `dataMissing` | 两次都没抓到节点时，空 diff 只说明抓取失败，不能证明"结构未变" |
| `added`/`removed` 形状异常（非数组/null） | `dataMissing` | 字段坏掉不等于没有变化，不能当 `pass` |
| 只有快照、没有 diff | `dataMissing` | 无基线就没有"变没变"的结论，不能当 `pass` |

`added`/`removed` 里的每个节点都会带上 `role` 与 `name`，并给出 `reproduction`（重跑 diff 对比该节点），人工只需确认"该节点是否应有可访问名称/角色"。

## 边界

- `accessibility.snapshot()` 对隐藏/disabled 节点有自身规则（部分不计入），与清单口径一致即可，不必强求完全一致。
- 纯装饰性节点（纯 `<img alt="">`、分隔）的变化通常无害，落入 `added/removed` 后由人工在报告中确认是否需跟踪。
