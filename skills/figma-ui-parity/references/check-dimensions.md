# 检查维度编号表（7.x 稳定命名空间）

**正文流程步骤是 1–9，本表的 7.x 是检查维度编号，两者正交。** references 里出现的 `7.x` 一律指向本表，不再指向正文步骤。机器可读版本在 `schemas/check-dimensions.json`，`scripts/coverage_gate.py` 按它计算覆盖率。**驱动/产物以 `assets/dimension_manifest.json` 为单一事实源**（`plan_run` 与 `pre_assemble_gate` 只认它）；本表「自动化」列与 manifest 不一致时以 manifest 为准。本表「自动化」列给的是**排程驱动 / 落账脚本**；页面内采集脚本见 `schemas/check-dimensions.json` 的 `script` 字段（两者是协作关系，不是同一字段的两种说法）。

## 一、为什么需要这张表

历史引用里 `7.1–7.23` 被写成「步骤 7.x」，正文压缩重构后步骤只剩 1–9，导致所有对账表、Excel 类型映射表都定位不到。解决办法不是重编号正文，而是把 7.x 升格为**稳定的维度命名空间**：维度只增不改，正文步骤可以自由重构。

## 二、维度总表

| ID | 维度 | 承载文档 | 自动化 |
|---|---|---|---|
| 7.1 | 静态样式检查清单（尺寸/间距/排版/视觉/内容适配） | `extraction.md`、`figma-fields.md` | `compare_fields.py` |
| 7.2 | 交互状态对比（hover / active / disabled） | `extraction.md` | 部分 |
| 7.3 | 图标与图片对比（区块级） | `extraction.md` | 部分 |
| 7.4 | 响应式 / 多视口实测（含 constraints 断言） | `responsive.md` | `responsive_check.js` |
| 7.5 | 图标成对精确比对 | `icon-diff.md` | `extract_icons.js` |
| 7.6 | 排列顺序比对 | `extraction.md` | `struct_checks.py`（排程驱动/落账；采集 `order_divider_scroll_check.js`。无设计侧对照 → 不自动下结论） |
| 7.7 | 装饰元素（分隔线）比对 | `extraction.md` | `struct_checks.py`（排程驱动/落账；采集 `order_divider_scroll_check.js`。是否有设计意图需人判） |
| 7.8 | 重复文案与文案一致性 | `extraction.md` | `compare_fields.py` |
| 7.9 | 免滚动可达性 | `extraction.md` | `order_divider_scroll_check.js` + `struct_checks.py`（首屏外主操作→pending；`actionCandidates>0` 且无命中→pass；无候选计数或 `actionCandidates=0`→`dataMissing`/`notRun`） |
| 7.10 | 跨环境对比（无多环境时 `notRun`） | `bug-driven-checks.md` | 无（需第二个环境，单环境只能 `notRun`） |
| 7.11 | 缺陷记录携带现象与元素定位 | `report-templates.md` | `validate_defects.py` |
| 7.12 | 待确认问题记录 | `report-templates.md` | `assemble_report.py`（由本轮 pending / dataMissing 条目与 `pendingChecks` 派生，原因分开写明两类；**同时给出 `blocked_scenarios` 与 `resolution_condition`**——只说「有待确认项」等于把问题往人手里一推，不知道卡了哪些场景、也不知道确认什么才能放行；手写结论优先保留；派生结果带 `derived` 标记，**重复运行会重算**，不会被上一轮结果冻结；无待确认项时 `notRun` 并写明，不虚判 `pass`） |
| 7.13 | 复合元素内部对齐 | `bug-driven-checks.md` | `alignment_modal_checks.js`（星号居中 / hint 左对齐 / 图标与文字居中）+ `struct_checks.py`（落账）+ `compare_fields.py`（文本对齐与大小写） |
| 7.14 | 弹框 / 浮层逐个走查 | `bug-driven-checks.md` | `alignment_modal_checks.js` |
| 7.15 | 控件内容完整显示与多余文案 | `bug-driven-checks.md` | `alignment_modal_checks.js` + `struct_checks.py`（溢出→confirmed，可疑文案→pending） |
| 7.16 | 长内容布局与状态相关显隐 | `bug-driven-checks.md` | 部分 |
| 7.17 | 表单校验与错误态 | `bug-driven-checks.md` | `discover_scenarios.py` |
| 7.18 | 浮层 / 下拉 / 弹框交互与层级 | `button-audit.md` | 部分 |
| 7.19 | 可访问性与键盘可用性 | `aria-diff.md` | `capture_aria.js` + `struct_checks.py`（结构变化→pending；首次无基线→`dataMissing`） |
| 7.20 | 加载 / 空态 / 禁用态 | `state-journey.md` | `discover_scenarios.py`、`extra_checks.js` |
| 7.21 | 长文本 / 多语言 / 密集数据适配 | `bug-driven-checks.md` | `discover_scenarios.py` |
| 7.22 | 按钮可用性与点击生效 | `button-audit.md` | `run_button_audit.cjs` |
| 7.23 | 元素双向盘点（设计稿有页面无 / 页面有设计稿无） | `button-audit.md` | `compare_fields.py` |
| 7.24 | 图片与插画适配（比例失真 / 裁剪 / 加载失败） | `bug-driven-checks.md` | `extra_checks.js` |
| 7.25 | 暗色模式与主题切换 | `bug-driven-checks.md` | `extra_checks.js`、`run_state_scenarios.cjs` |
| 7.26 | 打印与导出视图 | `bug-driven-checks.md` | `run_state_scenarios.cjs` |
| 7.27 | 多行文本截断与省略 | `bug-driven-checks.md` | `extra_checks.js` |
| 7.28 | 表单控件细节（placeholder / caret / autocomplete） | `bug-driven-checks.md` | `run_state_scenarios.cjs`（排程驱动；采集 `extra_checks.js`） |
| 7.29 | 焦点可见性与键盘细节 | `bug-driven-checks.md` | `extra_checks.js` |
| 7.30 | 国际化细节（RTL / emoji / 全角半角） | `bug-driven-checks.md` | `extra_checks.js` |
| 7.31 | 滚动 UX（滚动条 / 固定底栏 / sticky / 滚动锁定） | `bug-driven-checks.md` | `extra_checks.js` |
| 7.32 | 页面级状态残留（SPA 路由 / 多标签页 / 返回刷新） | `bug-driven-checks.md` | `discover_scenarios.py`（生成）+ `run_state_scenarios.cjs`（执行）（**需识别到 tabs / table / pagination，或缺陷池命中 7.32**；两者都不满足时该维度无场景，应填 `notRun:页面无路由类元素`，**不是漏跑**） |
| 7.33 | 网络条件矩阵：接口 500 / 断网 / 弱网下的界面（白屏、骨架残留、无错误提示、无加载反馈） | `bug-driven-checks.md` | `run_state_scenarios.cjs --network-conditions` |
| 7.34 | 角色与权限态视图差异 | `bug-driven-checks.md` | 无 |
| 7.35 | 运行期 JS 异常（console.error / onerror / unhandledrejection） | `runtime-observability.md` | `runtime_observability.js` |
| 7.36 | 交互期网络失败（fetch/XHR 非 2xx） | `runtime-observability.md` | `runtime_observability.js` |
| 7.37 | 重复请求 / 重复提交 | `runtime-observability.md` | `runtime_observability.js` |
| 7.38 | 可交互元素遮挡（点击落不到） | `runtime-observability.md` | `runtime_observability.js` |
| 7.39 | 多页用户旅程（跨页导航：404/500、落地空白、返回中断、中途弹登录） | `journeys.md` | `run_journeys.cjs` |
| 7.40 | 性能与核心网页指标（TTFB / FCP / LCP / CLS / TBT / 资源体积 / 未压缩大图） | `performance.md` | `performance_probe.js`（驱动 `node/run_performance.cjs`） |
| 7.41 | 可访问性规则合规（WCAG 2.1 A/AA 子集，单次运行即可判定） | `a11y-rules.md` | `a11y_audit.js`（驱动 `node/run_a11y_audit.cjs`） |
| 7.42 | 设计稿侧文本对比度（在设计模型上直接算，评审阶段就能发现问题） | `design-contrast.md` | `design_contrast.py` |
| 7.43 | 键盘可达性与焦点顺序（Tab 能否走到、顺序是否与 DOM 一致、正 tabindex、弹层焦点逃逸） | `keyboard-a11y.md` | `keyboard_order.js`（驱动 `node/run_keyboard_order.cjs`） |
| 7.44 | 交互首响应时延（点击到首个实质变化的时间） | `performance.md` | `response_timer.cjs`（由 `run_button_audit.cjs` 调用） |
| 7.45 | 请求载荷完整性（占位符泄漏 / 同名参数重复 / 写请求零参数） | `runtime-observability.md` | `runtime_observability.js` |

