"""Validate design references and convert one JSON source to YAML. Does not run Web tests."""
import argparse
import json
from pathlib import Path
import re
import sys

PATH_KINDS = ("positive", "boundary", "exception", "permission")
LAYERS = {"ui", "api", "component", "integration", "e2e"}
# 与 exploration_state.ATTRIBUTES 对齐：易用性（usability）与文案（content）此前只在探索域存在，
# 导致需求→用例这一跳可以不需要回答"这两维是否适用"，等于静默豁免。
ATTRIBUTES = {"functional", "security", "performance", "accessibility", "compatibility",
              "visual", "reliability", "usability", "content"}
SUITES = {"feature", "smoke", "regression", "exploratory", "e2e"}
APPLICABILITY_STATES = {"applicable", "not_applicable", "insufficient_evidence", "deferred"}
ELEMENT_KEYS = ("scene", "object", "condition", "focus")
NONCOMMITTAL = ("正常", "成功", "符合预期", "无异常", "功能可用", "正常显示", "功能正常",
                "工作正常", "无问题", "没问题", "ok", "OK")
PLACEHOLDERS = ("TODO", "TBD", "FIXME", "待补充", "待完善", "待落实", "待填写", "占位符", "xxx", "XXX")
METHOD_CLAIMS = ("边界值", "边界条件", "等价类", "状态迁移", "判定表", "组合覆盖", "Pairwise", "pairwise")
GAP_KINDS = {"scene_unbound", "element_uncovered", "capability_unsupported",
             "method_unwitnessed", "budget_exceeded", "data_unavailable"}
# 覆盖义务类型：与 design-spec「义务先行」一致；L3/L4 属方法见证与探索预算，不在此列。
OBLIGATION_KINDS = {"path", "field_boundary", "element", "combination", "state_edge",
                    "permission", "data", "copy", "compatibility", "security"}
OBLIGATION_LAYERS = ("L1", "L2")
GAP_ID_RE = re.compile(r"^GAP-\d{2,}$")


def bare(value):
    """去掉空白与常见标点，用于判断整句是否为纯空话。"""
    return re.sub(r"[\s，。、；;,.:：！!？?（）()【】\[\]\"'“”‘’]+", "", value)


def is_noncommittal(value):
    target = bare(value)
    return bool(target) and any(target == bare(word) for word in NONCOMMITTAL)


def has_placeholder(value):
    return any(word in value for word in PLACEHOLDERS)


