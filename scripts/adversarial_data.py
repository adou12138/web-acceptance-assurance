"""adversarial-plan 校验器：标准库实现，不依赖第三方 JSON Schema 库。

API：
- load(path) 读取并解析 plan JSON（拒绝重复字段），文件/解析错误抛 OSError/ValueError；
- fingerprint(path) 计算文件内容 sha256（十六进制小写），用于冻结来源指纹；
- validate(plan, material_path=None, cases_json_path=None) 校验 plan 全部契约约束，
  返回计数摘要；任何违规抛 ValueError。传入实际路径时逐枚复核指纹一致性。

校验约束（对应 references/schemas/adversarial-plan.schema.json）：
- 根对象仅允许 schema_version="1.0"、plan_id（ADVPLAN- 前缀）、source_fingerprints
  （恰含 material/cases_json 两枚 64 位小写十六进制指纹）、surfaces/oracles/obligations；
- SURF：id 前缀 SURF-、entry/role/state/data/dependencies/failure_hypothesis 非空、
  risk ∈ high/medium/low，surfaces 不得为空（无风险面即未启用对抗，不应建 plan）；
- ORC：id 前缀 ORC-、source ∈ REQ/AC/contract/invariant/req_issue、observation_entry 与
  applicable_condition 非空（判据缺来源即报错，自猜规则不得入账）；
- ADV：id 前缀 ADV-、surface_ref/oracle_ref 必须指向已存在的 SURF/ORC（未知引用报错）、
  probe_type ∈ PROBE_TYPES（含 visual_walkthrough/copy_audit/heuristic_review/consistency/
  regression 等界面与一致性维度，不是只有功能类攻击）、budget 非空、
  status ∈ planned/executed/cancelled（cancelled 必须附 cancel_reason）；
- ADV 的 method 若提供必须落在方法库稳定 ID 内（见 references/probe-playbook.md B 节）；
- 全部条目 ID 全局唯一，重复 ID 报错；
- 指纹缺失/形态非法报错；传入实际文件且 sha256 与登记值不一致报"指纹失配"。

本脚本只读输入，不修改 plan；CLI：python adversarial_data.py plan.json
[--material 需求材料路径] [--cases cases JSON 路径]，成功输出 JSON 摘要并以 0 退出，
输入/校验错误向 stderr 输出原因并以 2 退出。
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
IDENT_RE = r"[A-Za-z0-9][A-Za-z0-9_.:-]*"
RISKS = {"high", "medium", "low"}
ORACLE_SOURCES = {"REQ", "AC", "contract", "invariant", "req_issue"}
# 探针类型是「打什么维度的失效」，不是只有功能类：界面走查、文案核对、启发式任务评审、
# 数据一致性对账、影响面回归都是合法探针类型。此前只认功能四类，导致如实登记的 UI 类
# 发现无处安放，被制度性地挤出记录。
PROBE_TYPES = {"challenge", "fault_injection", "timing", "permission",
               "visual_walkthrough", "copy_audit", "heuristic_review",
               "consistency", "regression"}
# 方法库与坑点模板的稳定 ID（与 references/probe-playbook.md 同步；模板编号以文档 A 节为准）
PLAYBOOK_TEMPLATES = {f"P{i}" for i in range(1, 26)}
METHOD_IDS = {"equivalence", "boundary", "decision_table", "state_transition", "combinatorial",
              "differential", "metamorphic", "fault_injection", "concurrency", "rollback",
              "visual_walkthrough", "copy_audit", "heuristic_review", "consistency_check",
              "regression_impact"}
# 方法多样性下限：高风险 SURF 达阈后必须至少用 N 种方法，否则"全面"无从谈起
METHOD_DIVERSITY_MIN_SURFACES = 3
METHOD_DIVERSITY_MIN_METHODS = 2
OBLIGATION_STATUSES = {"planned", "executed", "cancelled"}
SCENARIO_KINDS = {"cross_module", "operation_sequence", "state_bypass"}
SURF_FIELDS = {"id", "entry", "role", "state", "data", "dependencies", "failure_hypothesis", "risk"}
# v1.55 R3（P25 校验双方向）：direction 为 SURF 可选字段；未声明按披露，不熔断。
SURF_OPTIONAL_FIELDS = {"direction"}
SURF_DIRECTIONS = {"increase", "decrease", "both", "n/a"}
ORC_REQUIRED = {"id", "source", "observation_entry", "applicable_condition"}
ORC_OPTIONAL = {"source_ref"}
ADV_REQUIRED = {"id", "surface_ref", "oracle_ref", "probe_type", "budget", "status"}
ADV_OPTIONAL = {"cancel_reason", "method", "playbook_ref"}


def fingerprint(path):
    """计算文件内容 sha256，返回十六进制小写摘要；文件缺失抛 OSError。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"JSON 对象包含重复字段: {key}")
        value[key] = item
    return value


