# 可访问性规则合规（7.41）

## 与 7.19 的分工（不要混为一谈）

| 维度 | 回答的问题 | 首次运行 | 缺基线时 |
|---|---|---|---|
| 7.19 可访问性树 diff | 相对上次，语义结构**变没变** | 只能存快照 | `dataMissing` |
| 7.41 规则合规 | 这一版**合不合规** | 直接给 `pass/fail` | 不依赖基线 |

7.19 的价值在"回归"（结构悄悄少了一个 aria-label 能被 diff 抓到），但它**回答不了
"合不合规"**——`aria-diff.md` 明确写着"具体断言仍由清单规则判定"，而那些规则是文字
约定、不是可执行断言。7.41 补的就是这一块：单次运行就能出结论，不需要历史基线。

## 内置规则集（15 条，纯计算，零外部依赖）

`scripts/a11y_audit.js` 在页面内执行，判据全部可机械复核：

| 规则 id | WCAG | 判据 | 等级 | 状态 |
|---|---|---|---|---|
| `img-alt` | 1.1.1 (A) | `img` 缺 `alt` 属性（`alt=""` 合法，装饰图） | P2 | confirmed |
| `button-name` | 4.1.2 (A) | 按钮无可见文本 / aria-label / aria-labelledby / title | P2 | confirmed |
| `link-name` | 2.4.4 (A) | `a[href]` 无任何可访问名称（纯图标链接最常中） | P2 | confirmed |
| `form-label` | 1.3.1 / 3.3.2 (A) | 控件无 `label[for]` / 包裹 label / aria-label | P2 | 有 placeholder → pending |
| `heading-order` | 1.3.1 (A) | 标题层级跳级（h2 → h4） | P3 | pending（可能是视觉需要） |
| `html-lang` | 3.1.1 (A) | `html` 无 `lang` | P2 | confirmed |
| `landmark-main` | 1.3.1 (A) | 无 `main` / `[role=main]` | P3 | pending |
| `role-valid` | 4.1.2 (A) | `role` 不在 WAI-ARIA 合法角色表 | P3 | confirmed |
| `aria-hidden-focus` | 4.1.2 (A) | `aria-hidden=true` 容器内仍有可聚焦元素 | **P1** | confirmed |
| `tabindex-positive` | 2.4.3 (A) | `tabindex > 0`（打乱 Tab 顺序） | P2 | confirmed |
| `color-contrast` | 1.4.3 (AA) | 文本对比度 < 4.5:1（大字 < 3:1） | P2 | 见下 |
| `duplicate-id` | 4.1.1 (A) | 文档内 `id` 重复 | P3 | confirmed |
| `aria-label-empty` | 4.1.2 (A) | `aria-label=""`（会覆盖自身名称） | P2 | confirmed |
| `label-for-dangling` | 1.3.1 (A) | `label[for]` 指向不存在的 id | P2 | confirmed |
| `input-type-missing` | 1.3.1 (A) | `input` 未声明 `type` | P3 | pending |

单条规则最多产出 8 条 finding，`summary.truncated` 记录真实总数——**截断必须留痕**，
否则 30 张缺 alt 的图只看到 8 条，会以为"就 8 个问题"。

## 对比度的背景回溯（最容易误报的地方）

`computedStyle.color` 只给前景，背景要往上找第一个不透明 `background-color`。找不到时：

| 页面模式 | 判定 | 置信度 | 理由 |
|---|---|---|---|
| 有实测不透明背景 | `confirmed` | 0.8 | 证据完整 |
| 回溯落空 + 浅色页面 | `confirmed` | 0.65 | 浏览器默认白底，假设基本成立 |
| 回溯落空 + `prefers-color-scheme: dark` | `pending` | 0.45 | 白底假设会把深底当白底，批量误报 |

背景是图片/渐变时回溯必然落空，这一条**永远只能到 pending**——不要去猜图片的平均色。

## axe 通道（可选，补强覆盖）

```bash
node scripts/node/run_a11y_audit.cjs --url "https://目标页" --out ./parity-test \
  --axe ./node_modules/axe-core/axe.min.js
```

注入后按 `wcag2a / wcag2aa / wcag21a / wcag21aa` 规则集跑一遍，结果合并进同一份
findings（`engine=axe+builtin`，规则名带 `axe-` 前缀）。axe 加载失败时产物里
`axe.status=failed` 并带原因，**内置结果照常出**——不能因为补强通道挂了就整维 notRun。

## 覆盖边界（必须随结论一起给出）

内置 15 条是 WCAG 2.1 A/AA 的**子集**，产物里固定带 `coverageNote`。**未覆盖**：
键盘可达性与焦点顺序、动态内容播报（live region）、屏幕阅读器实测、缩放 200% 重排、
动效可关闭、字幕与音频描述。要这些结论必须走 axe 或人工复核，**不得声称"通过 WCAG AA"**。

## 自检

```bash
node scripts/tests/a11y_audit_smoke.cjs
```

用最小 DOM 桩真跑规则集，锁住：对比度比值计算（#999 白底 ≈ 2.85:1）、
背景推断在浅色可确认 / 暗色必须 pending、`aria-hidden-focus` 为 P1、单规则截断留痕。
