# SYNC-NOTES — 抽取副本人工对照清单与版本变更记录

> 文件分两部分：**上半部分**（对照清单）是开发期抽取副本的人工对照，**运行时完全不读**
> （集成方案 v2.4 §9）。**下半部分**按版本追加变更记录与发布说明，是本包的变更日志载体。
> 冻结源：qa-req2testcase-generator **4.17.0**（以 SKILL.md frontmatter 为准），frozen_at **2026-11-16T18:12:05+08:00**（policy.json meta）。
> 已知风险并接受：源 skill 升级后副本可能漂移；漂移只影响判据新旧，不影响本包运行（运行时不读取 qa 的任何路径、版本或配置）。

## 对照清单

| # | 对照对象（qa 侧） | 副本（本包） | 比什么 | 状态 |
|---|---|---|---|---|
| 1 | `tools/export_excel.py` `COLUMNS` | `skills/case-generator/references/design-spec.md` §1 与 `config/policy.json` views.columns | 19 列名与顺序逐字 | 已对照（4.17.0；「需求」列取 project 值的口径一并保留） |
| 2 | `config/test_design_capabilities.json` | `config/policy.json` structure/authority | 枚举、budget、forbidden_patterns、coverage_layers | 已对照（阶段 0 §7A 对账：priority P0-P2、attributes 校验器七类、layers 五类、词表以 case_data.py 为权威） |
| 3 | `SKILL.md` 元规则 9/10/12 + orchestrator 阈值段 | `config/policy.json` thresholds | 冒烟三层/P0/去重/小样本/分批阈值 | 已对照（定稿值见 policy.json，各带 role） |
| 4 | `tools/gate_checker.py` G1~G7、`tools/p5_validate.py` V05~V13 | `skills/case-generator/scripts/gate_checks.py` | 判据与白名单 | 已对照（用例化改写：G1 禁止模式+确认拦截、G2 九种模糊表述+具体性信号、G3 A≥3 步/B 不标冒烟、G4 冒烟来源、G5 Jaccard+占位并集、G6 合并模式+豁免、G7 追溯；V01-V04/V10 明确不搬，见 policy.gates.not_migrated。**G1 规则集已抽象为 `policy.gate_rules.g1` 可配置**（方案 §12，2026-11-17 落地：脚本内置默认与 policy 段一致，policy 段存在时以 policy 为准；确认分级豁免简化为纯「确认」步拦截，恢复口径见 policy.gate_rules.g1.confirm.note） |
| 5 | `prompts/P0.5_prd_quality_review.md` 八维度 | `skills/requirement-review/references/prd-dimensions.md` | 维度清单与 blocking 判定 | 已对照（只取维度与判定，不取分数/权重/等级） |
| 6 | `prompts/schemas/*.json`（16）、`knowledge/**`（4） | `skills/case-generator/references/legacy-schemas/`、`references/methodology/` | 复制件完整性 | 已对照（一次性复制，参考用）。**v1.19.0 起**：schema 目录改名 `legacy-schemas/` 并加 README 声明"未实现、不代表支持 px 等能力"；`methodology/` 4 份已由 `case-generator/SKILL.md`「方法论参考」逐份回链（此前零引用属孤儿） |

## 人工对照方法

1. 读取 qa 侧 `SKILL.md` frontmatter 确认当前版本，逐段读取上表左列文件。
2. 19 列：diff `export_excel.py:COLUMNS` 的列名与顺序 ↔ `policy.json` views.columns 的 header。
3. 阈值：diff gate_checker/orchestrator 数值 ↔ `policy.json` thresholds（含 role 标注；冲突时回源裁定后更新 policy 并记录）。
4. 词表：diff `case_data.py` 常量（NONCOMMITTAL/PLACEHOLDERS/METHOD_CLAIMS）↔ `policy.json` authority.mirrored_constants（校验器为权威，policy 只存副本）。
5. 对照后更新 `policy.json` 的 `meta.source_version` / `meta.frozen_at` 与本清单状态列。

## 本次抽取未搬运清单（有意不搬，防误判遗漏）

orchestrator 状态机、6 次「继续」确认交互、gate/HMAC 五元哈希绑定、主会话逐批 P6 调度、图片抽取/OCR/视觉理解、L5 知识库（459M）、`p6_templates.py`、`p6_guide.py`、评分等级与分数放行。理由见集成方案 v2.4 §2「不搬清单」与 §12（`render_xlsx` 不得整段复制 `export_excel.py`：HMAC 硬校验与 `p6_output` 结构假设，质检一律留给 gate_checks.py）。
## 审核修复记录（2026-11-17，v1.10.1）

| 审核编号 | 动作 | 落点 |
|---|---|---|
| D4 | review-summary 恢复方案 §5 固定六节（删除第七节「已确认规则备查」，confirmed_rules 数据仍在 JSON 事实源中） | `skills/requirement-review/scripts/render_review.py` |
| D3 | G1 判据抽象为 `policy.gate_rules.g1` 可配置规则集，脚本内置默认与 policy 段一致；确认分级豁免简化口径写入 policy note | `config/policy.json` + `scripts/gate_checks.py` |
| D1（机制部分） | 门禁耗时 `elapsed_ms` 由 gate_checks 报告自动采集并汇入 aggregate-report.json；`calibration.status` 改 `mechanism_ready`（token 由会话侧登记，数值待真实样本回填） | `scripts/gate_checks.py` + `config/policy.json` |
| D5 | batches.json 的超集字段（unit_id/feature_ids/task_id 等）经审核确认保留，写入记录 | 本表（无代码改动） |
| R4 | requirement-review SKILL.md 内联 ID 规则最小集，消除对 case-generator 文档级强引用 | `skills/requirement-review/SKILL.md` |
| D7（方案侧） | 集成方案 v2.4 §1 表格「40」笔误按 §8/§13C 定稿修正为 30 | `outputs/砺衡_能力集成方案_包内Skills_v2.4.md` |
| D2（结构层） | 回退验证：临时删除 plugin.json skills 数组后 `validate_expert.py` 通过（专家原能力结构完好），恢复后再次通过 | `.codebuddy-plugin/plugin.json` |
| 验收补充 | 19 列 required 14 列非空率 100% 显式断言；跨视图 case_id 一致正式测试；门禁报告 elapsed_ms 断言；摘要六节结构断言 | `tests/test_render_xlsx.py`、`tests/test_gate_checks.py`、`tests/test_render_review.py` |

D1 数值回填（单批 token 与耗时统计）、五场景路由的剩余 3 条实操、G5-W 告警线校准：留待首次真实需求跑批时执行，不属本次修复范围。
## api-test 包内 skill 对照表（v1.12.0，2026-11-17）

- **剥离源**：wjx-api-test **5.0.2**（2026-11-13 冻结）。源 skill **零改动**——对照验证以源目录前后快照证明零实质写入（对照用独立 workdir，产物不落源目录）。
- **运行时独立**：包内无外部路径依赖；KB 契约由会话从外部知识库取回后经 `--contract/--kb` 喂入，本包不内置知识库路径。**复审补全（v1.12.1）**：KB 回退链指向外部兄弟 skill 的引用共 13 处（references 6 + 脚本 docstring/标签 7）已全部补"剥离适配"标注（行为不改：回退候选不存在时报错属预期）；`contract.md` 命令表补 M13 双通道指针行（判路 7 导航断链修复，源 skill 既有问题）。
- **不搬清单**：`adapters/sojump.py`（站点适配）、`scripts/business_flow_executor.py`（站点绑定）、`smoke_*_wjx`/`business_flow.*.candidate`（站点资产）、`personal_config.json`（真实配置）、`tests/`（20531 行，自建 14 例替代）、`docs/reviews`、`backup/`、运行产物。
- **行为改造（3 处，剥离适配）**：`flow.py` 默认 adapter `sojump`→`rest`；sojump 分支 ImportError 友好提示（自装可用）；写操作门禁通用化（适配器声明 `is_write_allowed` 即生效，随包适配器均未实现，写保护由 R2 授权与环境配置承担）。
- **已知局限**：信封键仍为问卷星口径（`common_params.py` 硬编码，其它项目接入需按 `envelope_keys` 扩展）；`WJX_*` 命名空间保留兼容。
- **剥离依据**：import 闭包（AST 遍历 4 入口）41 脚本 + 判路/文档配套 4 脚本；引用面改写 59 处（references 中被排除资产的裸引用清零）。
- **双维护**：源 wjx 升级时按本表人工对照；冻结版本 5.0.2。
- **离线示例与链路补全（v1.12.2）**：新增 `assets/smoke_cases.example.json`（通用 2 例，含提取/依赖链 skip_if）——此前 `smoke_cases_wjx` 被排除后判路 4 的 dry-run 无示例可走；`contract.md` 的 M13 行补真实用法（`dependency_scan --contract/--out`、`precondition_scan --prep/--confirm`，均已实跑验证）；SKILL.md 判路 3 注明“未配置 KB 时 `--action` 以校验错误 exit 2 属预期”。自建测试增至 17 例（新增冒烟 dry-run / 依赖扫描 / 回读错误路径三条链路）。

## figma-ui-parity 包内 skill 对照表（v1.13.0，2026-11-17）

- **剥离源**：figma-ui-parity **4.48.0**（版本冻结；初次搬入为 4.47.0，v1.18.1 对齐到 4.48.0）。源 skill **零改动**（源 git 零 WIP + 对照快照证明零写入）。
- **搬入**：SKILL.md（**源复制 + 头部插入砺衡段**，frontmatter `version: 4.47.0` 原样保留——`test_environment_contract` 强制 frontmatter 与 `check_environment.VERSION` 一致）、references **35** 份、scripts **34 py + 16 cjs**、scripts/tests **35 py + 2 cjs**、schemas 3、assets **3（含 `dimension_manifest.json`）**、skill-dependencies.json（宿主依赖矩阵）、test-prompts.json（触发回归基线）、README、.gitignore。
- **不搬**：`.git/`（1322 文件）、`.codeartsdoer/`（IDE 元数据 22）、`.pytest_cache/`、`parity-test/`（源运行工作区 + 认证目录，安全）。
- **剥离适配（2 处行为级）**：① `check_environment.local_playwright_core()` 候选链新增 **`PLAYWRIGHT_CORE_PATH`**（与 `scripts/node/browser.cjs` 候选链对齐；专家包内无 `cdp_driver` 布局）；② 砺衡段声明 Windows 中文环境跑测试需 **`PYTHONUTF8=1`**（测试依赖 UTF-8 默认编码，GBK 环境下 32 errors 的实测教训）。
- **运行前提**：Python ≥3.10、Node + `playwright-core`（已装于 `D:\TestCase\.tools\pwcore`，经 `PLAYWRIGHT_CORE_PATH` 引用）、Windows 测试跑 `PYTHONUTF8=1`；完整采集能力依赖 Figma/Playwright/visual-parity **MCP（宿主）**，未就绪时按官方降级链（页面走查/静态 DOM/截图+样式，不得写“还原度”）。
- **验收状态**：**365 py tests + 2 node --test 全绿**（PYTHONUTF8=1 + PLAYWRIGHT_CORE_PATH）；`check_environment` 四态实测（needs_setup → 适配后 playwright-core ready）；单 stage 实跑（浏览器启动/登录态注入 ✓）；回链 orphan=0。**未完成**：完整 pipeline 双跑对照（真实页面采集长时测试，超预算终止——由 365 单测覆盖，列为待办）。
- **边界**：两套质量体系不混用（45 维度覆盖率/validate_defects/smoke_chain 自治）；无设计稿走砺衡 visual-usability 五视角；借鉴 A1–A4 已落地（结论红线→visual-usability、写操作台账→execution-management、必问矩阵 2 行→agents、报告链自检→output-templates）。
- **双维护**：源 figma 升级时按本表人工对照；冻结版本 4.47.0。
- **复审修复（v1.13.1，2026-11-17）**：`scripts/tests/test_start_gate.py` 的 `test_url_pair_start_writes_manifest_after_policy` 只断言 ready/blocked 两分支，**未覆盖 needs_setup/partial 环境下 start_test 合法返回的 `degraded` 分支**（`start_test.py:293-298`：仅 unavailable 才 blocked）——源作者环境 playwright-core 常备故未暴露；剥离后无 PLAYWRIGHT_CORE_PATH 的环境实测暴露。修复：按同文件既有【审查修复·环境耦合】先例扩为三态分段断言（rc=0 → ready/degraded；rc=3 → blocked），manifest/策略断言保留。验证：口径 A（无变量）365 OK / 口径 B（带变量）365 OK。
- **复审修复 2（v1.13.2，2026-11-17）**：`compare_fields.py` 缺输入/坏 JSON 直接抛裸 Traceback（rc=1），与同包 `render_reports`/`validate_defects` 的 **rc=2 + 友好消息**契约不一致（跨脚本编排会把“用法/数据错误”误读为“判定不通过”）。修复：入口加存在性检查 + 解析 try/except → rc=2 + 中文消息；新增 `test_compare_input_errors.py` 2 例锁定。验证：**367 py tests OK**（两口径）、**`smoke_chain` 61/61 全绿**（官方端到端全链冒烟，首次实跑）、异常矩阵 compare/render/extract/validate 四项 rc 语义一致。

## 整合轮（v1.14.0，2026-11-17）

四 skill 并存后的路由/流程/授权/报告整合（审查→方案 I1–I12+P1–P6→三轮审核→执行；方案见 v2.4 方案文档与对话内三轮审核）：

- **I1 figma 主入口收窄**：description/R0 行改“须提供设计稿；无设计稿走主体五视角；专家主动升级或显式要求才走页面侧”（N2：改后 367 OK + 判路 14 ok 命中不降）。
- **I2 生成边界互斥**：case-generator/api-test 双向互斥句 + layer=api 执行归属（接口执行，结果由主体会话回填单写者台账）。
- **I3 判路表补 2 行**：无设计稿专项走查→主体；多 skill 串行→流水线逐跳。
- **I4/I5 标准流水线**：三跳进门条件/交接路径（`<task>/` 约定）/主动引导/跳过降级；layer=api 执行归接口、结果由主体会话回填单写者台账。
- **I6 默认档位**：默认 safe + 5 类风险必问；四档口径权威=figma setup-guide；安全论证登记（不构成缺口）。
- **I7 报告汇总**：主链 HTML+CSV 为总报告，api/figma 报告为附件；一屏交付摘要模板（output-templates）。
- **I9/I10 检出率整合**：新增 `references/bug-detection-map.md`（三套维度交叉索引 + 主动升级映射；衍生对照物声明）；methodology 异常四问扩共享清单（figma/api 引用）。
- **I11/I12 主动升级与反向引用**：信号→维度映射 6 行 + N1 门禁联动（编排层传 `--safety-policy safe`）；三 SKILL 反向引用总图。
- 验证：四入口回归全绿（123/68/17/367）+ figma TestPromptContract 判路 14 ok 命中不降 + validate ✅ + 路由 8 条静态落表。

## 集成修复轮（v1.15.0，2026-11-17）

基于本轮真实需求实跑的集成审查（9 条发现 E1–E9）逐项修复：

- **E1 Skill 解析污染**：新建 `backup/web-acceptance-assurance.backup.v1.14.0/`；备份目录 18 个与旧版缓存 4 个启用态 `SKILL.md` 全部改 `.disabled`；新增 `tests/test_skill_registration.py` 锁定“备份/旧缓存不得有启用态 SKILL.md”与“plugin.json 四个 skill 与正文一致”。会话内注册表仍缓存旧路径（实测仍解析到 cache/1.14.0），**须重开会话后复验解析到 1.15.0**。
- **E2 门禁假通过**：`exploration_state` 的 `views` 保留全量，`executed_views` 仅 PASS/FAIL，`skipped_views` 仅 BLOCKED/SKIPPED；`missing_views` 与 `verdict_ceiling` 按实测计算；`EXP-TRAV-` 只有非 NOT_RUN 才算已记录。同步 `exploration-runtime.md` 与 agents「批次结论门禁」。
- **E3 生成链路消歧**：case-generator description 加“需求级不要改走 qa-req2testcase-generator”；agents 判路表增两行；台账 schema 不符时给定向提示。
- **E4 UI 口径汇流**：agents 增“升级后的回写契约”，figma 六态映射在 `bug-detection-map.md` 固化；新门禁 `COV-UI`（visual/accessibility 零用例须在 applicability 逐类裁定）落到 `gate_checks.py` + `policy.json.gate_rules.cov_ui`。
- **E5 第 3 跳机械门**：`execution_ledger init` 新增 `--batches` / `--gate-report`，批次未全 done 或门禁 fail 拒绝建台账，baseline 记 `gate_precondition`；三跳表第 3 跳改为“门禁无 failed + 探索清单已初始化”，明确 exploration gate 属批次结论门禁而非执行前门禁。
- **E6 五视角 CSV**：新增 `skills/case-generator/scripts/render_csv.py`，视角映射复用 `exploration_state.VIEWS`、列头复用 `policy.views.columns`，utf-8-sig + 回读校验；SKILL.md 输出路由与命令速查已接入。
- **E7 缺陷 ID 命名空间**：台账 `_bugs()` 接受 `source`/`external_id`；agents 与 `bug-detection-map.md` 固化 `BUG-`/`API-`/`FIG-` 前缀。
- **E8 文档对齐**：agents “三个 skill”改“四个”并补 figma 条目；项目级与包内 figma description 统一（设计稿走本 skill，无设计稿常规走查走主体五视角，页面侧仅升级/显式要求）。
- **E9 发布治理**：版本 1.15.0 同步到 `plugins/cache/.../1.15.0`，旧缓存禁用；本轮改动提交 git。

**验证**：专家包根 132 OK；case-generator 74 OK；figma 367 OK；合计 573 Python 测试通过。注入复验：五视角 4 项 SKIPPED/BLOCKED 时 gate 从“通过”改判“待核实”，`missing_views` 含 4 项。

## 改动审查修复轮（v1.16.0，2026-11-17）

针对 v1.15.0 改动审查提出的 R1–R12 逐项修复。

- **R1 跳间门禁可绕过**：`_gate_precondition` 增加 `design_ids` 绑定——批次须登记 `case_ids` 且并集与设计用例一致；门禁报告须含非空 `checks`（每项 id + passed/warning/failed）、`S0` 为 passed、`counts.testcases` 等于设计条数。仅 `{"status":"pass"}` 或空 `checks` 的最小载荷一律拒绝（退出码 2，不留工作区）。`baseline.gate_precondition` 增记 `batches_case_count` / `gate_report_case_count`；新增 CLI 反向用例。
- **R2 全 FAIL 仍给通过**：`exploration_state` 新增 `failed_items`，存在 FAIL 时上限降为“待核实”且 message 明写不得写“通过”；agents / `exploration-runtime.md` / `output-templates.md` 同步。历史 ROUND-001 报告追加“修订说明”，不改变原“不通过”结论。
- **R3 必读文档旧口径**：`output-templates.md` 5.4 删除“SKIPPED/BLOCKED 视为已记录”，改为只认 PASS/FAIL。
- **R4 README 过期**：补齐 api-test、figma-ui-parity 与 `render_csv.py` 条目。
- **R5 死参数**：`_gate()` 去掉未使用的 `views` 形参（AST 复核）。
- **R6 计数失真**：`render_csv` 摘要改为 `view_rows` + `unique_cases`（真实 30 条用例：38 / 30）。
- **R7 渲染器健壮性**：`UsageError`/`VerifyError` 取代中文子串判码（1=回读失败、2=用法/输入）；`task_id` 限 `[A-Za-z0-9._-]{1,64}` 防路径穿越；缺 `case_id` 列给出明确报错，不再抛无消息 `StopIteration`。
- **R8 COV-UI 破坏性变更**：改为两级——声明 `applicability` 的用例集零用例仍 BLOCK；未声明 `applicability` 的存量用例集降为 WARN 并在报告中强制列缺口。`policy.json.gate_rules.cov_ui.note` 与 case-generator SKILL 同步标注。
- **R9 提交非原子**：`b6a36a7` 含 192 文件（夹带 v1.14 未提交文档与 figma skill），无法按项回滚；本轮起改为一次逻辑一提交，本版提交范围仅含本轮修复。历史提交不回写。
- **R10 历史交付物口径**：ROUND-001 执行报告门禁行标注新旧口径差异并附修订说明。
- **R11 指代不明**：agents “前两者产物”改为“评审与生成两跳的产物”。
- **R12 执行文档缺新参数**：`execution-management.md` 补 `init --batches/--gate-report` 命令与全部拒绝条件。

**验证**：专家包根 134 OK；case-generator 78 OK；figma 367 OK；合计 579 Python 测试通过。真实产物复验：真实 batches+gate 建台账成功（`gate_report_case_count=30`）、最小载荷 rc=2、少登记一条用例 rc=2、真实 30 条用例 CSV 摘要 `view_rows=38 / unique_cases=30`、非法 task_id rc=2、存量用例集 COV-UI 为 warning。

## 浏览器通道文档（v1.17.0，2026-11-17）

此前执行期只有 `browser_observer.js` 等采集脚本，没有「用哪套 MCP、怎么连、句柄怎么用」的约定，实测中登录、截图落盘、白屏归因都是临时摸索。本轮按 `web-agent-stream-capture` 技能的连接方式提炼稳定部分，新增包级文档并接入路由：

- **新增 `references/browser-connection.md`**：MCP 判定表（默认 Chrome DevTools，Playwright 为备选）；三种连法与 `--isolated` 禁令；登录由用户手动完成、不读 Cookie、多信号判定；机器人检测两层参数（信息栏开关 + Blink 特性）；`pageId`/`uid`/`ref` 当次有效且两套不互通；`fill` 用 `value`、`wait_for` 无「等消失」；截图需 `--filesystemRoot`；双通道协同（驱动归 Playwright、观察归 DevTools）与未对齐不采信；证据边界（空 issues/成功退出不算通过）。
- **接入点**：agents「参考规范读取契约」增加“首次操作浏览器前读取”与工作流程第 3 步的通道选择段；`exploration-runtime.md` 责任分层指向该文档；README 文件清单登记。
- **新增 `tests/test_reference_links.py`**：校验文档提到的 references 真实存在（按包级/skill 级同名解析，跳过缓存溯源行）、每个 reference 都有回链、agents 与 README 必须路由浏览器通道文档、该文档保留关键约束。变异验证：删除该文档后测试变红。
- 外部技能的深层排障（跨域 iframe、审批卡、流式结束判定）以“本机装有该技能时按其 references 处理”表述，不在本包复制，避免两份说法漂移。

## 设计数据通道（v1.18.0，2026-11-17）

起因：核实「专家有没有参考 figma-ui-parity 用 figma-mcp 取设计数据」时，实测发现取数链路有三处错位，且专家包对取数没有任何约定。

- **新增 `references/design-data.md`**：三来源判定（Figma MCP / REST + PAT / 用户导出物，**按能力不按工具名**）；`nodeId` 用 `1234:5678`、深链分号串整体算一个节点；多画板必须显式指定、不得默认第一个；MCP 返回的 YAML-ish 文本须先 `figma_mcp_to_json.py` 再 `extract_design_fields.py`；取不到时记 `dataMissing` 而非 `notRun`，报告不得写「还原度/像素级」，禁止截图倒推设计值与编造色值尺寸。
- **接入点**：agents「参考规范读取契约」增加“取设计数据前读取”，入口判路表 figma 行改为“先判定取数来源再读其环境路由”；README 文件清单登记；`tests/test_reference_links.py` 扩展为同时守卫 browser-connection 与 design-data 的路由及关键约束。
- **figma-ui-parity 同步修（4.47.0 → 4.48.0）**：通道 A 改为按能力判定，兼容 figma-developer-mcp（`get_figma_data` / `download_figma_images`）与官方远程 MCP（`get_file_nodes` / `get_images`）两族；通道 B 的 token 读取兼容 `headers.X-Figma-Token` 与 `env.FIGMA_API_KEY`/`FIGMA_TOKEN`（原先只读 headers，本机实测 `KeyError`）；`extraction.md`/`figma-fields.md`/`error-handling.md`/`real-env-verification.md` 的工具名表述一并统一。
- **实测**：修复后令牌脚本实跑成功、`/v1/me` 返回 200；`mcp__figma__get_figma_data` 已加载，用假 fileKey 探活返回 404（链路通、非 401）；figma 技能 365 测试 OK。

