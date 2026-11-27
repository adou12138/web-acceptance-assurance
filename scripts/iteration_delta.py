"""迭代增量与循环产物边界：只从执行台账派生，不读可编辑清单。

口径（S2/S3 修订版）：

- **本轮新增执行数**：`events.jsonl` 中 `seq` 大于水位线、`status ∈ {PASS, FAIL}`、
  `attempt_id` 未在历史出现、且执行签名 `(case_id, surf_id, intended_action)` 未在
  历史出现的 FINISH 数。BLOCKED/NOT_RUN/SKIPPED 不计；只有 BEGIN 没有 FINISH 的
  不在此计（属于 side_effect_pending 安全联锁）。
- **证据 sha256 只披露、不排除**：截图/带时间戳日志每轮哈希都不同，用它排除会把
  同一探针的重跑永远算新执行；用它计入又会把确定性响应（哈希稳定）误判成零增量。
  执行签名才是跨证据类型稳定的口径。
- **oracle_source=independent 单列披露**：`execution_ledger._attack_fields` 只校验
  「给了通道 + 有证据文件」，不能证明通道真的独立，因此不作为停机判据。
- **计划外风险面**：FINISH 的 `attack.surf_id` 不在 plan 的 surfaces 清单内时记
  `unregistered_expansion`，**照常计入执行增量**（与 attack_coverage「计划外计为产出」
  同口径），只标记不阻断、不丢弃。
- **循环产物**：只允许 raw 候选（现象/引用/证据/iteration_id），出现判定字段即视为
  该轮产物无效；禁用键集在采集阶段口径上补 accepted/accepted_by/triage_state。
"""
import phase_gate as _PG

# 采集阶段禁判定口径的扩展：loop 候选同样不得携带裁决字段。
# `accepted` 不在 COLLECTION_FORBIDDEN_KEYS 里，单列补齐（否则方案 2.5 会漏检）。
LOOP_FORBIDDEN_KEYS = frozenset(_PG.COLLECTION_FORBIDDEN_KEYS) | {
    "accepted", "accepted_by", "triage_state"}
# 嵌套层与采集阶段同口径：HTTP status、查询结果集是可观测事实，不是裁决字段；
# 顶层仍用全量集合（顶层 status 冒充判定通道必须拦）。
LOOP_NESTED_FORBIDDEN_KEYS = frozenset(_PG.NESTED_JUDGEMENT_KEYS) | {
    "accepted", "accepted_by", "triage_state"}

EXECUTED_STATUSES = {"PASS", "FAIL"}


def _seq(event, fallback):
    value = event.get("seq")
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback


def attempts(events):
    """attempt_id → 归一执行记录（仅 FINISH；BEGIN 提供 intended_action）。"""
    begins, records = {}, {}
    for index, event in enumerate(events or [], 1):
        if not isinstance(event, dict):
            continue
        attempt_id = event.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id:
            continue
        seq = _seq(event, index)
        if event.get("kind") == "BEGIN":
            begins[attempt_id] = {"case_id": event.get("case_id"),
                                  "intended_action": event.get("intended_action"),
                                  "seq": seq}
        elif event.get("kind") == "FINISH":
            begin = begins.get(attempt_id) or {}
            evidence = event.get("evidence") or []
            hashes = sorted({item.get("sha256") for item in evidence
                             if isinstance(item, dict)
                             and isinstance(item.get("sha256"), str)})
            records[attempt_id] = {
                "attempt_id": attempt_id,
                "case_id": event.get("case_id") or begin.get("case_id"),
                "intended_action": begin.get("intended_action"),
                "surf_id": event.get("surf_id"),
                "method": event.get("method"),
                "status": event.get("status"),
                "oracle_source": event.get("oracle_source"),
                "oracle_channel": event.get("oracle_channel"),
                "evidence_sha256": hashes,
                "seq": seq,
            }
    return records


def signature(record):
    """执行签名：同一 case × 同一风险面只算一次执行。

    `intended_action` 不进签名——它是 BEGIN 时手填的自由文本，换个措辞就能把同一格
    包装成「新执行」，与 B′ 反标签操纵的初衷冲突。动作文本仍原样留在台账与披露项里。
    """
    return (record.get("case_id") or "", record.get("surf_id") or "")


def plan_surface_ids(plan):
    """adversarial-plan 的 surfaces → 稳定 ID 集合；无 surfaces 字段返回 None。"""
    surfaces = (plan or {}).get("surfaces")
    if surfaces is None:
        return None
    if not isinstance(surfaces, list):
        raise ValueError("plan.surfaces 必须为数组")
    return {str(item.get("id")) for item in surfaces
            if isinstance(item, dict) and item.get("id")}


