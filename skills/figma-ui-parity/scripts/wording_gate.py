# -*- coding: utf-8 -*-
"""措辞门禁：拦住「正常展示」「符合预期」这类无法据以判定的描述。

为什么需要
----------
缺陷或待确认项写成「展示正常」「符合预期」时，读者无法复核，开发也无法据此复现。
但只做禁止词匹配会大面积误杀——「提示『保存成功』」里有禁止词，却是完全合格的描述。
所以判据是**双条件**：命中禁止词 **且** 同一句里没有任何具体性信号。

三类具体性信号（任一存在即放过）：
  1. 引号内有文案：'「保存成功」' 是可核对的字面量；
  2. 可量化数字：'间距 8px'、'新增 3 条'；
  3. UI 信号：颜色+元素（'红色边框'），或两个以上 UI 元素词（'弹窗'+'按钮'）。

刻意不做语义判断：这里只回答「这句话能不能据以判定」，不评价对错。
"""
import re

BANNED = ("正常展示", "正常显示", "正常加载", "符合预期", "功能正常", "数据正确",
          "操作成功", "验证成功", "展示完整", "流程正确", "无异常", "一切正常")

_QUOTED = re.compile(r"「[^」]{1,40}」|『[^』]{1,40}』|\"[^\"]{2,40}\"")
_NUMERIC = re.compile(r"\d+(?:\.\d+)?\s*(?:px|PX|个|条|项|列|行|字符|秒|ms|次|%)")
_COLORS = ("红色", "绿色", "蓝色", "橙色", "黄色", "灰色", "黑色", "白色")
_UI_WORDS = ("边框", "背景", "文字", "图标", "按钮", "弹窗", "提示", "标签", "输入框", "下拉")

# 扫描哪些字段：都是给人看的自然语言描述。结构化字段（selector/rect）不扫。
FIELDS = ("symptom", "expected", "actual", "reason", "detail", "desc")


def has_specificity(text):
    """是否存在任一具体性信号。"""
    text = str(text or "")
    if _QUOTED.search(text) or _NUMERIC.search(text):
        return True
    if any(c in text for c in _COLORS) and any(u in text for u in _UI_WORDS):
        return True
    return sum(1 for u in _UI_WORDS if u in text) >= 2


def check_text(text):
    """返回命中的禁止词；命中但存在具体性信号时返回 None（放过）。"""
    text = str(text or "")
    hit = [w for w in BANNED if w in text]
    if not hit:
        return None
    return None if has_specificity(text) else hit[0]


def scan(report):
    """扫描报告里的自然语言字段。返回 {checked, flagged, pass}。"""
    flagged, checked = [], 0
    for item in _iter_items(report):
        for field in FIELDS:
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                continue
            checked += 1
            pattern = check_text(value)
            if pattern:
                flagged.append({
                    "field": field, "pattern": pattern,
                    "text": value[:120],
                    "dimension": item.get("dimension"),
                    "selector": (item.get("element") or {}).get("selector")
                                if isinstance(item.get("element"), dict) else None,
                })
    return {"checked": checked, "flagged": flagged, "pass": not flagged}


RESTORE_CLAIM = "还原度"
_CONCLUSION_FIELDS = ("title", "summary", "conclusion", "note")


def scan_restore_claims(report):
    """无设计稿时，报告标题/结论不得出现「还原度」。

    只在 meta.designProvided is False 时启用：None/True 表示未提供该事实、
    或确有设计稿，此时不做断言（避免误伤有设计稿的还原度报告）。这是机械
    门禁，不依赖 agent 自觉——缺设计稿的走查报告一旦写「还原度」，
    读者会误以为做过设计稿比对。
    """
    if not isinstance(report, dict):
        return {"checked": 0, "flagged": [], "pass": True}
    meta = report.get("meta")
    if not isinstance(meta, dict) or meta.get("designProvided") is not False:
        return {"checked": 0, "flagged": [], "pass": True}
    flagged, checked = [], 0
    targets = [("meta." + f, meta.get(f)) for f in _CONCLUSION_FIELDS]
    targets += [(f, report.get(f)) for f in _CONCLUSION_FIELDS]
    for field, value in targets:
        if not isinstance(value, str) or not value.strip():
            continue
        checked += 1
        if RESTORE_CLAIM in value:
            flagged.append({"field": field, "text": value[:120]})
    return {"checked": checked, "flagged": flagged, "pass": not flagged}


def _iter_items(report):
    """遍历可能承载自然语言的条目：缺陷容器 + 待确认容器。"""
    if not isinstance(report, dict):
        return
    try:
        import defect_keys as DK
        for _key, items in DK.iter_containers(report):
            for item in items or []:
                if isinstance(item, dict):
                    yield item
        for key in DK.PENDING_CONTAINER_KEYS:
            for item in report.get(key) or []:
                if isinstance(item, dict):
                    yield item
    except Exception:
        for item in report.get("defects") or []:
            if isinstance(item, dict):
                yield item
