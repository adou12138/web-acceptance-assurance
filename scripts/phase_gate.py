"""Three-phase execution gate: plan -> execute -> adjudicate, with human approval between phases.

设计原则：
1. 阶段推进只认状态机，不认“我觉得可以了”。每阶段开始前必须有用户显式放行记录
   （放行人 + 时间 + 依据），缺一项即拒绝开始。
2. 采集阶段只许记录观察，不许下判定：记录里出现判定字段或 PASS/FAIL 类枚举值时拒绝写入。
   这条把“采集方只负责点、填、读，不负责判定”落成机械约束，而不是口头约定。
3. 裁决阶段只许依据实际观察下判定：无证据引用、依据来源非 observed_*、
   或声明“由脚本补全页面行为”时拒绝。这是诚实性规则的机械投影。
4. 模型路由以“声明 + 核对”实现：每阶段必须声明实际执行模型类别，与路由表不符时
   结论上限压“待核实”，除非显式记录误配理由。本脚本不切换模型，只核对声明。
5. 本闸门只约束阶段顺序与记录合规，不产生“通过/不通过”业务结论；
   技术结论仍由 execution_ledger / adversarial_gate 计算并按最严取齐。
"""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import sys

SCHEMA = "phase-state/1"
PHASES = ("plan", "execute", "adjudicate")
NEXT_PHASE = {"plan": "execute", "execute": "adjudicate", "adjudicate": None}
PHASE_LABELS = {"plan": "规划与用例生成", "execute": "批量执行与状态采集",
                "adjudicate": "结果裁决与缺陷分类"}

# 路由表：类别（class）是机械可核对项，model_hint 只是建议，不作判据。
ROUTING = {
    "plan": {"model_class": "strong-reasoning",
             "model_hint": "GPT-6.1-Sol medium",
             "rationale": "规划错误会在后续被放大：意图覆盖、风险分级、场景设计的质量由强推理模型承担"},
    "execute": {"model_class": "fast",
                "model_hint": "GLM-5.3-Flash high",
                "rationale": "点击/填写/读 DOM 是标准化动作，只采集不判定；高速低成本模型足以胜任"},
    "adjudicate": {"model_class": "strong-reasoning",
                   "model_hint": "GPT-6.1-Sol medium",
                   "rationale": "区分产品缺陷/环境故障/测试步骤问题需要强推理，且需严格执行诚实性规则"},
}

STATUSES = {"pending", "awaiting_approval", "approved", "in_progress", "done", "rejected"}

# 采集阶段禁入的判定类键名（出现即视为越界判定）
COLLECTION_FORBIDDEN_KEYS = (
    "verdict", "verdicts", "judgement", "judgment", "conclusion", "severity",
    "defect", "defects", "classification", "root_cause", "pass_fail",
)
COLLECTION_STATE_ALLOWED = {"collected", "blocked", "not_run", "skipped", "partial"}
JUDGEMENT_TOKENS = {"PASS", "FAIL", "通过", "不通过", "已通过", "判定通过", "判定失败"}

# 裁决阶段的合法观测依据
OBSERVATION_BASES = {
    "observed_ui", "observed_dom", "observed_network", "observed_console",
    "observed_screenshot", "observed_api_response",
}
# 禁止的依据来源（推断、脚本补全、伪造）
FORBIDDEN_SOURCES = {"assumed", "inferred_from_design", "script_inferred", "fabricated",
                     "script_completed", "expected_only"}
CLASSIFICATIONS = {"product_defect", "environment_failure", "test_step_error",
                   "requirement_issue", "not_a_defect"}

# 智能体身份不得充当放行人：自授放行等于把人工闸门变成自动放行
AGENT_IDENTITIES = {"agent", "assistant", "bot", "system", "ai", "expert",
                    "砺衡", "助手", "智能体"}


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _entry_hash(prev, payload):
    return hashlib.sha256((prev + _canonical(payload)).encode("utf-8")).hexdigest()


def _last_hash(state):
    history = state.get("history") or []
    return history[-1]["hash"] if history else ""


