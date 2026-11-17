# 7.43 键盘可达性与焦点顺序

## 覆盖范围

只回答两个问题：**Tab 能不能走到**、**走过去的顺序对不对**。

| 判据 | 属性 | 结论 |
|---|---|---|
| 可见且 `tabindex >= 0`，Tab 循环后从未获得焦点 | `kb-unreachable` | `confirmed` P2 |
| 存在 `tabindex > 0` | `kb-positive-tabindex` | `confirmed` P3 |
| 焦点顺序回跳（中间没有循环回落） | `kb-order-mismatch` | `confirmed` P2 |
| 连续两次 Tab 停在同一元素 | `kb-focus-stuck` | `pending` P3 |
| 弹层打开时焦点跑到弹层外 | `kb-dialog-escape` | `pending` P3 |
| Tab 落在未枚举的元素上 | `kb-unplanned-focusable` | `pending` P3 |

## 为什么顺序要逐次按 Tab

`tabindex=2` 会把焦点顺序从 DOM 序改成数值序，视觉上完全看不出来，截图也拍不到。
只有真的按 Tab、逐次读 `document.activeElement` 才能发现。这不是可以用静态检查替代的事。

## 安全边界

- 只按 **Tab**，不按 Enter / Space，不提交任何表单 → `safe` 策略下即可执行
- `staticOnly` 策略：不启动浏览器，直接 `notRun` 并写明「键盘可达性需真实按键才能观测」

## 采样协议

页面脚本（`keyboard_order.js`）枚举期望序列并给每个元素打上 `data-vp-kb` 序号；
驱动负责按键，只做「按一次、读一次」：

```
plan()   → 期望序列（可见 + tabindex>=0 + 未禁用）
Tab × N  → record() 每次读 activeElement 的序号
finish() → 比对并产出 findings
```

按键次数 = `min(120, 2 × 期望数 + 4)`：第一轮拿顺序，多出来的用于确认能循环回起点。

## 循环与回跳的区分

从最后一项回到第一项（中间没经过 `body`）是正常的 Tab 循环，**不算顺序错误**。
只有「中间位置往回跳」才是缺陷——这条区分不做，每个页面都会报一条假顺序问题。

## 已知缺口

写进产物 `coverageNote`：

- 弹层内焦点陷阱只在页面加载时已有弹层的情况下生效；要验「点开后是否困住焦点」，
  用 `--open "<CSS 选择器>"` 指定触发元素
- iframe / shadow DOM 内的可聚焦元素枚举不到，会记成 `kb-unplanned-focusable` 而不是静默漏掉
- 焦点是否**可见**由 7.29 判定，本维度不看样式
- 关闭弹层后焦点是否归还到触发元素：需要「打开→关闭」两步交互，不在本维度

## 命令

```bash
node scripts/node/run_keyboard_order.cjs --url "https://目标页" --out ./parity-test \
  [--open ".edit-btn"] [--max-tabs 120]
```

产物：`keyboard_order.json`（`findings` 会被 `assemble_report.py --findings` 消费）。
