#!/usr/bin/env python3
"""render_testpoints.py — 测试点评审视图（**派生**，不替代用例）。

用途：需求对齐 / 评审抽查时，只给对方"要测什么"，不必给完整步骤。
数据来源仍是 `cases-<short>.json`（唯一事实源），本脚本**不生成、不改写**任何用例；
因此它不能替代用例生成成本，只是把已有用例按「功能点 × 验收标准」折叠成测试点清单。

输出列（8 列，utf-8-sig）：
功能点 / 功能点名称 / 验收标准 / 测试点 / 覆盖层 / 设计方法 / 用例数 / 最高优先级 / 含冒烟（共 9 列）

边界：
- 产物**不得**进执行台账（`execution_ledger.init` 只接受含 `testcases[]` 的 cases JSON，会直接拒绝本 CSV）。
- 覆盖缺口随行带出：`coverage_gaps` 按 target 前缀匹配到功能点，避免"点看起来齐全但缺口被吞"。
- `--verify` 回读校验 BOM / 表头 / 列数 / 行数。

退出码：0=成功；1=回读不一致；2=用法/输入错误。
"""

import argparse
import csv
import json
import sys
from pathlib import Path

HEADERS = ["功能点", "功能点名称", "验收标准", "测试点", "覆盖层", "设计方法",
           "用例数", "最高优先级", "含冒烟"]
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}


def load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"输入解析失败：{exc}", file=sys.stderr)
        sys.exit(2)


def feature_names(cases):
    names = {}
    for module in cases.get("features") or []:
        for child in (module.get("children") or []) if isinstance(module, dict) else []:
            if isinstance(child, dict) and child.get("id"):
                names[str(child["id"])] = str(child.get("name") or "")
    return names


def gaps_for(gaps, feature_id):
    hits = []
    for gap in gaps or []:
        if not isinstance(gap, dict):
            continue
        target = str(gap.get("target") or "")
        # 精确 ID 边界：FP-01 不得命中 FP-010（v3 G06 呈现完整性）
        if target == feature_id or target.startswith(feature_id + ".") or target.startswith(feature_id + " "):
            hits.append(f"{gap.get('kind')}:{gap.get('obligation') or gap.get('reason')}")
    return "；".join(hits)


def build_rows(cases):
    names = feature_names(cases)
    gaps = cases.get("coverage_gaps") or []
    groups = {}
    for tc in cases.get("testcases") or []:
        if not isinstance(tc, dict):
            continue
        for fid in tc.get("feature_ids") or [""]:
            for ac in tc.get("acceptance_ids") or [""]:
                key = (str(fid), str(ac))
                groups.setdefault(key, []).append(tc)
    rows = []
    for (fid, ac), items in sorted(groups.items()):
        layers = sorted({str(f) for tc in items for f in (tc.get("coverage_flags") or [])})
        methods = sorted({str(m) for tc in items for m in (tc.get("method_evidence") or {})})
        priority = min((str(tc.get("priority", "P2")) for tc in items),
                       key=lambda p: PRIORITY_ORDER.get(p, 9))
        smoke = "是" if any("smoke" in (tc.get("suites") or []) for tc in items) else "否"
        points = "；".join(str(tc.get("title", "")) for tc in items)
        gap_note = gaps_for(gaps, fid)
        if gap_note:
            points = f"{points}〔缺口：{gap_note}〕"
        rows.append([fid, names.get(fid, ""), ac, points, "/".join(layers),
                     "/".join(methods), str(len(items)), priority, smoke])
    # v3 G18：义务未被任何用例绑定时，单独出测试点行（无 testcases 也出评审视图）；
    # 已绑定的义务由所属功能点行体现，不重复出行，避免同一功能点在视图里裂成多行。
    case_ids = {str(tc.get("id")) for tc in (cases.get("testcases") or []) if isinstance(tc, dict)}
    for ob in cases.get("coverage_obligations") or []:
        if not isinstance(ob, dict):
            continue
        bound = [str(x) for x in (ob.get("tc_ids") or []) if str(x) in case_ids]
        if bound:
            continue
        target = str(ob.get("target") or "")
        fid = target.split(".")[0].split(" ")[0]
        rows.append([fid, names.get(fid, ""), str(ob.get("source") or ""),
                     f"{ob.get('id', '')}｜{target}（{ob.get('kind', '')}）",
                     str(ob.get("layer") or ""), "义务", str(len(bound)),
                     "P0" if ob.get("required") else "P2", "否"])
    return rows


def verify(path, expected_rows):
    path = Path(path)
    if not path.read_bytes().startswith(b"\xef\xbb\xbf"):
        raise ValueError(f"{path.name}: 缺少 utf-8-sig BOM")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    if not rows or rows[0] != HEADERS:
        raise ValueError(f"{path.name}: 表头不一致")
    if any(len(r) != len(HEADERS) for r in rows):
        raise ValueError(f"{path.name}: 列数不一致")
    if len(rows) - 1 != expected_rows:
        raise ValueError(f"{path.name}: 行数 {len(rows) - 1} != 预期 {expected_rows}")


def main():
    ap = argparse.ArgumentParser(description="测试点评审视图（由 cases JSON 派生）")
    ap.add_argument("--cases", required=True)
    ap.add_argument("--out", required=True, help="输出 CSV 路径（如 test_points.csv）")
    ap.add_argument("--verify", action="store_true", help="写后回读校验 BOM/表头/列数/行数")
    args = ap.parse_args()

    cases = load(args.cases)
    has_tc = isinstance(cases.get("testcases"), list)
    has_ob = isinstance(cases.get("coverage_obligations"), list) and bool(cases.get("coverage_obligations"))
    if not has_tc and not has_ob:
        print("输入既无 testcases[] 也无 coverage_obligations[]：无可渲染的测试点", file=sys.stderr)
        sys.exit(2)
    rows = build_rows(cases)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(HEADERS)
        writer.writerows(rows)
    if args.verify:
        try:
            verify(out, len(rows))
        except ValueError as exc:
            print(f"回读校验失败：{exc}", file=sys.stderr)
            sys.exit(1)
    print(json.dumps({"out": str(out), "rows": len(rows),
                      "note": "派生视图：不替代用例，不进执行台账"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