## figma 副本漂移对齐（v1.18.1，2026-11-17）

起因：核实「包内 figma-ui-parity 是否为全量集成的剥离副本」时逐文件比对，发现 **快照同步只做了一半**——`SKILL.md` 与 `README.md` 已升到 4.48.0，但承载 4.48.0 操作口径的 5 份 `references` 仍是 4.47.0 文本。

- **漂移证据**（源 `ab76825` 的 8 个文件里，包内缺 5 份）：`tooling.md` 88 行 vs 源 100 行，缺「通道 A 两族工具名能力表」（`get_figma_data`/`download_figma_images` ↔ `get_file`/`get_file_nodes`/`get_images`）、「`get_figma_data` 返回 YAML-ish 文本须经 `figma_mcp_to_json.py` 转换」、「`download_figma_images` 的 `localPath` 限制」、「取不到 token 的判据行」；`extraction.md`/`figma-fields.md`/`error-handling.md`/`real-env-verification.md` 各 1 处取数与工具名口径停在 4.47.0（`real-env-verification.md` 甚至只写 `get_file_nodes`）。
- **后果**：包内执行时仍按工具名猜族，只有 figma-developer-mcp 时会被判成「通道 A 不可用」白白降级到 REST；即便取到数据也会把 YAML-ish 文本当 REST JSON 直接喂 `extract_design_fields.py`。SKILL.md 头部却已声明「4.48.0 起按能力判定」——声明与操作口径自相矛盾。
- **修复**：5 份按源文件整体覆盖。覆盖前确认三者皆无（包内无剥离适配注记、无测试断言其文本、无被排除资产引用），故整份覆盖不会冲掉适配。
- **复核**：与源逐文件 `cmp` 后，剩余 4 处差异均为**预期剥离适配**——`SKILL.md`（+10 行包内版声明与整合标注）、`check_environment.py`（`PLAYWRIGHT_CORE_PATH` 候选）、`compare_fields.py`（缺输入/坏 JSON → rc=2）、`test_start_gate.py`（三态断言）；另有包内独有 `test_compare_input_errors.py`。
- **判据补强**：此前「快照同步至 4.48.0」的验收只看 `SKILL.md` 版本号，版本号升了即认为同步完成。此后同步验收必须按**源提交涉及的文件清单**逐份 `cmp`，不能只看入口文件的 frontmatter。
- **验证**：包根 139 测试 OK；包内 figma 367 测试 OK（`PYTHONUTF8=1` + `PLAYWRIGHT_CORE_PATH`）。缓存 `1.18.1` 与工作区 `diff` 为空，旧缓存 `1.16.0`/`1.17.0`/`1.18.0` 的 `SKILL.md` 全部转为 `.disabled`。

### 复审修复（v1.18.2，2026-11-17）

> **版本谱系说明**：本会话早先的整合轮（I1–I12）此后经后续轮次（v1.15.0–v1.18.1：跳间门禁、通道文档、figma 4.48.0 副本对齐等）继续演进；本轮 OM1–OM6 修复基于 **v1.18.1** 基线落盘。包内 figma 实际版本为 **4.48.0**（更早的 4.47.0 冻结表述以本文件尾部 v1.18.1 记录为准）。

遗漏审查 OM1–OM6 一次修完（源：qa `p2_evidence` / api M9·M12 / figma `init_workdir`·degradation）：

| # | 遗漏 | 落点 | 修复 |
|---|---|---|---|
| OM1 | 工作区自动 `.gitignore` | `scripts/execution_ledger.py`（`initialize` + `_ensure_gitignore`） | init 时写 `.auth/`、`*.storage.json`、图片/视频/har/zip/log 忽略行（追加缺失行语义，不覆盖已有）；`tests/test_om_fixes.py` 回归 |
| OM2 | 能力 × 降级 × 停止条件矩阵 | `references/execution-evidence.md` | 8 能力矩阵（trigger / fallback / limitations / stop_condition / evidence_label）；与 figma `tooling.md` 的取数通道能力表**主题相邻但不重复**（前者为专家主体通用降级，后者为 figma 内部通道） |
| OM3 | 测试点来源与原文片段 | `review-result.schema.json` + `prd-dimensions.md` + `review_check.py` | AC 可选 `evidence_refs:[{source,quote}]`（schema + 校验），评审要求 blocking/pending 指原文；回归测试 |
| OM4 | 计划 vs 实际对账 | `batch.py`（plan 记 `estimated_total`、aggregate 出 `plan_vs_actual`）+ `batches.schema.json` | 偏差 >20% 告警不静默；回归测试 |
| OM5 | 套件批准 / 基线晋升 | `references/execution-management.md` | 晋升四条件（全 TC 记录且 bugs 已知 / 显式批准留痕 / 旧基线留档 / 冻结只读） |
| OM6 | 跨 skill 路由回归样本集 | 新增 `references/route-prompts.json`（9 条）+ agents 判路表引用 + `tests/test_om_fixes.py` | 判路表或 description 改动后逐条对照，命中率不降 |

**与既有轮次的兼容性核查**：本轮 11 个改动文件均基于 v1.18.1 现有文本成功叠加，`git status` 仅显示本轮改动（9 改 + 2 新增），未覆盖 v1.15.0–v1.18.1 的提交成果；agents「标准流水线与主动升级」段经后续轮次修正后的口径（第 3 跳机械校验 / exploration gate 为批次结论门禁）**保持不变**。

验证：四入口 **606 tests OK**（包根 144 = 原 139 + OM 5 / case-generator 78 / api-test 17 / figma 367（4.48.0））；validate ✅。

## 能力覆盖补齐（v1.19.0，2026-11-17，依据外部审计 + 自审）

> 一次发布覆盖计划分段 P0–P4（计划里的 1.19.0–1.23.0 未单独发版）；P5 属包外挂载，不改包版本。

### 1. 声明与实现对齐（P0）

| # | 问题 | 处置 | 落点 |
|---|---|---|---|
| 1 | 门禁名写「四层覆盖与方法见证」，实现只到 L1（S0 保证）+ L3 方法见证 | L2/L4 由新增 `COV-LAYERS` 判定（存量集无 `coverage_flags` 时降告警，新集缺层才 BLOCK），门禁名改为「四层覆盖 L1/L3 + 方法见证」，policy 增 `coverage_layers_note` 与逐层 `implementation/judged_by` | `gate_checks.py`、`config/policy.json`、`SKILL.md`、`design-spec.md` |
| 2 | 16 份 `references/schemas/` 零引用（`px_output` 会被误读为"支持图片理解"） | 改名 `legacy-schemas/` 并加 README 逐条声明未实现；同步改本文件对照表 #6 路径 | `references/legacy-schemas/` |
| 3 | `references/methodology/` 4 份零引用（含四层覆盖原始判据 §5） | `case-generator/SKILL.md` 增「方法论参考」表逐份回链，并指定 §5 为 L1–L4 口径来源 | `SKILL.md` |
| 4 | 门禁改名后跨轮比对可能因 `name` 不同报差异 | 明确"改名仅影响文案，判据不变；跨轮比对按 gate id 不看 name" | 本节 |
| 5 | 旧版缓存 1.18.1 仍留启用态 `SKILL.md` | 4 份改 `SKILL.md.disabled`（`tests/test_skill_registration.py` 的契约要求） | 插件缓存 `cache/my-experts/web-acceptance-assurance/1.18.1/` |

### 2. 判据补强（P1）

| # | 能力 | 实现 | 判据 |
|---|---|---|---|
| A1 | 四层覆盖矩阵 | 新增 `scripts/coverage_matrix.py`：L1 每功能点 ≥1 条 L1 用例；L2 functional 适用时每功能点 ≥1 条 L2，否则记 `not_required` 并回写理由；L3 有见证方法数 ≥ `min_witnessed_methods`（默认 2）且不得有"只有名字"的方法；L4 只统计不计入覆盖 | `COV-LAYERS`：`missing_layers` 非空时——新集（有 `coverage_flags`）且已声明 `applicability` → BLOCK；未声明 applicability 或全表无 `coverage_flags` 的存量集 → WARN 并列入缺口（沿用 COV-UI 存量口径）；矩阵随报告输出 `coverage_matrix` 字段 |
| A2 | 组合覆盖举证 | 移植 `scripts/pairwise.py`（零第三方依赖）；**verify 增加对产物形态 `{"角色":"访客"}` 的兼容**，使"生成 → 独立重算"闭环可落地 | `COV`：声明 `pairwise`/`组合覆盖` 必须整份回填 `parameters/combinations/input_hash`，由 `pairwise.verify` 独立重算，未通过即 BLOCK（不接受生成器自证） |
| A3 | 缺口登记 | `case_data` 新增顶层 `coverage_gaps[]`（`gap_id/kind/target/reason/obligation`，kind 六值枚举、`obligation` 禁占位）；19 列 Excel 增「覆盖缺口」附页（主表仍逐字 19 列）；门禁 counts 增 `coverage_gaps` | 绑不到场景的证据必须进缺口，不得挂无关场景凑数 |

### 3. 效率与导出（P2）

- `render_csv.py --only P0,P1`：精选视图；**回读校验改用筛选后集合为基准**，避免按全集比对导致假失败。
- 新增 `render_testpoints.py`：由 cases JSON 折叠出 9 列测试点评审视图（功能点/验收标准/测试点/覆盖层/设计方法/用例数/最高优先级/含冒烟/缺口随行）。**边界：派生视图，不替代用例生成**，`execution_ledger.init` 会拒收该 CSV（只认含 `testcases[]` 的 cases JSON）。

### 4. 入口质量（P3）

- 新增 `skills/requirement-review/scripts/extract_docx_media.py`：zipfile + hashlib 解包 `word/media/` 并登记 sha256（不调云端 OCR、不判断内容）；识读由宿主多模态完成，结果写 `review-result.json` 的 `visual_evidence[]`（schema 与 `review_check` 同步校验；`image_understood=false` 时必须写 `boundary` 说明缺口，禁止凭图猜规则）。
- 本地文档优先条款：`requirement-review` 与 `api-test` 各写明"接口/规则以本地知识库全文为准，禁止用 WebFetch 抓在线文档替代本地；联网仅核对版本差异并标注；本地缺失报缺不默认联网"。

### 5. 维护侧质量基线（P4）

- 新增 `scripts/eval_cases.py` + `docs/evals-baseline/`：机械指标（缺层数 / L1 比例 / 方法数 / 断言密度 / 空话占位 / 冒烟与 P0 占比 / 缺口数 / 结构错误）跨版本比对，退化退出码 1；基线与样例 `fixture-12.json`。
- **能力边界已写入 README**：只回答"结构是否更完整"，不回答"是否抓到真缺陷"；基线相同不代表质量相同。

### 6. 站点挂载（P5，包外）

- `D:/TestCase/.workbuddy/site-adapters/wjx/`：`sojump.py`、`business_flow_executor.py`、wjx 冒烟配置与业务流候选、`exclude_params.json` 母本 + 落位/移除/自检说明；真实个人配置仍只在 `~/.workbuddy/wjx-api-test/personal_config.json`（用 `WJX_CONFIG` 指过去，不复制）。
- 包根 `.gitignore` 增两条落位文件忽略，防误提交。
- 实测：落位后 `from adapters.sojump import SojumpAdapter` 可导入，移除后 `adapters/` 恢复原状。

### 7. 验证与已知边界

- 测试（`PYTHONUTF8=1`）：包根 **164 tests OK**（含新增 `test_coverage_matrix.py` 7 例、`test_pairwise.py` 7 例、`test_eval_cases.py` 6 例）、case-generator **109 tests OK**（含新增 `test_cov_layers.py` 13 例、`test_render_extras.py` 7 例、`test_visual_evidence.py` 11 例）、api-test **17 tests OK**；figma 367 例本轮未跑（未改动其任何文件）。
- 已知边界：`testpoints` 是派生不是免生成；`eval_cases` 只做机械指标；原型图识读质量取决于宿主多模态能力与图本身可读性，脚本只保证"图被取出来了"。
- 工作区提示：本轮开始前仓库已有未提交改动（`execution_ledger.py`、`batch.py`、`batches.schema.json`、`prd-dimensions.md`、`agents`、`references/execution-*.md`、`review-result.schema.json` 的 `evidence_refs` 等），非本轮产物；本轮改动已尽量保持最小 diff（schema 曾因 JSON 重写产生整篇格式漂移，已回滚重做为 33 行增量）。

## 复审修复（v1.19.0 追加，2026-11-17 19:20，依据第二次独立核验）

| # | 发现 | 处置 | 落点 |
|---|---|---|---|
| R1 | 交付 JSON 视图（`render_json.build_view`）不透传 `coverage_gaps`，"缺口不静默"只对 Excel/测试点视图成立 | 视图在有缺口时输出顶层 `coverage_gaps`；补透传测试（无缺口不出键） | `render_json.py`、`tests/test_render_json.py` |
| R2 | `tests/test_reference_links.py` 的正则不匹配子目录引用，`references/methodology/**` 的回链实际未被守卫覆盖 | 正则扩展为允许 `references/<子目录>/<名>.md`；回链检查覆盖 references 全树（`legacy-schemas/` 整目录隔离除外）；指向其它 skill 文档（`*/SKILL.md`）的资料性链接不校验存在性 | `tests/test_reference_links.py` |

复审回归：包根 164 / case-generator 110 / api-test 17 全绿。

## v3 目标审计整改 A 批（v1.20.0，2026-11-17）

依据《砺衡目标审计与整改执行方案 v3》，先写失败反例再修实现，覆盖 G03/G08/G09/G12 中“错误放行”类机制；保留先行备份 `D:/TestCase/backup/web-acceptance-assurance.v1.19.0.pre-v3.zip`。

| ID | 修复 | 落点 |
|---|---|---|
| G08 | 探索门禁新增“必选义务”约束：未决副作用、必选质量属性（security/compatibility）被阻塞/未执行、必选页面未遍历，均压低 verdict_ceiling 为待核实；非必选项仍只披露。保留有 sibling PASS 时非必选 SKIPPED 不阻断的既有语义 | `scripts/exploration_state.py`(_gate + gate 输出新增 unreconciled_pending/blocked_mandatory)；`tests/test_v3_goal_a.py` |
| G09 | 台账进门新增门禁报告内容绑定：携带 binding(design_hash/policy_version/scope/required_checks) 且 hash 与设计一致→已绑定；缺 binding→标“待核实/兼容模式”；声明必过项未通过或 hash 不符→拒绝 | `scripts/execution_ledger.py`(_gate_precondition 增 design 入参)；既有精确断言测试同步 |
| G12 | 写授权改为适配器无关：只读操作放行；写/未知/空操作需 environment.type=test+write_enabled=true；适配器的 is_write_allowed 拒绝权适用于任何适配器名（不再硬编码 sojump）；门禁判定异常 fail closed | `skills/api-test/scripts/flow.py`(READ_OPS + write_gate 重写) |

回归：A 批反例 10/10、包根 174、case-generator 110、api-test 17 全绿（PYTHONUTF8=1）。

## v3 目标审计整改 B 批（v1.21.0，2026-11-17）

| ID | 修复 | 落点 |
|---|---|---|
| G01 | 评审契约新增可选 scenarios 字段（顶层键 + schema + 结构校验：id/name/goal/paths.kind），workflow/stateful 可由 review_bridge 原样承接，消除“评审拒收但生成强求”的互斥 | `requirement-review/references/review-result.schema.json`、`review_check.py`、`tests/test_v3_goal_b.py` |
| G02 | 新增 `coverage_obligations`（稳定 id/layer/kind/target/source/required/tc_ids）：声明后 L1/L2 以“义务→有效 TC 引用”重算，flags 仅派生；未绑定义务进 `unmet_obligations`/gaps，单改 flags 不再提高覆盖 | `scripts/coverage_matrix.py`(_obligation_overlay) |
| G03 补 | functional=deferred/insufficient_evidence/unspecified 不再记 L2 not_required，保持未覆盖缺口；仅明确 not_applicable 剔除分母 | `coverage_matrix._layer_l2` |
| G04 | 方法见证分类型校验（_witness_valid）：boundary 需 ≥2 具名/具体边界，scenario/state 需真实边，equivalence 需有效/无效类，decision_table 需条件+动作；布尔/字符串/"x" 伪见证不计入 L3 | `coverage_matrix._layer_l3`、`gate_checks` 复用矩阵 |
| G05 | COV-PATH 按“每条已声明异常/权限路径须有 tc.path_id 关联用例或对象形态豁免”判定；flow_kind=simple 一旦声明场景也不再直通；小样本仅放宽比例告警，必选路径缺失仍阻断；字符串豁免继续拒绝 | `gate_checks._cov_path`；`tests/test_gate_checks.py` 夹具补异常路径 TC |

回归：B 批反例 6/6、case-generator 110、包根 180、api-test 17 全绿。

## v3 目标审计整改 C 批（v1.22.0，2026-11-17）

| ID | 修复 | 落点 |
|---|---|---|
| G11 | 回补源 5.0.2 已有但包内遗漏的语义复核修复：新增 `_normalize_newlines`（CRLF/CR 归一），correct_llm_value 的修订值逐项验据——enum 条目只验值不验释义、bool 归一小写、扁平清单逐词验；空 enum 条目判无据而非崩溃。源/包同输入实测一致（enum、bool 均 1 applied/0 rejected） | `skills/api-test/scripts/llm_semantics.py` |
| G13 | 新增 `resolve_adapter_path` 与 build_adapter 的包外加载：WJX_ADAPTER_DIR 指向目录下同名 .py 优先于包内适配器加载（importlib.spec_from_file_location），validate_exec_config 放行包外同名适配器；站点适配器不再需要改写已安装专家包。dry-run 在加载前可解析来源 | `skills/api-test/scripts/flow.py` |

回归：C 批反例 5/5、包根 185、case-generator 110、api-test 17 全绿；源/包语义复核同输入结果一致。

## v3 目标审计整改 D 批（v1.23.0，2026-11-17）

| ID | 修复 | 落点 |
|---|---|---|
| G06 | review_bridge 保留 AC 的 evidence_refs、评审的 visual_evidence/not_reviewed/confirmed_rules；pending 的 AC 归一为 case_data 合法值 provisional + needs_confirmation=true（不再冒充 confirmed）；coverage_gaps 从评审 {area,reason} 机械翻译为 cases {gap_id,kind,target,reason,obligation}，避免直接透传触发校验失败反而丢缺口 | `review_bridge.py`(translate_gaps + 字段保留) |
| G07 | review_check 拒绝 image_understood=false 且带 extracted_rules（禁止凭未识读图猜规则）；render_review 新增「原型/截图证据」章节，列出每张图已/未识读、边界与提取规则 | `review_check.py`、`render_review.py` |
| G17 | 回链守卫语料与枚举扩展到全部 skill 级 references（70 份），跳过 GitHub 出处/外部开源引用行以免误报；孤儿判断相对 ROOT | `tests/test_reference_links.py` |
| G18 | render_testpoints 在 coverage_obligations 存在时即使无 testcases 也能出测试点（稳定 TP id/义务类型/来源/0 用例）；缺口 target 精确 ID 边界匹配（FP-01 不再串 FP-010）；main 接受“仅义务”输入 | `render_testpoints.py` |

回归：D 批反例 6/6、包根 191、case-generator 110、api-test 17 全绿；四类反例逐条复核通过。

## v3 目标审计整改 E 批（v1.24.0，2026-11-17）

| ID | 修复 | 落点 |
|---|---|---|
| G14 | bug-detection-map 升级映射新增 5 类可判定信号（死按钮无反馈、状态残留/读回不一致、控制台 5xx/断网、长文本/空态/未渲染变量、正常异常交替纪律），命中即主动升级，未命中仍不随意扩大范围 | `references/bug-detection-map.md` |
| G15 | 六态映射增“保真纪律”：映射只用于门禁计数，不改外部报告原状态；warn 不以业务 PASS 覆盖、须留原文与严重性；pending/notRun/dataMissing 不因同视角另一 PASS 消失；必选质量属性上限按 `blocked_mandatory` 判定 | `references/bug-detection-map.md` §五.4；配合 A 批 exploration_state 门禁 |
| G16 | 新增 `content`（文案）属性到探索词汇表并列为必选质量属性（与 security/compatibility 同口径）；visual-usability 增文案检查面（术语/单位/日期时区/按钮语义/错误定位/未渲染变量/长文/i18n，客观错误与主观建议分账） | `scripts/exploration_state.py`(ATTRIBUTES/MANDATORY_ATTRIBUTES)、`references/visual-usability.md` |

回归：E 批反例 3/3、包根 194、case-generator 110、api-test 17 全绿。

## v3 目标审计整改 F 批（v1.25.0，2026-11-17）

| ID | 修复 | 落点 |
|---|---|---|
| G10 | eval.compare 退化方向重写：LOWER_IS_WORSE（缺层/结构错误/空话/未关闭缺口上升即退化）+ HIGHER_IS_WORSE（方法数/断言密度/L1 比例下降即退化）；结构错误/空话/缺口不再被忽略，退化退出码 1 | `scripts/eval_cases.py` |
| G19 | 新增独立检出效果评测协议 + 5 个夹具（4 基础场景 + 1 留出）：冻结隐藏答案、合法近似反例、必选分支分母、分账指标（检出/误报/证据完整/未测义务/耗时），未实测保持"效果待验证" | `docs/evals-baseline/detection-protocol.md`、`detection-fixtures/*.json` |

回归：F 批反例 5/5、包根 199、case-generator 110、api-test 17 全绿；三类硬退化逐条复核通过。

## 全面核查修复（v1.26.0，2026-11-17）

核查结论修正：A–F 六批中 6 项（G02/G03/G06/G09/G16/G17）属部分关闭——机制存在但真实链路不可达或可被"不写就没事"绕过。本批按核查报告逐项闭合：

| 项 | 原缺口（可复现） | 修复 | 落点 |
|---|---|---|---|
| G02 | `coverage_obligations` 无生成规范、无契约校验，不写义务时 L1/L2 仍按 flags 自证 | 义务进 `case_data.validate`（id 唯一、layer∈L1/L2、kind 枚举、target 非占位、source 必须存在、required 布尔、tc_ids 必须存在且声明同层）；design-spec §9.1/§10 定义必填；SKILL.md 增"覆盖义务先行" | `scripts/case_data.py`、`skills/case-generator/references/design-spec.md`、`SKILL.md` |
| G03 | 删光 `coverage_flags` 仍按"存量"降 WARN，A 批要求的显式 legacy 登记未实现 | 取消"无 flags 即存量"推断：新资产无义务 → COV-LAYERS failed；仅 `meta.legacy_source{schema_version,migrated_at,owner}` 齐全才 WARN 并披露未迁移义务 | `gate_checks._cov_layers`、`spec §10` |
| G06 | `coverage_gaps` 在矩阵/门禁零引用，已登记缺口判定上消失 | 未关闭缺口按 kind 回落 L1/L2/L3 并压层，进 `gaps`/`open_registered_gaps`；`closed_by` 才算关闭；缺口可选 `severity/required/status` 进契约 | `coverage_matrix._gap_overlay`、`case_data` |
| G09 | 门禁不产 `binding`，只有消费端校验 → 真实报告永远待核实，手写 binding 即可自证 | `gate_checks._summary` 直接输出 binding；哈希抽出 `scripts/design_fingerprint.py`，与 `execution_ledger.fingerprint` 同一实现 | `gate_checks.py`、`design_fingerprint.py`、`execution_ledger.py` |
| G16 | 安全/兼容/文案只在"记录里出现"时生效，整类省略可拿通过 | 清单根 `attribute_rulings` 逐类裁定（applicable / not_applicable+理由 / blocked）；缺失或 applicable 未实测 → 上限待核实，`gate.mandatory_attributes` 明示状态 | `exploration_state.py`、`exploration-runtime.md`、`agents` |
| G17 | 回链守卫仍按"同名文件任一存在"兜底，写错层级也能过 | 按引用方所属根解析（skill 只查本 skill、包级只查包级）；跨层须显式白名单；裸 `references/…md` 写法才纳入匹配，全路径不误算 | `tests/test_reference_links.py` |