> 7.40 / 7.41 / 7.42 / 7.43 的共同点：**首次运行就能出结论**，不依赖历史基线。7.19 那种"无基线
> 即 `dataMissing`"的维度，在新页面上等于空转；这四个不会。
>
> 7.42 与 7.41 是同一件事的两端：7.42 在设计稿上算对比度（越早越便宜），7.41 在页面上验实际渲染。
> 两者都判「不达标」时不是重复，而是确认了「设计稿这样写、页面也这样实现了」——那正是要修的。
>
> 两者的执行档位见 `assets/run_profiles.json`（smoke / standard / full）；档位只改
> "未执行维度的原因写法"，不改判定，覆盖率也不会因为换档位而虚高。

## 三、使用规则

1. 报告必须逐项输出本表全部维度的结论，状态只能是 `pass` / `fail` / `warn` / `pending` / `notRun` / `dataMissing`。
2. 覆盖率 = (`pass+fail+warn+pending`) / 维度总数，由 `scripts/coverage_gate.py` 计算，禁止手填。
3. 新增能力时给新维度分配**新编号**（当前下一个是 7.46），不得复用或改写已发布编号的含义。
4. 缺陷记录里的 `dimension` 字段必须填本表 ID，跨层（像素层 / 样式层 / 清单层）同维度同元素才能正确合并去重。
