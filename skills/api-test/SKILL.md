---
name: api-test
description: 接口测试工作流（砺衡包内版，剥离自 wjx-api-test 5.0.2）。判路→契约审查→契约驱动生成→七步深度→冒烟→问题单 16 列；执行适配 REST/GraphQL/gRPC（站点适配器未随包，自装可用）。当任务涉及契约审查、契约驱动生成、批量冒烟、接口回归、依赖梳理、按维度设计接口用例时使用。**不含需求级 UI 验收用例生成**（需求级 → 砺衡 case-generator）；cases 中 `layer=api` 的 TC 由本链路执行，结果回填主体台账。检出维度全景见专家包 `references/bug-detection-map.md`。
---

# api-test — 接口测试工作流（砺衡包内版）

> 剥离自 **wjx-api-test 5.0.2**（2026-11-13 冻结），与本专家运行时完全独立，源 skill 零改动。
> 未随本包分发：站点适配器（`adapters/sojump.py`）、站点绑定入口（`business_flow_executor.py`）、站点专属资产（`smoke_*_wjx`、`business_flow.*.candidate`）、真实个人配置——接入方式见 `references/generality_boundary.md` 与 `references/adapter_contract.md`。

## 0. 与砺衡专家的分工（两套质量体系边界，不得混用）

- 本 skill 自带质量体系：**M2 防呆门禁 / M3 产物回读 / M11 语义层 / M13 依赖双通道**（§5 机制锚点）。
- **接口专项按本 skill 自身门禁判定，不套用砺衡的 S0/G/V/COV/COV-PATH 门禁**；砺衡门禁只作用于砺衡的 cases JSON 与执行台账。
- 产物（plan / cases / manifest / 16 列问题单）自成体系，**不与砺衡 cases JSON、执行台账合并**；在砺衡报告中按附件引用，不写转换器。
- 砺衡验收任务中接口侧的“供数 / AB 口径 / 失败归属”三职责仍按砺衡既有契约执行，与本 skill 无关。

## 1. 冷启动（首次使用必读）

- **R7**：测试地址只经 `WJX_BASE_URL` 传入，未设置启动即报错。
- **首次配置**：复制 `assets/personal_config.example.json` 为包外本地配置（默认路径 `~/.workbuddy/wjx-api-test/personal_config.json`，或用 `WJX_CONFIG` 指定），填入 `base_url`（只写域名/根）等；真实配置永不入包。
- **零配置可跑**：文档审查（不发请求）与契约驱动生成（`--plan-only` / `--dry-run`）；冒烟与七步执行需配置 + 真实环境 + R2 授权。
- **依赖**：Python ≥ 3.9 标准库；`pyyaml`（YAML/OpenAPI 契约）与 `grpcio`（gRPC）按需安装（requirements.txt）。
- **站点适配器**：sojump 未随包；`--adapter rest` 为默认；自装方式见 `references/adapter_contract.md`。
- **知识库契约**：判路第 3 行的契约由会话从外部知识库取回后经 `--contract`/`--kb` 喂入，本包不内置知识库路径。

## 2. 判路（先判路，再动手）

| # | 输入形态 | 动作/意图信号 | 路径 | 第一步（只读一个文件） |
|---|---|---|---|---|
| 1 | 契约文件 / 粘贴正文 / 文档 URL | 审、检查、合理吗、有没有问题 | 纯文档审查（**不发请求**） | `references/doc_review.md` |
| 2 | 同上 | 测、跑、验证、尽量多发现问题 | 契约驱动 | `references/playbooks/contract.md` |
| 3 | 只给 action / 接口名 | 同上 | 契约驱动（契约由会话从知识库取回喂入；**未配置 KB 时 `--action` 会以校验错误 exit 2，属预期**——先取契约或显式传 `--kb`/`KB_PATH`） | `references/playbooks/contract.md` |
| 4 | 接口清单 / 批量配置 | 全跑一遍、哪些通、通不通 | 冒烟 | `references/playbooks/smoke.md` |
| 5 | 单接口 + 深挖信号 | 按维度设计用例、深挖 | 七步深度 | `references/playbooks/seven_step.md` |
| 6 | 基线 JSON / manifest | 回归、复测、多接口批次 | 契约驱动（`--retest` / `multi_interface`） | `references/playbooks/contract.md` |
| 7 | 契约文件 / 知识库契约 | 依赖梳理、前置条件未满足 | 契约驱动（M13，`dependency_scan.py` / `precondition_scan.py`） | `references/playbooks/contract.md` |
| 8 | 无法判定 | — | 兜底：问清输入与目标，禁止猜 | — |

层级说明：四条路径**非平级**——文档审查既是独立交付模式（行 1），也是契约驱动/七步的**前置门禁**（R1）。**两条最常见判错**：①「审文档」≠「执行测试」，判错会把静态审查变成真实调用；②「一批跑一遍」走冒烟（广度优先），不是逐接口七步（深度优先）。

## 3. 红线 R1–R8（不可让步，全文唯一出处）