def round_delta(events, plan_surfaces=None, since_seq=0):
    """按水位线统计一轮增量；不改输入、不读 exploration.json。"""
    since = int(since_seq or 0)
    records = attempts(events)
    prior = [record for record in records.values() if record["seq"] <= since]
    prior_ids = {record["attempt_id"] for record in prior}
    prior_signatures = {signature(record) for record in prior}
    prior_evidence = {digest for record in prior for digest in record["evidence_sha256"]}

    # 同一签名在本轮内也只算一次：两条同 case×surf×动作的 FINISH 是重放，
    # 不能因为其中一条的截图哈希不同就变成两次「新增」。
    new, replays, seen_signatures = [], [], set(prior_signatures)
    for record in sorted(records.values(), key=lambda row: row["seq"]):
        if record["seq"] <= since or record["attempt_id"] in prior_ids:
            continue
        if record["status"] not in EXECUTED_STATUSES:
            continue
        token = signature(record)
        if token in seen_signatures:
            replays.append(record)
            continue
        seen_signatures.add(token)
        new.append(record)

    # 披露项：证据哈希全部在历史出现过的新执行（重跑同一探针），不据此排除。
    evidence_reused = sum(
        1 for record in new
        if record["evidence_sha256"]
        and not (set(record["evidence_sha256"]) - prior_evidence))
    independent = [record for record in new
                   if record.get("oracle_source") == "independent"
                   and record.get("oracle_channel")
                   and record["evidence_sha256"]]
    unregistered = []
    if plan_surfaces is not None:
        allowed = {str(surface) for surface in plan_surfaces}
        unregistered = [record["attempt_id"] for record in new
                        if record.get("surf_id")
                        and str(record["surf_id"]) not in allowed]
    blocked = [record for record in records.values()
               if record["seq"] > since and record["status"] == "BLOCKED"]
    through = max([_seq(event, index) for index, event in enumerate(events or [], 1)]
                  or [since])
    # 检出披露（G-C）：终极目标是发现 bug，每轮新增 FAIL 数必须可见；
    # 纯披露、不作门禁与停机判据。
    fails = [record for record in new if record["status"] == "FAIL"]
    new_case_ids = sorted({str(record["case_id"]) for record in new
                           if record.get("case_id")})
    new_surf_ids = sorted({str(record["surf_id"]) for record in new
                           if record.get("surf_id")})
    method_counts = {}
    for record in new:
        method = str(record.get("method") or "unspecified")
        method_counts[method] = method_counts.get(method, 0) + 1
    return {
        "since_seq": since,
        "through_seq": through,
        "new_executions": len(new),
        "new_attempt_ids": [record["attempt_id"] for record in new],
        "new_fail_count": len(fails),
        "fail_attempt_ids": [record["attempt_id"] for record in fails],
        "oracle_executions": len(independent),
        "oracle_attempt_ids": [record["attempt_id"] for record in independent],
        "evidence_reused": evidence_reused,
        "replay_not_counted": len(replays),
        "blocked_not_counted": len(blocked),
        "unregistered_expansion": unregistered,
        # 覆盖扩张披露（不作门禁）：只有执行数没有「新增了哪些格」时，
        # 无法回答循环到底扩大了覆盖还是只在原地重跑。
        "new_case_ids": new_case_ids,
        "new_surf_ids": new_surf_ids,
        "method_counts": method_counts,
    }


def scan_loop_products(records):
    """循环产物合规检查：顶层与任意嵌套层出现判定字段即违规（含 accepted）。"""
    violations = []
    for index, record in enumerate(records or []):
        if not isinstance(record, dict):
            violations.append({"index": index, "code": "not_object",
                               "message": "循环候选必须是对象"})
            continue
        ident = record.get("id") or f"#{index}"
        stack, seen = [(record, "")], set()
        while stack:
            current, path = stack.pop()
            if not isinstance(current, (dict, list)):
                continue
            if id(current) in seen:
                continue
            seen.add(id(current))
            if isinstance(current, dict):
                forbidden = LOOP_FORBIDDEN_KEYS if not path else LOOP_NESTED_FORBIDDEN_KEYS
                for key, value in current.items():
                    child = f"{path}.{key}" if path else str(key)
                    if str(key).lower() in forbidden:
                        violations.append({
                            "index": index, "id": ident, "code": "judgement_key",
                            "key": child,
                            "message": f"循环产物不得包含判定字段 {child}；"
                                       "循环内只记现象、引用与证据，判定属裁决阶段"})
                    stack.append((value, child))
            else:
                for position, item in enumerate(current):
                    stack.append((item, f"{path}[{position}]"))
    return violations
