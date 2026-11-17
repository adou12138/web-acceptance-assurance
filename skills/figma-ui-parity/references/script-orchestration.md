# 确定性脚本一览、输入产出与执行顺序

`scripts/` 下每个脚本只负责一件事，`run_parity_pipeline.py` 负责按序串起来并记录每个阶段的实际状态。禁止凭记忆写命令，按本文件核对参数。

## 一、数据流总览

```text
Figma 文件/节点 dump ─ enumerate_page_frames ─▶ frame_map.json ─▶ extract_design_fields.py（按页面 Frame 分片） ─▶ design_model.json ─┐
真实页面        ─ collect_page_inventory.js ▶ page.json ─────────┼─▶ discover_scenarios.py ▶ scenarios.json
                 extract_containers.js 等   ▶ containers/texts/图标 ─┘        │
                                    compare_fields.py ▶ styleDiffs.json ─────┴─▶ normalize_evidence.py
                                    extra_checks.js   ▶ findings.json ─────────┘        │
                          runtime_observability.js ▶ findings.json（运行期）─────────────┘
                                                                                        ▼
                     coverage_gate.py ◀ report.json ◀ validate_defects.py ◀────────────────┘
                                                                                        │
                     baseline_regression.cjs ─▶ regression.json ────────────────────────┤
                                                                                        ▼
                                                              capture_defect_shots ▶ shots/defects/*.png + defects[].screenshots
                                                              generate_reports.py ▶ report.html / report.json / defects.csv
```

## 二、统一入口

正式测试先经过启动门禁；环境检查和安全策略记录完成后，才进入确定性管线：

```bash
python scripts/start_test.py \
  --instruction "<原始测试指令>" \
  --figma-url "<Figma URL>" \
  --page-url "<页面 URL>" \
  --safety-policy <all|safe|mockWrites|staticOnly> \
  --user-safety-selection "<用户原始选择>" \
  [--test-environment-confirmed]
python scripts/run_parity_pipeline.py \
  --workdir ./parity-test \
  --page-url "<页面URL>" \
  --design-json ./parity-test/design_model.json \
  --page-json ./parity-test/page.json \
  --figma-png ./parity-test/shots/figma.png \
  --page-png ./parity-test/shots/page.png \
  --report ./parity-test/report.json \
  --outdir ./parity-test/reports
```

约定：

- 缺哪个输入就跳过对应阶段，并在 `pipeline-log.json` 里写 `notRun` + 原因，不伪造成功。
- **登录站**：优先用 `.auth` 里的 storage state（各驱动在 `goto` 之前统一注入），不传 `--browser-profile`。持久化 profile 会随会话过期而静默失效，表现为整轮结论都采自登录页——`run_page_probes.cjs` 检测到仍停在登录页时直接失败，不把登录框当目标页产出缺陷。确需 profile 时用 `--browser-profile <目录>`，它与 `--profile`（执行档位）是两个不同的东西。
- `--safety-policy` 透传给按钮审计、状态场景、跨页旅程与页面探针；`mockWrites` 下页面探针会中断全部非只读请求，运行期观测阶段只点非风险词按钮。
- `--no-probes` / `--no-aria` / `--no-button-audit` / `--no-journeys` 用于显式裁剪阶段；跳过的维度仍按档位记账，不会因为没跑而虚高覆盖率。
- 同时提供 `--design-json` 与 `--page-json` 时，管线自动串 `compare_fields` 与 `discover_scenarios`；`--report` 存在时先跑 `validate_defects` → `coverage_gate` → `ignore_regions`（存在时）门禁，再回归与出报告。
- `--report` 分支里，工作目录中已存在的 `alignment.json` / `order.json` / `aria_diff.json`（无 diff 时退到 `aria.json`）会被自动交给 `struct_checks.py`，`scenarios.json` 会被自动交给 `scenario_coverage.py`；产物随后一并喂给 `assemble_report.py`。这些是**可选输入**：文件不存在就跳过并在 `pipeline-log.json` 留 `notRun` 原因。
- 加 `--dry-run` 只打印将要执行的命令。
- 单个非核心脚本失败可继续其余阶段，但必须在报告里体现。设计输入转换或提取失败时立即写日志并非零退出，禁止消费上轮残留模型；`--figma-mcp-raw` 与 `--design-json` 互斥。多画板用 `--figma-node-id` 或 `--figma-root-name` 显式选择。

