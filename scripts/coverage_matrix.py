#!/usr/bin/env python3
"""coverage_matrix.py — 四层覆盖矩阵（只读，不判定用例内容质量）。

判据来源（两处必须一致）：
- `skills/case-generator/references/methodology/testcase_design_spec.md` §5「四层覆盖深度检查」
- `skills/case-generator/config/policy.json` 的 `structure.coverage_layers`

机械判据（不做语义猜测，无法判定的一律记缺口而不是记通过）：
- **L1 主流程**：每个功能点（`features[].children[].id`，以及只在 `testcases[].feature_ids` 出现但未登记的 id）
  至少一条 `coverage_flags` 含 `L1` 的用例。
- **L2 业务分层**：`applicability.functional.state == applicable` 时，每个功能点至少一条含 `L2` 的用例；
  非 applicable 时记 `not_required` 并回写该状态与理由（不得默认判覆盖）。
- **L3 设计方法**：`method_evidence` 中有实际见证的方法数 ≥ `min_witnessed_methods`（policy 可配，默认 2）；
  每条声明的方法必须有见证，否则进 `declared_without_evidence`。
- **L4 探索性**：`suites` 含 `exploratory` 的用例数与占比，只统计不要求达标（`counts_as_covered=false`），
  超 `budget_policy.exploratory_ratio_limit` 记 warn。

`missing_layers` 只含 L1/L2/L3；L4 不进入该列表。

用法：
    python scripts/coverage_matrix.py --cases <cases JSON> [--policy <policy.json>] [--out <json>] [--json]
退出码：0=矩阵生成且 missing_layers 为空；1=存在未覆盖层；2=用法/输入错误。
"""

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

DEFAULT_POLICY = (Path(__file__).resolve().parents[1] / "skills" / "case-generator"
                  / "config" / "policy.json")
WITNESS_REQUIRED_LAYERS = ("L1", "L2", "L3")
# 已登记缺口按类别回落到对应层：功能点缺失压 L1，元素/边界缺失压 L2，方法无见证压 L3。
GAP_LAYER = {"feature_uncovered": "L1", "element_uncovered": "L2", "method_unwitnessed": "L3"}


def _flags(case):
    return {str(v) for v in (case.get("coverage_flags") or []) if str(v).strip()}


def _non_empty(value):
    if value is None:
        return False
    if isinstance(value, (str, list, dict, tuple, set)):
        return len(value) > 0
    return True


# 分类型方法见证校验（v3 G04）：布尔/字符串/占位不得充当见证。
def _witness_valid(method, witness):
    if isinstance(witness, bool) or not isinstance(witness, (dict, list)):
        return False
    if method == "boundary":
        # 需具体边界集合：上下界或越界值至少两处具体取值
        vals = witness if isinstance(witness, list) else (
            witness.get("values") or witness.get("boundaries") or witness.get("boundary_sets")
            or witness.get("sets"))
        if isinstance(vals, dict):
            # 边界集以命名集合给出（empty_set/single/max/over…）：至少两个具名集合
            return len([k for k, v in vals.items() if v not in (None, "", [], {})]) >= 2
        return isinstance(vals, list) and len(vals) >= 2 and all(
            isinstance(v, (str, int, float)) and str(v).strip() for v in vals)
    if method in ("scenario", "state_transition"):
        # 需真实边：source/target/trigger 或 path/edge 描述
        if isinstance(witness, list):
            return len(witness) >= 1 and all(isinstance(x, dict) for x in witness)
        edge = any(k in witness for k in ("source", "from", "target", "to", "trigger", "edge", "path"))
        return edge and len(witness) >= 2
    if method == "equivalence":
        if not isinstance(witness, dict):
            return False
        classes = witness.get("valid") or witness.get("invalid") or witness.get("classes")
        return isinstance(classes, list) and len(classes) >= 1
    if method == "decision_table":
        if not isinstance(witness, dict):
            return False
        cond = witness.get("conditions") or witness.get("condition")
        act = witness.get("actions") or witness.get("action")
        return bool(cond) and bool(act)
    # 其它方法（含 pairwise）由专门门禁处理，这里只要求结构化非空
    return _non_empty(witness)


def _layer_cfg(policy, layer_id):
    for layer in ((policy.get("structure") or {}).get("coverage_layers") or []):
        if layer.get("id") == layer_id:
            return layer
    return {}


