# SYNC-NOTES — 抽取副本人工对照清单

> 只服务开发期对照，**运行时完全不读**（集成方案 v2.4 §9）。
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

**边界（不宣称已解决的）**：`ui_metrics.py` 只判可量化项，不构成 WCAG 合规认证；对比度对渐变/半透明只能报待确认；攻击密度 0.30、视角单元 ≥2、方法多样性 ≥3 均为工程判断，未经真实轮次校准；`--residual-note`、`assumptions`、`regression` 仍属"愿不愿意填"层面的机械约束，防得了忘记、防不了决心造假。

版本 1.41.0（v1.40.0→1.41.0）；480 tests + 217 subtests 全绿（新增 61 例：attack_coverage 12、execution_ledger 9、phase_gate 9、exploration 若干、ui_metrics 30）。