def _state_digest(state):
    body = {"phases": state["phases"], "amendments": state["amendments"],
            "history_tail": _last_hash(state)}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def verify_chain(state):
    """校验历史哈希链与状态摘要：手工改写能被检出，而不是被默默接受。"""
    prev = ""
    for index, entry in enumerate(state.get("history") or []):
        payload = {key: entry.get(key) for key in ("at", "action", "phase", "detail")}
        if entry.get("seq") != index or entry.get("prev", "") != prev:
            return False, f"history[{index}] 链断裂：seq/prev 不匹配"
        if entry.get("hash") != _entry_hash(prev, payload):
            return False, f"history[{index}] 内容与哈希不符"
        prev = entry["hash"]
    digest = state.get("digest")
    if digest and digest != _state_digest(state):
        return False, "状态摘要不符：phases/amendments 疑被手工改写，放行记录不可信"
    return True, ""


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _phase(phase):
    if phase not in PHASES:
        raise ValueError(f"unknown phase: {phase} (expected one of {list(PHASES)})")
    return phase


def _record(state, action, phase=None, detail=None):
    payload = {"at": _now(), "action": action, "phase": phase, "detail": detail or {}}
    prev = _last_hash(state)
    state["history"].append(dict(payload, seq=len(state["history"]), prev=prev,
                                 hash=_entry_hash(prev, payload)))
    state["updated_at"] = payload["at"]


def new_state(task):
    task = _text(task, "task")
    return {
        "schema": SCHEMA,
        "task": task,
        "created_at": _now(),
        "updated_at": _now(),
        "phases": {
            phase: {"status": "pending", "artifacts": [], "routing": None,
                    "submitted_at": None, "approval": None, "started_at": None,
                    "completed_at": None, "rejections": []}
            for phase in PHASES
        },
        "amendments": [],
        "history": [],
    }


def init_state(path, task):
    path = Path(path)
    if path.exists():
        raise ValueError(f"phase state already exists: {path}")
    if not path.parent.is_dir():
        raise ValueError("phase state parent directory must already exist")
    state = new_state(task)
    _record(state, "init", None, {"task": task})
    save(path, state)
    return state


def load(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"phase state not found: {path}")
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("schema") != SCHEMA:
        raise ValueError(f"unsupported phase state schema: {state.get('schema')}")
    for phase in PHASES:
        if phase not in (state.get("phases") or {}):
            raise ValueError(f"phase state missing phase: {phase}")
    state.setdefault("amendments", [])
    state.setdefault("history", [])
    ok, why = verify_chain(state)
    if not ok:
        raise ValueError(f"阶段状态不可信：{why}；须由用户重新放行，不得沿用被改写的记录")
    return state


def save(path, state):
    state["digest"] = _state_digest(state)
    Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")


def _entry(state, phase):
    _phase(phase)
    return state["phases"][phase]


def _prior_done(state, phase):
    """前一阶段是否已 done（plan 无前置）。"""
    index = PHASES.index(phase)
    if index == 0:
        return True, None
    prior = PHASES[index - 1]
    if state["phases"][prior]["status"] != "done":
        return False, f"前一阶段 {prior}（{PHASE_LABELS[prior]}）未完成：当前状态 {state['phases'][prior]['status']}"
    return True, None


def submit(state, phase, artifacts=None, model_class=None, model_name=None, note=None):
    """阶段产物就绪，提交放行申请。"""
    entry = _entry(state, phase)
    if entry["status"] not in {"pending", "rejected"}:
        raise ValueError(f"phase {phase} cannot be submitted from status {entry['status']}")
    ok, why = _prior_done(state, phase)
    if not ok:
        raise ValueError(why)
    artifacts = artifacts or []
    for item in artifacts:
        _text(item, "artifact")
    entry["artifacts"] = artifacts
    entry["submitted_at"] = _now()
    entry["routing"] = {
        "declared_class": (_text(model_class, "model_class").lower() if model_class else None),
        "declared_model": model_name.strip() if isinstance(model_name, str) and model_name.strip() else None,
        "expected_class": ROUTING[phase]["model_class"],
        "model_hint": ROUTING[phase]["model_hint"],
        "ack": None,
    }
    entry["status"] = "awaiting_approval"
    _record(state, "submit", phase, {"artifacts": artifacts,
                                     "declared_class": entry["routing"]["declared_class"],
                                     "declared_model": entry["routing"]["declared_model"],
                                     "note": note})
    return state