def collect_points(cases):
    """功能点全集：先取登记的功能点，再补用例里出现但未登记的 id（未登记本身就是缺口）。"""
    points = []
    seen = set()
    for module in cases.get("features") or []:
        if not isinstance(module, dict):
            continue
        for child in module.get("children") or []:
            if not isinstance(child, dict) or not child.get("id"):
                continue
            pid = str(child["id"])
            if pid in seen:
                continue
            seen.add(pid)
            points.append({"id": pid, "name": child.get("name", ""), "module": module.get("id", ""),
                           "registered": True})
    for case in cases.get("testcases") or []:
        if not isinstance(case, dict):
            continue
        for fid in case.get("feature_ids") or []:
            fid = str(fid)
            if fid in seen:
                continue
            seen.add(fid)
            points.append({"id": fid, "name": "", "module": "", "registered": False})
    return points


def _layer_l1(cases, points):
    cases_by_point = {}
    for case in cases.get("testcases") or []:
        if not isinstance(case, dict):
            continue
        for fid in case.get("feature_ids") or []:
            cases_by_point.setdefault(str(fid), []).append(case)
    covered, uncovered, unregistered = [], [], []
    for point in points:
        related = cases_by_point.get(point["id"], [])
        hit = [c.get("id") for c in related if "L1" in _flags(c)]
        if hit:
            covered.append({"feature": point["id"], "cases": hit})
        else:
            uncovered.append(point["id"])
            if not point.get("registered"):
                unregistered.append(point["id"])
    return {
        "covered": not uncovered,
        "detail": {"covered_features": covered, "uncovered_features": uncovered,
                   "unregistered_features": unregistered},
    }


def _layer_l2(cases, points):
    functional = ((cases.get("applicability") or {}).get("functional") or {})
    state = str(functional.get("state") or "unspecified")
    reason = str(functional.get("reason") or "")
    cases_by_point = {}
    for case in cases.get("testcases") or []:
        if not isinstance(case, dict):
            continue
        for fid in case.get("feature_ids") or []:
            cases_by_point.setdefault(str(fid), []).append(case)
    if state != "applicable":
        # v3 G03：只有明确 not_applicable 才剔除分母；deferred/insufficient_evidence/unspecified
        # 是“未确认”，必须作为未覆盖缺口保留，不得记为 not_required。
        if state == "not_applicable":
            return {
                "covered": None,
                "not_required": True,
                "detail": {"functional_state": state, "reason": reason,
                           "note": "functional 明确 not_applicable，L2 剔除分母；须有理由"},
            }
        return {
            "covered": False,
            "not_required": False,
            "detail": {"functional_state": state, "reason": reason,
                       "uncovered_features": [p["id"] for p in points],
                       "note": "functional 证据不足/暂缓/未裁定，L2 保持未覆盖缺口，不得记不要求"},
        }
    covered, uncovered = [], []
    for point in points:
        hit = [c.get("id") for c in cases_by_point.get(point["id"], []) if "L2" in _flags(c)]
        if hit:
            covered.append({"feature": point["id"], "cases": hit})
        else:
            uncovered.append(point["id"])
    return {"covered": not uncovered, "detail": {"functional_state": state,
                                                 "covered_features": covered,
                                                 "uncovered_features": uncovered}}


def _layer_l3(cases, policy):
    cfg = _layer_cfg(policy, "L3")
    minimum = int(cfg.get("min_witnessed_methods") or 2)
    method_ids = {str(m.get("id")) for m in ((policy.get("structure") or {}).get("design_methods") or [])}
    witnessed, declared_without_evidence = {}, []
    for case in cases.get("testcases") or []:
        if not isinstance(case, dict):
            continue
        evidence = case.get("method_evidence")
        if not isinstance(evidence, dict):
            continue
        for method, witness in evidence.items():
            if method_ids and str(method) not in method_ids:
                continue
            if _witness_valid(str(method), witness):
                witnessed.setdefault(str(method), []).append(case.get("id"))
            else:
                declared_without_evidence.append({"case_id": case.get("id"), "method": str(method)})
    return {
        "covered": len(witnessed) >= minimum and not declared_without_evidence,
        "count": len(witnessed),
        "detail": {"min_witnessed_methods": minimum, "witnessed": witnessed,
                   "methods": sorted(witnessed),
                   "declared_without_evidence": declared_without_evidence},
    }


def _layer_l4(cases, policy):
    limit = float(((policy.get("structure") or {}).get("budget_policy") or {})
                  .get("exploratory_ratio_limit") or 0.2)
    cases_all = [c for c in (cases.get("testcases") or []) if isinstance(c, dict)]
    exploratory = [c.get("id") for c in cases_all if "exploratory" in set(c.get("suites") or [])]
    ratio = (len(exploratory) / len(cases_all)) if cases_all else 0.0
    return {
        "covered": None,
        "counts_as_covered": False,
        "count": len(exploratory),
        "ratio": round(ratio, 4),
        "warn": ratio > limit,
        "detail": {"exploratory_ratio_limit": limit, "cases": exploratory,
                   "note": "探索性只统计，不计入已确认规则覆盖"},
    }


