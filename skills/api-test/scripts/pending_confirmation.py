# -*- coding: utf-8 -*-
"""待确认项（PCI）聚合：从「几条失败归因于契约不确定」变成「谁去问、问什么、问完放行哪些用例」。

为什么需要单独一层
------------------
归因只在单条用例上判 `NEEDS_CONFIRMATION`，产出的是「这条失败先别提单」。
但执行完一轮，人拿到的是十几条同维度、同成因的待确认项——没有分组、没有解除条件，
结果要么全部搁置，要么被当成缺陷提上去。这一层做三件事：

1. 按维度聚组，给出每组阻塞了多少条；
2. 写清解除条件（确认哪个维度/哪类约束后可以放行）；
3. 标 impact —— 组内出现 P0 用例时升级为 blocker，其余 high。

刻意不做语义相似度聚类：分组键只有维度，稳定、可复核。合并错的代价是「两组被并成一组」，
比「用相似度猜分组」可解释得多。
"""
NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"


def _attr_of(finding):
    return str((finding or {}).get("attribution") or "").upper()


def collect(findings, cases=None):
    """聚合待确认项。``cases`` 可选：给了就按维度统计其中 P0 条数决定 impact。"""
    cases = cases or []
    p0_by_dim = {}
    for case in cases:
        if str(case.get("priority")) == "P0":
            dim = str(case.get("dimension") or "")
            p0_by_dim[dim] = p0_by_dim.get(dim, 0) + 1

    groups = {}
    for finding in findings or []:
        if _attr_of(finding) != NEEDS_CONFIRMATION:
            continue
        dim = str(finding.get("dimension") or "-")
        group = groups.setdefault(dim, {"dimension": dim, "findings": [], "p0": 0})
        group["findings"].append({
            "id": finding.get("id"),
            "desc": str(finding.get("desc") or "")[:120],
            "expected": str(finding.get("expected") or "")[:80],
            "actual": str(finding.get("actual") or "")[:80],
        })
        if str(finding.get("severity") or "") in ("高", "high"):
            group["p0"] += 1

    items = []
    for dim in sorted(groups):
        group = groups[dim]
        blocked = len(group["findings"])
        blocker = group["p0"] > 0 or p0_by_dim.get(dim, 0) > 0
        items.append({
            "dimension": dim,
            "impact": "blocker" if blocker else "high",
            "blocked_count": blocked,
            "blocked_findings": [f["id"] for f in group["findings"]],
            "summary": "、".join(f["desc"] for f in group["findings"][:3]),
            "reason": "契约证据置信度不足（<0.8），断言依据来自推断而非契约原文",
            "resolution_condition": (
                "确认维度 %s 这些失败对应的约束在契约中是否确有声明（类型/枚举/长度/默认值/"
                "必填性）；确认后把依据补成契约原文并重跑，本轮结论即为定论" % dim),
            "next_action": "补齐契约原文或标注该约束为推断，然后重跑本维度",
        })
    return {"total": sum(i["blocked_count"] for i in items),
            "groups": len(items), "items": items}