附带行为对齐：义务绑定的用例必须声明同层 flags（S0 拦截）；L2 在 functional 非 applicable 时不被义务拉回"已覆盖"；`render_testpoints` 只对未绑定义务单独出测试点行，避免同一功能点裂成多行。

附带：`scripts/legacy_scan.py`（只读扫描存量 cases，分 obligations/legacy/blocked 三类）；另修真实数据暴露的健壮性问题——历史用例的 `method_evidence` 可能是数组，见证校验器改为判"无见证"而非抛异常（此前会中断整套门禁）。

回归：新增 `tests/test_v126_hardening.py` 23 例；包根 199 → **222**、case-generator 110 → **111**、api-test 17 全绿（合计 350）。

## 独立盲测加强 + 判定方法学（v1.27.0，2026-11-18）

**盲测链从"协议文本"变成"可执行、可拒绝、可复算"的工具链**：

| 项 | 修复前 | 现在 |
|---|---|---|
| 答案隔离 | `hidden_answers` 与题面同文件，执行方打开即见 | `blind_eval.py prepare` 只导题面（场景/输入/必选分支/交付格式），答案键留在评测者侧；`verify` 检出答案文件、答案 ID、`executor_saw_answers=true` 即拒 |
| 计分 | 无脚本，靠人工看报告 | `score` 机械计分：按 `match_all/match_any` 词元匹配、按 severity 分账、误报按 `category` 归类、证据文件必须存在、分支分母来自答案键（改名即报错） |
| 运行治理 | 未定义 | `run.json` 记 `fixture_sha256`/`protocol_sha256`/预算/执行方；`compare` 对同执行方、协议变更、夹具不同分别告警并标"不可比" |
| 准出 | 文字条件 | 写进 `verdict`：blocker/major 全检出 + 误报 0 + 分支 100% + 证据齐全 + 未测义务 0 + 无效动作 0 才 pass |

配套：五个夹具的答案与近似反例补 `match` 规则与 `category`（八类）；协议文档重写为八节（隔离/治理/计分/对照/准出/反作弊/逃逸回流/边界）。

**方法论文档补三份**（与源 `qa-req2testcase-generator/knowledge/methodology` 同体例；该目录三份已在包内逐字同步，本次补的是判定侧缺口）：

| 文档 | 内容 | 接入点 |
|---|---|---|
| `detection_methods.md` | 判定链、oracle 六源、七类可判定信号、证据最小集、六态、"只能待核实"四种情形 | `agents` 判定纪律、`bug-detection-map.md`、`case-generator/SKILL.md` |
| `defect_patterns.md` | 11 类缺陷模式（功能/数据/状态/权限/界面/易用/文案/兼容/性能/接口）的最小触发、观察点、常见误判 | 同上；盲测答案键取模式来源 |
| `false_positive_guard.md` | 误报四问、八类合法近似反例、缺陷/待确认/建议分账 | 同上；盲测 `category` 与之一一对应 |

回归：新增 `tests/test_blind_eval.py` 15 例；包根 237、case-generator 111、api-test 17 全绿。

## 本机专家资产盘点与接入（v1.28.0，2026-11-18）

对本机 `experts/plugins` 下 36 个专家做了只读盘点，筛出 10 个测试/质量相关专家逐份阅读，按"是否补砺衡缺口、是否与现有资产重复"决策：

| 专家 | 可借鉴内容 | 决策 |
|---|---|---|
| `bug-test-expert` | 40+ 业务模块坑点知识库（登录/权限/上传/支付/导入/接口/兼容/弱网/安全/无障碍/时区/回调…） | **部分采纳**：只取 Web 新需求验收相关 15 个模块，改写为 `references/business-pitfalls.md`；移动端/车载/IoT/MQ/混沌工程不搬 |
| `qa-testing` | OWASP Top 10 覆盖目标、缺陷级别 P0–P4、质量指标 | 部分采纳：OWASP 映射进坑点库"安全基线"模块；指标口径与砺衡 `execution-evidence` 冲突，不采纳 |
| `ai-product-testing-expert` | 幻觉/注入/多轮一致性/固定参数可复现 | 部分采纳：压缩为坑点库"AI 生成/对话"模块；不引入其评测集流程 |
| `test-automation`（defect-analyst） | 失败归组、竞争假设、置信度、共同根因、flaky | **不采纳**：砺衡 `references/execution-evidence.md` 的"缺陷与复测"已覆盖（竞争假设、置信度、相似现象不直接合并根因、flaky 隔离），重复搬会两套口径 |
| `software-acceptance-expert` | defect-triage / test-strategy / acceptance-review | 不采纳：技能均约 1.5KB，只有原则无判据，低于现有 execution-evidence |
| `api-testing-expert` | 通用 API 测试策略 | 不采纳：包内 `skills/api-test`（wjx 5.0.2 系）资产更完整 |
| `functional-test-expert` | 需求→用例数据包 | 不采纳：与 `case-generator` 主链重复 |
| `ai-testing-quality-assurance-expert` | crossdoc 证据报告 / skill-preflight | 不采纳：前者是文档交叉校验、后者是技能自检，均非检出能力 |
| `webapp-testing` | 起服务/浏览器测试流程 | 不采纳：`references/browser-connection.md` 已覆盖且更严 |
| `software-test-engineer` / `flycatsoft-ce-shi-gong-cheng-shi` | 用例方法速查、行业坑点 | 不采纳：内容为通用口诀，已含于 `design_methods.md` / `business-pitfalls.md` |

新增 `references/business-pitfalls.md`：15 个 Web 模块 × 高/中风险 × "先看什么"，附安全"仅防护验证"与不虚构阈值纪律；与按症状分类的 `methodology/defect_patterns.md` 互补。

接入：`agents`（进探索前按模块过一遍）、`references/bug-detection-map.md`（业务坑点入口）、`methodology/defect_patterns.md`（指向模块清单）、`README.md` 文件清单；新增路由断言（三处都必须指向该文档）。

回归：包根 238、case-generator 111、api-test 17 全绿。

## 方法论文档补充盘点（v1.29.0，2026-11-18）

**修正上一轮盘点口径**：v1.28.0 的扫描只到 3 层目录，漏掉了专家包 `skills/*/references/` 子目录（这批正是方法论文档所在）。本轮全深度重扫 10 个测试类专家共 54 份文档，输出 `本机测试类方法论文档清单_v1.csv`（UTF-8-sig，含决策与理由）。

新采纳两份（均为之前漏扫的 references）：

| 文档 | 来源 | 落地 |
|---|---|---|
| 证据等级 E1/E2/E3 | `test-automation/skills/test-automation-ops/references/evidence-grading.md` | 改编为 `methodology/evidence_grading.md`：三级定义、与 PASS/FAIL/BLOCKED 的映射、计数口径、表述对照、交付前五问；接入 `execution-evidence.md`、`agents`、盲测计分（`evidence_grade != E1` 不计检出） |
| AI 功能测试 | `ai-product-testing-expert/skills/ai-product-quality-gate/references/01–04` | 压缩为 `methodology/ai_feature_testing.md`：四维度判据、八场景、可复现参数、上线门禁、缺陷定级；`business-pitfalls` 的 AI 模块改为指向该专章 |

复核后仍不采纳（本轮补理由）：`qa-testing` 的 ground_truth（已有单源+双向校验+回链守卫）、google_testing_standards（AAA/金字塔与现有口径重叠）、prompt 库/入职指南（非判据）；`software-acceptance-expert` 五个 references 各 <1.4KB，只有原则；`functional-test-expert` 的 Mermaid 流程模板列为候选（与场景/PATH 模型部分重叠）。

回归：包根 239（新增"E2 不计检出"1 例）、case-generator 111、api-test 17 全绿。

## 扫描范围纠正与两份方法学补充（v1.30.0，2026-11-18）

前两轮盘点的扫描范围本身有缺口，先纠正再补：

| 轮次 | 扫了什么 | 漏了什么 |
|---|---|---|
| v1.28.0 | `experts/plugins` 下 10 个测试类专家的 `agents/`、`skills/*/SKILL.md`（3 层） | 专家包 `skills/*/references/` 子目录 |
| v1.29.0 | 上述专家全深度 `*.md`（54 份） | **`codebuddy-plugins-official/external_plugins/`（149 个插件）**、**用户级 `~/.workbuddy/skills`**、**`D:/TestCase/MySharedSkills`**、`cb_teams_marketplace` |
| v1.30.0 | 四个范围全部补扫（含 `*.json/*.csv/*.txt`） | — |

补扫命中两份真缺口，均对应此前明确宣布"未做"的能力：

| 新增 | 来源 | 补的缺口 |
|---|---|---|
| `references/a11y-audit.md` | `external_plugins/accessibility-compliance`（wcag-audit-patterns 38 条、screen-reader-testing 的 VoiceOver/NVDA 命令与检查点、accessibility-audit 的键盘/对比度/人工清单） | figma 7.41 只有 15 条纯计算规则，且 `a11y-rules.md` 自己声明未覆盖键盘全流程、读屏、200% 重排、动效、live region——这些正是本文件内容 |
| `references/security-verification.md` | `D:/Users/personal skills/security-best-practices`（10 个框架安全规范，含 REACT-XSS-001 等规则号） | `business-pitfalls` §11 只有 6 行"先看什么"，没有验法；本文件给 12 项外部可验防护 + OWASP 映射 + "不算缺陷"情形 |

接入：`agents`（模块排查行明确两条深查入口）、`bug-detection-map`（无障碍行 + §四外部深查说明）、`visual-usability`（基础无障碍与红线）、`business-pitfalls`（§11/§12）、`figma-ui-parity/references/a11y-rules.md`（未覆盖段指向人工审计，跨层白名单登记）、`README` 清单；新增 `tests/test_a11y_security_docs.py`（6 例）锁条数、锁"标内置必须真在规则集里"、锁安全清单每项都有验法与误报说明。

仍不采纳（本轮新增理由）：`performance-testing-review`（能力清单，无阈值无判据）、`unit-testing`/`test-driven-development`（代码级）、`api-testing-observability/api-mock`（mock 生成工具）、`deployment-validation`/`incident-response`（部署与事后运维）、`frontend-design-pro/anti-patterns`（审美偏好，纳入即制造误报）、`design-to-code/accessibility-review`（4.2KB 速查，已被新清单覆盖）。
委派而非复制：`web-agent-stream-capture`（流式结束判定、跨域 iframe、审批卡）——`browser-connection.md` 已明确指向该技能，保持单一口径。
候选未接：`lanhu-ui-parity`（蓝湖设计稿来源，`design-data.md` 当前只认 Figma）、`wjx-knowledge-base`（问卷星领域检索，可作接口 oracle 依据）、`dogfood/issue-taxonomy`（严重度与分类重叠，待逐条比对）。

回归：包根 239 → **246**、case-generator 111、api-test 17。

## 问卷星知识库按方案 B 接入（v1.30.1，2026-11-18）

背景：`qa-req2testcase-generator/user_knowledge`（L5 KB，含 showdoc_api_docs 91+ / distilled 规则 M01–M23 / business_flows / api_relations / tapd_launched 2521 / wjx_help 664）此前只在数据层被 `kb_load_contract.py` 支持，但包内同级回退不存在，路径从未登记——接口任务每次都要靠人记 `--kb`。

本批（方案 B，登记进文档契约层，不动脚本）：

- `doc_review.md` 新增「本机知识库根路径」节：登记 `D:/TestCase/MySharedSkills/qa-req2testcase-generator/user_knowledge`（单一出处，脚本自动扫 `project/` 子目录），写明 `kb_load_contract.py` 与 `flow.py --action` 共用解析（`--kb` > `KB_PATH` > 同级回退）。
- 使用纪律五条：①问卷星接口任务优先从 KB 取契约原文作 oracle，引用写出处；②每次先做存在性检查，不可用时降级 B/C/D 模式，**不得凭记忆补契约**；③检索为空如实写"知识库无记录"并留缺口；④题型/帮助/历史需求等非契约领域事实直接调 `wjx-knowledge-base` skill，不复制内容；⑤语义索引手动重建，过期退回关键词通道并注明。
- `asset_inventory.md` 的 `kb_load_contract.py` 条目指向登记节（只引用不复制路径）。
- 新增 `tests/test_kb_registration.py` 5 例：锁单一出处（路径重复即漂移）、锁登记节与 `project/` 说明、锁纪律关键词齐全、锁领域事实委派、锁资产清单引用。
- 实跑验证：`kb_load_contract.py --kb <登记路径> --action 1000107/1000108` 各命中 5 份文档（ShowDoc 契约原文 + distilled 规则 + ai_homepage/survey_lifecycle 业务流 + 接口依赖关系）。

版本 1.30.0 → 1.30.1（patch：文档层登记 + 测试，无脚本行为变更）。api-test 17 → 22 全绿。

## 衔接点与纪律落地核对（v1.30.1 → 1.30.2，2026-11-18）

对照"知识库衔接点分析"逐项审计实际落地，补齐三处只停在分析层面的缺口：

| 项 | 审计发现 | 处置 |
|---|---|---|
| 纪律"原文为准，蒸馏只作索引" | 未写入登记节（R91 把响应字段当必填的教训只在会话里） | doc_review 登记节补第 6 条：判定必回 showdoc 原文；摘要偏差作为 KB 质量问题单独记录 |
| requirement-review 衔接 | 已有（SKILL.md 本地文档优先 + prd-dimensions #8 历史一致），但变量名 `WJX_KB_PATH` 与 api-test 的 `KB_PATH` 脱节，且无检索 skill 委派 | 统一为 `KB_PATH` 并声明旧名废弃；补 wjx-knowledge-base 委派与"知识库无记录"纪律 |
| case-generator 衔接 | 无任何契约约束来源约束 | SKILL.md 判据源表后新增"接口类 TC 约束来源"段：约束必须取自契约原文（kb_load_contract），蒸馏不作判定依据，禁止凭记忆编造，KB 不可用改记缺口 |

锁定：`test_kb_registration.py` 新增 StageWiring 4 例（蒸馏纪律固化、变量名统一且旧名废弃、生成侧约束绑定契约原文、历史一致维度出处纪律仍在）。api-test 22 → 26；全量 383 passed / 201 subtests。版本 1.30.2。

## 接入方案审核修复（v1.30.2 → 1.30.3，2026-11-18）

对《问卷星知识库接入方案》审核出的问题按序修复（备份：D:/TestCase/backup/web-acceptance-assurance.v1.30.2.zip）：

**P0 已交付代码缺陷**

| 项 | 问题（实测证据） | 修复 |
|---|---|---|
| P0-1 | `flow.resolve_contract_from_kb` 不读 `KB_PATH`——仅设环境变量时返回 None，而其 `--kb` 帮助文本与 `default_kb_candidates` docstring（"实际仅 KB_PATH 生效"）都声明了该回退。`doc_review.md:28`/`asset_inventory.md:19` 的"共用解析"陈述失真 | `flow.py` 补回退（`--kb` > `KB_PATH` > 同级候选），恢复设计意图；`business_flow.py` 同样补回退（两者都提供仍二选一报错） |
| P0-2 | `business_flow.py` 强制显式 `--kb`，登记路径不生效 | 同上；`business_flow.md` 示例外补 KB_PATH 说明（不进代码块，避免弄坏示例） |

文档修正三处：`doc_review.md`（共用解析陈述补 business_flow.py）、`asset_inventory.md`（同）、`business_flow.md`。

**Tier 1（按审核修正后落地）**

- 新增 `scripts/kb_freshness.py`：**主判据 = 源-索引漂移**（源文件最新 mtime > 索引 `source_mtime` → stale_drift）；墙钟年龄只作提示；`crawled_at` 作"数据截至"展示不判 stale；KB 不可用 exit 2 + 降级提示（对工作流 fail open，不阻断契约定位）。
- `api-test/SKILL.md` 判路表后固化开场自检顺序：先 `kb_freshness.py`（建议性）→ 再 `kb_load_contract.py`（失败才降级 B/C/D）。
- `doc_review.md` 纪律第 5 条对齐 fail-open 口径。

**Tier 2 决策**：C（历史一致扫描器）删除——检索 skill 已具备该能力（实测 `--source tapd_launched` 命中 AI 主页两条真实需求），按纪律委派；D（约束导出）本轮不做——`contract_parser.py` 为纯库模块且 `case_generator.py` 已实现字段级变异，做则须先定消费者。

**测试**：`test_kb_registration.py` 增行为级 7 例（KB_PATH 回退/fail closed/显式优先/CLI 三态）；新增 `test_kb_freshness.py` 6 例（漂移判据/墙钟禁用/数据截至/缺失索引/不可用降级）；api-test 26 → **39**。

**端到端**：真实 KB 上 `kb_freshness` 输出 build_at=2026-10-22/漂移通过；`business_flow.py --query` 经 KB_PATH 命中 3 条业务流；`flow.py --plan-only --action 1000107` 成功经 KB 定位契约并进入语义事实准备层（R1 门禁与语义层均为既有设计行为；测试写入 KB 的 staging 文件已清理）。

## 修改内容审核修复（v1.30.3 → 1.30.4，2026-11-18）

对本会话 v1.25.0→1.30.3 全部修改做代码层审核（非仅 git/测试层），修三处：

| 级别 | 问题（实测证据） | 修复 |
|---|---|---|
| P0 | `gate_checks.py` 对 `design_fingerprint` 用 `except Exception: = None` 静默降级：移走该模块后门禁仍输出 `warning`、`failed=[]`、binding 消失——正是 G09 要消灭的"内容绑定不可达"换了位置 | 改为与 `case_data` 同口径：导入失败即 `sys.exit(2)` + stderr 说明；binding 产出处去掉判空。实测缺模块 → rc=2、stdout 空 |
| P0 | `blind_eval.score_run` 只约束 blocker/major，漏 normal 级仍判 `pass` 且**无任何披露**——"全检出"与"只漏轻微"报告完全一样 | 遵守协议不改 verdict（协议第 53 行明定 blocker/major），新增 `warnings`：normal 漏检 + 未被答案键匹配的发现均须披露，写入 score.json |
| P2 | `flow.resolve_contract_from_kb` 里 `except Exception: hits=[]` 把"读取失败（权限/编码）"与"知识库无记录"混为一谈 | 改为 stderr 告警后跳过该 root，保留其余候选继续解析 |

同时验证成立（未改）：G02 义务契约校验（畸形义务 5 条 error）、G03 无义务未登记 → COV-LAYERS failed / 登记 legacy → warning、G06 登记缺口 → 压 L2 且 missing_layers 命中、G09 binding 64 位哈希、eval_cases 指标方向（LOWER/HIGHER 分组正确）、pairwise 生成验真闭环（12/12 verified）、kb_freshness 漂移判据与 fail-open、从任意 cwd 运行不依赖 cwd。

回归：新增 `test_v126_hardening.py` 3 例（fail-closed 依赖、normal 披露、全检出无 warnings）；**四套完整口径 770 passed / 305 subtests**（此前"全量 400"漏了 figma 367 例，本轮纳入）。

## 实测经验整合（v1.31.0，2026-11-18）

来源：问卷星 Agent 助手（星仔）全量验收实测（154 项台账，含路径穿越串 405 静默丢失 P0 四轮复现、抽样冒充全量两次纠正、专有类型载体纠正、带外生效核对）。把踩坑经验沉淀进四个落点，均为判据级新增、不动既有判定链：

| 落点 | 新增内容 |
|---|---|
| `skills/case-generator/references/methodology/ai_feature_testing.md` | 新增 §8「对话式 Agent 被测对象的实测要点」：静默消息丢失两层判定（网络钩子+DOM 双核实、列每轮回归）、流式完成判定两坑（思考阶段长度不变、用户气泡不渲染）、**写入结论三层独立验证**（网络层→新会话冷读→产物页直读；agent 自写自读属同源互证不得判 PASS）、审批/澄清卡片驱动纪律（上限、限流死锁、超时≠零副作用先核副作用）、专属能力在专属载体上测；原 §8 顺延为 §9 |
| `references/execution-evidence.md` | 「页面与接口/副作用」补同源互证边界（同通道读写不构成独立验证）；「缺陷与复测」补长期未闭环阻断缺陷设为每轮回归首项 |
| `references/methodology.md` | 「逐类适用性裁定」补"不适用"前置核实载体（错误载体被拒≠不适用）；「覆盖复核与交付」补覆盖口径开工前锁定（用户明示全量不得自行降级为代表性覆盖）、结论粒度与明细逐字对齐、交付前独立复核覆盖声明、覆盖矩阵作硬门禁 |
| `references/business-pitfalls.md` | AI 模块表新增 3 行排查入口：特殊字符串静默丢失（高）、写入同源互证（高）、专属能力误判不适用（中） |

**与冻结源的关系**：`ai_feature_testing.md` §8 为本包实测新增，冻结源（ai-product-testing-expert 01–04）无对应内容，属有意增量漂移，后续对照时以本表为准。本次仅改 markdown 判据与文档，无脚本/schema 改动，四套测试口径不受影响。

备份：`web-acceptance-assurance-backup-20261118/`（同级目录，包内 4 个 SKILL.md 已改 `.disabled` 防误触发）。

## 证伪立场整合（v1.32.0，2026-11-18）

用户明确要求：专家以挑刺角度测试、尽可能发现问题、不顺从产品设计。落为判定标准（非口号），三个落点：

| 落点 | 新增内容 |
|---|---|
| `agents/web-acceptance-assurance.md` | 服务目标新增「证伪立场」段：产品自述（含被测 Agent 的"设计如此/仅XX支持"解释）一律按待验证声明处理，有来源才结案、无来源记缺口、可疑说法用替代通道核实；挑刺不等于乱报，判定链与误报四问兜底 |
| `references/methodology.md` | 需求预审后新增「证伪立场」段：三分账（规格来源边界/无来源能力缺口/疑似缺陷）+ 替代通道核实；与执行前必问矩阵的发现档衔接——挑刺改变判定与结案标准，不是无预算扩量 |
| `skills/case-generator/references/methodology/ai_feature_testing.md` | §8 新增「不顺从产品自述」：Agent 边界声称三分账，声称"仅网页可做"须实际打开网页验证、声称"接口无字段"可对照网页端实际请求载荷核实；不符时按疑似缺陷立案 |

动机实例：星仔验收中 agent 多次声称"仅网页可做/接口不支持"，验收方均按 PASS(不适用) 结案且未去网页核实——属顺从产品自述。本次判据落地后，此类声称须核实后才能定性。备份：`backup/pre-1.32/`（三文件改动前快照）。

## 受控对抗测试改造（v1.33.0，2026-11-18）

按用户参考方案与独立审核修订后的清单（IMP-01~16）实施，将风险探索升级为受控对抗发现。原则：契约验证为底线、独立挑战为主线、证据链与误报治理为约束；不新建第二套评测，扩展既有 blind_eval。

| 层 | 落点 |
|---|---|
| 判据统一（IMP-01） | false_positive_guard：历史不一致/稳定复现不再一票否决，复现率 k/n 只表稳定性；detection_methods/specification-contract 增判据来源与观测入口要求 |
| 证据强制力对齐（IMP-02） | execution_ledger result.json 增可选 evidence_grade；PASS+E2/E3 拒收，与 blind_eval 既有行为一致；旧格式兼容（tests/test_evidence_grade_gate.py） |
| 比例三层澄清（IMP-03） | SKILL.md/policy.json/gate_checks 注释按 guidance/WARN/BLOCK 三层与两种失效方向重写，必选覆盖义务不得因配比删减 |
| 安全许可分级（IMP-04） | security-verification 按动作副作用标注许可分级，注入/越权仅限授权+隔离环境（既有 12 条硬约束测试全绿） |
| 最小对抗闭环（IMP-05~10） | 新增 references/adversarial-testing.md（双循环+能力探测+预算）、oracle-contract.md、finding-triage.md、output-templates 对抗门禁呈现；references/schemas/{adversarial-plan,adversarial-events,finding-reviews}.schema.json；scripts/{adversarial_data,adversarial_gate}.py；review_bridge 扩展评审→生成对抗交接；agents/methodology 增路由（≤40 行，路由基线不动） |
| 断言挑战（IMP-11/12） | references/assertion-challenges.md + challenge-results.schema.json；源码变异只在隔离副本、仅目标断言捕获计 killed、U 单列；黑盒注入/自然故障分账（injected 必填）；执行器与拦截通道为接口约定，能力待探测未实测 |
| 辅助治理与指标（IMP-13/14） | scripts/finding_summary.py（归簇/k/n/最小化/预算+UTF-8-sig CSV）、scripts/challenge_summary.py（源码与黑盒两账、零分母 NA、unresolved 分列）及 13 个单测 |
| 盲测扩展（IMP-15） | blind_eval findings 增可选 reproduction k/n、cost、unresolved_candidates（向后兼容）；新夹具 06-injection-vs-natural（注入 vs 自然 500 判别格）；detection-protocol 增对抗增量约定；prepare→verify→score 真链自验通过 |
| 注册发布（IMP-16） | 版本 1.32.0→1.33.0 小步递增；缓存旧版 SKILL.md 全部禁用；备份：D:/wjx_agent_test/outputs/adversarial-upgrade-20261118/backup/web-acceptance-assurance-1.32.0 |

