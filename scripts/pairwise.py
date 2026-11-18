"""Pairwise（两两组合）生成与独立覆盖验真。

只在参数、值域与约束都有证据且组合规模值得压缩时使用。
输出必须能被独立验证器重新核对，不允许生成器自己宣布覆盖完整。

用法（命令行）：
    python tools/pairwise.py params.json
    python tools/pairwise.py params.json --output-json result.json

输入结构：
    {
      "parameters": [{"name": "角色", "values": ["管理员", "普通用户"]}],
      "constraints": [[{"name": "角色", "value": "访客"}, {"name": "权限", "value": "导出"}]]
    }

约束表示“这些取值不能同时出现”，数组中任一元素命中即视为非法组合。
"""

import argparse
import hashlib
import itertools
import json
import sys

MAX_EXHAUSTIVE_COMBINATIONS = 200000
DEFAULT_MAX_ROWS = 200


class PairwiseError(ValueError):
    """输入不合法或无法完成覆盖时抛出。"""


def validate_parameters(parameters):
    if not isinstance(parameters, list) or not parameters:
        raise PairwiseError("parameters 必须是非空数组")
    names = []
    normalized = []
    for item in parameters:
        if not isinstance(item, dict):
            raise PairwiseError("参数项必须是对象")
        name = str(item.get("name") or "").strip()
        if not name:
            raise PairwiseError("参数缺少 name")
        if name in names:
            raise PairwiseError("参数名重复: %s" % name)
        values = item.get("values")
        if not isinstance(values, list) or not values:
            raise PairwiseError("参数 %s 的 values 必须是非空数组" % name)
        cleaned = []
        for value in values:
            if isinstance(value, (dict, list)):
                raise PairwiseError("参数 %s 的取值必须是标量" % name)
            if value in cleaned:
                raise PairwiseError("参数 %s 的取值重复: %r" % (name, value))
            cleaned.append(value)
        names.append(name)
        normalized.append({"name": name, "values": cleaned})
    return normalized


def normalize_constraints(constraints, parameters):
    if not constraints:
        return []
    if not isinstance(constraints, list):
        raise PairwiseError("constraints 必须是数组")
    names = {p["name"] for p in parameters}
    result = []
    for entry in constraints:
        # 兼容 generate() 输出的归一形态 {"角色": "访客"}：产物可直接喂回 verify() 重算，
        # 否则"生成器自证"与"独立验真"之间会因形态不通而无法落地。
        if isinstance(entry, dict):
            entry = [{"name": key, "value": value} for key, value in entry.items()]
        if not isinstance(entry, list) or not entry:
            raise PairwiseError("每条约束必须是非空数组（或 {参数名: 取值} 对象）")
        conditions = {}
        for cond in entry:
            if not isinstance(cond, dict):
                raise PairwiseError("约束项必须是对象")
            name = str(cond.get("name") or "").strip()
            if name not in names:
                raise PairwiseError("约束引用了未定义参数: %s" % name)
            if "value" not in cond:
                raise PairwiseError("约束项缺少 value")
            conditions[name] = cond["value"]
        result.append(conditions)
    return result


def is_allowed(row, constraints):
    for cond in constraints:
        if all(row.get(name) == value for name, value in cond.items()):
            return False
    return True


def _required_pairs(parameters, constraints):
    """枚举所有“存在合法完整组合”的参数值对。"""
    rows = _all_allowed_rows(parameters, constraints, limit=MAX_EXHAUSTIVE_COMBINATIONS)
    if rows is None:
        return None
    pairs = set()
    names = [p["name"] for p in parameters]
    for row in rows:
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                pairs.add((names[i], row[names[i]], names[j], row[names[j]]))
    return pairs


def _all_allowed_rows(parameters, constraints, limit):
    total = 1
    for p in parameters:
        total *= len(p["values"])
        if total > limit:
            return None
    names = [p["name"] for p in parameters]
    rows = []
    for combo in itertools.product(*[p["values"] for p in parameters]):
        row = dict(zip(names, combo))
        if is_allowed(row, constraints):
            rows.append(row)
    if not rows:
        raise PairwiseError("所有组合都被约束排除，无法生成")
    return rows


