# 标准工作流细则（1–9）

> `SKILL.md`「标准工作流」只留每阶段的执行骨架与硬判据；本文件是各阶段的完整口径、脚本行号级判据与实测教训。
> **执行到某阶段时再打开对应小节**，不要通读。章节号与 `SKILL.md` 的阶段编号一一对应，`references/` 里出现的「SKILL.md 步骤 N」仍指向同一阶段。

## 1. 建立运行清单

`run-manifest.json` 必须记录：输入 URL（脱敏）、时间、版本、视口、依赖状态、执行阶段、脚本哈希、未运行原因，以及 `safetyPolicy`（`all` / `safe` / `mockWrites` / `staticOnly`）、`userSafetySelection`、`writeInterception`（`none` / `mock` / `abort`）、`testEnvironmentConfirmed`（选择 `all` 时必须为 `true`）、由安全策略导致的未执行维度；报告从该清单读取并展示这些字段。

指定了执行档位（`--profile smoke|standard|full`，表在 `assets/run_profiles.json`）时一并记录 `runProfile`：档位只决定"本轮打算跑哪些维度"，未纳入的维度仍记 `notRun` 并写明原因，覆盖率照实下降。本轮的写操作台账固定在 `PARITY_WORK_DIR/write_ops_ledger.json`。路径相对当前 workspace：

```text
PARITY_WORK_DIR=./parity-test
REPORT_DIR=./parity-test/reports
BASELINE_DIR=./parity-test/baseline
AUTH_DIR=./parity-test/.auth
```

## 2. 设计侧建模

Figma URL 必须优先路由到已连接的 Figma MCP，调用设计数据接口获取 file/node 数据；禁止先用普通网页抓取替代设计数据。

解析 `node-id` 时先读取节点类型、名称、尺寸和父级关系：若 URL 节点是 COMPONENT、INSTANCE、子 FRAME 或尺寸明显小于页面，不得直接当作页面设计稿；先对文件做浅层枚举（`depth=1/2`），收集 Page 下的顶层页面 FRAME，再按页面名称、URL 路径和页面指纹建立映射。只有确认节点是页面级 FRAME，或唯一映射到页面级 FRAME 后，才进入逐字段对照。文件响应过大时必须采用"浅层枚举 → 按页面 FRAME 分片读取"，不得退回单个子节点并判定设计稿缺失。只有在 MCP 不可用时，才可使用用户提供的导出 JSON/PNG，并将受影响阶段标记为 `partial` 或 `dataMissing`。

按 `references/figma-fields.md` 判定字段可比性，用 `scripts/extract_design_fields.py` 把节点 dump 转成设计模型：坐标统一为相对 frame 原点；颜色、圆角、边框、阴影、渐变换算为 CSS 可比单位；`fontWeight` 由名称映射为数值；`lineHeight` / `letterSpacing` 的 `PERCENT` 换算成 px；多阴影保留顺序与内外类型不合并；文本以 `characters` 为准；`constraints` 保留用于多视口断言。

## 3. 页面盘点与对应性预检

登录态按 `references/login-detect.md` 多信号检测。若页面需要登录且没有有效 storage state，必须直接执行 `scripts/node/save_auth.cjs --url "<受保护页面>" --profile "<workdir>/parity-test/.vp_profile" --headed` 启动可见浏览器，等待用户手动登录；不得把无头 MCP 的登录页当作终点，也不得要求用户复制密码、验证码或 Cookie。登录成功后保存 `.auth/auth.storage.json`，再使用同一 profile 和认证目录执行提取。若有头脚本启动失败，记录明确缺失项并停止受保护页面阶段。

用 `scripts/collect_page_inventory.js` 盘点页面资产（按钮、输入、图片、浮层、滚动容器、交互种类），**宽高同时为 0** 的元素不入库（`collect_page_inventory.js:25` 判据是 `width === 0 && height === 0`；单边为 0 的元素仍会入库），每个元素记录 `nth`。

