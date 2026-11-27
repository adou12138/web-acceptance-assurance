#!/usr/bin/env python3
"""gate_absent_check.py — R9（v1.55）：门禁缺席检测。

背景：两轮真实任务（额度 web-quota-1037678、访谈 20261116/20261119）都在**没有台账、
没有 attack_coverage、没有 exploration gate** 的情况下出了交付物——规则写着「未走台账
门禁须明写并压上限」（agents:48/:52/:218），但没有任何东西能检出"门禁压根没跑"。
本脚本做缺席检测：扫工作区，找出执行类产物与门禁类产物，把缺的门禁点名。

只做存在性启发式判定（文件名模式），不校验内容合法性——内容校验是各门禁自己的职责。
退出码：0=门禁齐备或 --dir 下没有执行类产物；1=存在执行类产物但门禁缺席（--enforce 时）。

用法：
    python scripts/gate_absent_check.py --dir <工作区> [--json] [--enforce]
"""
import argparse
import json
import sys
from pathlib import Path

EXECUTION_PATTERNS = ("execution-results*.csv", "*execution*results*.csv",
                      "events.jsonl", "collection.jsonl", "*defect*.csv")
LEDGER_PATTERNS = ("*ledger*.json",)
ATTACK_PATTERNS = ("*attack*coverage*.json", "*attack-coverage*.json")
EXPLORATION_PATTERNS = ("*exploration*.json", "*gate-full*.json", "*gate*.json")
# gate.json 这类泛名既可能是探索门禁也可能是别的，归入 exploration 候选并在
# message 里说明按文件名启发式判定，须人工确认归属。


def _match(directory, patterns):
    hits = []
    for pattern in patterns:
        hits.extend(directory.glob(pattern))
    return sorted({p for p in hits if p.is_file()})


def scan(directory):
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError(f"目录不存在：{directory}")
    execution = sorted(set(_match(directory, EXECUTION_PATTERNS)))
    ledger = _match(directory, LEDGER_PATTERNS)
    attack = _match(directory, ATTACK_PATTERNS)
    exploration = _match(directory, EXPLORATION_PATTERNS)

    absent = []
    has_execution = bool(execution)
    if has_execution and not ledger:
        absent.append({"gate": "execution_ledger",
                       "reason": "存在执行类产物但没有台账（execution_ledger）产物："
                                 "按交付契约须明写「未走台账门禁」并压上限"})
    if has_execution and not attack:
        absent.append({"gate": "attack_coverage",
                       "reason": "存在执行类产物但没有 attack_coverage 结论："
                                 "攻击密度与判据独立性未经机械检查"})
    if has_execution and not exploration:
        absent.append({"gate": "exploration_gate",
                       "reason": "存在执行类产物但没有探索/视角门禁产物："
                                 "missing_views 未检查，视角覆盖缺失无法发现"})
    return {
        "workspace": str(directory),
        "execution_artifacts": [str(p) for p in execution],
        "gate_artifacts": {"execution_ledger": [str(p) for p in ledger],
                           "attack_coverage": [str(p) for p in attack],
                           "exploration_candidates": [str(p) for p in exploration]},
        "absent": absent,
        "verdict_ceiling_suggestion": "待核实" if absent else None,
        "note": "按文件名启发式判定，只检缺席不检内容；gate 类泛名文件的归属须人工确认",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="门禁缺席检测（R9，v1.55）")
    parser.add_argument("--dir", required=True, help="工作区目录")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    parser.add_argument("--enforce", action="store_true",
                        help="缺席时退出码 1（供自检清单/流水线调用）")
    args = parser.parse_args(argv)
    try:
        result = scan(args.dir)
    except ValueError as exc:
        print(f"输入错误：{exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result["absent"]:
            for item in result["absent"]:
                print(f"[缺席] {item['gate']}：{item['reason']}")
            print(f"结论上限建议：{result['verdict_ceiling_suggestion']}")
        else:
            print("门禁齐备（或目录下没有执行类产物，无需检测）" if not result["execution_artifacts"]
                  else "门禁齐备")
    if args.enforce and result["absent"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