## 三、脚本清单（含输入产出）

| 脚本 | 输入 | 产出 | 说明 |
|---|---|---|---|
| `start_test.py` | 原始指令、Figma URL、页面 URL、安全策略 | `run-manifest.json`、环境状态 | 仅正式测试启动时运行；策略缺失或隔离环境未确认时阻断 |
| `init_workdir.py` | 工作区根目录 | `parity-test/` 目录树、`.gitignore` 规则 | 幂等；只追加缺失的忽略行 |
| `check_environment.py` | 无（8 项依赖检查硬编码在脚本内，**不读取** `skill-dependencies.json`） | 就绪状态与缺失项 | 只输出脱敏状态；由启动门禁调用 |
| `enumerate_page_frames`（MCP 浅层读取，必要时按 Frame 分片） | Figma 文件/节点 JSON | `frame_map.json`（页面 Frame 清单与 URL 映射） | 识别页面级 FRAME，排除 COMPONENT/INSTANCE/局部子 Frame；文件过大时分片读取 |
| `figma_mcp_to_json.py` | Figma MCP YAML-ish 文本（NAME/GLOBAL_VARS/ELEMENTS/NODES） | Figma-like JSON | 解析 MCP 人类可读返回，展开 ELEMENTS 模板与 GLOBAL_VARS；颜色/文本/字号/布局转为 REST-like 字段；NODES 只有 flow 相对位置时显式标记 `coordinateBasis=unavailable`，禁止伪造绝对坐标。管线用 `--figma-mcp-raw` 自动接入 |
| `extract_design_fields.py` | Figma 页面 Frame JSON 或 `figma_mcp_to_json.py` 产物 | `design_model.json`（单位统一换算为 px） | 字重名称→数值、行高/字距 PERCENT→px、渐变角度、多阴影保留顺序；坐标相对选定 Frame 原点 |
| `collect_page_inventory.js` | 页面（`browser_evaluate`） | `page.json`（inventory 契约） | 零尺寸元素不入库；记录 `nth` |
| `compare_fields.py` | `design_model.json` + 页面提取 + 可选 mapping | `styleDiffs.json`（含 `defects`、`unmatched`） | 坐标基准归一（中位归一需 ≥3 对配对，偏移超阈值输出告警）；属性级容差表；文本/容器分开配对，`pairConfidence` 记录配对依据（explicit/text-exact/name-segment/name-substring/geometry-contained），name-hint 按**词段**比对、<3 字符名不参与，低置信（中文名退化子串）不判 confirmed 只记 pending；文本子节点配对成功的父容器先尝试与唯一承载盒配对走正常样式比对（`pairRole: box-credit`），找不到才记 `matchedIndirect`（标 pending + 原因，不再静默 credit）；颜色支持 `#rgb/#rrggbb(aa)`、逗号/空格分隔 `rgb()/rgba()`、`/ alpha`、百分比与 `hsl()`，形似颜色却解析失败的值进产物 `parseWarnings.unparsedColors`；透明背景与半透明填充不参与比对；rotation 非零的节点不比位置尺寸；缺陷直接带 `severity`/`confidence`/`reproduction`/`suggestion` |
| `assert_constraints.py` | `design_model.json` + 多视口测量 | `constraints.json` | LEFT/RIGHT/CENTER/SCALE/FIXED 断言数学，位置 ±2px、比例 ±2% |
| `extra_checks.js` | 页面（`browser_evaluate`） | `findings.json` | 7.20/7.24/7.25/7.27/7.28/7.29/7.30/7.31 无副作用探测：图片比例、暗色、多行截断、控件细节、焦点可见性、国际化、滚动 UX、加载残留（**不含 7.26 打印视图与 7.32–7.34，这些须由场景驱动**） |
| `node/run_page_probes.cjs` | 页面 URL + 登录态 + 断点列表 | `responsive.json`、`extra_checks.json`、`alignment.json`、`order.json`、`icons.json`、`runtime.json`、`page_probes.json` | **注入型探针的统一驱动**。`responsive_check.js` / `extra_checks.js` / `alignment_modal_checks.js` / `order_divider_scroll_check.js` / `extract_icons.js` / `runtime_observability.js` 都是「IIFE 返回 JSON」的页面内脚本，靠人手工注入一次漏一次，就是一片 notRun。本驱动一次会话跑完全部探针，登录态在 `goto` **之前**注入，停在登录页时显式失败而不是把登录框当目标页；`--safety-policy mockWrites` 会中断全部非只读请求，运行期观测阶段的点击只取非风险词按钮。响应式按「断点 × 问题类型」聚合，明细留在 `responsive.json` 的 `viewports` 里 |
| `design_compare.py` | `report.json`（网页整页图、可选 `shotBox`）+ 设计稿 1x 整图 | `<stem>.design.png` / `.compare.png` / `.region.png`，回写截图引用 | 网页整页图取首屏，与设计稿整图保持原有比例并排；缺少整页图不拿特写冒充。`shotBox` 仅用于网页周边区域图，不推算设计稿位置。`--only-dims` 默认限定设计还原类维度；撤下所有旧对比引用（含超限项），不删除原始素材 |
| `runtime_observability.js` | 页面（`browser_evaluate`，**两阶段**） | `findings.json`（运行期） | 7.35–7.38 运行期观测：先调用一次安装钩子、交互后再次调用采集；7.36 额外从响应体识别「HTTP 200 + 业务失败」与「错误页伪装 200」；见 `runtime-observability.md` |
| `performance_probe.js` + `node/run_performance.cjs` | 页面 URL（驱动用 `addInitScript` 先装观察器） | `performance.json`（findings 形状） | 7.40 性能：TTFB/FCP/LCP/CLS/TBT/资源体积/未压缩大图。**CLS 无 buffered 入口，必须 install 阶段提前装**；无网络节流时耗时只记 `warn`；见 `performance.md` |
| `a11y_audit.js` + `node/run_a11y_audit.cjs` | 页面（`browser_evaluate`）+ 可选 `--axe` | `a11y_audit.json`（findings 形状） | 7.41 可访问性规则合规：内置 15 条 WCAG 2.1 A/AA 子集规则；对比度背景回溯在暗色页降级 `pending`；`--axe` 存在时合并 axe 结果（`engine=axe+builtin`）；见 `a11y-rules.md` |
| `design_contrast.py` | `extract_design_fields.py` 产物（需含 `tree`） | `design_contrast.json`（findings 形状） | 7.42 设计稿侧文本对比度：在祖先填充链上合成候选底色后算 WCAG 1.4.3 AA；图片底/渐变部分不达标/半透明祖先/顶层透明一律 `pending`，不按白底硬判；见 `design-contrast.md` |
| `keyboard_order.js` + `node/run_keyboard_order.cjs` | 页面 URL（驱动负责按键，页面脚本负责枚举与记录） | `keyboard_order.json`（findings 形状） | 7.43 键盘可达性与焦点顺序：Tab 循环采样 `activeElement`；只按 Tab 不按 Enter，`safe` 策略可跑；`--open "<选择器>"` 才会检查弹层焦点陷阱；见 `keyboard-a11y.md` |
| `node/response_timer.cjs` | 由 `run_button_audit.cjs` 调用（不单独运行） | `<button-audit>/response_findings.json` + `button_audit.json.responseTime` | 7.44 交互首响应时延：轮询「首个实质变化」（URL/弹层/请求/节点数±3/文本±10），>1000ms `warn`、>2000ms 缺陷。**不是 INP**，不冒充 INP 数值；单节点抖动不算响应 |
| `flaky_report.py` | `baseline/runs/*.json` 历史快照 | `flaky.json` + `flaky.csv`(utf-8-sig) | 跨轮不稳定性量化：按 pass/非 pass 边界数翻转，只读历史不改判定。旧快照缺 `checks` 键时记 `legacyRuns` 并点名，不静默当「稳定」；见 `flaky-analysis.md` |
| `discover_scenarios.py` | `design_model.json` + `page.json` + 可选 `styleDiffs.json` | `scenarios.json` | 元素级场景 + 异常数据 + 角色态；`--roles`、`--max-elements`、`--max-data-cases`、`--combo-depth`、`--breakpoints`、`--patterns`（缺陷模式池反哺，见 `bug-driven-checks.md`） |
| `run_state_scenarios.cjs` | 页面 URL + 安全策略 | `state_scenarios.json` + 截图 | 7.25/7.26/7.32/7.33 状态场景驱动（emulateMedia 切暗色/打印、route 拦载 fulfill 500、hash 导航+返回）；`--only` 挑维度、`staticOnly` 短路不启动浏览器；见 `bug-driven-checks.md` |
| `run_journeys.cjs` | 页面 URL + 安全策略 | `journeys.json` + `journey_findings.json` | 7.39 多页用户旅程：BFS 有界点击站内链接；`--max-hops`/`--max-links-per-page`/`--max-visits`；同缺陷多路径到达按维度+属性+页面去重记 occurrences；见 `journeys.md` |
| `state_probes.js` | 页面（切换状态后 `browser_evaluate`） | `findings`（暗色/打印/接口异常后界面） | 状态切换后的页内探测；判定与采集分离（纯函数 node 可测）；全文无反斜杠 |
| `scenario_coverage.py` | `scenarios.json`（计划） + 可选执行结果 JSON / `report.json` | 场景执行率 JSON（可直接放进报告 payload 的 `scenarios` 段） | **计划 vs 实际**：`--results`/`--from-report` 回填执行痕迹，`--enforce` 拦截"未执行且未写原因"与低于 `--min-rate`，`--exempt-dimensions` 显式豁免并单列；见 `scenario-contract.md`。`run_parity_pipeline.py` 在 `scenarios.json` 存在时**自动生成**该产物并喂给 `assemble_report`——只消费不生成的话，执行率门禁在标准流程里不生效。**回收范围**：管线里它跑在 `assemble_report` 之前，`--from-report` 只能看到 `report.json` 中**已存在**的 `scenario` 痕迹；来自 findings 产物的缺陷尚未并入，因此执行率可能偏低（保守方向，不会虚高），且不影响维度状态——`summarize` 取最严重状态时 `fail`/`pass` 优先于 `notRun`。要拿到含 findings 痕迹的精确执行率，需在 `assemble_report` 之后单独重跑并人工核对 |
| `pattern_pool.py` | 缺陷产物 JSON（`diffs`/`styleDiffs`/`defects`/`findings`/`violations`） | 池更新（`--merge`）/ 风险排序 Top（`--rank`） | 缺陷模式池 `assets/defect_patterns.json`；`--merge` 幂等、`--rank --top N` 出 Top 列表；见 `bug-driven-checks.md` |
| `normalize_evidence.py` | 各来源差异 JSON | 去重后的差异集合 | 按 `fingerprint` 合并，保留 `sources` 与 `evidence` |
| `defect_keys.py` | —（被依赖模块） | `CONTAINER_KEYS` / `signature()` / `iter_containers()` | 缺陷容器键的**唯一来源**，consumer 必须真正 import（`assemble_report` / `gen_excel_fallback` / `validate_defects` / `normalize_evidence`）；同型漂移由 `tests/test_data_contract_keys.py` 拦截 |
| `node/browser.cjs` | —（被依赖模块） | `resolvePlaywright()` / `launch()` / `closeAll()` / `restoreAuth()` | 浏览器生命周期的**唯一来源**。三个驱动（状态场景 / 性能 / 可访问性）共用，避免"只关 context 不关 browser"在多份副本里各自漏一遍——漏了的表现是 node 不退出、管线等到超时，日志只说"无输出"。所有兜底都带留痕 |
| `locator_quality.py` | `element`（`selector` + `nth`） | `element.locatorQuality` / `locatorSignals` / `locatorAdvice` | 定位可信度分级 `stable/semi/fragile/none` 的**唯一来源**，在 `assemble_report` 与 `normalize_evidence` 两处收口补入；不参与判定、不进任何门禁，只让「下次可能找不到的定位」在报告里可识别；见 `locator-quality.md` |
| `write_ops_ledger.py` | 写操作条目 JSON / 台账文件 | `write_ops_ledger.json`、清理计划 markdown、`report.pendingChecks` 条目 | 写操作清理台账：登记（`reversible` 必填）、`--plan` 出计划、`--mark-clean/failed` 回填、`--audit --enforce` 拦未清理、`--merge-pending` 并入报告待确认。**不执行清理动作**；见 `write-ops-cleanup.md` |
| `assemble_report.py` | 各驱动产物（`styleDiffs.json` / `findings.json` / `performance.json` / `a11y_audit.json` / `keyboard_order.json` / `design_contrast.json` / `state_scenarios.json` / `scenario_coverage.json`）+ `report.json` | `report.json` 的 `meta.checks`（45 维度结论）+ `meta.runProfile` | **进门禁前必跑**：把维度级证据汇总成 checks，缺一个维度 `coverage_gate.py` 就判缺失。边界纪律：无证据只能 `notRun`+原因，不得推断 `pass`；已有手写 checks 原样保留。`--profile smoke/standard/full`（表在 `assets/run_profiles.json`）只把未执行维度的原因改写成「档位未纳入」，并把「档位要求但本轮无产物」单列为 `requiredButMissing`（漏跑），**不改判定、不抬高覆盖率** |
| `stabilize.js` | 页面 URL | 稳定化后的页面会话 | 截图前必须跑 |
| `diff_score.js` | 两张 PNG | `diffScore` + 遮罩图 | `--threshold` 是像素噪声门槛，非百分比 |
| `extract_containers.js` / `extract_texts.js` / `extract_icons.js` / `order_divider_scroll_check.js` | 页面 | 容器/文本/图标/分隔线数据（脚本 A–D） | 见 `extraction.md`；注意 MCP 传参吃反斜杠 |
| `capture_aria.js` | 页面 + 登录态 | 可访问性树快照与 diff | 见 `aria-diff.md`。**不依赖 `page.accessibility`**（该 API 在 Playwright 1.63 已移除）：角色与可访问名称在页面内按 ARIA 优先级自建，输出形状与旧快照一致 |
| `alignment_modal_checks.js` | 页面 | 对齐与弹框走查结果 | 见 `bug-driven-checks.md` |
| `wording_gate.py` | 报告本身（缺陷容器 + `pendingChecks`） | `meta.wording_gate = {checked, flagged, pass}` | 措辞门禁：拦住「正常展示」「符合预期」这类无法据以判定的描述。判据是**双条件**——命中禁止词 **且** 同句没有任何具体性信号（引号内字面量 / 可量化数字 / 颜色+元素或两个以上 UI 元素词）；只做单条件会误杀「提示『保存成功』」。`assemble_report.py` 自动调用，**只记录不阻断**：措辞不合格的正确去向是让人改写，不是进缺陷台账 |
| `struct_checks.py` | 结构化观察产物：`alignment.json` / `order.json` / `aria_diff.json`（三者任一或多个） | findings JSON（`--findings` 可直接喂 `assemble_report.py`） | 四路观察的唯一消费方：脚本 E 的 `alignments`/`clippedControls`/`suspiciousText`（7.13/7.15）、脚本 D 的 `needScrollActions`（7.9）、可访问性结构 diff（7.19）。`pass`→pass 证据、对齐失败与控件溢出→confirmed、可疑文案 / 首屏外主操作 / a11y 结构变化→pending、仅快照无基线→`dataMissing`。**判 pass 的前置条件必须成立**：7.9 要求 `actionCandidates>0`（否则空列表只代表"没有判定对象"），7.19 要求两次快照节点数均 >0（否则空 diff 只代表抓取失败）；字段形状异常一律降级 `dataMissing`，绝不因"读不出内容"而判通过。**7.6/7.7 仅在提供 `--design-inventory` 时产条目**：设计侧清单由 `design_inventory.py` 从节点树机械推导，两侧文本集一致但顺序不同→confirmed、几何推导（confidence<0.8）只出 pending、容器未匹配→dataMissing。不提供清单时仍不产——凭页面单侧数据下结论就是编造。`run_parity_pipeline.py` 在对应产物存在时自动接入；一个输入都不给时以非零码退出，避免空 findings 冒充"已执行" |
| `responsive_check.js` | 页面 | 多断点健康检查 | 布局断言部分交给 `assert_constraints.py` |
| `button_audit.js` / `node/run_button_audit.cjs` | 页面 | 按钮可用性与双向盘点 | 见 `button-audit.md`；默认 caution 拦写请求、danger 不点击；`all + testEnvironmentConfirmed=true` 时按授权执行 caution/danger |
| `node/risk_words.cjs` | —（被依赖模块） | 导出的 `DANGER_WORDS` / `CAUTION_WORDS` | 风险词表**单一来源**。`run_journeys.cjs` 直接 `require` 它做危险链接过滤；`discover_scenarios.py`（场景 safety 分级）与 `button_audit.js`（`RE_DANGER`）各持一份同名常量与之**对拍**。改词表只改这一处再同步 `discover_scenarios.py`，`tests/test_risk_words_parity.py` 保证两边集合一致、并校验 `button_audit.js` 的 `RE_DANGER` 覆盖全部词条，漂移即测试红 |
| `extract_design_buttons.cjs` | 设计节点 | 设计侧按钮清单 | 双向盘点输入 |
| `gen_button_report.cjs` | 按钮审计目录 | 按钮报告 | `--dir` 必填；同时认 `<dir>/<page>/button_audit.json`（多页）与 `<dir>/button_audit.json`（单页）两种布局 |
| `journey_coverage.cjs` | `journey.json` + `coverage.json` | 状态×断点覆盖率 | 与维度覆盖率是两套口径 |
| `coverage_gate.py` | `report.json` + `schemas/check-dimensions.json` | `meta.coverage` | 覆盖率与 70% 门禁；未写原因的 `notRun`/`dataMissing` 直接拒绝 |
| `validate_defects.py` | `report.json` + `schemas/defect.schema.json` | 校验结论 | 缺 `symptom`/`element` 即失败；`--strict` 还要求证据与复现非空 |
| `ignore_regions.py` | `ignoredRegions.json` | 生效/过期/宽泛/字段缺失清单 | 有 `--enforce` 时过期、字段缺失或宽泛/缺到期信息均非零码 |
| `baseline_regression.cjs` | `report.json` | `regression.json` | 默认只读基线；`--update-baseline` 才提升；每次运行留 `baseline/runs/` 快照 |
| `node/capture_defect_shots.cjs` | 汇总后的 `report.json`（读 `defects[].element.selector/nth`）+ 页面 URL | 回写 `defects[].screenshots/shotError/shotNote`；`shots/defects/*.png` | 缺陷位置图：元素裁图 + 整页红框图。**必须紧跟 assemble、且在 generate_reports 之前**，并且同一次运行内完成（坐标会漂移）。每轮独立目录，文件名用指纹前 8 位而非序号；导航失败、登录页、跨页/frame、明确非默认状态未复现时不截图，部分失败返回非零并保留成功证据。继承本轮安全策略，staticOnly 不点击侧栏；mockWrites 在导航前拦写请求 |
| `generate_reports.py` | 汇总后的 `report.json`（含 `defects[].screenshots`） | `report.html` / `report.json` / `defects.csv` | 15 个区块，CSV 严格 10 列（第 9 列「截图」为相对路径，第 10 列「备注」）；HTML 渲染 `meta.htmlNote` 注入段（重新生成不丢失）；HTML 把小于 400KB 的附图内联为 base64，大图给相对链接。上游只带 `severityHint` 时兜底映射为 `severity` |
| `plan_run.py` | `assets/dimension_manifest.json` + 档位表 | `run-plan.json` | **跑前计划门禁**：45 维度逐个排程或预登记豁免理由（黑名单话术：待定/TODO/过短 → rc=2）；needsDesign 未传设计自动转豁免。退出码 0=核准 / 1=参数错 / 2=必测未排程。管线在 stabilize 前强制执行，rc=2 当场熔断 |
| `pre_assemble_gate.py` | `run-plan.json` + run 目录 + assemble 入参清单 | 对账结论 + `assemble-args.suggested.json` | **汇总前接线门禁**：正向验真（产物存在+非空+合法 JSON+requiredKeys，防假跑）+反向对账（run 目录已知产物不在入参 = 漏传拦截）+建议命令生成。管线在 assemble 前强制执行，rc≠0 当场熔断。门禁熔断判定 **notRun 与 failed 同样算未通过** |
| `design_inventory.py` | `design_model.json` | `design_inventory.json` | **设计侧盘点引擎**：从节点树机械推导顺序（AUTO_LAYOUT conf1.0 / 几何 conf0.6）、图标（尺寸 0.9 > 命名 0.8 > 叶矢量 0.6）、分隔线（极端宽高比）三类清单，逐条带节点证据；坏节点进 unrecognized 绝不崩溃。空清单是诚实数据点（消费方记 dataMissing）。struct_checks 以 `--design-inventory` 消费 |
| `enrich_defects.py` | `report.json`（+ 可选 design_model.json） | 回写 `defects[]` 的 `sourceClass`/`sourceNote`/设计稿色值对照/详细复现步骤 | **在 assemble+门禁之后、capture_defect_shots 之前**执行。三分类来源标注（无对照数据留空不猜）、对比度现象带设计稿色值、占位复现替换为按键级步骤；人工已标注的缺陷整体跳过，幂等 |
| `smoke_chain.py` | 内置 fixture | 串链冒烟结论（exit 0/1） | 用真实脚本产物端到端串联 extract→compare→validate→normalize→reports 及各门禁；改任何脚本字段契约后必须先跑它，全绿才允许交付 |
| `gen_excel_fallback.py` | 缺陷列表 | 无 `openpyxl` 时的表格兜底 | 见脚本头部说明 |

