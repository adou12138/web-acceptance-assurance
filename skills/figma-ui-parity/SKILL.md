---
name: "figma-ui-parity"
description: "以 Figma 设计稿为基准执行 UI 还原、视觉回归、响应式、可访问性、性能与交互可用性检查，双向盘点设计与页面元素并输出 HTML、JSON、CSV 报告。有 Figma 设计稿或导出物时走本 skill；无设计稿的常规页面走查由砺衡主体五视角承担，仅当砺衡专家按信号主动升级或用户显式要求页面侧 45 维深查时才使用本 skill 的页面侧链路。"
version: 4.47.0
author: TestCase
agent_created: true
category: ui-testing
platforms: [windows, macos, linux]
---

# Figma UI Parity

> **砺衡包内版 · 剥离适配声明（v1.13.0，2026-11-17）**
> - 剥离源：figma-ui-parity **4.47.0**（版本冻结，源 skill 零改动）；frontmatter `version` 与 `check_environment.py VERSION` 保持源值（版本一致性测试强制）。
> - **两套质量体系边界**：本 skill 按自身门禁判定（45 维度覆盖率 / `validate_defects` / `smoke_chain` 全绿），**不套用**砺衡的 S0/G/V/COV/COV-PATH 门禁；报告（HTML/JSON/CSV）自成体系，在砺衡报告中按附件引用。
> - **与砺衡视觉能力分工**：有 Figma 设计稿 → 走本 skill（像素级还原）；**无设计稿** → 走砺衡 `visual-usability.md` 五视角走查，报告不得写“还原度”（见 §R0 输入路由）。
> - **宿主依赖（冷启动四态）**：先跑 `python scripts/check_environment.py` 查看四态（`ready/partial/needs_setup/unavailable`）——设计数据依赖 Figma MCP/REST token、页面提取依赖 Playwright MCP、像素 diff 依赖 visual-parity MCP（可选）；未就绪时按官方降级链（页面走查 / 静态 DOM / 截图+样式）执行，不得声称像素级通过。安装指引见 `references/tooling.md`（playwright MCP 与 figma token 的 `~/.workbuddy/mcp.json` 配置）。
> - **运行时要求**：Python ≥ 3.10（与包内其它 skill 的 3.9 并存，按各自 SKILL 声明）；**Windows 中文环境跑 `scripts/tests` 需 `PYTHONUTF8=1`**（测试依赖 UTF-8 默认编码）；Node 探针按候选链加载 `playwright-core/playwright`（全局、cwd node_modules 或 `PLAYWRIGHT_CORE_PATH`），跑 `scripts/tests` 亦需该变量就位。
> - **工作区**：默认相对当前目录 `./parity-test`（不写入 skill 包内）；剥离时已排除源工作区与认证目录。
> - **专家主动升级（整合 v1.14.0）**：主体五视角命中深度信号（键盘/对比度/CLS/首响/断点溢出）时由砺衡专家自动升级本 skill 页面侧链路（编排层传 `--safety-policy safe`，写操作仍须必问矩阵确认）；映射见 agents「专家主动升级」。
> - **检出维度全景**：专家包 `references/bug-detection-map.md`（三套维度交叉索引，命名空间独立）。

## 目标与边界

把 Figma 设计稿和实际页面作为两个有证据的数据源，完成八类检查：对象是否对应、静态样式是否一致、交互是否生效、异常状态是否稳定、运行期是否安静、性能是否达标、可访问性是否合规、跨轮是否稳定与回归。每个结论必须标注 `pass`、`fail`、`warn`、`pending`、`notRun` 或 `dataMissing`（`notRun` 与 `dataMissing` 还须写明原因）。不得凭截图猜测；不得自动填充账号密码；默认不得在未确认环境的情况下执行危险写操作；仅当用户在本次运行中明确选择「全部操作」并确认当前为隔离测试环境时，才允许按本策略执行全部 safe、caution、danger 操作。不得把 token 或 Cookie 原文写入日志、报告或对话。

