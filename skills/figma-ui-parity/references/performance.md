# 性能与核心网页指标（7.40）

## 为什么单独成维度

「这个页面打开得快不快」是样式比对、DOM 盘点、截图 diff 都回答不了的问题——
它们只看"画出来的样子"，不看"画出来用了多久"。像素还原度再高，首屏 6 秒白屏，
用户照样骂。所以性能单独成维度，与还原度判定互不替代。

## 两阶段采集（为什么必须提前装观察器）

`scripts/performance_probe.js` 分两次执行：

| 阶段 | 时机 | 动作 |
|---|---|---|
| `install` | 页面加载**之前**（`page.addInitScript`） | 装 LayoutShift / LCP / longtask 观察器，挂到 `window.__vpPerf` |
| `collect` | 页面稳定后 | 读 `navigation` / `paint` / `resource` 条目 + 观察器累积值，出 findings |

**CLS 没有 buffered 入口**：采集时才装观察器，累积值永远是 0——那不是"没有布局偏移"，
是"根本没测"。所以 install 阶段结束后脚本会自己把 `__vpPerfMode` 切回 `collect`，
否则下次 evaluate 会再装一遍并返回 `{mode:"installed"}`（没有 findings）。

MCP `browser_evaluate` 路径没有 install 阶段，此时 `metrics.cls` 为 `null`、
`clsSource=unavailable`、`summary.skipped` 里记 `cls:no-observer`——**不得当作 0 判通过**。

## 阈值（Web Vitals 官方口径）

| 指标 | 良好上限 | 差阈值下限 | 说明 |
|---|---|---|---|
| TTFB | 800ms | 1800ms | `navigation.responseStart` |
| FCP | 1800ms | 3000ms | 首次内容绘制 |
| LCP | 2500ms | 4000ms | 最大内容绘制 |
| CLS | 0.1 | 0.25 | 只累加 `hadRecentInput=false` 的偏移 |
| TBT | 200ms | 600ms | 长任务超过 50ms 部分的累加（近似 Lighthouse 口径） |
| 资源传输总量 | 2MB | 5MB | `transferSize` 求和 |

INP 需要真实用户交互才能算，本轮不做，**不要把它写进结论**。

## 判定纪律（防假缺陷的核心）

1. **无网络节流时，耗时类指标只记 `warn`**。本机跑出来的 300ms LCP 换一台机器就是
   3 秒，绝对耗时与网络档、机器性能强相关。只有 `--throttle fast3g/fast4g` 真实生效
   （CDP `Network.emulateNetworkConditions` 调用成功）时，超「差阈值」才 `confirmed`。
   节流设置失败时驱动会把 `applied` 改回 false 并带 `error`，结论随之降级。
2. **LCP 采集时刻可能未 finalize**（真实 finalize 在首次交互或页面隐藏），产物固定标
   `lcpFinalized=false`，判定因此**偏乐观**——超阈值的可能是真的，达标的不代表最终达标。
3. **体积类指标与网络档无关**，可以直接确认：单图解码体积 > 200KB 且压缩率 < 5%
   （未开 gzip/br）→ `confirmed`；资源总量 > 5MB → `confirmed`。
4. 一条可判定指标都没有（navigation 与 paint 均空）→ `dataMissing`，不是 `pass`。
5. 全部达标时必须产出**一条 `status=pass` 的汇总证据**，否则该维度会退化成 `notRun`
   （「测过且没问题」与「没测」在 checks 里长得一样）。

## 与其它维度的交叉

- **7.24 图片适配**：本维度看"图片多大 / 压没压"，7.24 看"图片比例、裁剪、加载失败占位"。互补。
- **7.31 滚动 UX**：滚动卡顿往往来自长任务，本维度的 `tbt` 与 `longTaskCount` 是量化证据。
- **7.33 接口异常与慢网**：7.33 是**主动拦造 500**看界面表现，本维度是**在真实网络档下**量耗时。

## 7.44 交互首响应时延（同一维度的交互面）

页面加载快不代表点下去有反应。7.44 测「点击到首个实质变化」的时间，由按钮审计顺带采：
不需要额外跑一遍页面，点击本来就要做。

```bash
node scripts/node/run_button_audit.cjs --url "https://目标页" --out ./parity-test --click \
  [--response-timeout 3000] [--response-warn 1000] [--response-fail 2000] [--no-measure-response]
```

| 首响应 | 结论 |
|---|---|
| < 1000ms | 不产 finding（只记进 `responseTime` 概览） |
| 1000 ~ 2000ms | `warn` P3 |
| >= 2000ms | `confirmed` P2 |

**退化检测**：页面若一直在改 DOM（流式输出、光标闪烁、轮播），MutationObserver 会把「下一帧无关变更」
当成响应，样本全是 0ms。这不是「响应很快」而是**没测到**——此时只产出一条
`dataMissing`（`response-measure-unreliable`）并停止逐条结论，不再写 20 行 0ms 数据。

**口径说明（重要）**：这不是 INP。INP 需要真实用户采样与按次分组，本 skill 没有 RUM 数据。
这里测的是 Playwright 轮询到的「首个实质变化」，比 INP 粗，但可复现。
结论写成「首响应 xx ms」，不冒充 INP 数值。

**怎么判「有反应」**：在点击**之前**装 `MutationObserver`，记录第一条 DOM 变更的时间戳，
并与 URL 变化、请求发出三路信号取最早。

不用「轮询整页签名 + 阈值」的原因（实测踩过）：最初按「节点数变化 >= 3 / 文本长度变化 >= 10」
压噪声，结果点一下只弹一个 6 字 toast（+1 节点）——**真实响应被阈值压掉**，
两个按钮全判成「无响应」，比不测更糟。DOM 变更由浏览器自己告知，不需要猜阈值。

代价：页面若有与交互无关的周期性 DOM 更新（时钟、轮播），测量会偏乐观。
这个偏差是**单向**的（只会把「慢」测成「快」），不会把「没反应」测成有反应。

产物 `<out>/response_findings.json` 是 findings 形状，管线通过 `--findings` 消费；
`button_audit.json` 里另有 `responseTime` 概览（p50 / p90 / max / 超阈值计数）。

## 命令

```bash
node scripts/node/run_performance.cjs --url "https://目标页" --out ./parity-test \
  --throttle fast3g          # none / fast3g / fast4g；默认建议 fast3g 或 fast4g
```

产物 `parity-test/performance.json` 是 findings 形状，管线通过 `--findings` 直接消费，
`assemble_report.py` 会把它汇总成 7.40 的 checks 与缺陷条目。

## 自检

```bash
node scripts/tests/performance_probe_smoke.cjs
```

桩里真跑 install → 触发 → collect 三个环节，锁住两条防假缺陷口径：
无节流只能是 `warn`、未装观察器时 CLS 必须是 `null`。
