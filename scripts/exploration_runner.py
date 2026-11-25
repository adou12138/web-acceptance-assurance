#!/usr/bin/env python3
"""批次编排器（D2）：只编排，不执行。

不自动浏览、不点击、不代判 PASS/FAIL、不生成缺陷、不重放未知写入；浏览器动作
仍由 Agent 在已选通道做。输入 exploration.json + runtime-capabilities.json
（+ runtime-manifest.json），输出 batch-plan.json（schema: batch-plan/1）与
预算账本 budget-consumption.json（schema: budget-consumption/1）。

- ready_queue 全部来自 exploration_state.summarize()["ready"]（唯一事实源），
  **不另建排序**；capabilities 页面交互通道均 missing 时整批转
  blocked_with_recovery（保留 recovery 原文，不借此免压档）。
- 自动续批（X-02）走 amendment_class.classify() + is_blocking() 双层凭据校验，
  仅 readonly_lead 且凭据合格且未超预算才 auto；**不触碰 checkpoint/resume
  的 approval**（人工放行不变）。
- writeback_commands[] 是**发射**给 Agent 逐条执行的命令模板（P3 显式口径）：
  脚本不自行调用 execution_ledger、不代写证据与结论。
- honest_exits（X-13）只登记「条目上已填齐 acknowledged_unexplored / falsify_action
  的条目 ID + 未填清单」——两字段由 Agent 填，脚本不代写。

用法：
    python scripts/exploration_runner.py --exploration <exploration.json> \
        --capabilities <runtime-capabilities.json> [--runtime-manifest <m.json>] \
        --out <batch-plan.json> [--budget-ledger <budget-consumption.json>] \
        [--declaration <只读声明.json>] [--batch-size N]
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import amendment_class as _AMD
import exploration_state as _EXP

SCHEMA = "batch-plan/1"
BUDGET_SCHEMA = "budget-consumption/1"
PAGE_CHANNELS = ("browser_selfhost", "browser_mcp")
DEFAULT_BATCH_SIZE = 10


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _load(path):
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def within_budget(manifest, consumed):
    """三口径（探针数/时长/写入次数）任一达上限即 False；无预算声明不拦截。"""
    budget = ((manifest or {}).get("read_only_budget") or {})
    consumed = consumed or {}
    for key, limit in budget.items():
        if isinstance(limit, (int, float)) and (consumed.get(key) or 0) >= limit:
            return False
    return True


def auto_continue(declaration, manifest=None, consumed=None):
    """X-02 自动续批判定（phase_gate 与台账共用 amendment_class 单一事实源）。

    构造 declaration（scope/environment/role/allowed_actions/no_state_change 五项）
    → classify → 仅当 kind == "readonly_lead" 且 reasons == [] 且
    not is_blocking(record) 且未超 read_only_budget → 写 auto_approval
    {auto: true, basis: "readonly batch within declared budget"}；
    其余一律 requires_human: true，classify reasons 原文附在输出里。
    凭据类型不合格（no_state_change 写成字符串 "false"、allowed_actions 空数组、
    scope 空白）在 classify 内归 scope_change，落到 requires_human 分支。
    """
    declaration = declaration or {}
    kind, reasons = _AMD.classify(declaration)
    # record 用台账式形状（kind + 内嵌 declaration）：is_blocking 对 readonly_lead
    # 会复验 declaration 凭据（第二层），kind 字段被改写也拦得住（amendment_class 133-137）。
    record = {"kind": kind, "status": None, "declaration": declaration}
    budget_ok = within_budget(manifest, consumed)
    if (kind == "readonly_lead" and not reasons
            and not _AMD.is_blocking(record) and budget_ok):
        return {"auto": True, "requires_human": False,
                "basis": "readonly batch within declared budget",
                "kind": kind, "classify_reasons": [], "over_budget": False}
    return {"auto": False, "requires_human": True, "basis": None,
            "kind": kind, "classify_reasons": list(reasons or []),
            "over_budget": not budget_ok}


def _writeback_commands(ready_ids):
    """发射给 Agent 的回写命令模板（P3 显式口径：脚本不自行执行、不代写证据）。
    占位符由 Agent 逐条填实；证据仍逐例记 BEGIN/FINISH（agents 责任分层）。"""
    commands = []
    for ident in ready_ids:
        commands.append(
            f"python scripts/execution_ledger.py begin <workspace> --case {ident} "
            "--session <当前会话>   # Agent 执行探针前落 BEGIN")
        commands.append(
            f"python scripts/execution_ledger.py finish <workspace> --case {ident} "
            "--status <PASS|FAIL|BLOCKED|NOT_RUN|SKIPPED> --evidence <证据路径> "
            "--session <当前会话>   # Agent 落 FINISH 与证据，不代写结论")
    return commands


def _honest_exits(items):
    """X-13：只登记已填齐/未填清单的条目 ID；字段由 Agent 填，脚本不代写。
    acknowledged_unexplored 须含 next_action+owner（ITEM_OPTIONAL 校验同口径）。"""
    filled, unfilled = [], []
    for row in items or []:
        if row.get("status") != "NOT_RUN":
            continue
        ack = row.get("acknowledged_unexplored")
        ack_ok = (isinstance(ack, dict)
                  and str(ack.get("next_action") or "").strip()
                  and str(ack.get("owner") or "").strip())
        falsify_ok = bool(str(row.get("falsify_action") or "").strip())
        (filled if (ack_ok and falsify_ok) else unfilled).append(row["id"])
    return {"filled_ids": filled, "unfilled_ids": unfilled,
            "note": "条目级可选字段（ITEM_OPTIONAL）；未填清单不是压档依据也不是免压档依据"}


def build_batch_plan(report, capabilities, manifest=None, batch_size=None):
    """从 summarize() 报告 + capabilities 构造 batch-plan（只编排，不执行）。"""
    channels = (capabilities or {}).get("channels") or {}
    page_ok = any((channels.get(c) or {}).get("state") == "available"
                  for c in PAGE_CHANNELS)
    ready, blocked = [], []
    for ident in report.get("ready") or []:
        if page_ok:
            ready.append(ident)
        else:
            recovery = "；".join(x for x in (
                (channels.get("browser_selfhost") or {}).get("recovery"),
                (channels.get("browser_mcp") or {}).get("recovery")) if x)
            blocked.append({
                "id": ident,
                "reason": "页面交互通道均不可用（capabilities missing/unprobed→missing）",
                "recovery": recovery})
    size = max(1, int(batch_size or DEFAULT_BATCH_SIZE))
    batches = [ready[i:i + size] for i in range(0, len(ready), size)]
    budget = ((manifest or {}).get("read_only_budget") or None)
    stop_conditions = [
        {"code": "budget_exhausted",
         "trigger": "探针数/时长/写入次数任一达 read_only_budget 上限",
         "on_stop": "停机记录写「已消耗量 + 剩余未执行 ADV 义务 + 影响」，不改判已执行探针结论"},
        {"code": "quota_triggered",
         "trigger": "attack_coverage / exploration 门禁配额触发",
         "on_stop": "同上；配额触发不等于失败，如实登记"},
        {"code": "side_effect_pending",
         "trigger": "needs_reconcile 非空（写操作状态未知）",
         "on_stop": "先核副作用再决定是否继续；不因排序自动重放"},
        {"code": "capability_missing",
         "trigger": "页面交互通道均 missing",
         "on_stop": "受影响项记 BLOCKED 并保留 recovery 原文；HTTP 旁证不冒充页面证据"},
    ]
    return {
        "schema": SCHEMA,
        "built_at": _now(),
        "ready_queue": ready,
        "blocked_with_recovery": blocked,
        "batch_size": size,
        "batches": batches,
        "budget": budget,
        "stop_conditions": stop_conditions,
        "reconcile_items": {
            "needs_reconcile": list(report.get("needs_reconcile") or []),
            "reproduction_candidates": list(report.get("reproduction_candidates") or []),
            "note": "写操作状态未知先核副作用；FAIL 复现候选按 finding-triage 处理，不自动重放"},
        "writeback_commands": _writeback_commands(ready),
        "honest_exits": _honest_exits(report.get("items") or []),
        "source": {
            "ready_from": "exploration_state.summarize()[\"ready\"]（唯一事实源，不另建排序）",
            "channel_gate": "页面交互类条目由 browser_selfhost/browser_mcp 门控；"
                            "清单条目无通道字段，接口类条目的门控由 Agent 按 blocked_reason 承担",
            "discipline": "只编排不执行：不点击、不代判、不重放未知写入"},
    }


def new_budget_ledger(manifest):
    """预算账本（budget-consumption/1）：三口径累计 + auto_approval 事件
    （E2 自主性对照的 auto_approval_ratio 指标数据源）。"""
    return {"schema": BUDGET_SCHEMA, "created_at": _now(),
            "declared_budget": (manifest or {}).get("read_only_budget"),
            "batches": [], "auto_approval": [],
            "probes_consumed": 0, "minutes_consumed": 0, "writes_consumed": 0}


def record_batch(ledger, batch_id, probes=0, minutes=0, writes=0, auto=None):
    """登记一个批次的消耗；auto 为 auto_continue 的输出（None = 未走自动续批判定）。"""
    ledger["batches"].append({"batch_id": batch_id, "recorded_at": _now(),
                              "probes": probes, "minutes": minutes, "writes": writes})
    ledger["probes_consumed"] += probes
    ledger["minutes_consumed"] += minutes
    ledger["writes_consumed"] += writes
    if auto:
        ledger["auto_approval"].append({"batch_id": batch_id, "recorded_at": _now(),
                                        "auto": True, "basis": auto.get("basis"),
                                        "classify_reasons": auto.get("classify_reasons")})
    return ledger


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="批次编排器（只编排不执行）：ready 队列/预算/停止条件/回写命令模板")
    parser.add_argument("--exploration", required=True, help="探索清单 JSON（summarize 唯一事实源）")
    parser.add_argument("--capabilities", required=True,
                        help="runtime-capabilities.json（D1 输出）")
    parser.add_argument("--runtime-manifest", dest="runtime_manifest",
                        help="runtime-manifest.json（预算声明；缺省不拦截预算）")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--declaration", help="只读声明 JSON（触发 X-02 自动续批判定）")
    parser.add_argument("--budget-ledger", help="budget-consumption.json 路径（新建或追加）")
    parser.add_argument("--out", required=True, help="batch-plan.json 输出路径")
    args = parser.parse_args(argv)

    data = json.loads(Path(args.exploration).read_text(encoding="utf-8-sig"))
    report = _EXP.summarize(data)
    capabilities = _load(args.capabilities) or {}
    manifest = _load(args.runtime_manifest)
    plan = build_batch_plan(report, capabilities, manifest, args.batch_size)

    auto = None
    ledger = None
    if args.budget_ledger:
        ledger_path = Path(args.budget_ledger)
        if ledger_path.is_file():
            ledger = json.loads(ledger_path.read_text(encoding="utf-8-sig"))
        else:
            ledger = new_budget_ledger(manifest)
        if args.declaration:
            consumed = {k: ledger[k] for k in
                        ("probes_consumed", "minutes_consumed", "writes_consumed")}
            auto = auto_continue(_load(args.declaration), manifest, consumed)
            ledger["batches"].append({"batch_id": f"plan-{plan['built_at']}",
                                      "recorded_at": _now(), "probes": 0,
                                      "minutes": 0, "writes": 0})
            if auto.get("auto"):
                ledger["auto_approval"].append({
                    "batch_id": ledger["batches"][-1]["batch_id"],
                    "recorded_at": _now(), "auto": True,
                    "basis": auto.get("basis"),
                    "classify_reasons": auto.get("classify_reasons")})
        ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    plan["auto_continue"] = auto
    plan["budget_ledger_present"] = ledger is not None
    Path(args.out).write_text(json.dumps(plan, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(json.dumps({"out": args.out, "ready": len(plan["ready_queue"]),
                      "blocked": len(plan["blocked_with_recovery"]),
                      "requires_human": (auto or {}).get("requires_human")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())