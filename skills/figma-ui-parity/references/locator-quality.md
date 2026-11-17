# 元素定位可信度（locatorQuality）

## 为什么单独拎出来

缺陷的 `element.selector` 同时承担两件事，而这两件事对选择器的要求正好相反：

1. **给人复现** —— 写清楚"点哪里"就够，`.btn-primary` 也能用；
2. **给机器跨轮认人** —— `normalize_evidence.fingerprint` 与 baseline 的 `fingerprint`
   都含 `selector + nth`，选择器一变，同一缺陷在下一轮就被算成**新增**。

第 2 件事决定了选择器质量会直接污染回归结论：`div:nth-child(3)`、`css-1x2y3z` 这类
定位在页面重构后必然变化，报告于是长出一批假回归；反过来真新增的缺陷也可能因为
nth 恰好撞上而被误判成旧的。

`scripts/locator_quality.py` 只做一件事：判出"这条定位有多可信"，写进
`element.locatorQuality`。

## 分级口径

| 等级 | 判据 | 例子 |
|---|---|---|
| `stable` | 语义锚点：`data-testid` / `data-test` / `data-qa` / `data-cy` / `aria-label` / `[role=` / `[name=` | `[data-testid=submit]` |
| `semi` | 单段 id、语义化 class、tag.class | `#submitBtn`、`button.btn-primary` |
| `fragile` | 纯通用标签名、构建期生成类名、结构伪类、依赖 `nth>0` | `div`、`.css-1x2y3z`、`li:nth-child(3)` |
| `none` | `selector` 为空——页面级缺陷，本就没有元素定位 | 性能指标、旅程落地页 |

`nth > 0` 表示靠序号区分同类元素，可靠度降一级（`stable→semi`、`semi→fragile`）。

## 使用方式

- **不参与缺陷判定**，也不进任何门禁：它回答的是"下次还认不认得出"，不是"是不是缺陷"。
- 报告的元素定位列在 `fragile` 时追加一个短标签「定位脆弱」，其余等级不标（标了只是噪音）。
- `element.locatorAdvice` 给可执行建议：补 `data-testid`，或用语义化 `role` / `aria-label`。
- 补入点只有两处，都是缺陷进报告的必经通道：`assemble_report._ensure_defect_shape`
  与 `normalize_evidence`。producer 不必各自填，producer 明确声明了的不覆盖。

## 已知局限（不要据此删缺陷）

跨轮匹配的降级策略（"fragile 的新增项先记为 suspected，不直接算回归"）需要改
`baseline_regression.cjs` 的匹配逻辑，属于独立改动。当前只做**标注**：
报告能看出"这条下次可能找不到"，但 baseline 该判新增还是判新增。
在改匹配引擎之前，不要指望 locatorQuality 能减少回归噪声——它只是让噪声**可识别**。
