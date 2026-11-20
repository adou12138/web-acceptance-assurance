#!/usr/bin/env python3
"""eval_cases.py — 用例生成质量基线（**只评分，不生成**）。

为什么需要它：门禁只能保证"不合法会被拦"，保证不了"合法但空洞"。改门禁、改 prompt、改判据后，
需要一个同口径的尺子做跨版本比对，否则退化只能靠人眼发现。

评分口径（全部机械可复算，不含语义判定）：
- `coverage_layers_missing`：四层矩阵缺几层（`scripts/coverage_matrix.py`）
- `feature_l1_ratio`：有 L1 用例的功能点占比
- `method_variety`：有见证的设计方法数
- `assertion_density`：平均每用例断言数
- `vague_or_placeholder`：空话/占位命中数（`case_data.validate` 的 errors 中相关条数）
- `smoke_ratio` / `p0_ratio`：冒烟与 P0 占比
- `gaps_open`：`coverage_gaps` 条数（缺口是义务，不是得分项）
- `structural_errors`：结构校验错误数（v3 G10：增加即硬退化，无效产物不能显示无退化）

退化方向（v3 G10）：缺层/结构错误/空话/未关闭缺口上升、或方法数/断言密度/L1 比例下降，均判退化并使退出码 1。
固定场景与变异/已知答案盲测见 `docs/evals-baseline/detection-protocol.md`；本脚本只做机械指标与基线比对。

**能力边界（必须如实披露）**：本脚本不判断"用例是否抓到了真缺陷"，那需要真实执行与缺陷证据；
语义质量只能靠评审抽检与独立检出评测。基线相同不代表质量相同。

用法：
    python scripts/eval_cases.py --cases <cases JSON> --out <报告 csv> [--baseline <json>] [--save-baseline <json>]
退出码：0=评分完成（有基线时差异在阈值内）；1=与基线存在超阈值退化；2=用法/输入错误。
"""

import argparse
import csv
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "skills" / "case-generator" / "config" / "policy.json"
# 退化方向分两类（v3 G10）：
# - LOWER_IS_WORSE：数值上升即退化（缺层、结构错误、空话、未关闭缺口）
# - HIGHER_IS_WORSE：数值下降即退化（方法数、断言密度、L1 比例）
LOWER_IS_WORSE = ("coverage_layers_missing", "structural_errors", "vague_or_placeholder", "gaps_open")
HIGHER_IS_WORSE = ("method_variety", "assertion_density", "feature_l1_ratio")
DEGRADE_KEYS = LOWER_IS_WORSE + HIGHER_IS_WORSE

# 基线格式版本。基线不再只是一组数字：它同时固定了「输入指纹」与「工具版本」，
# 否则换一份用例也能拿来比，指标对不上时无从判断是退化还是换题了。
BASELINE_SCHEMA = "case-eval-baseline/2"
METRICS_SCHEMA = "case-eval-metrics/1"
TOOL_VERSION = "2"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def metrics(cases, policy):
    cm = _load_module("coverage_matrix", ROOT / "scripts" / "coverage_matrix.py")
    cd = _load_module("case_data", ROOT / "scripts" / "case_data.py")
    matrix = cm.build_matrix(cases, policy)
    tcs = [tc for tc in (cases.get("testcases") or []) if isinstance(tc, dict)]
    total = len(tcs) or 1
    l1_hit = {entry["feature"] for entry in
              (matrix["layer_matrix"]["L1"]["detail"].get("covered_features") or [])}
    features = matrix["counts"]["features"] or 1
    assertions = sum(len(tc.get("assertions") or []) for tc in tcs)
    errors, _ = cd.validate(cases)
    vague = sum(1 for e in errors if "空话" in e or "占位" in e)
    return {
        "testcases": len(tcs),
        "features": matrix["counts"]["features"],
        "coverage_layers_missing": len(matrix["missing_layers"]),
        "feature_l1_ratio": round(len(l1_hit) / features, 3),
        "method_variety": matrix["counts"]["methods_witnessed"],
        "assertion_density": round(assertions / total, 2),
        "vague_or_placeholder": vague,
        "smoke_ratio": round(sum(1 for tc in tcs if "smoke" in (tc.get("suites") or [])) / total, 3),
        "p0_ratio": round(sum(1 for tc in tcs if tc.get("priority") == "P0") / total, 3),
        "gaps_open": len([g for g in (cases.get("coverage_gaps") or []) if isinstance(g, dict)]),
        "structural_errors": len(errors),
    }


