#!/usr/bin/env python3
"""copy_consistency.py — 文案三通道一致性与机械可查缺陷。

为什么需要它：文案问题最容易"看见但记不下来"——用户会投诉"提示看不懂""两个按钮叫法
不一样""这里冒出个 {{name}}"，但这类问题不进任何一条用例的预期结果里。能用规则判的
部分应该机械判，别靠走查时的记忆。

四类检查（前两类是机械缺陷，后两类是待人工裁定的线索）：
- 未替换的占位符：{{name}}、${x}、%s、{0}、TODO、undefined、null
- 排版硬缺陷：连续重复标点（，，）、首尾空格、成对括号不闭合、重复字（的的）
- 同角色近义异写：同一 role 下相似度 ≥ 0.8 但不相同的文本（删除/移除/清除）
- 术语表偏离：出现 variant 而未用 preferred

用法：
    python scripts/copy_consistency.py --input strings.json
    python scripts/copy_consistency.py --input strings.json --glossary g.json --enforce

输入：
    strings.json = [{"id": "S1", "page": "https://...", "element": "button#del",
                     "role": "button", "text": "删除"}, ...]
    g.json       = {"term_groups": [["删除", "移除", "清除"]],
                    "preferred": {"删除": "统一用「删除」"}}
"""

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

PLACEHOLDER_RE = re.compile(r"\{\{[^}]*\}\}|\$\{[^}]*\}|\{[0-9]+\}|%[sd]|__\w+__|\bTODO\b")
RAW_VALUE_RE = re.compile(r"\b(undefined|null|NaN|\[object Object\])\b")
DOUBLE_PUNCT_RE = re.compile(r"([，。、；：！？])\1+")
DUPLICATE_CHAR_RE = re.compile(r"([\u4e00-\u9fa5])\1")
BRACKETS = (("（", "）"), ("(", ")"), ("【", "】"), ("《", "》"))
SUCCESS_WORDS = ("成功", "已保存", "已删除", "已完成", "保存成功")
FAILURE_ROLES = {"error", "toast_error", "提示-失败"}
SIMILARITY_THRESHOLD = 0.8

# R5（v1.55）：跨视图互比。单页 copy_audit 看不见"七个页签之间"的不一致——
# 额度任务实证：短信单位"条"、OCR"次"、免广告"次"各页都对（与设计稿一致），
# 但横向互比才发现单位语义不统一。只产观察候选（advisory），不代判业务。
NUM_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([条次个点份项名岁元人题份份页张日天时秒%)％])")
DECIMAL_RE = re.compile(r"\d+\.\d+")


def cross_view_compare(views):
    """views: {"视图名": [文案, ...]}。横向比对各视图的量词单位与小数精度使用，
    输出观察候选（advisory）——是否为缺陷须人工裁定。"""
    if not isinstance(views, dict) or not views:
        raise ValueError('views 必须为非空对象 {"视图名": [文案, ...]}')
    normalized = {}
    for name, texts in views.items():
        if not isinstance(texts, list):
            raise ValueError(f"views[{name}]: 必须为字符串数组")
        normalized[str(name)] = [str(t) for t in texts if str(t).strip()]
    findings = []
    unit_views = {}
    decimal_views = {}
    for name, texts in normalized.items():
        units = set()
        has_decimal = False
        for text in texts:
            for m in NUM_UNIT_RE.finditer(text):
                units.add(m.group(2))
            if DECIMAL_RE.search(text):
                has_decimal = True
        unit_views[name] = units
        decimal_views[name] = has_decimal
    all_units = set().union(*unit_views.values()) if unit_views else set()
    names = sorted(normalized)
    for unit in sorted(all_units):
        with_unit = [n for n in names if unit in unit_views[n]]
        without = [n for n in names if unit not in unit_views[n]]
        if with_unit and without:
            findings.append({
                "code": "cross_view_unit_mismatch", "id": f"unit:{unit}",
                "text": f"量词「{unit}」出现于 {with_unit}，未出现于 {without}",
                "token": unit,
                "message": f"量词「{unit}」在 {len(with_unit)}/{len(names)} 个视图出现："
                           "各子视图的同位信息单位可能不统一（观察候选，须人工裁定）",
                "severity": "advisory"})
    dec_with = [n for n in names if decimal_views[n]]
    dec_without = [n for n in names if not decimal_views[n]]
    if dec_with and dec_without:
        findings.append({
            "code": "cross_view_decimal_mismatch", "id": "decimal:precision",
            "text": f"小数精度出现于 {dec_with}，未出现于 {dec_without}",
            "token": "decimal",
            "message": "部分视图数值带小数、部分只出整数：同位数值的精度口径可能不统一"
                       "（观察候选，须人工裁定）",
            "severity": "advisory"})
    return {"views": names, "findings": findings,
            "scope": "跨视图横向互比只产观察候选；是否为缺陷须人工裁定（与设计稿一致性另行判定）"}