效果声明边界：本轮为结构与规则改造 + 301 项单测全绿；对抗检出能力提升未经盲测 A/B 证实，不得宣称。IMP-11/12 运行时执行器与浏览器拦截适配器未实现，标注能力待探测。

## 批判者缺口整改（v1.34.0，2026-11-19）

按《专家批判者目标审核意见》（outputs/adversarial-upgrade-20261118/）F 节 1~4 项执行，解决 A1/A2/B1 缺口与反例 G1~G4：

| 缺口 | 整改 |
|---|---|
| B1 对抗可选（G4） | adversarial_gate 增 enforce/waiver：`--enforce` 下空 plan 无豁免理由上限压"待核实"，`--waiver` 附理由豁免留痕；agents 路由段升"必选"，methodology/output-templates/adversarial-testing 同步；新增 EnforceModeTests 5 例 |
| A1 不批判需求（G1/G2） | ORC source 枚举增 `req_issue`（schema+adversarial_data+oracle-contract）；requirement-review 新增需求可疑点清单（requirement-issues.json 伴随文件，type=missing/conflict/hidden_assumption/business_sanity）；review_bridge 增 --requirements-input 映射为 req_issue 探针草稿；finding-triage 增 req_issue 候选分账（归需求问题清单，不建产品 BUG-）；新增 RequirementIssueHandoffTests 5 例 |
| A2 探针无单可循（G3） | 新增 references/probe-playbook.md：A 节坑点→探针模板（P1~P15 与 business-pitfalls 15 模块联动），B 节通用方法库（等价类/边界值/判定表/状态迁移/组合/差分/metamorphic/故障注入/并发/回滚补偿，何时用/怎么用/典型失效）；探针构造前先查清单（agents/methodology/adversarial-testing 三处接线） |
| G5 执行器 | 保持待办：源码变异执行器与浏览器拦截适配器仍为接口约定，能力待探测未实测 |
| 盲测 A/B | 保持待办：效果证据须以既有夹具 01~06 跑改造前后对照后才能宣称 |

版本 1.33.0→1.34.0；备份 backup/web-acceptance-assurance-1.33.0；全量 311/311 测试绿。

## 强制力接线（v1.35.0，2026-11-19）

按《复审意见_v1.34.0》D 节 1~4 项执行，解决 A1~A4 缺口（上轮"开关有了没插电"）：

| 缺口 | 整改 |
|---|---|
| A1 对抗可选（H1） | `execution_ledger.initialize` 增 `--adversarial-gate`/`--adversarial-waiver`：未提供且未豁免记 `adversarial_state=missing` 且 `adversarial_ceiling=待核实`；`summarize` 新增机械派生的 `verdict_ceiling`（FAIL→不通过、BLOCKED/NOT_RUN→待核实、再与对抗上限取严者），不再由人工宣称 |
| A2 方法无覆盖面（H2/H4） | plan obligation 增 `method`（方法库 10 个稳定 ID）与 `playbook_ref`（P1~P15）；`adversarial_data` 增 `summarize_coverage`/`coverage_gaps`（高风险 SURF 无探针、探针未注明方法均列缺口）；`adversarial_gate` 把方法缺口并入上限压制；schema 同步 |
| A3 需求可疑点无校验（H3） | `review_check` 增 `--requirement-issues` 与 `check_requirement_issues`：未提供即判失败（不得默认"需求无问题"），空数组合法（已审阅无疑点），四类 type 校验；新增 `tests/test_requirement_issues_declared.py` 7 例 |
| A4 缺纵向场景（H5/场景） | 新增 `references/schemas/adversarial-scenarios.schema.json`（SCN-ADV：cross_module/operation_sequence/state_bypass）；`probe-playbook` 增 C 节场景设计纪律（A/B 管单点，C 管组合序列） |

端到端实测（非仅单测）：空 plan `--enforce`→待核实；台账三路径（missing/waived/verified）符合预期；探针缺 method→待核实，补齐后→通过；需求可疑点未声明→fail、空数组→pass。

版本 1.34.0→1.35.0；备份 backup/web-acceptance-assurance-1.34.0；320/320 测试绿。

## 身份重写与对抗性堵漏（v1.36.0，2026-11-19）

按《审核结论_角色定义与对抗性_v1.35.0》C1~C7 执行。本轮与前两轮的根本差别：**改的是身份，不只是工具**。

| 缺口 | 整改 |
|---|---|
| A1 无对抗身份（C1） | 核心能力新增**第 0 条「对抗发现（首要身份）」**；新增第 1 条「需求批判」、第 9 条「断言有效性验证」；「证伪立场」从服务目标下的补充段落**升为一级标题**，扩展为质疑需求/质疑实现/质疑自身结论三方向；主线改为"这套实现和这份需求会在哪里坏" |
| A2 自主被授权卡死（C2） | 必问矩阵删除「发现档需用户要求才放宽」，改为「成本超预算」；新增明文：**"更用力地找问题不需要许可，可能造成副作用才需要许可"**；明确 safe 档约束副作用等级而非挑刺强度 |
| A5 全是否定式约束 | 职责范围增「与边界配平的正向义务」四条（必做对抗、必声明需求可疑点、不适用须给理由、无缺陷须写尝试过哪些攻击面） |
| B1 waiver 自授即通过（C3） | `_adversarial_precondition` 加机械约束：豁免**永远不能把上限抬到"通过"**（最高"待核实"）；须同时满足 `read_only`（dependency_mode ∈ replay/mock）与 `case_count ≤ WAIVER_MAX_CASES=5`，条件从 context 机械判定而非信理由文本；新增 `tests/test_waiver_backdoor.py` 6 例 |
| B2 覆盖只报不判（C4） | `coverage_gaps` 增多样性检查：高风险 SURF ≥3 时方法种类须 ≥2（否则"方法过于单一"）；已用模板数须 ≥ 高风险 SURF 数一半（否则"模板覆盖过窄"）；门禁把两类缺口压入上限 |
| B3 SCN-ADV 无脚本消费（C5） | `adversarial_data` 增 `validate_scenarios`（sequence≥2 步、cross_module≥2 SURF、ID 前缀）与 `scenario_gaps`（零场景/缺跨模块/高风险面未被覆盖）；CLI `--scenarios`；`adversarial_gate` 增 `scenarios` 参数并压上限 |
| B4 破坏性变更（C7） | `review_check` 增 `--requirement-issues-migration`：迁移期未声明降为 warning，默认仍 fail；新批次不得用迁移口径 |

版本 1.35.0→1.36.0；备份 backup/web-acceptance-assurance-1.35.0；329/329 测试绿。

**仍未实现（如实登记）**：源码变异执行器与浏览器拦截适配器（接口约定，能力待探测）；盲测 A/B 效果实证。

## 第四轮审核整改（v1.37.0，2026-11-19）

按《第四轮审核意见_v1.36.0》逐条处理（A1/A2/A3 + 可选排版项）：

| 审核项 | 整改 |
|---|---|
| A1 gate 层 waiver 语义与台账相反 | `adversarial_gate.py`：enforce+waiver 路径**豁免不上抬上限**——verdict_ceiling 最高仍为"待核实"（与台账"豁免不得自授即通过"对齐），新增结构化 `waiver_granted` 标志；enforce=False 空 plan 保持既有放行行为（向后兼容）；测试按语义同步修正（原 `test_enforce_empty_plan_with_waiver_passes_and_records` 改为 `..._records_but_never_passes`，另增"基础上限更严时豁免不得放松"与"非空 plan waiver_granted=false"两例）；角色定义与 adversarial-testing.md 措辞同步对齐（顺带修复原文缺失的反引号） |
| A2 盲测 A/B 四轮零实证 | 工具与协议就绪：`blind_eval.py` 新增 `ab` 子命令（`compare_ab`：两臂各为目录、聚合多 run 的 score.json，按臂聚合检出/误报/复现/成本，输出 precision_proxy 与每百探针有效发现派生指标，分母不足输出 NA 不编造比例；可比性检查——协议/夹具哈希同臂跨臂一致、执行方跨臂须不同、答案分母不等即留痕 comparable=false）；新增 `docs/evals-baseline/ab-protocol.md`（两臂定义/执行步骤/指标解读纪律/禁止的解读/诚实披露边界）。**真实对照数据仍待首个真实测试批次执行，不伪造**；角色定义能力 9 增 ab-protocol 指针 |
| A3 G5 执行器未实测 | 维持登记，不做改动（文档已如实声明"能力待探测，未实测"） |
| 可选排版项 | 核心能力第 9/10/11 条间距统一（删除多余空行） |

版本 1.37.0（v1.36.0→1.37.0）；备份 backup/web-acceptance-assurance-1.36.0（本轮改动先行后建，备份由拷贝当前树逆向应用全部编辑重建，重建后 328 passed + 1 skipped[环境路径跳过] 验证与 1.36.0 行为一致：备份 gate waiver 仍返回"通过"）；339/339 测试绿。

**仍未实现（如实登记）**：源码变异执行器与浏览器拦截适配器（接口约定，能力待探测）；A/B 真实对照数据（工具与协议已就绪，待真实测试批次执行）。

## 三阶段执行闸门（v1.38.0，2026-11-19）

按用户要求把执行分为三阶段并加入人工放行闸门（规划与用例生成 → 批量执行与状态采集 → 结果裁决与缺陷分类）：

| 落地项 | 内容 |
|---|---|
| 阶段状态机 | 新增 `scripts/phase_gate.py`：`init/submit/approve/reject/start/complete/amend/approve-amendment/ack-routing/status/check-verdict/scan-collection/check-adjudication`。状态 `pending→awaiting_approval→approved→in_progress→done`（+`rejected`）；未放行 `start` 报错 rc=2；前一阶段未 done 不得提交下一阶段 |
| 采集只采集 | `scan-collection`：禁 `verdict`/`judgement`/`conclusion`/`severity`/`defect`/`classification`/`root_cause`/`pass_fail` 等判定字段；`collection_state` 只许 collected/blocked/not_run/skipped/partial；collected 必附证据路径。**已知盲区**：自然语言观察文本里的判定拦不住，已在文档如实标注 |
| 裁决诚实性 | `check-adjudication`：必须有 evidence_refs；basis 须含 observed_*（ui/dom/network/console/screenshot/api_response）；禁 assumed/inferred_from_design/script_inferred/fabricated/script_completed/expected_only；`page_behavior_completed_by_script` 直接拒绝；产品缺陷须 E1 且 page_observed=true；E2/E3 只能记待核实 |
| 台账接线 | `execution_ledger.initialize(..., phase_state=None)` + CLI `--phase-state`：三阶段未全 done / 存在未批准修订 / 存在未确认路由误配 → `gate_precondition.phase_ceiling="待核实"`，由 `_verdict_ceiling` 按最严合并；不传该参数时 baseline 结构不变（存量批次可复跑） |
| 模型路由（能力边界） | 本包不能切换模型，只做「声明实际模型类别 + 与路由表核对」：不符时结论上限压待核实，须 `ack-routing` 记录误配理由并在报告披露。**不宣称已实现自动路由** |
| 交付文档 | 新增 `references/phase-workflow.md`（三阶段定义/放行协议/采集禁令/裁决纪律/结论合并顺序/常见错误），角色定义新增「三阶段执行闸门」章节 + 必问矩阵「阶段放行」行 + 判定纪律「裁决诚实性」段 |

**自攻发现并修复的两条绕过路径**（实现后黑盒验证，修复前均成立）：

1. `approve --by agent` 自授放行 → 智能体身份集合（agent/assistant/bot/system/ai/expert/砺衡/助手/智能体）一律拒绝；
2. 手改 `phase-state.json` 把三阶段置 done → history 哈希链 + 状态摘要校验，`load` 检出报「阶段状态不可信」，拒绝沿用。诚实边界：检不出「按格式重算整条链」的有意伪造（无签名密钥），已写入文档，不宣称防篡改完备。

版本 1.38.0（v1.37.0→1.38.0）；备份 `backup/web-acceptance-assurance-1.37.0`（git worktree 导出 HEAD 并复验 338 passed + 1 skipped）；377 tests + 198 subtests 全绿（新增 `tests/test_phase_gate.py` 38 例）。

## 阶段间跨会话交接（v1.39.0，2026-11-19）

用户澄清：**阶段之间要停会话，由人手动发话并新开会话继续**（不是在会话内确认），目的是在不同阶段切换执行模型。原实现是「同会话 `approve`」，正是要排除的做法。

| 落地项 | 内容 |
|---|---|
| 状态机扩展 | schema 升 `phase-state/2`；新增 `active_session`（状态单写者）、`awaiting_session_for`（交接未消费时指向待续跑阶段）、`sessions`（每会话的模型与开关时间）。摘要哈希覆盖这三项，手改会被检出 |
| 新增 `checkpoint` | 阶段收尾：必须先 `submit` 下一阶段（末阶段除外），生成 `handoff/handoff-<phase>.md` + `.json`，含**确认码**、**下阶段建议模型**、**用户需原样发出的确认文本**、产物清单与恢复命令；状态置 done 并挂 `awaiting_session_for`，本会话至此不得推进 |
| 新增 `resume` | **跨会话唯一入口**。四条硬校验：新会话标识 ≠ 交接会话、从未使用过、`--user-confirm` 含确认码、交接包未被消费。通过后记 `approval.mode=cross_session`（含 from/to session 与确认码），切换 `active_session`，按新会话模型声明路由并核对 |
| 同会话 `approve` 降级 | 保留兼容，但记 `mode=same_session`；`check-verdict` 与 `execution_ledger._phase_precondition` 一律压「待核实」——同会话换不了模型 |
| 单写者与会话边界 | `active_session` 之外的会话调任何推进命令被拒；交接未消费时旧会话 `start` 被拒；交接点允许新会话直接 `reject`（无先 resume） |
| 规划阶段无需再申请 | `init` 即放行 `plan`（`approval.mode=task_start`，用户下达任务＝同意规划）；规划阶段的 routing 由起始会话的 `model_class` 声明 |
| 文档 | `references/phase-workflow.md` 第 2 节重写（标准动作序列含会话切换、硬规则、自攻表、诚实边界）；角色定义三阶段表加「执行会话」列并新增「阶段收尾固定交付」四条 |

**实现期的三处自发现缺陷（均已修）**：①`checkpoint` 若把阶段置成非 done，末阶段永远拿不到结论——改为「阶段完成即 done + 交接未消费标记」；②`resume` 去目标阶段找确认码（确认码在**上一阶段**的交接包里）——改为沿交接链取；③`resume` 误把「目标阶段须 done」当校验（应校验上一阶段）。另撤销一条自拟的「一会话一阶段」校验：`resume` 已要求会话标识未使用过，该条属死代码且会误伤同会话兼容路径。

**诚实边界（写进文档，不得夸大）**：确认码存于状态文件，能读文件的智能体理论上可自行填入——它防误操作与蒙混，不防有意伪造；真正不可替代的控制点是「停会话、新开会话」这两个只有人能做的动作，以及放行记录进报告供人核对。本包仍不能切换模型，只能核对声明的模型类别。

版本 1.39.0（v1.38.0→1.39.0）；备份 `backup/web-acceptance-assurance-1.38.0`（git worktree 导出 HEAD，复验为旧实现：schema/1、无 checkpoint）；缓存 1.39.0 已同步、1.38.0 的 4 个 SKILL.md 已禁用；390 tests + 198 subtests 全绿（`test_phase_gate.py` 51 例）。

## 攻击面门禁与验收口径纠偏（v1.40.0，2026-11-19）

触发：用户追问「为什么刚刚使用专家实际测试只发现几个问题，项目实际问题远远超出发现的数量」。对 2026-11-18 那次 154 项真实执行做了数据取证，根因不是被测系统干净，而是执行范式错位。

**取证结论（`evidence_20261118/ledger_final.json` + `gate_result.json` + `cases.json`）**：154 条里正向路径 143、边界 10、组合 1、权限 0、异常 0；`surf_id` 关联 0 条、`method` 标注 0 条；`evidence` 含「读回一致」（同源自证）27 条，明确用到独立通道（截图/网页端/抓包）的仅 2 条；4 条 FAIL 全部围绕同一个历史 P0，**0 条来自为新攻击面设计的探针**；`cases.json` 38 条用例无 method/SURF/path_type 字段；目录内无 `adversarial-plan`/`EVT-`/`CHL-`，即对抗循环未启用。门禁当时已给出 `verdict_ceiling=待核实`，但报告仍以「97.4% 通过率」为主指标。

| 项 | 整改 |
|---|---|
| 新增 `scripts/attack_coverage.py` | 从台账机械算非正向路径占比（门槛 0.30）、判据独立性（0.30）、风险面关联（0.50）、方法标注；字段缺失报 `path_type_missing` 一类缺口而不是默认通过；`--infer` 的关键词粗分单列 `inferred_*`，**不参与门禁**；`--enforce` 有缺口 rc=2 |
| 台账侧前置 | `execution_ledger.initialize` 增 `--attack-coverage`：结论并入 `gate_precondition.attack_coverage_ceiling`，`_verdict_ceiling` 按最严合并；未传保持旧结构（向后兼容） |
| 阶段闸门硬卡口 | `phase_gate.complete --attack-coverage`：`execute` 阶段收尾必填，缺失记 `attack_state=missing` 且 `check-verdict` 直接拦（压待核实）。这是"154 项铺面直接进裁决"那个洞的机械封堵 |
| `blind_eval` 四项工具缺陷 | ①`_evidence_ok` 兼容 str/list（原先只认数组，写成字符串时全部判「没有证据路径」、命中答案也不计检出）；②`score.json` 改写到 `run_dir` 同级的 `scores/`，修掉"二次计分被自己判成泄漏"的非幂等；③`_finding_text` 纳入 `expected`/`actual`/`observed_via`，`unmatched` 的警告从「可能是臆造缺陷」改为「待人工复核（措辞差异/答案键缺失/无效发现三选一须留痕）」；④`precision_proxy` 分母 <10 输出 NA 并附分母 |
| GAP-01 断言守卫 | 源码变异实测中 `CHL-SM-06` 存活——删掉 `FORBIDDEN_SOURCES` 的 `fabricated` 后 51 例全绿。新增 `tests/test_adjudication_forbidden_sources.py`（六来源参数化 + 合法对照），在隔离副本复验该变异已被捕杀 |
| 能力声明回写 | `references/assertion-challenges.md` 从「全部执行器仅为接口约定，能力待探测」改为实测表：源码变异可用（K/(G−I−E)=5/6）、Playwright 可注入可撤销、Chrome DevTools 只可观不可注入 |
| 身份与报告口径 | 角色定义新增「反模式：为什么测了很多却只发现几个」（四反模式 + 五条机械约束）；核心能力 0 增「测了很多 ≠ 打得深」；批次门禁并列「攻击面门禁」；输出规范要求**首屏给攻击面而非通过率**；`output-templates.md` 与 `phase-workflow.md` 同步 |

**本次 A/B 侧结论（诚实登记）**：`blind_eval` 机制链已实测跑通（prepare/verify/score/ab，隔离护栏与可比性检查均真实生效，单臂 8/8 分支 3 检出 16 张截图），但**两臂真实对照数据仍缺**——卡点是"两臂需独立且盲的执行方"，本会话内评测者已读答案，用不同提示词伪造两臂差异会把"提示词差异"当成"改造效果"，故拒绝。D1 的准确归因更正为：题面模板本就要求数组，是执行方 prompt 写成了字符串而计分器只认数组且报错不可诊断（原记录"题面模板写字符串"属过度归因，已更正）。

**残余缺口（不宣称已堵）**：自建台账、伪造 coverage JSON 仍可绕过门禁——属"有意伪造"，与既有诚实边界同类；机械层防的是"忘了做/懒得做"，防不了"决心造假"。攻击密度门槛 0.30 是"每 3 条至少 1 条非正向"的最低线，不是质量目标。

版本 1.40.0（v1.39.0→1.40.0）；备份 `backup/web-acceptance-assurance-1.39.0`（git archive 导出 HEAD）；419 tests + 207 subtests 全绿（新增 29 例：attack_coverage 10、attack_precondition 6、forbidden_sources 6、blind_eval 5、phase_gate 2）。

## 攻击面维度补全、量化界面标准与交接产物校验（v1.41.0，2026-11-19）

触发：用户要求「以批判者/挑刺者身份自主全面测试」并明确点名「页面文案、样式、布局、操作逻辑不合理」也要能测出来；同时要求颜色对比度按公开规范核查（普通文本 4.5:1、大号文本与界面组件 3:1），并检查字号、间距、可点击区域等可量化问题。

**先更正上一版的错误归因（M1）**：v1.40.0 把「自建台账」记为"有意伪造"。实测证明 `execution_ledger.finish()` 的 payload 里根本没有 path_type/surf_id/method/oracle_source/discovery_mode，全仓只有消费者没有生产者——按官方接口记录的人必然被压「待核实」，手搓 JSONL 反而是唯一可行路径。**这是设计缺陷，不是道德问题**，已在「攻击面字段」一节改为由 finish() 正式产出。

| 项 | 整改 |
|---|---|
| 路径维度（S1，阻断） | `attack_coverage.PATH_TYPES` 由功能五类扩到十类：新增 `ui_content`/`usability`/`accessibility`/`data_consistency`/`regression`，全部计入非正向分子。此前 UI 类发现如实标 `visual` 被判 `path_type_missing`、塞进 `positive` 又被判 `attack_density_low`，两条路都挨罚 = 制度性劝阻登记界面问题 |
| 探针/方法枚举（S1） | `adversarial-plan.schema.json` 与 `adversarial_data.py` 的 `probe_type` 增 visual_walkthrough/copy_audit/heuristic_review/consistency/regression；`method` 增 visual_walkthrough/copy_audit/heuristic_review/consistency_check/regression_impact；`playbook_ref` 扩到 P1~P20 |
| 台账产出（S2，阻断） | `execution_ledger.finish(attack={...})` 新增七个字段并做枚举校验；`attack_coverage` 可直接消费 `events.jsonl`（自动取 FINISH 并补 case_id）。端到端用例：官方接口写出的台账不再被判"字段缺失" |
| 声明≠行为（S3，高） | `surf_id` 须匹配 `SURF-` 且（传 `--plan` 时）在风险面清单内；`oracle_source=independent` 须有 `oracle_channel` 且不得只写"读回一致/助手自述"，并须带证据；`method` 须落枚举；新增 `method_diversity`（≥20 条记录至少 3 种方法）与 `probes` 计数 |
| 通过档下限（S8） | 无缺口还不够：`designed_probe=0` 或未声明未破攻击面（`--residual-note`）都压上限。空记录+漂亮字段不再能拿「通过」 |
| 交接产物（N1，阻断） | `phase_gate.submit` 逐条校验 `--artifact` 为**存在的绝对路径**；plan→execute 必须凑齐 review / cases / adversarial 三件，缺一即拒。此前零产物 + 三个不存在的路径也能推进到执行阶段 |
| 视角覆盖（S4，高） | `executed_views` 改为按 `(page_url, state)` 单元计数，每个必查视角至少 2 个单元（可用根对象 `view_scope` 带理由缩到 1）；`visual`/`usability`/`accessibility` 纳入 `MANDATORY_ATTRIBUTES`。此前 7 条同页同状态的记录就能把三个视角全部点亮 |
| 探针模板（S5，高） | `probe-playbook` 增 P16 文案 / P17 布局 / P18 操作逻辑 / P19 数据一致性 / P20 可量化界面，B 节增五个方法；`business-pitfalls` 增界面样式、文案术语、操作逻辑、数据一致性四个模块 |
| 数据一致性（S7/N3） | `methodology.md` 增「数据一致性三通道」专章（前端展示/后端接口/缓存副本；跨视图/跨会话/跨时间/跨缓存；"谁赢谁输必须有提示"不变量） |
| 风险假设（B1） | `phase_gate check-verdict --assumptions`：须 `reviewed=true`，每条含 assumption/basis/falsify_action/result，`result=open` 压上限 |
| 回归门禁（N2） | `phase_gate check-verdict --regression`：有变更却零候选、候选无 `basis`、未写 `no_impact_reason` 均压上限 |
| 可量化界面标准（新增） | 新增 `scripts/ui_metrics.py`：按 WCAG 2.2 SC 1.4.3（4.5:1 / 大号文本 3:1）、1.4.11（非文本 3:1）、2.5.8（≥24×24 CSS px，44×44 为建议档）、1.4.12（行高 ≥1.5）、1.4.10（320px 无横向滚动）机械核算；渐变/半透明/图片背景返回 `not_evaluable` 而不是按纯色算个漂亮数字；失败无证据降级 `pending_evidence`。30 例测试覆盖算法锚点（#767676 过 / #777777 不过）与各阈值边界 |
| schema 对齐（S6） | `case_data.ATTRIBUTES` 补 `usability`/`content`，与探索清单 9 类对齐；`examples/cases-example.json` 补两项裁定。此前这两维在需求→用例这一跳可被静默豁免 |
| 文档口径（S9/C1C2） | `phase-workflow.md` 增两套状态枚举映射表（`collection_state` ≠ `status`）；角色定义明确"路径维度不等于功能维度"；输出模板首屏增界面量化项与未破攻击面 |

