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
    python scripts/exploration_runner.py record --ledger <budget-consumption.json> \
        --events <工作区>/events.jsonl --batch-id B1 \
        [--write-ops-ledger <写台账.json>] [--runtime-manifest <m.json>] \
        [--declaration <只读声明.json>]
    python scripts/exploration_runner.py loop --exploration <exploration.json> \
        --capabilities <runtime-capabilities.json> --plan <adversarial-plan.json> \
        --events <工作区>/events.jsonl --ledger <budget-consumption.json> \
        --state <loop-state.json> --log <loop-log.jsonl> \
        [--n-zero-rounds 2] [--candidates <本轮候选.json>] [--resume-after-human] \
        [--runtime-manifest <m.json>]

record 模式把「一批」的消耗累计进账本：探针数由台账逐条 attack.probes 求和，
时长由 BEGIN→FINISH 时间戳派生，写入次数取写台账条目数。**总量不由人填**；
逐项声明仍是执行方给的（脚本看不到网络），未声明项单列计数，不按 0 冒充。
账本记录 `recorded_through_seq` 水位线：重复调用只累计水位线之后的新 FINISH，
不会把历史批次重复计费。

loop 模式只推进「有界自迭代」的状态机，不执行探针、不改门禁语义：
停机条件为三条收敛条件（队列空 / 连续 N 轮增量归零 / 预算耗尽）加一条安全联锁
（副作用未决）；N 与 plan_hash 在首轮写入 loop-state.json，之后改值即拒绝；
--plan 必填（缺省即关闭自扩边界检查）。每轮追加一行 loop-log.jsonl（增量、新增
case/surf/方法、新增 FAIL、独立 oracle、计划外面、队列三态、预算、停机判定）。
上一轮产物含判定字段时，下一轮必须带 --resume-after-human 才可推进（人工核对
loop-log 的 product_violations）。record 与 loop 都先校验 events 的
digest/seq/previous 哈希链，并把消耗写回账本共用的 recorded_through_seq 水位线。
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import amendment_class as _AMD
import design_fingerprint as _FP
import exploration_state as _EXP
import iteration_delta as _DELTA

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


BUDGET_KEYS = ("probes", "minutes", "writes")


def _consumed(consumed, key):
    """按预算口径取已消耗量，兼容台账的 *_consumed 键名。

    修复的实测缺陷：main() 传的是 probes_consumed/minutes_consumed/writes_consumed，
    而 read_only_budget 声明的是 probes/minutes/writes；键名对不上时 consumed.get(key)
    恒为 None → 预算拦截静默失效（实测上限 1、累计 999 仍判 auto）。
    """
    for name in (key, key + "_consumed"):
        value = (consumed or {}).get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return 0


def _event_seq(event, fallback):
    """事件序号；旧台账无 seq 时按行号兜底，保证水位线仍可前进。"""
    value = (event or {}).get("seq")
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback


def _undeclared_probes(consumed):
    """未声明 probes 的 FINISH 数（按每条至少 1 次探针计的下限）。"""
    total = 0
    for batch in (consumed or {}).get("batches") or []:
        detail = (batch or {}).get("detail") or {}
        value = detail.get("probes_undeclared_attempts")
        if isinstance(value, int) and not isinstance(value, bool):
            total += value
    return total


def within_budget(manifest, consumed):
    """三口径（探针数/时长/写入次数）任一达上限即 False；无预算声明不拦截。

    上限为 0 表示「不允许发生」：此时已消耗必须为 0 才算未超，不能用 0>=0 把
    「一次都没写」判成超预算——否则只读声明（writes:0）会让自动续批永久失效。
    探针口径把「未声明 probes 的 FINISH」按每条至少 1 次计入下限：否则执行方
    不填 probes，预算永远显示 0，停机条件形同虚设。
    """
    budget = ((manifest or {}).get("read_only_budget") or {})
    undeclared = _undeclared_probes(consumed)
    for key, limit in budget.items():
        if not isinstance(limit, (int, float)) or isinstance(limit, bool):
            continue
        used = _consumed(consumed, key)
        if key == "probes":
            used += undeclared
        if (used > limit) if limit == 0 else (used >= limit):
            return False
    return True


