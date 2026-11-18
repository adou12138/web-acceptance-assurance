# legacy-schemas（未实现的抽取残留，仅供参考）

这里的 16 份 JSON Schema 来自 qa-req2testcase-generator 4.17.0 的 `prompts/schemas/`，是一次性抽取残留：**本包没有任何脚本或流程消费它们**，也不存在对应的 P0–P6 分步产物。

保留用途只有一个：查 qa 侧字段形态时对照。**不代表本包具备以下能力**：

| 文件 | 对应 qa 能力 | 本包现状 |
|---|---|---|
| `px_output.schema.json` | 图片理解（分类 / 价值分级 / OCR / 视觉推断） | **未实现**。本包的原型图通路见 `skills/requirement-review` 的图片处理步骤（轻量解包 + 多模态直读），不产出该 schema。 |
| `p0_input/p0_output/p0.5_output` | 需求结构化与 PRD 质量评审 | 由 `skills/requirement-review` 的 `review-result.json` 承担（字段契约见该 skill 的 `references/review-result.schema.json`） |
| `p1_*` / `p2_*` / `p3_*` / `p4_*` / `p5_*` | 骨架、测试点、风险、PCI、测试点合并 | 本包不生成测试点层，见 `config/policy.json` 的 `gates.not_migrated` |
| `p6_input/p6_output` | 用例生成产物（含 HMAC 结构与统计） | 由 `cases-<short>.json`（`scripts/case_data.py` schema 1.0）承担 |

禁止事项：
- 不得把这里的 schema 当作"已支持"对外声明（尤其 `px_output`）。
- 不得把新脚本的产物往这些 schema 上套，避免出现第二套字段契约。

如需恢复其中某项能力，走 `docs/SYNC-NOTES.md` 的对照流程，在 `scripts/` 与 `skills/` 内实现后再迁出本目录。