**端到端实测（不是手填数字）**：用 Playwright 在 `outputs/ui-metrics-demo-20261119/` 建 7 个已知状态元素（4 个应失败 + 3 个合规对照），从真实浏览器 `getComputedStyle`/`getBoundingClientRect` 取数后交 `ui_metrics.py --enforce`：`#c1` 2.32:1 fail、`#c2` 3.23:1 pass（同色不同字号，验证大号阈值真生效）、18×18 目标 fail、行高 1.3 fail、`#c6` 4.54:1 pass、32×32 advisory、320px 视口 `scrollWidth=492` fail，rc=2。**合规对照必须不报警**（防假阳性）与**已知故障必须报警**（防假阴性）两侧同时验证。

**自审补的两处加固（本轮实测中发现）**：
- `kind=text_large` 是自报的，12px 标成大号就能拿 3:1 宽松阈值 → 改为按实测 `font_size_px`/`font_weight` 复算资格，不符回落 4.5:1 并在备注写明；未给字号时按声明计但标注"未核实"。
- 字重写成 `"bold"` 会被当 400，19px 粗体因此被判 4.5:1 失败（假失败）→ 新增 `normalize_weight`，`700`/`"700"`/`"bold"`/`"bolder"` 等价。
- **CSV 漏了 `measured` 列**：只给判定不给实测值，复核者无法验证判定是怎么来的 → 补进列并加断言。这条是靠"自己读一遍交付物"发现的，不是靠测试。

**边界（不宣称已解决的）**：`ui_metrics.py` 只判可量化项，不构成 WCAG 合规认证；对比度对渐变/半透明只能报待确认；攻击密度 0.30、视角单元 ≥2、方法多样性 ≥3 均为工程判断，未经真实轮次校准；`--residual-note`、`assumptions`、`regression` 仍属"愿不愿意填"层面的机械约束，防得了忘记、防不了决心造假。

版本 1.41.0（v1.40.0→1.41.0）；480 tests + 217 subtests 全绿（新增 61 例：attack_coverage 12、execution_ledger 9、phase_gate 9、exploration 若干、ui_metrics 30）。

## v1.43.0（2026-11-20）：修 v1.42.0 真跑暴露的门禁断链

来源：2026-11-19 一次完整三阶段真实验收（agent.wjx.cn）自评出 13 条装置问题。本轮修其中 6 条，其余按设计权衡保留并在装置清单中登记理由。原则是**只修「守规矩的人被罚、违规的人被放过」的结构性问题**，不动业务判定口径。

| 编号 | 问题 | 修法 | 兼容性 |
|---|---|---|---|
| DEV-01 | `finish()` 的 `ATTACK_FIELDS` 不含 `hypothesis`，而 `attack_coverage.py` 从台账读它 → 守规矩者必被判「无假设」；实测代价是手工回写 JSONL 导致 20 行 digest 全损、整份台账作废 | 白名单补 `hypothesis`/`hypothesis_outcome` + 成对校验（给一不给一即拒） | 新字段可选，旧台账照常可读 |
| DEV-02 | 无证据时 `counts` 里 `fail` 键直接消失，只看 `counts` 会读成「零失败」 | 状态键恒显（含 0），键集合抽成 `RESULT_STATUSES` 常量并与代码实际写入值逐一对齐 | 仅增键，不删不改既有值 |
| DEV-07 | `complete` 无 `--artifact`，`plan.artifacts` 恒空，交接包输出「（未登记产物路径）」；产物还被记到下一阶段名下串账 | `complete` 增 `--artifact`（可重复），**追加**而非覆盖；路径校验与 `submit` 同强度（只认真实绝对路径） | 不传即保持原行为 |
| DEV-08 | SKILL 把探索清单 gate 写成硬前置，但 `phase_gate.py` 里 grep 不到 `exploration`——规则只在文档里。实测：未生成清单，三步收尾全过零告警 | `complete`/`check-verdict` 接线 `exploration_state.summarize()['gate']`；缺失压上限且缺口原样透出 | 缺省只压上限不报错，存量状态可加载 |
| DEV-09 | 装置分不清「产品方需求」与「测试者自造需求」：`source` 写了「无独立产品需求文档」，装置读到却让 `blocking=[]`、`go_ahead` 全放行、`review_check` 仍 pass | `requirement` 增 `source_type`；派生型强制 `blocking` 非空，全量放行告警 | 字段**可选**，缺省仅 warning；存量评审 0 error |
| DEV-12 | `amend` 子命令一调即崩（`args.session` 未定义），而协议强制「必须走 amend」 | argparse 补 `--session` | 纯新增参数 |
| DEV-13 | execute 在 S2 冻结快照，S3 补齐后重跑得「通过」却无法回写 → `check-verdict` 永远读过期快照 | 新增 `revise` 子命令：只对 **execute** 补正、不改状态与 `completed_at`、写 `history` 留痕；**默认拒绝把未通过结论升级为「通过」**（须 `--allow-relax` 并留痕） | 新命令，不改变既有路径 |

配套：`ui_measure.js` 拆出宿主无关的 `collectInDocument()` 与 `measureInDocument()`（DEV-03），`measure(page)` 签名不变——采集逻辑逐行零漂移（规范化比对 5537 字符全等）；`visual-usability.md` 明写禁用程序化 `focus()` 测焦点（DEV-04）；`phase-workflow.md` 与主提示词补「授权模式决定的天花板」三档表（DEV-11）。

**保留未修（设计权衡，非缺陷）**：DEV-05（`unknown` 背景占 56%，保守起见不向上合成，只在报告披露覆盖率）、DEV-06（`finish()` 拒绝 `NOT_RUN`，防「声称跑过」，建议单列未执行原因而非放宽）、DEV-10（结论上限未随 CSV 传递，属产物层约定）。

### 复审轮（v1.43.0 代码审核后追加的修正）

首轮实施完成后对同一批改动做了逐行复审，发现 5 条自身引入或同族遗留的缺陷，已修：

| 编号 | 问题 | 修法 |
|---|---|---|
| R-01 | `revise` 的 docstring 声称「只允许补正为更严或更准」，但代码无任何拦截——实测可把 `attack_coverage` 从「不通过」直接洗成「通过」，等于给门禁开后门 | 默认拒绝「升级为通过」，须 `--allow-relax` 显式放行；`history.detail.transitions` 留痕。**首版修过头**：按"好坏排序"一刀切，把合法的「不通过↔待核实」也拦了，复审时改正为只拦升级为通过 |
| R-02 | `revise` 不校验阶段，可给 `plan`/`adjudicate` 写 `attack_coverage`；而 `check_verdict` 只读 `execute` → 写入的是门禁看不见的死数据，却会让人以为"已补正" | 限定仅 `execute`，其他阶段直接报错（实测确认过 plan 写入后无任何门禁消费） |
| R-03 | `_exploration_check` 只接受 `通过/待核实` 两种取值，与 `_attack_check` 的三种不一致；传「不通过」时报「缺 verdict_ceiling」——字段明明在，是取值不在白名单，报错误导 | 抽出 `CEILINGS` 常量，两个检查器共用 |
| R-04 | `ui_metrics` 的恒显键列表漏了 `false_positive`：有误报时该键在、无误报时消失，正是本项要修的「键缺失代替 0」，只是换了个状态。误报率是首屏指标，分子项尤其不能隐身 | 抽 `RESULT_STATUSES` 常量，与代码实际写入的 6 个状态逐一对齐 |
| R-05 | 上游数据自相矛盾时（`ceiling=通过` 却带着非空 `gaps`）被直接放过——「通过」只是个词，缺口才是机械可核对的 | `_attack_check` / `_exploration_check` 均按缺口从严降为「待核实」。`_attack_check` 这条是原有缺陷，同族一并修 |

另修：`complete` 在非 execute 阶段收到 `--attack-coverage`/`--exploration` 时**静默丢弃**（调用方以为交了、实际没进任何门禁），改为报错；`--artifact` 的 argparse `default=[]` 改为 `None`（append 的共享 list 陷阱）；CLI 帮助里的 `relaxed_from` 更正为实际字段名 `transitions`。

**验证**：`tests/` 569 passed / 0 failed；真产物串链冒烟两轮（`exploration_state.summarize → phase_gate._exploration_check` 达「通过」且 0 缺口）；存量 `phase-state.json` 可加载、篡改仍被哈希链检出。复审新增 6 项测试覆盖 R-01~R-05（升级为通过须放行、收紧/平移不拦、非 execute 拒绝、占位产物拒绝、`false_positive` 键恒显、状态集与写入集一致）。

## v1.42.0（2026-11-19）：把考核对象从「记录形状」换成「失效构造」

**触发**：v1.41.0 拿真实数据实测审核，8 项自述声明只有 3 项成立；真实登录页 16 条界面 fail 里 11 条（68.75%）是工具算错的；817 行真实台账按新口径 0 行可用。根因是上轮 14 条整改**全是给流程加锁、0 条增加检测能力**——门禁只能减少虚报，不能增加发现。

### A 组：修测量装置（不修则后面全不可信）

| 项 | 变更 | 实测依据 |
|---|---|---|
| A1 背景来源 | 记录新增 `background_source ∈ solid/image/gradient/unknown`；非 solid 一律 `not_evaluable` 并转人工视觉通道（报告单列 `manual_review_required`）；取数层找不到不透明祖先时返回 `unknown`，**禁止兜底成白色** | 整页是照片时落白，白字算出 1.0:1，5 条误报 |
| A2 目标尺寸例外 | 实现 2.5.8 四个例外（Inline/Spacing/UA/Essential）；欠尺寸但未做例外判定 → `pending_evidence`，命中例外 → `advisory`，例外不成立才 `fail` | 6 个欠尺寸目标全部 `spacingHits=0`，原判定 6/6 误报 |
| A3 文本间距 | 改为 `text_spacing_override` 前后差分（`before_clipped`/`after_clipped`），只认**新增**裁剪；旧 `text_spacing` 返回 `not_evaluable` 并提示改口径 | 1.4.12 考的是用户覆盖后不丢内容，不是页面默认行高；真页覆盖后 0 处新增裁剪，旧判据 4 条全误报 |
| A4 重排 | 新增 `two_d_layout_exempt`（数据表/地图等二维布局）→ advisory | 规范本身有该豁免 |
| A5 误报回流 | 每条失败须 `human_ruling ∈ confirmed/exception/false_positive/pending`；输出 `false_positive_rate`；`--enforce` 仅在**有经确认缺陷且误报率 ≤15%** 时 rc=2 | 没有回流机制，68.75% 噪声无法收敛 |
| 自审加固 | `kind=text_large` 不得自报豁免；字重 `700`/`"700"`/`"bold"` 归一；CSV 补 `ruling` 列 | 前两条实测得出，第三条靠通读交付物发现 |

### B 组：拆掉反向激励（发现未知问题不再算违规）

| 项 | 旧 | 新 |
|---|---|---|
| B1 计划外风险面 | `surf_unknown` 压上限 | `surfaces_beyond_plan` 计为产出，只披露 + `--amend-note` 要求纳入后续计划 |
| B2 "没有设计攻击" | 全 incidental → `no_designed_probe` | 改判 `hypothesis`（如果 X 则 Y 会坏）；高风险面一条预测都没有才压上限；`broken` 是产出 |
| B3 方法多样性 | ≥3 种否则压上限 | 降为 `single_method_surface` 提示 |
| B4 诚实记录 | `NOT_RUN` 计入 `ready_pending` 压上限 | `acknowledged_unexplored{next_action, owner}` 齐备即不计入；缺字段仍计 |
| B5 采集禁判定 | 禁用词漏 `status`（`verdict` 拦、`status` 不拦） | 补全 `status/result/outcome/assessment/score` 等；开 `lead{suspicion, basis, where}` 结构化出口 |
| B6 视角覆盖 | 每视角 ≥2 个页面×状态单元 | ≥2 个**不同 `falsify_action`** 也算覆盖（二者满足其一），防"人为多切一个状态凑满" |

同时删除 `attack_density_low`、`oracle_self_attested`（比例类）门禁，换为**绝对值**门禁：`no_attack_paths`（一条非正向都没有）、`oracle_no_independent_evidence`（一条独立判据都没有）。理由：比例能凑，绝对零凑不出来。

### C 组：加装发现能力（本轮真正的增量）

| 脚本 | 发现什么 |
|---|---|
| `scripts/ui_measure.js` | 页面内取数引擎：逐元素判定 `background_source`、24px 圆相交检测、间距覆盖前后差分、可交互控件清点 |
| `scripts/state_matrix.py` | 角色×状态×数据条件格点展开，摊出未覆盖格点与"一次都没被行使的取值" |
| `scripts/control_traversal.py` | 逐个控件的观察覆盖；"点了没反应"记为线索（须人工复核），死控件/无反馈在此暴露 |
| `scripts/copy_consistency.py` | 未替换占位符、运行时原始值 `undefined/null`、重复标点/赘字、术语偏离、同角色近义异写 |
| `scripts/assertion_mutation.py` | 断言杀伤率：**抽样由哈希决定不由人挑**；注入已知故障后仍通过 = 假绿 |

### D 组：结论口径

首屏改给攻击指标（误报率/检出数/杀伤率/前沿透明度/计划外发现数/被打破假设数），通过率降为附注；「待核实」分三级（缺能力/缺证据/主动未探明）；工具判定转缺陷须 `instrumented + confirmed`。

### 验证

- 544 tests + 222 subtests 全绿（v1.41.0 为 480 + 217；新增 64 例：ui_metrics 47 例重写、attack_coverage +7、exploration +4、phase_gate +4、v142_capabilities 29）
- 真实页面端到端复测：16 条 fail → 11 条误报被消除（背景 5 / 例外 6），4 条间距误报消失，保留 4 条对比度候选 + 2 条待人工裁定
- 三项探针双侧验证：真页 0 误报 + 注入已知故障必捕获（间距覆盖 `before=2/after=2` 证明必须做差分；2.5.8 例外 6/6；背景 `NONE` 与 `SOLID:#fff` 可区分）

**边界（不宣称已解决的）**：`ui_metrics` 仍只判六类可量化项，不构成 WCAG 合规认证；图片/渐变/动效上的文字只能人工判；`state_matrix` 不排业务优先级；`copy_consistency` 判不了语义歧义；`assertion_mutation` 只证明抽样断言会响，不证明断言覆盖了该覆盖的东西。**A/B 两臂对照未跑，因此本轮不宣称"检出能力提升"**——只宣称"测量装置误报收敛、反激励拆除、新增 5 项可执行检测能力"。

## v1.45.1（2026-11-21）：v1.45.0 影响面审查缺陷修复

对 v1.45.0（`fc3ad38`）做影响面审查后修复 8 项已证实问题。审查为包内自审，不替代真实需求实测；
下列每条都附 E1 复现路径，修复后逐条复测。

| ID | 严重度 | 问题 | 修复 |
|---|---|---|---|
| F1 | major | 缺口判定双向误判：旧基线（v1.44 建账、**确实提供过** `--batches`/`--gate-report`）被报"本轮未提供"（假事实）；反向提供 `--phase-state` 又把从未提供的项一并抹平 | `_gate_inputs_present` 对旧基线按 `gate_precondition` 已成立判定回退推导；`_gate_input_gaps` 把"未提供"拆成 `missing`（压上限）与 `delegated`（由三阶段闸门承担，不压上限但须披露）；取消 phase_state 整体短路 |
| F2 | major | 修订分类可被字符串真值绕过：`no_state_change:"false"` 被判"凭据齐备"→ 只读放行 | 凭据校验判类型与取值：`no_state_change` 须 `is True`、`allowed_actions` 须非空字符串数组、`scope`/`environment`/`role` 须非空白字符串；`is_blocking` 对 `readonly_lead` 记录复验 declaration 凭据 |
| F3 | normal | `headline.schema` 三处文档承诺"版本不同即回退并标注"，实现静默消费 | `render_report_html.load_headline` 校验 `HEADLINE_SCHEMA`，不符即回退并标注版本不符；新增常量一致性测试防两端漂移 |
| F4 | major | A/B 汇总把"未测量"报成 0：`known_near_miss_hits` 硬编码 0、`known_near_miss_rate` 硬编码 `"NA"`、`insufficient_a/b` 从不产出 | `near_miss_hits`/`insufficient_evidence` 逐 run 真算，不可统计时给 `null` 并写 `not_measured` 原因与 `*_unmeasured_runs`；CSV 同步加 `near_miss_hits` 列（空 = 未测量） |
| F5 | normal | 只读自动放行写入 `approval`，与人工放行混同字段 | 改记 `auto_approval`（`auto:true` + 判定依据），`approval` 留空 |
| F6 | minor | SYNC-NOTES/提交信息写"未做跳间校验时拒绝建账"，实为留痕压上限（rc=0） | 三处措辞按实测改写；同时修正"仅披露"一栏误把 `missing_inputs` 当示例（它是压上限的缺口） |
| F7 | minor | `required_checks.status` 文档声明四态、实现只产三态；`_headline_checks(read_only=)` 死参 | 文档改三态并说明"不设未知"的理由；删死参 |
| F8 | minor | `attack_coverage` 判定口径收紧但产物无版本戳，跨版本重判无法归因 | 产物加 `schema=attack-coverage/1` + `tool_version=2`（判据口径版本）；`phase_gate` 收尾快照与台账前置条件记录该版本，首屏攻击覆盖项显示版本，缺失即写"跨版本重判差异无法归因" |

### 准出记录

- **全量回归**：`python -m pytest tests/ -q` → **750 passed, 230 subtests passed**（v1.45.0 为 737 + 230；新增 13 例）。
- 新增回归：`test_gate_link_contract.py` +3（旧基线回退推导 / 旧基线真实缺口仍报 / 三阶段委派不写成"均已提供"）、
  `test_amendment_class.py` +3（14 组类型绕过 / 记录 kind 被改写仍阻断 / auto_approval 不占 approval）、
  `test_headline_contract.py` +2（schema 不符回退 / 两端常量同值）、
  `test_attack_depth_contract.py` +2（产物版本戳 / 版本语义）、
  `test_ab_matrix_contract.py` +3（已知反例真算 / 无裁定为 null / 缺字段整体不给数）。

### 兼容性说明

- **旧工作区可续测**：`baseline.json` 新增 `delegated_inputs`（顶层），旧基线缺失时按 `gate_precondition` 回退推导；
  `gate_precondition` 结构不变，`schema_version` 仍为 `1.0`。
- **产物字段**：`headline.required_checks[].status` 仍是三取值（此前文档误写四态，实现未变）；
  `attack_coverage` 输出新增 `schema`/`tool_version`，其余键不变；`ab-matrix-summary` 中
  `overall_rate`/`known_near_miss_rate`/`insufficient_evidence` 由 `"NA"` 改为 `null`（语义更明确：未测量）。
- **判定口径变化**：`attack_coverage` 的 `tool_version` 由 1（v1.44.2 及更早）升为 2（v1.45.0 起）——
  同一台账在两版下可能得到不同 `verdict_ceiling`，引用归档结论时须写明版本。

### 本版不宣称的能力

- 未执行真实网站测试（对象是专家包自身）；F4 的修复只保证"未测量不被显示为 0"，**不宣称 A/B 检出能力**。
- 变异验证数字（v1.45.0 记录的"34 处语义变异全部捕获"）无法复核，本版未采信也未否定。
## v1.46.1（2026-11-22）：第四轮审核整改——模板枚举同步、运行产物脱库、答案分发隔离、档位结构化

按第四轮以项目目的为准绳的审核整改。目的锚点不变：专家作为批判者/挑刺者自主全面测试，主动质疑需求、假设与实现，扩大覆盖、减少漏测、多发现 bug；诚实登记不被惩罚。本轮四项均为纠偏既有口径漂移与治理隐患，不新增测试配额。

| 条目 | 变更 | 回归 |
|---|---|---|
| F1 | 挑刺模板枚举与模板库漂移修复：`adversarial_data.PLAYBOOK_TEMPLATES` 由 P1~P20 扩到 P1~P24，schema `pattern` 与描述同步到 P21~P24。此前状态组合/控件遍历/文案一致性/断言杀伤四类高价值探针诚实引用 P21+ 会被校验拒绝，规避方式只剩删掉模板引用，导致探针失去溯源、方法多样性失真——属惩罚诚实登记的制度性缺陷，方向与目的相反 | 3 例：P21~P24 合法通过、P25 拒绝、代码枚举与 probe-playbook.md A 节编号双向一致守卫 |
| P2 | 历史运行产物移出版本库：`git rm --cached` 400 份 evidence JSON 与 3 份 issues/observations CSV、`api_test_report_full.json`；磁盘文件保留，依赖既有 `.gitignore` 规则（`api_test_out/` 目录 + 两份 CSV + `*_report_full.json` 共四条）不再入库。测试与脚本对这些历史产物路径零引用，清理不破坏运行期输出 | 测试零引用核验；四条 ignore 规则 `check-ignore` 均命中 |
| P3 | 盲测答案键分发边界：`detection-protocol.md` 补「随包分发隔离」条款，`evals-baseline/README.md` 同步指引——答案夹具属评测者侧受限资料，插件/技能包/仓库副本对外分发时必须排除答案目录，执行方只拿 `prepare` 产物，对外归档只用 `--sanitized` 去敏件；答案夹具本身不移除 | 文档 |
| 低优先 | 对抗门禁档位判定去 reason 字符串扫描：压档规则改为同时记录结构化档位 flag（待核实/不通过），上限取 flag 最严值；不再靠扫描 reason 文案里的「待核实/不通过」字样反推。文案改写不会再误改上限 | 4 例：源码守卫（禁止回退到 `in reason` 扫描）、未闭环 blocker 压不通过、三高风险面单一方法压待核实、压档 reason 与 flag 一一对应不变量守卫 |

### 准出记录

