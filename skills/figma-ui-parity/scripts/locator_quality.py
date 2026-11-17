#!/usr/bin/env python3
"""元素定位可信度分级（单一来源）。

## 为什么需要它

缺陷的 `element.selector` 同时承担两件事：
1. 给人复现（点哪里）；
2. 给机器跨轮认人（`normalize_evidence.fingerprint` 与 baseline 的 `fingerprint`
   都含 selector + nth）。

第 2 件事决定了选择器质量会直接影响结论：`div:nth-child(3)`、`css-1x2y3z` 这类
定位在页面重构后必然变化，同一个缺陷在下一轮会被算成「新增」，报告于是长出
一批假回归；反过来，一个真正新增的缺陷也可能因为 nth 恰好撞上而被误判成旧的。

本模块只做一件事：把「这条定位有多可信」判出来，写进 `element.locatorQuality`。
它不改判定、不拦门禁——**它是给人排序和给报告加注释用的**，不是让机器据此
删除缺陷。跨轮匹配的降级策略需要改 baseline 引擎，属于独立改动。

分级口径（从稳到脆）：
  stable  —— data-testid / data-test / data-qa / role / aria-label 等语义锚点
  semi    —— 单段 id、语义化 class（如 .btn-submit）、tag.class
  fragile —— 纯标签名、生成类名（css-xxx / sc-xxx / 长哈希）、依赖 nth 序号
  none    —— 页面级缺陷，本就没有元素定位（不是脆弱，是不适用）
"""
from __future__ import annotations

import re

# 明确为测试而设的属性：页面作者能控制、重构时通常会保留
# [name= 也算：表单字段的 name 通常与接口契约绑定，改名比改类名慎重得多
STABLE_ATTRS = ("data-testid", "data-test", "data-qa", "data-cy", "aria-label",
                "[role=", "role=", "[name=")

# 构建工具随机生成的类名：每次构建都变，跨轮必然不同
_GENERATED_CLASS = re.compile(
    r"(^|[.#\s])(css-[a-z0-9]{4,}|sc-[a-zA-Z0-9]{4,}|jsx-\d+|[a-z]*_[a-z0-9]{5,})", re.ASCII)

# 纯结构标签：本身不携带任何语义
_GENERIC_TAGS = {"div", "span", "i", "b", "em", "strong", "p", "li", "td", "tr", "tbody", "section"}

# :nth-child / :nth-of-type 等结构伪类：位置一变就失配
_STRUCTURAL_PSEUDO = re.compile(r":nth-(child|of-type|last-child|first-child)", re.ASCII)


def _is_hashy(text: str) -> bool:
    """id / class 是否像随机串（含连续 4 位以上数字、超长串、React 的 :r1: 形式）。"""
    if not text:
        return False
    if len(text) > 24:
        return True
    if re.search(r"\d{4,}", text, re.ASCII):
        return True
    if re.search(r":[a-z]\d+:", text, re.ASCII):
        return True
    return False


def grade(selector: str, nth: int = 0) -> dict:
    """返回 {quality, signals, advice}。quality ∈ stable/semi/fragile/none。"""
    sel = str(selector or "").strip()
    signals = []
    try:
        n = int(nth or 0)
    except (TypeError, ValueError):
        n = 0

    if not sel:
        return {"quality": "none", "signals": ["页面级缺陷，无元素定位"],
                "advice": ""}

    lowered = sel.lower()
    for attr in STABLE_ATTRS:
        if attr in lowered:
            signals.append("语义锚点 " + attr.strip("[]="))
            quality = "stable"
            break
    else:
        quality = None

    if quality is None:
        if sel.startswith("#") and " " not in sel and ">" not in sel:
            ident = sel[1:]
            if _is_hashy(ident):
                quality = "fragile"
                signals.append("id 形似随机串：" + ident[:24])
            else:
                quality = "semi"
                signals.append("单段 id 定位")
        elif _GENERATED_CLASS.search(sel):
            quality = "fragile"
            signals.append("含构建期生成的类名（每次构建都会变）")
        elif _STRUCTURAL_PSEUDO.search(sel):
            quality = "fragile"
            signals.append("依赖结构伪类（位置一变就失配）")
        elif sel.startswith(".") or "." in sel:
            quality = "semi"
            signals.append("class 定位")
        else:
            tag = sel.split()[0].split(">")[0].strip().lower()
            if tag in _GENERIC_TAGS:
                quality = "fragile"
                signals.append("仅用通用标签名 " + tag)
            else:
                quality = "semi"
                signals.append("标签名定位")

    # nth > 0 表示靠序号区分同类元素：可靠度降一级
    if n > 0:
        signals.append("依赖 nth={} 区分同类元素".format(n))
        quality = {"stable": "semi", "semi": "fragile", "fragile": "fragile"}.get(quality, quality)

    advice = ""
    if quality == "fragile":
        advice = ("建议页面补 data-testid（或用语义化 role / aria-label）：当前定位依赖结构或"
                  "生成类名，重构后同一缺陷在下一轮会被判成新增，形成假回归。")
    elif quality == "semi":
        advice = "可用；补 data-testid 可让该缺陷在跨轮对比里稳定认人。"

    return {"quality": quality, "signals": signals, "advice": advice}


def enrich_element(element) -> dict:
    """给 element 补 locatorQuality / locatorSignals / locatorAdvice（幂等）。

    已有 locatorQuality 的不覆盖：producer 若明确声明了更好的判断，尊重它。
    """
    el = dict(element) if isinstance(element, dict) else {}
    if el.get("locatorQuality"):
        return el
    result = grade(el.get("selector", ""), el.get("nth", 0))
    el["locatorQuality"] = result["quality"]
    if result["signals"]:
        el["locatorSignals"] = result["signals"]
    if result["advice"]:
        el["locatorAdvice"] = result["advice"]
    return el


QUALITY_LABEL = {
    "stable": "稳定",
    "semi": "一般",
    "fragile": "脆弱",
    "none": "不适用",
}


def label_of(element) -> str:
    """报告用短标签：脆弱 / 一般 / 稳定 / 不适用。"""
    if not isinstance(element, dict):
        return ""
    quality = str(element.get("locatorQuality") or "")
    return QUALITY_LABEL.get(quality, "")
