# 七步深度流程 — 编排卡

> 本卡只做编排：步骤顺序、命令、门禁触发点、产物与出口。每步完整口径、易漏项与判据见 `references/workflow_steps.md` 对应小节。
> 判路来源：`SKILL.md` §0 行 5。红线编号（R1–R8）定义见 `SKILL.md` §1。

## 0. 前置

1. R1：行为测试前确认文档审查产物有效（查找顺序与 exit 3 条件见 `references/doc_review.md` §4；纯审查回 `SKILL.md` §0 行 1）。
2. 过一遍 `SKILL.md` §2 决策点表（目标环境 / 写操作授权 / 发现档 / AF / 清理授权 / 跨环境对比），逐项取得本次确认。

## 1. 七步表（执行到某一步，才打开对应文件）

| 步 | 动作（一句话） | 必读 | 门禁 |
|---|---|---|---|
| 0 | 登记用户当前指定的那一个环境（BaseURL/路径/encode/appid/sign/主子账号；多域名指向同一后端时合并） | `references/env_config.md` | R3/R7 |
| 0.5 | 从知识库取契约（可选，推荐） | `workflow_steps.md` §0.5 | — |
| 1 | 梳理契约 → `framework.md` §9.1 接口信息表；标清鉴权模型与状态机 | `workflow_steps.md` §1 | — |
| 1.5 | 适用性五步法判断；不支持的功能标 N/A 并列出证明已判断 | `references/applicability_matrix.md` §0–§4 | **硬** |
| 2 | 31 维度覆盖判定 + §4 覆盖率矩阵；优先补 C/E/H/G/B8 盲区 | `references/framework.md` §2/§4 | — |
| 2.5 | 过 21 条缺陷模式（12 Top + 9 补充），命中转用例 | `references/defect_patterns.md` | — |
| 3 | 断言分层四层（拒绝/成功/状态/反向）+ 辅助验证三层次至少第 2 层 | `workflow_steps.md` §3 + `framework.md` §9.4 | **硬**（R6） |
| 4 | 16 列台账、维度编号、现象口吻、公共参数铁律 | `references/issue_format.md` | **硬**（R8） |
| 5 | runner `main()` 编写用例并在指定环境执行 | `workflow_steps.md` §5 | R7 |
| 6 | 复盘收敛 + 数据清理（前缀/工厂/try-finally/孤儿扫描/并发隔离） | `workflow_steps.md` §6 + `framework.md` §11 | **硬**（R2） |

执行命令（R7）：

```bash
WJX_BASE_URL=https://实际地址/实际路径 python scripts/api_test_runner.py
```

地址取自契约『接口地址』；runner 不内置任何默认域名或路径。

## 2. 每步易漏速记（细则以 `workflow_steps.md` 为准）

- 1.5：**AB 不体现在操作类型里**（历史缺陷占比约 8.4%，第二高发）；测 AB 先拿页面基线（人工页面操作 / 浏览器自动化抓取 / 设计稿三选一），无基线的 AB 项记缺口、**不得凭空填预期值**。
- 2：AD 事件回调为选测、不进硬门禁（`applicability.OP_MATRIX`）；AE 六种操作类型必测（默认理由 `applicability.NA_REASON["AE"]`）；国际版维度默认全 N/A；AF 不计入 31 类（M10）。
- 3：5xx 一律记问题（R5）；4xx 语义与业务码一致性见 `framework.md` §9.7；按维度 helper 清单见 `workflow_steps.md` §3。
- 5：环境性异常（连接失败/限流）先归 ENV、重试隔离，不计业务缺陷（R3）。
- 6：复测正文加随机串 + 间隔 ≥0.4s；并发线程各用独立资源**禁止共享同一 ID**；不可删资源记环境残留清单；统一 flow 清理另需 `environment.type=test` + `write_enabled=true` + 显式开关，只删带本轮 `run_id`/`case_id` 所有权的资源。

## 3. 出口

- 复盘报告（用例数 / 通过 / 失败 / 问题数）+ 待确认项逐条收敛（缺陷 / 符合预期 / 已确认非问题 / ENV）。
- 归因口径：`references/attribution_rules.md`；症状定位回 `SKILL.md` §5。