不做：修改页面代码、替用户确认产品决策、把 Figma 不可比字段判为缺陷、把未执行伪装成通过。核心设计数据、权限或对象对应性无法确认时停止受影响阶段。

**不适用场景**（应直接说明而不是硬跑）：移动端原生 App 与小程序（无 DOM，提取手段不成立）、非 Figma 设计源且用户没有导出物、页面不可访问且无授权态。缺少设计稿时只能做页面 UI 走查，报告标题与结论**不得**写"还原度"。

## 术语：流程步骤与检查维度

- **流程步骤 1–9**：本文件定义的执行顺序（见「标准工作流」的阶段契约卡）。
- **检查维度 7.x**：稳定命名空间，权威定义在 `references/check-dimensions.md`，机器可读版在 `schemas/check-dimensions.json`。覆盖率按维度统计。**历史教训**：7.x 曾被写成「步骤」，正文重构后对账表全部定位不到——`7.x` 一律不写「步骤 N」。
- `references/` 里的 `7.x` 一律指检查维度，不指流程步骤。

## 参考规则按需加载

正文只做路由。执行到对应阶段必须读取下列 references，禁止凭记忆跳过（各阶段完整口径见 `references/workflow_steps.md`）：

| 阶段 | 必读 reference |
|---|---|
| 维度定义与覆盖率口径 | `references/check-dimensions.md` |
| 工具与依赖、凭据落地 | `references/tooling.md`、`references/setup-guide.md` |
| 设计字段提取 | `references/figma-fields.md`、`scripts/figma_mcp_to_json.py` |
| 页面提取 / 输入产出契约 | `references/extraction.md`、`references/scenario-contract.md` |
| 登录态 | `references/login-detect.md`、`references/headed-login-fallback.md` |
| 对象对应性 | `references/page-design-match-check.md` |
| 像素 diff | `references/visual-parity.md`、`references/diff-scoring.md`、`references/stabilization.md`、`references/ignore-regions.md` |
| 图标 / 响应式 | `references/icon-diff.md`、`references/responsive.md` |
| 行为、健壮性与资源偏好维度 | `references/bug-driven-checks.md`、`references/button-audit.md`、`references/aria-diff.md` |
| 页面注入型探针（响应式 / 补充检测 / 对齐 / 顺序 / 图标 / 运行期） | `references/script-orchestration.md`（统一驱动 `scripts/node/run_page_probes.cjs`） |
| 状态覆盖 / 跨页旅程（7.39） | `references/state-journey.md`、`references/journeys.md`；驱动 `scripts/node/run_state_scenarios.cjs`、`scripts/node/run_journeys.cjs` |
| 运行期观测（异常/网络/重复提交/遮挡） | `references/runtime-observability.md` |
| 性能 / 可访问性 / 设计侧对比度 / 键盘 | `references/performance.md`、`references/a11y-rules.md`、`references/design-contrast.md`、`references/keyboard-a11y.md` |
| 跨轮 flaky / 写操作清理 / 定位可信度 | `references/flaky-analysis.md`、`references/write-ops-cleanup.md`、`references/locator-quality.md` |
| 执行档位（smoke / standard / full） | `assets/run_profiles.json` |
| 回归 / 真实环境验证 | `references/baseline-regression.md`、`references/real-env-verification.md` |
| 报告与门禁 / 脚本命令 / 失败恢复 | `references/report-templates.md`、`references/gates.md`、`references/script-orchestration.md`、`references/error-handling.md` |
| 机器可读契约与缺陷模式池 | `schemas/check-dimensions.json`、`schemas/scenario-coverage.schema.json`、`schemas/defect.schema.json`、`assets/defect_patterns.json` |

## 触发后的输入确认

用户需要提供（Figma URL 在无设计稿时可省略）：

