"""对抗补充门禁：按 adversarial-plan、事件与复核记录计算结论上限的补充约束。

定位：exploration gate（scripts/exploration_state.py）约束批次结论上限；本脚本在其
之上叠加对抗维度——只读输入文件，不修改原 gate 数据，也不替代缺陷、断言与证据判定。

规则：
- plan 缺失或未登记任何 SURF/OBLIGATION（空 plan）：enforce=False（默认，向后兼容）
  时视为未启用对抗，放行但附注；enforce=True 时为必选对抗——无豁免理由（waiver）
  则上限"待核实"，附豁免理由（waiver）则放行并在 notes 留痕，**豁免不上抬上限：
  verdict_ceiling 最高仍为"待核实"**（与 execution_ledger._adversarial_precondition
  的"豁免不得自授即通过"原则对齐；豁免是否合资格——只读且小任务——由台账侧
  机械校验，本脚本不掌握 read_only/case_count，只留痕并输出 waiver_granted 标志）；
- 覆盖面缺口（高风险 SURF 未登记探针、探针未注明 method/playbook_ref）→ 上限
  "待核实"（依赖 adversarial_data.coverage_gaps；缺字段的旧 plan 按未注明处理）；
- 必选高风险 SURF（risk=high）无对应已执行探针（ADV status=executed 且存在指向
  该 ADV 的 EVT 事件）→ 上限"待核实"；
- 存在 accepted 且严重度为 blocker/major 的未闭环复核（tie_to_bug 为空）→ 上限"不通过"；
- 存在 verdict=unresolved 的候选 → 上限"待核实"（不计有效缺陷也不计误报）；
- 最终上限取基础 gate 上限与对抗上限中更严格者（通过 < 待核实 < 不通过）。

结论函数 evaluate(plan, events, reviews, base_ceiling=None, enforce=False,
waiver=None) 为纯函数，便于单测；
CLI：python adversarial_gate.py [--plan plan.json] [--events events.jsonl]
[--reviews reviews.jsonl] [--gate-summary summary.json] [--enforce]
[--waiver 豁免理由]，成功输出 JSON 结论并以 0 退出，输入/解析错误以 2 退出。
产出技术结论的批次默认应以 --enforce 运行；纯只读小任务用 --waiver 附理由豁免。
plan 读取复用 scripts/adversarial_data.py 的 load。
"""
import argparse
import json
from pathlib import Path
import sys

try:
    from adversarial_data import load as load_plan, coverage_gaps as plan_coverage_gaps, \
        scenario_gaps as plan_scenario_gaps
except ImportError:  # 直接脚本路径执行时回退同目录加载
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from adversarial_data import load as load_plan, coverage_gaps as plan_coverage_gaps, \
        scenario_gaps as plan_scenario_gaps

RANK = {"通过": 0, "待核实": 1, "不通过": 2}
RANK_NAME = {v: k for k, v in RANK.items()}
HIGH_SEVERITY = {"blocker", "major"}


def _strictest(a, b):
    return RANK_NAME[max(RANK.get(a, 0), RANK.get(b, 0))]