def _approver(by):
    """放行人校验：必须是人工身份，智能体不得自授放行。"""
    name = _text(by, "by")
    if name.lower() in AGENT_IDENTITIES:
        raise ValueError(f"放行人不得为智能体身份 {name!r}：阶段放行必须由用户确认，"
                         "自授放行等同于取消人工闸门")
    return name


def approve(state, phase, by, note):
    """用户放行：允许开始该阶段。放行人与依据缺一不可。"""
    entry = _entry(state, phase)
    if entry["status"] != "awaiting_approval":
        raise ValueError(f"phase {phase} is not awaiting approval (status={entry['status']})")
    ok, why = _prior_done(state, phase)
    if not ok:
        raise ValueError(why)
    approval = {"by": _approver(by), "at": _now(), "note": _text(note, "note")}
    entry["approval"] = approval
    entry["status"] = "approved"
    _record(state, "approve", phase, approval)
    return state


def reject(state, phase, by, reason):
    entry = _entry(state, phase)
    if entry["status"] != "awaiting_approval":
        raise ValueError(f"phase {phase} is not awaiting approval (status={entry['status']})")
    record = {"by": _text(by, "by"), "at": _now(), "reason": _text(reason, "reason")}
    entry["rejections"].append(record)
    entry["status"] = "rejected"
    _record(state, "reject", phase, record)
    return state


def start(state, phase):
    """开始阶段：必须已获放行，且前一阶段已 done。"""
    entry = _entry(state, phase)
    if entry["status"] != "approved":
        raise ValueError(f"phase {phase} not approved for start (status={entry['status']})；"
                         "阶段推进须用户显式放行，不得自行开始")
    ok, why = _prior_done(state, phase)
    if not ok:
        raise ValueError(why)
    if any(a["status"] == "awaiting_approval" for a in state["amendments"]):
        raise ValueError("存在未批准的阶段修订申请：修订批准前不得开始或继续阶段")
    entry["status"] = "in_progress"
    entry["started_at"] = _now()
    _record(state, "start", phase)
    return state


def complete(state, phase, note=None):
    entry = _entry(state, phase)
    if entry["status"] != "in_progress":
        raise ValueError(f"phase {phase} is not in progress (status={entry['status']})")
    entry["status"] = "done"
    entry["completed_at"] = _now()
    _record(state, "complete", phase, {"note": note})
    return state


def request_amendment(state, phase, by, note):
    """阶段中发现的计划缺口：登记修订申请，批准前不得给出裁决。"""
    _phase(phase)
    record = {"id": f"AMD-{len(state['amendments']) + 1}", "phase": phase,
              "status": "awaiting_approval", "requested_by": _text(by, "by"),
              "requested_at": _now(), "note": _text(note, "note"), "approval": None}
    state["amendments"].append(record)
    _record(state, "amendment_request", phase, {"id": record["id"], "note": record["note"]})
    return state


def approve_amendment(state, amendment_id, by, note):
    for item in state["amendments"]:
        if item["id"] == amendment_id:
            if item["status"] != "awaiting_approval":
                raise ValueError(f"amendment {amendment_id} is not awaiting approval")
            item["status"] = "approved"
            item["approval"] = {"by": _approver(by), "at": _now(), "note": _text(note, "note")}
            _record(state, "amendment_approve", item["phase"],
                    {"id": amendment_id, "note": note})
            return state
    raise ValueError(f"unknown amendment: {amendment_id}")