## 四、推荐执行顺序

```text
init_workdir            → 目录与忽略规则
check_environment       → 决定哪些阶段能跑
enumerate_page_frames   → 文件浅层枚举、页面 Frame 清单与 URL 映射
extract_design_fields   → 按页面 Frame 分片生成设计侧模型（含单位换算）
collect_page_inventory  → 页面资产盘点
compare_fields          → 设计↔页面逐字段比对 + 双向盘点
assert_constraints      → constraints 多视口断言
extra_checks            → 资源与偏好维度探测
runtime_observability   → 运行期观测（交互前安装钩子、交互后采集）
run_performance         → 7.40 性能（addInitScript 装观察器，必须最先于页面交互）
run_a11y_audit          → 7.41 可访问性规则合规（渲染稳定后）
run_page_probes         → 注入型探针统一驱动（响应式/补充检测/对齐/顺序/图标/运行期）
capture_aria            → 7.19 可访问性结构快照（结构 diff 的输入）
discover_scenarios      → 元素级场景矩阵（--combo-depth 组合）
stabilize               → 页面稳定化（截图前）
extract_* / capture_aria→ 样式与可访问性提取
diff_score              → 像素差异与遮罩
button_audit            → 按钮可用性与点击生效
gen_button_report       → 按钮结果落盘
journey_coverage        → 状态旅程覆盖率
normalize_evidence      → 缺陷归一与去重（同时补 locatorQuality）
pattern_pool            → 缺陷模式池累积 + 风险排序 Top（可选；**走 run_parity_pipeline.py 时默认执行**，见下）
write_ops_merge         → 写操作台账未清理项并入 report.pendingChecks（必须在 assemble 之前）
assemble_report         → 维度证据汇总成 checks（--profile 档位记账）
validate_defects        → 缺陷结构校验（不通过不得出报告）
coverage_gate           → 维度覆盖率与门禁
ignore_regions          → 忽略区到期审计
write_ops_audit         → 写操作清理审计（不加 --enforce：残留要可见，但不阻断出报告）
capture_defect_shots    → 缺陷位置附图（元素特写 + 整页带框），并记录重定位坐标 shotBox
design_compare          → 网页首屏/设计稿整图并排；shotBox 仅用于网页区域图
baseline_regression     → 与已确认基线对比
generate_reports        → HTML / JSON / CSV
smoke_chain             → 交付前串链冒烟（全绿才交付）
```

## 五、报告一致性要求

`generate_reports.py` 输出的 HTML、JSON、CSV 必须同源：

- 三者缺陷条数一致；HTML 摘要的 `defectTotal` 与 JSON 一致；CSV 严格 10 列（问题来源、复现步骤、截图与备注分列）且只含缺陷、不含忽略项与被忽略变更。
- 覆盖率必须来自 `coverage_gate.py` 的 `meta.coverage`，禁止手填。
- HTML 的「阈值与忽略项声明」章节必须写出本次像素阈值、区域百分比、容差表与生效忽略项。
- 任一不一致必须回到 `normalize_evidence.py` 重跑，不得手工改报告文件。