def validate(data):
    errors, warnings = [], []
    if not isinstance(data, dict):
        return ["root must be object"], []
    ids = {}
    def text(obj, key, loc):
        value = obj.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{loc}.{key}: nonempty string required")
    def rows(value, loc, nonempty=False):
        if not isinstance(value, list):
            errors.append(f"{loc}: array required")
            return []
        if nonempty and not value:
            errors.append(f"{loc}: nonempty array required")
        result = []
        for i, row in enumerate(value):
            if not isinstance(row, dict):
                errors.append(f"{loc}[{i}]: object required")
            else:
                result.append(row)
        return result
    def indexed(value, loc, nonempty=False):
        result = {}
        for row in rows(value, loc, nonempty):
            text(row, "id", loc)
            ident = row.get("id")
            if isinstance(ident, str) and ident.strip():
                if ident in ids:
                    errors.append(f"duplicate id {ident}: {loc}, {ids[ident]}")
                ids[ident] = loc
                result[ident] = row
        return result
    def strings(obj, key, loc, nonempty=True):
        value = obj.get(key)
        if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
            errors.append(f"{loc}.{key}: string array required")
            return []
        if nonempty and not value:
            errors.append(f"{loc}.{key}: nonempty array required")
        if len(set(value)) != len(value):
            errors.append(f"{loc}.{key}: duplicate reference")
        return value
    def links(obj, key, target, loc, nonempty=True):
        values = strings(obj, key, loc, nonempty)
        for value in values:
            if value not in target:
                errors.append(f"{loc}.{key}: unknown reference {value}")
        return values
    def safe_refs(obj, key):
        value = obj.get(key)
        return [x for x in value if isinstance(x, str)] if isinstance(value, list) else []
    def boolean(obj, key, loc):
        if type(obj.get(key)) is not bool:
            errors.append(f"{loc}.{key}: boolean required")
    meta = data.get("meta")
    if not isinstance(meta, dict):
        errors.append("meta: object required")
        meta = {}
    for key in ("schema_version", "id", "title", "flow_kind"):
        text(meta, key, "meta")
    if meta.get("schema_version") != "1.0":
        errors.append("meta.schema_version: expected 1.0")
    if meta.get("flow_kind") not in {"simple", "workflow", "stateful"}:
        errors.append("meta.flow_kind: expected simple/workflow/stateful")
    if "legacy_source" in meta:
        legacy = meta.get("legacy_source")
        if not isinstance(legacy, dict):
            errors.append("meta.legacy_source: object required")
        else:
            extra = set(legacy) - {"schema_version", "migrated_at", "owner"}
            if extra:
                errors.append(f"meta.legacy_source: unknown keys {sorted(extra)}")
            for key in ("schema_version", "migrated_at", "owner"):
                if not str(legacy.get(key) or "").strip():
                    errors.append(f"meta.legacy_source.{key}: nonempty string required")
            when = str(legacy.get("migrated_at") or "")
            if when and not re.match(r"^\d{4}-\d{2}-\d{2}", when):
                errors.append("meta.legacy_source.migrated_at: expected YYYY-MM-DD（迁移登记的日期）")
    reqs = indexed(data.get("requirements"), "requirements", True)
    tops = indexed(data.get("features"), "features", True)
    leaves = {}
    for ident, top in tops.items():
        for key in ("name", "description"):
            text(top, key, ident)
        children = indexed(top.get("children"), ident + ".children", True)
        leaves.update(children)
    acs = indexed(data.get("acceptance"), "acceptance", True)
    scenes = indexed(data.get("scenarios", []), "scenarios")
    cases = indexed(data.get("testcases"), "testcases", True)
    diagrams = indexed(data.get("diagrams", []), "diagrams")
    obligations = data.get("coverage_obligations")
    if obligations is not None:
        if not isinstance(obligations, list):
            errors.append("coverage_obligations: array required")
        else:
            known_sources = set(reqs) | set(leaves) | set(acs)
            seen_obligations = set()
            for i, ob in enumerate(obligations):
                loc = f"coverage_obligations[{i}]"
                if not isinstance(ob, dict):
                    errors.append(f"{loc}: object required")
                    continue
                extra = set(ob) - {"id", "layer", "kind", "target", "source", "required", "tc_ids", "note"}
                if extra:
                    errors.append(f"{loc}: unknown keys {sorted(extra)}")
                for key in ("id", "layer", "kind", "target", "source"):
                    if not str(ob.get(key) or "").strip():
                        errors.append(f"{loc}.{key}: nonempty string required")
                oid = str(ob.get("id") or "").strip()
                if oid:
                    if oid in seen_obligations:
                        errors.append(f"{loc}.id: duplicate {oid}")
                    seen_obligations.add(oid)
                layer = str(ob.get("layer") or "").strip()
                if layer and layer not in OBLIGATION_LAYERS:
                    errors.append(f"{loc}.layer: expected L1/L2（L3 方法见证、L4 探索预算不属义务）")
                kind = str(ob.get("kind") or "").strip()
                if kind and kind not in OBLIGATION_KINDS:
                    errors.append(f"{loc}.kind: unknown value {kind}（允许 {sorted(OBLIGATION_KINDS)}）")
                target = str(ob.get("target") or "").strip()
                if target and (has_placeholder(target) or is_noncommittal(target)):
                    errors.append(f"{loc}.target: 不得为占位或空泛描述，须落到元素/边界/状态边/条件组合")
                source = str(ob.get("source") or "").strip()
                if source and source not in known_sources:
                    errors.append(f"{loc}.source: unknown reference {source}（须引用存在的 REQ/FP/AC）")
                boolean(ob, "required", loc)
                tc_ids = ob.get("tc_ids", [])
                if not isinstance(tc_ids, list) or any(not isinstance(x, str) for x in tc_ids):
                    errors.append(f"{loc}.tc_ids: string array required")
                    tc_ids = []
                for tid in tc_ids:
                    tc = cases.get(tid)
                    if tc is None:
                        errors.append(f"{loc}.tc_ids: unknown testcase {tid}")
                        continue
                    flags = tc.get("coverage_flags")
                    if isinstance(flags, list) and layer and layer not in flags:
                        errors.append(f"{loc}.tc_ids: {tid} 未声明 {layer}（义务绑定须与该用例声明的层一致）")
    paths, path_scene = {}, {}
    for ident, req in reqs.items():
        for key in ("name", "source", "description", "confirmation"):
            text(req, key, ident)
        if req.get("confirmation") not in {"confirmed", "provisional", "ambiguous"}:
            errors.append(f"{ident}.confirmation: invalid value")
        if not any(ac.get("requirement_id") == ident and ac.get("required") is True for ac in acs.values()):
            errors.append(f"{ident}: no required acceptance")
    for ident, leaf in leaves.items():
        for key in ("name", "description"):
            text(leaf, key, ident)
        links(leaf, "requirement_ids", reqs, ident)
        for api in rows(leaf.get("apis", []), ident + ".apis"):
            for key in ("method", "path", "source"):
                text(api, key, ident + ".apis")
        if not any(ident in tc.get("feature_ids", []) for tc in cases.values() if isinstance(tc.get("feature_ids"), list)):
            warnings.append(f"{ident}: no testcase coverage")
    paths, path_scene = {}, {}
    na_reasons = {kind: [] for kind in PATH_KINDS}
    for ident, scene in scenes.items():
        for key in ("name", "goal"):
            text(scene, key, ident)
        links(scene, "feature_ids", leaves, ident)
        branches = scene.get("paths")
        if not isinstance(branches, dict):
            errors.append(f"{ident}.paths: object required")
            branches = {}
        na = scene.get("not_applicable", {})
        if not isinstance(na, dict):
            errors.append(f"{ident}.not_applicable: object required")
            na = {}
        for kind in PATH_KINDS:
            branch = indexed(branches.get(kind), ident + ".paths." + kind)
            entry = na.get(kind)
            if isinstance(entry, dict):
                extra = set(entry) - {"reason", "business_object", "checked_at"}
                if extra:
                    errors.append(f"{ident}.not_applicable.{kind}: unknown keys {sorted(extra)}")
                reason = entry.get("reason")
                if not isinstance(reason, str) or not reason.strip():
                    errors.append(f"{ident}.not_applicable.{kind}.reason: nonempty string required")
                    reason = ""
                else:
                    bo = entry.get("business_object")
                    scene_name = str(scene.get("name") or "")
                    if isinstance(bo, str) and bo.strip():
                        if bo not in reason and not (scene_name and (bo in scene_name or scene_name in bo)):
                            warnings.append(f"{ident}.not_applicable.{kind}.business_object: 未出现在 reason 或场景名「{scene_name}」中，豁免与该场景的关联存疑")
                    else:
                        warnings.append(f"{ident}.not_applicable.{kind}.business_object: 建议给出业务对象以证明豁免针对该场景")
                na_reasons[kind].append((ident, reason))
            elif isinstance(entry, str) and entry.strip():
                warnings.append(f"{ident}.not_applicable.{kind}: 字符串形态已废弃，请升级为对象并补 business_object/checked_at 举证")
                na_reasons[kind].append((ident, entry))
            if not branch and not ((isinstance(entry, dict) and isinstance(entry.get("reason"), str) and entry["reason"].strip()) or (isinstance(entry, str) and entry.strip())):
                warnings.append(f"{ident}.{kind}: no paths or not_applicable reason")
            for path_id, path in branch.items():
                text(path, "description", path_id)
                paths[path_id] = path
                path_scene[path_id] = ident
                if not any(tc.get("path_id") == path_id for tc in cases.values()):
                    warnings.append(f"{path_id}: no testcase coverage")
    for kind in PATH_KINDS:
        entries = na_reasons[kind]
        reasons = [reason for _, reason in entries if reason]
        if len(set(reasons)) < len(reasons):
            duplicated = sorted({reason for reason in reasons if reasons.count(reason) > 1})[0]
            scenes_with = "、".join(sid for sid, reason in entries if reason == duplicated)
            errors.append(f"not_applicable.{kind}: 豁免理由跨场景复用（「{duplicated[:40]}」出现于 {scenes_with}），每场景须独立举证或补真用例，不得用同一句模板批量豁免")
    if meta.get("flow_kind") in {"workflow", "stateful"} and not scenes:
        errors.append("scenarios: required for workflow/stateful design")
    for ident, ac in acs.items():
        for key in ("description", "reason", "oracle", "confirmation"):
            text(ac, key, ident)
        boolean(ac, "required", ident)
        if is_noncommittal(str(ac.get("description") or "")):
            errors.append(f"{ident}.description: 空话，需写出可观测的业务结果")
        if is_noncommittal(str(ac.get("oracle") or "")):
            errors.append(f"{ident}.oracle: 空话，需写出可判定的观测入口或通过标准")
        if has_placeholder(str(ac.get("description") or "")) or has_placeholder(str(ac.get("oracle") or "")):
            errors.append(f"{ident}: description/oracle 含未落实占位内容")
        elements = ac.get("elements")
        if elements is None:
            warnings.append(f"{ident}.elements: 缺少四要素（strict 视为失败）")
        elif not isinstance(elements, dict):
            errors.append(f"{ident}.elements: object required")
        else:
            extra = set(elements) - set(ELEMENT_KEYS)
            if extra:
                errors.append(f"{ident}.elements: unknown keys {sorted(extra)}")
            for key in ELEMENT_KEYS:
                value = elements.get(key)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"{ident}.elements.{key}: nonempty string required")
                elif is_noncommittal(value):
                    errors.append(f"{ident}.elements.{key}: 空话，需具体到可观测对象")
                elif has_placeholder(value):
                    errors.append(f"{ident}.elements.{key}: 含未落实占位内容")
        if ac.get("requirement_id") not in reqs:
            errors.append(f"{ident}.requirement_id: unknown reference")
        if ac.get("confirmation") not in {"confirmed", "provisional", "ambiguous"}:
            errors.append(f"{ident}.confirmation: invalid value")
        if ac.get("case_mode") not in {"AND", "OR"}:
            errors.append(f"{ident}.case_mode: expected AND/OR")
        if ac.get("case_mode") == "OR":
            text(ac, "alternative_reason", ident)
        for case_id in links(ac, "case_ids", cases, ident):
            tc = cases.get(case_id, {})
            if ac.get("required") and tc.get("required") is not True:
                errors.append(f"{ident}: required acceptance uses optional case {case_id}")
            if ident not in safe_refs(tc, "acceptance_ids"):
                errors.append(f"{ident}: acceptance reverse reference missing in {case_id}")
    for ident, tc in cases.items():
        for key in ("title", "classification_reason", "role", "cleanup"):
            text(tc, key, ident)
        boolean(tc, "required", ident)
        if has_placeholder(str(tc.get("classification_reason") or "")):
            errors.append(f"{ident}.classification_reason: 含未落实占位内容")
        reason_text = str(tc.get("classification_reason") or "")
        if any(word in reason_text for word in METHOD_CLAIMS):
            if not isinstance(tc.get("data"), dict) or not tc.get("data"):
                errors.append(f"{ident}.data: 声称使用设计方法但未给具体取值或状态")
        feature_ids = links(tc, "feature_ids", leaves, ident)
        acceptance_ids = links(tc, "acceptance_ids", acs, ident)
        feature_reqs = {r for fp in feature_ids for r in safe_refs(leaves.get(fp, {}), "requirement_ids")}
        for ac_id in acceptance_ids:
            if ac_id in acs and acs[ac_id].get("requirement_id") not in feature_reqs:
                errors.append(f"{ident}: acceptance requirement does not match feature")
        if tc.get("required"):
            for ac_id in acceptance_ids:
                if ac_id in acs and ident not in safe_refs(acs[ac_id], "case_ids"):
                    errors.append(f"{ident}: required case not in acceptance {ac_id}")
        if tc.get("layer") not in LAYERS:
            errors.append(f"{ident}.layer: invalid value")
        for key, allowed in (("attributes", ATTRIBUTES), ("suites", SUITES)):
            if any(x not in allowed for x in strings(tc, key, ident)):
                errors.append(f"{ident}.{key}: invalid value")
        if tc.get("priority") not in {"P0", "P1", "P2"}:
            errors.append(f"{ident}.priority: invalid value")
        if tc.get("dependency_mode") not in {"real", "sandbox", "mock", "replay"}:
            errors.append(f"{ident}.dependency_mode: invalid value")
        strings(tc, "preconditions", ident)
        if not isinstance(tc.get("data"), dict):
            errors.append(f"{ident}.data: object required")
        for step in rows(tc.get("steps"), ident + ".steps", True):
            for key in ("action", "expected"):
                text(step, key, ident + ".steps")
        for assertion in rows(tc.get("assertions"), ident + ".assertions", True):
            for key in ("check", "oracle"):
                text(assertion, key, ident + ".assertions")
            if "expect" not in assertion:
                errors.append(f"{ident}.assertions.expect: required")
            if is_noncommittal(str(assertion.get("check") or "")):
                errors.append(f"{ident}.assertions.check: 空话，需写出被验证的具体对象或行为")
            if is_noncommittal(str(assertion.get("oracle") or "")):
                errors.append(f"{ident}.assertions.oracle: 空话，需写出观测入口")
            if isinstance(assertion.get("expect"), str) and has_placeholder(assertion["expect"]):
                errors.append(f"{ident}.assertions.expect: 含未落实占位内容")
        scene_id, path_id = tc.get("scenario_id"), tc.get("path_id")
        if scene_id is not None or path_id is not None:
            if not isinstance(scene_id, str) or scene_id not in scenes:
                errors.append(f"{ident}.scenario_id: unknown reference {scene_id}")
            if not isinstance(path_id, str) or path_id not in paths or path_scene.get(path_id) != scene_id:
                errors.append(f"{ident}.path_id: unknown/mismatched reference {path_id}")
            if scene_id in scenes and not set(feature_ids).issubset(set(safe_refs(scenes[scene_id], "feature_ids"))):
                errors.append(f"{ident}: feature not included in scenario")
        elif meta.get("flow_kind") in {"workflow", "stateful"}:
            errors.append(f"{ident}: scenario_id and path_id required")
    for ident, diagram in diagrams.items():
        links(diagram, "scenario_ids", scenes, ident)
        text(diagram, "mermaid", ident)
        if "```" in str(diagram.get("mermaid", "")):
            errors.append(f"{ident}.mermaid: no code fence allowed")
    applicability = data.get("applicability")
    if applicability is None:
        warnings.append("applicability: 缺少逐类裁定矩阵（strict 视为失败）")
    elif not isinstance(applicability, dict):
        errors.append("applicability: object required")
    else:
        for key, entry in applicability.items():
            if key not in ATTRIBUTES:
                errors.append(f"applicability.{key}: unknown test type")
                continue
            if not isinstance(entry, dict):
                errors.append(f"applicability.{key}: object required")
                continue
            state = entry.get("state")
            if state not in APPLICABILITY_STATES:
                errors.append(f"applicability.{key}.state: invalid value")
            if state in {"not_applicable", "deferred"} and not str(entry.get("reason") or "").strip():
                errors.append(f"applicability.{key}.reason: required for {state}")
        missing = sorted(set(ATTRIBUTES) - set(applicability))
        if missing:
            # v1.55 R1：缺类仍是告警（S0 层只管结构与引用合法）；九类全裁的**强制**
            # 在 coverage_matrix 的维度下限层落地（dimension_gaps 计入退出码）——
            # 访谈任务实测"缺 usability/content 仍可交付"，强制点必须在覆盖层而非结构层。
            warnings.append(f"applicability: 未裁定类型 {missing}，九类逐类裁定为交付前置"
                            "（coverage_matrix 将按 dimension_gaps 阻断），每类都要给出 state")
    if cases:
        smoke = sum(1 for tc in cases.values() if "smoke" in safe_refs(tc, "suites"))
        if smoke / len(cases) > 0.5:
            warnings.append(f"suites: smoke 占 {smoke}/{len(cases)}，冒烟应聚焦入口可达与核心展示，不宜过半")
    gaps = data.get("coverage_gaps")
    if gaps is not None:
        if not isinstance(gaps, list):
            errors.append("coverage_gaps: array required")
        else:
            seen_gaps = set()
            for i, gap in enumerate(gaps):
                loc = f"coverage_gaps[{i}]"
                if not isinstance(gap, dict):
                    errors.append(f"{loc}: object required")
                    continue
                for key in ("gap_id", "kind", "target", "reason", "obligation"):
                    if not str(gap.get(key) or "").strip():
                        errors.append(f"{loc}.{key}: nonempty string required")
                gid = str(gap.get("gap_id") or "")
                if gid:
                    if not GAP_ID_RE.match(gid):
                        errors.append(f"{loc}.gap_id: expected GAP-NN")
                    if gid in seen_gaps:
                        errors.append(f"{loc}.gap_id: duplicate {gid}")
                    seen_gaps.add(gid)
                kind = gap.get("kind")
                if kind and kind not in GAP_KINDS:
                    errors.append(f"{loc}.kind: unknown value {kind}（允许 {sorted(GAP_KINDS)}）")
                if has_placeholder(str(gap.get("obligation") or "")):
                    errors.append(f"{loc}.obligation: 含占位词，须写明补齐动作或责任方")
                extra = set(gap) - {"gap_id", "kind", "target", "reason", "obligation",
                                    "severity", "required", "status", "closed_by"}
                if extra:
                    errors.append(f"{loc}: unknown keys {sorted(extra)}")
                if "required" in gap and type(gap["required"]) is not bool:
                    errors.append(f"{loc}.required: boolean required")
                if "status" in gap and gap["status"] not in {"open", "in_progress", "closed"}:
                    errors.append(f"{loc}.status: expected open/in_progress/closed")
                if "closed_by" in gap and not isinstance(gap["closed_by"], dict):
                    errors.append(f"{loc}.closed_by: object required（须给出证据或补测用例）")
    stats = {"requirements": len(reqs), "functions": len(tops), "feature_points": len(leaves), "acceptance": len(acs), "scenarios": len(scenes), "testcases": len(cases)}
    if "stats" in meta and meta["stats"] != stats:
        errors.append(f"meta.stats mismatch; computed {stats}")
    return errors, warnings