再按 `references/page-design-match-check.md` 生成设计指纹与页面指纹：标题、路由、导航、区块、字段、表头、弹窗标题、关键按钮。设计稿存在多个顶层 Frame 时，必须先输出 Frame 清单，再依据名称和 URL 路径自动配对；例如"通讯录"↔`addressbook.aspx`、"管理员·管理"↔`qywxusermanage.aspx`、"成员·作答"↔`contactsanswer.aspx`、"其他设置"↔`addressbooksetting.aspx`。输出 `matched`、`suspected` 或 `mismatch`。

仅当自动映射无法唯一确定时，才用 `AskUserQuestion` 请求选择；不得因为 URL 的 `node-id` 指向子组件就直接判定设计稿不足。`mismatch` 必须询问用户后才能继续；`suspected` 顶部警告；对象不匹配时不得产生正式缺陷结论。设计稿与页面尺寸不一致时，优先把浏览器调整到设计 Frame 的宽高做一比一对照，再补充用户指定的实际视口；不得在不同宽度下直接作页面级尺寸/位置结论。

## 4. 逐字段比对与双向盘点

用 `scripts/compare_fields.py` 比对设计模型与页面提取结果，配对顺序为：显式 mapping → 精确文本锚点 → 名称提示（文本节点只与带文本的页面项配对，避免与同名容器错配）。

- **坐标基准必须先归一**：设计坐标相对 frame，页面坐标相对文档。脚本按配对元素的中位偏移自动归一（或由 `--frame-origin` 显式给出），并在结果里输出 `positionBasis`。
- **可比性检查**：页面视口宽与设计稿宽比例偏差 > 2% 时，**几何类属性（位置 / 尺寸 / 宽高）标记 `notComparable`**——它们随缩放整体漂移，比对无意义；**样式类属性（颜色、文案、字号、字重、圆角、边框、阴影、透明度、行高、字距）仍参与比对**，容差内记 `withinTolerance`、超差记 `confirmed`。不得因视口不一致而整页放弃比对：那会让「文案写错」「主色错成蓝」这类与本场景无关的真缺陷一并消失，而 `notComparable` 下游记为 `dataMissing`、不计入已执行，等于整页空转。可比性结论必须写明原因，几何差异不得当成缺陷。
- **属性级容差**：颜色用 ΔE（≤3）、尺寸 ±1px、位置 ±2px、字距 ±0.5px、圆角 ±1px、阴影模糊 ±2px。容差内记 `withinTolerance`，不生成缺陷。
- **透明背景不参与比对**：`rgba(0,0,0,0)` 不是黑色，必须跳过，否则会把每个无背景元素误报为颜色不符。
- 输出 `unmatched.designOnly` 与 `unmatched.pageOnly`，作为双向盘点（7.23）输入。
- 按钮缺失判定必须采用双通道证据：先查页面按钮盘点，再查可见完整文本。设计按钮命中按钮盘点时记录 `matchedBy=buttonInventory`；仅命中完整文本时记录 `matchedBy=fullText` 并标记 `nonStandardEntry=true`；只有两个来源都未命中时才记录 `matchedBy=none` 并进入 `missingInPage`。**该双通道判定落地在 `scripts/node/run_button_audit.cjs`（`matchedBy=buttonInventory` 见 `:200`、`fullText`+`nonStandardEntry` 见 `:209`、`none`→`missingInPage` 见 `:213`），不在 `compare_fields.py`。**

`constraints` 断言用 `scripts/assert_constraints.py` 执行：LEFT/TOP 位置不变、RIGHT/BOTTOM 边距不变、CENTER 中心不变、SCALE 比例不变（±2%）、FIXED 宽度不变。

## 5. 场景生成与执行

用 `scripts/discover_scenarios.py` 生成元素级场景矩阵（契约见 `references/scenario-contract.md`）：

- 场景必须绑定到具体元素、动作、预期与断言，禁止只输出泛化状态列表。
- 覆盖：默认态、Hover、Focus、Disabled、Loading、空态、错误态、成功态、弹窗、下拉、Tooltip、滚动、刷新、路由往返、窄屏、长文本、多语言、密集数据、暗色模式、打印、接口异常、角色态。
- 每个输入框生成异常数据场景：空值、单字符、超长中文、超长英文、中英混排、数字符号、emoji、全角、多行、长链接、标签字符、极端数值。
- 页面多出的元素仍必须验证可用性，不得因设计稿未画而跳过。
- 元素级与数据级场景有数量上限，被截断时必须在报告里写明，不得静默丢弃。

