# PRD 评审八维度（定稿版，不含分数）

> 本 skill 的评审判据源。抽取自 qa-req2testcase-generator 4.17.0 的 P0.5 PRD 质量审查（该源以百分制计分，本包**只取维度清单与阻塞判定，不取分数与等级**）。`config/policy.json` 的 `prd_review.evaluation = qualitative_only`，`score_allowed = false`，`grade_allowed = false`。
> 输出落点：问题写进 `review-result.json` 的 `blocking[] / pending[]`，材料不足写 `not_reviewed[]`。不评产品价值、不润色、不打分不评级。

## 维度总表（8 维，与源表格实列一致）

| # | 维度 id | 名称 | 检查项 | 缺材料时 |
|---|---|---|---|---|
| 1 | completeness | 完整性 | 触发条件与前置条件；字段类型/格式/取值范围；业务规则适用条件；异常场景处理方式；数据口径来源与单位 | 逐项记录缺口 |
| 2 | consistency | 一致性 | 同一概念跨位置描述一致；术语统一；页面路径与菜单层级一致；端差异（PC/移动）描述完整 | 逐项记录缺口 |
| 3 | clarity | 清晰度 | 规则可唯一理解；数值有明确单位；状态条件有明确边界；默认值已定义 | 逐项记录缺口 |
| 4 | testability | 可测试性 | 每个功能点可设计确定步骤；预期结果可观测；判定条件不含主观词 | 逐项记录缺口 |
| 5 | performance_security | 性能与安全 | 性能基线是否声明；安全约束是否声明 | 逐项记录缺口 |
| 6 | state_coverage | 状态覆盖 | 空态、加载态、异常态、边界态 | 逐项记录缺口 |
| 7 | acceptance_mapping | 验收映射 | 验收标准与功能点一一对应；验收标准可判定 | 逐项记录缺口 |
| 8 | historical_consistency | 历史一致 | 与已上线行为是否矛盾；与历史需求是否矛盾 | **无对照材料 → 记 `not_reviewed`，不得凭常识补全历史行为** |

## 各维度审查细则

**完整性**：所有操作是否有明确触发条件与前置条件；所有字段是否有类型、格式、取值范围；业务规则是否带完整适用条件；异常场景是否有处理方式；数据口径（来源、单位、计算方式）是否可追溯。
**一致性**：同一概念在不同位置描述是否一致（如文件格式）；术语是否统一（"客户/用户"是否同一角色）；页面路径与菜单层级是否一致；PC/Mobile 端差异是否完整。
**清晰度**：规则是否可被唯一理解（无多种解读）；数值是否有单位（金额、时间格式）；状态条件边界是否具体（"大数据量"具体是多少）；默认值是否定义。
**可测试性**：每个功能点能否设计确定步骤；期望结果能否明确验证；测试数据能否构造；环境依赖能否满足。
**性能与安全**：性能指标是否有量化基线（响应时间/并发数/数据量）；安全约束是否定义（权限/加密/审计）；文件上传是否有大小/格式限制；异常场景是否有明确错误提示。
**状态覆盖**：空态（无数据时展示什么）；加载态（异步等待展示与超时表现）；异常态（依赖失败、网络异常的降级与提示）；边界态（满额、极值、达到上限）。异步功能须覆盖"进行中/成功/失败"三种时态。
**验收映射**：每条验收标准是否至少被一个功能点支撑；每个功能点是否至少被一条验收标准覆盖；只基于已结构化的验收标准，不自行发明新验收项。
**历史一致（条件维度）**：仅基于明确标注为项目知识库/已上线需求/帮助材料的对照内容；引用必须注明出处；无对照材料时该维度记 `not_reviewed` 并写明缺什么；不得凭记忆编造历史行为。

## 判定映射（三级，不带分数）

| 判定 | 定义 | 落点 |
|---|---|---|
| blocking | 无法设计测试用例，或设计出的用例无法确定期望结果 | `blocking[]`（issue_id/feature_id/question/impact） |
| pending | 可以设计用例但质量可能不高，存在遗漏风险 | `pending[]`（issue_id/feature_id/question/assumption） |
| not_reviewed | 材料不足，无法评审 | `not_reviewed[]`（dimension/reason） |

blocking 判定口径（从严）：关键状态（空态/异常态）完全未定义且无法推断 → blocking；仅部分缺失 → pending。验收标准完全无功能点支撑且无法推断 → blocking。历史矛盾默认 pending（可能是有意变更）；仅当 PRD 明确声称"与现有行为一致"但实际矛盾时 → blocking。

## 与生成阶段的交接

- `blocking[]` 涉及的 feature **不进入** `go_ahead`，相关用例不生成。
- `pending[]` 涉及的 feature 可进入 `go_ahead`，但相关用例生成后标 `待确认`（`coverage_flags` 或 `remarks`），不阻塞其余部分。
- `requirements[].confirmation` 只允许 `confirmed / provisional / ambiguous`（`case_data` 枚举）；`provisional/ambiguous` 的 requirement 相关断言按待确认处理，不得写成确定断言。
- 每个被拆出的 requirement **必须至少有一个 `required: true` 的 acceptance**，且 AC 带 `elements` 四要素与 `oracle`——否则 `case_data.validate` 直接失败。- **证据溯源（OM3，借鉴 qa evidence_refs）**：`acceptance[]` 可选 `evidence_refs: [{source, quote}]`——source 写需求文档定位（章节/表格/条款），quote 摘录原文片段；`blocking[]`/`pending[]` 的 question/impact 须指到原文位置（章节或原句），禁止仅凭记忆虚构规则。原文缺失时按 `not_reviewed` 记，不补写。