#!/usr/bin/env python3
"""legacy_scan.py — 只读扫描存量 cases，判断 v1.26.0 的义务要求会影响哪些文件。

用法：
    python scripts/legacy_scan.py <cases.json | 目录> [更多路径...] [--json]

分类：
- `obligations`：已声明 coverage_obligations，按新口径判定；
- `legacy`：未声明义务但已登记 meta.legacy_source，按存量判 WARN 并披露未迁移义务；
- `blocked`：既无义务也无 legacy 登记，v1.26.0 起 COV-LAYERS 判失败；
- `unreadable`：不是可识别的 cases JSON（缺 meta/testcases），跳过。

只读，不改文件；退出码 0 表示扫描完成（不影响文件内容），2 表示参数/路径错误。
"""

import argparse
import json
import sys
from pathlib import Path


def classify(data):
    if not isinstance(data, dict):
        return "unreadable", "root 不是对象"
    if not isinstance(data.get("meta"), dict) or not isinstance(data.get("testcases"), list):
        return "unreadable", "缺 meta 或 testcases"
    obligations = data.get("coverage_obligations")
    if isinstance(obligations, list) and obligations:
        return "obligations", f"{len(obligations)} 条义务"
    legacy = data["meta"].get("legacy_source")
    if isinstance(legacy, dict) and all(str(legacy.get(k) or "").strip()
                                       for k in ("schema_version", "migrated_at", "owner")):
        return "legacy", f"legacy_source={legacy.get('schema_version')} @ {legacy.get('migrated_at')}"
    return "blocked", "无 coverage_obligations 且无 meta.legacy_source"


def candidates(paths):
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            yield from sorted(path.rglob("*.json"))
        elif path.is_file():
            yield path
        else:
            raise OSError(f"路径不存在：{path}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="存量 cases 义务迁移扫描（只读）")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--json", action="store_true", help="输出结构化结果")
    args = parser.parse_args(argv)
    rows, counts = [], {"obligations": 0, "legacy": 0, "blocked": 0, "unreadable": 0}
    try:
        for path in candidates(args.paths):
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            state, detail = classify(data)
            counts[state] += 1
            tcs = data.get("testcases") if isinstance(data, dict) else None
            rows.append({"path": str(path), "state": state, "detail": detail,
                         "testcases": len(tcs) if isinstance(tcs, list) else 0})
    except OSError as exc:
        print(f"扫描失败：{exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"counts": counts, "rows": rows}, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            if row["state"] == "unreadable":
                continue
            print(f"[{row['state']:>11}] {row['path']}  用例 {row['testcases']} 条  {row['detail']}")
        print(f"\n汇总：obligations={counts['obligations']} legacy={counts['legacy']} "
              f"blocked={counts['blocked']} 跳过(非 cases)={counts['unreadable']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