静态探测用 `scripts/extra_checks.js`（只做无副作用探测，不点击不提交），其**实际覆盖的维度是 7.20 / 7.24 / 7.25 / 7.27 / 7.28 / 7.29 / 7.30 / 7.31**：图片比例与加载失败、暗色模式、多行截断、控件细节、焦点可见性、国际化、滚动 UX、加载残留。**7.26 打印视图、7.32 状态残留、7.33 接口异常、7.34 角色差异不在该脚本内**，须由场景驱动（`run_state_scenarios.cjs`）执行——不要把「7.24–7.34」整段默认为它已覆盖。

状态类场景（暗色模式 7.25、打印视图 7.26、状态残留 7.32、接口异常 7.33）由 `scripts/node/run_state_scenarios.cjs` 驱动执行：先用 `page.emulateMedia` 切换渲染模式或 `page.route` 拦载 XHR/fetch fulfill 500（请求不到服务端、零真实写），再用 `scripts/state_probes.js` 在切换后的状态里重新探测。产物为每个维度给出 `pass/fail/pending/notRun` 状态与 findings；`staticOnly` 策略下不做页面交互，相应维度填 `notRun` 并写明原因。判定口径（哪些记 confirmed、哪些记 pending）见 `references/bug-driven-checks.md` 对应维度节。

跨页用户旅程（7.39）由 `scripts/node/run_journeys.cjs` 驱动：从起始页实时提取同源候选链接（危险词、跨源、锚点、下载类、新标签页均被剔除，`isCandidateLink` 会给出跳过原因），BFS 有界点击走查，每步采集运行期观测并判定落地页缺陷（404/500、落地空白、链接无反应、返回中断、中途弹登录），判据与安全边界见 `references/journeys.md`。被剔除的链接按原因汇总进产物（`skippedLinks` 段，含跳过原因计数与样例）；每步点击后 1200ms 有界等待 + 稳定化，不在异常页面挂死。

维度 7.35–7.38（运行期 JS 异常、交互期网络失败、重复提交、元素遮挡）用 `scripts/runtime_observability.js`：在交互前安装钩子、交互后采集，见 `references/runtime-observability.md`。仅当本轮做了交互并安装了钩子才算执行过，否则填 `notRun` 并写明原因。

场景数量与组合：`scripts/discover_scenarios.py` 默认按单点生成（`--combo-depth 1`）；`--combo-depth 2` 只加深**两类**组合：异常数据条数（`data_cases+3`）与逐断点展开（7.4 逐断点、异常输入 × 首尾断点，需 ≥2 个断点才生效）。

**「state×viewport×异常数据」的说法不准确**：状态类场景里 7.25 暗色、7.26 打印、7.33 接口异常**无条件生成**（`discover_scenarios.py:244/248/251`），与 combo-depth 无关；**7.32 状态残留不是无条件**——仅当识别到 `tabs / table / pagination` 交互（`:255`）或缺陷池命中 `7.32`（`:260`）时才生成，页面无此类元素时该维度不出场景。元素级场景按风险排序（删除/写操作、无名称图标按钮优先）也**无条件执行**，`combo=1` 同样排序。

**缺陷模式池**（`--patterns assets/defect_patterns.json`）：历史高频翻车的维度即使被页面内容门槛挡住也会补出页面级场景（`source=pattern-deep`）、表单校验类高发时加深异常数据，产物 `patternFeedback` 写明加权依据；该模式只影响覆盖与深度，不自动生成断言。不传 `--patterns` 时**场景清单本身**与接入前逐条一致——产物里的 `patternFeedback` 字段恒在，未接入时 `applied=false` 并附说明，那是输出结构的差异、不是场景差异。