def budget_status(manifest, consumed):
    """预算三口径的可核对状态（供 evaluate_stop 与 loop-log 使用）。"""
    budget = ((manifest or {}).get("read_only_budget") or {})
    undeclared = _undeclared_probes(consumed)
    details, exhausted = {}, False
    for key, limit in budget.items():
        if not isinstance(limit, (int, float)) or isinstance(limit, bool):
            continue
        used = _consumed(consumed, key)
        floor = used + (undeclared if key == "probes" else 0)
        hit = (floor > limit) if limit == 0 else (floor >= limit)
        exhausted = exhausted or hit
        details[key] = {"limit": limit, "used": used, "used_floor": floor,
                        "exhausted": hit}
    return {"declared": bool(budget), "exhausted": exhausted,
            "probes_undeclared_attempts": undeclared, "details": details}


def _ack_state(row):
    """承认项三态：complete（字段齐）/ incomplete（有对象但缺字段）/ None。"""
    ack = row.get("acknowledged_unexplored")
    if not isinstance(ack, dict):
        return None
    ok = (bool(str(ack.get("next_action") or "").strip())
          and bool(str(ack.get("owner") or "").strip()))
    return "complete" if ok else "incomplete"


def queue_state(report, capabilities=None):
    """ready 三态：可执行 / 已承认 / 被阻塞；字段不全单列，不当作已收敛。

    承认/阻塞直接从 items 派生，**不读 gate 结论**（gate 只作判定输出，不参与停机）。
    提供 capabilities 且页面交互通道均不可用时，ready 计为 blocked_by_capability，
    避免循环空跑两轮才因增量归零停下。
    """
    items = (report or {}).get("items") or []
    ready = list((report or {}).get("ready") or [])
    acknowledged = incomplete = 0
    blocked = []
    for row in items:
        state = _ack_state(row)
        if state == "complete" and row.get("status") == "NOT_RUN":
            acknowledged += 1
        elif state == "incomplete":
            incomplete += 1
        if (row.get("status") == "NOT_RUN"
                and str(row.get("blocked_reason") or "").strip()):
            blocked.append(row.get("id"))
    blocked_by_capability = 0
    if capabilities is not None:
        channels = (capabilities or {}).get("channels") or {}
        page_ok = any((channels.get(name) or {}).get("state") == "available"
                      for name in PAGE_CHANNELS)
        if not page_ok and ready:
            blocked_by_capability = len(ready)
            blocked.extend(ready)
            ready = []
    return {
        "ready": len(ready),
        "ready_ids": ready,
        "acknowledged": acknowledged,
        "acknowledged_incomplete": incomplete,
        "blocked": len(blocked),
        "blocked_ids": blocked,
        "blocked_by_capability": blocked_by_capability,
        "needs_reconcile": len((report or {}).get("needs_reconcile") or []),
    }


