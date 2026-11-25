# 结构化用例与覆盖检查契约

## 作用与边界

此契约将需求拆解、功能点和场景覆盖落实为可校验的设计资产。小任务可使用等价回复表格；用户要数据包、需要跨轮维护或用例较多时采用本格式。JSON 是唯一数据源；YAML、HTML 和统计是派生产物。格式版本为 `meta.schema_version: "1.0"`，与专家版本独立。

脚本只验证设计结构、引用和部分覆盖信号，不运行浏览器、不计算执行通过率、不判定业务完整性、不生成 UAT 签收。不得以命令返回0代替需求评审或网站实测。执行事实沿用 `execution-evidence.md`，按 TC ID 关联独立 RUN/BUG 文件，不将实际响应、重试或证据塞入预期字段。不要使用旧专家按名称关联的格式直接调用本脚本。

## 文件与数据关系

在用户项目的 `deliverables/requirement-YYYYMMDD-<short>/` 维护：`cases-<short>.json`、按需生成的同名 YAML、按需 `review-<short>.html` 和 `manifest.md`。用户未要文件且任务很小不强制创建这些文件。

- `scripts/case_data.py`：包根目录下的校验、JSON→YAML、语义比对工具。
- `examples/cases-example.json`：教学设计源，包含有意测试的空字符串，不是完整边界清单或真实执行结果。
- `tests/test_case_data.py`：真实脚本单测及 JSON→YAML→校验→比对串链测试。

修改 JSON 后重新派生 YAML 和评审视图，不分别手写两份数据。必需字段不得空白占位；可选未知描述可省略并在 open_questions 说明；**测试数据中的空字符串、null、false、0 可以是有意输入，应保留其类型**，不能机械清除。schema 的数据类型是协议约束，不是从关键词推断。

## 顶层结构

| 字段 | 内容 |
|---|---|
| meta | schema_version、id、title、flow_kind（simple/workflow/stateful）；可选 application_version、example_only、stats |
| requirements | 非空数组：id/name/source/description/confirmation；确认状态 confirmed/provisional/ambiguous |
| features | 非空功能数组：id/name/description/children；每功能至少一个叶子 |
| children | 叶子 id/name/description/requirement_ids；可选 apis，元素含 method/path/source，未知接口不编造 |
| acceptance | 非空数组：id/requirement_id/description/required布尔/reason/confirmation/oracle/elements/case_mode/case_ids；elements为四要素对象（scene/object/condition/focus）；case_mode为AND或OR，OR须alternative_reason |
| scenarios | workflow/stateful 必填且非空，simple可省略；场景 id/name/goal/feature_ids/paths/not_applicable |
| testcases | 非空用例数组，字段见下表 |
| diagrams | 按需数组：id/scenario_ids/mermaid；源码无代码围栏；脚本只检查基本字段，不能证明图可渲染或语义正确 |
| applicability | 按需对象：键限于 functional/security/performance/accessibility/compatibility/visual/reliability/usability/content，每项含 state 与 reason；缺类或状态非法产生告警/错误 |
| decisions / open_questions | 按需记录拆解、分类、范围、图和评审决定，以及待澄清问题；由人或专家分析，脚本不代判 |

ID 跨所有节点全局唯一，引用按 ID；名称修改不影响关联。每个 REQ 至少一个必选 AC；每个 AC 至少一个必要 TC，必选 AC 的必要 TC 必须 required=true。AC.case_ids 与必要 TC.acceptance_ids 保持反向关系；TC可关联多个AC和FP，但AC的需求必须在关联FP的需求范围内。补充非必选TC可关联AC而不进入必要组合。必要组合不能测试失败后临时修改以获得通过。

`elements` 四项都要写具体：场景定位页面或业务域，对象定位字段/功能/元素，条件写前置、边界、角色或状态，关注点写要验证的可观察行为。任一项为空、只剩「正常/成功/符合预期/功能可用」这类空话、或含 TODO/待补充等占位内容，`case_data.py` 直接判错；整体缺 `elements` 给告警，`--strict` 下视为失败。AC 的 description/oracle 与 TC 的 assertions.check/oracle 同样禁止空话和占位。

`applicability` 是逐类裁定矩阵，七类都要有结论：状态取 applicable / not_applicable / insufficient_evidence / deferred，not_applicable 与 deferred 必须给非空 reason，缺类产生未裁定告警。状态非法、给未知类型或漏理由均判错。该矩阵只表达「这一类要不要测、为什么」，不代替实际执行结果。

## 场景路径

`paths` 固定含 positive/boundary/exception/permission 四个数组。每条路径为 id/description。空数组须在 `not_applicable` 对应键写**对象形态**的具体举证：`{reason, business_object, checked_at}`——reason 写清该场景为何无此类路径（页面入口、规则依据等可核查事实），business_object 给出该场景的业务对象且须出现在 reason 文本或场景名中，checked_at 为核对日期。豁免理由**不得跨场景复用**：同一 kind 下两场景出现相同理由判 error（模板化批量豁免是把异常/权限这类 bug 高发区整块让渡，机器必须拦截）。字符串形态仍被接受但降级为 warning 提示升级对象举证（向后兼容；`--strict` 下视为失败）。缺理由产生告警。复杂流程每个 TC 指定 scenario_id 和 path_id，路径必须属于该场景，TC所关联FP须在场景的feature_ids中。无 TC 的路径和叶子产生覆盖告警；告警不得静默忽略，修复或在 manifest 记录原因，严格校验将其视为失败。