**管线默认接入两道防遗漏门禁**：`plan_run`（stabilize 之前）按 `assets/dimension_manifest.json` 对 45 维度逐个排程——auto 维度直接排，manual 维度必须带合规豁免理由（黑名单：待定/TODO/过短），不合规 rc=2 **当场熔断**；`pre_assemble_gate`（assemble 之前）双向对账——计划产物必须存在且含 requiredKeys（空壳假跑拦截），run 目录里已产出的已知产物不在 assemble 入参 = 漏传拦截（实测漏传产物吞掉 confirmed 缺陷的教训固化）。门禁熔断判定 notRun 与 failed 同样算未通过。手工跑驱动的场景也必须先出 run-plan 再开跑。

**管线默认已接入模式池**：`run_parity_pipeline.py` 现在默认把 `--patterns` 指向 `assets/defect_patterns.json`（`--no-patterns` 可关），并在 `generate_reports` 之后追加 `pattern_pool --merge` 阶段，把本轮缺陷按指纹并回池（幂等，无新模式不写盘）。因此**走管线时模式池加权默认生效**——池里现有 31 条 DP-* 模式，会按历史缺陷维度补场景（如 7.31 滚动 UX）。手工逐个调 `discover_scenarios.py` 仍是不传则不生效，两条路径的口径差异就在这。`--combo-depth` / `--roles` / `--breakpoints` 也可由管线透传。

**场景执行率（计划 vs 实际）**：覆盖率门禁统计的是「维度是否跑过」，**逐条场景有没有跑只能看 `scripts/scenario_coverage.py`**——把 `scenarios.json`（计划）与执行结果（显式 `--results` 文件，或从缺陷项的 `scenario` 字段 `--from-report` 自动回收）合成执行率，未执行项必须显式写明原因，`--enforce` 拦截"未写原因的未执行"与低于 `--min-rate` 的执行率，`--exempt-dimensions` 显式豁免并单列。`run_parity_pipeline.py` 在 `scenarios.json` 存在时自动生成该产物并纳入报告（默认不带 `--enforce`，需要硬门禁时显式加）。产出形状见 `schemas/scenario-coverage.schema.json`，用法见 `references/scenario-contract.md` 第五节。

## 6. 十层检查

