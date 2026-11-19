#!/usr/bin/env python3
"""attack_coverage.py — 攻击面覆盖与判据独立性检查（清单式验收的诊断与门禁）。

问题来源：2026-11-18 一次 154 项真实测试，143 项走正向路径、权限 0 项、异常 0 项、
组合 1 项，最终只产出 4 条 FAIL，其中 0 条来自为新攻击面设计的探针。原因是执行范式
是「覆盖确认」（确认功能可用）而非「失效构造」（证明它会坏）——用例数增长不等于
攻击面增长，而报告主指标是通过率，天然奖励"确认正常"。

本脚本只做两件事：
  1. 诊断：把台账摊成路径类型/判据来源/发现方式的分布，给出攻击密度；
  2. 门禁：密度与判据独立性不达标时压结论上限，避免"铺得全"冒充"打得深"。

纪律：
- 只按台账里**显式声明**的字段计算；字段缺失记 unknown 并列入缺口，不用启发式值
  充当机械判据（`--infer` 的粗分结果单列 inferred_*，仅供参考，不进 density）。
- 工具不判缺陷有无，只判"攻击面是否被真正打过"。

用法：
    python scripts/attack_coverage.py --ledger ledger.jsonl
    python scripts/attack_coverage.py --ledger ledger.csv --emit-json out.json --emit-csv out.csv
    python scripts/attack_coverage.py --ledger ledger.jsonl --enforce   # 缺口非空则 rc=2
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

PATH_TYPES = ("positive", "boundary", "exception", "permission", "combo")
DISCOVERY_MODES = ("designed_probe", "incidental", "undetermined")
ORACLE_SOURCES = ("independent", "self_attested", "unknown")

# 阈值依据：每个功能点在正向/边界/异常/权限/组合五类路径中至少有一类非正向，
# 理论下限即 1/5=20%；取 30% 作为"每 3 条至少 1 条非正向"的最低门槛（非目标值）。
DEFAULT_DENSITY_THRESHOLD = 0.30
# 判据独立性：同源自证（用被测物自己的回显当 oracle）不得占多数
DEFAULT_INDEPENDENT_RATE = 0.30

INFER_RULES = (
    ("boundary", r"边界|极限|超长|超限|空值|上界|下界|0 ?份|最大值|最小值|长度"),
    ("permission", r"权限|角色|越权|未登录|他人|租户|无权限|越界访问"),
    ("exception", r"异常|失败|错误|超时|中断|重试|回滚|静默"),
    ("combo", r"组合|同时|并发|交叉|序列|连续|交替"),
)


def _load(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".csv":
        return list(csv.DictReader(text.splitlines()))
    # 整体 JSON（数组或 {"records": [...]}）优先，其次按 JSONL 逐行解析
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return data["records"]
    records = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def _infer_path_type(text):
    for name, pattern in INFER_RULES:
        if re.search(pattern, text):
            return name
    return "positive"


def analyse(records, density_threshold=DEFAULT_DENSITY_THRESHOLD,
            independent_rate=DEFAULT_INDEPENDENT_RATE):
    total = len(records)
    path_counts = {k: 0 for k in PATH_TYPES}
    path_unknown = 0
    inferred_counts = {k: 0 for k in PATH_TYPES}
    oracle = {k: 0 for k in ORACLE_SOURCES}
    modes = {k: 0 for k in DISCOVERY_MODES}
    surf_linked = 0
    methods = {}
    declared_method_missing = 0

    for rec in records:
        if not isinstance(rec, dict):
            continue
        ptype = str(rec.get("path_type") or "").strip().lower()
        if ptype in PATH_TYPES:
            path_counts[ptype] += 1
        else:
            path_unknown += 1
        text = " ".join(str(rec.get(k) or "") for k in ("id", "desc", "title", "note", "text"))
        inferred_counts[_infer_path_type(text)] += 1

        source = str(rec.get("oracle_source") or "").strip().lower()
        oracle[source if source in ORACLE_SOURCES else "unknown"] += 1

        mode = str(rec.get("discovery_mode") or "").strip().lower()
        modes[mode if mode in DISCOVERY_MODES else "undetermined"] += 1

        if str(rec.get("surf_id") or "").strip():
            surf_linked += 1
        method = str(rec.get("method") or "").strip()
        if method:
            methods[method] = methods.get(method, 0) + 1
        else:
            declared_method_missing += 1

    non_positive = sum(v for k, v in path_counts.items() if k != "positive")
    # 分母只取显式声明了 path_type 的条目：字段全缺时报 NA，不以 0 冒充"一条非正向都没有"
    declared_total = total - path_unknown
    density = round(non_positive / declared_total, 4) if declared_total else "NA"
    independent_total = oracle["independent"] + oracle["self_attested"]
    indep_rate = (round(oracle["independent"] / independent_total, 4)
                  if independent_total else "NA")

    gaps = []
    if not total:
        gaps.append({"code": "empty_ledger", "message": "台账为空，无任何执行记录"})
    if path_unknown:
        gaps.append({"code": "path_type_missing",
                     "message": f"{path_unknown}/{total} 条未声明 path_type（positive/boundary/exception/permission/combo），"
                                "攻击密度无法机械计算；推断值仅供参考，不作判据"})
    if isinstance(density, float) and density < density_threshold:
        gaps.append({"code": "attack_density_low",
                     "message": f"攻击密度 {density} 低于门槛 {density_threshold}："
                                f"非正向路径 {non_positive}/{total}，本轮以确认功能可用为主"})
    if isinstance(indep_rate, float) and indep_rate < independent_rate:
        gaps.append({"code": "oracle_self_attested",
                     "message": f"判据独立性 {indep_rate} 低于门槛 {independent_rate}："
                                f"同源自证 {oracle['self_attested']} 条（用被测物自己的回显当 oracle）"})
    if modes["incidental"] and not modes["designed_probe"]:
        gaps.append({"code": "no_designed_probe",
                     "message": f"缺陷全部来自偶然撞见（incidental {modes['incidental']} 条），"
                                "没有一条来自为失效假设设计的探针"})
    if total and surf_linked / total < 0.5:
        gaps.append({"code": "surf_linkage_low",
                     "message": f"仅 {surf_linked}/{total} 条关联到已登记风险面（surf_id）："
                                "攻击点未追溯到失效假设，无法判断覆盖了哪些失效方式"})
    if declared_method_missing:
        gaps.append({"code": "method_missing",
                     "message": f"{declared_method_missing}/{total} 条未标注探针方法（方法库 ID），"
                                "无法核对方法多样性"})

    return {
        "total": total,
        "path_type": {**path_counts, "unknown": path_unknown},
        "inferred_path_type": inferred_counts,
        "inferred_note": "关键词粗分，仅作诊断参考，不参与门禁判定",
        "attack_density": density,
        "density_threshold": density_threshold,
        "oracle_source": {**oracle, "independent_rate": indep_rate},
        "discovery_mode": modes,
        "surf_linked": surf_linked,
        "surf_linkage_rate": round(surf_linked / total, 4) if total else "NA",
        "methods": methods,
        "method_missing": declared_method_missing,
        "gaps": gaps,
        "verdict_ceiling": "待核实" if gaps else "通过",
        "scope": "只判攻击面覆盖与判据独立性，不判缺陷有无、不替代对抗门禁与探索门禁",
    }


def _emit_csv(result, path):
    rows = []
    for key in ("path_type", "oracle_source", "discovery_mode"):
        for name, value in result[key].items():
            rows.append({"section": key, "metric": name, "value": value})
    for name in ("total", "attack_density", "density_threshold", "surf_linked",
                 "surf_linkage_rate", "method_missing", "verdict_ceiling"):
        rows.append({"section": "summary", "metric": name, "value": result[name]})
    for gap in result["gaps"]:
        rows.append({"section": "gap", "metric": gap["code"], "value": gap["message"]})
    with Path(path).open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=["section", "metric", "value"])
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description="攻击面覆盖与判据独立性检查")
    parser.add_argument("--ledger", required=True, help="执行台账（JSONL 或 CSV）")
    parser.add_argument("--emit-json", help="结论输出路径")
    parser.add_argument("--emit-csv", help="CSV 导出路径（UTF-8-sig）")
    parser.add_argument("--density-threshold", type=float, default=DEFAULT_DENSITY_THRESHOLD)
    parser.add_argument("--independent-rate", type=float, default=DEFAULT_INDEPENDENT_RATE)
    parser.add_argument("--enforce", action="store_true",
                        help="存在缺口时以退出码 2 结束（用于门禁链）")
    args = parser.parse_args(argv)

    records = _load(args.ledger)
    result = analyse(records, args.density_threshold, args.independent_rate)
    if args.emit_json:
        Path(args.emit_json).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    if args.emit_csv:
        _emit_csv(result, args.emit_csv)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.enforce and result["gaps"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