## 对抗场景回链（C3 约定，纯文档口径，零 schema 改动）

`scenarios[].id` 是设计侧唯一标识。对抗批次登记的 `SCN-ADV-` 场景（形态见 `schemas/adversarial-scenarios.schema.json`，校验 `adversarial_data.py --scenarios`）在**其 `id` 或 `note` 中带所对应设计场景的 ID 前缀**作回链（如 `SCN-ADV-01-…` 对应设计场景 `SCN-01`）。两条纪律：

- **cases JSON 是设计唯一事实源**：不把对抗场景结构复制进 cases JSON，也不另建第二套场景库；对抗场景伴随文件 `adversarial-scenarios.json` 与 cases JSON 通过 ID 前缀互相定位。
- **四类路径完备填表 ≠ 组合场景已登记**：paths 四类数组齐备且豁免举证合规，只证明单点路径覆盖；`SCN-ADV-` 三类组合场景（跨模块/操作序列反常/状态机规避链）是否登记由 `adversarial_gate.py --scenarios` 机械核查，两者不可互相替代。

## 用例字段

| 字段 | 约定 |
|---|---|
| id/title | 稳定 TC ID 与具体标题 |
| feature_ids/acceptance_ids | 非空 ID 数组；跨功能旅程可以多个FP |
| scenario_id/path_id | workflow/stateful 必填，simple按需；保持所属关系 |
| priority/required | P0/P1/P2；布尔，与必要AC组合一致 |
| layer | ui/api/component/integration/e2e，表示主要验证层级 |
| attributes | 非空数组：functional/security/performance/accessibility/compatibility/visual/reliability/usability/content，可多选 |
| suites | 非空数组：feature/smoke/regression/exploratory/e2e，可多选，汇总按TC去重 |
| classification_reason | 根据测试目标与断言解释分类，关键词仅辅助提醒，不强制覆盖人工判断 |
| role/preconditions/data | 角色、非空前置字符串数组、具体数据对象；未知认证不得编造 |
| steps | 非空数组，每项 action/expected，完整展开 |
| assertions | 非空数组，每项 check/expect/oracle；expect可为字符串、数字、布尔、null等业务预期类型 |
| dependency_mode | real/sandbox/mock/replay；结论限定模式，不暗示生产副作用 |
| cleanup | 本次实体清理方式，受授权及平台规则约束 |

层级、质量属性、套件分别描述不同维度。出现“密码”不自动认定安全，出现“接口”不自动取代UI功能；真正的鉴权安全断言与功能登录可以分别建例。无授权的压测或安全攻击场景可以先设计，不能因数据包里写了就自动执行。主链路场景应包含至少一条**持久化读回或跨页一致性断言**（保存后刷新读回、页面 vs 导出、tab A vs tab B）；该项由设计评审人工核对，不做关键词机器判定，避免“恒真测试”式的编码期黑名单永久化。

## 使用命令

路径均相对专家包根目录；从任意项目调用时将脚本参数换成绝对路径。转换和比对依赖 PyYAML；本次环境已具备，其他环境缺失时报告工具阻塞，不自动安装。

```bash
python scripts/case_data.py validate examples/cases-example.json --strict
python scripts/case_data.py to-yaml cases-demo.json cases-demo.yaml
python scripts/case_data.py validate cases-demo.json --strict
python scripts/case_data.py validate cases-demo.yaml --strict
python scripts/case_data.py diff cases-demo.json cases-demo.yaml
python -m unittest discover -s tests -v
```

退出码：0=符合该命令检查，1=校验错误/严格告警/语义差异，2=输入解析、参数或工具依赖错误。交付数据包前 JSON和YAML校验及diff必须通过；不需要YAML时只检查JSON。可选 meta.stats 必须完整等于脚本计算的 requirements/functions/feature_points/acceptance/scenarios/testcases 六个计数；未提供时不强制写，报告统计仍从数据算。

## 评审视图与记录

可按用户需要从同一份 JSON 生成三视角：功能点视角看局部覆盖；场景视角看路径分支；接口视角按有来源的 method+path 聚合。**所有用例必须可见**，UI层的安全/性能检查也保留；没有endpoint的用例放“无接口映射”并说明，不能按type过滤丢失。前置、steps、assertions均逐条展开，保留TC/FP/SCN/PATH及AC链接。

HTML是按契约生成的可选评审稿，本包不提供自动HTML渲染器。生成后核对源数据唯一TC数与视图可定位的唯一TC数、重复视图不重复计数、步骤及断言未截断、图能渲染；不能仅凭结构校验声称HTML已检验。评审意见与执行状态独立存储并按TC ID关联，重渲染先读取并保留已有意见，不能以评审通过替代执行通过。

manifest记录需求摘要、拆分/分类/范围/图/评审决定、发现和处置的覆盖缺口、待确认问题、校验命令及真实结果、派生文件来源。不得写入密码或虚构业务确认。