def detail(cases, policy):
    """条目级摘要：结构、覆盖与断言三类的可比明细。

    指标级差异只说"哪一项变差了"，不说明"差在哪一条"。差异定位要能落到
    具体用例与具体缺口上，否则拿到一份"断言密度下降"的报告仍然不知道该改哪条。
    """
    cm = _load_module("coverage_matrix", ROOT / "scripts" / "coverage_matrix.py")
    cd = _load_module("case_data", ROOT / "scripts" / "case_data.py")
    matrix = cm.build_matrix(cases, policy)
    errors, _warnings = cd.validate(cases)
    per_case = []
    for tc in cases.get("testcases") or []:
        if not isinstance(tc, dict):
            continue
        per_case.append({
            "id": str(tc.get("id") or ""),
            "assertions": len(tc.get("assertions") or []),
            "priority": str(tc.get("priority") or ""),
            "suites": sorted(tc.get("suites") or []),
            "layer": str(tc.get("layer") or ""),
            "methods": sorted((tc.get("method_evidence") or {}).keys()),
        })
    gaps = []
    for gap in cases.get("coverage_gaps") or []:
        if isinstance(gap, dict):
            gaps.append({"id": str(gap.get("id") or ""),
                         "target": str(gap.get("target") or gap.get("feature") or ""),
                         "reason": str(gap.get("reason") or gap.get("why") or "")})
    return {
        "per_case": per_case,
        "structural_errors": list(errors),
        "coverage_gaps": gaps,
        "missing_layers": list(matrix.get("missing_layers") or []),
        "methods_witnessed": matrix.get("counts", {}).get("methods_witnessed"),
    }


def diff_detail(current, baseline):
    """条目级差异定位：结构、覆盖、断言三处分别给出"多了什么/少了什么"。

    基线没有条目级摘要（旧格式）时返回可定位=False——不猜、不用当前值冒充基线。
    """
    if not baseline:
        return {"comparable": False,
                "reason": "基线不含条目级摘要（旧格式基线），无法做条目级定位",
                "structural": {}, "coverage": {}, "assertions": {}, "cases": {}}
    cur_cases = {c["id"]: c for c in current.get("per_case") or []}
    base_cases = {c["id"]: c for c in baseline.get("per_case") or []}

    def _multiset(values):
        out = {}
        for value in values or []:
            out[value] = out.get(value, 0) + 1
        return out

    cur_errors = _multiset(current.get("structural_errors"))
    base_errors = _multiset(baseline.get("structural_errors"))
    structural = {
        "added": sorted([e for e, n in cur_errors.items()
                         for _ in range(n - base_errors.get(e, 0))]),
        "removed": sorted([e for e, n in base_errors.items()
                           for _ in range(n - cur_errors.get(e, 0))]),
    }
    cur_gaps = {g.get("id") or g.get("target"): g for g in current.get("coverage_gaps") or []}
    base_gaps = {g.get("id") or g.get("target"): g for g in baseline.get("coverage_gaps") or []}
    coverage = {
        "gaps_added": [cur_gaps[k] for k in sorted(cur_gaps) if k not in base_gaps],
        "gaps_removed": [base_gaps[k] for k in sorted(base_gaps) if k not in cur_gaps],
        "layers_before_missing": list(baseline.get("missing_layers") or []),
        "layers_now_missing": list(current.get("missing_layers") or []),
    }
    assertions = {"thinner": [], "denser": []}
    for case_id in sorted(set(cur_cases) & set(base_cases)):
        before = base_cases[case_id].get("assertions", 0)
        now = cur_cases[case_id].get("assertions", 0)
        if now < before:
            assertions["thinner"].append({"id": case_id, "before": before, "now": now})
        elif now > before:
            assertions["denser"].append({"id": case_id, "before": before, "now": now})
    cases = {
        "added": sorted(set(cur_cases) - set(base_cases)),
        "removed": sorted(set(base_cases) - set(cur_cases)),
    }
    return {"comparable": True, "reason": "",
            "structural": structural, "coverage": coverage,
            "assertions": assertions, "cases": cases}