- **全量回归**：`python -m pytest tests/ -q` → **764 passed, 2 skipped（环境依赖）, 230 subtests passed**（v1.46.0 基线 757 + 本轮新增 7 例）；`compileall scripts` 通过。2 项跳过来自 `test_skill_registration` 的本机路径/缓存目录检查。
- **二轮加固（审核观察项 O1/O3）**：补多样性（方法单一）压档行为用例与「每条带档位字样的压档 reason 必须登记同档位 flag」不变量守卫，防以后新增压档规则漏标 flag 导致上限静默变松；同步修正 P2 行 ignore 规则条数表述（目录 + 两 CSV + report_full JSON 共四条）。
- **口径不变项**：通过 < 待核实 < 不通过 的最严档位顺序不变；豁免不上抬结论上限（最高待核实）；`broken` 是产出不是违规；`not_verified` 保守登记不压档；计划外发现计为产出；方法多样性整体仍只作披露。
- **本轮不改项**：台账 FAIL 未闭环粒度（观察项 D）为既有分层口径，保留；正式 A/B 盲测仍缺受控可运行被测应用与独立执行方，不在本轮修复范围。

### 兼容性说明

- 移出版本库的运行产物仅退出版本跟踪，磁盘保留；再次运行 api-test 管线按原路径重新生成并被 ignore 拦截，不影响脚本输出。
- 门禁结构化 flag 为内部实现，CLI 输出字段（verdict_ceiling/reasons/notes/stats）形状不变；旧台账与旧产物可读。

## v1.46.0（2026-11-21）：批判者口径加固——假设锐度、解释或复打、只读 × 写台账对账

按本轮修订方案（N3–N12）落地。目的锚点：专家作为批判者/挑刺者，主动质疑需求、假设与实现，扩大覆盖、减少漏测、在自主测试中多发现 bug；全部新增检测服从「加检测不加锁」，如实登记不被惩罚。

| 条目 | 变更 | 回归 |
|---|---|---|
| N3 | 基线口径修正：735 passed + 2 skipped（环境依赖）+ 230 subtests；v1.44.2 为 630 总（628 passed + 2 跳过）+ 226 subtests；passed 与 skipped 分账书写 | — |
| N12 | `case_data.py` YAML 写入改为 `open(..., newline=)`，兼容 Python 3.9（旧 `Path.write_text(newline=)` 仅 3.10 支持） | 既有 CLI 链 |
| N4 | 未提交 ATK 或提交为空且无理由 → `atk_absent_unexplained` 压档；有非空理由只留痕；新增 `--atk-absent-reason`；`atk_checked` 仅在登记非空时为真 | 5 例三向（无 ATK 无理由 / 有理由 / 已提交不受影响 / 空登记无理由 / 空登记有理由） |
| N5 | 假设锐度质检：空话、占位词不计入覆盖（`hypothesis_vague`），记录保留披露；**已 broken 的笼统假设只披露不压档**（`hypothesis_vague_but_broken`，打破即产出）；合格信号含数值/长度、角色状态名、条件组合、对象 + 预期方向词 | 4 例双向（空话被拦、自然语言与数值假设放行、笼统但 broken 不被罚） |
| N6 | **政策反转**：高风险面（含 `critical`）单一方法且可采信结果全部 held，须换方法复打或提交 `--replay-note` 点名不复打理由，否则 `surface_unreplayed_unexplained`；已 broken 的面不触发；不是方法数量配额 | 5 例双向（无理由被拦 / 复打理由放行 / broken 不触发 / 第二方法探针放行 / critical 面同义务） |
| N7 | 通过档残余声明必须为结构化声明；仅自由文本 → `residual_declaration_required_for_pass`；待核实档保留文本入口；**判定排在 ATK 轨道之后**，ATK 已压档时不再追加声明义务 | 并入 M5 残余声明用例改写 + 1 例顺序回归 |
| N8 | `readonly_lead` × 写台账交叉核对：只对账显式 `linked_records`；关联到 `caution`/`danger` 写记录 → 过程问题 + 压待核实 + 首屏披露；写台账缺失或未显式关联只披露不压档；新增 `--write-ops-ledger` | 5 例三向（不符压档 / 无关联放行 / 缺台账只披露 / 未关联只披露 / 已批 scope_change 不受本项约束） |
| N10 | 浏览器通道降级路由：DevTools 通道无 route/fulfill，需要黑盒注入时切 Playwright 或按缺口四分类登记「能力待探测，未实测」，不得虚构注入 | 文档 |

### 准出记录

- **全量回归**：`python -m pytest tests/ -q` → **755 passed, 2 skipped（环境依赖）, 230 subtests passed**（v1.45.0 基线 735 + 本轮新增 20 例）。2 项跳过来自 `test_skill_registration` 的本机路径/缓存目录检查，随运行环境跳过或通过。
- **二轮审核修正（以项目目的为准绳的四条）**：① N7 判定移到 ATK 轨道之后，消除「同时报结论将达通过 + ATK 未闭环」的自相矛盾误罚；② 笼统假设但结果 broken 只披露不压档（打破即产出，不因措辞笼统扣分）；③ `--atk` 传空数组等同零登记，须附理由，堵住「空文件免解释且标为已核对」的旁路；④ 高风险面判定补回 `critical`（旧版按 high/critical 取面，只判 high 会把更严的面整段漏出义务与建议）。另修 `case_data` 裸 import 为按文件路径加载，避免门禁按路径加载本脚本时导入失败。
- **政策反转显式登记**：N6 替代旧「高风险面单一方法只提示、不压上限」口径。旧测试 `test_single_method_surface_is_disclosed_not_gated`、`test_single_method_repeat_is_notice_only` 覆盖的是低风险/未验证/多方法场景，维持只披露；全 held 的高风险单一方法面走新「解释或复打」义务。
- **三轮复核修正（风险枚举统一 + 档位传播 + 探索分层）**：① 风险分级全项目统一为 `high/medium/low`（上游对抗 plan 契约 `adversarial_data.RISKS`、对抗门禁、探索清单、报告渲染均只认 `high`）；回退 `attack_coverage` 旧版遗留的 `critical` 死分支，`required_surfaces` 与 N6 义务、复测建议只取 `risk=high`，`critical` 不再被当作高风险面。② `phase_gate.check_verdict` 未放行时按上游 ceiling 取最严值，上游已判「不通过」不再被恒压成「待核实」；更正 `CEILINGS` 注释对上游客出档位的描述。③ 探索文档写明分层：机械「不通过」来自台账未闭环 FAIL 用例，探索清单 FAIL 属观察候选，须先落成台账缺陷/用例条目并经闭环核对才进入不通过判定，探索门禁本身不代判。历史条目 N6 与二轮修正④保留当时记录，口径以本条为准。回归用例：`test_critical_is_not_treated_as_high_risk`、attack/explor 两分支 `test_fail_ceiling...`、`test_explore_fail_ceiling...`。全量回归 **757 passed, 2 skipped（环境依赖）, 230 subtests passed**。
- **文档回链**：`agents/web-acceptance-assurance.md`、`references/execution-management.md`、`references/phase-workflow.md`、`references/browser-connection.md` 口径已同步；孤儿引用扫描为 0。

### 兼容性说明

- 新增 CLI 参数全部可选；旧台账与旧产物可读；未提供写台账时对账项只披露，不改变既有结论。
- 自由文本残余声明在「待核实」档仍可用；仅在无其他缺口、结论将达「通过」时要求结构化。
- 历史报告与历史 SKILL 登记口径不重算。

### 本版不宣称的能力

- N1 冻结 A/B 运行矩阵未在本轮执行，不宣称检出能力提升；N2 真实需求样本待项目方提供脱敏素材。
- N9 红队密封包时序核验、N11 第二会话独立复核暂缓，待 N1 数据显示误报/漏检仍是瓶颈后再评估。
- 本轮未改变既有口径：`broken` 是产出不是违规、`not_verified` 保守登记不压档、计划外发现（`surfaces_beyond_plan`）计为产出、方法多样性整体仍只作披露。

### 回退说明

- 本版对应待打 tag `v1.46.0`，上一已验证基线为 `v1.45.0`。
- 回退路径：暂停候选版本用于真实验收 → 切回 `v1.45.0` 对应提交并重新注册 → 按确认后的有效版本重新计算并登记两个结果。

## v1.45.0（2026-11-20）：砺衡项目修订方案 M1–M10 落地

按《砺衡项目修改方案与执行计划（修订版）》（2026-11-20）执行，本版一次性落地
M1–M10 共十项改造，分批推进、每批单独准出：

| 批次 | 范围 | 核心变更 | 新增回归 |
|---|---|---|---|
| S1 | M1 / M12 / M2 / M3 | 门禁链接线闭环：建账→采集→收尾共用同一份产物契约；`--batches`/`--gate-report` 提供但不合格时拒绝建账（rc=2），未提供时不拒绝建账而是逐项留痕并把结论上限压「待核实」（rc=0） | `test_gate_link_contract.py`（19 + 4 subtests） |
| S2 | M4 / M5 / M6 | 攻击面深度校验：结构校验、残余风险报告、跨轮提示与固定正反例 | `test_attack_depth_contract.py`（25） |
| S3 | M7 | 修订申请分级：`readonly_lead`（凭据齐备可继续）与 `scope_change`（先批后动）二分，判定收敛到 `scripts/amendment_class.py` 单一事实源 | `test_amendment_class.py`（20） |
| S3 | M8 | 首屏事实单一来源：台账摘要输出 `headline`，报告首屏与摘要消费同一份事实 | `test_headline_contract.py`（12） |
| S4 | M9 | A/B 运行矩阵：5 夹具 × 2 臂 × 2 重复的配对、汇总、独立裁定与两层归档 | `test_ab_matrix_contract.py`（19） |
| S4 | M10 | 用例资产基线：固定输入指纹与工具版本，差异定位到条目级 | `test_eval_baseline_contract.py`（12） |

### 准出记录

- **全量回归**：`python -m pytest tests/ -q` → **735 passed, 2 skipped（环境依赖）, 230 subtests passed**（v1.44.2 为 630 总：628 passed + 2 环境跳过，226 子测试）。2 项跳过来自 `test_skill_registration` 的本机路径/缓存目录检查，随运行环境跳过或通过，计数以实际环境为准；总选用例数 737 = 735 passed + 2 skipped，passed 与 skipped 分账书写。
- **变异验证**：本轮 34 处语义变异全部被对应断言精确捕获，无假阴性；其中 M7 首轮 2 处
  未捕获（分类判定存在死分支、`execution_ledger` 未真实加载分类模块）已修复后重做。
- **引用回链**：全仓 `.md` 语料扫描，references 与新增脚本的孤儿引用数为 0。

### 兼容性说明

- **旧产物可读**：旧格式基线 `case-eval-baseline/1`（`docs/evals-baseline/fixture-12.json`）保留可读，
  指标照常比对，仅条目级定位标为不可用；旧工作流缺产物时按新口径限档，原因写入报告。
- **CLI 兼容**：新增参数均为可选；不传 `--summary` 时报告走回退路径并显式标注口径说明。
- **历史报告保留**：已用旧口径产出的报告不重算，按当时有效版本存档。

### 回退说明

- 本版对应 tag `v1.45.0`，上一已验证基线为 `v1.44.2`。
- 回退路径：暂停候选版本用于真实验收 → 切回 `v1.44.2` 对应提交并重新注册 →
  按确认后的有效版本重新计算并登记两个结果。
- 源仓库保留可审计的回退提交；原始台账、交接记录、评测记录与标签不随回退删除。
- 若已按新口径登记的任务需要回退，须按受影响范围逐项标注，不整体作废。

### 本版不宣称的能力

- **不宣称检出能力提升**：M9 的正式 A/B 矩阵尚未在真实执行方与真实夹具上运行，本版只交付
  运行装置、配对口径、汇总口径与裁定流程。检出收益须待矩阵实跑后按冻结阈值签收。
- **不宣称真实需求样本覆盖**：`docs/evals-baseline/samples/` 现为合成脱敏样本，
  真实需求样本须由项目方提供已脱敏且获准使用的素材后按同一管线接入。
- **M11（时序证明）、M13、M14 未在本版范围内**。

## 门禁链接线闭环（M1–M3，2026-11-20）

本轮把「建账 → 采集 → 收尾」的证据链从"各脚本各判各的"改为**同一份契约、两端消费**。
改动落在 `scripts/execution_ledger.py`、`scripts/attack_coverage.py`、`scripts/phase_gate.py`，
回归由 `tests/test_gate_link_contract.py` 锁定（19 项 + 4 subtests）。

### 一、措辞—行为对照表（M1）

**三分类的定义**（判定依据是代码实际路径，不是文案承诺）：

- **拦操作**：命令直接失败（退出码 2），不产生台账/工作区。
- **限结论**：命令成功、产物照常写出，但 `summary.verdict_ceiling` 被机械压低。
- **仅披露**：记入留痕字段（`delegated_inputs` / `missing_input_reasons` 等说明字段），不影响上限。
  `missing_inputs` **不属**此类——它是压上限的缺口；由三阶段闸门承担而未单独提供的项记 `delegated_inputs`。

**建账期（`init`）各输入的真实处置**

| 输入 | 提供且不合规 | 未提供 | 分类 |
|---|---|---|---|
| `--batches` | 拒绝建台账（rc=2）：批次未 `done`、`gate` 非 `pass`/`waived`、批次未登记 `case_ids`、`case_ids` 与设计不一致 | 记 `missing_inputs` 缺口 → 压「待核实」；走三阶段流程时转入 `delegated_inputs`（不压上限，首屏须披露"未单独提供、由闸门承担"） | 拦 / 限 |
| `--gate-report` | 拒绝建台账（rc=2）：`status` 非 `pass`/`warning`、`checks` 缺失或为空、存在 `failed`、缺 `S0` 通过项、缺 `counts.testcases`、覆盖条数与设计不一致、`binding` 校验不过 | 同上 | 拦 / 限 |
| `--adversarial-gate` | 提供但字段不合法（缺 `verdict_ceiling`/`adversarial_enabled`/`reasons`）→ 拒绝建台账 | `adversarial_state="missing"`，`adversarial_ceiling="待核实"`；有合资格 `--adversarial-waiver` 记 `waived` 但**上限仍为待核实** | 限 |
| `--phase-state` | 阶段未走完放行流程（有未批准修订 / 路由误配未确认 / 同会话放行 / 交接未消费）→ `phase_ceiling="待核实"` | 记 `missing_inputs` 缺口 → 压「待核实」 | 限 |
| `--attack-coverage` | 缺 `verdict_ceiling` 或 `gaps` → 拒绝建台账 | 记 `missing_inputs` 缺口 → 压「待核实」 | 拦 / 限 |

**关键修正**：文档此前写"不传这两个参数时保持旧行为"。实测为**不拒绝建账、但逐项留痕并压上限「待核实」**——
"保持旧行为"会让使用方以为不传就没有后果。已在 `agents/web-acceptance-assurance.md`、
`skills/case-generator/SKILL.md`、`references/execution-management.md`、`references/phase-workflow.md`
四处同步改写。CLI 帮助文本同样逐条对齐（`--adversarial-gate` 原文写"拒绝建账"，实为压上限）。

**已登记的其他措辞差异（记录影响，留待对应任务处理，本轮不改）**

| 位置 | 差异 | 影响 | 归属 |
|---|---|---|---|
| `scripts/execution_ledger.py` `--adversarial-waiver` | 豁免成立时仍记 `adversarial_ceiling="待核实"`，文案暗示"豁免后可恢复通过" | 豁免被理解为放行时，使用方预期与上限不符 | M7（修订分类契约）落地时统一表述 |
| `references/execution-management.md` 结论上限段 | 只写了对抗门禁与跳间校验对上限的影响，未提 `missing_inputs` 通路 | 使用方只知两条压档路径，不知第三条 | 已在本轮补入 |

### 二、plan 交叉核对三态契约（M2）

`attack_coverage.py` 产出 `plan_check_state`，取值三态：

| 值 | 含义 | 后果 |
|---|---|---|
| `not_provided` | 未提供 plan | 弱化调用 → 压「待核实」 |
| `empty` | 提供了 plan 但风险面集合为空 | 弱化调用 → 压「待核实」 |
| `checked` | plan 与风险面已交叉核对 | 不额外压档 |

**空 plan 不得自证覆盖**——这是三态设计与单一布尔位（`surf_plan_checked` 兼容读取）的根本差别：
布尔位分不出"没传"和"传了空的"，两者都会冒充"已核对"。

两个消费端（`phase_gate._attack_check`、`execution_ledger._attack_precondition`）按**同一口径**消费：
非 `checked` 即压「待核实」并留痕。生产端 → 消费端串链由 `test_producer_consumer_chain` 锁定。
旧产物缺该字段时**不压档**（字段缺失 ≠ 弱化调用），保证存量批次可复跑。

### 三、缺失即留痕与收尾刷新（M3）

- **缺口另记**：缺口写入 `baseline.missing_inputs`（顶层），**不混进 `baseline.gate_precondition`**——
  后者是门禁判定结果，已有精确断言锁定其形状。
- **同一职责不得被计两次缺失**：走三阶段流程时（提供 `--phase-state`），阶段完整性、跳间校验、
  攻击覆盖三项职责都由 `phase_gate` 承担（它的 `submit` 强制校验评审/用例/对抗 plan 三件交接产物，
  `execute` 阶段收尾强制交攻击覆盖结论）。此时台账**不再索要** `--batches`/`--gate-report`/
  `--attack-coverage`——否则合法三阶段流程会被误压成「待核实」，让"走完流程"比"只做一半"更差。
  旧工作流（未提供 `--phase-state`）则按原入口契约计缺口。
- **对抗门禁单独判定**：其缺失由 `_adversarial_precondition` 判定并留痕
  （`adversarial_state`/`adversarial_reasons`），不在缺口清单里重复计——否则同一缺口会出现两套原因码。
- **收尾复核同轮最终产物**：`summary` 可接 `--phase-state`/`--attack-coverage`/`--adversarial-gate`，
  按同一判据重算缺口；同轮补齐即消除对应临时缺口，**已成立的更严结论不因此放松**
  （「不通过」在任何刷新下保持「不通过」）。

### 四、清单新增标准（M12）

**适用范围**：本包所有维度清单与条目清单——`references/bug-detection-map.md` 的检出维度总图、
`skills/requirement-review/references/prd-dimensions.md` 的八维度、
`skills/figma-ui-parity/references/check-dimensions.md` 的 7.x 编号表、
`skills/api-test/references/framework.md` 的 A–AE 大类，以及各 skill 的 `MANUAL_CHECKS` 类黑名单。

**新增一个维度/条目，必须附三项论证，缺一项即驳回**：

1. **对应漏测类型**：说明它覆盖哪一类**原本发现不了**的问题。必须指到具体的失败模式
   （如"上传成功但内容为空"），不能写"提升全面性"这类无法证伪的话。
2. **为什么现有维度覆盖不到**：须点名是**哪个现有维度**覆盖不到、差在哪。
   若现有维度加一个判据就能覆盖 → **驳回**（这是加判据的事，不是加维度的事）。
3. **共享规则归属**：说明它落在哪套共享清单口径下。与现有维度重叠时，
   走 `references/bug-detection-map.md` §三的共享口径（重叠允许，但结论口径各自独立，
   跨 skill 引用必须带 skill 名、禁止裸编号），并在总图里登记，不另起一套事实源。

**"暂不支持"类条目的移出同样是变更**：以编码期事实（能否拿到输入/观测通道）为准，
不因"名字在不在某个字典里"而永久化。移出须在同一次提交里补上替代路径或写明为何不需要。

**拟增条目演示（审核记录）**

拟增：主体系五视角新增「**跨会话交接完整性**」维度。

| 论证项 | 提交内容 | 裁定 |
|---|---|---|
| 对应漏测类型 | 交接包缺失或交接内容与上一阶段实际结论不符，导致下一阶段按错误的起点继续 | 成立——交接错误一旦发生，后续全部观察都建立在错基线上，且不易从单阶段产物看出 |
| 为什么现有维度覆盖不到 | `phase_gate` 的机械校验只管**交接是否发生**（token、session、确认码），不管交接**内容是否忠实**。五视角各看各的页面，不看上一阶段说了什么 | 成立——"发生"与"忠实"是两个正交失败模式，现有维度查后者查不到 |
| 共享规则归属 | 落在 `bug-detection-map` 总图的「流程纪律」组，与三阶段闸门组交叉索引；不新增编号空间 | 成立 |

**裁定：驳回（留待 M7 落地时重新提报）**。理由：本轮 M7 正在改 `phase_gate` 的修订分类契约，
交接内容的忠实性判定需要 `request_amendment` 的 `kind` 分类作为载体（当前 `request_amendment()`
没有 `kind` 字段），先立维度会造成"有维度、无判定依据"的空挂。等 M7 的分类契约落地后，
按本标准重新提报，届时须补上机械判据（交接包声明与上一阶段终态的逐项比对）。

### 五、验证

- 全量：`pytest tests/ -q` → **649 passed, 230 subtests passed**（改造前基线 630 + 新增 19）。
- 新增回归：`tests/test_gate_link_contract.py` 19 项 + 4 subtests，覆盖 M1/M2/M3 三条。
- **变异验证**（证明新增测试不是恒真）：注入三处语义变异，各自被精确捕获——
  移除三阶段适用性豁免 → `test_same_duty_not_counted_twice` 转红；
  移除 `phase_gate` 消费侧 `plan_check_state` 压档分支 → `test_phase_gate_consumes_weak_call`
  的 `not_provided`/`empty` 两态转红；把 `--adversarial-gate` 帮助文案改回"拒绝建台账" →
  `test_adversarial_gate_help_does_not_claim_reject` 转红。变异体均在确认注入后执行，
  失败信息均为语义断言而非 SyntaxError。

## 攻击面深度校验（M4/M5/M6，2026-11-20）

改动落在 `scripts/attack_coverage.py`，回归由 `tests/test_attack_depth_contract.py` 锁定（25 项）。

### 一、M4 结构校验与假设质检

**三类分开处理**（合成一条时使用方只知道"判据不可证"，不知道该补什么）：

| 情形 | 缺口码 | 补法 |
|---|---|---|
| 无判据占位（未声明独立通道，或通道只写被测物自述） | `oracle_no_criterion` | 补一条真正的独立通道 |
| 缺真实执行证据（通道有，但拿不出可复核的证据路径） | `oracle_no_evidence` | 补可复核的证据路径（截图/接口响应/日志） |

**两条硬规则**：

- **缺结果不能计为 held**。原先 `hypothesis_outcome` 缺失时按 status 兜底成 `held`，
  于是 NOT_RUN 的记录被算成"假设没被打破"——**未验证冒充已验证**，
  正是这套门禁要防的事。现在结果推导不出时单列 `no_result`，
  记缺口 `hypothesis_without_result`，且不计入 `held`。
- **无独立判据或证据无法支持覆盖**。风险面"被覆盖"现在要求该面的失效假设
  **有结果 + 独立判据 + 有证据**三者齐备（`surface_coverage[surf].supported`）。
  只写了一句预测不算打过——否则高风险面门槛可被一行文字绕过。
  高风险面因此分两种缺口：`surface_without_hypothesis`（没写预测）与
  `surface_hypothesis_unverified`（写了但无可采信验证结果）。

**只提示不压上限的两项**（误伤合法描述会把如实登记变成违规）：

- `suspicious_wording`：可疑自然语言措辞（如"这个应该没问题"）。
  合法描述（"并发建卷时卷名重复应拒绝"）不命中。
- `single_method_surface`：同方法重复。

**高风险面方法单一**（`high_risk_surface_single_method`）单独成条，与整体方法多样性分开：
整体多样性只是分布描述（可被凑），但某个高风险面只用一种方法是该面没被打透的信号。
**只提示，不压上限**——方法多样性一旦压档就会逼人拆碎去测没打算测的东西
（`test_single_method_surface_is_disclosed_not_gated` 的既定口径），
且"换手法"是使用方的选择，不是本轮结论成立与否的前提。

**复测建议**（`retest_suggestions`）把"建议换手法"落到具体动作，三要素齐备：
针对哪个风险面（`surface`）、已有观察是什么（`observations`）、
本轮可用的是什么（`available_methods` / `available_paths`，取自已登记数据，不臆造）。
建议是**附加说明**，不取代原提示与理由。

### 二、M5 残余风险声明三态

旧文本入口（`--residual-note` 自由文本）判不了"这条声明说的是不是真的"，保持兼容，
只判有没有引用已登记编号。新增结构化入口 `--residual-declaration`（JSON 文件）：

```json
{"items": [{"id": "SURF-01", "status": "unbroken"}]}
```

