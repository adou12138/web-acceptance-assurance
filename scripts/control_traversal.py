#!/usr/bin/env python3
"""control_traversal.py — 控件遍历覆盖：逐个可交互元素追踪"有没有人碰过它"。

为什么需要它：用例写的是"业务路径"，而用户点的是"控件"。死按钮、点了没反应、
禁用态其实可点、重复提交无防护——这类问题不在任何一条用例的预期里，只能靠
"把控件清点一遍并逐个确认有过观察"来发现。

`EXP-TRAV-` 那条记录只能证明"点了一批控件"，证明不了"每个控件都被点过"。
本脚本把清点结果（inventory）与被观察结果（observations）对账，把没碰过的控件摊出来。

用法：
    python scripts/control_traversal.py --inventory inv.json --observed obs.json
    python scripts/control_traversal.py --inventory inv.json --observed obs.json --enforce

输入：
    inv.json  = {"page": "https://...", "controls": [{"control_id": "CTL-01",
                 "element": "button#save", "kind": "button"}, ...]}
    obs.json  = [{"control_id": "CTL-01", "action": "click", "effect": "保存成功提示",
                  "evidence": "/abs/path.png"}, ...]

`effect` 为空或 "none" 记为**无反馈**候选，需要人工复核（可能是死控件，也可能是
异步完成但没有可见反馈）。
"""

import argparse
import json
import sys
from pathlib import Path

NO_EFFECT_TOKENS = {"none", "noop", "nothing", "无", "无反应", "无变化"}


def _text(value, name, required=True):
    if not isinstance(value, str):
        raise ValueError(f"{name}: 必须为字符串")
    text = value.strip()
    if required and not text:
        raise ValueError(f"{name}: 不能为空")
    return text


def analyse(inventory, observations):
    if not isinstance(inventory, dict):
        raise ValueError("inventory 必须为对象")
    controls = inventory.get("controls")
    if not isinstance(controls, list):
        raise ValueError("inventory.controls 必须为数组")
    if not isinstance(observations, list):
        raise ValueError("observed 必须为数组")

    page = _text(inventory.get("page") or "", "inventory.page", False)
    inventory_map = {}
    for index, item in enumerate(controls):
        if not isinstance(item, dict):
            raise ValueError(f"controls[{index}] 必须为对象")
        ident = _text(item.get("control_id"), f"controls[{index}].control_id")
        if ident in inventory_map:
            raise ValueError(f"controls[{index}].control_id: 重复 {ident}")
        inventory_map[ident] = {"control_id": ident,
                                "element": _text(item.get("element") or "", "element", False),
                                "kind": _text(item.get("kind") or "", "kind", False)}

    observed = {}
    unknown = []
    for index, item in enumerate(observations):
        if not isinstance(item, dict):
            raise ValueError(f"observed[{index}] 必须为对象")
        ident = _text(item.get("control_id"), f"observed[{index}].control_id")
        if ident not in inventory_map:
            unknown.append(ident)
            continue
        entry = observed.setdefault(ident, {"actions": [], "evidence": [],
                                           "no_effect": False, "notes": []})
        action = _text(item.get("action") or "", "action", False)
        if action:
            entry["actions"].append(action)
        effect = str(item.get("effect") or "").strip()
        if effect.lower() in NO_EFFECT_TOKENS or not effect:
            entry["no_effect"] = True
        evidence = item.get("evidence")
        if isinstance(evidence, str) and evidence.strip():
            entry["evidence"].append(evidence.strip())
        elif isinstance(evidence, list):
            entry["evidence"].extend(e for e in evidence if isinstance(e, str) and e.strip())

    untouched = [c for cid, c in inventory_map.items() if cid not in observed]
    no_effect = [inventory_map[cid] | {"actions": entry["actions"]}
                 for cid, entry in observed.items() if entry["no_effect"]]
    no_evidence = [inventory_map[cid] for cid, entry in observed.items()
                   if not entry["evidence"]]

    total = len(inventory_map)
    gaps = []
    if not total:
        gaps.append({"code": "empty_inventory", "message": "控件清单为空：无法证明遍历过"})
    if untouched:
        gaps.append({"code": "controls_untouched",
                     "message": f"{len(untouched)}/{total} 个控件没有任何观察记录："
                                + "、".join(c["control_id"] for c in untouched[:8])})
    if unknown:
        gaps.append({"code": "control_not_inventoried",
                     "message": f"观察记录里的控件不在清单中：{sorted(set(unknown))[:5]}"})
    if no_evidence:
        gaps.append({"code": "control_without_evidence",
                     "message": f"{len(no_evidence)} 个控件的观察没有证据路径"})

    return {
        "page": page,
        "total_controls": total,
        "observed": len(observed),
        "untouched": len(untouched),
        "coverage_rate": round(len(observed) / total, 4) if total else None,
        "untouched_controls": untouched,
        "no_effect_candidates": no_effect,
        "controls_without_evidence": no_evidence,
        "gaps": gaps,
        "verdict_ceiling": "待核实" if gaps else "通过",
        "scope": ("只判控件是否被逐个观察过；no_effect_candidates 是线索不是缺陷，"
                  "须人工复核（可能是死控件，也可能是异步完成但无可见反馈）"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="控件遍历覆盖对账")
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--observed", required=True)
    parser.add_argument("--emit-json")
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args(argv)
    inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8-sig"))
    observations = json.loads(Path(args.observed).read_text(encoding="utf-8-sig"))
    result = analyse(inventory, observations)
    if args.emit_json:
        Path(args.emit_json).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.enforce and result["gaps"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