1. Figma URL（提取 `fileKey` 和 `node-id`；API 使用时把 `-` 转成 `:`）。**没有设计稿时可不提供**，按「标准工作流」R0 输入矩阵降级为页面 UI 走查，不得产出"还原度"结论。
2. 页面 URL（本地或远程）。
3. 如有登录，提供已授权的 Cookie/storage state 文件路径或选择人工登录；不索取密码。
4. **交互安全策略（每次测试开始前必问）**：必须用 `AskUserQuestion` 让用户选择「全部操作」「安全操作」「写操作拦截」或「仅静态检查」。未获得本次明确选择前，不得执行任何点击、键盘输入、表单校验、路由往返或可能触发请求的交互；不得沿用上一次运行或历史对话中的选择。
5. 可选：目标断点、环境 URL、基线目录、重点流程、需要走查的角色列表。

安全策略选项与执行边界：

| 用户选择 | 允许执行 | 禁止或限制 |
|---|---|---|
| `全部操作` | 仅在用户明确确认当前为隔离/测试环境后，执行 safe、caution、danger 全部分级操作，包含真实写入、删除、清空、同步、支付、退款、解绑、下线等动作 | 必须在报告记录用户原始选择、`testEnvironmentConfirmed=true`、每个高风险动作的前后证据；若无法确认环境，拒绝启用本模式，不得用于生产环境 |
| `安全操作` | safe 分级操作：查询、筛选、展开、收起、分页、查看、取消、关闭、下载、复制；每次操作后还原页面 | caution/danger 操作不触发；在报告标注未执行原因 |
| `写操作拦截` | safe 操作真实执行；caution 操作必须先拦截并 **abort** 所有非 GET/HEAD/OPTIONS 请求，仅观察前端信号（**不造假 200**——假响应会让"点击有效"的判定失真） | danger 操作永不点击；不得绕过拦截或真实落库；报告须注明已放行的初始化写请求数（`initAllowedCount`） |
| `仅静态检查` | 截图、DOM/样式/ARIA 盘点、无副作用探测、视口与媒体模拟 | 不执行点击、键盘输入、提交、路由往返、下拉/浮层打开、表单校验 |

将用户的原始选择、解析后的模式、拦截策略与受影响的未执行维度写入 `run-manifest.json` 和报告。Figma URL 缺失不算缺料，按输入矩阵降级；缺页面 URL 时只询问缺失项、不猜历史地址（完整口径见 `references/setup-guide.md`「必需输入」）。用户只提供部分输入时，按 `references/setup-guide.md` 的「最小可行跑法」说明能覆盖到哪一步、哪些维度必须记 `notRun`，不因缺料整体拒答。

## 环境路由

只有明确启动 UI parity 测试时才进入环境路由。启动信号满足其一即可：

- 用户明确说「开始/重新执行 UI parity、Figma 对照、视觉还原或 UI 走查」，或提出**响应式 / 断点适配 / 自适应**类检查诉求（如"看看这个页面的响应式有没有问题""检查下各断点的适配"）；
- 同一轮输入同时提供 Figma URL 和页面 URL。

单纯咨询依赖、解释 Playwright/MCP、查看 Skill、或只说"响应式是什么"这类无检查意图的话题时不触发。**只提供单个页面 URL 但带有走查 / 响应式 / 断点等检查意图时正常启动**（按「标准工作流」R0 输入矩阵降级为页面侧链路，不做设计稿比对）；只有「无指令文本 + 单个页面 URL」不构成启动信号。

正式启动必须先由编排层调用 `AskUserQuestion` 取得本轮安全策略，再调用启动门禁：

```bash
python scripts/start_test.py \
  --instruction "<用户原始指令>" \
  --figma-url "<Figma URL>" \
  --page-url "<页面 URL>" \
  --safety-policy <all|safe|mockWrites|staticOnly> \
  --user-safety-selection "<用户原始选择>" \
  [--test-environment-confirmed]
```

`start_test.py` 只在识别到正式测试启动后执行 `init_workdir.py` 与 `check_environment.py`，写入 `parity-test/run-manifest.json`；缺少安全策略或 `all` 未确认隔离测试环境时以非零状态阻断。咨询类输入直接返回 `started=false`，不产生环境检查副作用。`init_workdir.py` 负责建目录并把凭据相关忽略规则写进工作区 `.gitignore`（只追加缺失行）。

