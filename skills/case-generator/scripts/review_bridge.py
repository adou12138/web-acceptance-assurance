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

对抗交接（IMP-07）：build_adversarial_handoff(review, negative_assertions=None) 把评审产物
映射为 adversarial-plan 的 ADV 草稿候选清单（不落 plan，由生成/执行阶段消费并冻结判据）：
- coverage_gaps[]（{area,reason}）→ 每条缺口一个 challenge 类 ADV 草稿；
- 评审未声明 coverage_gaps 时不报错，产出空 ADV 草稿清单并附提示（评审契约允许缺省）；
- 负面断言（--negative-input 提供的外部数组，评审 schema 根对象 additionalProperties:false
  不允许内嵌）每条必须写明副作用（side_effect）与授权要求（authorization），缺一即拒绝映射；
- 需求可疑点（--requirements-input 提供的外部数组，同因不入评审根对象）每条必须写明
  type（missing/conflict/hidden_assumption/business_sanity）、area 与 description，
  映射为 origin=requirement_issues 的 challenge 草稿；其产物只能归需求问题分账，
  不得直接判产品缺陷（见 references/oracle-contract.md 的 req_issue 纪律）；
- 产物只是候选草稿，判据（ORC）与风险面（SURF）须在 adversarial-plan 定稿时按
  专家包 references/oracle-contract.md 补齐并冻结来源指纹。
