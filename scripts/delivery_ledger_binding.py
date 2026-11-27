#!/usr/bin/env python3
"""交付-台账绑定检查（v1.56 S10，不产生新检出）。

背景：M9 审核指出"文件存在 + design_hash 一致"判台账有效可以被空台账绕过——
建一个台账然后照旧手写交付物，存在性检查毫无判别力。真正的绑定判据是集合
包含关系：交付 CSV 的每条执行记录必须能在台账 events.jsonl 里找到对应 FINISH。

只读检查，不改任何文件；--enforce 时缺失则退出码 1（供收尾脚本门禁）。

用法：
    python scripts/delivery_ledger_binding.py --delivery <执行结果.csv> \
        --ledger <workspace 或 events.jsonl> [--enforce] [--out <report.json>]

交付 CSV 的用例 ID 列按序识别：用例ID / case_id / id。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ID_COLUMNS = ("用例ID", "case_id", "id")


def _delivered_ids(csv_path):
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return [], "empty"
    columns = list(rows[0].keys())
    id_col = next((c for c in ID_COLUMNS if c in columns), None)
    if id_col is None:
        return [], f"no_id_column（现有列：{columns[:8]}）"
    ids = [str(r.get(id_col) or "").strip() for r in rows]
    return [i for i in ids if i], None


def _ledger_finish_ids(ledger_path):
    path = Path(ledger_path)
    if path.is_dir():
        path = path / "events.jsonl"
    if not path.is_file():
        return None, f"ledger not found: {path}"
    ids = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("kind") == "FINISH" and event.get("case_id"):
            ids.add(str(event["case_id"]).strip())
    return ids, None


def check(delivery_csv, ledger):
    """返回 {missing, delivered_count, ledger_finish_count, verdict_ceiling_suggestion}。"""
    ids, id_err = _delivered_ids(delivery_csv)
    if id_err:
        return {"error": f"delivery 解析失败：{id_err}", "missing": None,
                "verdict_ceiling_suggestion": "待核实"}
    finish_ids, led_err = _ledger_finish_ids(ledger)
    if led_err:
        return {"error": led_err, "missing": None,
                "verdict_ceiling_suggestion": "待核实"}
    missing = sorted({i for i in ids if i not in finish_ids})
    return {
        "missing": missing,
        "delivered_count": len(ids),
        "ledger_finish_count": len(finish_ids),
        "verdict_ceiling_suggestion": "待核实" if missing else None,
        "note": ("交付记录未绑定台账：以下用例在台账 events 中没有对应 FINISH——"
                 "属「未走台账门禁」形态，报告须明写并压结论上限" if missing else
                 "交付记录与台账 FINISH 一一对应"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="交付-台账绑定检查（S10）")
    parser.add_argument("--delivery", required=True, help="交付的执行结果 CSV")
    parser.add_argument("--ledger", required=True, help="台账工作区目录或 events.jsonl")
    parser.add_argument("--enforce", action="store_true",
                        help="发现缺失时退出码 1")
    parser.add_argument("--out", help="结果 JSON 落盘路径")
    args = parser.parse_args(argv)
    result = check(args.delivery, args.ledger)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                                  encoding="utf-8")
    if args.enforce and (result.get("missing") or result.get("error")):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
