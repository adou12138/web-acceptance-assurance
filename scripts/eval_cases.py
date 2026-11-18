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
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "skills" / "case-generator" / "config" / "policy.json"
# 退化方向分两类（v3 G10）：
# - LOWER_IS_WORSE：数值上升即退化（缺层、结构错误、空话、未关闭缺口）
# - HIGHER_IS_WORSE：数值下降即退化（方法数、断言密度、L1 比例）
LOWER_IS_WORSE = ("coverage_layers_missing", "structural_errors", "vague_or_placeholder", "gaps_open")
HIGHER_IS_WORSE = ("method_variety", "assertion_density", "feature_l1_ratio")
DEGRADE_KEYS = LOWER_IS_WORSE + HIGHER_IS_WORSE


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
    ap.add_argument("--baseline", help="基线 JSON（比对用）")
    ap.add_argument("--save-baseline", help="把本次评分存为基线 JSON")
    args = ap.parse_args()
    try:
        cases = json.loads(Path(args.cases).read_text(encoding="utf-8-sig"))
        policy = json.loads(POLICY.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"输入解析失败：{exc}", file=sys.stderr)
        return 2
    current = metrics(cases, policy)
    baseline = {}
    if args.baseline:
        try:
            baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"基线读取失败：{exc}", file=sys.stderr)
            return 2
    worse = compare(current, baseline)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["指标", "本次", "基线"])
            for key, value in current.items():
                writer.writerow([key, value, baseline.get(key, "")])
            writer.writerow(["degraded", len(worse), ""])
    if args.save_baseline:
        path = Path(args.save_baseline)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"metrics": current, "degraded": worse,
                      "note": "机械指标；语义质量需评审抽检，基线相同不代表质量相同"},
                     ensure_ascii=False))
    return 1 if worse else 0


if __name__ == "__main__":
    sys.exit(main())