def generate(parameters, constraints=None, max_rows=DEFAULT_MAX_ROWS):
    """生成覆盖参数对的组合。约束下无法穷举时返回 incomplete，不假称完整。"""
    parameters = validate_parameters(parameters)
    constraints = normalize_constraints(constraints, parameters)
    names = [p["name"] for p in parameters]
    full_product = 1
    for p in parameters:
        full_product *= len(p["values"])

    result = {
        "parameters": parameters,
        "constraints": constraints,
        "full_cartesian_product": full_product,
        "combinations": [],
        "required_pairs": 0,
        "covered_pairs": 0,
        "missing_pairs": [],
        "complete": False,
        "strategy": None,
        "note": "",
    }

    if len(parameters) == 1:
        rows = _all_allowed_rows(parameters, constraints, limit=MAX_EXHAUSTIVE_COMBINATIONS)
        result["combinations"] = rows or []
        result["strategy"] = "single-parameter"
        result["complete"] = rows is not None
        result["note"] = "单参数无需组合压缩" if rows is not None else "组合规模超出穷举上限"
    else:
        legal_rows = _all_allowed_rows(parameters, constraints, limit=MAX_EXHAUSTIVE_COMBINATIONS)
        if legal_rows is None:
            result["strategy"] = "skipped-too-large"
            result["note"] = "合法组合规模超出穷举上限 %d，未生成；请缩小值域" % MAX_EXHAUSTIVE_COMBINATIONS
            return result
        required = _required_pairs(parameters, constraints)
        covered = set()
        chosen = []
        rows_sorted = sorted(legal_rows, key=lambda r: tuple(str(r[n]) for n in names))
        while covered != required:
            best_row = None
            best_gain = -1
            for row in rows_sorted:
                gain = 0
                for i in range(len(names)):
                    for j in range(i + 1, len(names)):
                        pair = (names[i], row[names[i]], names[j], row[names[j]])
                        if pair in required and pair not in covered:
                            gain += 1
                if gain > best_gain:
                    best_gain = gain
                    best_row = row
                    if gain == len(required) - len(covered):
                        break
            if best_row is None or best_gain <= 0:
                break
            chosen.append(best_row)
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    covered.add((names[i], best_row[names[i]], names[j], best_row[names[j]]))
            if len(chosen) >= max_rows:
                break
        result["combinations"] = chosen
        result["required_pairs"] = len(required)
        result["covered_pairs"] = len(covered & required)
        missing = sorted(required - covered, key=lambda x: (str(x[0]), str(x[1]), str(x[2]), str(x[3])))
        result["missing_pairs"] = [
            {"left": {"name": p[0], "value": p[1]}, "right": {"name": p[2], "value": p[3]}}
            for p in missing
        ]
        result["complete"] = not missing
        result["strategy"] = "exhaustive-greedy"
        if missing:
            result["note"] = "受 max_rows=%d 限制，仍有 %d 对未覆盖" % (max_rows, len(missing))

    payload = json.dumps(
        {"parameters": parameters, "constraints": constraints, "combination_count": len(result["combinations"])},
        ensure_ascii=False, sort_keys=True,
    )
    result["input_hash"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return result


def verify(parameters, constraints, combinations):
    """独立验真：重新枚举要求覆盖的参数对，与给定组合的实际覆盖对比。"""
    parameters = validate_parameters(parameters)
    constraints = normalize_constraints(constraints, parameters)
    required = _required_pairs(parameters, constraints)
    if required is None:
        return {"verified": False, "reason": "组合规模超出穷举上限，无法独立验真", "missing_pairs": []}
    names = [p["name"] for p in parameters]
    covered = set()
    for row in combinations or []:
        if not isinstance(row, dict):
            continue
        if set(row.keys()) - set(names):
            return {"verified": False, "reason": "组合含未定义参数", "missing_pairs": []}
        for name in names:
            if name not in row:
                return {"verified": False, "reason": "组合缺少参数取值: %s" % name, "missing_pairs": []}
        if not is_allowed(row, constraints):
            return {"verified": False, "reason": "组合违反约束: %r" % row, "missing_pairs": []}
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                covered.add((names[i], row[names[i]], names[j], row[names[j]]))
    missing = sorted(required - covered, key=lambda x: (str(x[0]), str(x[1]), str(x[2]), str(x[3])))
    return {
        "verified": not missing,
        "required_pairs": len(required),
        "covered_pairs": len(covered & required),
        "missing_pairs": [
            {"left": {"name": p[0], "value": p[1]}, "right": {"name": p[2], "value": p[3]}}
            for p in missing
        ],
        "reason": "" if not missing else "仍有参数对未覆盖",
    }


def generate_and_verify(parameters, constraints=None, max_rows=DEFAULT_MAX_ROWS):
    result = generate(parameters, constraints, max_rows=max_rows)
    check = verify(parameters, constraints or [], result["combinations"])
    result["verification"] = check
    if not check.get("verified"):
        result["complete"] = False
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Pairwise 组合生成与独立覆盖验真")
    parser.add_argument("input", help="输入 JSON 文件路径")
    parser.add_argument("-o", "--output-json", default="", help="结果 JSON 输出路径")
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS, help="最大生成行数")
    args = parser.parse_args(argv)

    try:
        with open(args.input, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        result = generate_and_verify(
            data.get("parameters", []),
            data.get("constraints", []),
            max_rows=args.max_rows,
        )
    except (PairwiseError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, ensure_ascii=False))
        return 2

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "status": "ok" if result["complete"] else "incomplete",
        "combinations": len(result["combinations"]),
        "required_pairs": result["required_pairs"],
        "covered_pairs": result["covered_pairs"],
        "missing_pairs": len(result["missing_pairs"]),
        "verified": result["verification"].get("verified"),
        "note": result["note"],
    }, ensure_ascii=False))
    return 0 if result["complete"] else 3


if __name__ == "__main__":
    sys.exit(main())