def _read_baseline(payload):
    """读基线，兼容两种格式。

    - 新格式（`case-eval-baseline/2`）：含 metrics / detail / input 指纹；
    - 旧格式（顶层即指标，如 fixture-12.json）：只有指标，条目级定位不可用。

    旧格式**保留可用**：M10 要求保留 fixture-12，它是历史口径的锚点。
    """
    if isinstance(payload, dict) and isinstance(payload.get("metrics"), dict):
        return payload.get("metrics") or {}, payload.get("detail"), payload
    return dict(payload or {}), None, None


def compare(current, baseline):
    """退化方向：缺层变多、方法变少、断言变稀、L1 比例下降。"""
    worse = {}
    if not baseline:
        return worse
    for key in DEGRADE_KEYS:
        if key not in baseline or key not in current:
            continue
        now, before = current[key], baseline[key]
        if key in LOWER_IS_WORSE:
            if now > before:
                worse[key] = {"before": before, "now": now}
        elif now < before:
            worse[key] = {"before": before, "now": now}
    return worse


def main():
    ap = argparse.ArgumentParser(description="用例生成质量基线（只评分不生成）")
    ap.add_argument("--cases", required=True)
    ap.add_argument("--out", default="", help="评分 CSV 输出路径")
    ap.add_argument("--out-diff", default="", help="条目级差异定位 CSV 输出路径（UTF-8 BOM）")
    ap.add_argument("--baseline", help="基线 JSON（比对用）")
    ap.add_argument("--save-baseline", help="把本次评分存为基线 JSON")
    args = ap.parse_args()
    try:
        cases_text = Path(args.cases).read_text(encoding="utf-8-sig")
        cases = json.loads(cases_text)
        policy = json.loads(POLICY.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"输入解析失败：{exc}", file=sys.stderr)
        return 2
    input_sha = hashlib.sha256(cases_text.encode("utf-8")).hexdigest()
    current = metrics(cases, policy)
    current_detail = detail(cases, policy)
    baseline_metrics, baseline_detail, baseline_doc = {}, None, None
    if args.baseline:
        try:
            baseline_doc = json.loads(Path(args.baseline).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"基线读取失败：{exc}", file=sys.stderr)
            return 2
        baseline_metrics, baseline_detail, _ = _read_baseline(baseline_doc)
    worse = compare(current, baseline_metrics)
    located = diff_detail(current_detail, baseline_detail)
    # 输入与版本固定：基线记了输入指纹与工具版本，输入不同或工具换版时，
    # 机械指标口径可能已变——此时仍给出差异供人看，但标明不可直接当退化判定。
    comparable = True
    comparability_notes = []
    if baseline_doc:
        base_input = (baseline_doc.get("input") or {}).get("sha256")
        if base_input and base_input != input_sha:
            comparable = False
            comparability_notes.append(
                "基线与本次输入的指纹不同（换题了）：机械指标口径可能已变，"
                "差异结果仅供参考，不得直接当退化判定")
        base_version = baseline_doc.get("tool_version")
        if base_version and str(base_version) != TOOL_VERSION:
            comparable = False
            comparability_notes.append(
                f"基线由 eval_cases v{base_version} 生成，本次为 v{TOOL_VERSION}："
                "评分口径可能已调整")
    else:
        comparability_notes.append("未提供基线：本次只有评分，没有对照")
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["指标", "本次", "基线", "变化", "条目级定位"])
            locator = _locator_summary(located, worse)
            for key, value in current.items():
                before = baseline_metrics.get(key, "")
                writer.writerow([key, value, before, _delta(key, value, before), locator.get(key, "")])
            writer.writerow(["degraded", len(worse), "", "", ""])
    if args.out_diff:
        _write_diff_csv(Path(args.out_diff), located, current_detail, baseline_detail)
    if args.save_baseline:
        path = Path(args.save_baseline)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema": BASELINE_SCHEMA,
            "metrics_schema": METRICS_SCHEMA,
            "tool_version": TOOL_VERSION,
            "scored_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "input": {"path": str(args.cases), "sha256": input_sha},
            "metrics": current,
            "detail": current_detail,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"metrics": current, "degraded": worse,
                      "detail": located,
                      "comparable": comparable,
                      "comparability_notes": comparability_notes,
                      "input_sha256": input_sha,
                      "note": "机械指标；语义质量需评审抽检，基线相同不代表质量相同"},
                     ensure_ascii=False))
    return 1 if worse else 0


