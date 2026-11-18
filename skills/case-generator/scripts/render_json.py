#!/usr/bin/env python3
"""render_json.py — 交付 JSON 视图（给程序/下游消费的派生视图）。

结构契约见 references/output-json.md。只渲染不判定；投影保留用例全部字段（含断言），
下游不得把视图当唯一事实源回写。渲染后自检：testcases[].id 集合与源 JSON 完全相等。

退出码：0=成功；1=一致性校验失败；2=用法/输入错误。
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def load(path, code=2):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"输入解析失败：{exc}", file=sys.stderr)
        sys.exit(code)


def build_view(cases):
    tcs = [tc for tc in cases.get("testcases", []) if isinstance(tc, dict)]
    meta = cases.get("meta") or {}
    summary = {
        "requirements": len(cases.get("requirements", [])),
        "features": len(cases.get("features", [])),
        "feature_points": sum(len(f.get("children", [])) for f in cases.get("features", [])),
        "acceptance": len(cases.get("acceptance", [])),
        "scenarios": len(cases.get("scenarios", []) or []),
        "testcases": len(tcs),
        "smoke": sum(1 for tc in tcs if "smoke" in (tc.get("suites") or [])),
        "p0": sum(1 for tc in tcs if tc.get("priority") == "P0"),
    }
    view = {
        "schema_version": "1.0",
        "view": "delivery-json",
        "note": "派生视图：只读投影，唯一事实源是 cases-<short>.json；本文件不得回写。",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": {
            "requirement_id": meta.get("id", ""),
            "title": meta.get("title", ""),
            "flow_kind": meta.get("flow_kind", ""),
        },
        "summary": summary,
        "testcases": [dict(tc) for tc in tcs],
        "trace": {
            "requirements": cases.get("requirements", []),
            "features": cases.get("features", []),
            "acceptance": cases.get("acceptance", []),
        },
    }
    if cases.get("scenarios"):
        view["trace"]["scenarios"] = cases["scenarios"]
    # 缺口必须随视图走：Excel 有附页、测试点视图随行，交付 JSON 也不能丢（否则"缺口不静默"只对部分视图成立）
    if cases.get("coverage_gaps"):
        view["coverage_gaps"] = cases["coverage_gaps"]
    return view


def main():
    ap = argparse.ArgumentParser(description="交付 JSON 视图渲染")
    ap.add_argument("--cases", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cases = load(args.cases)
    view = build_view(cases)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(view, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    src_ids = sorted(str(tc.get("id")) for tc in cases.get("testcases", []) if isinstance(tc, dict))
    view_ids = sorted(str(tc.get("id")) for tc in view["testcases"])
    if src_ids != view_ids:
        print(f"视图一致性失败：投影 {len(view_ids)} 条 vs 源 {len(src_ids)} 条集合不等；视图作废重渲", file=sys.stderr)
        sys.exit(1)
    print(f"rendered -> {out}（{len(view_ids)} 条，一致性通过）")
    sys.exit(0)


if __name__ == "__main__":
    main()