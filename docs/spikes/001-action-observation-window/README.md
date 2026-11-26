# Spike 001 · 动作观察窗口（action-observation-window）

> 状态：**VALIDATED**（机制级实证）。
> 归属：补充方案 M7 首批 spike 之一。spike 产物不作为产品验收证据，不更新正式基线。

## 问题（Given/When/Then）

- **Given** 页面完成稳定加载，观察钩子已安装（`window.__vpObs` 跨 evaluate 保持）。
- **When** 安装钩子后执行一次填写和提交（产生 console 异常与网络请求），随后再次采集。
- **Then** 只能得到该动作窗口内的新增事件，且重复采集不会重复计数。

## 方法

`skills/figma-ui-parity/scripts/runtime_observability.js` 增加**增量游标**：
`__vpObs.lastRead` 记录上次 collect 已消费的 `errors`/`requests` 计数，每次 `collect`
返回 `sinceLastRead`（距上次的净新增）与 `cursor`（累计位点），并把 `lastRead` 推进到当前值。
消费方（Agent/台账）只对 `sinceLastRead` 入账——重复采集同一事件不会重复入账。

## 结果（实证）

`skills/figma-ui-parity/scripts/tests/runtime_observability_smoke.cjs` 用桩全局在 Node
真跑（install → 触发 → collect×3），新增 3 条断言全部通过（冒烟总计 **27 通过 / 0 失败**）：

| 断言 | 实测 |
|---|---|
| 首次 collect 报全量增量（errors=1） | PASS |
| 第二次 collect 只报新增 1 条（cursor 累计=2） | PASS |
| 无新增时 sinceLastRead 为 0（重复采集不重复入账） | PASS |

对应的动作级采集记录契约与嵌套裁决字段拦截见
`references/exploration-runtime.md`「动作级观察采集」与
`scripts/phase_gate.py scan_collection_records`（递归扫描 `judgement_key_nested`，
测试 `tests/test_phase_gate.py::CollectionRecordTests` 锁定）。

## 边界

- 桩全局未覆盖**真实浏览器**的多标签页、Service Worker、异步微任务时序；
  真实会话首次使用时按 `browser-connection.md` 通道实测一次动作窗口并留痕。
- `sinceLastRead` 是计数游标，不含事件内容快照；跨会话恢复后 cursor 归零重算
  （`__vpObs` 随页面生命周期存在），长会话应逐动作采集而非跨页累积。
- 游标只保证"不重复入账"，不保证事件顺序与网络请求因果（归裁决阶段判断）。

## 结论

**VALIDATED**（机制级）：增量游标语义成立，动作窗口内只取新增、重复采集不重计。
真实浏览器端到端复核留待首个真实测试会话（不阻塞本机制进入契约）。