def acknowledge_routing(state, phase, note):
    """路由误配的显式确认（记录理由后才不压上限）。"""
    entry = _entry(state, phase)
    if not entry.get("routing"):
        raise ValueError(f"phase {phase} has no declared routing to acknowledge")
    entry["routing"]["ack"] = {"at": _now(), "note": _text(note, "note")}
    _record(state, "routing_ack", phase, {"note": note})
    return state


def _routing_warnings(state):
    warnings = []
    for phase in PHASES:
        routing = state["phases"][phase].get("routing")
        if not routing:
            continue
        declared = routing.get("declared_class")
        expected = routing.get("expected_class")
        if not declared:
            warnings.append({"phase": phase, "code": "routing_undeclared",
                             "message": f"{phase} 未声明实际执行模型类别，无法核对路由"
                                        f"（应路由 {expected} / {routing.get('model_hint')}）"})
        elif declared != expected:
            acked = bool(routing.get("ack"))
            warnings.append({"phase": phase,
                             "code": "routing_mismatch" if not acked else "routing_mismatch_acknowledged",
                             "declared": declared, "expected": expected,
                             "message": f"{phase} 声明模型类别 {declared} 与路由表 {expected} 不符"
                                        + ("（已记录误配理由）" if acked else "")})
    return warnings


def check_verdict(state):
    """裁决前置检查：返回是否允许给技术结论，以及阶段层面的结论上限。"""
    reasons, warnings = [], []
    for phase in PHASES:
        status = state["phases"][phase]["status"]
        if status != "done":
            reasons.append(f"{phase}（{PHASE_LABELS[phase]}）状态为 {status}，未完成")
    for amendment in state["amendments"]:
        if amendment["status"] == "awaiting_approval":
            reasons.append(f"阶段修订申请 {amendment['id']} 未获批准：{amendment['note']}")
    routing_warnings = _routing_warnings(state)
    warnings.extend(routing_warnings)
    if any(w["code"] == "routing_mismatch" for w in routing_warnings):
        reasons.append("存在未确认的模型路由误配：阶段质量前提不成立，结论上限压待核实")
    allowed = not reasons
    return {
        "schema": "phase-verdict-check/1",
        "task": state.get("task"),
        "verdict_allowed": allowed,
        "phase_ceiling": None if allowed else "待核实",
        "reasons": reasons,
        "warnings": warnings,
        "phases": {phase: {"label": PHASE_LABELS[phase],
                           "status": state["phases"][phase]["status"],
                           "approval": state["phases"][phase].get("approval"),
                           "routing": state["phases"][phase].get("routing")}
                   for phase in PHASES},
        "next_action": _next_action(state),
    }


def _next_action(state):
    for phase in PHASES:
        entry = state["phases"][phase]
        status = entry["status"]
        if status == "pending":
            return f"提交阶段 {phase}（{PHASE_LABELS[phase]}）产物并申请放行"
        if status == "rejected":
            return f"阶段 {phase} 被驳回：按驳回意见修订后重新 submit"
        if status == "awaiting_approval":
            return f"等待用户放行阶段 {phase}（{PHASE_LABELS[phase]}）——未放行不得开始"
        if status == "approved":
            return f"已获放行，可开始阶段 {phase}（{PHASE_LABELS[phase]}）"
        if status == "in_progress":
            return f"阶段 {phase}（{PHASE_LABELS[phase]}）进行中，完成后 complete 并申请下一阶段放行"
    return "三阶段均已完成，可按台账口径汇总结论"


