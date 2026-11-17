# -*- coding: utf-8 -*-
"""回归基线：契约哈希、回归用例选择、期望值快照与复测判定。

快照纪律（防过期期望误报 / 防空转假通过）：
* 快照按用例 ID 记录最近一次"通过时"的关键期望值与契约哈希；
* 复测时快照缺失 → UNBASELINED（不得静默判过）；契约哈希变化 → STALE_BASELINE；
* 判定输出四态：FIXED / STILL_FAILING / REGRESSED / BLOCKED_ENV。
"""
import hashlib
import json


def contract_hash(model_dict):
    raw = json.dumps(model_dict, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def case_fingerprint(case):
    """完整用例内容摘要；审批以外的输入、断言与检查配置均参与绑定。"""
    raw = json.dumps(case, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def select_cases(cases, mode="risk", finding=None):
    finding = finding or {}
    if mode == "targeted":
        ids = set(finding.get("case_ids", []))
        return [case for case in cases if case.get("id") in ids]
    if mode == "related":
        dim = finding.get("dimension")
        return [case for case in cases if case.get("dimension") == dim]
    return [case for case in cases if case.get("priority") == "P0" or case.get("risk", {}).get("level") == "high"]


def save_baseline(path, entries):
    """写入/更新回归基线快照。entries: [{case_id, contract_hash, expectations:{...}}]"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "entries": entries}, f, ensure_ascii=False, indent=2)


def load_baseline(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    cases = data.get("cases")
    hashes = {}
    if cases is not None:
        if not isinstance(cases, list) or any(not isinstance(c, dict) or not c.get("id") for c in cases):
            raise ValueError("基线 cases 必须是有编号的用例列表")
        hashes = {c["id"]: case_fingerprint(c) for c in cases}
        if len(hashes) != len(cases):
            raise ValueError("基线 cases 编号重复")
    entries = data.get("entries", [])
    baseline = {}
    for entry in entries:
        cid = entry["case_id"]
        if cid in baseline:
            raise ValueError("基线 entries 编号重复：%s" % cid)
        item = dict(entry)
        if cid in hashes:
            if item.get("case_hash") and item["case_hash"] != hashes[cid]:
                raise ValueError("基线 case_hash 与用例内容不一致：%s" % cid)
            item["case_hash"] = hashes[cid]
        baseline[cid] = item
    return baseline


def retest_verdicts(baseline, results, current_contract_hash=None, current_cases=None):
    """复测判定。results: [{case_id, passed, error}] → [{case_id, verdict}]。

    verdict:
      FIXED          快照存在，本轮通过
      STILL_FAILING  快照存在，本轮仍失败（同类错误）
      REGRESSED      快照存在且曾通过，本轮以不同错误失败（修复引入新回归）
      STALE_BASELINE 契约哈希变化，快照不可信，需重建基线
      UNBASELINED    无快照，不得声称复测通过
      BLOCKED_ENV    error 为环境类关键词
    """
    env_words = ("timeout", "timed out", "connection", "频率过快", "网络", "限流")
    out = []
    current_hashes = ({c["id"]: case_fingerprint(c) for c in current_cases}
                      if current_cases is not None else None)
    for r in results:
        cid = r.get("case_id")
        base = baseline.get(cid)
        err = str(r.get("error") or "")
        if any(w in err.lower() for w in env_words):
            out.append({"case_id": cid, "verdict": "BLOCKED_ENV"})
            continue
        if base is None:
            out.append({"case_id": cid, "verdict": "UNBASELINED"})
            continue
        if current_contract_hash and base.get("contract_hash") != current_contract_hash:
            out.append({"case_id": cid, "verdict": "STALE_BASELINE"})
            continue
        if current_hashes is not None:
            if not base.get("case_hash") or cid not in current_hashes:
                out.append({"case_id": cid, "verdict": "UNBASELINED"})
                continue
            if base["case_hash"] != current_hashes[cid]:
                out.append({"case_id": cid, "verdict": "STALE_BASELINE"})
                continue
        if r.get("passed"):
            out.append({"case_id": cid, "verdict": "FIXED"})
        elif base.get("expectations", {}).get("last_error") and err and err != base["expectations"].get("last_error"):
            out.append({"case_id": cid, "verdict": "REGRESSED"})
        else:
            out.append({"case_id": cid, "verdict": "STILL_FAILING"})
    return out