"""

import argparse
import copy
import json
import sys
from pathlib import Path

P_FP_OK = lambda s: isinstance(s, str) and s.startswith("FP-")


def translate_gaps(review_gaps):
    """评审缺口 {area,reason} → cases 缺口 {gap_id,kind,target,reason,obligation}（v3 G06）。

    评审阶段的 area 是自然语言描述，cases 契约要求稳定 ID 与可核对的补齐义务；
    这里做机械映射并保留原文，不丢失缺口也不能直接塞入非法结构。
    """
    out = []
    allowed = {"scene_unbound", "element_uncovered", "capability_unsupported",
               "method_unwitnessed", "budget_exceeded", "data_unavailable"}
    for i, gap in enumerate(review_gaps or [], 1):
        if not isinstance(gap, dict):
            continue
        if all(str(gap.get(k) or "").strip() for k in ("gap_id", "kind", "target", "reason", "obligation")):
            out.append(copy.deepcopy(gap))
            continue
        kind = str(gap.get("kind") or "scene_unbound")
        if kind not in allowed:
            kind = "scene_unbound"
        area = str(gap.get("area") or gap.get("target") or "评审缺口")
        out.append({
            "gap_id": str(gap.get("gap_id") or f"GAP-{i:02d}"),
            "kind": kind,
            "target": str(gap.get("target") or area),
            "reason": str(gap.get("reason") or ""),
            "obligation": str(gap.get("obligation") or f"补齐评审缺口：{area}（{gap.get('reason', '')}）"),
        })
    return out


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
        # v3 G06：保留 AC 原文证据引用；待确认状态降级为 provisional 并显式打标，
        # 不得用非法 confirmation 值破坏 case_data 契约，也不得冒充已确认。
        confirmation = str(ac.get("confirmation", "confirmed"))
        needs_confirmation = fid in pending_flagged or confirmation not in {"confirmed"}
        if needs_confirmation and confirmation not in {"provisional", "ambiguous"}:
            # 含非法值（如评审的 pending）一律归一为 case_data 允许的 provisional
            confirmation = "provisional"
        acceptance_out.append({
            "needs_confirmation": bool(needs_confirmation),
            "id": str(ac.get("acceptance_id")),
            "requirement_id": str(ac.get("requirement_id")),
            "description": oracle,
            "reason": f"评审阶段确认（来源功能点 {fid}）",
            "required": bool(ac.get("required")),
            "confirmation": confirmation,
            "evidence_refs": copy.deepcopy(ac.get("evidence_refs") or []),
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
    # v3 G06：评审的缺口、图片证据、未评审项与已确认规则逐跳保留，禁止在桥接处静默丢弃。
    # coverage_gaps 需从评审的 {area,reason} 机械翻译为 cases 的稳定 ID 结构，
    # 否则直接透传会触发 case_data 契约校验错误（缺口反而丢得更快）。
    if review.get("coverage_gaps") is not None:
        skeleton["coverage_gaps"] = translate_gaps(review["coverage_gaps"])
    for key in ("visual_evidence", "not_reviewed", "confirmed_rules"):
        if key in review and review[key] is not None:
            skeleton[key] = copy.deepcopy(review[key])
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


def build_adversarial_handoff(review, negative_assertions=None, requirement_issues=None):
    """评审产物 → adversarial-plan 的 ADV 草稿候选清单（IMP-07 + 需求批判）。

    覆盖缺口逐条映射为 challenge 草稿；负面断言必须写明副作用与授权要求，否则抛
    ValueError（CLI 转退出码 1）；需求可疑点必须含 type/area/description，type 限于
    missing/conflict/hidden_assumption/business_sanity。产物只是候选草稿：无 SURF/ORC
    关联，须在 plan 定稿时补判据并冻结指纹。
    """
    notes, drafts = [], []
    gaps = review.get("coverage_gaps")
    if gaps is None:
        notes.append("评审未声明 coverage_gaps：产出空 ADV 草稿清单；请补评审覆盖缺口或手工登记风险面后再定稿 plan")
        gaps = []
    if not isinstance(gaps, list):
        raise ValueError("coverage_gaps 必须为数组，请先运行 requirement-review 的 review_check.py")
    for i, gap in enumerate(gaps, 1):
        if not isinstance(gap, dict):
            raise ValueError(f"coverage_gaps[{i - 1}] 必须为对象（area/reason）")
        drafts.append({
            "adv_id": f"ADV-DRAFT-{i:02d}",
            "origin": "coverage_gaps",
            "target": str(gap.get("area") or ""),
            "hypothesis": str(gap.get("reason") or ""),
            "probe_type": "challenge",
            "expectation_basis": "",
            "side_effect": "",
            "authorization": "",
            "note": "预期依据待评审判据确认；自猜规则先记待核验，不得直接当 ORC 入账",
        })
    if drafts and any(not d["target"] or not d["hypothesis"] for d in drafts):
        notes.append("存在 area/reason 缺失的缺口草稿：预期依据须在 plan 定稿前补齐来源")

    for i, neg in enumerate(negative_assertions or [], 1):
        name = f"negative_assertions[{i - 1}]"
        if not isinstance(neg, dict):
            raise ValueError(f"{name} 必须为对象")
        text = str(neg.get("text") or "").strip()
        side_effect = str(neg.get("side_effect") or "").strip()
        authorization = str(neg.get("authorization") or "").strip()
        if not text:
            raise ValueError(f"{name}.text 缺失：负面断言必须写明禁止/拒绝的具体行为")
        if not side_effect:
            raise ValueError(f"{name}（{text}）缺副作用说明：负面断言必须写明触发后需核对的副作用，拒绝生成 ADV 草稿")
        if not authorization:
            raise ValueError(f"{name}（{text}）缺授权要求：负面断言必须写明所需授权边界，拒绝生成 ADV 草稿")
        drafts.append({
            "adv_id": f"ADV-DRAFT-{len(drafts) + 1:02d}",
            "origin": "negative_assertions",
            "target": text,
            "hypothesis": f"拒绝路径可能未生效或产生副作用：{side_effect}",
            "probe_type": "challenge",
            "expectation_basis": "负面断言（已确认规则）",
            "side_effect": side_effect,
            "authorization": authorization,
            "note": "",
        })

    drafts.extend(map_requirement_issues(requirement_issues, start_index=len(drafts) + 1))
    return {"adv_candidates": drafts, "notes": notes,
            "reminder": "候选草稿须按 references/oracle-contract.md 补 SURF/ORC 并冻结来源指纹后方可执行"}


REQ_ISSUE_TYPES = {"missing", "conflict", "hidden_assumption", "business_sanity"}
REQ_ISSUE_TYPE_NAMES = {
    "missing": "需求遗漏",
    "conflict": "需求冲突",
    "hidden_assumption": "隐含假设",
    "business_sanity": "业务合理性存疑",
}


def map_requirement_issues(requirement_issues, start_index=1):
    """需求可疑点数组 → origin=requirement_issues 的 ADV 草稿（批判需求本身）。

    判据来源记 req_issue：探针产物只能是需求问题候选或待业务确认事项，
    不得直接判产品缺陷（oracle-contract.md req_issue 纪律）。
    """
    drafts = []
    for i, issue in enumerate(requirement_issues or [], 1):
        name = f"requirement_issues[{i - 1}]"
        if not isinstance(issue, dict):
            raise ValueError(f"{name} 必须为对象")
        itype = str(issue.get("type") or "").strip()
        area = str(issue.get("area") or "").strip()
        description = str(issue.get("description") or "").strip()
        if itype not in REQ_ISSUE_TYPES:
            raise ValueError(f"{name}.type 必须为 {sorted(REQ_ISSUE_TYPES)} 之一，实为 {itype or '缺失'}")
        if not area:
            raise ValueError(f"{name}（{itype}）缺 area：需求可疑点必须写明涉及的功能点/规则位置")
        if not description:
            raise ValueError(f"{name}（{itype}）缺 description：必须写明可疑点内容与怀疑依据")
        drafts.append({
            "adv_id": f"ADV-DRAFT-{start_index + i - 1:02d}",
            "origin": "requirement_issues",
            "target": area,
            "hypothesis": f"[{REQ_ISSUE_TYPE_NAMES[itype]}] {description}",
            "probe_type": "challenge",
            "expectation_basis": "req_issue（需求可疑点）：探针产物归需求问题分账或待业务确认，不直接判产品缺陷",
            "side_effect": "只读挑战为主；涉及状态变更须先按负面断言口径补副作用与授权",
            "authorization": "",
            "note": f"需求可疑点编号 {str(issue.get('id') or f'RI-{i:02d}')}；澄清后须转业务确认，不得以需求原文单方判定",
        })
    return drafts


def main():
    ap = argparse.ArgumentParser(description="评审结果 → cases JSON 骨架映射；可选产出对抗交接候选清单")
    ap.add_argument("--review", required=True, help="review-result.json 绝对路径")
    ap.add_argument("--out", required=True, help="骨架 cases JSON 输出路径")
    ap.add_argument("--scope-out", help="范围清单 JSON 输出路径（缺省打印 stdout）")
    ap.add_argument("--flow-kind", choices=["simple", "workflow", "stateful"], default="simple")
    ap.add_argument("--adversarial-out", help="对抗交接候选清单 JSON 输出路径（缺省不产出）")
    ap.add_argument("--negative-input", help="负面断言数组 JSON 文件路径（每条须含 text/side_effect/authorization）")
    ap.add_argument("--requirements-input", help="需求可疑点数组 JSON 文件路径（每条须含 type/area/description）")
    args = ap.parse_args()

    review = _load(args.review)
    skeleton, scope = build_skeleton(review, args.flow_kind)

    if args.adversarial_out:
        negative = _load(args.negative_input) if args.negative_input else None
        if negative is not None and not isinstance(negative, list):
            _fail("--negative-input 必须为负面断言数组 JSON 文件")
        req_issues = _load(args.requirements_input) if args.requirements_input else None
        if req_issues is not None and not isinstance(req_issues, list):
            _fail("--requirements-input 必须为需求可疑点数组 JSON 文件")
        try:
            handoff = build_adversarial_handoff(review, negative, req_issues)
        except ValueError as exc:
            _fail(str(exc))
        adv_out = Path(args.adversarial_out)
        adv_out.parent.mkdir(parents=True, exist_ok=True)
        adv_out.write_text(json.dumps(handoff, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"adversarial handoff -> {adv_out}（ADV 草稿：{len(handoff['adv_candidates'])}）")

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