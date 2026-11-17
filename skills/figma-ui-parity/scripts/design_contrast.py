#!/usr/bin/env python3
"""7.42 设计稿侧文本对比度：在**评审阶段**算出「这行字在设计稿上就读不清」。

为什么放在设计侧
----------------
7.41 只能事后发现「页面上这行灰字不达标」。而设计模型里已经有文字颜色和它逐层祖先的
填充——对比度不需要页面就能算准。这是设计稿驱动测试独有的位置：任何纯页面测试工具
手上没有设计数据，只能在页面做出来之后才发现；而这时改配色要同时改设计稿和代码。

判定边界（哪些算 confirmed、哪些只能 pending）
---------------------------------------------
- 文字背后是**纯色**：可精确计算 → 不达标即 `confirmed`。
- 背后是**图片**：无法从节点数据得到像素 → `pending`，写明原因，不猜。
- 背后是**渐变**：对每个色标分别算，取最差一档。所有色标都不达标 → `confirmed`；
  只有部分色标不达标 → `pending`（部分区域可能仍可读，需人看设计稿）。
- 祖先链上没有任何填充（设计稿透明）→ 按白底计算，但 `pending`：
  透明背景最终落在什么底色上由实现决定，不假设即通过。
- 祖先 `opacity < 0.95`：整组半透明，合成结果依赖下层，`pending`。

不判定项（写进产物 notes，避免读成「这块已经测过」）
- 图标/图形对比度（非文本，WCAG 1.4.11 另属一类）
- 文字压在照片上的实际像素亮度
- 设计稿与实现是否一致（那是像素层/样式层维度的事）

输入：`extract_design_fields.py` 产出的设计模型（含 tree 层级）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DIMENSION = "7.42"
# WCAG 2.1 1.4.3：正文 4.5:1；大字（>=24px 或 >=18.66px 且粗体 >=700）3:1
AA_NORMAL = 4.5
AA_LARGE = 3.0
# 祖先链累计不透明度低于该值时不再当纯色处理：合成结果依赖下层，算出来的数不可信
OPAQUE_ENOUGH = 0.95
# 渐变/多层叠加后候选底色数量上限（再多就退化为 pending，避免"算了一堆没人看"）
MAX_CANDIDATES = 8

WHITE = {"r": 255, "g": 255, "b": 255, "a": 1.0}


# ---------------------------------------------------------------- 颜色运算
def _channel(value: float) -> float:
    value = value / 255.0
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def luminance(color: dict) -> float:
    return (0.2126 * _channel(color["r"]) + 0.7152 * _channel(color["g"])
            + 0.0722 * _channel(color["b"]))


def contrast_ratio(a: dict, b: dict) -> float:
    la, lb = luminance(a), luminance(b)
    high, low = max(la, lb), min(la, lb)
    return (high + 0.05) / (low + 0.05)


def over(fg: dict, bg: dict) -> dict:
    """alpha 合成：fg 叠在 bg 上（bg 视为不透明）。"""
    alpha = max(0.0, min(1.0, float(fg.get("a", 1.0))))
    return {
        "r": int(round(fg["r"] * alpha + bg["r"] * (1 - alpha))),
        "g": int(round(fg["g"] * alpha + bg["g"] * (1 - alpha))),
        "b": int(round(fg["b"] * alpha + bg["b"] * (1 - alpha))),
        "a": 1.0,
    }


def paint_color(paint: dict) -> dict | None:
    """SOLID paint → 颜色；其它 paint 类型返回 None。"""
    if not isinstance(paint, dict) or paint.get("type") != "SOLID":
        return None
    color = paint.get("color") or {}
    rgb = color.get("rgb")
    if not isinstance(rgb, list) or len(rgb) != 3:
        return None
    try:
        alpha = float(color.get("alpha", 1)) * float(paint.get("opacity", 1))
    except (TypeError, ValueError):
        alpha = 1.0
    return {"r": int(rgb[0]), "g": int(rgb[1]), "b": int(rgb[2]),
            "a": max(0.0, min(1.0, alpha))}


def effective_paint(fills: list) -> dict:
    """取最上层可见填充：Figma 的 fills 数组末项绘制在最上面。"""
    if not isinstance(fills, list):
        return {"kind": "none"}
    for paint in reversed(fills):
        if not isinstance(paint, dict) or paint.get("visible") is False:
            continue
        kind = str(paint.get("type") or "")
        if kind == "SOLID":
            color = paint_color(paint)
            if color:
                return {"kind": "solid", "color": color}
            continue
        if kind.startswith("GRADIENT"):
            colors = []
            for stop in paint.get("stops") or []:
                color = paint_color({"type": "SOLID", **(stop or {})})
                if color:
                    colors.append(color)
            if colors:
                return {"kind": "gradient", "colors": colors}
            continue
        if kind in ("IMAGE", "VIDEO"):
            return {"kind": "image"}
        if kind == "UNSUPPORTED":
            return {"kind": "unknown"}
    return {"kind": "none"}


def apply_fill(candidates: list | None, paint: dict) -> list | None:
    """把一层填充叠到当前候选底色上；返回 None 表示底色已不可知。"""
    kind = paint.get("kind")
    if kind == "none":
        return candidates
    if kind == "image":
        return None
    if kind == "solid":
        if candidates is None:
            return None
        return [over(paint["color"], base) for base in candidates]
    if kind == "gradient":
        if candidates is None:
            return None
        out = []
        for stop in paint["colors"]:
            for base in candidates:
                out.append(over(stop, base))
        return _dedupe(out)
    return None


def _dedupe(colors: list) -> list:
    seen, out = set(), []
    for color in colors:
        key = (color["r"], color["g"], color["b"])
        if key in seen:
            continue
        seen.add(key)
        out.append(color)
    return out


# ---------------------------------------------------------------- 模型遍历
def text_of(node: dict) -> dict | None:
    value = node.get("text")
    if not isinstance(value, dict):
        return None
    if not str(value.get("characters") or "").strip():
        return None
    return value


def required_ratio(text: dict) -> tuple:
    """→ (阈值, 是否大字)。字号缺失时不按大字处理（宁可严一点）。"""
    try:
        size = float(text.get("fontSize") or 0)
    except (TypeError, ValueError):
        size = 0.0
    try:
        weight = int(text.get("fontWeight") or 400)
    except (TypeError, ValueError):
        weight = 400
    if size >= 24:
        return AA_LARGE, True
    if size >= 18.66 and weight >= 700:
        return AA_LARGE, True
    return AA_NORMAL, False


def node_path(parent_path: str, node: dict) -> str:
    name = str(node.get("name") or node.get("id") or "?")
    return (parent_path + " / " + name) if parent_path else name


def _dedupe_reasons(reasons: list) -> list:
    out = []
    for item in reasons:
        if item not in out:
            out.append(item)
    return out


def walk(node: dict, candidates: list | None, path: str, uncertain: list,
         findings: list, stats: dict) -> None:
    """递归遍历。`candidates` 是**该节点自身填充之上**的候选底色集合。"""
    if not isinstance(node, dict) or node.get("visible") is False or node.get("opacity") == 0:
        stats["skippedInvisible"] += 1
        return
    here = node_path(path, node)
    paint = effective_paint(node.get("fills"))
    try:
        opacity = float(node.get("opacity", 1) or 1)
    except (TypeError, ValueError):
        opacity = 1.0
    own_uncertain = list(uncertain)
    source = node.get("sourceEvidence") or {}
    own_uncertain.extend(source.get("uncertainties") or [])
    if source.get("source") == "figma-mcp-yaml-ish":
        availability = source.get("propertyAvailability") or {}
        if not availability.get("fills", False):
            own_uncertain.append("MCP 未提供可信填充，不能据祖先底色确认对比度")
        if not availability.get("opacity", False):
            own_uncertain.append("MCP 未提供可信不透明度，实际合成效果待确认")
        if node.get("type") == "TEXT" and (not availability.get("text.fontSize") or not availability.get("text.fontWeight")):
            own_uncertain.append("MCP 字号或字重未提供，可读性阈值待确认")
    if opacity < OPAQUE_ENOUGH:
        own_uncertain.append("祖先 `{}` 不透明度 {}（整组半透明，合成底色依赖下层）".format(
            node.get("name") or node.get("id"), round(opacity, 2)))
    own_uncertain = _dedupe_reasons(own_uncertain)

    if node.get("type") == "TEXT":
        stats["textNodes"] += 1
        check_text(node, candidates, here, own_uncertain, findings, stats, opacity)
        return

    next_candidates = apply_fill(candidates, paint)
    if next_candidates is None and paint.get("kind") == "image":
        own_uncertain = _dedupe_reasons(own_uncertain + [
            "祖先 `{}` 使用图片填充，文字实际落在图片像素上".format(node.get("name") or node.get("id"))])
        stats["imageBackdrop"] += 1
    for child in node.get("children") or []:
        walk(child, next_candidates, here, own_uncertain, findings, stats)


def check_text(node: dict, candidates: list | None, path: str, uncertain: list,
               findings: list, stats: dict, node_opacity: float = 1.0) -> None:
    text = text_of(node)
    if text is None:
        stats["skippedEmptyText"] += 1
        return
    fill = effective_paint(node.get("fills"))
    if fill.get("kind") != "solid":
        # 文字用渐变/图片填充：无法用单一前景色计算，留痕不猜
        stats["skippedNonSolidText"] += 1
        findings.append(_finding(
            node, path, text, "pending", 0.0, AA_NORMAL, False,
            symptom="文字填充不是纯色（{}），无法用单一前景色判定对比度".format(fill.get("kind")),
            expected="文字填充为纯色，或在设计评审时人工确认可读性",
            actual="该文本节点填充类型：{}".format(fill.get("kind") or "none"),
            extra={"skipReason": "non-solid-text-fill"}))
        return
    fg = dict(fill["color"])
    # 文本节点自身不透明度直接削弱前景与底色的差：乘进 alpha 才是真实呈现色
    if node_opacity < OPAQUE_ENOUGH:
        fg["a"] = max(0.0, min(1.0, float(fg.get("a", 1)) * node_opacity))
    threshold, large = required_ratio(text)

    if candidates is None:
        stats["skippedUnknownBackdrop"] += 1
        findings.append(_finding(
            node, path, text, "pending", 0.0, threshold, large,
            symptom="文字背后是图片，无法从节点数据得到实际像素亮度",
            expected="正文对比度 >= 4.5:1，大字 >= 3:1",
            actual="底色为图片填充；需在设计稿上人工取样或等页面侧 7.41 复核",
            extra={"skipReason": "image-backdrop"}))
        return

    ratios = [contrast_ratio(over(fg, base), base) for base in (candidates or [WHITE])]
    worst = min(ratios) if ratios else 0.0
    best = max(ratios) if ratios else 0.0
    stats["textChecked"] += 1

    if worst >= threshold and not uncertain:
        return
    all_fail = best < threshold
    gradient = len(candidates or []) > 1
    if uncertain:
        status, confidence = "pending", 0.5
        why = "；".join(uncertain)
    elif gradient and not all_fail:
        status, confidence = "pending", 0.55
        why = "部分渐变区段不达标（最差 {:.2f}:1，最好 {:.2f}:1），需人工确认文字实际落在哪一段".format(worst, best)
    else:
        status = "confirmed"
        confidence = 0.85 if not gradient else 0.7
        why = "渐变全部区段均不达标" if gradient else "文字背后为纯色，可精确计算"

    severity = "P2" if worst < 3.0 else "P3"
    findings.append(_finding(
        node, path, text, status, confidence, threshold, large,
        symptom=("设计稿文本对比度待确认：{:.2f}:1（要求 {}:1）" if uncertain else
                 "设计稿文本对比度不足：{:.2f}:1（要求 {}:1）").format(worst, threshold),
        expected="正文对比度 >= 4.5:1，大字（>=24px 或 >=18.66px 粗体）>= 3:1（WCAG 2.1 1.4.3 AA）",
        actual="前景 {} / 底色 {}；最差 {:.2f}:1{}".format(
            _hex(fg), _hex(candidates[0]) if candidates else "白", worst,
            "（含 {} 个候选底色）".format(len(candidates)) if len(candidates or []) > 1 else ""),
        extra={"ratio": round(worst, 2), "bestRatio": round(best, 2), "required": threshold,
               "largeText": large, "candidateBackdrops": len(candidates or []),
               "why": why},
        severity=severity))


def _hex(color: dict) -> str:
    alpha = round(float(color.get("a", 1)), 2)
    base = "#{:02x}{:02x}{:02x}".format(color["r"], color["g"], color["b"])
    return base if alpha >= 1 else "{} (alpha {})".format(base, alpha)


def _finding(node: dict, path: str, text: dict, status: str, confidence: float,
             threshold: float, large: bool, symptom: str, expected: str,
             actual: str, extra: dict, severity: str = "P3") -> dict:
    rect = node.get("rect") or {}
    return {
        "dimension": DIMENSION,
        "status": status,
        "property": "design-contrast",
        "severity": severity,
        "severityHint": severity,
        "confidence": confidence,
        "symptom": "{}（设计稿节点：{}）".format(symptom, path),
        "expected": expected,
        "actual": actual,
        "element": {
            "selector": "",
            "nth": 0,
            # 设计侧没有 CSS 选择器，用节点名路径做锚点：人工回设计稿按名字搜索
            "anchor": path,
            "bbox": {"x": rect.get("x") or 0, "y": rect.get("y") or 0,
                     "w": rect.get("w") or 0, "h": rect.get("h") or 0},
            "designNodeId": node.get("id"),
            "locatorQuality": "stable" if node.get("id") else "semi",
            "locatorSignals": ["design-node-id"],
        },
        "evidence": [{"type": "design-fill", "nodeId": node.get("id"),
                      "fontSize": text.get("fontSize"), "fontWeight": text.get("fontWeight")}],
        "raw": dict(extra, designPath=path, characters=str(text.get("characters"))[:40],
                    coordinateBasis=((node.get("sourceEvidence") or {}).get("coordinates") or "frame-relative")),
        "sources": ["design-contrast"],
    }


def analyse(model: dict) -> dict:
    tree = model.get("tree")
    if not isinstance(tree, dict):
        flat = model.get("flat")
        if isinstance(flat, list) and flat:
            # 只有扁平表、没有层级时无法回溯祖先底色：明确报 notRun，不按白底硬算
            return {"status": "notRun",
                    "reason": "设计模型缺少 tree 层级，无法回溯文字背后的祖先填充；"
                              "请用 extract_design_fields.py 的完整产物（含 tree）"}
        return {"status": "notRun", "reason": "设计模型为空或格式不符"}

    findings: list = []
    stats = {"textNodes": 0, "textChecked": 0, "skippedInvisible": 0,
             "skippedEmptyText": 0, "skippedNonSolidText": 0,
             "skippedUnknownBackdrop": 0, "imageBackdrop": 0}
    root_paint = effective_paint(tree.get("fills"))
    root_has_fill = root_paint.get("kind") != "none"
    uncertain = [] if root_has_fill else ["顶层画板无填充（透明背景），按白底计算"]
    source = tree.get("sourceEvidence") or {}
    uncertain.extend(source.get("uncertainties") or [])
    if source.get("source") == "figma-mcp-yaml-ish" and not (source.get("propertyAvailability") or {}).get("fills", False):
        uncertain.append("MCP 根画板填充不可确认")
    walk(tree, [WHITE], "", uncertain, findings, stats)

    confirmed = [f for f in findings if f["status"] == "confirmed"]
    pending = [f for f in findings if f["status"] == "pending"]
    status = "fail" if confirmed else ("pending" if pending else "pass")
    # 一条缺陷都没有 ≠ 没测：必须产出显式 pass 证据，否则 assemble 只看到
    # 「0 条 findings」，把「跑过且干净」记成 notRun。
    if not findings and stats["textChecked"]:
        findings.append({
            "dimension": DIMENSION,
            "status": "pass",
            "property": "design-contrast-pass",
            "severity": "P3",
            "severityHint": "P3",
            "confidence": 0.85,
            "symptom": "设计稿文本对比度全部达标",
            "expected": "正文对比度 >= 4.5:1，大字 >= 3:1",
            "actual": "已检查 {} 个文本节点全部达标".format(stats["textChecked"]),
            "element": {"selector": "", "nth": 0, "anchor": "design-contrast",
                        "bbox": {"x": 0, "y": 0, "w": 0, "h": 0},
                        "locatorQuality": "none", "locatorSignals": []},
            "evidence": [{"type": "design-fill", "checked": stats["textChecked"]}],
            "raw": {"textChecked": stats["textChecked"]},
            "sources": ["design-contrast"],
        })
    if not stats["textChecked"] and not findings:
        status = "notRun"
    return {
        "status": status,
        "findings": findings,
        "summary": {
            "textNodes": stats["textNodes"],
            "textChecked": stats["textChecked"],
            "confirmed": len(confirmed),
            "pending": len(pending),
            "skippedNonSolidText": stats["skippedNonSolidText"],
            "skippedUnknownBackdrop": stats["skippedUnknownBackdrop"],
            "skippedEmptyText": stats["skippedEmptyText"],
            "skippedInvisible": stats["skippedInvisible"],
        },
        "coverageNote": "只判定文本对比度（WCAG 1.4.3 AA）。未覆盖：图标/图形对比度（1.4.11）、"
                        "文字压在图片上的实际像素亮度、设计稿与实现的差异（那是像素层/样式层维度）。"
                        "底色为图片或顶层画板透明的文本记为 pending，不做白底假设式判定。",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="7.42 设计稿侧文本对比度")
    ap.add_argument("--design", required=True, type=Path,
                    help="extract_design_fields.py 的产物（设计模型 JSON）")
    ap.add_argument("--out", required=True, type=Path, help="findings 产物路径")
    args = ap.parse_args()

    if not args.design.exists():
        print("输入不存在: {}".format(args.design), file=sys.stderr)
        return 2
    try:
        model = json.loads(args.design.read_text(encoding="utf-8"))
    except ValueError as exc:
        print("设计模型不是合法 JSON: {}".format(exc), file=sys.stderr)
        return 2

    result = analyse(model)
    result["design"] = str(args.design)
    result["rootId"] = model.get("rootId")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out), "status": result.get("status"),
                      "summary": result.get("summary"), "reason": result.get("reason")},
                     ensure_ascii=False, indent=2))
    # notRun 不当作失败：缺少 tree 是输入问题，由门禁按 notRun 记账
    return 0


if __name__ == "__main__":
    sys.exit(main())
