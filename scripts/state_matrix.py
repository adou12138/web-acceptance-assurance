#!/usr/bin/env python3
"""state_matrix.py — 状态组合矩阵展开与未覆盖格点追踪。

为什么需要它：手工列的用例只会覆盖"想到的组合"。角色 × 状态 × 数据条件这类三维
组合，人脑列到第 8 条就开始重复，而状态机规避、跨状态数据残留、越权后的状态不一致
恰好都藏在没列出来的那些格点里。

做法是机械的：按维度做笛卡尔积，每个格点给一个稳定 ID，执行后把没覆盖的点摊出来。
不要求全覆盖（组合爆炸时该排优先级），但**未覆盖格点必须显式存在**——"没列出来"
和"列了但没测"在报告里必须长得不一样。

用法：
    python scripts/state_matrix.py --input matrix.json
    python scripts/state_matrix.py --input matrix.json --emit-json out.json --emit-csv out.csv
    python scripts/state_matrix.py --input matrix.json --enforce   # 有未覆盖格点时 rc=2

输入：
    {
      "dimensions": {"role": ["owner", "editor"], "state": ["draft", "published"],
                     "data": ["empty", "max"]},
      "executed": ["MAT-1a2b3c4d", ...],
      "exclude": [{"cell": "MAT-...", "reason": "该角色无此数据入口"}]
    }
"""

import argparse
import csv
import hashlib
import itertools
import json
import sys
from pathlib import Path

MAX_CELLS = 2000


def cell_id(dimension_names, values):
    """稳定 ID：同一组取值永远得到同一 ID，跨轮次可比。"""
    payload = "|".join(f"{name}={value}" for name, value in zip(dimension_names, values))
    return "MAT-" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:8]


def expand(dimensions):
    if not isinstance(dimensions, dict) or not dimensions:
        raise ValueError("dimensions 必须为非空对象 {维度: [取值...]}")
    names = sorted(dimensions)
    axes = []
    for name in names:
        values = dimensions[name]
        if not isinstance(values, list) or not values:
            raise ValueError(f"dimensions.{name}: 必须为非空数组")
        cleaned = []
        for value in values:
            text = str(value).strip()
            if not text:
                raise ValueError(f"dimensions.{name}: 取值不能为空")
            if text in cleaned:
                raise ValueError(f"dimensions.{name}: 取值重复 {text}")
            cleaned.append(text)
        axes.append(cleaned)
    total = 1
    for axis in axes:
        total *= len(axis)
    if total > MAX_CELLS:
        raise ValueError(f"组合数 {total} 超过上限 {MAX_CELLS}：先按风险裁剪维度再展开，"
                         "不要一次铺满（铺满等于没优先级）")
    cells = []
    for combo in itertools.product(*axes):
        cells.append({"cell_id": cell_id(names, combo),
                      "values": dict(zip(names, combo))})
    return names, cells


def analyse(payload):
    if not isinstance(payload, dict):
        raise ValueError("输入必须为对象")
    names, cells = expand(payload.get("dimensions") or {})
    executed = {str(value).strip() for value in (payload.get("executed") or [])}
    excludes = {}
    for item in payload.get("exclude") or []:
        if not isinstance(item, dict):
            raise ValueError("exclude 条目必须为对象")
        ident = str(item.get("cell") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not ident or not reason:
            raise ValueError("exclude 条目必须同时有 cell 与 reason")
        excludes[ident] = reason

    known = {cell["cell_id"] for cell in cells}
    unknown_executed = sorted(executed - known - set(excludes))
    unknown_excludes = sorted(set(excludes) - known)

    covered, uncovered, excluded = [], [], []
    for cell in cells:
        bucket = {"cell_id": cell["cell_id"], "values": cell["values"]}
        if cell["cell_id"] in executed:
            covered.append(bucket)
        elif cell["cell_id"] in excludes:
            excluded.append({**bucket, "reason": excludes[cell["cell_id"]]})
        else:
            uncovered.append(bucket)

    # 每个维度的每个取值至少要在一个已覆盖或已豁免的格点里出现，
    # 否则"这个角色整体没测"会被总体覆盖率掩盖掉。
    value_gaps = []
    for name in names:
        for value in (payload["dimensions"][name]):
            text = str(value).strip()
            seen = any(cell["values"][name] == text for cell in covered + excluded)
            if not seen:
                value_gaps.append({"dimension": name, "value": text})

    total = len(cells)
    rate = round(len(covered) / total, 4) if total else None
    gaps = []
    if uncovered:
        gaps.append({"code": "cells_uncovered",
                     "message": f"{len(uncovered)}/{total} 个格点未执行且未豁免："
                                + "、".join(c["cell_id"] for c in uncovered[:6])})
    if value_gaps:
        detail = "、".join(f"{g['dimension']}={g['value']}" for g in value_gaps[:6])
        gaps.append({"code": "value_never_exercised",
                     "message": f"以下取值一次都没出现在已覆盖/已豁免的格点里：{detail}"})
    if unknown_executed:
        gaps.append({"code": "executed_cell_unknown",
                     "message": f"executed 里有不存在的格点 ID：{unknown_executed[:5]}"})
    if unknown_excludes:
        gaps.append({"code": "exclude_cell_unknown",
                     "message": f"exclude 里有不存在的格点 ID：{unknown_excludes[:5]}"})

    return {
        "dimensions": names,
        "total_cells": total,
        "covered": len(covered),
        "excluded": len(excluded),
        "uncovered": len(uncovered),
        "coverage_rate": rate,
        "covered_cells": covered,
        "uncovered_cells": uncovered,
        "excluded_cells": excluded,
        "value_gaps": value_gaps,
        "gaps": gaps,
        "verdict_ceiling": "待核实" if gaps else "通过",
        "scope": "只判状态组合的覆盖，不判每个格点的实测结论；未覆盖格点必须进报告",
    }


def _emit_csv(result, path):
    fields = ["cell_id", "status", "values", "reason"]
    with Path(path).open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for cell in result["covered_cells"]:
            writer.writerow({"cell_id": cell["cell_id"], "status": "covered",
                             "values": json.dumps(cell["values"], ensure_ascii=False)})
        for cell in result["uncovered_cells"]:
            writer.writerow({"cell_id": cell["cell_id"], "status": "uncovered",
                             "values": json.dumps(cell["values"], ensure_ascii=False)})
        for cell in result["excluded_cells"]:
            writer.writerow({"cell_id": cell["cell_id"], "status": "excluded",
                             "values": json.dumps(cell["values"], ensure_ascii=False),
                             "reason": cell["reason"]})


def main(argv=None):
    parser = argparse.ArgumentParser(description="状态组合矩阵展开与覆盖追踪")
    parser.add_argument("--input", required=True)
    parser.add_argument("--emit-json")
    parser.add_argument("--emit-csv")
    parser.add_argument("--enforce", action="store_true",
                        help="存在未覆盖格点时以退出码 2 结束")
    args = parser.parse_args(argv)
    payload = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
    result = analyse(payload)
    if args.emit_json:
        Path(args.emit_json).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    if args.emit_csv:
        _emit_csv(result, args.emit_csv)
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ("covered_cells", "excluded_cells")},
                     ensure_ascii=False, indent=2))
    if args.enforce and result["gaps"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