def load(path):
    """读取 plan JSON；拒绝重复字段，非法输入抛 ValueError，文件错误抛 OSError。"""
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object)


def _object(value, required, optional, name):
    if not isinstance(value, dict):
        raise ValueError(f"{name}: 必须为对象")
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required - optional)
    if missing or unknown:
        # 与 exploration_state 同口径：缺字段与多余字段分列点名。合并成一句时执行者
        # 无法判断该补哪个字段，只能回头读源码——手写 plan JSON 时这是第一道坎。
        parts = []
        if missing:
            parts.append(f"缺字段 {missing}")
        if unknown:
            parts.append(f"未知字段 {unknown}")
        raise ValueError(f"{name}: " + "；".join(parts))


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}: 必须为非空字符串")


def _prefix_id(value, prefix, name):
    _text(value, name)
    if not re.fullmatch(prefix + IDENT_RE, value):
        raise ValueError(f"{name}: 必须为 {prefix} 前缀的稳定 ID")


def validate(plan, material_path=None, cases_json_path=None):
    """校验 plan 契约；违规抛 ValueError，通过返回计数摘要。不修改输入。"""
    _object(plan, {"schema_version", "plan_id", "source_fingerprints", "surfaces", "oracles", "obligations"}, set(), "root")
    if plan["schema_version"] != "1.0":
        raise ValueError("schema_version: 必须为字符串 1.0")
    _prefix_id(plan["plan_id"], "ADVPLAN-", "plan_id")

    fingerprints = plan["source_fingerprints"]
    _object(fingerprints, {"material", "cases_json"}, set(), "source_fingerprints")
    for key, value in fingerprints.items():
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            raise ValueError(f"source_fingerprints.{key}: 必须为 64 位十六进制 sha256 指纹")

    surfaces, oracles, obligations = plan["surfaces"], plan["oracles"], plan["obligations"]
    if not isinstance(surfaces, list) or not surfaces:
        raise ValueError("surfaces: 必须为非空数组——无风险面不应启用对抗循环")
    if not isinstance(oracles, list) or not oracles:
        raise ValueError("oracles: 必须为非空数组——对抗义务必须关联至少一条判据")
    if not isinstance(obligations, list):
        raise ValueError("obligations: 必须为数组")

    seen = set()
    methods_used, templates_used = set(), set()
    for index, item in enumerate(surfaces):
        name = f"surfaces[{index}]"
        _object(item, SURF_FIELDS, SURF_OPTIONAL_FIELDS, name)
        _prefix_id(item["id"], "SURF-", name + ".id")
        if item["id"] in seen:
            raise ValueError(f"{name}.id: 重复的稳定 ID {item['id']}")
        seen.add(item["id"])
        for key in ("entry", "role", "state", "data", "dependencies", "failure_hypothesis"):
            _text(item[key], f"{name}.{key}")
        if item["risk"] not in RISKS:
            raise ValueError(f"{name}.risk: 必须为 high/medium/low")
        if "direction" in item:
            if item["direction"] not in SURF_DIRECTIONS:
                raise ValueError(f"{name}.direction: 必须为 {sorted(SURF_DIRECTIONS)} 之一")

    oracle_ids = set()
    for index, item in enumerate(oracles):
        name = f"oracles[{index}]"
        _object(item, ORC_REQUIRED, ORC_OPTIONAL, name)
        _prefix_id(item["id"], "ORC-", name + ".id")
        if item["id"] in seen:
            raise ValueError(f"{name}.id: 重复的稳定 ID {item['id']}")
        seen.add(item["id"])
        oracle_ids.add(item["id"])
        if item["source"] not in ORACLE_SOURCES:
            raise ValueError(f"{name}.source: 判据来源必须为 REQ/AC/contract/invariant，不得以模型自猜规则入账")
        _text(item["observation_entry"], name + ".observation_entry")
        _text(item["applicable_condition"], name + ".applicable_condition")
        if "source_ref" in item:
            _text(item["source_ref"], name + ".source_ref")

    surface_ids = {item["id"] for item in surfaces}
    for index, item in enumerate(obligations):
        name = f"obligations[{index}]"
        _object(item, ADV_REQUIRED, ADV_OPTIONAL, name)
        _prefix_id(item["id"], "ADV-", name + ".id")
        if item["id"] in seen:
            raise ValueError(f"{name}.id: 重复的稳定 ID {item['id']}")
        seen.add(item["id"])
        if item["surface_ref"] not in surface_ids:
            raise ValueError(f"{name}.surface_ref: 未知引用 {item['surface_ref']}")
        if item["oracle_ref"] not in oracle_ids:
            raise ValueError(f"{name}.oracle_ref: 未知引用 {item['oracle_ref']}")
        if item["probe_type"] not in PROBE_TYPES:
            raise ValueError(f"{name}.probe_type: 必须为 {sorted(PROBE_TYPES)} 之一")
        _text(item["budget"], name + ".budget")
        if item["status"] not in OBLIGATION_STATUSES:
            raise ValueError(f"{name}.status: 必须为 planned/executed/cancelled")
        if item["status"] == "cancelled":
            if "cancel_reason" not in item:
                raise ValueError(f"{name}: status=cancelled 必须附 cancel_reason")
            _text(item["cancel_reason"], name + ".cancel_reason")
        if "method" in item:
            if item["method"] not in METHOD_IDS:
                raise ValueError(f"{name}.method: 必须为方法库稳定 ID {sorted(METHOD_IDS)} 之一")
        if "playbook_ref" in item:
            if item["playbook_ref"] not in PLAYBOOK_TEMPLATES:
                raise ValueError(f"{name}.playbook_ref: 必须为挑刺清单模板 ID P1~P25 之一")
        methods_used.add(item.get("method"))
        templates_used.add(item.get("playbook_ref"))

    if material_path is not None:
        actual = fingerprint(material_path)
        if actual != fingerprints["material"]:
            raise ValueError("source_fingerprints.material: 指纹失配——需求材料已变更，须重建或重新登记 plan")
    if cases_json_path is not None:
        actual = fingerprint(cases_json_path)
        if actual != fingerprints["cases_json"]:
            raise ValueError("source_fingerprints.cases_json: 指纹失配——用例资产已变更，须重建或重新登记 plan")

    return {
        "plan_id": plan["plan_id"],
        "surfaces": len(surfaces),
        "oracles": len(oracles),
        "obligations": len(obligations),
        "high_risk_surfaces": sum(1 for item in surfaces if item["risk"] == "high"),
        "coverage": summarize_coverage(surfaces, obligations),
        "fingerprint_checked": {"material": material_path is not None, "cases_json": cases_json_path is not None},
    }