def scan_collection_records(records):
    """采集阶段记录合规检查：只许观察，不许判定。"""
    violations = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            violations.append({"index": index, "code": "not_object",
                               "message": "记录必须是对象"})
            continue
        ident = record.get("id") or f"#{index}"
        for key in record:
            if str(key).lower() in COLLECTION_FORBIDDEN_KEYS:
                violations.append({"index": index, "id": ident, "code": "judgement_key",
                                   "key": key,
                                   "message": f"采集阶段记录不得包含判定字段 {key}，判定属裁决阶段"})
        state_value = record.get("collection_state")
        if state_value is not None:
            text = str(state_value)
            if text in JUDGEMENT_TOKENS:
                violations.append({"index": index, "id": ident, "code": "judgement_state",
                                   "value": text,
                                   "message": f"采集状态不得为 {text}；合法值为 {sorted(COLLECTION_STATE_ALLOWED)}"})
            elif text not in COLLECTION_STATE_ALLOWED:
                violations.append({"index": index, "id": ident, "code": "unknown_state",
                                   "value": text,
                                   "message": f"未知采集状态 {text}；合法值为 {sorted(COLLECTION_STATE_ALLOWED)}"})
        else:
            violations.append({"index": index, "id": ident, "code": "missing_state",
                               "message": "缺少 collection_state（collected/blocked/not_run/skipped/partial）"})
        if record.get("collection_state") == "collected" and not record.get("evidence_paths"):
            violations.append({"index": index, "id": ident, "code": "no_evidence",
                               "message": "采集完成的记录必须附 evidence_paths，否则无法在裁决阶段复核"})
    return violations


def check_adjudications(findings):
    """裁决阶段合规检查：诚实性规则的机械投影。"""
    violations = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            violations.append({"index": index, "code": "not_object",
                               "message": "裁决条目必须是对象"})
            continue
        ident = finding.get("id") or f"#{index}"
        classification = finding.get("classification")
        if classification not in CLASSIFICATIONS:
            violations.append({"index": index, "id": ident, "code": "bad_classification",
                               "value": classification,
                               "message": f"分类必须是 {sorted(CLASSIFICATIONS)} 之一"})
        refs = finding.get("evidence_refs") or []
        if not isinstance(refs, list) or not [r for r in refs if str(r).strip()]:
            violations.append({"index": index, "id": ident, "code": "no_evidence_refs",
                               "message": "裁决必须给出 evidence_refs（指向实际观察记录/截图/日志）"})
        basis = finding.get("basis") or []
        basis_list = [str(b) for b in basis] if isinstance(basis, list) else [str(basis)]
        if not set(basis_list) & OBSERVATION_BASES:
            violations.append({"index": index, "id": ident, "code": "no_observed_basis",
                               "message": "裁决依据必须包含至少一项 observed_*（实际观察），"
                                          f"不得仅凭 {basis_list or '无依据'} 推断"})
        source = str(finding.get("source") or "").lower()
        if source in FORBIDDEN_SOURCES:
            violations.append({"index": index, "id": ident, "code": "forbidden_source",
                               "value": source,
                               "message": "禁止使用推断/脚本补全/伪造类依据来源"})
        if finding.get("page_behavior_completed_by_script"):
            violations.append({"index": index, "id": ident, "code": "script_completed_behavior",
                               "message": "禁止用脚本补全页面行为后判定；页面未呈现的状态不得计为通过"})
        grade = finding.get("evidence_grade")
        if classification == "product_defect":
            if grade and grade != "E1":
                violations.append({"index": index, "id": ident, "code": "defect_grade_too_low",
                                   "value": grade,
                                   "message": "产品缺陷须为 E1（真实执行）证据；E2/E3 只能记待核实"})
            if finding.get("page_observed") is not True:
                violations.append({"index": index, "id": ident, "code": "defect_not_observed",
                                   "message": "判为产品缺陷须 page_observed=true（页面实际呈现）"})
    return violations