def load(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    if Path(path).suffix.lower() in {".yaml", ".yml"}:
        import yaml
        return yaml.safe_load(text)
    return json.loads(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("validate")
    check.add_argument("input")
    check.add_argument("--strict", action="store_true")
    convert = sub.add_parser("to-yaml")
    convert.add_argument("input")
    convert.add_argument("output")
    compare = sub.add_parser("diff")
    compare.add_argument("left")
    compare.add_argument("right")
    args = parser.parse_args()
    try:
        if args.command == "validate":
            errors, warnings = validate(load(args.input))
            print(json.dumps({"errors": errors, "warnings": warnings, "scope": "design structure only; no execution verdict"}, ensure_ascii=True))
            return int(bool(errors or (args.strict and warnings)))
        if args.command == "diff":
            same = load(args.left) == load(args.right)
            print("SAME" if same else "DIFFERENT")
            return 0 if same else 1
        if Path(args.input).suffix.lower() != ".json":
            raise ValueError("to-yaml source must be JSON")
        if Path(args.output).suffix.lower() not in {".yaml", ".yml"}:
            raise ValueError("to-yaml output must be YAML")
        data = load(args.input)
        errors, warnings = validate(data)
        if errors:
            print(json.dumps({"errors": errors}, ensure_ascii=True))
            return 1
        import yaml
        class Dumper(yaml.SafeDumper):
            pass
        def represent_string(dumper, value):
            style = "|" if "\n" in value else '"'
            return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)
        Dumper.add_representer(str, represent_string)
        output = Path(args.output)
        if not output.parent.is_dir():
            raise ValueError("output parent directory must exist")
        with output.open("w", encoding="utf-8", newline="\n") as stream:
            yaml.dump(data, stream, Dumper=Dumper, allow_unicode=True, sort_keys=False)
        print("YAML generated; validate both formats and diff before delivery")
        return 0
    except Exception as exc:
        print(f"Input/tool error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