1. **像素层**：整页与区块 screenshot diff，按 `references/visual-parity.md`；截图前先跑 `scripts/stabilize.js`（`references/stabilization.md`）；差异量化用 `scripts/diff_score.js`（`references/diff-scoring.md`）。设计稿导出倍率与页面截图 `deviceScaleFactor` 应保持一致——**尺寸不等时 `diff_score.js:226` 直接返回 `size mismatch` 拒比**（CLI 侧以 `exit 2` 结束），既不缩放后硬比、也不标记 `notComparable`。因此倍率不一致的表现是"这次像素比对没跑成"，而不是"比出一大堆差异"。确需只比对公共区时显式传 `--crop-to-common`：裁剪事实与未比对区域会写进产物留痕（默认不裁，裁了就不是整页结论）。回归 diff 噪声偏高时可加 `--enhanced`（`run_parity_pipeline.py --enhanced-diff`）：抑制抗锯齿与孤立噪点，抑制量写进 `suppressed` 留痕、遮罩画淡黄；**该模式口径与基础模式不同，跨模式比基线前先确认基线同模式**。
2. **样式层**：逐字段比对结果按 `references/extraction.md` 与 `references/figma-fields.md` 复核；图标成对精确比对按 `references/icon-diff.md`。
3. **行为层**：按 `references/button-audit.md` 做三层判定（静态可见 / 可交互 / 结果生效）；弹层打开后必须补充盘点。落地脚本 `scripts/button_audit.js`、`scripts/node/run_button_audit.cjs`、`scripts/gen_button_report.cjs`。
4. **健壮性层**：响应式按 `references/responsive.md` 与 `assert_constraints.py`；溢出、遮挡、层级、键盘、焦点、ARIA 按 `references/aria-diff.md` 与 `references/bug-driven-checks.md`；长文本与异常数据按该文件的断言细则执行。
5. **运行期层**：交互前安装 `scripts/runtime_observability.js` 钩子、交互后采集——7.35 运行期 JS 异常、7.36 交互期网络失败（含 HTTP 200 + 业务失败 / 错误页伪装）、7.37 重复提交、7.38 元素遮挡、7.45 请求载荷完整性（占位符泄漏 / 同名参数重复 / 写请求零参数），判据与安全边界见 `references/runtime-observability.md`。未安装钩子或本轮无交互时，相应维度填 `notRun` 并写明原因。
6. **性能层**：`scripts/node/run_performance.cjs` 用 `addInitScript` 在页面加载前装观察器，采集 TTFB / FCP / LCP / CLS / TBT / 资源体积（7.40）。**CLS 没有 buffered 入口，采集时才装必然拿不到**；无网络节流时耗时类只记 `warn`，不得据本机数据确认缺陷。交互侧的首响应时延由 `scripts/node/response_timer.cjs` 在按钮审计里顺带采（7.44：>=2000ms 才算缺陷，1000~2000ms 记 warn），它**不是 INP**，汇报时必须写成"首响应 xx ms"。见 `references/performance.md`。
7. **合规层**：`scripts/node/run_a11y_audit.cjs` 跑内置 15 条 WCAG 2.1 A/AA 子集规则（7.41），首次运行即可出结论，不依赖历史基线；与 7.19 的"结构变没变"互补而非替代。内置规则是子集，**不得声称"通过 WCAG AA"**；需要更全覆盖时传 `--axe`。见 `references/a11y-rules.md`。
8. **设计侧层**：`scripts/design_contrast.py` 直接在设计模型上算文本对比度（7.42），评审阶段就能报出「这行灰字读不清」。它是 7.41 的另一端：页面侧只能事后发现，设计侧在写代码前就能发现。底色为图片/渐变/半透明时降级 `pending`，**不按白底硬判**。见 `references/design-contrast.md`。
9. **键盘层**：`scripts/node/run_keyboard_order.cjs` 只按 Tab、逐次读 `document.activeElement`（7.43）——正 tabindex 造成的顺序错乱截图上完全看不出来，只有真按键才能发现。只按 Tab 不按 Enter，无副作用。见 `references/keyboard-a11y.md`。
10. **回归层**：按 `references/baseline-regression.md` 与**已确认基线**比较，输出新增、已修复、持续、波动和无法比较项。未确认的临时基线只作参考，不据此判定方向。缺陷定位的可信度按 `references/locator-quality.md` 标注，脆弱定位的新增项在跟进时要先确认"是真新增还是选择器变了"。跨轮稳定性用 `scripts/flaky_report.py` 单独量化（只读历史快照）：**A 轮 fail=3 / B 轮 fail=3 可能是同一批缺陷，也可能完全换了一批**，后者说明判定口径不稳，先修口径再报缺陷。见 `references/flaky-analysis.md`。

## 7. 交互安全策略

分级细节与误报防线见 `references/button-audit.md`。本阶段只能执行第 0 步已取得的用户选择，不得默认启用任何交互：

- `all`：用户已明确确认当前为隔离测试环境；允许执行 safe、caution、danger 全部操作，包含真实写入、删除、清空、支付、退款、解绑和下线。每个 caution/danger 动作必须登记到写操作台账（`scripts/write_ops_ledger.py`，`reversible` 必填）并记录操作前状态、目标元素、请求/响应摘要、操作后状态与回收方式；收尾按 `--plan` 执行清理、`--mark-clean` 回填，未清理项由 `--merge-pending` 自动进报告「待确认」。**不登记等于环境残留不可追**，见 `references/write-ops-cleanup.md`。若页面不是隔离测试环境，必须拒绝该模式并回到安全策略选择。
- `safe`：仅允许 safe 分级操作（查询、查看、展开、收起、分页、取消、下载、复制），真实点击后必须还原。
- `mockWrites`：允许 safe 分级操作；caution 操作必须先拦截并 **abort** 所有非 GET/HEAD/OPTIONS 请求，只观察前端反应。**不造假 200**：假响应会让「点击有效」的判定失真（页面对假响应的反应被当成真实能力）；报告须注明已放行的初始化写请求数（`initAllowedCount`）。
- `staticOnly`：不执行任何交互，仅做静态可点击性、样式、DOM、ARIA、截图和无副作用探测。
- `danger`：在 `all` 且 `testEnvironmentConfirmed=true` 时允许点击和执行；必须保留操作前后证据并记录不可逆风险。其他策略下不点击，只做静态检查并标记人工或隔离测试环境验证。