def status(state):
    return {
        "schema": SCHEMA,
        "task": state.get("task"),
        "phases": {phase: {"label": PHASE_LABELS[phase],
                           "status": state["phases"][phase]["status"],
                           "artifacts": state["phases"][phase].get("artifacts"),
                           "routing": state["phases"][phase].get("routing"),
                           "approval": state["phases"][phase].get("approval")}
                   for phase in PHASES},
        "amendments": state["amendments"],
        "routing_warnings": _routing_warnings(state),
        "next_action": _next_action(state),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="初始化阶段状态机")
    p.add_argument("state")
    p.add_argument("--task", required=True)

    p = sub.add_parser("submit", help="提交阶段产物并申请放行")
    p.add_argument("state")
    p.add_argument("--phase", required=True)
    p.add_argument("--artifact", action="append", default=[])
    p.add_argument("--model-class", help="实际执行模型类别：strong-reasoning / fast")
    p.add_argument("--model-name", help="实际模型名（留痕用）")
    p.add_argument("--note")

    p = sub.add_parser("approve", help="用户放行阶段")
    p.add_argument("state")
    p.add_argument("--phase", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--note", required=True)

    p = sub.add_parser("reject", help="用户驳回阶段")
    p.add_argument("state")
    p.add_argument("--phase", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--reason", required=True)

    p = sub.add_parser("start", help="开始已放行的阶段")
    p.add_argument("state")
    p.add_argument("--phase", required=True)

    p = sub.add_parser("complete", help="完成进行中的阶段")
    p.add_argument("state")
    p.add_argument("--phase", required=True)
    p.add_argument("--note")

    p = sub.add_parser("amend", help="登记计划修订申请")
    p.add_argument("state")
    p.add_argument("--phase", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--note", required=True)

    p = sub.add_parser("approve-amendment", help="放行计划修订申请")
    p.add_argument("state")
    p.add_argument("--id", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--note", required=True)

    p = sub.add_parser("ack-routing", help="确认模型路由误配理由")
    p.add_argument("state")
    p.add_argument("--phase", required=True)
    p.add_argument("--note", required=True)

    p = sub.add_parser("status", help="查看阶段状态与下一步动作")
    p.add_argument("state")

    p = sub.add_parser("check-verdict", help="裁决前置检查（未通过则结论上限待核实）")
    p.add_argument("state")

    p = sub.add_parser("scan-collection", help="采集记录合规检查（禁判定字段）")
    p.add_argument("records", help="JSONL 或 JSON 数组文件")
    p.add_argument("--json", action="store_true", help="以 JSON 输出")

    p = sub.add_parser("check-adjudication", help="裁决条目合规检查（诚实性规则）")
    p.add_argument("findings")

    args = parser.parse_args(argv)
    try:
        if args.command == "scan-collection":
            records = _load_records(args.records)
            violations = scan_collection_records(records)
            print(json.dumps({"violations": violations, "count": len(records),
                              "ok": not violations}, ensure_ascii=False, indent=2))
            return 0 if not violations else 1
        if args.command == "check-adjudication":
            findings = _load_records(args.findings)
            violations = check_adjudications(findings)
            print(json.dumps({"violations": violations, "count": len(findings),
                              "ok": not violations}, ensure_ascii=False, indent=2))
            return 0 if not violations else 1

        if args.command == "init":
            result = init_state(args.state, args.task)
            print(json.dumps(status(result), ensure_ascii=False, indent=2))
            return 0

        state = load(args.state)
        if args.command == "submit":
            submit(state, args.phase, args.artifact, args.model_class, args.model_name, args.note)
        elif args.command == "approve":
            approve(state, args.phase, args.by, args.note)
        elif args.command == "reject":
            reject(state, args.phase, args.by, args.reason)
        elif args.command == "start":
            start(state, args.phase)
        elif args.command == "complete":
            complete(state, args.phase, args.note)
        elif args.command == "amend":
            request_amendment(state, args.phase, args.by, args.note)
        elif args.command == "approve-amendment":
            approve_amendment(state, args.id, args.by, args.note)
        elif args.command == "ack-routing":
            acknowledge_routing(state, args.phase, args.note)
        if args.command != "status":
            save(args.state, state)
        if args.command == "check-verdict":
            check = check_verdict(state)
            print(json.dumps(check, ensure_ascii=False, indent=2))
            return 0 if check["verdict_allowed"] else 1
        print(json.dumps(status(state), ensure_ascii=False, indent=2))
        return 0
    except ValueError as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


def _load_records(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"file not found: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("JSON 输入必须是数组")
        return data
    return [json.loads(line) for line in text.splitlines() if line.strip()]


if __name__ == "__main__":
    sys.exit(main())