def evaluate(plan, events, reviews, base_ceiling=None, enforce=False, waiver=None, scenarios=None):
    """纯函数：返回对抗补充门禁结论（上限、启用标记、原因与统计）。

    enforce=True 时对抗循环为必选：空 plan 无豁免理由压上限"待核实"，
    附 waiver 理由则放行留痕，但**豁免不上抬上限**——ceiling 最高仍为
    "待核实"，与台账侧豁免语义一致。enforce=False 保持既有放行行为。
    scenarios 为对抗场景数组（SCN-ADV），缺省按未设计场景计入缺口。
    """
    reasons, notes = [], []
    waiver_granted = False
    surfaces = (plan or {}).get("surfaces") or []
    obligations = (plan or {}).get("obligations") or []
    events = list(events or [])
    reviews = list(reviews or [])

    if not surfaces and not obligations:
        if enforce and not waiver:
            return {
                "adversarial_enabled": False,
                "verdict_ceiling": _strictest("待核实", base_ceiling or "通过"),
                "reasons": ["对抗循环未启用：未登记任何风险面与对抗义务，且未提供豁免理由；"
                            "结论上限为待核实（对抗必选）"],
                "notes": [],
                "waiver_granted": False,
                "stats": {"probes": 0, "effective_bugs": 0, "false_positives": 0, "unresolved": 0,
                          "high_risk_surfaces": 0, "high_risk_uncovered": 0},
                "scope": "只读输入的对抗补充上限；不替代缺陷、断言与证据判定，报告结论不得高于本上限",
            }
        if enforce and waiver:
            # A1 修复（审核四）：豁免只解除"对抗循环未启用"的阻断，不上抬上限——
            # 与台账 "豁免不得自授即通过" 对齐；豁免是否合资格由台账机械校验。
            waiver_granted = True
            empty_ceiling = _strictest("待核实", base_ceiling or "通过")
            notes.append(f"对抗循环豁免：{waiver}（enforce 模式下凭豁免理由放行，豁免留痕；"
                         "豁免不上抬上限，verdict_ceiling 最高仍为待核实；豁免合资格与否"
                         "——只读且小任务——由 execution_ledger 台账侧机械校验）")
        else:
            # enforce=False 保持既有放行行为（向后兼容）；enforce=True 无豁免
            # 已在上分支提前返回，不会走到这里
            empty_ceiling = base_ceiling or "通过"
            notes.append("空 plan：未登记任何风险面与对抗义务，视为未启用对抗循环，仅按基础 gate 上限放行")
        return {
            "adversarial_enabled": False,
            "verdict_ceiling": empty_ceiling,
            "reasons": [],
            "notes": notes,
            "waiver_granted": waiver_granted,
            "stats": {"probes": 0, "effective_bugs": 0, "false_positives": 0, "unresolved": 0,
                      "high_risk_surfaces": 0, "high_risk_uncovered": 0},
        }

    events = [e for e in events if isinstance(e, dict)]
    reviews = [r for r in reviews if isinstance(r, dict)]
    plan_id = (plan or {}).get("plan_id", "")
    obligation_ids = {o.get("id") for o in obligations if isinstance(o, dict)}
    executed_by_surface = {}
    for obligation in obligations:
        if not isinstance(obligation, dict) or obligation.get("status") != "executed":
            continue
        adv_id = obligation.get("id")
        if any(e.get("probe_ref") == adv_id for e in events):
            executed_by_surface.setdefault(obligation.get("surface_ref"), set()).add(adv_id)

    high_risk = [s for s in surfaces if isinstance(s, dict) and s.get("risk") == "high"]
    uncovered = [s["id"] for s in high_risk if s.get("id") not in executed_by_surface]
    if uncovered:
        reasons.append("必选高风险风险面无对应已执行探针：" + "、".join(sorted(uncovered))
                       + "；结论上限为待核实（风险面不可用单条 PASS 抵消）")

    # 方法/模板覆盖面：计划层面就要有依据，不靠执行期才发现“只用了 challenge”
    plan_gaps = [gap for gap in plan_coverage_gaps(plan or {})
                 if "未登记任何风险面" not in gap]  # 空 plan 已在上方分支处理
    method_gaps = [gap for gap in plan_gaps if "method/playbook_ref" in gap]
    if method_gaps:
        reasons.append("探针未注明方法/挑刺模板：" + "；".join(method_gaps[:6])
                       + "；结论上限为待核实（无法核对方法与模板是否有效使用）")

    # 多样性缺口：方法单一/模板过窄同样视为未达成“全面”
    diversity_gaps = [gap for gap in plan_gaps
                      if "方法过于单一" in gap or "挑刺模板覆盖过窄" in gap]
    if diversity_gaps:
        reasons.append("方法/模板多样性不足：" + "；".join(diversity_gaps)
                       + "；结论上限为待核实（多样化方法未实际使用）")

    # 场景缺口：目标要求场景设计，零场景或只有单点不算全面
    sc_gaps = plan_scenario_gaps(plan or {}, scenarios)
    if sc_gaps:
        reasons.append("对抗场景缺口：" + "；".join(sc_gaps)
                       + "；结论上限为待核实（组合/序列场景未设计）")

    unresolved = [r.get("finding_id") for r in reviews
                  if r.get("verdict") == "unresolved"]
    if unresolved:
        reasons.append("存在未裁定候选：" + "、".join(sorted(str(x) for x in unresolved))
                       + "；不计有效缺陷也不计误报，结论上限为待核实")

    unclosed_blocker = [r.get("finding_id") for r in reviews
                        if r.get("verdict") == "accepted"
                        and r.get("severity") in HIGH_SEVERITY
                        and not r.get("tie_to_bug")]
    if unclosed_blocker:
        reasons.append("存在 accepted 且严重度为 blocker/major 的未闭环缺陷候选："
                       + "、".join(sorted(str(x) for x in unclosed_blocker)) + "；结论上限为不通过")

    adversarial_ceiling = "通过"
    for reason in reasons:
        if "不通过" in reason:
            adversarial_ceiling = "不通过"
            break
        if "待核实" in reason:
            adversarial_ceiling = "待核实"

    ceiling = _strictest(adversarial_ceiling, base_ceiling) if base_ceiling else adversarial_ceiling
    accepted = [r for r in reviews if r.get("verdict") == "accepted"]
    stats = {
        "plan_id": plan_id,
        "probes": sum(1 for o in obligations if isinstance(o, dict) and o.get("status") == "executed"),
        "events": len(events),
        "effective_bugs": sum(1 for r in accepted if r.get("tie_to_bug")),
        "false_positives": sum(1 for r in reviews if r.get("verdict") == "rejected"),
        "unresolved": len(unresolved),
        "high_risk_surfaces": len(high_risk),
        "high_risk_uncovered": len(uncovered),
        "obligations_without_method": sum(1 for o in obligations
                                          if isinstance(o, dict) and not o.get("method") and not o.get("playbook_ref")),
        "scenarios": len(scenarios or []),
        "scenario_gaps": len(sc_gaps),
    }
    return {
        "adversarial_enabled": True,
        "verdict_ceiling": ceiling,
        "reasons": reasons,
        "notes": notes,
        "waiver_granted": waiver_granted,
        "stats": stats,
        "scope": "只读输入的对抗补充上限；不替代缺陷、断言与证据判定，报告结论不得高于本上限",
    }


