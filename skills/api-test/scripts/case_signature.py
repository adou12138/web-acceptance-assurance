# -*- coding: utf-8 -*-
"""用例签名：判等，不打分。

为什么需要它
------------
「两条用例是不是同一件事」必须能被机械判定。可用的判据只有三类：ID、名字、签名。
ID 是标签（同一请求可以有两个 ID），名字是措辞（同义改写后不同），只有签名是
「决定这条请求在测什么」的字段组合。

为什么不用相似度
----------------
相似度是打分，必须挑一个阈值，而阈值永远在两种错之间摇摆：
  - 共享脚手架多 → 高分 → 不同用例被判成重复（误判）
  - 措辞差异大   → 低分 → 同一条请求被判成不重复（漏判）
实测（outputs/af-dedup-probe，真实 133 条用例池）：
  jaccard 0.80 在 B-NULL 族上判重率 95%、B-OVER 族 93%，全是不同字段的用例；
  同时 20 组「同 dimension + 同 params + 同 expect」的真重复，它一组都没判出（0.68~0.75）。
判等没有这个自由度：签名相同才是同一件事。

签名里放什么
------------
``(dimension, params, expect)`` 的规范化 JSON。
故意不含 ``name`` / ``category`` / ``evidence`` / ``priority``——那些是意图标签，
改标签不代表请求变了。代价是「同签名不同意图」会被判重，所以折叠时要把意图并进
``evidence``，不能直接扔掉后一条（见 :func:`collapse`）。

与另外两个「指纹」的区别
------------------------
- ``regression.case_fingerprint``：整条用例的 SHA256，回答「和基线里的是不是同一条」，
  变了就是 ``STALE_BASELINE``。
- ``defect_keys.SIGNATURE_FIELDS``：缺陷的结构化字段组合，回答「两条缺陷是不是同一条」。
两者与去重签名用途不同，不能混用：同一条用例被改了文案，去重签名不变、基线指纹变。
"""
import hashlib
import json

def _sends_own_payload(case):
    """用例自带载荷：``check_args.payload_a/b`` 才是真正发出的请求。

    这类用例的 ``params`` 只是占位（典型是 H01 根因对比：两条错误请求放在
    check_args 里）。拿它的 params 参与判重或矛盾检测，会把「本来就不同的请求」
    当成「同一请求被写了相反期望」。
    """
    args = case.get("check_args") or {}
    return any(str(k).startswith("payload") for k in args)


# 比较时排除的用例：占位、待人工项，以及自带载荷的检查器。它们要么不是真实请求，
# 要么真实请求不在 params 里，参与判重或矛盾检测只会制造噪音。
def _actionable(case):
    if case.get("enabled") is False:
        return False
    if str(case.get("id") or "").startswith("TODO-"):
        return False
    if case.get("needs"):
        return False
    if _sends_own_payload(case):
        return False
    return True


def canon(obj):
    """规范化 JSON：键序、空格、分隔符全部固定，才能做逐字节比较。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def signature(case):
    """去重签名。dimension 参与，因为不同维度验不同的事（同请求可以合法复用）。"""
    raw = canon([str(case.get("dimension") or ""),
                 case.get("params") or {},
                 case.get("expect") or {}])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def params_signature(case):
    """请求签名：只含 params，用于「同请求不同期望」的矛盾检测。"""
    return hashlib.sha256(canon(case.get("params") or {}).encode("utf-8")).hexdigest()


def duplicate_groups(cases):
    """返回重复组（每组 >=2 条），顺序按首次出现，组内也按出现顺序。"""
    buckets = {}
    for case in cases:
        if not _actionable(case):
            continue
        buckets.setdefault(signature(case), []).append(case)
    return [group for group in buckets.values() if len(group) > 1]


def duplicate_stats(cases):
    """只统计不修改：用于报告「本轮有多少条是同一条请求的重复」。"""
    groups = duplicate_groups(cases)
    return {
        "groups": len(groups),
        "extra": sum(len(g) - 1 for g in groups),
        "cases": sum(len(g) for g in groups),
        "total": len(cases),
        "detail": [{
            "signature": signature(g[0])[:16],
            "kept": str(g[0].get("id") or ""),
            "duplicates": [str(x.get("id") or "") for x in g[1:]],
        } for g in groups],
    }


def collapse(cases):
    """折叠重复：保留首条，把后续各条的意图（名字/类别/证据）并进首条。

    只在显式要求时调用——默认不改用例集合，避免历史基线与覆盖率口径被动变化。
    返回 (kept_cases, collapsed_count)。
    """
    kept, seen, collapsed = [], {}, 0
    for case in cases:
        if not _actionable(case):
            kept.append(case)
            continue
        sig = signature(case)
        first = seen.get(sig)
        if first is None:
            seen[sig] = case
            kept.append(case)
            continue
        collapsed += 1
        merged = first.setdefault("merged_intents", [])
        intent = {"id": case.get("id"), "name": case.get("name"),
                  "category": case.get("category")}
        merged.append(intent)
        evidence = first.setdefault("evidence", [])
        for item in case.get("evidence") or []:
            if item not in evidence:
                evidence.append(item)
    return kept, collapsed


def expectation_conflicts(cases):
    """同一请求被写了互相冲突的期望。

    分两级，避免把「断言维度不同」当成矛盾：
      CONFLICT  —— 同一 params 既期望 success=true 又期望 success=false。两者不可能
                   同时成立，跑起来至少一条产出假缺陷。
      DIVERGENT —— success 一致，但 expect 内容不同（典型是各维度断言不同字段）。
                   这是正常复用，只计数不点名。
    """
    buckets = {}
    for case in cases:
        if not _actionable(case):
            continue
        buckets.setdefault(params_signature(case), []).append(case)

    conflicts, divergent = [], 0
    for sig, group in buckets.items():
        if len(group) < 2:
            continue
        want_true = [str(c.get("id")) for c in group if (c.get("expect") or {}).get("success") is True]
        want_false = [str(c.get("id")) for c in group if (c.get("expect") or {}).get("success") is False]
        if want_true and want_false:
            conflicts.append({
                "params_signature": sig[:16],
                "expect_success": want_true,
                "expect_failure": want_false,
                "params": group[0].get("params") or {},
            })
        elif len({canon(c.get("expect") or {}) for c in group}) > 1:
            divergent += 1
    return {"conflicts": conflicts, "divergent_groups": divergent}