def _obligation_overlay(cases, layers):
    """v3 G02：若声明 coverage_obligations，则以“义务→TC 引用”重算 L1/L2，flags 仅作派生展示。

    返回 (unmet_obligations, overlay_layers)。无义务声明时返回 ([], {})，保持既有 flags 口径。
    """
    obligations = cases.get("coverage_obligations")
    if not isinstance(obligations, list) or not obligations:
        return [], {}
    case_ids = {str(c.get("id")) for c in (cases.get("testcases") or []) if isinstance(c, dict)}
    # 绑定必须与用例声明的层一致：用例把 L1 标记删掉后，L1 义务不能靠旧引用继续算覆盖。
    layer_flags = {}
    for case in (cases.get("testcases") or []):
        if isinstance(case, dict):
            flags = case.get("coverage_flags")
            layer_flags[str(case.get("id"))] = set(flags) if isinstance(flags, list) else None
    unmet = []
    by_layer = {"L1": [], "L2": []}
    for i, ob in enumerate(obligations):
        if not isinstance(ob, dict):
            continue
        lid = str(ob.get("layer") or "")
        if lid not in by_layer:
            continue
        ref = [str(x) for x in (ob.get("tc_ids") or []) if str(x).strip()]
        bound = [x for x in ref if x in case_ids and (layer_flags.get(x) is None or lid in layer_flags.get(x, set()))]
        entry = {"id": str(ob.get("id") or f"OBL-{i}"), "layer": lid,
                 "kind": str(ob.get("kind") or ""), "target": str(ob.get("target") or ""),
                 "source": str(ob.get("source") or ""), "required": bool(ob.get("required")),
                 "tc_ids": bound}
        by_layer[lid].append(entry)
        if not bound:
            unmet.append(dict(entry, reason="义务未绑定任何有效用例（仅改 flags 不产生覆盖）"))
    overlay = {}
    functional_state = str(((cases.get("applicability") or {}).get("functional") or {}).get("state") or "")
    for lid in ("L1", "L2"):
        if not by_layer[lid]:
            continue
        if layers[lid].get("not_required"):
            # 适用性已裁定该层不要求（如 functional=not_applicable），义务不得反过来把层拉回要求态。
            continue
        if lid == "L2" and functional_state and functional_state != "applicable":
            # 功能适用性未定（insufficient_evidence/deferred）时，L2 保持未知，
            # 不能因为写了义务就把"证据不足"改写成"已覆盖"。
            continue
        required = [o for o in by_layer[lid] if o["required"]]
        covered = [o for o in required if o["tc_ids"]]
        uncovered = [o["id"] for o in required if not o["tc_ids"]]
        detail = dict(layers[lid].get("detail") or {})
        detail["obligations_required"] = [o["id"] for o in required]
        detail["obligations_covered"] = [o["id"] for o in covered]
        detail["obligations_uncovered"] = uncovered
        # 义务不足以覆盖"整块没测"：按标记统计出的未覆盖功能点仍要压住该层，
        # 否则删掉某功能点的用例与义务后还能判已覆盖。
        leftover = detail.get("uncovered_features") or []
        overlay[lid] = {"covered": bool(required) and not uncovered and not leftover,
                        "not_required": False, "detail": detail,
                        "basis": "coverage_obligations"}
    return unmet, overlay


def _gap_overlay(cases, layers):
    """已登记覆盖缺口参与判定（v3 G06 判定侧）。

    缺口只要没有关闭证据（closed_by 或 status=closed），就按其类别压回对应层，
    避免"评审已登记缺口、矩阵却当没发生"。返回 (open_gaps, overlay_layers)。
    """
    gaps = cases.get("coverage_gaps")
    if not isinstance(gaps, list) or not gaps:
        return [], {}
    open_gaps, by_layer = [], {}
    for i, gap in enumerate(gaps):
        if not isinstance(gap, dict):
            continue
        gid = str(gap.get("gap_id") or f"GAP-{i}")
        status = str(gap.get("status") or "open")
        closed = status == "closed" or (isinstance(gap.get("closed_by"), dict) and bool(gap["closed_by"]))
        if closed:
            continue
        layer = GAP_LAYER.get(str(gap.get("kind") or ""))
        entry = {"gap_id": gid, "kind": str(gap.get("kind") or ""), "layer": layer,
                 "target": str(gap.get("target") or ""), "reason": str(gap.get("reason") or ""),
                 "state": status}
        open_gaps.append(entry)
        if layer:
            by_layer.setdefault(layer, []).append(entry)
    overlay = {}
    for lid, items in by_layer.items():
        payload = dict(layers[lid])
        detail = dict(payload.get("detail") or {})
        detail["unresolved_gaps"] = [g["gap_id"] for g in items]
        payload["detail"] = detail
        payload["gap_blocked"] = True
        if isinstance(payload.get("covered"), bool):
            payload["covered"] = False
        overlay[lid] = payload
    return open_gaps, overlay


