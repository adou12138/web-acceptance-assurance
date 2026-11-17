#!/usr/bin/env python3
"""review_bridge.py — 读评审结果，划可生成/待确认范围，并映射为 cases JSON 顶层实体。

映射规则（方案 A：cases JSON 为唯一事实源）：
- features（FUNC 层）按 module_code 分组，feature.id = module_code（M01…）；
- 只映射可生成范围（feature_id ∈ go_ahead 且未被 blocking 阻断）的 FP 与 AC；
- 每个 requirement 映射后仍须至少有一个 required:true 的 AC 落在可生成范围，
  否则显式失败（不静默生成非法骨架）；
- flow_kind 默认 simple；评审契约不产生场景实体，workflow/stateful 需在评审结果
  顶层扩展 scenarios（本脚本检测到后原样搬运），否则拒绝。
- testcases 由生成阶段（主会话）逐条写入，本脚本只写骨架与范围清单。

退出码：0=成功；1=映射失败（范围/契约问题）；2=用法/输入错误。
"""

import argparse
import json
import sys
from pathlib import Path

P_FP_OK = lambda s: isinstance(s, str) and s.startswith("FP-")


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"输入解析失败：{exc}", file=sys.stderr)
        sys.exit(2)


def _fail(msg, code=1):
    print(f"映射失败：{msg}", file=sys.stderr)
    sys.exit(code)