若用户未选择，安全策略为 `unselected`，所有交互相关维度必须标记 `notRun:未获得本次安全策略授权`，不得以默认安全模式代替确认。选择 `all` 但未记录 `testEnvironmentConfirmed=true` 时，必须拒绝执行 caution/danger 操作并将其标记为 `notRun:未确认隔离测试环境`。命中测试须多点采样，记录 `selector + nth`，处理 dialog/下载/新页，点击后清理副作用。隐藏容器内尺寸为 0 的按钮记为 **`zero-size`**（`button_audit.js:76/115/169`，归入 `hidden` 列表，产物输出 `hidden` / `hiddenCount`；`notObservable` 一词当前不在产品代码里使用），重复列表按钮按组抽样并记录 `duplicateCount`。

## 8. 缺陷归一、去重与校验

每条缺陷必须携带维度号（7.x）、现象、设计值、实际值、复现步骤、页面定位、证据来源与验证状态，结构遵循 `schemas/defect.schema.json`：

```json
{
  "fingerprint": "page|scenario|element|dimension|viewport",
  "dimension": "7.1",
  "severity": "P0|P1|P2|P3",
  "confidence": 0.0,
  "status": "confirmed|pending|notRun|dataMissing",
  "symptom": "用户可观察到的现象",
  "expected": "设计或交互预期",
  "actual": "实际结果",
  "module": "保存按钮",
  "category": "7.1|color",
  "suggestion": "可执行的修复建议",
  "element": {"selector": "…", "nth": 0, "bbox": {"x":0,"y":0,"w":0,"h":0}, "anchor": "…", "frame": "main"},
  "designRef": {"nodeId": "…", "path": "…"},
  "evidence": [],
  "reproduction": [],
  "sources": []
}
```

- 同一 `fingerprint` 只保留一条，合并 `sources` 与 `evidence`；维度号与元素一致时，像素层、样式层、清单层的同一问题必须合并。
- 用 `scripts/normalize_evidence.py` 做归一，用 `scripts/validate_defects.py` 做结构校验；**缺陷数据结构不合法不得出报告**。覆盖率不达阈值是另一回事：它说明「还有维度没查」，不说明「已查的结论不可信」，因此照常出报告，由 `meta.coverage` 与报告横幅如实标注结论不完整——把整份报告扣住只会让人看不到已经查出来的东西。
- 风险按影响范围、核心流程、稳定复现、差异程度、是否阻断操作排序。`pending`（待确认）与确定缺陷必须分离。

## 9. 覆盖率门禁、报告与交付

先汇维度结论，再过门禁，再出报告：

```bash
python scripts/assemble_report.py --report ./parity-test/report.json \
  --style-diffs ./parity-test/styleDiffs.json \
  --findings ./parity-test/findings.json \
  --findings ./parity-test/performance.json \
  --findings ./parity-test/a11y_audit.json \
  --state-scenarios ./parity-test/state_scenarios.json \
  --scenario-coverage ./parity-test/scenario_coverage.json --write [--profile smoke]
python scripts/validate_defects.py --report ./parity-test/report.json --strict
python scripts/ignore_regions.py --file ./parity-test/ignoredRegions.json --enforce
python scripts/coverage_gate.py --report ./parity-test/report.json --enforce --write
```

`assemble_report.py` 把各驱动产物（样式差异 / 探测发现 / 状态场景 / 旅程发现 / 场景执行率）按维度汇总成 `meta.checks`——**45 个维度一个都不能少**，否则 `coverage_gate.py` 会直接判「维度缺失」。它的边界纪律：**无证据的维度只能填 `notRun` + 原因，严禁推断 `pass`**（「没跑」和「跑了没发现问题」是两件事）；report 里已有的手写 checks 原样保留，人填的比机器猜的优先级高。产物路径按实际存在的传，缺的就不传（对应维度落到 `notRun`）。

`coverage_gate.py` 按 `schemas/check-dimensions.json` 计算覆盖率 = (`pass`+`fail`+`warn`+`pending`) / 维度总数。`notRun` 与 `dataMissing` 必须写明原因，否则脚本拒绝；`pending` 与 `notRun` 语义不得混用。覆盖率低于 70% 时报告顶部显示「非全量覆盖」，并在给用户的总结里说明未覆盖维度。