def _delta(key, value, before):
    """指标变化：有基线才给差值，缺基线留空（不当 0）。"""
    if before == "" or before is None:
        return ""
    try:
        return round(float(value) - float(before), 4)
    except (TypeError, ValueError):
        return ""


def _locator_summary(located, worse):
    """把条目级定位压成"哪个指标对应哪几条"，写进主 CSV 的定位列。"""
    if not located.get("comparable"):
        return {}
    mapping = {}
    if "structural_errors" in worse:
        mapping["structural_errors"] = "；".join(
            (located["structural"].get("added") or [])[:5]) or "见差异明细"
    if "gaps_open" in worse:
        mapping["gaps_open"] = "；".join(
            str(g.get("id") or g.get("target"))
            for g in (located["coverage"].get("gaps_added") or [])[:5]) or "见差异明细"
    if "coverage_layers_missing" in worse:
        mapping["coverage_layers_missing"] = "现缺层：" + "、".join(
            located["coverage"].get("layers_now_missing") or []) or "见差异明细"
    if "assertion_density" in worse:
        mapping["assertion_density"] = "；".join(
            f"{item['id']} {item['before']}→{item['now']}"
            for item in (located["assertions"].get("thinner") or [])[:5]) or "见差异明细"
    return mapping


def _write_diff_csv(path, located, current_detail, baseline_detail):
    """条目级差异明细 CSV：结构、覆盖、断言三处逐条列出。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["维度", "变化", "定位", "说明"])
        if not located.get("comparable"):
            writer.writerow(["全部", "不可定位", "", located.get("reason") or "基线缺条目级摘要"])
            return
        for item in located["structural"].get("added") or []:
            writer.writerow(["结构", "新增错误", item, "本次新增的结构校验错误"])
        for item in located["structural"].get("removed") or []:
            writer.writerow(["结构", "已修复", item, "基线有、本次已无的结构错误"])
        for gap in located["coverage"].get("gaps_added") or []:
            writer.writerow(["覆盖", "新增缺口", gap.get("id") or gap.get("target"),
                             gap.get("reason") or ""])
        for gap in located["coverage"].get("gaps_removed") or []:
            writer.writerow(["覆盖", "缺口已闭合", gap.get("id") or gap.get("target"),
                             gap.get("reason") or ""])
        if located["coverage"].get("layers_now_missing"):
            writer.writerow(["覆盖", "缺层",
                             "、".join(located["coverage"]["layers_now_missing"]),
                             "基线缺层：" + "、".join(
                                 located["coverage"].get("layers_before_missing") or [])])
        for item in located["assertions"].get("thinner") or []:
            writer.writerow(["断言", "变稀", item["id"],
                             f"断言数 {item['before']} → {item['now']}"])
        for item in located["assertions"].get("denser") or []:
            writer.writerow(["断言", "变密", item["id"],
                             f"断言数 {item['before']} → {item['now']}"])
        for case_id in located["cases"].get("added") or []:
            writer.writerow(["用例", "新增", case_id, ""])
        for case_id in located["cases"].get("removed") or []:
            writer.writerow(["用例", "删除", case_id, ""])


if __name__ == "__main__":
    sys.exit(main())