def build_skeleton(review, flow_kind):
    errors = []
    req = review.get("requirement") or {}
    reqs = review.get("requirements") or []
    feats = review.get("features") or []
    acs = review.get("acceptance") or []
    mods = review.get("modules") or []
    go_ahead = review.get("go_ahead") or []
    blocking = review.get("blocking") or []

    if not isinstance(reqs, list) or not reqs:
        errors.append("requirements 非空数组缺失")
    if not isinstance(feats, list) or not feats:
        errors.append("features 非空数组缺失")
    if not isinstance(acs, list) or not acs:
        errors.append("acceptance 非空数组缺失")
    if not isinstance(mods, list) or not mods:
        errors.append("modules 非空数组缺失")
    if errors:
        _fail("；".join(errors) + "。请先运行 requirement-review 的 review_check.py")

    blocked_fps = {str(r.get("feature_id")) for r in blocking if isinstance(r, dict)}
    go_set = [f for f in go_ahead if P_FP_OK(f) and f not in blocked_fps]
    go_all = set(go_set)
    pending_flagged = sorted({str(r.get("feature_id")) for r in review.get("pending") or [] if isinstance(r, dict)} & go_all)

    feat_by_id = {str(f.get("feature_id")): f for f in feats if isinstance(f, dict)}
    ac_by_feature = {}
    for ac in acs:
        if isinstance(ac, dict):
            ac_by_feature.setdefault(str(ac.get("feature_id")), []).append(ac)

    # 功能点 -> 所属需求（从 AC 反查）
    fp_reqs = {}
    for ac in acs:
        if isinstance(ac, dict):
            fp_reqs.setdefault(str(ac.get("feature_id")), set()).add(str(ac.get("requirement_id")))

    # FUNC 层：按模块分组；children 只放可生成范围 FP
    mod_by_code = {str(m.get("module_code")): m for m in mods if isinstance(m, dict)}
    features_out = []
    leaf_ids = set()
    for code, mod in sorted(mod_by_code.items()):
        children = []
        for fid in sorted(go_all):
            f = feat_by_id.get(fid)
            if not f or f.get("module_code") != code:
                continue
            rids = sorted(fp_reqs.get(fid, set()))
            if not rids:
                _fail(f"{fid} 没有任何验收标准（AC）可反查需求归属，评审结果不完整")
            children.append({
                "id": fid,
                "name": str(f.get("name", "")),
                "description": str(f.get("description", "")),
                "requirement_ids": rids,
            })
            leaf_ids.add(fid)
        if children:
            features_out.append({
                "id": code,
                "name": str(mod.get("name", code)),
                "description": f"{mod.get('name', code)}（评审模块 {code} 拆解的功能组）",
                "children": children,
            })
    if not features_out:
        _fail("可生成范围为空：go_ahead 为空或全部被 blocking 阻断，无功能点可进入骨架")

    # AC：只映射可生成范围；桥接补 case_data 必需的 description/reason/case_mode
    acceptance_out = []
    for ac in acs:
        if not isinstance(ac, dict):
            continue
        fid = str(ac.get("feature_id"))
        if fid not in leaf_ids:
            continue
        elements = ac.get("elements") or {}
        oracle = str(ac.get("oracle", ""))
        acceptance_out.append({
            "id": str(ac.get("acceptance_id")),
            "requirement_id": str(ac.get("requirement_id")),
            "description": oracle,
            "reason": f"评审阶段确认（来源功能点 {fid}）",
            "required": bool(ac.get("required")),
            "confirmation": str(ac.get("confirmation", "confirmed")),
            "oracle": oracle,
            "elements": {
                "scene": str(elements.get("scene", "")),
                "object": str(elements.get("object", "")),
                "condition": str(elements.get("condition", "")),
                "focus": str(elements.get("focus", "")),
            },
            "case_mode": "OR",
            "alternative_reason": "单条 AC 先按任一必要用例登记；必要时组合由生成阶段按 AND 补登",
            "case_ids": [],
        })

    # 每个 requirement 映射后仍须至少一个 required AC
    req_required_ok = {str(r.get("id")): False for r in reqs if isinstance(r, dict)}
    for ac in acceptance_out:
        if ac["required"]:
            req_required_ok[ac["requirement_id"]] = True
    for rid, ok in sorted(req_required_ok.items()):
        if not ok:
            _fail(f"{rid} 的全部 required AC 都不在可生成范围（被 blocking 阻断或未列 go_ahead）。"
                  f"需先解除 blocking 或调整 required AC 的 feature 归属，再重跑评审与映射")

    scenarios = review.get("scenarios")
    if flow_kind in {"workflow", "stateful"}:
        if not scenarios:
            _fail(f"flow_kind={flow_kind} 需要 scenarios 实体；评审契约不产生场景，请改用 --flow-kind simple 或在评审结果顶层扩展 scenarios")
    skeleton = {
        "meta": {
            "schema_version": "1.0",
            "id": str(req.get("id", "REQ-00")),
            "title": str(req.get("title", "")),
            "flow_kind": flow_kind,
        },
        "requirements": [
            {
                "id": str(r.get("id")),
                "name": str(r.get("name", "")),
                "source": str(r.get("source", "")),
                "description": str(r.get("description", "")),
                "confirmation": str(r.get("confirmation", "confirmed")),
            }
            for r in reqs if isinstance(r, dict)
        ],
        "features": features_out,
        "acceptance": acceptance_out,
        "testcases": [],
    }
    if flow_kind in {"workflow", "stateful"}:
        skeleton["scenarios"] = scenarios
    scope = {
        "go_ahead_effective": sorted(go_all),
        "pending_flagged": pending_flagged,
        "blocked": sorted(blocked_fps),
        "not_in_scope": sorted(set(feat_by_id) - go_all - blocked_fps),
        "leaf_ids": sorted(leaf_ids),
    }
    return skeleton, scope


def main():
    ap = argparse.ArgumentParser(description="评审结果 → cases JSON 骨架映射")
    ap.add_argument("--review", required=True, help="review-result.json 绝对路径")
    ap.add_argument("--out", required=True, help="骨架 cases JSON 输出路径")
    ap.add_argument("--scope-out", help="范围清单 JSON 输出路径（缺省打印 stdout）")
    ap.add_argument("--flow-kind", choices=["simple", "workflow", "stateful"], default="simple")
    args = ap.parse_args()

    review = _load(args.review)
    skeleton, scope = build_skeleton(review, args.flow_kind)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(skeleton, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.scope_out:
        sp = Path(args.scope_out)
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(scope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"scope -> {sp}")
    print(f"skeleton -> {out}")
    print(f"可生成 FP：{len(scope['go_ahead_effective'])}；待确认标记：{len(scope['pending_flagged'])}；"
          f"阻断：{len(scope['blocked'])}；范围外：{len(scope['not_in_scope'])}；AC 映射：{len(skeleton['acceptance'])}")
    sys.exit(0)


if __name__ == "__main__":
    main()