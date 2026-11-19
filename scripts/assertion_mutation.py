#!/usr/bin/env python3
"""assertion_mutation.py — 断言杀伤率：证明"断言真的会报警"。

为什么需要它：假绿是「测了很多却只发现几个」最体面的成因。断言写得根本不会响，跑
多少次都是绿的，而报告上一切正常。防御只有一个办法——往被测逻辑里注入一个已知故障，
看断言有没有抓住它。抓不住就是断言无效，不是产品没问题。

三条纪律：
- **抽样由哈希决定，不由执行者指定**。否则会专挑容易的断言去验证，等于自证。
- 只对**支撑结论的断言**抽样（required=true），不对全部断言铺一遍——成本换不来等值收益。
- 覆盖不到的断言明确标 `unverified`，其支撑的结论压「待核实」；这条只压那个结论，
  不压整批（否则又变成"诚实登记反被罚"）。

用法：
    python scripts/assertion_mutation.py --assertions a.json --records r.json --sample-rate 0.5
    python scripts/assertion_mutation.py --assertions a.json --records r.json --enforce

输入：
    a.json = [{"id": "AS-01", "case_id": "TC-1", "check": "保存后标题读回",
               "required": true}, ...]
    r.json = {"AS-01": {"mutation": "把标题长度改到 5001 字", "observed": "仍显示保存成功",
                        "caught": true, "evidence": "/abs/path.png"}, ...}

`caught=true` 表示断言在注入故障后**失败**了——也就是它真的会响。
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

DEFAULT_SAMPLE_RATE = 0.5
DEFAULT_MIN_KILL_RATE = 0.8


def _sample_key(ident, seed=""):
    return hashlib.sha256(f"{seed}|{ident}".encode("utf-8")).hexdigest()


def select_sample(assertions, rate=DEFAULT_SAMPLE_RATE, seed=""):
    """按哈希排序取前 rate 比例：同一批输入永远得到同一批抽样，但不由人挑。"""
    if not 0 < rate <= 1:
        raise ValueError("sample_rate 必须在 (0, 1] 内")
    required = [item for item in assertions if item.get("required", True)]
    ordered = sorted(required, key=lambda item: _sample_key(item["id"], seed))
    count = max(1, round(len(ordered) * rate)) if ordered else 0
    return ordered[:count]


def analyse(assertions, records, rate=DEFAULT_SAMPLE_RATE, min_kill_rate=DEFAULT_MIN_KILL_RATE,
            seed=""):
    if not isinstance(assertions, list) or not assertions:
        raise ValueError("assertions 必须为非空数组")
    seen = set()
    cleaned = []
    for index, item in enumerate(assertions):
        if not isinstance(item, dict):
            raise ValueError(f"assertions[{index}] 必须为对象")
        ident = str(item.get("id") or "").strip()
        if not ident:
            raise ValueError(f"assertions[{index}] 缺 id")
        if ident in seen:
            raise ValueError(f"assertions[{index}].id 重复：{ident}")
        seen.add(ident)
        cleaned.append({"id": ident, "case_id": str(item.get("case_id") or ""),
                        "check": str(item.get("check") or ""),
                        "required": bool(item.get("required", True))})
    if records is None:
        records = {}
    if not isinstance(records, dict):
        raise ValueError("records 必须为 {断言ID: 验证记录} 对象")

    sample = select_sample(cleaned, rate, seed)
    results, killed, survived, missing = [], 0, [], []
    for item in sample:
        record = records.get(item["id"])
        if not isinstance(record, dict):
            missing.append(item["id"])
            results.append({**item, "status": "unverified"})
            continue
        caught = record.get("caught")
        if not isinstance(caught, bool):
            raise ValueError(f"records.{item['id']}.caught 必须为布尔值")
        if not str(record.get("mutation") or "").strip():
            raise ValueError(f"records.{item['id']}.mutation 不能为空（注入要可复现）")
        if caught:
            killed += 1
            results.append({**item, "status": "killed",
                            "mutation": record["mutation"]})
        else:
            survived.append(item["id"])
            results.append({**item, "status": "survived",
                            "mutation": record.get("mutation", "")})

    unsampled = [item["id"] for item in cleaned
                 if item.get("required", True) and item["id"] not in {s["id"] for s in sample}]
    verified = killed + len(survived)
    kill_rate = round(killed / verified, 4) if verified else None

    gaps = []
    if not sample:
        gaps.append({"code": "no_required_assertion",
                     "message": "没有 required 断言可供验证：结论没有任何会报警的断言支撑"})
    if missing:
        gaps.append({"code": "assertion_unverified",
                     "message": f"{len(missing)} 条抽中但没做变异验证：{missing[:6]}"})
    if survived:
        gaps.append({"code": "assertion_survived_mutation",
                     "message": f"{len(survived)} 条断言在注入已知故障后仍然通过（假绿）："
                                f"{survived[:6]}"})
    if kill_rate is not None and kill_rate < min_kill_rate:
        gaps.append({"code": "kill_rate_low",
                     "message": f"杀伤率 {kill_rate} 低于下限 {min_kill_rate}："
                                "断言不足以支撑「通过」"})

    return {
        "total_assertions": len(cleaned),
        "required_assertions": sum(1 for item in cleaned if item.get("required", True)),
        "sample_rate": rate,
        "sampled": len(sample),
        "sampled_ids": [item["id"] for item in sample],
        "verified": verified,
        "killed": killed,
        "survived": survived,
        "missing": missing,
        "kill_rate": kill_rate,
        "min_kill_rate": min_kill_rate,
        "unsampled_required": unsampled,
        "results": results,
        "gaps": gaps,
        "verdict_ceiling": "待核实" if gaps else "通过",
        "scope": ("只证明抽样断言会报警，不证明断言覆盖了该覆盖的东西；"
                  "未抽中的断言标未验证，其支撑的结论不得写「通过」"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="断言杀伤率（变异/故障注入验证）")
    parser.add_argument("--assertions", required=True)
    parser.add_argument("--records", help="验证记录 {断言ID: {mutation, caught}}")
    parser.add_argument("--sample-rate", type=float, default=DEFAULT_SAMPLE_RATE)
    parser.add_argument("--min-kill-rate", type=float, default=DEFAULT_MIN_KILL_RATE)
    parser.add_argument("--seed", default="", help="抽样种子（跨轮次复现同一批抽样）")
    parser.add_argument("--emit-json")
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args(argv)
    assertions = json.loads(Path(args.assertions).read_text(encoding="utf-8-sig"))
    records = (json.loads(Path(args.records).read_text(encoding="utf-8-sig"))
               if args.records else {})
    result = analyse(assertions, records, args.sample_rate, args.min_kill_rate, args.seed)
    if args.emit_json:
        Path(args.emit_json).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.enforce and result["gaps"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