**门禁通过后、出报告前，先做缺陷收口富化**（`enrich_defects.py`，顺序在门禁之后、缺陷截图之前——它只改 symptom/reproduction/来源标注，不动 element）：

```bash
python scripts/enrich_defects.py --report ./parity-test/report.json \
  [--design ./parity-test/design/design_model.json]
```

三条固化纪律（2026-10-29 用户确认）：**① 问题来源标注**——每条缺陷带 `sourceClass`（规范缺陷＝网页忠实还原设计稿但设计稿自身不达标，修复入口在设计侧／实现缺陷＝设计稿达标或不承载此定义，前端实现引入／规范+实现共同缺失＝设计稿缺规范且前端无兜底），判定依据写进 `sourceNote`；**无对照数据时留空，不猜**。**② 对比度类缺陷现象必须带设计稿色值对照**（文字色/底色/设计稿自身对比度）——"网页照抄设计稿"意味着根因在设计规范，这条结论不写，读者会去怪前端。**③ 复现步骤要详细到按键/面板级**（F12、Ctrl+Shift+C、看哪个面板、达到什么条件算复现），assemble_report 的占位模板（"打开受检页面/触发对应检查场景"）由本脚本按属性模板替换。人工已标注 `sourceClass` 的缺陷整体跳过（人填的比机器猜的优先），重复运行幂等。契约测试 `scripts/tests/test_enrich_defects.py`。

**门禁通过后、出报告前，给每条缺陷补位置图**（顺序不能颠倒：这一步要用 `assemble_report.py` 产出的 `element.selector / nth`，而报告一生成就定型了）：

```bash
node scripts/node/capture_defect_shots.cjs \
  --report ./parity-test/report.json --url "https://目标页" --out ./parity-test \
  --safety-policy <本轮已确认策略> \
  [--profile <浏览器配置目录>] [--viewport 1440x900] [--max 60]
```

- **必须在同一次运行里做**。`bbox` / `selector` 是采集那一刻的坐标，页面改版或换视口后补图会指错位置——「图上有框、框在别处」比没有图更糟。
- 文件名用**缺陷指纹前 8 位 + 维度**，不用序号。报告渲染前会按严重度重排缺陷，序号会变，按序号命名会让 CSV 第 1 条指向第 13 张图（实测踩过）。
- 三类缺陷三种产物：有 `selector` 且能定位 → 元素裁图 + 整页红框图；定位不到 → 只写 `shotError` 说明原因（**不出假图**）；页面级缺陷（无元素）→ 仅整页图。
- 每轮撤下旧图引用并使用独立截图目录；页面打开失败、疑似登录页、跨页面或明确非默认状态未复现时，不拿当前首页冒充证据。部分截图失败也返回非零，保留本轮成功图和失败原因。
- 截图继承本轮安全策略：`mockWrites` 在导航前拦截非只读请求，`staticOnly` 不点击侧栏；无法明确定位的展开按钮不猜坐标点击。
- 对比图仅使用网页整页图取首屏与设计稿整图并排；没有整页证据时不把元素特写标成整屏对比。尺寸或数据状态不一致须标限制，不能据并排图自动确认差异。

报告模板与字段见 `references/report-templates.md`，门禁细则见 `references/gates.md`。统一出口：

```bash
python scripts/generate_reports.py --input ./parity-test/report.json --outdir ./parity-test/reports
```

输出三件套：

- `report.html`：检查维度完成度、执行摘要、对象匹配与坐标基准、场景覆盖（**计划 vs 实际**：`scenario_coverage.py` 产出的执行率、未执行分布与豁免维度）、并排截图、差异热力图、样式分组差异、图标与图片差异、关键差异汇总、缺陷清单、待确认、回归对比、降级能力、diffScore 趋势、阈值与忽略项声明。
- `report.json`：机器可读完整结果，含 `meta.coverage` 与 `meta.defectValidation`。
- `defects.csv`：UTF-8-SIG、逗号分隔、严格 10 列（序号、等级、缺陷类型、问题来源、问题描述、复现步骤、解决方案、元素定位、截图、备注）。截图列为相对 CSV 的路径，跨磁盘时为文件 URI。问题描述为「现象 + 指标」同列双段。问题来源列含括注判定依据。报告 HTML 的并排截图区支持 `meta.htmlNote` 注入段（report.json 的 meta 里写 HTML 片段即可，重新生成不丢失——像素量化等轮次结论写这里，不再靠生成后手工改 HTML）。

