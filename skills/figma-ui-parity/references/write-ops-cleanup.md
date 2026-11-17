# 写操作清理台账

## 为什么需要

`all` 安全策略允许执行真实的写、删、支付、退款动作。SKILL 要求记录「操作前后状态与
是否可恢复」，但**没有回收机制**：跑完一轮，测试数据（问卷、成员、订单）留在目标环境里，
下一轮从脏数据开始，还会让"这条问卷为什么多出来一个"变成无人认领的问题。

`scripts/write_ops_ledger.py` 把「可恢复性」变成可校验、可审计的结构化台账。

## 条目契约

```json
{
  "id": "WO-0001",
  "action": "创建测试问卷",
  "target": "https://目标站/survey/create",
  "safetyLevel": "danger",
  "reversible": true,
  "cleanup": {"method": "api", "target": "DELETE /api/survey/{id}", "evidence": "列表页 0 条"},
  "before": {"surveyCount": 3},
  "after": {"surveyCount": 4},
  "status": "pending"
}
```

校验规则（不满足直接拒收，不写入）：

- `action` / `target` 必填；
- `safetyLevel` 只能是 `caution` / `danger`；
- **`reversible` 必须显式声明布尔值**——缺省当 `false` 会让"其实能删"的数据永久留下，
  缺省当 `true` 会让人以为清理已完成，两种默认都是错的；
- `reversible=true` 必须有 `cleanup.method`（`api` / `ui` / `manual`）与 `cleanup.target`；
- `status` ∈ `pending` / `cleaned` / `failed` / `uncleanable`。

## 命令

```bash
# 登记（动作执行前后各写一条 before/after）
python scripts/write_ops_ledger.py --file ./parity-test/write_ops_ledger.json \
  --add-file ./parity-test/write_op_new.json

# 出清理计划（markdown，可照着执行）
python scripts/write_ops_ledger.py --file ... --plan --out ./parity-test/write-ops-plan.md

# 回填结果
python scripts/write_ops_ledger.py --file ... --mark-clean WO-0001 --evidence "问卷已删除，列表 0 条"
python scripts/write_ops_ledger.py --file ... --mark-failed WO-0002 --evidence "无删除接口，需后台操作"

# 审计（存在未清理的可回收项时非零退出，用于交付前门禁）
python scripts/write_ops_ledger.py --file ... --audit --enforce

# 并入报告「待确认」（幂等，按 _ledgerId 去重）
python scripts/write_ops_ledger.py --file ... --merge-pending --report ./parity-test/report.json
```

## 与报告的关系

未清理项并入 `report.pendingChecks`，于是：

- 报告「待确认与数据缺失」表里出现一行（维度列显示"写操作清理"）；
- 7.12 维度会统计到它（不会出现"表里有残留、7.12 说无待确认项"的自相矛盾）。

管线顺序是 **merge-pending → assemble_report**。反过来 7.12 统计不到，报告会显示
"无待确认项"，而环境里还留着上一轮造的数据。

## 边界（不要在这里做假动作）

1. **本脚本不执行清理**。逆向操作用哪条接口、哪个页面只有写操作的人知道；自动猜一个
   "删除接口"去调用，比不清理更危险（可能删错对象）。它负责登记、校验、出计划、审计、上报。
2. 不可恢复项（`reversible=false`）不拦门禁，但必须在报告里单列，由人决定是否接受。
3. 清理失败要 `--mark-failed` 并写证据，不要静默跳过——静默跳过等于伪造"环境干净"。
4. 台账文件与 `parity-test/.auth` 同级，属敏感运行数据：**不要提交到 git**，
   条目里不得写 token / Cookie 原文（`target` 只写路径与主键）。