def build_matrix(cases, policy=None, source=None):
    """返回矩阵 dict；不写文件、不改入参。"""
    policy = policy or {}
    points = collect_points(cases)
    layers = {
        "L1": _layer_l1(cases, points),
        "L2": _layer_l2(cases, points),
        "L3": _layer_l3(cases, policy),
        "L4": _layer_l4(cases, policy),
    }
    unmet_obligations, overlay = _obligation_overlay(cases, layers)
    for lid, payload in overlay.items():
        layers[lid] = payload
    open_gaps, gap_overlay = _gap_overlay(cases, layers)
    for lid, payload in gap_overlay.items():
        layers[lid] = payload
    for layer_id, payload in layers.items():
        cfg = _layer_cfg(policy, layer_id)
        payload["name"] = cfg.get("name", layer_id)
        payload["requirement"] = cfg.get("requirement", "")
        payload["implementation"] = cfg.get("implementation", "unknown")
        payload["judged_by"] = cfg.get("judged_by", "")
    missing = [lid for lid in WITNESS_REQUIRED_LAYERS if layers[lid].get("covered") is False]
    gaps = []
    for fid in (layers["L1"]["detail"].get("uncovered_features") or []):
        gaps.append({"kind": "feature_uncovered", "layer": "L1", "target": fid,
                     "reason": "该功能点没有任何声明 L1 的用例"})
    for fid in (layers["L2"]["detail"].get("uncovered_features") or []):
        gaps.append({"kind": "element_uncovered", "layer": "L2", "target": fid,
                     "reason": "functional 适用但该功能点无声明 L2 的用例"})
    for item in (layers["L3"]["detail"].get("declared_without_evidence") or []):
        gaps.append({"kind": "method_unwitnessed", "layer": "L3",
                     "target": f"{item['case_id']}.{item['method']}",
                     "reason": "方法只有名字或布尔/字符串占位，没有符合契约的见证"})
    for ob in unmet_obligations:
        gaps.append({"kind": "obligation_unbound", "layer": ob["layer"],
                     "target": ob["id"], "reason": ob["reason"]})
    for gap in open_gaps:
        gaps.append({"kind": gap["kind"] or "registered_gap", "layer": gap["layer"],
                     "target": gap["target"], "reason": gap["reason"],
                     "gap_id": gap["gap_id"], "state": gap["state"],
                     "source": "coverage_gaps"})
    return {
        "generated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": str(source) if source else "",
        "applicability_declared": bool(cases.get("applicability")),
        "counts": {
            "testcases": len([c for c in (cases.get("testcases") or []) if isinstance(c, dict)]),
            "features": len(points),
            "methods_witnessed": layers["L3"].get("count", 0),
            "exploratory": layers["L4"].get("count", 0),
        },
        "layer_matrix": layers,
        "missing_layers": missing,
        "gaps": gaps,
        "unmet_obligations": unmet_obligations,
        "open_registered_gaps": open_gaps,
    }


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="四层覆盖矩阵（只读）")
    ap.add_argument("--cases", required=True, help="cases-<short>.json 路径")
    ap.add_argument("--policy", default=str(DEFAULT_POLICY), help="policy.json（默认包内）")
    ap.add_argument("--out", help="输出 coverage-matrix.json 路径（缺省只打印摘要）")
    ap.add_argument("--json", action="store_true", help="把完整矩阵打印到标准输出")
    args = ap.parse_args(argv)
    try:
        cases = load_json(args.cases)
        policy = load_json(args.policy)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"输入解析失败：{exc}", file=sys.stderr)
        return 2
    matrix = build_matrix(cases, policy, source=args.cases)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(matrix, ensure_ascii=False, indent=2))
    else:
        layers = matrix["layer_matrix"]
        brief = {}
        for lid, payload in layers.items():
            if payload.get("covered") is True:
                brief[lid] = "covered"
            elif payload.get("not_required"):
                brief[lid] = "not_required"
            elif payload.get("counts_as_covered") is False:
                brief[lid] = "info"
            else:
                brief[lid] = "missing"
        print(json.dumps({"missing_layers": matrix["missing_layers"], "layers": brief,
                          "gaps": len(matrix["gaps"]), "out": args.out or ""},
                         ensure_ascii=False))
    return 0 if not matrix["missing_layers"] else 1


if __name__ == "__main__":
    sys.exit(main())