def load_jsonl(path):
    """逐行解析 JSONL；空行跳过，重复解析错误抛 ValueError，文件错误抛 OSError。"""
    records = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} 第 {number} 行解析失败: {exc}") from exc
    return records


def _base_ceiling_from_summary(summary):
    gate = (summary or {}).get("gate") or {}
    return gate.get("verdict_ceiling") or (summary or {}).get("verdict_ceiling")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="计算对抗补充门禁结论；只读输入，不修改原 gate 数据")
    parser.add_argument("--plan", help="adversarial-plan.json 路径（缺省视为未提供 plan）")
    parser.add_argument("--events", help="对抗事件 JSONL 路径")
    parser.add_argument("--reviews", help="候选复核 JSONL 路径")
    parser.add_argument("--gate-summary", help="exploration_state.py 摘要 JSON 路径，读取其 gate.verdict_ceiling 作为基础上限")
    parser.add_argument("--enforce", action="store_true", help="对抗必选模式：空 plan 无豁免理由时上限压待核实")
    parser.add_argument("--waiver", help="豁免理由（配合 --enforce 使用，豁免留痕）")
    parser.add_argument("--scenarios", help="对抗场景伴随文件（SCN-ADV 数组）；缺省按未设计场景计入缺口")
    args = parser.parse_args(argv)
    try:
        plan = load_plan(args.plan) if args.plan else None
        events = load_jsonl(args.events) if args.events else []
        reviews = load_jsonl(args.reviews) if args.reviews else []
        base = None
        if args.gate_summary:
            base = _base_ceiling_from_summary(json.loads(Path(args.gate_summary).read_text(encoding="utf-8-sig")))
            if base is None:
                raise ValueError("gate-summary 中未找到 gate.verdict_ceiling，无法读取基础上限")
        result = evaluate(plan, events, reviews, base, enforce=args.enforce, waiver=args.waiver,
                          scenarios=(json.loads(Path(args.scenarios).read_text(encoding="utf-8-sig"))
                                     if args.scenarios else None))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"对抗补充门禁输入/校验错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
