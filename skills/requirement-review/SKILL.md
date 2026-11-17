---
name: requirement-review
description: 砺衡专家包内 skill：需求评审。收到需求文档/正文、尚未拆规格时调用；按 PRD 八维度做定性评审（不打分不评级），拆解需求/功能点/验收标准并产出 review-result.json（唯一事实源）与人读摘要 review-summary.md，供 case-generator 消费。
---

# requirement-review（需求评审）

> 砺衡（web-acceptance-assurance）包内 skill。与 qa-req2testcase-generator **运行时完全独立**：本目录内容为一次性抽取副本，运行时不读取 qa 的任何路径、版本或配置。
> 评审判据：`references/prd-dimensions.md`（8 维度定稿版，不含分数）。

## 职责边界

| 项 | 约定 |
|---|---|
| 何时调用 | 收到需求文档/正文（Word 读出文本 / 粘贴文本 / TAPD 导出文本），尚未拆规格 |
| 输入 | 需求正文 |
| 输出 | `review-result.json`（唯一事实源）+ `review-summary.md`（`render_review.py` 渲染的派生视图） |
| 不适用 | 不评产品价值、不润色、**不打分不评级**；材料不足的维度记 `not_reviewed` 并写明缺什么 |
| 写入 | 只写 `review-result.json`；`review-summary.md` 一律由脚本渲染，**不手工写、不口头补充** |

## 流程

1. **读需求正文**。Word 内的原型截图、批注、修订痕迹可能读不到，关键规则须文字化；TAPD 连接器已授权时可按任务定向读取**指定需求**，未授权或读取失败则要求导出为文本；任何情况下不做全空间拉取；PDF 需求先转文本。
2. **按 `references/prd-dimensions.md` 八维度定性评审**：问题按 blocking / pending 归档；材料不足记 `not_reviewed`。历史一致维度无对照材料时不评，不得凭常识补全。
3. **拆解实体并分配 ID**（ID 规则最小集如下，与 `case-generator` 的判据同源；case-generator 缺失时本清单自足）：
   - 需求 `^REQ-\d{2,}$`（REQ-01）、功能点 `^FP-\d{2,}$`（FP-01）、验收标准 `^AC-\d{2,}$`（AC-01）、已确认规则 `^R-\d{2,}$`（R-01）、阻塞项 `^B-\d{2,}$`（B-01）、待确认项 `^P-\d{2,}$`（P-01）；
   - 模块码 `^M\d{2}$`（M01）**由本阶段分配，一经确定不得重排**——需求变更导致模块增删时按新增追加，不重编号（否则用例编号集体失效）。
   - 每个功能点归属唯一模块；`modules[].estimated_case_count` 给粗估（唯一用途是 case-generator 判「要不要分批」，不要求精确）。
   - 每条 AC 必须带 `elements` 四要素（scene/object/condition/focus，具体到可观测对象，禁空话与占位）与 `oracle`（可判定的观测入口或通过标准）。
   - **每个 requirement 至少有一个 `required: true` 的 AC**——这是 `case_data.validate` 的硬校验，缺失则下游必然失败。
4. **写 `review-result.json`**（字段契约：`references/review-result.schema.json`；`requirements[].confirmation` 只允许 `confirmed/provisional/ambiguous`）。
5. **校验**：`python scripts/review_check.py --input <review-result.json 绝对路径>`。errors 非空 → 修订后重跑；本脚本**只报不改**，不自动修订评审结论。
6. **渲染摘要**：`python scripts/render_review.py --input <review-result.json 绝对路径> --out <review-summary.md 路径>`。摘要章节固定：需求信息 → 阻塞项（按 feature_id 分组）→ 待确认项 → 可直接推进 → 覆盖缺口 → 未评审维度；**摘要中不得出现 JSON 里没有的条目或结论**。用户在会话里看到的文字可以是摘要的复述，但归档与转发的交付物是这份文件。
7. **交接**：把 `review-result.json` 的**绝对路径**交给 case-generator（`review_bridge.py` 按此读）；不共享代码、不共享运行时。

## blocking 与 go_ahead 的关系

- `blocking[]` 涉及的 feature **不得**列入 `go_ahead`（相关用例不生成）。
- `pending[]` 涉及的 feature 可列入 `go_ahead`，但生成阶段会把相关用例标 `待确认`，不阻塞其余部分。
- 仅缺失规则的相关部分等待澄清，其余明确部分继续推进。

## 工具不可用时

会话没有脚本执行或文件写入能力时，按砺衡既有口径**报告阻塞、保留待执行项**，不以手工编造数据代替脚本输出。

## 命令

```bash
# 评审判定（只报不改；退出码 0=通过 1=校验失败 2=用法/输入/依赖错误）
python scripts/review_check.py --input <绝对路径>/review-result.json
python scripts/review_check.py --input <绝对路径>/review-result.json --json --strict

# 摘要渲染（纯派生，不新增判定）
python scripts/render_review.py --input <绝对路径>/review-result.json --out <绝对路径>/review-summary.md
```

脚本按相对本 skill 目录的 `../../scripts/case_data.py` 加载词表（空话/占位判定与执行期校验器同源）；该文件缺失时报工具阻塞退出码 2，不降级为无词表模式。