def analyse_cross_view(payload):
    """兼容 analyse 的输出形态：cross-view 模式共用 findings/gaps 结构。"""
    result = cross_view_compare(payload.get("views") if isinstance(payload, dict) else payload)
    findings = result["findings"]
    return {
        "mode": "cross_view",
        "views": result["views"],
        "total": sum(len(v) for v in (payload.get("views") or {}).values()) if isinstance(payload, dict) else 0,
        "defects": 0,
        "advisories": len(findings),
        "findings": findings,
        "gaps": [],
        "verdict_ceiling": "通过",
        "scope": result["scope"],
    }


def _normalize(text):
    return re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE).lower()


def _mechanical_defects(item):
    text = item["text"]
    found = []
    for match in PLACEHOLDER_RE.finditer(text):
        found.append(("placeholder_not_rendered", match.group(0),
                      f"文案含未替换占位符 {match.group(0)}"))
    for match in RAW_VALUE_RE.finditer(text):
        found.append(("raw_runtime_value", match.group(0),
                      f"文案含运行时原始值 {match.group(0)}"))
    for match in DOUBLE_PUNCT_RE.finditer(text):
        found.append(("duplicate_punctuation", match.group(0),
                      f"连续重复标点 {match.group(0)}"))
    if text != text.strip():
        found.append(("leading_trailing_space", repr(text),
                      "文案首尾有空格"))
    for open_char, close_char in BRACKETS:
        if text.count(open_char) != text.count(close_char):
            found.append(("unbalanced_bracket", open_char,
                          f"括号不配对：{open_char}/{close_char}"))
    for match in DUPLICATE_CHAR_RE.finditer(text):
        if match.group(0) not in {"谢谢", "刚刚", "渐渐", "看看", "试试"}:
            found.append(("duplicate_char", match.group(0),
                          f"重复字 {match.group(0)}，疑似赘字"))
    if item.get("role") in FAILURE_ROLES and any(w in text for w in SUCCESS_WORDS):
        found.append(("failure_message_says_success", text[:16],
                      "失败态提示里出现成功字样，方向可能反了"))
    return found