`status` 取 `unbroken` / `broken` / `not_verified`，逐条核对：

| 情形 | 处理 |
|---|---|
| 真编号且状态相符 | `residual_declaration.items[].verdict = "matched"` |
| 编号存在但关联错误（状态与实际验证状态不符） | 缺口 `residual_status_mismatch`，压上限 |
| 虚构编号（不在已登记清单） | 缺口 `residual_ref_not_registered`，压上限 |
| 零项（空 items） | 三态为「无未破项」，不压上限 |
| 声明为 `not_verified` | 三态为「尚未验证」，**不压上限**——保守登记不该被惩罚 |

三态：**有未破项**（has_unbroken）/ **无未破项**（no_unbroken）/ **尚未验证**（not_verified）。
"实际验证状态"只由已登记事实决定：SURF 面出现过 broken 假设 → 已破；
有可采信验证且未破 → 未破；没打或验证不可采信 → 尚未验证。
ATK 条目：命中 → 已破，未命中 → 未破，其余 → 尚未验证。

**报告首屏**按 `residual_declaration` 的实际条目呈现三态，不得只写一句"已声明残余风险"。

### 三、M6 ATK 跨轮停滞披露

「允许长期悬置」是刻意的让步，但长期悬置若一直不被看见，就退化成"登记了就算做过"。
新增 `--atk-prev`（上一可比批次的 ATK 快照），判停滞的条件全部机械可核对：

- **同范围同稳定 ID**：范围键 = （ID，验证路径）。旧 ID 被复用（换了验证路径）不是停滞——
  那是换了一条假设，拿它说"停滞"是误判。
- **仍未执行**：本轮状态为「未执行」；上一批同键也须为「未执行」（曾经动过就不是持续悬置）。
- **验证路径无实质变化**：已含在范围键里。

**只披露不压上限**（`atk_stagnant`）：停滞是过程事实，不是缺陷。
与本轮的 `atk_unexecuted` 分开——后者是本轮事实，前者是**跨轮**事实。

### 四、验证

- 全量：`pytest tests/ -q` → **674 passed, 230 subtests passed**（M4–M6 改造前 649）。
- 新增回归：`tests/test_attack_depth_contract.py` 25 项，覆盖 M4/M5/M6 三个方向 + CLI 入口。
- **变异验证**（证明新增测试不是恒真）：七处语义变异各自被精确捕获——
  缺结果不再单列 → `test_missing_result_is_not_counted_as_held` 转红；
  覆盖判定不看独立判据与证据 → `test_shallow_record_cannot_support_coverage` 转红；
  判据缺口不再分两类 → `test_missing_oracle_and_missing_evidence_are_separate` 转红；
  高风险面方法单一不再提示 → `test_high_risk_single_method_is_disclosed_with_retest_plan` 转红；
  状态不符不再记缺口 → `test_status_mismatch_is_a_gap` 转红；
  虚构编号不再记缺口 → `test_fabricated_reference_is_a_gap` 转红；
  停滞判定的两处条件失效 → `test_same_id_same_path_still_unexecuted_is_stagnant`
  与 `test_old_id_reuse_is_not_stagnant` 转红。
  变异体均在注入后做语法校验（首轮两处因替换成 `pass` 造成 SyntaxError、
  一处未触及有效分支，均已重做并确认捕获）。

## 修订申请分级（M7，2026-11-20）

修订申请此前只有一条处置路径：登记 → 等批准 → 才能继续。这条路径对**范围变更**是对的
（新增写入、扩权、换环境、超预算必须先批后动），对**固定授权范围内的只读观察**则是错的——
如实登记一个只读线索，比"先做后说"更糟，却反而被卡住。

现按两类分流，处置相反：

| 分类 | 凭据 | 处置 |
|---|---|---|
| `readonly_lead` | `scope` / `environment` / `role` / `allowed_actions` / `no_state_change` 五项齐备 | 登记后即可继续，状态记 `approved_readonly`，不阻断推进、不压结论上限 |
| `scope_change` | 命中 `writes` / `role_change` / `environment_change` / `over_budget` / `unknown_attributes` / `incomplete_evidence` 任一信号 | 先批后动，状态记 `awaiting_approval` |

### 一、判据只认登记事实

- **从严顺序固定**：先看范围变更信号，再看只读凭据是否齐备。反过来会让"有写入信号
  但也填了只读字段"的申报蒙混过关。
- **缺凭据从严**：五项只读凭据缺任一项 → 归 `scope_change`。
- **`path_type`、HTTP 方法、"未看到写操作"单独都不是只读凭据**（方案 §3.2）。
- **不传声明从严**：旧调用（不传 `declaration`）一律按 `scope_change` 处理，原行为不放宽。

### 二、单一事实源

分类契约落在 `scripts/amendment_class.py`（新增），`phase_gate` 与 `execution_ledger`
共用同一个 `is_blocking()` 判定函数。口径是「**类别决定需不需要先批，状态决定批了没有**」。

首轮实现把判定写成「`status == awaiting_approval` 且 `kind != readonly_lead`」，
**变异验证时发现这处 kind 判断是死分支**：`readonly_lead` 登记后的状态是
`approved_readonly`，永远不等于 `awaiting_approval`，条件恒不成立——kind 字段无人消费。
且 `execution_ledger` 当时只在注释里说"共用契约"，实际上根本没加载该模块。
现已改为判定统一收敛到 `is_blocking()`，kind 重新承重（只读观察即便落在待批准状态也不阻断）。

### 三、阻断点覆盖四个入口

未批准的范围变更在 `start` / `checkpoint` / `resume` / `check-verdict` 四处一致阻断。
`resume` 是本轮补上的——跨会话换会话不应成为绕过先批后动的缺口。
台账侧（`_phase_precondition`）与 `phase_gate` 同口径，不再各判一份。

### 四、验证

- 全量：`pytest tests/ -q` → **694 passed, 230 subtests passed**（M4–M6 改造后 674）。
- 新增回归：`tests/test_amendment_class.py` 20 项，覆盖分类判据、phase_gate 四处阻断行为、
  台账上限、CLI 入口。
- **变异验证**（证明新增测试不是恒真）：七处语义变异全部被精确捕获——
  只读不得压过写入信号 → 1 failed；只读凭据缺项不再从严 → 2 failed；
  `is_blocking` 去掉类别判断 → 2 failed；`phase_gate` 退回只认 status → 1 failed；
  台账退回只认 status → 1 failed；`resume` 不再阻断 → 1 failed；
  登记时分类不再决定处置状态 → 2 failed。
  首轮两处变异（撤掉的是死分支）未触发红，经复核判定为**假阴性**并重做。

## 首屏事实单一来源（M8，2026-11-20）

首屏此前由两处各自推断：报告渲染器从 CSV 台账与门禁 JSON 拼「未执行项/缺失视角」，
交付模板则由人按印象写「完成范围」。两者各算一次，结论必然漂移——而且漂移方向
通常是报好看的那个：门禁自身判「通过」，综合后其实「待核实」，报告首屏却只显示前者。

现由 `execution_ledger.py summary` 产出 `headline`（`schema=ledger-headline/1`），
**报告渲染器与交付模板消费同一份事实**：

- **完成范围**（`completion_scope`）：计划/已执行/未执行/阻塞/跳过/失败计数与一句人话；
  `can_claim_readonly_complete` 标志「只读范围内已完成」是否**允许**写。
- **必查项状态**（`required_checks`）：三阶段闸门、对抗门禁、攻击面覆盖、证据链完整性、
  门禁输入齐备性五项，状态为 通过/受限/缺失/未知，各带可核对说明。
- **限档原因**（`limitation_reasons`）：逐条 code 与说明（失败/阻塞/未执行/跳过/
  门禁缺失/门禁受限/仅授权限制）。

### 一、上限只有一个来源

`headline.verdict_ceiling` 与顶层 `verdict_ceiling` **同值**：上限只由
`_verdict_ceiling()` 算一次，headline 消费该值，不重算。

渲染时另有一处修正：报告第二屏「各数据集的门禁结果」里的结论上限是**门禁单项判定**，
此前与首屏并列显示，同样读作「本报告结论上限」。现表头改为「门禁自身结论上限」，
并加注说明两者口径不同、冲突时以首屏为准——两套上限并排而不加区分，读者只会取其一。

### 二、口径来源必须可区分

渲染器新增 `--summary` 参数，三种情形在报告上可区分：

1. 提供 summary 且含 headline → 正常路径，首屏直接消费；
2. 提供 summary 但缺 headline（旧摘要）→ 回退推断，**首屏显式标注**「台账摘要缺首屏事实字段」；
3. 未提供 summary → 回退推断，**首屏显式标注**「未提供台账摘要」。

第 2、3 种不得静默按推断口径出首屏——否则报告看起来照样完整，读者却不知道限制因素是推断的。

### 三、只读完成声明的条件

`can_claim_readonly_complete` 为 true **当且仅当**：本轮为只读授权范围、
五个必查项全为「通过」、无失败/阻塞/未执行/跳过、无适用门禁输入缺失。
任一条件不满足即为 false——授权限制不能替代未完成的必查项。

### 四、验证

- 全量：`pytest tests/ -q` → **706 passed, 230 subtests passed**（M7 后 694）。
- 新增回归：`tests/test_headline_contract.py` 12 项，覆盖台账侧五种首屏情形与
  渲染器端到端（真实 CLI 产出 HTML，断言首屏取自 summary 而非门禁值）。
- **变异验证**：七处语义变异全部被精确捕获——只读完成标志恒真 / 恒假各 1 failed；
  首屏上限另算一份 → 3 failed；渲染器改用门禁上限 → 1 failed；
  缺字段与未提供两种标注被撤掉各 1/2 failed；走三阶段时误报攻击覆盖缺失 → 2 failed。

## A/B 运行矩阵与配对汇总（M9，2026-11-20）

此前 `blind_eval.py` 只能做两臂**全局聚合**对照（`ab` 子命令）。但方案要求的是
**按夹具分别配对评分，再汇总配对差值**——臂内聚合把不同环境的运行混在一起，
配对差值就不再是"同一条件下换个做法"的差值。

### 一、冻结的运行矩阵

新增 `docs/evals-baseline/run-matrix.json`（`ab-run-matrix/1`）：5 个夹具 × 2 臂 × 2 次
重复 = 20 次运行，逐条登记（夹具、臂、重复编号、run_id）、预算、规则与**冻结阈值**。
出分后不得改夹具、重复数、阈值或预算；如需变更，另立矩阵 ID 并同时披露。

### 二、三个新子命令

- `pair`：单夹具配对。配对键 = （夹具，重复编号）。产出每对的差值、夹具内均值，
  以及缺陷级明细（新增命中 / 共同命中 / A 有 B 无 / 两臂皆漏）与**新增 blocker 漏检**
  （A 命中、B 未命中的 blocker）。
- `matrix`：汇总。先对同夹具重复取均值，再将各夹具**等权**汇总；主指标 =
  配对运行中命中冻结答案键的 blocker/major 缺陷数差值（B−A）。输出逐夹具明细、
  无效配对清单、冻结阈值逐项对照（`threshold_check`，只给事实不下效果结论）与
  UTF-8 BOM 的可复算 CSV。
- `adjudicate`：独立裁定。机械未匹配的发现逐条裁定（`valid` / `false_positive` /
  `insufficient_evidence`），每条写明理由与 `root_cause`，同根因重复发现按根因归并。
  **必须逐条覆盖**：漏一条即拒——否则误报率没有可复算的分母。机械已命中的发现
  不得被裁定为无效。

### 三、无效配对与证据不足

无效配对（夹具/协议指纹不同、预算不同、跨臂同一执行方、缺一侧 run）一律排除，
原因逐条列出。**有效配对不足的夹具标为证据不足，不计入汇总分母**——拿一半运行
算出的均值描述的是那一半，不是这个夹具。

### 四、归档分两层

- **受限区完整件**：协议副本、矩阵副本、原始计分、裁定、隔离检查、无效记录、汇总。
- **常规去敏件**（`--sanitized`）：不含原始计分与答案 ID，只留汇总事实、去敏明细与
  可复算 CSV。答案键与原始证据一律不进归档。

隔离检查在归档时**重跑一次**并留档：`prepare` 当时通过不代表执行完成后目录仍干净
（执行方可能事后把答案写回目录）。去敏模式下，泄漏诊断只报**命中数量与文件位置**，
不写答案 ID 本身——把 ID 写进问题描述再脱敏，等于脱敏只做了一半。

### 五、验证

- 全量：`pytest tests/ -q` → **725 passed, 230 subtests passed**（M8 后 706）。
- 新增回归：`tests/test_ab_matrix_contract.py` 19 项，覆盖配对口径、汇总口径、
  无效配对、证据不足、裁定完备性、阈值对照与两层归档。
- **变异验证**：八处语义变异全部被精确捕获——配对退化为单次 → 3 failed；
  夹具内均值改拉平求和 → 2 failed；无效配对不再排除 → 5 failed；
  跨臂同执行方不再判无效 → 1 failed；裁定漏一条不再拒 → 1 failed；
  证据不足夹具仍进分母 → 2 failed；去敏归档不脱敏 → 1 failed；
  新增 blocker 漏检不报 → 1 failed。

## 用例资产基线：固定输入与条目级定位（M10，2026-11-20）

`eval_cases.py` 此前只存一组指标做跨版本比对，有三个缺口：
①基线不记输入，换一份用例也能拿来比，指标对不上时无从判断是"退化"还是"换题了"；
②工具换版后口径可能已变，但基线看不出来；
③差异只到指标级——"断言密度下降"这种报告，仍然不知道该改哪条用例。

### 一、基线新格式（`case-eval-baseline/2`）

基线除指标外还固定三样：**输入指纹**（用例文件 sha256）、**工具版本**、**条目级摘要**
（每用例的断言数/优先级/套件/层/方法、结构错误、覆盖缺口、缺层）。

- 输入指纹不同 → 输出标 `comparable=false` 并说明"换题了"：机械指标口径可能已变，
  差异仅供参考，**不得直接当退化判定**。
- 工具版本不同 → 同样标不可比并写明版本差。
- **旧格式基线保留可用**：`fixture-12.json` 是历史口径锚点，仍能读（指标照常比对），
  只是没有条目级摘要，差异定位标为不可用——不猜、不用当前值冒充基线。

### 二、差异定位到条目

`diff_detail` 把差异落到三类：**结构**（新增/已修复的结构错误逐条列出）、
**覆盖**（新增/已闭合的缺口、缺层变化）、**断言**（哪条用例的断言数从几变到几），
外加用例的新增/删除。主 CSV 增加「变化」与「条目级定位」两列，
`--out-diff` 输出完整明细（UTF-8 BOM）。

### 三、样本与分账

- `docs/evals-baseline/samples/`：新增脱敏样本 `requirement-sample-desensitized.json`
  及其已发布基线，附 `README.md` 写明可复跑命令、真实需求样本的接入与脱敏要求。
  **现有样本是合成教学需求，不是真实客户需求**——真实需求样本须由项目方提供
  已脱敏且获准使用的素材后按同一管线接入。
- **M10 的结构评分与 M9 的实际检出指标分账出具**：结构更完整不等于抓到更多真缺陷；
  M9 的检出收益也不能反推结构评分达标。

### 四、验证

- 全量：`pytest tests/ -q` → **737 passed, 230 subtests passed**（M9 后 725）。
- 新增回归：`tests/test_eval_baseline_contract.py` 12 项，覆盖输入指纹固定、版本固定、
  换题标不可比、旧基线兼容、四类条目级定位、差异 CSV 与样本可复跑。

## v1.46.2（2026-11-23）：v1.45.1 修复线与 v1.46.x 加固线合并

### 一、合并来源

- 本地线（`backup/v1.45.1-fixes` 留痕）：`59ac7bd`（F1–F8 影响面审查修复）+
  `0cb952c`（R1/R2 复审修复）。
- 远端线（origin/main 53fe31d）：v1.46.0 自主挑刺能力加固（N3/N12/N4–N10）、
  二轮/四轮审核整改（产物脱库、模板枚举同步、档位结构化）、档位不变量守卫。
- 分叉点 `f047b30`；两线为独立演进，merge 后逐项核对：F1/F4/F7/R1/R2 已被远端
  等价实现覆盖（`_applicable_missing`、`"NA"` 口径、blind_eval 重写），
  F2/F5/F8 为远端仍缺的独立修复，随合并带入。

### 二、冲突解决（3 处，均融合保留双方）

- `plugin.json`：版本取 `1.46.2`（合并提交顺延远端 1.46.1，小步递进）。
- `agents/web-acceptance-assurance.md`：修订申请段融合（本地「类型取值合格 +
  auto_approval」× 远端「linked_records 对账」）；攻击面门禁段取远端压档清单 +
  本地 schema/tool_version 尾段。
- `docs/SYNC-NOTES.md`：双方记录块均保留。

### 三、判据版本

- `attack_coverage.TOOL_VERSION` 2 → **3**：远端 v1.46 改了判定口径
  （N6 例外、ATK 轨道、多样性压档），按 F8 口径必须 +1；旧台账在 v2/v3 下
  可能得到不同 `verdict_ceiling`，归档引用须写明判的版本。
- `eval_cases.TOOL_VERSION` 保持 2：本轮未改其判定，不同步 bump。

### 四、验证

- 合并冲突解决后全量：见本轮回归记录（预期 ≥779 passed / 230 subtests）。
- cache 同步：`cache/my-experts/web-acceptance-assurance/1.46.2/` 新建；
  `1.45.1/` 下 4 个启用态 `SKILL.md` 改名 `.disabled`
  （`test_skill_registration` 旧缓存禁用规则）。

## v1.47.0（2026-11-23）：实测整改——Agent 专项路由、字段诊断、视角深度口径、裁决前置登记

按 2026-11-23 问卷星星仔全能力测试后的专家包自审结论落地。目的锚点不变：专家作为批判者/挑刺者自主全面测试，扩大覆盖、减少漏测、如实登记不被惩罚。本版**不新增测试配额**，只修两类问题：让该测的被测到（知识路由、要求可见性），以及让按文档写的第一次就能过（字段诊断、参数措辞）。

| 条目 | 变更 | 回归 |
|---|---|---|
| E07 Agent 专项路由 | `agents/web-acceptance-assurance.md` 入口判路表新增「被测对象本身是对话式 Agent / AI 助手」一行；「参考规范读取契约」把 `skills/case-generator/references/methodology/ai_feature_testing.md` 第 8 节列为该类对象的必读项（静默消息丢失双层核对、流式完成判定、同通道互证三层证据、审批与澄清驱动纪律、专属载体、不顺从自述）；`references/route-prompts.json` 增 R10 路由回归样本 | 新增 `tests/test_v147_route_and_gates.py::TestAgentSubjectRouting` 3 项 |
| E01 字段诊断可定位 | `scripts/exploration_state.py` `_object()` 把「缺少必填字段或包含未知字段」拆成「缺字段 [...]」与「未知字段 [...]」两段并点名具体字段 | `TestFieldDiagnostics` 3 项 |
| E03 视角深度口径 | `scripts/render_report_html.py` 消费门禁 `thin_views` 与 `view_cells`：视角表新增「页面×状态单元」列，覆盖偏薄的视角标「记录偏薄」而非「有台账记录」，未测项小节同步披露 | `TestDepthWording::test_report_marks_thin_views` |
| E05 提示语形态 | `scripts/attack_coverage.py` `surface_unreplayed_unexplained` 缺口提示补「--replay-note 传的是 JSON 文件路径，不是文本」及文件形态 | `TestDepthWording::test_replay_note_hint_states_file_path` |
| E06 help 措辞 | `scripts/render_report_html.py` `--dataset` help 明确门禁参数须为探索清单门禁（整份报告或含顶层 `gate` 的 JSON），并写明其它门禁产物不适用 | `TestDepthWording::test_render_help_states_gate_source` |
| E08 产物契约对照表 | `references/output-templates.md` 新增「0.4 产物与消费脚本对照」，列明各阶段产物文件、字段集要点与消费方；主提示词指向该表 | `TestArtifactContractTable` 3 项 |
| E04 裁决前置登记 | `scripts/phase_gate.py`：`complete` 新增 `--assumptions` / `--regression`（execute/adjudicate 阶段可提前登记，只做结构校验）；交接包新增「下一阶段需提交的前置清单」，未登记即列出；`_assumption_reasons` / `_regression_reasons` 增加 `structure_only` 口径，使 `result=open` 可登记但**不豁免终检**（登记过 ≠ 已证伪） | `TestVerdictInputRegistration` 6 项 |

- 全量：`pytest tests/ -q` → **798 passed, 230 subtests passed**（v1.46.2 为 780 + 230）。
- 判定口径未变：`attack_coverage.TOOL_VERSION` 保持 3、`eval_cases.TOOL_VERSION` 保持 2——本版未改任何判定口径，只改诊断与呈现。
- 本版**不宣称**：跨域 iframe 场景的探索清单同源限制未放宽（本版以分域清单 + 跨域证据关联表落地，契约未改）；界面样式/易用性/无障碍的量化检查能力未新增；A/B 检出能力未评测。

## v1.47.1（2026-11-23）：影响面复审整改——旧产物兼容、条目数防漂移、第二处字段诊断

按「结合影响范围、以项目目标为核心」的 v1.47.0 复审结论落地。v1.47.0 的 7 项改动本身兑现了承诺，但影响面只覆盖了改动时想到的那一个消费方，遗漏三类：旧版门禁产物、同一类报错的另一处校验器、引用基线条目数的常量。本版只修这三类，**不新增测试配额、不改判定口径**。

| 条目 | 变更 | 回归 |
|---|---|---|
| F1 旧产物兼容 | `scripts/render_report_html.py`：`view_cells` 缺席时整列不渲染（此前 v1.46 形态门禁会被渲染成「8 条目 / 0 单元 / 有台账记录」自相矛盾）；部分缺失该格显 `—`；note 明确「依据产物未提供该数据」 | `TestLegacyGateCompatibility` 3 项 |
| F2 条目数防漂移 | 主提示词「路由回归基线（OM6）」条目数 9 → 10，覆盖枚举补「Agent 能力测试」——原先照字面执行会跳过新增 R10，使 E07 的防回归机制自伤；`references/route-prompts.json` 注释改指真实测试文件，并写明增减条目须同步声明 | `TestRouteBaselineCountGuard`（机械比对声明与实际条数） |
| F3 第二处字段诊断 | `scripts/adversarial_data.py` `_object()` 与 `exploration_state` 对齐：分列「缺字段 [...]」「未知字段 [...]」——手写 `adversarial-plan.json` 是第一道坎，原先只能读源码定位 | `TestAdversarialFieldDiagnostics` 3 项 |

- 全量：`pytest tests/ -q` → **805 passed, 230 subtests passed**（v1.47.0 为 798 + 230）。
- 判定口径未变：`attack_coverage.TOOL_VERSION` 保持 3、`eval_cases.TOOL_VERSION` 保持 2。
- 本版**不宣称**：跨域 iframe 的探索清单同源限制仍未放宽；`mandatory_attributes` 与 `thin_views` 并存的原始 gate 输出未改口径（报告层已消歧，门禁算法按 v1.47.0 复审决定暂缓）。

## v1.47.2（2026-11-23）：跨域清单契约与多份分域 gate 补登记

按 2026-11-23 问卷星星仔全能力实测后的两项遗留整改落地（v1.47.1 交付说明标「未做/待授权」，本轮用户授权两件都做并发版）。只放宽清单可表达范围与补登记通道，**不改任何判定口径、不新增测试配额**。

| 条目 | 变更 | 回归 |
|---|---|---|
| G1 跨域清单契约 | `scripts/exploration_state.py`：`context` 增可选 `allowed_origins`（纯 origin 数组，无路径/查询/片段/凭据）；`page_url` 与 `target_url` 或任一同源即通过。缺省行为完全不变。`export_csv` 不把该字段展开进 CSV 行（否则 DictWriter 多余字段报错），JSON 摘要保留供审阅识别跨域清单 | `CrossOriginContractTests` 10 项（旧清单零回归、跨域放行、纯 origin 校验、门禁不放宽、CSV 不展开） |
| G2 多份分域 gate | `scripts/phase_gate.py`：`--exploration`（complete/revise）接受 JSON 数组一次登记多份分域清单。合并取最严 ceiling、合并缺口、任一份缺控件遍历即整体缺；外层 `source`/`label` 透传进 `sources` 与缺口标签供追溯；单份调用方零回归 | `MultiExplorationGateTests` 10 项（含反后门：任一份非通过不得解锁；revise 不得洗白通过） |
| G3 契约文档 | `references/exploration-runtime.md` 字段表与同源规则同步；`references/phase-workflow.md` 写明多分域 gate 合并口径与补登不改阶段状态 | `test_reference_links` 等既有文档校验 |