def evaluate_stop(queue, increments, n_zero_rounds=2, budget=None,
                  side_effect_pending=None):
    """机械停机判定（只读入参，不读清单、不看 gate 结论）。

    优先级：安全联锁（副作用未决）> 预算耗尽 > 队列空 > 连续 N 轮增量归零。
    - queue_empty 的 convergence 只有在「无承认项、无阻塞项、无字段不全项」时为真；
      有承认/阻塞时停机但 convergence=false，避免把诚实登记当成已收敛。
    - increment_zero 停机时 ready 仍可能有剩余，必须把 ready_remaining 写进记录并
      置 convergence=false，避免把「执行方卡住」伪装成「已收敛」。
    """
    if int(n_zero_rounds) < 1:
        raise ValueError("n_zero_rounds 必须 >= 1（N 在循环启动前写死）")
    pending = list(side_effect_pending or [])
    if pending:
        return {"stop": True, "code": "side_effect_pending", "class": "safety",
                "convergence": False, "pending": pending,
                "reason": "写操作状态未知：先核副作用再继续，不因排序自动重放"}
    budget = budget or {}
    if budget.get("exhausted"):
        return {"stop": True, "code": "budget_exhausted", "class": "convergence",
                "convergence": False, "budget": budget,
                "reason": "预算三口径任一耗尽；停机记录须写已消耗量+剩余义务+影响"}
    queue = queue or {}
    if queue.get("ready", 0) == 0:
        converged = (queue.get("acknowledged", 0) == 0
                     and queue.get("acknowledged_incomplete", 0) == 0
                     and queue.get("blocked", 0) == 0)
        return {"stop": True, "code": "queue_empty", "class": "convergence",
                "convergence": converged, "queue": queue,
                "reason": "ready 队列为空" if converged
                          else "ready 队列为空，但仍有承认/阻塞/字段不全项，不得当作已收敛"}
    trailing = 0
    for value in reversed(list(increments or [])):
        if value == 0:
            trailing += 1
        else:
            break
    if trailing >= int(n_zero_rounds):
        return {"stop": True, "code": "increment_zero", "class": "convergence",
                "convergence": False, "zero_rounds": trailing,
                "ready_remaining": queue.get("ready"),
                "reason": f"连续 {trailing} 轮新增执行数为 0；"
                          f"ready 仍有 {queue.get('ready')} 项，按未收敛停机"}
    return {"stop": False, "code": None, "class": None, "convergence": None,
            "zero_rounds": trailing, "ready_remaining": queue.get("ready")}


def _stop_conditions():
    """写进 batch-plan 的停机契约：三条收敛条件 + 一条安全联锁。

    明确不设「门禁通过 / 缺口为零 / 无新增可证伪预测」当停机：门禁通过是判定结果
    不是收敛信号，后两者是语义判断或会逼出凑数。配额类不再作为独立 code——
    writes/污染/告警配额属于预算口径，并入 budget_exhausted。
    """
    conditions = [
        {"code": "side_effect_pending", "class": "safety", "mechanical": True,
         "when": "needs_reconcile 非空（任意状态的写操作状态未知）",
         "source": "exploration_state.summarize()['needs_reconcile']",
         "on_stop": "优先于其余停机；先核副作用再继续，不因排序自动重放"},
        {"code": "budget_exhausted", "class": "convergence", "mechanical": True,
         "when": "read_only_budget 三口径任一耗尽（未声明 probes 的 FINISH 按每条至少 1 次计）",
         "source": "budget-consumption/1 + runtime-manifest.json",
         "on_stop": "停机记录写「已消耗量 + 剩余未执行 ADV 义务 + 影响」，不改判已执行探针结论"},
        {"code": "queue_empty", "class": "convergence", "mechanical": True,
         "when": "ready 队列为空且无 NOT_RUN 可执行项",
         "source": "exploration_state.summarize()['ready'] + gate.ready_pending_ids",
         "on_stop": "输出 done/acknowledged/blocked 三态；承认/阻塞/字段不全 >0 时 convergence=false"},
        {"code": "increment_zero", "class": "convergence", "mechanical": True,
         "when": "连续 N 轮新增执行数为 0（N 由启动参数写死，默认 2）",
         "source": "events.jsonl（iteration_delta.round_delta）",
         "on_stop": "ready 仍有剩余时 convergence=false 并写 ready_remaining，不得当作已收敛"},
    ]
    for item in conditions:
        # 兼容旧字段名 trigger（仓内无消费者，但外部脚本可能读）。
        item.setdefault("trigger", item["when"])
    return conditions


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
    return {
        "schema": SCHEMA,
        "built_at": _now(),
        "ready_queue": ready,
        "blocked_with_recovery": blocked,
        "batch_size": size,
        "batches": batches,
        "budget": budget,
        "queue_state": queue_state(report, capabilities),
        "stop_conditions": _stop_conditions(),
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
            "batches": [], "auto_approval": [], "auto_judgements": [],
            "probes_consumed": 0, "minutes_consumed": 0, "writes_consumed": 0,
            # 缺失项单列：不把「没申报」写成 0（0 与 null 含义相反）
            "minutes_missing_batches": 0, "writes_missing_batches": 0,
            # 已计入的最后一个事件序号：重复 record 只累计其后的新 FINISH，
            # 否则每轮调用都会把历史批次重新求和，预算虚增导致提前停机。
            "recorded_through_seq": 0}