- **R1** 行为测试前契约必须先过文档审查；`flow.py` 按 `--doc-review` → 契约同目录 → 当前目录查找，未找到 / 产物早于契约 → **exit 3**（唯一出处 `doc_review.md` §4）。
- **R2** 写操作、数据清理、发现档正式化，逐次取得用户**本次**授权后才执行。
- **R3** 只测用户当前指定的那一个环境；环境不存在即报错 exit 2，不回退。
- **R4** 覆盖率只认 `PASS` / `FAIL` / `PRECONDITION_UNMET` 为已执行；`SKIPPED` / `N/A` 不算，缺口记 `COV-*` 两级进台账。
- **R5** 服务端 5xx 一律记问题，即使业务“正常”。
- **R6** 拒绝类用例必须命中文档化 `err_exact`：未配 → 「待确认(高危)」不判 PASS；配而未中 → FAIL。
- **R7** 测试地址取自契约『接口地址』、只经 `WJX_BASE_URL` 传入，禁止写死域名；未设置启动即报错。
- **R8** 台账请求 Body 必含**项目信封声明的全部公共参数**（当前实现为问卷星口径 `encode`/`appid`/`action`/`sign`/`ts`，见 `scripts/common_params.py`；其它项目接入需按 `envelope_keys` 口径扩展）。

## 4. 执行前决策点矩阵（每次必问，不得沿用上次运行或历史对话的选择）

| 决策点 | 何时必问 | 不问的后果 |
|---|---|---|
| 目标环境 | 每次开始前 | 误测生产环境 |
| 写操作授权 | 用例涉及创建/修改/删除/状态变更 | 污染数据；删除不可逆（`smoke_runner.py` 无默认保护） |
| 发现档 discovery | 说「尽量多发现问题」但未明说放宽时 | 用例与写请求倍增，压被测服务 |
| AF 业务场景 | 提供 `af_cases` 前（无真实场景则注明原因不参与） | 每条都可能真实发请求 |
| 数据清理授权 | 执行 `teardown` / 清理前 | 误删非本次创建的资源 |
| 跨环境对比 | 仅用户明确要求时（非默认） | 环境差异误判为接口缺陷 |

注记：运行实证依赖候选**只有 `--confirm` 转为 `confirmed` 才生效**——判错等于静默吞掉真缺陷；ENV 台账仅用户明确要求时做，非交付门槛。

## 5. 陷阱与机制锚点（指针）

- **18 条陷阱速查**：`references/traps.md`（编号稳定，正文多处引用）。
- **M1–M13 机制锚点**（一句话作用/口径/边界/故障码）：`references/mechanisms.md`——契约解析自检、防呆五道、回读校验、生成链、用例分层、判重、排除参数、M8 方法格式位置、三类对账、AF 业务场景、维度适用性（默认全开 32 维）、语义层接入（默认严格，exit 4/5）、资产归档与正式化、计划审阅与依赖双通道。
- 硬口径：门禁默认只告警留痕，`WJX_STRICT_COVERAGE / WJX_STRICT_CHECKKEY / WJX_STRICT_READBACK / WJX_STRICT_CONSISTENCY` 才升级 exit(2)；**没有可回读的产物 ≠ 通过**。

## 6. 症状路由（遇到 X 去哪）

| 症状 | 去哪 |
|---|---|
| exit 2 / 3 及全部退出码（1–5）语义 | `references/quick_ref.md` §〇；exit 3 详见 R1；exit 2 详见 mechanisms.md M2 |
| 某维度 0 条用例 / `COV-*` | M4（生成）+ M2（覆盖率门禁） |
| 报告出现待确认 / 环境风险条目 | M9 |
| 判重 / 矛盾不合直觉 | M6 |
| 前置未满足被吞 / 依赖没识别 | M13 |
| AF 门禁不放行 | M10 |
| 语义三态看不懂 | M11 |
| 台账列错位 / CSV 乱码 / finding 丢失 | 陷阱 7 + `scripts/artifact_readback.py`（M3） |
| FAIL 疑似误报（限流/防重） | 陷阱 6/8 + `references/attribution_rules.md` |

## 7. 边界

- **通用性**：不绑定具体接口；站点适配器与专属资产未随包（接入见 `generality_boundary.md`）；域名族判定中的问卷星域名仅为 `personal_config.py` 的示例规则。
- **UI 边界**：视觉走查不在本 skill 内；接口侧三职责（供数 §11.8、AB 口径 `ui_baseline.md`、失败归属）。
- **个人配置**：全部外置，包内只有 `*.example.json`；细则见 `references/personal_config.md`。
- **契约格式**：ShowDoc `.md`、OpenAPI 3.x / Swagger 2.0、极简 JSON 契约——按内容自动分派。

## 8. 资源索引

- **编排卡**（路径入口命令唯一出处）：`references/playbooks/contract.md` · `seven_step.md` · `smoke.md`
- **方法论**：`framework.md`（31 维度 + 断言矩阵）· `workflow_steps.md` · `mechanisms.md` · `quick_ref.md`（全部命令/开关/状态词表）· `issue_format.md`（16 列唯一来源）· `traps.md` · `doc_review.md` · `case_schema.md` · `applicability_matrix.md` · `defect_patterns.md` · `attribution_rules.md` · `case-coverage.md` · `auto_generation.md` · `ui_baseline.md` · `env_config.md` · `personal_config.md` · `intelligence_policy.md` · `smoke_testing.md` · `real_smoke_delivery.md` · `business_flow.md` · `adapter_contract.md` · `generality_boundary.md` · `pattern_candidates.md` · `asset_inventory.md` · `case-coverage.md`
- **执行资产**：入口 `scripts/flow.py` / `api_test_runner.py` / `smoke_runner.py` / `case_runner.py`；适配器 `adapters/`；逐文件职责见 `references/asset_inventory.md`