交付前核对：对象匹配状态；每个维度有结论；每条缺陷有现象与元素定位；所有 `notRun`/`dataMissing` 有原因；无明文 token 或 Cookie；无危险操作；HTML/JSON/CSV 同源一致；回归结论基于已确认基线；阈值与生效忽略项已声明。交付前必须跑一次串链冒烟并全绿：

```bash
python scripts/smoke_chain.py
```

该冒烟用真实脚本产物端到端串联提取→比对→校验→归一→报告及各门禁。任何脚本的字段契约改动后都必须重跑，全绿才允许交付；失败即停止交付并修复。

**真实环境验证（关闭 E 维度"推断"标注）**：smoke_chain 用离线 fixture 验证契约，不代表真实增益。需要用真实 Figma token + 真实页面跑通一次完整链路后，按 `references/real-env-verification.md` 流程执行 `scripts/capture_real_env_evidence.py` 生成 `real-env-evidence.json`，作为 E4「显著增益（已查证）」证据。该步骤必须在用户真实环境执行，自动化改造过程不得持有或使用真实凭据。

## 容忍度与不可比项（完整口径）

- 普适容差（`scripts/compare_fields.py` 的容差表）与具体元素的忽略区叠加使用：先排除忽略元素，再对剩余元素应用容差。
- 浏览器抗锯齿、±1px 渲染舍入、字体渲染差异不直接判缺陷；但必须由容差表或忽略区**机械抑制**，不能只写在文档里。
- 忽略项必须带 `expiresAt` 或 `reviewedAt`，过期项不得进入报告；每次运行后列出本次生效的忽略项。
- 无可信页面对应物的字段（组件 ID、圆角平滑、原型动画、跨平台字体渲染）只能记 `pending`，禁止进入确定缺陷。
- 响应式约束、可访问性、真实点击属于必须验证项，不得列为不可比。
- 坐标基准未归一，或页面与设计稿宽度比例偏差 > 2% 时，**几何类属性（位置 / 尺寸 / 宽高）**的结论必须标 `notComparable` 并写明原因；样式类不受此限，仍按容差正常判定——视口差异不改变颜色、文案、字重这类属性该有的值。

## 交付总结（完整清单）

对话中输出：总场景数与已执行/未执行数、覆盖率与未覆盖维度、缺陷数量、P0/P1 Top 项、新增/修复/持续回归、降级能力、三个报告路径、本次阈值与生效忽略项。不得只说"通过"，必须给出证据覆盖范围。

另外几项按本轮实际执行情况补充，跑过就不能省：

- **性能（7.40 / 7.44）**：给出 TTFB / LCP / CLS 等关键值与**网络档**；无节流时必须说明"耗时仅供参考、未确认缺陷"。交互侧另给首响应 p50 / p90 与超阈值个数。
- **可访问性双端（7.41 / 7.42 / 7.43）**：给出页面侧命中数、设计侧对比度命中数（设计有、页面未复现的要点名）、键盘不可达/顺序错乱项数；内置规则与键盘采样都只是子集，不得声称"通过 WCAG AA"。
- **网络条件矩阵（7.33）**：分别给出 500 / 断网 / 弱网三个条件的结论；有条件没跑成时必须点明"该条件未测"，不能合并成一句"接口异常已检查"。
- **稳定性（flaky）**：给出翻转维度数与最不稳的前 3 个维度；一个都没翻时说明"共 N 轮可比"。
- **写操作清理**：执行过 caution/danger 动作时，给出已清理 / 未清理 / 不可恢复各多少条；有未清理项必须点名。
- **档位**：用了 `--profile` 时说明本轮档位与被档位排除的维度；`requiredButMissing` 非空要指出"这些是档位要求却没跑的"，不能混作"本轮不打算跑"。