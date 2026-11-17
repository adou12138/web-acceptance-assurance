# 交付 JSON 视图结构（output-json.md）

> 定位：`render_json.py` 从 cases JSON 渲染的**派生视图**，给程序或下游会话消费。唯一事实源始终是 `cases-<short>.json`；本视图只读，**不得回写**，手工修改后不得回流。

## 顶层结构

| 字段 | 内容 |
|---|---|
| `schema_version` | 视图契约版本，当前 `"1.0"` |
| `view` | 固定 `"delivery-json"` |
| `note` | 只读投影声明 |
| `generated_at` | 渲染时间（带时区 ISO8601） |
| `source` | `{requirement_id, title, flow_kind}`，取自 cases `meta` |
| `summary` | 计数：`requirements / features / feature_points / acceptance / scenarios / testcases / smoke / p0` |
| `testcases` | 用例投影数组：**浅拷贝每条用例的全部字段**（16 个必填 + 扩展字段），断言三键 `{check, oracle, expect}` 不丢、不折叠 |
| `trace` | `{requirements, features, acceptance}`；cases 有 `scenarios` 时附带 |

## 下游消费契约

1. 用例识别键：`testcases[].id`（`^TC-[A-Z0-9]{2,8}-\d{3}$`，跨批全局唯一）。
2. 追溯链：`testcases[].feature_ids → trace.features[].children[].id`；`testcases[].acceptance_ids → trace.acceptance[].id → trace.acceptance[].requirement_id → trace.requirements[].id`。
3. 断言消费：每条用例 `assertions[]` 是拆分后的多断言（Excel 视图是其折叠合并）；下游需要"期望结果"时自行按 `expect` 拼接，不要把折叠文本当拆分源。
4. 一致性自检：渲染器已保证 `view.testcases[].id` 集合与源 JSON 完全相等（不等即渲染失败退出码 1）；下游解析时发现条数不符应拒收，不要猜。
5. 冒烟判定：`suites` 含 `"smoke"` 即冒烟用例；`smoke_source` 指向 A 类来源（G4 门禁项）。
6. 执行事实不在本视图：执行结果、截图在执行台账 JSONL；本视图 `remarks` 不承载门禁结论或未覆盖义务。