- 全量：`pytest tests/ -q` → **825 passed, 243 subtests passed**（v1.47.1 为 805 + 230，新增 20 项）。
- 判定口径未变：`attack_coverage.TOOL_VERSION` 保持 3、`eval_cases.TOOL_VERSION` 保持 2。
- **本版不宣称**：跨域放宽只让跨域清单能表达并通过字段校验，不改变门禁算法（缺视角/FAIL/缺遍历仍压待核实）；待核实不会被补登洗成通过。本项目实测把两份分域 gate 补登后，execute 探索字段从 missing 变 provided，但整体结论仍为待核实（同会话放行 + 四视角实测缺口）。

## v1.47.3（2026-11-23）：回退跨域旁路字段 + 多份 gate 路径加固

v1.47.2 复审（两轮）发现其 G1（`context.allowed_origins`）虽未改门禁算法，但**换了输入池**：视角覆盖按 (page_url, state) 单元统计，跨域混入单份后一域的记录会补齐另一域的缺口——受控实验：域A全覆盖+域B仅功能1单元，两份独立登记=待核实，合并单份=通过。「算法没改但口径被换」，属过度宣称下的放水旁路。本轮按复审建议**方案甲回退**，同时修复数组路径的四处 P3。本项目实测走的是两份独立登记路径，交付物未被污染。

| 条目 | 变更 | 回归 |
|---|---|---|
| P1 回退 allowed_origins | `scripts/exploration_state.py`：context 恢复严格三字段（未知字段显式拒绝）、page_url 恢复严格同源，报错指路「每域各出一份清单 + phase_gate 多份 gate 合并」；docstring、`references/exploration-runtime.md` 字段表同步。**跨域表达力不损失**：多份独立清单是更严的等价表达 | `CrossOriginBypassBlockedTests`：allowed_origins 显式拒绝、跨域 page_url 拒绝含迁移提示、**v1.47.2 旁路场景整体阻断**（同一批证据只能走两份独立路径，结论待核实、缺口完整保留） |
| P3-a 数组混 None/空 | `phase_gate._exploration_check` 与 `_read_exploration_arg`：占位元素显式报错（原实现 state 仍报 provided、该份诊断因死变量丢失）；嵌套包装（数组项再带 gate 键）同样拒绝 | 锁死「静默丢诊断」通道 |
| P3-b 死变量清除 | 合并分支不再累加后丢弃逐份 reasons；最终 reasons 由 gaps 合成 | — |
| P3-c mandatory_attributes 合并 | 多份合并顶层不再恒 `{}`：逐键取最严（任一份未闭合即未闭合；闭合冲突时 covered 严于 not_applicable） | 合并结果含未闭合键并进 gaps |
| P3-d 误导报错 | multi=False 收到 JSON 数组时报「请走多份路径」，不再误报 verdict_ceiling 非法 | 报错文案断言 |
| P3-e CLI 免手工合并 | `--exploration`（complete/revise）改 `action="append"` 可重复传，`_read_exploration_args` 展平数组、单路径保持单份 gate 形状；单 dict `{"source":..,"gate":..}` 的标签也透传 | 多路径展平、数组展平、单份形状不变、None→None |
| P1-a 项目报告 | 本项目 `星仔全能力测试报告_20261123.md`：修正「未运行探索清单门禁」等与 phase-state 矛盾的表述；7 条门禁缺口（含 mandatory_attributes 未闭合、核验域无 EXP-TRAV）如实全列 | 报告口径与 check-verdict reasons=2 一致 |

- 全量：`pytest tests/ -q` → **829 passed, 239 subtests passed**（v1.47.2 为 825 + 243；测试文件重写：跨域断言反转为拒绝、新增 6 项 P3 测试）。
- 附带恢复正确的表述（v1.47.2 时曾判过时，回退后重新成立）：`observation_bridge.py` 同源硬校验、`README.md`「严格校验同源探索清单」、`references/source-comparison.md`。
- **本版宣称**：跨域场景唯一合法路径是「每域一份同源清单 + 多份 gate 合并」；单份混排从「能表达但会放水」变为「不存在」。判定口径依旧未放宽：缺视角/FAIL/未闭合/缺遍历仍压待核实。

## v1.47.4（2026-11-23）：原始清单误登记的误导报错修复（v1.47.3 自审补漏）

按用户要求对 v1.47.3 做审核自身修改，端到端实测（temp 副本 + CLI revise 双 --exploration）抓到一处残留：把**原始探索清单**（schema_version/items 形态，无 gate 结论）直接当 gate 登记时，报错仍是复审 P3-d 批判过的误导文案「verdict_ceiling 非法 … got None」——没告诉用户真正的问题（该先跑 summarize）。v1.47.3 只修了 multi=False 收数组的误导报错，原始清单入口漏了。

| 条目 | 变更 | 回归 |
|---|---|---|
| 原始清单检测 | `phase_gate._exploration_check_one`：识别 schema_version+items 且无 verdict_ceiling 的原始清单形态，报错指明「先运行 exploration_state.py summarize 生成 gate 摘要，或传 summarize 完整报告（自动取 gate）」 | `test_raw_plan_file_rejected_with_summarize_hint`（报错文案断言） |
| CLI append 数据链锁定 | `test_read_args_summarize_reports_end_to_end`：两份 summarize 完整报告 → `_read_exploration_args` → 合并取最严，锁住双路径 wiring | 端到端单测 |

- 全量：`pytest tests/ -q` → **830 passed, 239 subtests**（v1.47.3 条目记录的 829 passed 系 cache 未同步态下的数字，当时含 1 个 cache 态失败；同步后实际 830，本版以同步后全量为准）。
- 端到端验证（真实项目副本）：CLI `revise` 双 `--exploration` 传 summarize 报告 → ceiling=待核实、sources=2（标签透传）、gaps=7；会话校验对无权会话正确拦截。

## v1.47.5（2026-11-23）：v1.47.3/1.47.4 完整审核补漏

按用户要求对该轮修改做完整审核（不依赖自述，从 diff 与行为实测重推）。回退与合并逻辑本身正确，但发现三处行为缺陷与两处记录错误：

| 条目 | 发现（实测） | 修复 | 回归 |
|---|---|---|---|
| 单份 gate 字段为空 | `{"gate": null}` 文件静默降级为 state=missing——用户以为登记了清单，实际登记成「未提供」，与数组混 None 显式报错的口径不一致 | `_read_exploration_arg` 单 dict 分支显式报错 | `test_read_args_empty_gate_field_rejected_not_silently_missing` |
| 空对象 gate | 空文件 `{}` 仍报误导的「verdict_ceiling 非法 got None」（v1.47.4 只识别原始清单形态） | `_exploration_check_one` 增空对象检测，报「gate 为空对象」并指路 summarize | `test_empty_object_gate_rejected_with_clear_error` |
| 未闭合态合并顺序抖动 | `blocked + unresolved` 正反序得不同值（虽都判未闭合、判定不受影响） | 改「先到者胜」的稳定合并，注释写明多个未闭合态之间不定义先后 | `test_mandatory_attributes_two_open_states_are_order_stable` |
| 测试数字记录错误 | v1.47.3 记 829、v1.47.4 记 830，均为 cache 未同步态下跑出的数字（含 1 failed） | 同步 cache 后全量真值 **831 passed / 239 subtests**，交付说明两处一并更正 | — |
| cache 污染 | `cp -r` 把 `.pytest_cache` 带进 cache 1.47.4（源目录该目录未被 git 跟踪） | 已清理；后续同步排除 | — |

- 审核另做实证（结论：不需要改）：回退彻底（原始清单含 allowed_origins → 未知字段拒绝；export_csv 内部先 summarize 故同样拒绝）；mandatory_attributes 三态冲突逐项正确；reasons 由 gaps 合成且非通过时必有；全仓 allowed_origins 残留仅历史记录与测试；调用方仅 phase_gate 内部两处；本项目 6 份 exploration 产物无该字段（可复现）；报告与 check-verdict（reasons=2）一致。
- 全量：**834 passed, 241 subtests**（先同步 cache 再实测；v1.47.4 真值为 831 passed / 239 subtests，本版新增 3 测试 + 2 subtests）。

## v1.48.4（2026-11-24）：执行调度与证据事实化——滑块题实测四项落地

依据滑块题项目（liheng-slider-20261124）实测复盘的四项审核发现落地：高风险项遇阻未换路、覆盖缺失仍能收尾、证据类型错配被记 PASS、重复证据虚增探针数。改造方案与执行计划见 `D:/TestCase/liheng-slider-20261124/砺衡专家包改造方案与执行计划.md`。

| 条目 | 变更 | 回归 |
|---|---|---|
| M1 执行调度 | `exploration_state.py` summarize 新增 `gate.next_actions`：按风险+复现候选（FAIL 条目在同一风险级排前）排序的可执行清单、阻塞项复查条件、next_focus 单项指派；判定业务仍由专家负责，脚本只做排序呈现 | `test_next_actions_promotes_reproduction_candidate`（FAIL 候选优先）等 2 例 |
| M2 采集收尾 | `phase_gate.py` complete(execute) 新增 `--collection`：消费台账采集记录 JSONL，含判定字段（verdict/collection_state 违规混入）即拒绝收尾（结构违规列明逐条原因）；未提供记 `state=not_provided`、空文件（0 条）记 `state=empty`——两者均在 check-verdict 压「待核实」并分文案留痕（空清单不得冒充"已校验通过"）；旧状态文件无该字段按原契约披露，保留阶段性交付能力 | `CollectionClosureTests` 6 例（dirty 拒收尾/clean 入账/缺失压级/空文件登记 empty/旧状态文件披露/传给 plan 报错）+ 既有链路测试全部补合规采集 |
| M3 断言证据 | `execution_ledger.py` finish 新增 `evidence_verified`（逐断言布尔）：全部断言无证据核实却报 PASS → 拒绝；部分核实 → PASS 入账但 summary 新增 `evidence_unverified` 逐条暴露缺口（case_id+check）。下载/保存/交互类「证据类型错配」从此在台账层可见，不再拖到裁决 | `test_unverified_pass_rejected`、`test_partial_unverified_exposed_in_summary` 等 3 例 |
| M4 探针计数 | `attack_coverage.py` analyse 新增 `evidence_runs_deduped`（按现场证据集包含归并去重的独立执行数）与 `probes_declared_missing_runs`（无申报值记录数，区别于 0）；申报值 `probes_total` 原样披露不去重。修复首版归并逻辑两处缺陷：互为子集的证据组两侧全部被丢弃（真子集关系下 `<` 与 `<=` 判定互相命中）、旧批次「有证据无申报」漏计 missing | `ProbeEvidenceDedupTests`（同证据三记录计一次/新证据计复跑/无申报报 missing）3 例 |

- 准出记录：全量 `python -m unittest discover -s tests -q` → **870 passed**（M4 修复归并缺陷前 98 项 attack_coverage 有 2 失败，修复后全绿）。缓存守卫：plugin.json 升 1.48.4 后旧缓存 1.47.5 SKILL.md 已禁活（改名 .disabled，缓存副本无 git 属禁活例外）。
- 兼容性：`complete(execute)` 不传 `--collection` 仍可收尾（state=not_provided，非硬阻断）；`finish` 不传 `evidence_verified` 走原口径（视为已核实，兼容旧批次）；`next_actions`/`evidence_unverified`/`evidence_runs_deduped` 均为新增键，不改既有键语义。
- 回退：本版四项彼此独立，`git revert` 单条提交即可分别回退；M2 回退后 check-verdict 恢复不查采集；M4 回退后恢复申报值直加。
- 本版不宣称的能力：不改变「脚本排序、专家决策」的职责边界；采集记录的真实性仍由复核环节把关；探针去重只按证据文件集，不识别「同文件不同内容」的复跑（该场景应换新文件名归档）。

### v1.48.4 复审更正（v1.48.5 发布）

按「子 agent/自身描述不可信、以 git 与实跑为准」复审 v1.48.4 提交内容，发现四处问题，全部修复并实测：

| 发现（实测坐实） | 修复 | 回归 |
|---|---|---|
| **M2 测试类整批丢失**：SYNC-NOTES 声称「CollectionClosureTests 4 例」，`grep -c CollectionClosure tests/test_phase_gate.py` = 0——补丁流程中执行过 `git checkout -- tests/test_phase_gate.py` 回退，把新测试类连同首轮补丁一起冲掉，只重新补了 helper 与调用点。870 全绿是因为基座 finish_phase 已带合规 collection，缺的只是 M2 自身的锁定测试 | 补回 `CollectionClosureTests` **6 例**（比原声称多 2：空文件与传给 plan 的分支） | 6 例全绿；全量 876 passed |
| **空采集登记逃逸**：`--collection <空文件>` → `records=[]` → `violations=[]` → 登记 `state=provided`，一个空文件把「未提供」洗成「已校验通过」，check_verdict 采集分支静默放行 | complete 区分 `empty` 态；check_verdict 对 empty 单独文案压「待核实」（实测撤分支该测试变红） | `test_empty_collection_file_not_disguised_as_provided` |
| **版本痕迹错误**：4 个脚本共 6 处注释写 `v1.49.0`，实际发版 1.48.4 | 统一改为 v1.48.4（正文不留版本痕迹属常规清理，注释保留作变更溯源） | grep 零残留 |
| **新键消费缺位**：`next_actions`/`evidence_unverified`/`evidence_runs_deduped`/`probes_declared_missing_runs` 在脚本与文档里零读取点（仅 SYNC-NOTES 提及）——按「注释里说不算、grep 真实读取点」纪律，属半死字段 | 契约接线：`execution-management.md` §3 步 3 写 evidence_verified 口径与 §攻击面表写去重双指标；`exploration-runtime.md` 写 next_actions 换路口径；`phase-workflow.md` §3 写收尾三态口径；`attack_coverage.py` CSV summary 白名单补两个新指标 | 契约回链 grep 命中；CSV 导出含新行 |

- 复审同时核对的**非缺陷**项：旧状态文件兼容（collection 键缺失走「披露不拒绝」分支，实测留痕文案正确）；`scan_collection_records` 自 v1.38.0 已存在，本次是接进收尾主链路非新造；revise 不覆盖 collection 补正——旧状态文件本就压级、新状态空文件有 empty 态，升级依赖是重跑 complete 的正当流程，不构成解锁旁路（记录为已知设计边界）。
- 过程教训：**批量补丁脚本中途 `git checkout -- <test file>` 会把同文件内的新增测试一并冲掉**。跨会话/多轮补丁后必须 `grep` 点名核对每个声称的测试类/函数真实存在，再跑全量——"Ran N tests OK" 不能证明目标测试在其中。

## v1.48.6（2026-11-25）：v1.48.5 复审——采集漏交死路封堵与交接前置补全

对 v1.48.4/v1.48.5 两轮改动做第三轮审核（从 diff 与实测重推，含对真实项目 `D:/TestCase/liheng-slider-20261124` 状态文件的兼容实测）。上一版把「revise 不覆盖 collection」判为「不构成解锁旁路的设计边界」，本轮实测**该判断是错的**：`check_verdict` 的 `allowed = not reasons` 意味着采集缺口是**硬阻断裁决**（不只是压上限），而 `complete` 只能调一次——漏交即永久死路，没有任何补救入口。

| 条目 | 发现（实测坐实） | 修复 | 回归 |
|---|---|---|---|
| **采集漏交死路** | execute 收尾漏交 `--collection` → `state=not_provided` → `check_verdict` 追加 reason → `allowed=False`；`revise` 只认 attack_coverage/exploration，无法补正 ⇒ 该项目永远无法给出裁决结论 | `revise` 增 `collection` 参数（CLI `--collection`）：走与 complete 同一套 scan-collection 校验（脏记录照拒、空文件照记 empty），只解除「采集未校验」这一条阻断，不触碰 attack/exploration 各自上限 | `test_revise_collection_unblocks_verdict` / `..._rejects_dirty_records` / `..._empty_not_disguised` 3 例 |
| **交接包不提示采集前置** | `_pending_verdict_inputs` 只列 assumptions/regression，交接包明写「下一会话须先补齐再给技术结论」，却不提会硬阻断的采集缺口——下一会话开工即撞墙 | 交接包 `pending_inputs` 增采集缺口条目，附补正命令 `revise execute --collection` | `test_handoff_lists_collection_gap_as_pending_input` / `..._omits_..._when_provided` 2 例（撤该分支测试变红） |
| **契约冲突回归** | 既有 `test_registration_clears_pending_list` 断言「前置全部登记后交接包不再出现前置清单段」变红——新逻辑在采集未交时又加一条，该段仍现。该断言本身正确（锁的是「全登记即清空」语义），冲突源于其 fixture 用旧契约未交采集 | 按新契约补 fixture（`collection=clean_collection_file()`）；**不改测试意图** | 全量 881 passed |
| **CLI 与文档接线** | `revise --help` 无 collection；`phase-workflow.md` 未写补正入口；agent 主文档 §165「采集收尾必交」只提攻击覆盖，不提采集（会漏做） | 三处补齐：CLI help、phase-workflow 收尾段增「漏交的补正入口」、agent §165 增 `--collection` 必交与补正路径 | 文档回链 grep 命中 |

- 影响面实测（真实旧项目副本，不动原文件）：旧状态文件无 `collection` 键时新增 reason **不改变**结论——v1.48.4 与 v1.48.6 对 `liheng-slider-20261124` 均得 `allowed=False / ceiling=待核实 / 5 条 reasons`，零差异；模拟 `not_provided` 后 `revise --collection` 能解除采集 reason（allowed 仍 False 因该项目另有真实缺口，属正确行为）。
- 变异验证：撤 `revise` 的 collection 分支 → 3 例变红；撤交接包提示 → 1 例变红；还原后全绿。
- 影响面清点：全仓仅 `phase_gate.py` 读写 `phases[...]`，`render_report_html.py`/`finding_summary.py`/`blind_eval.py` 均不消费阶段状态，无遗漏消费者；`scan_collection_records` 输入容忍度实测（字符串数组/None/空数组/空对象）均按预期返回违规或空。
- 准出：全量 **881 passed**；注册守卫 3 例绿（旧缓存 1.47.5 已禁活）。新键 `collection.revised` 与 `empty` 态已进入 complete/revise 两条写入路径，`check_verdict` 三分支（缺失/empty/非 provided）各自有独立文案与测试。

## v1.49.0（2026-11-25）：裁决语义解耦 + 评测协议挂载 + 执行自主性加强

### B1（F-04）· check_verdict 声明式分层｜B2（F-06）· 旧状态补正｜B3（F-07）· note 留痕｜B4 · 文案收编

- **B1 根因**：`allowed = not reasons`（原 1352 行）把"压档"与"拒绝出具结论"实现成同一件事，`verdict_allowed=True + ceiling=待核实` 不可表达，缺证据类缺口与造假/越权同档硬阻断。**修复**：单一注册表 `REASON_POLICY`（18 code × {mode, remedy, needs_human}，不新增判断条件）；`verdict_allowed = not blocking_reasons`（仅 block 档：阶段未完成/未批范围变更/交接未消费），downgrade 档允许出具但上限取最严（上游「不通过」不降级）；schema 升 `phase-verdict-check/2`，新增 `blocking_reasons[]`/`limitations[]`（code/message/remedy/needs_human）/`policy_version`，reasons 全集保留。**回归**：`test_verdict_policy.py` 8 例 + test_phase_gate 13 处 downgrade 断言改写；变异（撤 block 归类）6 红。接线四处（CLI help / phase-workflow 补正入口段 / agents §165 / output-templates §0.3），grep 旧口径零残留。
- **B2**：`_pending_verdict_inputs` 的 `is not None` 让旧状态文件（无 collection 键）逃过点名 → 改「字段缺失或 state!=provided」一视同仁，remedy 取注册表；pending_inputs 只影响交接包文本，check-verdict 零差异。回归：`test_pending_inputs_cover_legacy_state_without_collection_key`（撤分支变红）。
- **B3**：`revise --collection` 无 approval 无 note 即可自解锁 → 不引入人工审批，`--note` 必填，事件记 `{auto:true, basis}`，不占 approval，文档明写「留痕供人核对，不防有意伪造」。回归：`test_revise_collection_requires_note`/`..._records_auto_basis`（撤校验变红）。
- **B4**：pending 的 assumptions/regression 手写 label 收编进注册表（`label` 字段，`{label}：{remedy}` 单一来源）；采集 pending 前缀改「采集缺口」，`grep -c '采集合规'` 3→2。回归：v147 交接包名称断言先红后修复（label 保留名称）；全量 892 OK。

### C 路 · 回链 + 探针补充 + 披露细化（纯文档/派生视图，零 schema 与 TOOL_VERSION 改动）

- **C3 回链**：case-data-contract.md 增「对抗场景回链」节（SCN-ADV- 的 id/note 带设计场景 ID 前缀；四类路径完备填表 ≠ 组合场景已登记）；probe-playbook C 节补纪律；§0.4 增「对抗场景」行；cases-example.json goal 补 SCN-ADV-01- 回链示例（yaml 镜像同步 to-yaml）。**偏离登记**：C3「一个夹具补示例」未执行——检测夹具含答案且 E1 未实跑，改题面破坏盲测纯净性并改冻结哈希，由 yaml 镜像承担（夹具哈希未变）。
- **C1 探针**：probe-playbook B 节补 3 行（payload 级状态机规避/双写覆盖/旧快照回读），登记 method 映射既有稳定 ID，METHOD_IDS 未变。
- **C2 披露**：5.8 七项→九项：combo 路径数（零脚本改动）+ 高风险面攻击路径分布（`_emit_csv` 追加 `section=surface_path`，result.get 兜底旧产物）；JSON 契约与 TOOL_VERSION 未变。回归：`test_csv_exports_combo_and_surface_path_sections`（撤段变红）。

### D 路 · 执行自主性加强（L4 阶段闸门保留不动；maxTurns 未改）

- **D 减法（C-1 兑现）**：run_journeys.cjs 删本地 resolvePlaywright+启动逻辑（空 catch 静默吞失败），launch/closeAll 接回 browser.cjs 唯一来源，finally 改 closeAll 顺带修复 browser 句柄泄漏；冗余删除 17 行。**验收口径（审核 P1 修正 b）**：本文件不再自带 resolvePlaywright；其余 4 个自带定义驱动（capture_aria/run_state_scenarios/run_official_extraction/run_button_audit）登记为后续减法项。
- **D1 runtime_probe.py**：五通道三态纯探测（http_contract 依赖恒具备/available 取决于 manifest——X-07；auth_state 只查 profile 存在可写不做读回；browser_mcp 恒 unprobed 会话内判定）+ runtime-manifest/1 校验；消费四落点见 §0.4；adversarial_gate 本版未改（X-03）。回归：19 例（撤 manifest 依赖判断变红）；无 playwright 环境返回结构化三态不崩。
- **D2 exploration_runner.py**：只编排不执行；ready_queue 复用 summarize() 不另建排序；X-02 自动续批走 classify+is_blocking 双层（record 内嵌 declaration 供第二层凭据复验），三种反向凭据 requires_human；X-13 honest_exits 只登记条目 ID；writeback_commands 为发射模板（审核 P3）；预算账本 budget-consumption/1。回归：14 例（三反向凭据全绿；classify/is_blocking 为防御纵深，无行为差异，验收按 §19 以 grep 在位确认）。
- **D3 runtime_diagnostics**：交接包独立字段（batch_count/probes_consumed/budget_remaining/blocked_by_capability）派生可重建；不落 state 顶层、不进 pending_inputs、phase-handoff/1 不升版（X-06）；CLI checkpoint --budget-ledger/--capabilities。回归：2 例；不自动 resume、不自动放行。
