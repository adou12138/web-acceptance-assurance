#!/usr/bin/env python3
"""batch.py — 按模块分批调度、批次状态、全量汇总（集成方案 §8）。

状态机：pending -> doing -> done；gate: pass/fail/waived/not_run。
- 分批主判据是条数：sum(estimated_case_count) > 30 分批；≤ 15 一律单批；模块数只决定怎么切。
- 中断检测：doing 或 done 但 case_ids 为空 → 视为中断，重跑该批。
- 指纹比对：需求正文 SHA-256 与 review-result.json 的 fingerprint 不一致 → 全量重跑、作废旧批次结论。
- 单写者：batches.json 只由本脚本写；cases JSON 的 testcases 由生成会话逐条写入，
  本脚本不写、不改 testcases；汇总门禁复用 gate_checks（只读判定）。

退出码：0=成功；1=判定/校验失败；2=用法/输入错误。
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL_DIR))
import gate_checks as gc  # noqa: E402
import review_bridge as rb  # noqa: E402

P_UNIT = re.compile(r"^M\d{2}(-FP-\d{2,})?$")
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_POLICY = SKILL_DIR.parent / "config" / "policy.json"


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_json(path, code=2):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"输入解析失败：{path}（{exc}）", file=sys.stderr)
        sys.exit(code)


def save_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_batches(workdir):
    path = Path(workdir) / "batches.json"
    if not path.is_file():
        print(f"未找到 {path}，请先运行 plan", file=sys.stderr)
        sys.exit(2)
    return load_json(path), path


def decide_split(modules, policy):
    th = policy.get("thresholds", {}).get("batch", {})
    split_over = th.get("split_over_cases", 30)
    single_below = th.get("single_batch_below_cases", 15)
    hint_over = th.get("module_count_hint_over", 5)
    total = sum(int(m.get("estimated_case_count") or 0) for m in modules)
    reasons = []
    if total > split_over:
        return "split", total, f"预估 {total} 条 > 分批阈值 {split_over}"
    if 0 < total <= single_below:
        return "single", total, f"预估 {total} 条 ≤ 免分批下限 {single_below}，一律单批"
    if total == 0:
        if len(modules) > hint_over:
            return "split", 0, f"预估缺失且模块数 {len(modules)} > {hint_over}，按模块分批（不猜数字）"
        return "single", 0, f"预估缺失且模块数 {len(modules)} ≤ {hint_over}，单批"
    reasons.append(f"预估 {total} 条介于 {single_below}~{split_over}，单批（模块数只决定切法）")
    return "single", total, reasons[-1]


def cmd_plan(args):
    policy = load_json(args.policy if args.policy else DEFAULT_POLICY)
    review = load_json(args.review)
    req = review.get("requirement") or {}
    modules = [m for m in review.get("modules") or [] if isinstance(m, dict)]
    if not modules:
        print("评审结果缺少 modules，先跑 review_check", file=sys.stderr)
        sys.exit(2)

    mode, total, reason = decide_split(modules, policy)
    feat_by_id = {str(f.get("feature_id")): f for f in review.get("features") or [] if isinstance(f, dict)}
    go_all = [f for f in review.get("go_ahead") or [] if f in feat_by_id]

    batches = []
    if mode == "split":
        for m in sorted(modules, key=lambda x: str(x.get("module_code"))):
            fids = sorted(f for f in go_all if feat_by_id[f].get("module_code") == m.get("module_code"))
            if fids:
                batches.append({
                    "unit_id": str(m.get("module_code")),
                    "module_code": str(m.get("module_code")),
                    "feature_ids": fids,
                    "status": "pending", "gate": "not_run", "case_ids": [],
                    "started_at": None, "finished_at": None, "gate_report": None,
                })
    else:
        batches.append({
            "unit_id": str(modules[0].get("module_code")),
            "module_code": str(modules[0].get("module_code")),
            "feature_ids": sorted(go_all),
            "status": "pending", "gate": "not_run", "case_ids": [],
            "started_at": None, "finished_at": None, "gate_report": None,
        })
    if not batches:
        print("无可生成范围（go_ahead 为空或全被 blocking 阻断）", file=sys.stderr)
        sys.exit(1)

    state = {
        "schema_version": "1.0",
        "requirement_fingerprint": str(req.get("fingerprint", "")),
        "task_id": args.task_id,
        "split_reason": reason,
        "batches": batches,
        "current": None,
    }
    workdir = Path(args.workdir)
    save_json(workdir / "batches.json", state)

    skeleton, scope = rb.build_skeleton(review, args.flow_kind)
    short = re.sub(r"[^\w-]", "", args.task_id)[-12:] or "task"
    cases_path = workdir / f"cases-{short}.json"
    scope_path = workdir / f"scope-{short}.json"
    save_json(cases_path, skeleton)
    save_json(scope_path, scope)
    print(f"分批判定：{reason}")
    print(f"batches.json -> {workdir / 'batches.json'}（{len(batches)} 批）")
    print(f"骨架 -> {cases_path}")
    print(f"范围 -> {scope_path}")
    sys.exit(0)


def cmd_resplit(args):
    state, path = load_batches(args.workdir)
    unit = next((b for b in state["batches"] if b.get("unit_id") == args.unit), None)
    if not unit:
        print(f"批次 {args.unit} 不存在", file=sys.stderr)
        sys.exit(2)
    if unit["status"] == "done" and unit["gate"] == "pass":
        print("该批已完成且通过，不得重拆", file=sys.stderr)
        sys.exit(1)
    fids = [f.strip() for f in args.feature_ids.split(",") if f.strip()]
    if len(fids) < 2:
        print("续拆至少给出 2 个一级功能点", file=sys.stderr)
        sys.exit(2)
    idx = state["batches"].index(unit)
    new_batches = []
    for fid in fids:
        new_batches.append({
            "unit_id": f"{unit['module_code']}-{fid}",
            "module_code": unit["module_code"],
            "feature_ids": [fid],
            "status": "pending", "gate": "not_run", "case_ids": [],
            "started_at": None, "finished_at": None, "gate_report": None,
        })
    state["batches"][idx:idx + 1] = new_batches
    state["split_reason"] = state.get("split_reason", "") + f"；{args.unit} 生成超限，按一级功能点续拆（已完成批不重跑）"
    save_json(path, state)
    print(f"{args.unit} 已续拆为 {len(fids)} 个子批")
    sys.exit(0)


def cmd_start(args):
    state, path = load_batches(args.workdir)
    if state.get("current") and state["current"] != args.unit:
        print(f"已有进行中的批次 {state['current']}，同会话串行，不并行开批", file=sys.stderr)
        sys.exit(1)
    unit = next((b for b in state["batches"] if b.get("unit_id") == args.unit), None)
    if not unit:
        print(f"批次 {args.unit} 不存在", file=sys.stderr)
        sys.exit(2)
    if unit["status"] != "pending":
        print(f"批次 {args.unit} 状态为 {unit['status']}，仅 pending 可开始", file=sys.stderr)
        sys.exit(1)
    unit["status"] = "doing"
    unit["started_at"] = now()
    state["current"] = args.unit
    save_json(path, state)
    print(f"批次 {args.unit} 开始（doing）")
    sys.exit(0)


def cmd_finish(args):
    state, path = load_batches(args.workdir)
    unit = next((b for b in state["batches"] if b.get("unit_id") == args.unit), None)
    if not unit:
        print(f"批次 {args.unit} 不存在", file=sys.stderr)
        sys.exit(2)
    if unit["status"] != "doing":
        print(f"批次 {args.unit} 状态为 {unit['status']}，仅 doing 可完成", file=sys.stderr)
        sys.exit(1)
    if args.gate not in {"pass", "fail", "waived"}:
        print("--gate 取值 pass|fail|waived", file=sys.stderr)
        sys.exit(2)
    cases = load_json(args.cases)
    batch_fids = set(unit.get("feature_ids") or [])
    cids = [tc.get("id") for tc in cases.get("testcases", [])
            if isinstance(tc, dict) and batch_fids & set(tc.get("feature_ids") or [])]
    unit["case_ids"] = sorted(cids)
    unit["gate"] = args.gate
    unit["gate_report"] = args.gate_report
    unit["status"] = "done"
    unit["finished_at"] = now()
    state["current"] = None
    save_json(path, state)
    print(f"批次 {args.unit} 完成：{len(cids)} 条用例，gate={args.gate}")
    sys.exit(0)


def cmd_status(args):
    state, _ = load_batches(args.workdir)
    print(f"task_id={state.get('task_id')} fingerprint={state.get('requirement_fingerprint', '')[:19]}… current={state.get('current')}")
    for b in state["batches"]:
        print(f"  {b['unit_id']:<14} {b['status']:<7} gate={b['gate']:<8} cases={len(b['case_ids'])} fids={len(b.get('feature_ids') or [])}")
    sys.exit(0)


def cmd_resume(args):
    state, path = load_batches(args.workdir)
    interrupted = []
    for b in state["batches"]:
        if (b["status"] in {"doing", "done"}) and not b.get("case_ids"):
            interrupted.append(b["unit_id"])
            b["status"] = "pending"
            b["started_at"] = None
            b["finished_at"] = None
            b["gate"] = "not_run"
    state["current"] = None
    save_json(path, state)
    nxt = next((b["unit_id"] for b in state["batches"] if b["status"] == "pending"), None)
    if interrupted:
        print(f"检测到中断批次（状态 doing/done 但 case_ids 为空），已重置：{interrupted}")
    else:
        print("无中断批次")
    print(f"下一个待办批次：{nxt or '（无，全部完成）'}")
    sys.exit(0)


def cmd_fingerprint(args):
    state, path = load_batches(args.workdir)
    review_path = Path(args.review) if args.review else None
    text = Path(args.requirement_text).read_bytes()
    digest = "sha256:" + hashlib.sha256(text).hexdigest()
    states = []
    if review_path:
        rv = load_json(review_path)
        states.append(("review-result", str((rv.get("requirement") or {}).get("fingerprint", ""))))
    states.append(("batches.json", str(state.get("requirement_fingerprint", ""))))
    mismatch = [name for name, fp in states if fp and fp != digest]
    if mismatch:
        for b in state["batches"]:
            b.update({"status": "pending", "gate": "not_run", "case_ids": [],
                      "started_at": None, "finished_at": None, "gate_report": None})
        state["current"] = None
        state["requirement_fingerprint"] = digest
        save_json(path, state)
        print(f"指纹变化（{digest[:19]}… 与 {', '.join(mismatch)} 不一致）：全量重跑，旧批次结论作废，批次已全部重置")
        sys.exit(1)
    print(f"指纹一致：{digest[:19]}…")
    sys.exit(0)


def cmd_aggregate(args):
    state, _ = load_batches(args.workdir)
    not_done = [b["unit_id"] for b in state["batches"] if b["status"] != "done"]
    if not_done:
        print(f"存在未完成批次 {not_done}，先完成再汇总", file=sys.stderr)
        sys.exit(1)
    failed = [b["unit_id"] for b in state["batches"] if b["gate"] == "fail"]
    if failed:
        print(f"批次 {failed} 门禁 fail，须修订重跑通过后才能汇总", file=sys.stderr)
        sys.exit(1)

    cases = load_json(args.cases)
    policy = load_json(args.policy if args.policy else DEFAULT_POLICY)
    report = gc.GateRunner(policy).run(cases)
    report["aggregate"] = True
    report["batches"] = [{k: b.get(k) for k in ("unit_id", "gate", "case_ids")} for b in state["batches"]]
    out = Path(args.workdir) / "aggregate-report.json"
    save_json(out, report)
    print(f"全量汇总门禁：{report['status']}（冒烟 {report['counts'].get('smoke')} / P0 {report['counts'].get('p0')} / 共 {report['counts'].get('testcases')}）")
    print(f"aggregate-report -> {out}")
    sys.exit(0 if report["status"] != "fail" else 1)


def main():
    ap = argparse.ArgumentParser(description="分批调度、批次状态、全量汇总")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan")
    p.add_argument("--review", required=True)
    p.add_argument("--workdir", required=True)
    p.add_argument("--task-id", required=True)
    p.add_argument("--flow-kind", choices=["simple", "workflow", "stateful"], default="simple")
    p.add_argument("--policy", default=None)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("resplit")
    p.add_argument("--workdir", required=True)
    p.add_argument("--unit", required=True)
    p.add_argument("--feature-ids", required=True, help="逗号分隔的一级功能点，如 FP-01,FP-02")
    p.set_defaults(func=cmd_resplit)

    p = sub.add_parser("start")
    p.add_argument("--workdir", required=True)
    p.add_argument("--unit", required=True)
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("finish")
    p.add_argument("--workdir", required=True)
    p.add_argument("--unit", required=True)
    p.add_argument("--cases", required=True)
    p.add_argument("--gate", required=True, choices=["pass", "fail", "waived"])
    p.add_argument("--gate-report", default=None)
    p.set_defaults(func=cmd_finish)

    p = sub.add_parser("status")
    p.add_argument("--workdir", required=True)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("resume")
    p.add_argument("--workdir", required=True)
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("fingerprint")
    p.add_argument("--workdir", required=True)
    p.add_argument("--requirement-text", required=True, help="需求正文文本文件")
    p.add_argument("--review", default=None)
    p.set_defaults(func=cmd_fingerprint)

    p = sub.add_parser("aggregate")
    p.add_argument("--workdir", required=True)
    p.add_argument("--cases", required=True)
    p.add_argument("--policy", default=None)
    p.set_defaults(func=cmd_aggregate)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()