def record_batch(ledger, batch_id, probes=0, minutes=None, writes=None, auto=None,
                 source=None, detail=None):
    """登记一个批次的消耗；auto 为 auto_continue 的输出（None = 未走自动续批判定）。

    minutes/writes 传 None 表示「本批无法派生」，不累加进总量并单列缺失批次；
    传 0 才是「确实为 0」。这条区分是为了避免用 0 冒充未测。
    """
    ledger.setdefault("minutes_missing_batches", 0)
    ledger.setdefault("writes_missing_batches", 0)
    entry = {"batch_id": batch_id, "recorded_at": _now(),
             "probes": probes, "minutes": minutes, "writes": writes}
    if source:
        entry["source"] = source
    if detail:
        entry["detail"] = detail
    ledger["batches"].append(entry)
    ledger["probes_consumed"] += probes
    if minutes is None:
        ledger["minutes_missing_batches"] += 1
    else:
        ledger["minutes_consumed"] += minutes
    if writes is None:
        ledger["writes_missing_batches"] += 1
    else:
        ledger["writes_consumed"] += writes
    if auto is not None:
        # 每次判定都登记（含 auto=false）：否则 auto_approval_ratio 只有分子没有分母。
        ledger.setdefault("auto_judgements", []).append({
            "batch_id": batch_id, "recorded_at": _now(),
            "auto": bool(auto.get("auto")),
            "requires_human": auto.get("requires_human"),
            "basis": auto.get("basis"),
            "classify_reasons": auto.get("classify_reasons"),
            "over_budget": auto.get("over_budget")})
    if auto and auto.get("auto"):
        ledger["auto_approval"].append({"batch_id": batch_id, "recorded_at": _now(),
                                        "auto": True, "basis": auto.get("basis"),
                                        "classify_reasons": auto.get("classify_reasons")})
    return ledger


def consumption_from_ledger(events, write_ops=None, since_seq=0):
    """从执行台账派生一批消耗：探针求和、时长按 BEGIN→FINISH 墙钟。

    - 探针数只能来自逐条 attack.probes 声明（脚本看不到网络请求），但**总量由脚本
      求和**，不再由人填汇总；未声明 probes 的 FINISH 单列计数。
    - 只统计 `seq > since_seq` 的 FINISH：账本用水位线防止重复调用重复计费。
    - 时长缺任一端时间戳的 attempt 不计入并单列，避免用 0 冒充「没花时间」。
    - 写入次数取写台账 entries 条数；未提供写台账时为 None（不写 0）。
    """
    events = list(events or [])
    since = int(since_seq or 0)
    indexed = [(_event_seq(event, number), event)
               for number, event in enumerate(events, 1)]
    begins = {event.get("attempt_id"): event for _, event in indexed
              if event.get("kind") == "BEGIN"}
    finishes = [event for seq, event in indexed
                if event.get("kind") == "FINISH" and seq > since]
    through_seq = max([seq for seq, _ in indexed] or [since])
    probes = 0
    undeclared = 0
    for event in finishes:
        declared = event.get("probes")
        if isinstance(declared, int) and not isinstance(declared, bool):
            probes += declared
        else:
            undeclared += 1
    seconds, unpaired = 0.0, 0
    for event in finishes:
        started = begins.get(event.get("attempt_id"))
        try:
            elapsed = (datetime.fromisoformat(str(event["timestamp"]))
                       - datetime.fromisoformat(str(started["timestamp"]))).total_seconds()
        except (KeyError, TypeError, ValueError):
            unpaired += 1
            continue
        seconds += max(0.0, elapsed)
    paired = len(finishes) - unpaired
    writes = None
    if isinstance(write_ops, dict) and isinstance(write_ops.get("entries"), list):
        writes = len(write_ops["entries"])
    return {"probes": probes, "probes_undeclared_attempts": undeclared,
            "minutes": round(seconds / 60.0, 2) if paired else None,
            "minutes_unpaired_attempts": unpaired,
            "writes": writes,
            "since_seq": since, "through_seq": through_seq,
            "finishes_counted": len(finishes)}


