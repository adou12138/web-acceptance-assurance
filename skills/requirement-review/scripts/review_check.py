#!/usr/bin/env python3
"""requirement-review 评审判定器（只报不改）。

校验 review-result.json 的结构契约、引用完整性、ID 规则、四要素/空话/占位，
以及「每个 requirement 至少一个 required:true 的 AC」这一 case_data 硬校验的等价前置。
不评分、不评级、不自动修订评审结论。

退出码：0=通过（strict 下无 warning 才通过）；1=校验失败；2=用法/输入/依赖错误。
词表权威源：专家包根 scripts/case_data.py（同包相对引用，运行时不读取任何外部 skill）。
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
try:
    import case_data as cd
except Exception as exc:  # noqa: BLE001
    print(f"工具阻塞：无法加载专家包校验器 scripts/case_data.py（{exc}）。报告阻塞，不手工编造词表。", file=sys.stderr)
    sys.exit(2)

SCHEMA_VERSION = "1.0"
CONFIRMATIONS = {"confirmed", "provisional", "ambiguous"}
DIMENSIONS = {
    "completeness", "consistency", "clarity", "testability",
    "performance_security", "state_coverage", "acceptance_mapping",
    "historical_consistency",
}
TOP_KEYS = [
    "schema_version", "requirement", "requirements", "features", "acceptance",
    "modules", "confirmed_rules", "blocking", "pending", "go_ahead",
    "coverage_gaps", "visual_evidence", "not_reviewed", "scenarios",
]
VISUAL_TYPES = {"ui_mockup", "flowchart", "state_diagram", "table_rule",
                "annotated_screenshot", "api_snapshot", "report_chart", "unknown"}
# 顶层必填键 = 允许键 - 可选键；visual_evidence 无原型图时不应强制出现
OPTIONAL_TOP_KEYS = {"visual_evidence", "scenarios"}
P_REQ = re.compile(r"^REQ-\d{2,}$")
P_FP = re.compile(r"^FP-\d{2,}$")
P_AC = re.compile(r"^AC-\d{2,}$")
P_MOD = re.compile(r"^M\d{2}$")
P_RULE = re.compile(r"^R-\d{2,}$")
P_ISSUE_B = re.compile(r"^B-\d{2,}$")
P_ISSUE_P = re.compile(r"^P-\d{2,}$")
# 需求可疑点（批判需求本身）：声明值 none 表示经审阅确认需求无疑点
REQ_ISSUE_TYPES = {"missing", "conflict", "hidden_assumption", "business_sanity"}
REQ_ISSUE_NONE = "none"
P_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
P_TZ = re.compile(r"(Z|[+-]\d{2}:\d{2})$")
SCORE_KEYS = re.compile(r"score|grade|weight|percent|rating", re.IGNORECASE)


def _is_str(v):
    return isinstance(v, str) and v.strip() != ""


def _err(errors, msg):
    errors.append(msg)


def _warn(warnings, msg):
    warnings.append(msg)


def check_requirement_issues(issues, migration_warn=False):
    """校验需求可疑点清单（requirement-issues.json 的数组）。

    列表可为空但必须显式声明：空列表 = 已审阅且无疑点。每条须含 type/area/description，
    type 限于四类。返回 (errors, warnings)。

    migration_warn=True 时，"未声明"降级为 warning（迁移期口径）：避免既有评审资产
    集体 fail 引发绕过检查的逆向选择；该模式不应用于新批次。
    """
    errors, warnings = [], []
    if issues is None:
        msg = ("requirement-issues: 未声明需求可疑点。批判者立场须显式声明——"
               "请提供 requirement-issues.json（空数组表示已审阅且无疑点），"
               "不得默认视为“需求无问题”")
        if migration_warn:
            _warn(warnings, msg + "（迁移期按 warning 处理，新批次须补齐）")
        else:
            _err(errors, msg)
        return errors, warnings
    if not isinstance(issues, list):
        _err(errors, "requirement-issues: 必须为数组（空数组表示已审阅且无疑点）")
        return errors, warnings
    for i, item in enumerate(issues):
        name = f"requirement-issues[{i}]"
        if not isinstance(item, dict):
            _err(errors, f"{name}: object required")
            continue
        itype = str(item.get("type") or "")
        if itype not in REQ_ISSUE_TYPES:
            _err(errors, f"{name}.type: 必须为 {sorted(REQ_ISSUE_TYPES)} 之一（实为 {itype or '缺失'}）")
        for key in ("area", "description"):
            if not _is_str(item.get(key)):
                _err(errors, f"{name}.{key}: nonempty string required")
    return errors, warnings


def check(data):
    errors, warnings = [], []
    if not isinstance(data, dict):
        return ["root must be object"], []

    for key in TOP_KEYS:
        if key in OPTIONAL_TOP_KEYS:
            continue
        if key not in data:
            _err(errors, f"missing top-level key {key}")
    unknown = sorted(set(data) - set(TOP_KEYS))
    if unknown:
        _err(errors, f"unknown top-level keys {unknown}")
    if data.get("schema_version") != SCHEMA_VERSION:
        _err(errors, f"schema_version: expected {SCHEMA_VERSION}")

    # 评分泄漏自查：任何嵌套键命中 score/grade/weight → 本契约禁止
    def _scan_score(obj, loc):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if SCORE_KEYS.search(str(k)):
                    _err(errors, f"{loc}.{k}: 评分字段不进入本契约（qualitative_only）")
                _scan_score(v, f"{loc}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _scan_score(v, f"{loc}[{i}]")

    _scan_score(data, "root")

    req = data.get("requirement")
    if isinstance(req, dict):
        for key in ("id", "title", "version", "fingerprint", "reviewed_at"):
            if not _is_str(req.get(key)):
                _err(errors, f"requirement.{key}: nonempty string required")
        rid = str(req.get("id") or "")
        if rid and not P_REQ.match(rid):
            _err(errors, f"requirement.id: expected ^REQ-\\d{{2,}}$, got {rid!r}")
        fp = str(req.get("fingerprint") or "")
        if fp and not P_FINGERPRINT.match(fp):
            _err(errors, "requirement.fingerprint: expected sha256:<64 hex>")
        ts = str(req.get("reviewed_at") or "")
        if ts and not P_TZ.search(ts):
            _err(errors, "requirement.reviewed_at: 需带时区（Z 或 ±HH:MM）")
    else:
        _err(errors, "requirement: object required")

    # requirements
    reqs = {}
    rows = data.get("requirements")
    if not isinstance(rows, list) or not rows:
        _err(errors, "requirements: nonempty array required")
        rows = []
    for i, row in enumerate(rows):
        loc = f"requirements[{i}]"
        if not isinstance(row, dict):
            _err(errors, f"{loc}: object required")
            continue
        for key in ("id", "name", "source", "description", "confirmation"):
            if not _is_str(row.get(key)):
                _err(errors, f"{loc}.{key}: nonempty string required")
        rid = str(row.get("id") or "")
        if rid:
            if not P_REQ.match(rid):
                _err(errors, f"{loc}.id: invalid pattern {rid!r}")
            if rid in reqs:
                _err(errors, f"duplicate id {rid}: requirements")
            reqs[rid] = row
        if row.get("confirmation") not in CONFIRMATIONS:
            _err(errors, f"{loc}.confirmation: invalid value")
        if row.get("confirmation") != "confirmed":
            _warn(warnings, f"{rid or loc}: confirmation 非 confirmed，相关断言按待确认处理")

    # features
    feats = {}
    feat_module = {}
    rows = data.get("features")
    if not isinstance(rows, list) or not rows:
        _err(errors, "features: nonempty array required")
        rows = []
    for i, row in enumerate(rows):
        loc = f"features[{i}]"
        if not isinstance(row, dict):
            _err(errors, f"{loc}: object required")
            continue
        for key in ("feature_id", "module_code", "name", "description", "source"):
            if not _is_str(row.get(key)):
                _err(errors, f"{loc}.{key}: nonempty string required")
        fid = str(row.get("feature_id") or "")
        if fid:
            if not P_FP.match(fid):
                _err(errors, f"{loc}.feature_id: invalid pattern {fid!r}")
            if fid in feats:
                _err(errors, f"duplicate id {fid}: features")
            feats[fid] = row
        mod = str(row.get("module_code") or "")
        if mod:
            if not P_MOD.match(mod):
                _err(errors, f"{loc}.module_code: invalid pattern {mod!r}")
            feat_module[fid] = mod

    # modules
    mods = {}
    rows = data.get("modules")
    if not isinstance(rows, list) or not rows:
        _err(errors, "modules: nonempty array required")
        rows = []
    for i, row in enumerate(rows):
        loc = f"modules[{i}]"
        if not isinstance(row, dict):
            _err(errors, f"{loc}: object required")
            continue
        for key in ("module_code", "name"):
            if not _is_str(row.get(key)):
                _err(errors, f"{loc}.{key}: nonempty string required")
        mod = str(row.get("module_code") or "")
        fids = row.get("feature_ids")
        if not isinstance(fids, list) or not fids or any(not isinstance(x, str) for x in fids):
            _err(errors, f"{loc}.feature_ids: nonempty string array required")
            fids = []
        est = row.get("estimated_case_count")
        if not isinstance(est, int) or isinstance(est, bool) or est < 0:
            _err(errors, f"{loc}.estimated_case_count: non-negative integer required")
        if mod:
            if not P_MOD.match(mod):
                _err(errors, f"{loc}.module_code: invalid pattern {mod!r}")
            if mod in mods:
                _err(errors, f"duplicate module_code {mod}")
            mods[mod] = row
        for fid in fids:
            if fid not in feats:
                _err(errors, f"{loc}.feature_ids: unknown reference {fid}")
            elif feat_module.get(fid) != mod:
                _err(errors, f"{fid}.module_code {feat_module.get(fid)!r} 与所属模块 {mod!r} 不一致")
    for fid, mod in feat_module.items():
        if mod not in mods:
            _err(errors, f"features: {fid} 的 module_code {mod!r} 未在 modules 中声明")

    # acceptance
    acs = {}
    ac_by_req = {}
    rows = data.get("acceptance")
    if not isinstance(rows, list) or not rows:
        _err(errors, "acceptance: nonempty array required")
        rows = []
    for i, row in enumerate(rows):
        loc = f"acceptance[{i}]"
        if not isinstance(row, dict):
            _err(errors, f"{loc}: object required")
            continue
        for key in ("acceptance_id", "requirement_id", "feature_id", "oracle"):
            if not _is_str(row.get(key)):
                _err(errors, f"{loc}.{key}: nonempty string required")
        if type(row.get("required")) is not bool:
            _err(errors, f"{loc}.required: boolean required")
        if row.get("confirmation") not in CONFIRMATIONS:
            _err(errors, f"{loc}.confirmation: invalid value")
        aid = str(row.get("acceptance_id") or "")
        if aid:
            if not P_AC.match(aid):
                _err(errors, f"{loc}.acceptance_id: invalid pattern {aid!r}")
            if aid in acs:
                _err(errors, f"duplicate id {aid}: acceptance")
            acs[aid] = row
        rid = str(row.get("requirement_id") or "")
        if rid and rid not in reqs:
            _err(errors, f"{loc}.requirement_id: unknown reference {rid}")
        fid = str(row.get("feature_id") or "")
        if fid and fid not in feats:
            _err(errors, f"{loc}.feature_id: unknown reference {fid}")
        oracle = str(row.get("oracle") or "")
        if oracle and cd.is_noncommittal(oracle):
            _err(errors, f"{aid or loc}.oracle: 空话，需写出可判定的观测入口或通过标准")
        if oracle and cd.has_placeholder(oracle):
            _err(errors, f"{aid or loc}.oracle: 含未落实占位内容")
        evidence_refs = row.get("evidence_refs")
        if evidence_refs is not None:
            if not isinstance(evidence_refs, list):
                _err(errors, f"{aid or loc}.evidence_refs: array required")
            else:
                for j, ref in enumerate(evidence_refs):
                    loc_ref = f"{aid or loc}.evidence_refs[{j}]"
                    if not isinstance(ref, dict):
                        _err(errors, f"{loc_ref}: object required")
                        continue
                    for key in ("source", "quote"):
                        if not _is_str(ref.get(key)):
                            _err(errors, f"{loc_ref}.{key}: nonempty string required")
        elements = row.get("elements")
        if elements is None:
            _err(errors, f"{aid or loc}.elements: 四要素必填（缺一即 case_data 校验失败）")
        elif not isinstance(elements, dict):
            _err(errors, f"{aid or loc}.elements: object required")
        else:
            extra = sorted(set(elements) - set(cd.ELEMENT_KEYS))
            if extra:
                _err(errors, f"{aid or loc}.elements: unknown keys {extra}")
            for key in cd.ELEMENT_KEYS:
                value = elements.get(key)
                if not _is_str(value):
                    _err(errors, f"{aid or loc}.elements.{key}: nonempty string required")
                elif cd.is_noncommittal(value):
                    _err(errors, f"{aid or loc}.elements.{key}: 空话，需具体到可观测对象")
                elif cd.has_placeholder(value):
                    _err(errors, f"{aid or loc}.elements.{key}: 含未落实占位内容")
        if rid:
            ac_by_req.setdefault(rid, []).append(row)
        if row.get("required") is True and row.get("confirmation") != "confirmed":
            _warn(warnings, f"{aid or loc}: required AC 的 confirmation 非 confirmed，必要组合的判据存疑")

    # 原型/截图证据（可选）：读不到就写 false + boundary 说明，禁止凭图猜规则
    visual = data.get("visual_evidence")
    if visual is not None:
        if not isinstance(visual, list):
            _err(errors, "visual_evidence: array required")
        else:
            for i, item in enumerate(visual):
                loc = f"visual_evidence[{i}]"
                if not isinstance(item, dict):
                    _err(errors, f"{loc}: object required")
                    continue
                for key in ("file", "boundary"):
                    if not _is_str(item.get(key)):
                        _err(errors, f"{loc}.{key}: nonempty string required")
                digest = item.get("sha256")
                if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                    _err(errors, f"{loc}.sha256: 需 64 位小写十六进制摘要（extract_docx_media.py 产出）")
                if item.get("type") not in VISUAL_TYPES:
                    _err(errors, f"{loc}.type: unknown value {item.get('type')}（允许 {sorted(VISUAL_TYPES)}）")
                conf = item.get("confidence")
                if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
                    _err(errors, f"{loc}.confidence: 0~1 之间的数字必填")
                rules = item.get("extracted_rules")
                if not isinstance(rules, list):
                    _err(errors, f"{loc}.extracted_rules: array required（可空数组，但不得缺）")
                elif any(not _is_str(rule) for rule in rules):
                    _err(errors, f"{loc}.extracted_rules: 元素须为非空字符串")
                understood = item.get("image_understood")
                if not isinstance(understood, bool):
                    _err(errors, f"{loc}.image_understood: boolean required")
                elif understood and not (isinstance(rules, list) and rules):
                    _warn(warnings, f"{loc}: 标记已识读但未提取任何规则，请确认是否真的读到了内容")
                elif not understood and isinstance(rules, list) and rules:
                    # v3 G07：未识读却携带确定规则 = 凭图猜规则，必须拒绝
                    _err(errors, f"{loc}: image_understood=false 不得携带 extracted_rules（禁止凭未识读图猜规则）")

    # 每个 requirement 至少一个 required:true 的 AC（case_data 硬校验等价前置）
    for rid in reqs:
        if not any(ac.get("required") is True for ac in ac_by_req.get(rid, [])):
            _err(errors, f"{rid}: no required acceptance")

    # confirmed_rules / blocking / pending / go_ahead / coverage_gaps / not_reviewed
    rule_ids, block_ids, pend_ids = set(), set(), set()
    for i, row in enumerate(data.get("confirmed_rules") or []):
        loc = f"confirmed_rules[{i}]"
        if not isinstance(row, dict):
            _err(errors, f"{loc}: object required")
            continue
        for key in ("rule_id", "feature_id", "text", "source"):
            if not _is_str(row.get(key)):
                _err(errors, f"{loc}.{key}: nonempty string required")
        rid2 = str(row.get("rule_id") or "")
        if rid2:
            if not P_RULE.match(rid2):
                _err(errors, f"{loc}.rule_id: invalid pattern {rid2!r}")
            if rid2 in rule_ids:
                _err(errors, f"duplicate id {rid2}: confirmed_rules")
            rule_ids.add(rid2)
        fid = str(row.get("feature_id") or "")
        if fid and fid not in feats:
            _err(errors, f"{loc}.feature_id: unknown reference {fid}")
    for i, row in enumerate(data.get("blocking") or []):
        loc = f"blocking[{i}]"
        if not isinstance(row, dict):
            _err(errors, f"{loc}: object required")
            continue
        for key in ("issue_id", "feature_id", "question", "impact"):
            if not _is_str(row.get(key)):
                _err(errors, f"{loc}.{key}: nonempty string required")
        iid = str(row.get("issue_id") or "")
        if iid:
            if not P_ISSUE_B.match(iid):
                _err(errors, f"{loc}.issue_id: invalid pattern {iid!r}")
            if iid in block_ids:
                _err(errors, f"duplicate id {iid}: blocking")
            block_ids.add(iid)
        fid = str(row.get("feature_id") or "")
        if fid and fid not in feats:
            _err(errors, f"{loc}.feature_id: unknown reference {fid}")
    for i, row in enumerate(data.get("pending") or []):
        loc = f"pending[{i}]"
        if not isinstance(row, dict):
            _err(errors, f"{loc}: object required")
            continue
        for key in ("issue_id", "feature_id", "question", "assumption"):
            if not _is_str(row.get(key)):
                _err(errors, f"{loc}.{key}: nonempty string required")
        iid = str(row.get("issue_id") or "")
        if iid:
            if not P_ISSUE_P.match(iid):
                _err(errors, f"{loc}.issue_id: invalid pattern {iid!r}")
            if iid in pend_ids:
                _err(errors, f"duplicate id {iid}: pending")
            pend_ids.add(iid)
        fid = str(row.get("feature_id") or "")
        if fid and fid not in feats:
            _err(errors, f"{loc}.feature_id: unknown reference {fid}")

    go_ahead = data.get("go_ahead")
    if not isinstance(go_ahead, list):
        _err(errors, "go_ahead: array required")
        go_ahead = []
    seen_go = set()
    for fid in go_ahead:
        if not isinstance(fid, str) or not P_FP.match(fid):
            _err(errors, f"go_ahead: invalid feature id {fid!r}")
        elif fid in seen_go:
            _err(errors, f"go_ahead: duplicate reference {fid}")
        else:
            seen_go.add(fid)
        if fid not in feats:
            _err(errors, f"go_ahead: unknown reference {fid}")
    blocked = {str(r.get("feature_id") or "") for r in data.get("blocking") or [] if isinstance(r, dict)}
    for fid in sorted(seen_go & blocked):
        _warn(warnings, f"go_ahead 含 blocking 涉及的 {fid}，相关用例不得生成，请从 go_ahead 移除后重跑")
    pended = {str(r.get("feature_id") or "") for r in data.get("pending") or [] if isinstance(r, dict)}
    for fid in sorted(pended - seen_go):
        _warn(warnings, f"pending 涉及的 {fid} 不在 go_ahead：相关能力本轮不生成用例，请确认符合预期")

    for i, row in enumerate(data.get("coverage_gaps") or []):
        loc = f"coverage_gaps[{i}]"
        if not isinstance(row, dict):
            _err(errors, f"{loc}: object required")
            continue
        for key in ("area", "reason"):
            if not _is_str(row.get(key)):
                _err(errors, f"{loc}.{key}: nonempty string required")
    for i, row in enumerate(data.get("not_reviewed") or []):
        loc = f"not_reviewed[{i}]"
        if not isinstance(row, dict):
            _err(errors, f"{loc}: object required")
            continue
        if row.get("dimension") not in DIMENSIONS:
            _err(errors, f"{loc}.dimension: invalid value {row.get('dimension')!r}")
        if not _is_str(row.get("reason")):
            _err(errors, f"{loc}.reason: nonempty string required")

    # scenarios（v3 G01）：评审阶段可选携带的场景建模交接，结构须合法且与 go_ahead 对齐。
    # 合法场景可经 review_bridge 原样进入 cases JSON，消除“评审拒收/workflow 又强求”的契约互斥。
    scenes = data.get("scenarios")
    if scenes is not None:
        if not isinstance(scenes, list):
            _err(errors, "scenarios: array required")
        else:
            seen_sid = set()
            for si, scene in enumerate(scenes):
                loc = f"scenarios[{si}]"
                if not isinstance(scene, dict):
                    _err(errors, f"{loc}: object required")
                    continue
                sid = scene.get("id")
                if not _is_str(sid) or sid in seen_sid:
                    _err(errors, f"{loc}.id: nonempty unique string required")
                    continue
                seen_sid.add(sid)
                if not _is_str(scene.get("name")) or not _is_str(scene.get("goal")):
                    _err(errors, f"{loc}.name/.goal: nonempty string required")
                paths = scene.get("paths")
                if not isinstance(paths, dict):
                    _err(errors, f"{loc}.paths: object required")
                    continue
                for kind, branch in paths.items():
                    if kind not in {"positive", "exception", "permission"}:
                        _err(errors, f"{loc}.paths.{kind}: unknown path kind")
                    if not isinstance(branch, dict):
                        _err(errors, f"{loc}.paths.{kind}: object required")

    counts = {
        "requirements": len(reqs),
        "features": len(feats),
        "acceptance": len(acs),
        "modules": len(mods),
        "confirmed_rules": len(rule_ids),
        "blocking": len(block_ids),
        "pending": len(pend_ids),
        "go_ahead": len(seen_go),
        "coverage_gaps": len(data.get("coverage_gaps") or []),
        "not_reviewed": len(data.get("not_reviewed") or []),
    }
    return errors, warnings, counts


def main():
    ap = argparse.ArgumentParser(description="requirement-review 评审判定器（只报不改）")
    ap.add_argument("--input", required=True, help="review-result.json 绝对路径")
    ap.add_argument("--json", action="store_true", help="输出结构化 JSON 结果")
    ap.add_argument("--strict", action="store_true", help="warning 也判失败")
    ap.add_argument("--requirement-issues", help="需求可疑点数组 JSON（requirement-issues.json）；"
                                                "缺省时判失败：必须显式声明，空数组表示已审阅且无疑点")
    ap.add_argument("--requirement-issues-migration", action="store_true",
                    help="迁移期口径：未声明需求可疑点仅记 warning 不判失败（仅用于既有资产过渡，新批次不得用）")
    args = ap.parse_args()

    path = Path(args.input)
    if not path.is_file():
        print(f"输入不存在：{path}", file=sys.stderr)
        sys.exit(2)
    try:
        text = path.read_text(encoding="utf-8-sig")
        data = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"输入解析失败：{exc}", file=sys.stderr)
        sys.exit(2)

    errors, warnings, counts = check(data)
    issues = None
    if args.requirement_issues:
        issues_path = Path(args.requirement_issues)
        if not issues_path.is_file():
            print(f"需求可疑点文件不存在：{issues_path}", file=sys.stderr)
            sys.exit(2)
        try:
            issues = json.loads(issues_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"需求可疑点解析失败：{exc}", file=sys.stderr)
            sys.exit(2)
    issue_errors, issue_warnings = check_requirement_issues(
        issues, migration_warn=args.requirement_issues_migration)
    errors.extend(issue_errors)
    warnings.extend(issue_warnings)
    counts["requirement_issues"] = len(issues) if isinstance(issues, list) else 0
    status = "pass" if not errors and not (args.strict and warnings) else "fail"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "file": str(path),
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "counts": counts,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"review_check: {status}")
        print(f"counts: {json.dumps(counts, ensure_ascii=False)}")
        for e in errors:
            print(f"  ERROR {e}")
        for w in warnings:
            print(f"  WARN  {w}")
    sys.exit(0 if status == "pass" else 1)


if __name__ == "__main__":
    main()