`check_environment.py` 只输出脱敏状态——**其 8 项依赖检查硬编码在脚本内（`check_environment.py:51-95`），并不读取仓库根的 `skill-dependencies.json`**；改那份清单不会改变检查结果，要改检查项必须改脚本。脚本内 `VERSION` 必须与 SKILL.md frontmatter 一致（`scripts/tests/test_environment_contract.py` 强制）。状态四态：`ready`（直接执行）/ `partial`（核心流程继续，报告受影响能力）/ `needs_setup`（只提示本次缺少的必需依赖）/ `unavailable`（说明认证、权限、版本、额度或服务故障，给出降级和恢复路径）。

| 能力 | 依赖 | 降级 | 停止条件 |
|---|---|---|---|
| 设计数据 | Figma MCP 或 REST + 已配置 token | 仅处理用户提供的导出 JSON/PNG | 没有可信设计数据 |
| 页面提取 | Playwright MCP 或受控 Playwright | 仅做静态文件检查，标记未验证 | 没有页面访问能力 |
| 像素 diff | visual-parity | 用 `diff_score.js` 做确定性差异量化 | 不得声称像素级通过 |
| 报告导出 | Python | 输出 JSON/HTML，表格走 `gen_excel_fallback.py` | 不得声称表格已生成 |
| 登录 | 用户提供 storage state/Cookie 或人工登录 | 公共页面继续 | 受保护页面无授权状态 |

## 标准工作流

> 每阶段的完整口径、脚本行号级判据与实测教训见 **`references/workflow_steps.md`**（执行到某阶段再打开对应小节）。本节只做路由与契约；两处表述冲突时**以 references 为准**。

### R0 输入路由（决定主链路）

| 手上有什么 | 主链路 | 必跳阶段 | 必须标注 |
|---|---|---|---|
| **设计稿 + 页面** | 第 1–9 步（`start_test.py` → 各阶段） | 无 | 按实际执行填覆盖率 |
| **只有页面 URL** | `python scripts/run_parity_pipeline.py --workdir ./parity-test --page-url "<页面URL>" --page-json ./parity-test/page.json --safety-policy "<本轮已确认模式>" --report ./parity-test/report.json --outdir ./parity-test/reports`（**不传** `--design-json` / `--figma-png`） | 第 2 步设计侧建模；字段比对与像素 diff 自动跳过并留痕 | 7.1–7.8、7.23 记 `notRun:未提供设计稿`；**报告标题与结论不得写"还原度"**。（**整合标注 v1.14.0**：本链路默认由砺衡专家按「专家主动升级」触发，或用户显式要求页面侧深查时使用；常规无设计稿走查走砺衡主体五视角） |
| **只有设计稿导出（PNG/JSON）** | 第 1–2 步（设计侧建模 + 字段换算） | 页面提取、交互、运行期、性能 | 除设计侧外全部 `notRun` |

缺 Figma URL 不等于流程中止（按上表继续页面侧链路）；只有连页面 URL 都没有时才停止。确定性主链路走 `run_parity_pipeline.py`，单脚本仅用于补跑或排障。`--page-json` 需先由第 3 步的页面盘点产出（`collect_page_inventory.js` 经 MCP `browser_evaluate` 或 `run_official_extraction.cjs` 注入，**不是独立 CLI**）；尚未产出时 pipeline 会把依赖它的阶段标 `notRun`。**`--safety-policy` 默认值 `safe` 仅 CLI 兜底、不代表已授权**——直接调 pipeline 必须显式传入本轮经 `AskUserQuestion` 确认的模式，选 `all` 还须显式加 `--test-environment-confirmed`，管线入口直接校验，缺失即 rc=2 拒绝执行。路径变量：`PARITY_WORK_DIR=./parity-test`、`REPORT_DIR=./parity-test/reports`、`BASELINE_DIR=./parity-test/baseline`、`AUTH_DIR=./parity-test/.auth`；写操作台账固定在 `PARITY_WORK_DIR/write_ops_ledger.json`。

