# 执行资产清单

> `SKILL.md` 只留路径判定、红线与能力索引（各路径入口命令在 `references/playbooks/`）；本文件逐个说明 `scripts/`、`adapters/`、`assets/`、`schemas/` 的职责。
> **找"某个脚本是干什么的""某个模板在哪"时打开这里**，不要通读。

## scripts/ — 入口

- `flow.py` — 一键流水线：载入契约 → 判定适用性 → 读写计划 → 生成用例 → 执行 → 增强 → 回读自检。
- `api_test_runner.py` — 运行器模板，渲染 `FINDING_CSV_COLS` 台账行。注意：其内部的 `complete_request_body()` 只是绑定模板示例常量的薄包装，规则本体在 `common_params.complete_request_body()`——独立脚本一律调后者，勿从本文件导入（会连带引入站点示例常量）。
- `smoke_runner.py` — 冒烟批量探活。
- `case_runner.py` — 用例执行与断言调度。

## scripts/ — 配置与共享层

- `personal_config.py` — 个人参数加载与优先级（环境变量 > 配置文件 > 报错）。
- `common_params.py` — 公共参数 `encode`/`appid`/`action`/`sign`/`ts` 的组装与补齐的单一出处，深度与冒烟共用；`complete_request_body()` 供独立脚本补齐台账报文。
- `finding_columns.py` — 台账列口径的单一来源：读 `assets/finding_columns.json` 并回退内置默认列。
- `discovery_profile.py` — 发现档：集中管理探测广度限额。
- `kb_freshness.py` — 知识库新鲜度检查（只读、建议性）：主判据为源-索引漂移，墙钟与站点快照时间只展示；KB 不可用 exit 2 并给降级提示，不阻断契约定位（fail open）。开场自检顺序见 `SKILL.md` 判路表。
- `kb_load_contract.py` — 从知识库按 action / keyword 载入契约；`flow.py --action` 与 `business_flow.py` 复用同一解析顺序（`--kb` > `KB_PATH` > 同级回退）。本机 KB 根路径、存在性检查与失效降级见 `doc_review.md`「本机知识库根路径」节（未传 `--kb`/`KB_PATH` 时报错属预期行为）。

## scripts/ — 生成链

- `contract_parser.py` — ShowDoc / OpenAPI 3.x / Swagger 2.0，JSON + YAML，`$ref` 与嵌套递归展开。
- `contract_model.py` — 契约模型。
- `field_semantics.py` — 字段语义识别。
- `applicability.py` — 适用性矩阵与 N/A 理由（`OP_MATRIX` / `NA_REASON`）。
- `pattern_engine.py` — 缺陷模式匹配。
- `case_generator.py` — 契约驱动用例生成。
- `business_scenarios.py` — AF 生成 + 七项门禁 + prompt 注入业务流程位置与 L5 知识。
- `companion_scan.py` — 配套接口解析：越权读面 / 真往返 / 列表与计数交叉验证。
- `dim_generators.py` — 零产出维度补齐。
- `llm_semantics.py` — 语义层并入与校验（`--mode prep/merge`）。
- `llm_fact_cases.py` — 语义层兑付：没并入就一条不产。

## scripts/ — 执行与闭环

- `assertion_engine.py` — 断言调度引擎。
- `assertion_helpers.py` — 纯断言 helper，经 import 回引保持 `R.check_xxx` 引用契约。
- `attribution_engine.py` — 失败归因（缺陷 / 环境风险）。
- `artifact_readback.py` — 产物回读校验（BOM / 表头 / 列数 / 编号 / 行数 / 证据链列）。
- `case_coverage.py` — 用例执行率对账：rate 严格 / looseTotal 宽松双口径。
- `check_key.py` — 约束对账门禁。
- `evidence_bundle.py` — 脱敏证据包。
- `minimizer.py` — 失败请求最小化。
- `regression.py` — 回归基线与复测四态判定。
- `asset_registry.py` — 资产登记与批准。
- `relationship_inference.py` — 接口关系推断。
- `risk_engine.py` — 风险排序。
- `plan_builder.py` — 计划构建。
- `plan_render.py` — `plan.md` 渲染。
- `case_signature.py` — 重复/矛盾判等签名。
- `asset_runtime.py` — 产物归档落点。
- `dependency_scan.py` — 契约声明依赖扫描。
- `precondition_scan.py` — 运行实证依赖扫描（候选须 `--confirm` 才生效）。
- `report_enhance.py` — 报告增强（curl 复现等）。
- `pattern_feedback.py` — 缺陷回流为模式候选。
- `bug_pattern_pool.py` — 缺陷模式池：`--merge` 累积跨轮 hits、`--rank` 出风险 Top、`--list` 汇总；只累积与排序，不自动生成用例。
- `ui_baseline.py` — 页面基线登记与校验。
- `pending_confirmation.py` — 待确认分组与放行条件，`pending_confirmations.json` 的生成方，`flow.py` 直接依赖。
- `multi_interface.py` — 多接口批次汇总与 acceptance 批次产物。