def summarize_coverage(surfaces, obligations):
    """覆盖面汇总：每个高风险 SURF 是否至少有一条已登记探针，以及方法/模板使用情况。

    只报事实与缺口，不直接改结论：门禁/报告按这里的 uncovered 决定是否压上限。
    """
    by_surface = {}
    methods, templates = set(), set()
    for item in obligations:
        by_surface.setdefault(item["surface_ref"], []).append(item["id"])
        if item.get("method"):
            methods.add(item["method"])
        if item.get("playbook_ref"):
            templates.add(item["playbook_ref"])
    high = [s["id"] for s in surfaces if s["risk"] == "high"]
    uncovered = sorted(sid for sid in high if sid not in by_surface)
    no_method = sorted(o["id"] for o in obligations if not o.get("method") and not o.get("playbook_ref"))
    return {
        "high_risk_ids": sorted(high),
        "high_risk_uncovered": uncovered,
        "methods_used": sorted(methods),
        "methods_unused": sorted(METHOD_IDS - methods),
        "templates_used": sorted(templates),
        "templates_unused": sorted(PLAYBOOK_TEMPLATES - templates),
        "obligations_without_method": no_method,
    }


def coverage_gaps(plan):
    """面向门禁的覆盖缺口：不只查"有没有写方法"，还查"用得够不够多样"。

    审核 C4 修正：原实现只要每条探针挂上任一 method 字符串就放行，"全面"无法验证。
    现增两类多样性缺口（阈值见 METHOD_DIVERSITY_MIN_*）：
    - 高风险 SURF 数达阈后，所用方法种类少于下限 → "方法单一"；
    - 已用坑点模板数少于高风险 SURF 数的一半 → "模板覆盖过窄"。
    """
    surfaces = plan.get("surfaces") or []
    obligations = plan.get("obligations") or []
    coverage = summarize_coverage(surfaces, obligations)
    gaps = []
    if not surfaces:
        gaps.append("plan 未登记任何风险面：对抗循环未启用，不得给出已测结论")
        return gaps
    for sid in coverage["high_risk_uncovered"]:
        gaps.append(f"高风险风险面 {sid} 未登记任何探针")
    for oid in coverage["obligations_without_method"]:
        gaps.append(f"探针 {oid} 未注明 method/playbook_ref：无法核对方法与挑刺模板是否有效使用")

    # 多样性检查：多样化测试方法是目标要求，"挂一个方法就通过"不算达成
    high_count = len(coverage["high_risk_ids"])
    used_methods = coverage["methods_used"]
    used_templates = coverage["templates_used"]
    if high_count >= METHOD_DIVERSITY_MIN_SURFACES and len(used_methods) < METHOD_DIVERSITY_MIN_METHODS:
        gaps.append(f"方法过于单一：{high_count} 个高风险风险面仅使用 {len(used_methods)} 种方法"
                    f"（{used_methods or '无'}），低于下限 {METHOD_DIVERSITY_MIN_METHODS}——"
                    f"未使用：{coverage['methods_unused']}")
    if high_count >= 2 and len(used_templates) * 2 < high_count:
        gaps.append(f"挑刺模板覆盖过窄：{high_count} 个高风险风险面仅覆盖 {len(used_templates)} 个模板"
                    f"（{used_templates or '无'}），低于半数要求——未使用：{coverage['templates_unused']}")
    return gaps