### R1 意图直达（发生在 `start_test.py` 门禁与安全策略确认**之后**）

| 用户意图 | 直达（owner 文档 + 脚本） | 本轮执行维度集 | 其余 45 维度 |
|---|---|---|---|
| 响应式 / 断点 / 自适应 | `references/responsive.md`；`node scripts/node/run_page_probes.cjs --url "<页面URL>" --probes responsive`、`assert_constraints.py` | 7.4、7.9 | `notRun:单意图直达` |
| 视觉差异 / 像素 / 还原 | `visual-parity.md`、`diff-scoring.md`、`stabilization.md`；`node scripts/node/run_page_probes.cjs --url "<页面URL>" --probes alignment,order,icons` | 7.1–7.8 | 同上 |
| 按钮点不动 / 浮层 | `button-audit.md`；`scripts/node/run_button_audit.cjs` | 7.18、7.22、7.23 | 同上 |
| 白屏 / 报错 / 接口异常 | `runtime-observability.md`；`node scripts/node/run_page_probes.cjs --url "<页面URL>" --probes runtime` | 7.33–7.38、7.45 | 同上 |
| 无障碍 / 键盘 / 对比度 | `a11y-rules.md`、`keyboard-a11y.md`、`aria-diff.md`、`design-contrast.md`；`node scripts/node/run_page_probes.cjs --url "<页面URL>" --probes aria` | 7.19、7.41–7.43 | 同上 |
| 性能 / 加载慢 | `performance.md`；`scripts/node/run_performance.cjs` | 7.40、7.44 | 同上 |

> `--probes` 只跑意图对应的探针段（可选 `responsive,extra,alignment,order,icons,runtime,aria`），其余探针不启动；页面内探针（responsive / runtime / performance_probe 等）一律由对应驱动注入执行，不得直接 `node` 运行探针 js（口径见 `references/script-orchestration.md`）。未纳入维度记 `notRun` + 原因，覆盖率照实下降（语义同 `assets/run_profiles.json` 执行档位）；`coverage_gate.py` 要求 45 维度齐全——直达省的是读文档量，不是记账量。

### R2 中断触发

| 出现什么状态 | 打开 |
|---|---|
| 401/403、429、token 中途失效 | `references/error-handling.md` 对应节 |
| 管线 rc=2（门禁熔断 / 计划缺维度） | `references/gates.md` |
| 对象 mismatch / 自动映射不唯一 | `references/page-design-match-check.md` |

### 执行契约

1. **结论六态**：`pass` / `fail` / `warn` / `pending` / `notRun` / `dataMissing`；`notRun` 与 `dataMissing` 必须写明原因，`pending` 与 `notRun` 不得混用。
2. **45 维度全覆盖**：无证据只能 `notRun`，严禁推断 `pass`；契约卡「必读」未读的维度一律 `notRun` + 原因。
3. **命名空间**：`7.x` 一律指检查维度（权威 `references/check-dimensions.md` / `schemas/check-dimensions.json`），与流程步骤 1–9 正交，禁止写成「步骤 N」。
4. **红线**：token/Cookie 原文不落盘；不自动填充账号密码；危险写默认拒绝，`all` 须确认隔离测试环境。
5. **安全策略**：四档（`all` / `safe` / `mockWrites` / `staticOnly`）必须当场 `AskUserQuestion`，不沿用历史；选项权威 `references/setup-guide.md`，执行边界权威 `references/button-audit.md`。
6. **权威顺序**：各领域 references > 本文件；工作流完整口径 = `references/workflow_steps.md`。
7. **版本一致**：frontmatter `version` 必须等于 `scripts/check_environment.py` 的 `VERSION`（测试强制）。
8. **结论红线**：缺设计稿不得写「还原度」；不得声称像素级通过或「通过 WCAG AA」；未确认的临时基线只作参考。

