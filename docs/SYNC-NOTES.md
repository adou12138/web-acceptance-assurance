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
| 6 | `prompts/schemas/*.json`（16）、`knowledge/**`（4） | `skills/case-generator/references/schemas/`、`references/methodology/` | 复制件完整性 | 已对照（一次性复制，参考用） |

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

- **剥离源**：figma-ui-parity **4.47.0**（版本冻结）。源 skill **零改动**（源 git 零 WIP + 对照快照证明零写入）。
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