## scripts/ — 业务流（问卷星专用、独立入口、未被通用引擎引用）

- `business_flow.py` — 读已确认/待确认的流程元数据，校验资源引用、步骤依赖、回读与清理规则，生成只读计划。
- `business_flow_executor.py（站点绑定入口，未随本包分发）` — 普通问卷首轮最小闭环的 dry-run / 显式执行入口；支持问卷、子账号、用户体系与通讯录候选流程，以及模块隔离、共享子账号、恢复子账号和"标记删除进回收站"语义；支持 `restore` 写操作（通讯录管理员不要求独立查询 action）。当前不接入 `wjx-cli`，候选流程必须人工确认后才可进入正式执行。

## adapters/ — 适配器

- `adapters/base.py` — 适配器基类与契约。
- `adapters/rest.py` — 通用 REST 适配器。
- `adapters/sojump.py（站点适配器，未随本包分发；自装方式见 adapter_contract.md）` — 公共参数、鉴权、写操作门禁（站点专属，可选）。
- `adapters/graphql.py` — HTTP 200 + errors 判失败，反假通过。
- `adapters/grpc.py` — 懒加载 grpcio，缺依赖实例化即报错。

## schemas/ — 机器可读结构约束

`schemas/contract_model.schema.json` · `schemas/test_plan.schema.json` · `schemas/execution_result.schema.json` · `schemas/finding.schema.json` · `schemas/regression_baseline.schema.json` · `schemas/business_flow.schema.json` · `schemas/case-coverage.schema.json`

## assets/ — 配置与实例资产

- **台账列口径**：`finding_columns.json`（**唯一来源**，深度流程与冒烟共用；改列名/增删列改这里，渲染层 `finding_row` 按它逐列取值，缺登记的表头在加载期就点名）。
- **占位模板**：`personal_config.example.json` · `request_headers.example.json` · `intelligent_config.example.json`（`flow.py --config`：`environment` 写门禁 / `execution` 档位与限流 / `inputs` 契约与基线）· `ui_baseline.example.json`（`template: true`，加载器主动跳过，防示范值造假缺陷）· `smoke_config.example.json`。
- **数据清单**：`security_payloads.json`（G 维度安全载荷清单，case_generator 按语义标签选用）· `bug_patterns.json`（缺陷模式池数据文件，冷启动为空、随轮次增长）。
- **排除参数清单（M7 的四级回退数据源）**：`exclude_params.default.json`（出厂兜底，随仓库分发）· `exclude_params.example.json`（仓库只提交这个空白示例）· `exclude_params.json` / `exclude_params_<project>.json`（本地 / 项目级，已 gitignore、不上传）。
- **问卷星业务流程候选（人工确认后才可进入正式执行）**：`business_flow.example.json`（骨架）· `business_flow.ai_homepage.candidate.json（站点业务候选，未随本包分发）` · `business_flow.survey_lifecycle.candidate.json（站点业务候选，未随本包分发）` · `business_flow.user_system.candidate.json（站点业务候选，未随本包分发）` · `business_flow.contacts.candidate.json（站点业务候选，未随本包分发）` · `business_flow.multi_user_management.candidate.json（站点业务候选，未随本包分发）`。
- **问卷星参考实例**：`smoke_config_wjx.json（站点专属配置，未随本包分发；通用配置用 smoke_config.example.json）` · `smoke_cases_wjx.json（站点专属资产，未随本包分发）` · `smoke_hooks_wjx.py（站点专属钩子，未随本包分发）`。
- **说明与模板**：`配置参数说明.md` · `issue_template.csv`。

> **真实凭据存放纪律**：个人真实配置统一放**包外** `~/.workbuddy/wjx-api-test/personal_config.json（真实配置不随包分发，用 personal_config.example.json）`（或 `WJX_CONFIG` 指定路径）；包内只保留 `*.example.json` 占位模板。真实环境实例（含页面基线）同样放包外并用参数显式指定。