### 阶段契约卡（1–9）

| 阶段 | 进入条件 | 必读 | 唯一产物 | 门禁 | 失败出口 |
|---|---|---|---|---|---|
| 1 建运行清单 | start_test 门禁通过 | workflow_steps.md §1 | run-manifest.json | start_test.py | 缺安全策略 rc≠0 |
| 2 设计侧建模 | 有 Figma URL | §2、figma-fields.md | design_model.json | extract_design_fields.py | MCP 不可用→导出降级 |
| 3 盘点与对应性预检 | 有页面 URL | §3、login-detect.md、page-design-match-check.md | page.json（盘点） | collect_page_inventory.js | mismatch→AskUserQuestion |
| 4 逐字段比对 | 3 完成 | §4 | styleDiffs.json | compare_fields.py、assert_constraints.py | 视口宽偏差>2%→几何类 notComparable |
| 5 场景生成与执行 | 3 完成 | §5、scenario-contract.md | scenarios / state_scenarios | discover_scenarios.py、plan_run、pre_assemble_gate | 截断须写明 |
| 6 十层检查 | 5 完成 | §6 与各层 reference | 各层 findings json | 各层脚本 | 见 R2 |
| 7 交互安全执行 | 用户已选策略 | §7、button-audit.md | 审计产物 + write_ops_ledger.json | run_button_audit.cjs、write_ops_ledger.py | unselected→notRun |
| 8 归一去重校验 | 各层产物齐 | §8 | findings.json（归一） | normalize_evidence.py、validate_defects.py | 结构不合法不得出报告 |
| 9 门禁报告交付 | 8 完成 | §9、gates.md、report-templates.md | report.html / report.json / defects.csv | 见下方命令链 | 覆盖率<70%→顶部横幅 |

各阶段易漏判据（完整口径以 workflow_steps.md 对应节为准）：

- **阶段 2**：`node-id` 指向 COMPONENT / INSTANCE / 子 FRAME 或尺寸明显小于页面时，先浅层枚举（`depth=1/2`）映射页面级 FRAME，不得直接当页面设计稿；MCP 不可用才用导出 JSON/PNG 并标 `partial` / `dataMissing`。
- **阶段 3**：需要登录且无有效 storage state 时用 `save_auth.cjs --headed` 等用户手动登录，不得索取密码/Cookie；**宽高同时为 0** 的元素不入库（单边为 0 仍入库），每项记 `nth`；`mismatch` 必须询问后才能继续，`suspected` 顶部警告。
- **阶段 4**：坐标基准先归一并输出 `positionBasis`；容差（ΔE≤3、±1px、±2px、字距±0.5px、圆角±1px、阴影模糊±2px）内记 `withinTolerance`；透明背景 `rgba(0,0,0,0)` 跳过；按钮缺失必须双通道取证（盘点 + 可见完整文本），落地在 `run_button_audit.cjs`，不在 `compare_fields.py`。
- **阶段 5**：场景必须绑定元素/动作/预期/断言，每输入框 12 类异常数据；缺陷模式池 `--patterns assets/defect_patterns.json` 只影响覆盖深度、不自动生成断言，未接入时 `patternFeedback.applied=false`；`extra_checks.js` 只覆盖 7.20/7.24/7.25/7.27–7.31；7.26/7.32/7.33/7.34 由 `run_state_scenarios.cjs` 驱动（emulateMedia / route fulfill 500，零真实写）；7.39 由 `run_journeys.cjs` 驱动（同源 BFS 有界点击，判据 `references/journeys.md`）；7.35–7.38 与 7.45 由 `runtime_observability.js` 交互前装钩子采集；`plan_run` 按 `assets/dimension_manifest.json` 逐维度排程，manual 维度无合规豁免理由即 rc=2 熔断；场景执行率只看 `scenario_coverage.py`（`--enforce` 拦截未写原因与低于 `--min-rate`）。
- **阶段 6**：截图倍率不一致 `diff_score.js` 直接 `size mismatch` 并 exit 2，不缩放硬比；CLS 无 buffered 入口必须 `addInitScript` 加载前装观察器，事后装拿不到；首响应时延**不是 INP**，必须写「首响应 xx ms」（≥2000ms 缺陷、1000–2000ms warn）；a11y 内置 15 条规则是 WCAG 子集，更全覆盖传 `--axe`；设计侧对比度底色为图片/渐变/半透明降级 `pending`，不按白底硬判。
- **阶段 7**：caution/danger 必须登记写操作台账（`reversible` 必填），收尾 `--plan` 清理 / `--mark-clean` 回填 / `--merge-pending` 进报告「待确认」——不登记等于环境残留不可追；`mockWrites` 不造假 200；隐藏容器内零尺寸按钮记 `zero-size`。
- **阶段 8**：每条缺陷必须带维度号（7.x）、现象、设计值、实际值、复现步骤、页面定位、证据来源与验证状态，结构以 `schemas/defect.schema.json` 为准（含 `element{selector,nth,bbox,anchor,frame}` / `designRef` / `evidence` / `reproduction` / `sources`）；同一 `fingerprint` 只留一条并合并 `sources` / `evidence`（像素层、样式层、清单层同问题必合并）；缺陷结构不合法不得出报告；覆盖率不足照常出报告，由 `meta.coverage` 标注结论不完整。
- **阶段 9** 命令链（顺序不可颠倒）：

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