def plan_main(argv=None):
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
            # 消耗只读账本累计值；键名经 _consumed 归一，兼容 *_consumed 形态。
            auto = auto_continue(_load(args.declaration), manifest, ledger)
            # 编排本身不产生探针消耗：只登记判定事件，不追加 0 消耗占位批。
            # auto_judgements 记录每次判定（auto 真/假），给 auto_approval_ratio 提供分母。
            judgement = {
                "batch_id": f"plan-{plan['built_at']}", "recorded_at": _now(),
                "auto": bool(auto.get("auto")),
                "requires_human": auto.get("requires_human"),
                "basis": auto.get("basis"),
                "classify_reasons": auto.get("classify_reasons"),
                "over_budget": auto.get("over_budget")}
            # 编排期判定是「当前一次判定」而非历史批次事件：反复重排同一计划
            # 不得虚增 auto_approval_ratio 的分子分母，先清掉旧的 plan-* 条目。
            ledger["auto_judgements"] = [
                row for row in ledger.setdefault("auto_judgements", [])
                if not str(row.get("batch_id") or "").startswith("plan-")]
            ledger["auto_judgements"].append(judgement)
            ledger["auto_approval"] = [
                row for row in ledger.get("auto_approval") or []
                if not str(row.get("batch_id") or "").startswith("plan-")]
            if auto.get("auto"):
                ledger["auto_approval"].append({
                    "batch_id": f"plan-{plan['built_at']}",
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


def _read_events(path):
    events = []
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def verify_event_chain(events):
    """校验执行台账的哈希链（digest/seq/previous）。

    增量与预算都以 events.jsonl 为唯一事实源；若只 json.loads 不验链，手改一行就能
    同时抬高增量、压低消耗，B′「执行台账难伪造」的前提会被绕过。返回 (ok, reason)。
    """
    previous = None
    for number, event in enumerate(events or [], 1):
        digest = event.get("digest")
        if not isinstance(digest, str) or not digest:
            return False, f"第 {number} 行缺 digest：执行台账必须由 execution_ledger 追加"
        content = {key: value for key, value in event.items() if key != "digest"}
        if _FP.fingerprint(content) != digest:
            return False, f"第 {number} 行 digest 不匹配：事件被改动"
        if event.get("seq") != number:
            return False, f"第 {number} 行 seq 不连续（期望 {number}）"
        if event.get("previous") != previous:
            return False, f"第 {number} 行 previous 链断裂"
        previous = digest
    return True, None


def record_main(argv=None):
    parser = argparse.ArgumentParser(
        prog="exploration_runner.py record",
        description="把一批消耗累计进预算账本（探针/时长由执行台账派生，不手填总量）")
    parser.add_argument("--ledger", required=True, help="budget-consumption.json 路径")
    parser.add_argument("--events", required=True, help="执行台账 events.jsonl")
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--write-ops-ledger", dest="write_ops_ledger",
                        help="写操作台账 JSON（按 entries 条数计写入次数；缺省不计写入）")
    parser.add_argument("--runtime-manifest", dest="runtime_manifest",
                        help="runtime-manifest.json（预算声明）")
    parser.add_argument("--declaration", help="只读声明 JSON（触发自动续批判定）")
    args = parser.parse_args(argv)

    manifest = _load(args.runtime_manifest)
    ledger_path = Path(args.ledger)
    ledger = (json.loads(ledger_path.read_text(encoding="utf-8-sig"))
              if ledger_path.is_file() else new_budget_ledger(manifest))
    if any(batch.get("batch_id") == args.batch_id
           for batch in ledger.get("batches") or []):
        print(json.dumps({"error": f"重复的 batch_id {args.batch_id}："
                                   "同一批不得重复登记（会重复累计探针）"},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    events = _read_events(args.events)
    ok, reason = verify_event_chain(events)
    if not ok:
        print(json.dumps({"error": reason}, ensure_ascii=False), file=sys.stderr)
        return 2
    # 水位线：只累计上次已计入序号之后的新 FINISH，重复调用不重复计费。
    since = int(ledger.get("recorded_through_seq") or 0)
    usage = consumption_from_ledger(events, _load(args.write_ops_ledger),
                                    since_seq=since)
    # 判定用「记录本批之前」的累计：本批是已经发生的事，不能用它给自己放行。
    auto = None
    if args.declaration:
        auto = auto_continue(_load(args.declaration), manifest, ledger)
    record_batch(ledger, args.batch_id, probes=usage["probes"],
                 minutes=usage["minutes"], writes=usage["writes"], auto=auto,
                 source="ledger", detail=usage)
    ledger["recorded_through_seq"] = max(since, int(usage.get("through_seq") or 0))
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(json.dumps({"batch_id": args.batch_id, **usage,
                      "probes_consumed": ledger.get("probes_consumed"),
                      "minutes_consumed": ledger.get("minutes_consumed"),
                      "writes_consumed": ledger.get("writes_consumed")},
                     ensure_ascii=False))
    return 0


LOOP_STATE_SCHEMA = "loop-state/1"
LOOP_LOG_SCHEMA = "loop-log/1"


def _load_json(path, default=None):
    if not path or not Path(path).is_file():
        return default
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def loop_main(argv=None):
    """推进一轮有界自迭代：只读入参与追加日志，不执行探针、不改门禁语义。"""
    parser = argparse.ArgumentParser(
        prog="exploration_runner.py loop",
        description="有界自迭代状态机：三条收敛停机 + 一条安全联锁；不做执行与判定")
    parser.add_argument("--exploration", required=True)
    parser.add_argument("--capabilities", required=True)
    parser.add_argument("--plan", required=True,
                        help="adversarial-plan.json（surfaces 用于计划外面判定；"
                             "缺省即关闭自扩边界检查，故必填）")
    parser.add_argument("--events", required=True, help="执行台账 events.jsonl")
    parser.add_argument("--ledger", required=True, help="budget-consumption.json")
    parser.add_argument("--state", required=True, help="loop-state.json（首轮新建）")
    parser.add_argument("--log", required=True, help="loop-log.jsonl（追加）")
    parser.add_argument("--n-zero-rounds", dest="n_zero_rounds", type=int, default=2,
                        help="连续多少轮增量归零停机；首轮写进 state，之后改值即拒绝")
    parser.add_argument("--candidates", help="本轮循环候选 JSON（数组；含判定字段则该轮产物作废）")
    parser.add_argument("--resume-after-human", dest="resume_after_human",
                        action="store_true",
                        help="上一轮产物作废（含判定字段）时，人工核对后凭此标志才可推进下一轮")
    parser.add_argument("--runtime-manifest", dest="runtime_manifest")
    args = parser.parse_args(argv)

    n_zero = args.n_zero_rounds
    if n_zero < 1:
        print(json.dumps({"error": "n_zero_rounds 必须 >= 1"}, ensure_ascii=False),
              file=sys.stderr)
        return 2
    state_path = Path(args.state)
    state = _load_json(state_path)
    if state is None:
        state = {"schema": LOOP_STATE_SCHEMA, "n_zero_rounds": n_zero,
                 "started_at": _now(), "updated_at": None,
                 "last_seq": 0, "rounds": [], "stopped": False, "stop": None}
    elif state.get("n_zero_rounds") != n_zero:
        # N 必须在循环启动前写死；中途改 N 等于把「续命」写进状态，直接拒绝。
        print(json.dumps({"error": "n_zero_rounds 与 loop-state 不一致："
                                   "N 必须启动时写死，不得中途调整",
                          "state_n": state.get("n_zero_rounds"),
                          "arg_n": n_zero}, ensure_ascii=False), file=sys.stderr)
        return 2
    if state.get("stopped"):
        print(json.dumps({"stopped": True, "stop": state.get("stop"),
                          "rounds": len(state.get("rounds") or [])},
                         ensure_ascii=False))
        return 0
    # G-A：上一轮产物含判定字段时循环必须停住等人核——「循环内不自我裁决」是硬约束，
    # 只记 requires_human 字段不拦下一轮，等于把硬约束降级成日志。
    rounds = state.get("rounds") or []
    if rounds and rounds[-1].get("products_invalid") and not args.resume_after_human:
        print(json.dumps({"error": "上一轮循环产物含判定字段，须人工核对 loop-log 的 "
                                   "product_violations 后带 --resume-after-human 才可推进"},
                         ensure_ascii=False), file=sys.stderr)
        return 2

    capabilities = _load(args.capabilities) or {}
    report = _EXP.summarize(json.loads(
        Path(args.exploration).read_text(encoding="utf-8-sig")))
    queue = queue_state(report, capabilities)
    plan = _load_json(args.plan)
    try:
        surfaces = _DELTA.plan_surface_ids(plan) if plan is not None else None
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    # 换 plan 等于换风险面口径：必须新建 loop-state，不能沿用同一轮收敛计数。
    plan_hash = _FP.fingerprint(plan) if plan is not None else None
    if state.get("plan_hash"):
        if plan_hash != state["plan_hash"]:
            print(json.dumps({"error": "plan 与 loop-state 不一致："
                                       "换 plan 必须新建 loop-state 并留痕"},
                             ensure_ascii=False), file=sys.stderr)
            return 2
    elif plan_hash:
        state["plan_hash"] = plan_hash

    events = _read_events(args.events)
    ok, reason = verify_event_chain(events)
    if not ok:
        print(json.dumps({"error": reason}, ensure_ascii=False), file=sys.stderr)
        return 2
    delta = _DELTA.round_delta(events, surfaces, state.get("last_seq") or 0)

    candidates = _load_json(args.candidates)
    if candidates is not None and not isinstance(candidates, list):
        print(json.dumps({"error": "--candidates 必须为数组"}, ensure_ascii=False),
              file=sys.stderr)
        return 2
    violations = (_DELTA.scan_loop_products(candidates)
                  if candidates is not None else [])
    invalid = bool(violations)
    increment = 0 if invalid else delta["new_executions"]

    manifest = _load(args.runtime_manifest)
    ledger_path = Path(args.ledger)
    ledger = (json.loads(ledger_path.read_text(encoding="utf-8-sig"))
              if ledger_path.is_file() else new_budget_ledger(manifest))
    # 先用「本轮之前」的累计判定，本轮消耗不能给自己放行。
    budget = budget_status(manifest, ledger)
    # 产物作废的轮次不计入增量归零：该轮需人核，不能靠空转自动停机。
    increments = [row.get("new_executions", 0)
                  for row in state.get("rounds") or []
                  if not row.get("products_invalid")]
    if not invalid:
        increments.append(increment)
    stop = evaluate_stop(queue, increments, n_zero, budget,
                         report.get("needs_reconcile"))

    round_no = len(state.get("rounds") or []) + 1
    iteration_id = f"ITER-{round_no:03d}"
    entry = {
        "schema": LOOP_LOG_SCHEMA,
        "round": round_no,
        "iteration_id": iteration_id,
        "at": _now(),
        "new_executions": increment,
        "new_attempt_ids": [] if invalid else delta["new_attempt_ids"],
        "new_case_ids": delta["new_case_ids"],
        "new_surf_ids": delta["new_surf_ids"],
        "method_counts": delta["method_counts"],
        # 检出披露：新增 FAIL 即本轮新发现，S5 第二轮观测检出的数据源。
        "new_fail_count": delta["new_fail_count"],
        "fail_attempt_ids": delta["fail_attempt_ids"],
        # plan 无 surfaces 字段时自扩边界无法判定，必须显式披露而非静默为空。
        "surfaces_missing": surfaces is None,
        "oracle_executions": delta["oracle_executions"],
        "oracle_attempt_ids": delta["oracle_attempt_ids"],
        "evidence_reused": delta["evidence_reused"],
        "replay_not_counted": delta["replay_not_counted"],
        "blocked_not_counted": delta["blocked_not_counted"],
        "unregistered_expansion": delta["unregistered_expansion"],
        # loop 不自动登记 amendment（没有 declaration 与人工状态）；列出待补项，
        # 由执行方在下一轮前走 request_amendment 登记。
        "amendment_required": delta["unregistered_expansion"],
        "products_invalid": invalid,
        "requires_human": invalid,
        "product_violations": violations,
        "queue": queue,
        "budget": budget,
        "stop": stop,
        "since_seq": delta["since_seq"],
        "through_seq": delta["through_seq"],
    }

    # 本轮消耗写回账本：与 record 共用 recorded_through_seq，互不重复计费。
    since = int(ledger.get("recorded_through_seq") or 0)
    usage = consumption_from_ledger(events, None, since_seq=since)
    # batch_id 带 plan 指纹前缀：同一 workspace 重开 loop（新 state）不会与旧轮次撞号。
    batch_id = f"loop-{str(state.get('plan_hash') or 'noplan')[:8]}-{iteration_id}"
    already = any(batch.get("batch_id") == batch_id
                  for batch in ledger.get("batches") or [])
    if usage["finishes_counted"] and not already:
        record_batch(ledger, batch_id, probes=usage["probes"],
                     minutes=usage["minutes"], writes=usage["writes"],
                     source="loop", detail=usage)
        ledger["recorded_through_seq"] = max(since,
                                             int(usage.get("through_seq") or 0))
        ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        entry["budget_after"] = budget_status(manifest, ledger)

    with Path(args.log).open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
    state["rounds"].append({"round": round_no,
                            "iteration_id": iteration_id,
                            "new_executions": increment,
                            "new_case_ids": delta["new_case_ids"],
                            "new_surf_ids": delta["new_surf_ids"],
                            "unregistered_expansion": delta["unregistered_expansion"],
                            "products_invalid": invalid})
    state["last_seq"] = delta["through_seq"]
    state["updated_at"] = _now()
    if stop["stop"]:
        state["stopped"] = True
        state["stop"] = stop
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(json.dumps({"round": round_no, "new_executions": increment,
                      "new_cases": len(delta["new_case_ids"]),
                      "new_fails": delta["new_fail_count"],
                      "oracle_executions": delta["oracle_executions"],
                      "unregistered_expansion": len(delta["unregistered_expansion"]),
                      "products_invalid": invalid, "stop": stop["code"],
                      "convergence": stop["convergence"]},
                     ensure_ascii=False))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "record":
        return record_main(argv[1:])
    if argv and argv[0] == "loop":
        return loop_main(argv[1:])
    return plan_main(argv)


if __name__ == "__main__":
    sys.exit(main())