def validate_scenarios(scenarios, surface_ids):
    """校验对抗场景伴随文件（SCN-ADV）。形态见 schemas/adversarial-scenarios.schema.json。

    审核 C5：场景不能只建 schema 不消费。返回 (errors, warnings)。
    """
    errors, warnings = [], []
    if not isinstance(scenarios, list):
        return ["adversarial-scenarios: 必须为数组"], warnings
    seen = set()
    for i, item in enumerate(scenarios):
        name = f"adversarial-scenarios[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{name}: object required")
            continue
        sid = str(item.get("id") or "")
        if not sid.startswith("SCN-ADV-"):
            errors.append(f"{name}.id: 必须以 SCN-ADV- 开头（实为 {sid or '缺失'}）")
        if sid in seen:
            errors.append(f"{name}.id: 重复的场景 ID {sid}")
        seen.add(sid)
        kind = item.get("kind")
        if kind not in SCENARIO_KINDS:
            errors.append(f"{name}.kind: 必须为 {sorted(SCENARIO_KINDS)} 之一")
        seq = item.get("sequence")
        if not isinstance(seq, list) or len(seq) < 2:
            errors.append(f"{name}.sequence: 至少两步——单点输入属挑刺模板范畴，不是场景")
        refs = item.get("surfaces")
        if not isinstance(refs, list) or not refs:
            errors.append(f"{name}.surfaces: 至少关联一条 SURF")
        else:
            unknown = [r for r in refs if r not in surface_ids]
            if unknown:
                errors.append(f"{name}.surfaces: 未知引用 {unknown}")
            if kind == "cross_module" and len(refs) < 2:
                errors.append(f"{name}: kind=cross_module 必须关联至少两条 SURF（组合场景）")
        if not item.get("expected_basis"):
            warnings.append(f"{name}: 缺 expected_basis，只能产出观察不能判 PASS/FAIL")
    return errors, warnings


def scenario_gaps(plan, scenarios):
    """场景覆盖面缺口：目标要求“场景设计”，零场景或只有单点无组合均视为缺口。"""
    gaps = []
    high = [s["id"] for s in (plan.get("surfaces") or []) if s.get("risk") == "high"]
    if not high:
        return gaps
    if not scenarios:
        gaps.append(f"未登记任何对抗场景（SCN-ADV）：{len(high)} 个高风险风险面只有单点探针，"
                    f"组合/序列/状态规避链场景未设计")
        return gaps
    kinds = {s.get("kind") for s in scenarios if isinstance(s, dict)}
    if "cross_module" not in kinds and len(high) >= 2:
        gaps.append(f"缺跨模块组合场景（cross_module）：{len(high)} 个高风险风险面未设计\"各自都对、组合起来坏\"的序列")
    covered = {r for s in scenarios if isinstance(s, dict) for r in (s.get("surfaces") or [])}
    never = sorted(set(high) - covered)
    if never:
        gaps.append(f"高风险风险面未被任何场景覆盖：{never}")
    return gaps


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="校验 adversarial-plan 契约文件；只读，不修改输入")
    parser.add_argument("plan")
    parser.add_argument("--material", help="需求材料文件路径，用于复核 material 指纹")
    parser.add_argument("--cases", help="cases JSON 路径，用于复核 cases_json 指纹")
    parser.add_argument("--scenarios", help="对抗场景伴随文件（SCN-ADV 数组），缺省时按未设计场景告警")
    args = parser.parse_args(argv)
    try:
        plan = load(args.plan)
        summary = validate(plan, args.material, args.cases)
        surface_ids = {s.get("id") for s in (plan.get("surfaces") or [])}
        scenarios = None
        if args.scenarios:
            scenarios = json.loads(Path(args.scenarios).read_text(encoding="utf-8-sig"))
            errors, warnings = validate_scenarios(scenarios, surface_ids)
            summary["scenario_errors"] = errors
            summary["scenario_warnings"] = warnings
            summary["scenario_gaps"] = scenario_gaps(plan, scenarios)
        else:
            summary["scenario_gaps"] = scenario_gaps(plan, None)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"adversarial-plan 输入/校验错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