- 门禁通过后、出报告前依次两步（顺序不可颠倒，参数见 `references/script-orchestration.md`）：① `enrich_defects.py` 缺陷收口富化——`sourceClass` + `sourceNote` 标注问题来源（规范缺陷 / 实现缺陷 / 共同缺失，无对照数据留空不猜）；对比度缺陷现象带设计色值对照；复现步骤到按键/面板级。② `node/capture_defect_shots.cjs` 补位置图——用 assemble 产出的 `element.selector / nth`，**必须同一次运行内完成**；文件名用缺陷指纹前 8 位 + 维度（不是序号）；定位不到只写 `shotError`，不出假图；页面打不开/疑似登录页/非默认状态未复现时不得拿首页冒充证据。
- 统一出口 `python scripts/generate_reports.py --input ./parity-test/report.json --outdir ./parity-test/reports`，输出 `report.html` / `report.json`（含 `meta.coverage` / `meta.defectValidation`）/ `defects.csv`（UTF-8-SIG、严格 10 列、截图相对路径）。
- **交付前必须跑 `python scripts/smoke_chain.py` 并全绿**（端到端串联提取→比对→校验→归一→报告及各门禁）；任何字段契约改动后必须重跑。真实环境验证按 `references/real-env-verification.md` 执行 `scripts/capture_real_env_evidence.py`，该步骤只能在用户真实环境执行，自动化改造过程不得持有真实凭据。

## 失败与恢复

按 `references/error-handling.md` 处理：管线层不自动重试（重跑可能产生二次点击/二次写）；401/403 进入认证恢复、429 退避、token 中途失效按其步骤执行；部分数据失败继续非受影响阶段并标注缺失。核心设计数据、页面权限、对象对应性或元素定位无法可靠取得时停止相关结论；报告只声明实际完成的范围。

## 交付总结

对话中输出：总场景数与已执行/未执行数、覆盖率与未覆盖维度、缺陷数量、P0/P1 Top 项、新增/修复/持续回归、降级能力、三个报告路径、本次阈值与生效忽略项；不得只说"通过"，必须给出证据覆盖范围。跑过就不能省：性能给关键值与网络档（无节流注明"仅供参考"）与首响应 p50/p90；可访问性给双端命中数与键盘项数；网络矩阵 500/断网/弱网分别给结论，没跑成点明"该条件未测"；稳定性给翻转维度数与最不稳前 3；写操作清理给已清理/未清理/不可恢复条数并点名未清理项；档位说明被排除维度，`requiredButMissing` 非空须指出。完整清单见 `references/workflow_steps.md` §交付总结。