def analyse(strings, glossary=None):
    if not isinstance(strings, list):
        raise ValueError("输入必须为文案数组")
    normalized = []
    for index, item in enumerate(strings):
        if not isinstance(item, dict):
            raise ValueError(f"strings[{index}] 必须为对象")
        ident = str(item.get("id") or "").strip()
        text = item.get("text")
        if not ident:
            raise ValueError(f"strings[{index}] 缺 id")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"strings[{index}].text 必须为非空字符串")
        normalized.append({"id": ident, "page": str(item.get("page") or ""),
                           "element": str(item.get("element") or ""),
                           "role": str(item.get("role") or "text"),
                           "text": text.strip()})

    findings = []
    for item in normalized:
        for code, token, message in _mechanical_defects(item):
            findings.append({"code": code, "id": item["id"], "text": item["text"],
                             "token": token, "message": message,
                             "severity": "defect"})

    # 同角色近义异写：相似度够高但不相同 = 同一个东西有两种叫法
    by_role = {}
    for item in normalized:
        by_role.setdefault(item["role"], []).append(item)
    for role, items in by_role.items():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                left, right = items[i], items[j]
                if left["text"] == right["text"]:
                    continue
                a, b = _normalize(left["text"]), _normalize(right["text"])
                if not a or not b:
                    continue
                ratio = difflib.SequenceMatcher(None, a, b).ratio()
                if ratio >= SIMILARITY_THRESHOLD:
                    findings.append({
                        "code": "inconsistent_wording", "id": f"{left['id']}|{right['id']}",
                        "text": f"{left['text']} / {right['text']}",
                        "token": role, "ratio": round(ratio, 3),
                        "message": f"同角色（{role}）两处表述不同但高度相似，"
                                   "需确认是否有意区分",
                        "severity": "advisory"})

    glossary_findings = []
    if glossary:
        if not isinstance(glossary, dict):
            raise ValueError("glossary 必须为对象")
        preferred = glossary.get("preferred") or {}
        if not isinstance(preferred, dict):
            raise ValueError("glossary.preferred 必须为对象")
        groups = glossary.get("term_groups") or []
        if not isinstance(groups, list):
            raise ValueError("glossary.term_groups 必须为数组")
        texts = {item["text"] for item in normalized}
        for group in groups:
            if not isinstance(group, list) or len(group) < 2:
                raise ValueError("term_groups 的每组至少要有 2 个写法")
            used = [term for term in group if any(term in text for text in texts)]
            if len(used) > 1:
                glossary_findings.append({
                    "code": "term_variant_mixed", "id": "|".join(used),
                    "text": " / ".join(used), "token": "",
                    "message": f"同一概念出现多种写法：{used}；应统一（术语表要求）",
                    "severity": "advisory"})
        for preferred_term, rule in preferred.items():
            variants = [g for g in groups if preferred_term in g]
            for group in variants:
                for term in group:
                    if term == preferred_term:
                        continue
                    hits = [item["id"] for item in normalized if term in item["text"]]
                    if hits:
                        glossary_findings.append({
                            "code": "terminology_deviation", "id": ",".join(hits),
                            "text": term, "token": preferred_term,
                            "message": f"应统一为「{preferred_term}」：{rule}", 
                            "severity": "defect"})
    findings.extend(glossary_findings)

    defects = [f for f in findings if f["severity"] == "defect"]
    advisories = [f for f in findings if f["severity"] == "advisory"]
    gaps = []
    if defects:
        gaps.append({"code": "copy_defects", "message": f"{len(defects)} 处文案机械缺陷："
                     + "；".join(f"{f['id']} {f['message']}" for f in defects[:5])})
    return {
        "total": len(normalized),
        "defects": len(defects),
        "advisories": len(advisories),
        "findings": findings,
        "gaps": gaps,
        "verdict_ceiling": "待核实" if gaps else "通过",
        "scope": ("只判能机械判的文案问题（占位符/排版/术语/近义异写）；"
                  "语义歧义、信息缺失、说服力等定性问题须人工判读"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="文案一致性与机械缺陷检查")
    parser.add_argument("--input", required=True)
    parser.add_argument("--glossary", help="术语表 {term_groups, preferred}")
    parser.add_argument("--emit-json")
    parser.add_argument("--cross-view", action="store_true",
                        help="跨视图互比模式：--input 为 {\"视图名\": [文案, ...]}，"
                             "横向比对量词单位与小数精度（只产观察候选）")
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args(argv)
    payload = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
    glossary = (json.loads(Path(args.glossary).read_text(encoding="utf-8-sig"))
                if args.glossary else None)
    result = (analyse_cross_view(payload) if getattr(args, "cross_view", False)
              else analyse(payload, glossary))
    if args.emit_json:
        Path(args.emit_json).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.enforce and result["gaps"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
