#!/usr/bin/env python3
"""写操作清理台账（write-ops ledger）。

## 为什么需要它

`all` 安全策略允许执行真实的写/删/支付/退款动作，SKILL 要求记录「操作前后状态与
是否可恢复」，但**没有任何回收机制**：跑完一轮，测试数据（问卷、成员、订单）留在
目标环境里，下一轮再从脏数据开始。这不是理论风险——造出来的数据会污染后续断言，
也会让"这条问卷为什么多出来一个"变成无人认领的问题。

本脚本把「可恢复性」变成可校验、可审计的结构化台账：

    python scripts/write_ops_ledger.py --file ./parity-test/write_ops_ledger.json \
        --add-file ./parity-test/write_op_new.json

    python scripts/write_ops_ledger.py --file ./parity-test/write_ops_ledger.json --plan
    python scripts/write_ops_ledger.py --file ./parity-test/write_ops_ledger.json \
        --mark-clean WO-0002 --evidence "问卷已删除，列表页 0 条"

    python scripts/write_ops_ledger.py --file ./parity-test/write_ops_ledger.json \
        --merge-pending --report ./parity-test/report.json
    python scripts/write_ops_ledger.py --file ./parity-test/write_ops_ledger.json --audit --enforce

## 边界（不要在这里做假动作）

1. 本脚本**不执行**清理动作。逆向操作用哪条接口、哪个页面，只有写操作的人知道；
   自动猜一个"删除接口"去调用，比不清理更危险（可能删错对象）。
   它负责的是：登记、校验、出计划、审计、把未清理项推进报告的「待确认」。
2. `reversible` 必须显式声明。缺省当 false 是错的——那会让"其实能删"的数据被永久
   留在环境里；缺省当 true 更错——会让人以为清理已完成。所以缺了直接报错。
3. `reversible=true` 必须有 `cleanup.method`（api / ui / manual）与 `cleanup.target`。
4. 未清理的 reversible 项是**环境残留**，`--audit --enforce` 以非零码拦截；不可恢复项
   （reversible=false）不拦，但必须在报告里单列，由人决定是否接受。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

LEDGER_VERSION = "1.0"
VALID_LEVELS = {"caution", "danger"}
VALID_CLEANUP_METHODS = {"api", "ui", "manual"}
VALID_STATUS = {"pending", "cleaned", "failed", "uncleanable"}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load(path: Path) -> dict:
    if not path.exists():
        return {"version": LEDGER_VERSION, "createdAt": _now(), "entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("台账无法解析：{}（{}）".format(path, exc))
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise SystemExit("台账形状非法：缺少 entries 数组（{}）".format(path))
    return data


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updatedAt"] = _now()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def next_id(data: dict) -> str:
    used = {str(e.get("id") or "") for e in data["entries"] if isinstance(e, dict)}
    n = 1
    while True:
        candidate = "WO-{:04d}".format(n)
        if candidate not in used:
            return candidate
        n += 1


def validate(entry: dict) -> list:
    """返回问题清单；空表示合格。校验失败不允许写入——漏填 reversible 的条目
    会让台账看起来"都登记了"，实际没人知道该不该清理。"""
    problems = []
    if not str(entry.get("action") or "").strip():
        problems.append("缺少 action（做了什么写操作）")
    if not str(entry.get("target") or "").strip():
        problems.append("缺少 target（操作对象：页面/接口/数据主键）")
    level = str(entry.get("safetyLevel") or "")
    if level not in VALID_LEVELS:
        problems.append("safetyLevel 必须是 caution 或 danger（当前 '{}'）".format(level or "空"))
    if "reversible" not in entry:
        problems.append("缺少 reversible：必须显式声明 true（能回收）或 false（不可恢复）")
    elif not isinstance(entry.get("reversible"), bool):
        problems.append("reversible 必须是布尔值")
    elif entry["reversible"] is True:
        cleanup = entry.get("cleanup")
        if not isinstance(cleanup, dict):
            problems.append("reversible=true 时必须提供 cleanup 对象（怎么回收）")
        else:
            method = str(cleanup.get("method") or "")
            if method not in VALID_CLEANUP_METHODS:
                problems.append("cleanup.method 必须是 api / ui / manual（当前 '{}'）".format(method or "空"))
            if not str(cleanup.get("target") or "").strip():
                problems.append("cleanup.target 不能为空（回收动作指向哪里）")
    status = str(entry.get("status") or "pending")
    if status not in VALID_STATUS:
        problems.append("status 必须是 pending / cleaned / failed / uncleanable（当前 '{}'）".format(status))
    return problems


def cmd_add(data: dict, add_path: Path) -> int:
    try:
        payload = json.loads(add_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print("错误：无法读取待登记条目 {}：{}".format(add_path, exc), file=sys.stderr)
        return 3
    items = payload if isinstance(payload, list) else [payload]
    existing = {str(e.get("id") or "") for e in data["entries"] if isinstance(e, dict)}
    added, rejected = 0, []
    for raw in items:
        if not isinstance(raw, dict):
            rejected.append(("(非对象)", ["条目必须是 JSON 对象"]))
            continue
        entry = dict(raw)
        entry.setdefault("registeredAt", _now())
        entry.setdefault("status", "pending")
        problems = validate(entry)
        if problems:
            rejected.append((str(entry.get("action") or "(未命名)"), problems))
            continue
        if entry.get("id") and str(entry["id"]) in existing:
            print("跳过已存在条目 id={}".format(entry["id"]), file=sys.stderr)
            continue
        if not entry.get("id"):
            entry["id"] = next_id(data)
        data["entries"].append(entry)
        existing.add(str(entry["id"]))
        added += 1
    if rejected:
        print("错误：{} 条条目校验未通过，未写入：".format(len(rejected)), file=sys.stderr)
        for action, problems in rejected:
            print("  - {}：{}".format(action, "；".join(problems)), file=sys.stderr)
        return 2
    print(json.dumps({"added": added, "total": len(data["entries"])}, ensure_ascii=False))
    return 0


def pending_entries(data: dict) -> list:
    out = []
    for entry in data["entries"]:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("status") or "pending") != "pending":
            continue
        out.append(entry)
    return out


def cmd_plan(data: dict, out_path: Path | None) -> int:
    pending = pending_entries(data)
    reversible = [e for e in pending if e.get("reversible") is True]
    irreversible = [e for e in pending if e.get("reversible") is not True]

    lines = ["# 写操作清理计划", "",
             "- 待清理（可恢复）：{} 条".format(len(reversible)),
             "- 不可恢复（需人工确认是否接受残留）：{} 条".format(len(irreversible)),
             "- 生成时间：{}".format(_now()), ""]

    if reversible:
        lines += ["## 可回收项（按此清单执行）", "",
                  "| ID | 分级 | 动作 | 目标 | 回收方式 | 回收目标 | 证据 |",
                  "|---|---|---|---|---|---|---|"]
        for e in reversible:
            cleanup = e.get("cleanup") or {}
            lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
                e.get("id"), e.get("safetyLevel"), e.get("action"), e.get("target"),
                cleanup.get("method"), cleanup.get("target"),
                (cleanup.get("evidence") or e.get("evidence") or "")))
        lines.append("")
        lines.append("执行完每条后用 `--mark-clean <id> --evidence \"<验证结果>\"` 回填；"
                     "清理失败用 `--mark-failed`，不要静默跳过。")
        lines.append("")

    if irreversible:
        lines += ["## 不可恢复项（无法回收，需在报告中声明）", "",
                  "| ID | 分级 | 动作 | 目标 | 执行前状态 |",
                  "|---|---|---|---|---|"]
        for e in irreversible:
            lines.append("| {} | {} | {} | {} | {} |".format(
                e.get("id"), e.get("safetyLevel"), e.get("action"), e.get("target"),
                json.dumps(e.get("before"), ensure_ascii=False) if e.get("before") is not None else "-"))
        lines.append("")

    text = "\n".join(lines)
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print("计划已写入 {}".format(out_path))
    print(text)
    return 0


def _find(data: dict, entry_id: str):
    for entry in data["entries"]:
        if isinstance(entry, dict) and str(entry.get("id")) == entry_id:
            return entry
    return None


def cmd_mark(data: dict, entry_id: str, status: str, evidence: str | None) -> int:
    entry = _find(data, entry_id)
    if entry is None:
        print("错误：台账中不存在 id={}".format(entry_id), file=sys.stderr)
        return 3
    entry["status"] = status
    entry["statusUpdatedAt"] = _now()
    if evidence:
        entry["cleanupEvidence"] = evidence
    print(json.dumps({"id": entry_id, "status": status, "evidence": evidence or ""}, ensure_ascii=False))
    return 0


def cmd_audit(data: dict, enforce: bool) -> int:
    all_entries = [e for e in data["entries"] if isinstance(e, dict)]
    pending = pending_entries(data)
    reversible = [e for e in pending if e.get("reversible") is True]
    irreversible = [e for e in pending if e.get("reversible") is not True]
    by_status: dict = {}
    for e in all_entries:
        key = str(e.get("status") or "pending")
        by_status[key] = by_status.get(key, 0) + 1
    summary = {
        "total": len(all_entries),
        "byStatus": by_status,
        "pendingReversible": [e.get("id") for e in reversible],
        "pendingIrreversible": [e.get("id") for e in irreversible],
        "ok": not reversible,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if reversible:
        print("环境残留：{} 条可回收写操作尚未清理（{}）——测试数据仍在目标环境里。".format(
            len(reversible), "、".join(str(e.get("id")) for e in reversible)), file=sys.stderr)
    if enforce and reversible:
        return 1
    return 0


def cmd_merge_pending(data: dict, report_path: Path) -> int:
    """把未清理项并入 report.pendingChecks（幂等，按 _ledgerId 去重）。

    并进「待确认」而不是缺陷：环境残留不是产品缺陷，但它必须出现在报告里，
    否则"跑完这一轮环境干净"就是一句没人验证过的话。
    """
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print("错误：无法读取 report {}：{}".format(report_path, exc), file=sys.stderr)
        return 3
    if not isinstance(report, dict):
        print("错误：report 顶层必须是对象", file=sys.stderr)
        return 3
    container = report.get("pendingChecks")
    if not isinstance(container, list):
        container = []
        report["pendingChecks"] = container
    known = {str(x.get("_ledgerId")) for x in container if isinstance(x, dict)}
    added = 0
    for entry in pending_entries(data):
        entry_id = str(entry.get("id"))
        if entry_id in known:
            continue
        cleanup = entry.get("cleanup") or {}
        reversible = entry.get("reversible") is True
        container.append({
            "_ledgerId": entry_id,
            "category": "写操作清理",
            "name": "写操作未清理：{}（{}）".format(entry.get("action"), entry.get("target")),
            "verdict": "pending",
            "reason": ("可回收：按 cleanup.{} 回收后回填 --mark-clean。".format(cleanup.get("method"))
                       if reversible else
                       "不可恢复：无法回收，需人工确认是否接受该残留。"),
            "reversible": reversible,
            "cleanup": cleanup if reversible else None,
            "registeredAt": entry.get("registeredAt"),
        })
        known.add(entry_id)
        added += 1
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"merged": added, "pendingChecksTotal": len(container)}, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="写操作清理台账：登记 / 出计划 / 审计 / 并入报告")
    ap.add_argument("--file", type=Path, default=Path("./parity-test/write_ops_ledger.json"))
    ap.add_argument("--add-file", type=Path, default=None, help="待登记条目 JSON（单条或数组）")
    ap.add_argument("--plan", action="store_true", help="输出待清理计划")
    ap.add_argument("--out", type=Path, default=None, help="--plan 的 markdown 落盘路径")
    ap.add_argument("--mark-clean", dest="mark_clean", default=None, help="标记某条已清理")
    ap.add_argument("--mark-failed", dest="mark_failed", default=None, help="标记某条清理失败")
    ap.add_argument("--mark-uncleanable", dest="mark_uncleanable", default=None, help="标记某条确认不可回收")
    ap.add_argument("--evidence", default=None, help="配合 --mark-* 记录验证结果")
    ap.add_argument("--audit", action="store_true", help="审计未清理项")
    ap.add_argument("--enforce", action="store_true", help="存在未清理的可回收项时非零退出")
    ap.add_argument("--merge-pending", dest="merge_pending", action="store_true",
                    help="把未清理项并入 report.pendingChecks")
    ap.add_argument("--report", type=Path, default=None, help="配合 --merge-pending 的目标 report.json")
    args = ap.parse_args()

    data = load(args.file)
    touched = False

    if args.add_file:
        rc = cmd_add(data, args.add_file)
        if rc != 0:
            return rc
        touched = True

    for flag, status in (("mark_clean", "cleaned"), ("mark_failed", "failed"),
                         ("mark_uncleanable", "uncleanable")):
        value = getattr(args, flag)
        if value:
            rc = cmd_mark(data, value, status, args.evidence)
            if rc != 0:
                return rc
            touched = True

    if touched:
        save(args.file, data)

    rc = 0
    if args.merge_pending:
        if not args.report:
            print("错误：--merge-pending 需要 --report", file=sys.stderr)
            return 3
        rc = cmd_merge_pending(data, args.report)
    if args.plan:
        rc = cmd_plan(data, args.out) or rc
    if args.audit:
        rc = cmd_audit(data, args.enforce) or rc
    if not (args.add_file or args.plan or args.audit or args.merge_pending
            or args.mark_clean or args.mark_failed or args.mark_uncleanable):
        ap.print_help()
    return rc


if __name__ == "__main__":
    sys.exit(main())
