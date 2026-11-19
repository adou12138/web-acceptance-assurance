"""Single-writer execution journal and resume summary; never executes tests or grants release."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import argparse

try:
    from case_data import validate as validate_design
except ImportError:
    import importlib.util
    _spec = importlib.util.spec_from_file_location("case_data", Path(__file__).with_name("case_data.py"))
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    validate_design = _module.validate

STATUSES = {"PASS", "FAIL", "BLOCKED", "NOT_RUN", "SKIPPED"}
SEVERITIES = {"blocker", "major", "normal", "minor"}
BUG_STATES = {"open", "in_progress", "fixed_pending_deploy", "fixed_pending_retest", "reopened", "closed"}
CAUSES = {"none", "product", "environment", "data", "automation", "unknown"}
# 证据等级门禁（对齐 blind_eval 的 grade != "E1" 不计检出）：
# result.json 可选字段 evidence_grade（E1/E2/E3）。未提供时完全兼容旧格式（不校验）；
# 提供时 PASS 必须携带 E1 证据，E2/E3 不得记 PASS。
EVIDENCE_GRADES = {"E1", "E2", "E3"}


try:
    from design_fingerprint import fingerprint as _fingerprint
except ImportError:
    import importlib.util as _iu
    _fp_spec = _iu.spec_from_file_location("design_fingerprint", Path(__file__).with_name("design_fingerprint.py"))
    _fp_module = _iu.module_from_spec(_fp_spec)
    _fp_spec.loader.exec_module(_fp_module)
    _fingerprint = _fp_module.fingerprint


def fingerprint(value):
    """内容指纹统一走 scripts/design_fingerprint.py，与 gate_checks 的 binding 同源。"""
    return _fingerprint(value)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}: nonempty string required")


def _bugs(value):
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("bug ledger must be an array or unknown (null)")
    ids = set()
    for bug in value:
        if not isinstance(bug, dict):
            raise ValueError("bug must be object")
        _text(bug.get("id"), "bug.id")
        if bug["id"] in ids or bug.get("severity") not in SEVERITIES or bug.get("status") not in BUG_STATES:
            raise ValueError("duplicate/invalid bug ID, severity or status")
        for key in ("source", "external_id"):
            if key in bug:
                _text(bug[key], "bug." + key)
        ids.add(bug["id"])
    return value


def _context(value):
    if not isinstance(value, dict):
        raise ValueError("context must be object")
    for key in ("target_url", "environment", "application_version", "dependency_mode"):
        _text(value.get(key), key)
    if value["dependency_mode"] not in {"real", "sandbox", "mock", "replay"}:
        raise ValueError("invalid context dependency_mode")
    if not isinstance(value.get("feature_flags"), dict):
        raise ValueError("feature_flags must be object")
    if not isinstance(value.get("role_scope"), list) or not value["role_scope"] or any(not isinstance(x, str) or not x.strip() for x in value["role_scope"]):
        raise ValueError("role_scope must be nonempty string array")
    return value


def _design(value):
    errors, warnings = validate_design(value)
    if errors:
        hint = ""
        if not isinstance(value, dict) or not isinstance(value.get("testcases"), list):
            hint = "；若用例来自 qa-req2testcase-generator，请改用砺衡 case-generator 重新生成（本台账只接受 case_data schema 1.0）"
        raise ValueError("invalid case design: " + "; ".join(errors) + hint)
    return {case["id"]: case for case in value["testcases"]}, warnings


def _write_json_new(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


REQUIRED_GATE_CHECKS = ("S0",)
CHECK_STATUSES = {"passed", "warning", "failed"}
# 门禁报告内容绑定要求（v3 目标审计 G09）：报告必须携带 binding 声明所依据的
# design_hash / policy_version / scope / required_checks，否则保持“待核实”，
# 不得凭“status=pass + 用例数相同”推定是同一份设计。旧报告按兼容模式显式标注。
BINDING_FIELDS = ("design_hash", "policy_version", "scope", "required_checks")


def _adversarial_precondition(adversarial_gate, waiver=None, read_only=False, case_count=None):
    """对抗门禁进门条件：记录对抗结论并压结论上限（不拒绝建账，保持旧行为兼容）。

    豁免不得自授即通过：waiver 只能把上限从"待核实"降到"待核实（已豁免）"，
    且必须同时满足可机械核验的条件（read_only=True 且 case_count 不超阈），
    否则即使给了 waiver 也保持"待核实"——避免任意理由字符串跳过全部对抗。

    返回值写入 baseline.gate_precondition，由 summarize 机械压制 verdict_ceiling：
    - 提供 adversarial_gate 且 adversarial_enabled=true → 采用其 verdict_ceiling；
    - 提供但 adversarial_enabled=false（空 plan）→ 无豁免待核实；有合资格豁免则标 waived；
    - 未提供 → 无豁免待核实；有合资格豁免标 waived。
    """
    def _valid_waiver(reason):
        """豁免必须挂可机械核对的依据：只读且规模小。返回 (ok, 说明)。"""
        if not (reason and str(reason).strip()):
            return False, "未提供豁免理由"
        if not read_only:
            return False, "本轮含写操作（read_only=False）：不允许通过豁免跳过对抗"
        if case_count is None or case_count > WAIVER_MAX_CASES:
            return False, (f"本轮用例数 {case_count} 超豁免阈 {WAIVER_MAX_CASES}："
                           f"豁免仅限纯只读小任务，大任务必须执行对抗")
        return True, ""

    reason = str(waiver).strip() if waiver else ""
    ok, why = _valid_waiver(reason)
    if adversarial_gate is None:
        if ok:
            return {"adversarial_state": "waived", "adversarial_ceiling": "待核实",
                    "adversarial_waiver": reason,
                    "adversarial_reasons": [f"豁免成立（只读且 {case_count} 条 ≤ {WAIVER_MAX_CASES}）：{reason}；"
                                            "但未执行对抗，结论上限仍为待核实，不得写通过"]}
        return {"adversarial_state": "missing", "adversarial_ceiling": "待核实",
                "adversarial_reasons": ["未提供对抗门禁结论：对抗循环为产出技术结论批次的必选项；"
                                        "请以 --enforce 运行 scripts/adversarial_gate.py 并传入 --adversarial-gate"
                                        + (f"（豁免不成立：{why}）" if reason else "")]}
    if not isinstance(adversarial_gate, dict):
        raise ValueError("adversarial gate must be a JSON object（adversarial_gate.py 的结论输出）")
    ceiling = adversarial_gate.get("verdict_ceiling")
    if ceiling not in {"通过", "待核实", "不通过"}:
        raise ValueError("adversarial gate 缺 verdict_ceiling（需为 通过/待核实/不通过）")
    enabled = adversarial_gate.get("adversarial_enabled")
    if enabled is None:
        raise ValueError("adversarial gate 缺 adversarial_enabled 标记")
    reasons = adversarial_gate.get("reasons")
    if not isinstance(reasons, list):
        raise ValueError("adversarial gate 缺 reasons 数组（须原样呈现，不得隐矞原因）")
    info = {"adversarial_state": "verified" if enabled else "not_enabled",
            "adversarial_ceiling": ceiling, "adversarial_enabled": bool(enabled),
            "adversarial_reasons": list(reasons)}
    if not enabled:
        # 空 plan 送到门禁（未 --enforce 直放行）不得当作已完成对抗
        if ok:
            info["adversarial_state"] = "waived"
            info["adversarial_ceiling"] = "待核实"  # 豁免不得抬高到通过
            info["adversarial_waiver"] = reason
            info["adversarial_reasons"] = list(reasons) + [
                f"豁免成立（只读且 {case_count} 条）：{reason}；但对抗未执行，上限仍待核实"]
        else:
            info["adversarial_ceiling"] = "待核实"
            info["adversarial_reasons"] = list(reasons) + [
                "对抗未启用（adversarial_enabled=false）且豁免不成立：结论上限待核实"
                + (f"（{why}）" if reason else "")]
    return info


def _gate_precondition(batches, gate_report, design_ids=None, design=None):
    """校验第 2→3 跳进门条件。design_ids 为用例 ID 集合；design 为完整用例对象（用于内容绑定）。

    两者均为可选，未提供时保持旧行为。只接受与设计绑定的完整载荷：仅有 status 的最小
    JSON、空 checks、覆盖条数不符、未登记所辖用例的空批次都不能解锁执行。
    """
    design_id_set = {str(x) for x in design_ids} if design_ids is not None else None
    info = {}
    if batches is not None:
        if not isinstance(batches, dict) or not isinstance(batches.get("batches"), list) or not batches["batches"]:
            raise ValueError("batches must be a batch state object with a nonempty batches array")
        bad = [str(b.get("unit_id", "?")) for b in batches["batches"]
               if not isinstance(b, dict) or b.get("status") != "done" or b.get("gate") not in {"pass", "waived"}]
        if bad:
            raise ValueError("design gate not closed: " + ", ".join(bad) + "（批次未完成或门禁未通过）")
        unregistered = [str(b.get("unit_id", "?")) for b in batches["batches"]
                        if not [x for x in (b.get("case_ids") or []) if str(x).strip()]]
        if unregistered:
            raise ValueError("batches 未登记所辖用例: " + ", ".join(unregistered) + "（空批次不得计入闭环）")
        covered = {str(x) for b in batches["batches"] for x in b["case_ids"]}
        if design_id_set is not None:
            uncovered, extra = sorted(design_id_set - covered), sorted(covered - design_id_set)
            if uncovered or extra:
                raise ValueError("batches.case_ids 与设计用例不一致：未登记 " + str(uncovered[:10]) + "，设计外 " + str(extra[:10]))
        info["batches_closed"] = True
        info["batches_case_count"] = len(covered)
    if gate_report is not None:
        if not isinstance(gate_report, dict) or gate_report.get("status") not in {"pass", "warning"}:
            raise ValueError("gate report must be pass/warning; fail or missing status blocks execution")
        checks = gate_report.get("checks")
        if not isinstance(checks, list) or not checks:
            raise ValueError("gate report must carry a nonempty checks array（仅有 status 不足以证明门禁已跑）")
        malformed = [str(i) for i, c in enumerate(checks)
                     if not isinstance(c, dict) or not str(c.get("id", "")).strip() or c.get("status") not in CHECK_STATUSES]
        if malformed:
            raise ValueError("gate report checks 不完整（需 id 与 passed/warning/failed）：索引 " + ", ".join(malformed))
        failed = [str(c["id"]) for c in checks if c["status"] == "failed"]
        if failed:
            raise ValueError("gate report has failed checks: " + ", ".join(failed))
        passed_ids = {str(c["id"]) for c in checks if c["status"] == "passed"}
        absent = [gid for gid in REQUIRED_GATE_CHECKS if gid not in passed_ids]
        if absent:
            raise ValueError("gate report 缺少必过检查项: " + ", ".join(absent) + "（结构校验未通过不得执行）")
        counts = gate_report.get("counts")
        if not isinstance(counts, dict) or not isinstance(counts.get("testcases"), int):
            raise ValueError("gate report 缺少 counts.testcases，无法与设计条数绑定")
        if design_id_set is not None and counts["testcases"] != len(design_id_set):
            raise ValueError(f"gate report 覆盖 {counts['testcases']} 条与设计 {len(design_id_set)} 条不一致（可能不是同一份用例）")
        info["gate_report_status"] = gate_report["status"]
        info["gate_report_case_count"] = counts["testcases"]
        # —— 内容绑定校验（G09）——
        binding = gate_report.get("binding")
        if not isinstance(binding, dict):
            info["binding_verdict"] = "待核实"
            info["binding_missing"] = ["binding（缺内容绑定声明，旧报告兼容模式）"]
            info["binding_compat"] = True
        else:
            missing = [f for f in BINDING_FIELDS if f not in binding]
            if missing:
                raise ValueError("gate report binding 缺字段: " + ", ".join(missing))
            expected_hash = fingerprint(design) if design is not None else None
            if expected_hash is not None and binding["design_hash"] != expected_hash:
                raise ValueError("gate report binding.design_hash 与设计内容不一致（不是同一份用例/报告）")
            _rc = binding["required_checks"]
            if isinstance(_rc, list):
                declared_checks = {str(c.get("id")) if isinstance(c, dict) else str(c) for c in _rc}
            else:
                raise ValueError("gate report binding.required_checks 必须为数组")
            if declared_checks != passed_ids:
                # 声明的必过项必须恰好等于实际 passed 集合的子集关系：报告声明的必过项缺一不可
                absent = sorted(declared_checks - passed_ids)
                if absent:
                    raise ValueError("gate report binding 声明必过项未通过: " + ", ".join(absent))
            scope = binding.get("scope")
            if design is not None and not isinstance(scope, list):
                raise ValueError("gate report binding.scope 必须为数组")
            info["binding_verdict"] = "已绑定"
            info["binding_missing"] = []
            info["binding_compat"] = False
            info["binding_policy_version"] = binding["policy_version"]
    return info


WORKSPACE_GITIGNORE = [
    "# 砺衡工作区自动生成（凭据与体积产物不入库；需归档的证据请显式 git add -f）",
    ".auth/",
    "*.storage.json",
    "*.png", "*.jpg", "*.jpeg", "*.webm", "*.har", "*.zip", "*.log",
]


def _ensure_gitignore(workspace):
    """工作区自动写 .gitignore（借鉴 figma init_workdir：只追加缺失行，不覆盖已有内容）。"""
    path = workspace / ".gitignore"
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = list(existing)
    for line in WORKSPACE_GITIGNORE:
        if line not in existing:
            lines.append(line)
    if lines != existing:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def initialize(workspace, design, context, round_id, known_issues=None, batches=None, gate_report=None,
               adversarial_gate=None, adversarial_waiver=None):
    cases, warnings = _design(design)
    _context(context)
    _text(round_id, "round_id")
    _bugs(known_issues)
    gate_precondition = _gate_precondition(batches, gate_report, list(cases), design)
    # 只读豁免条件从 context.dependency_mode 与用例数机械判定，不信理由文本
    read_only = context.get("dependency_mode") in {"replay", "mock"}
    gate_precondition.update(_adversarial_precondition(
        adversarial_gate, adversarial_waiver, read_only=read_only, case_count=len(cases)))
    workspace = Path(workspace)
    if not workspace.parent.is_dir():
        raise ValueError("workspace parent must already exist")
    workspace.mkdir(exist_ok=False)
    baseline = {"schema_version": "1.0", "round_id": round_id, "created_at": _now(), "design_hash": fingerprint(design), "context_hash": fingerprint(context), "context": context, "case_ids": list(cases), "known_issues": known_issues, "design_warnings": warnings, "gate_precondition": gate_precondition}
    _write_json_new(workspace / "cases.snapshot.json", design)
    _write_json_new(workspace / "baseline.json", baseline)
    (workspace / "events.jsonl").touch(exist_ok=False)
    _ensure_gitignore(workspace)
    return baseline


@contextmanager
def _lock(workspace):
    path = Path(workspace) / ".writer.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise ValueError("writer lock exists; inspect prior writer before manual recovery")
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        yield
    finally:
        path.unlink()


def _baseline(workspace):
    workspace = Path(workspace)
    baseline = json.loads((workspace / "baseline.json").read_text(encoding="utf-8"))
    snapshot = json.loads((workspace / "cases.snapshot.json").read_text(encoding="utf-8"))
    if fingerprint(snapshot) != baseline["design_hash"] or fingerprint(baseline["context"]) != baseline["context_hash"]:
        raise ValueError("baseline/snapshot integrity mismatch")
    return baseline


def read_events(workspace):
    path = Path(workspace) / "events.jsonl"
    events, seen = [], set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("event not object")
            digest = event.get("digest")
            content = {k: v for k, v in event.items() if k != "digest"}
            if digest != fingerprint(content):
                raise ValueError("event digest mismatch")
            if event.get("seq") != number or event.get("previous") != (events[-1]["digest"] if events else None):
                raise ValueError("journal chain mismatch")
            key = (event.get("attempt_id"), event.get("kind"))
            if key in seen or event.get("kind") not in {"BEGIN", "FINISH"}:
                raise ValueError("duplicate/invalid journal event")
            seen.add(key)
            events.append(event)
        except Exception as exc:
            raise ValueError(f"journal line {number} invalid: {exc}") from exc
    return events


def _compatible(workspace, design, context):
    cases, warnings = _design(design)
    _context(context)
    baseline = _baseline(workspace)
    same = baseline["design_hash"] == fingerprint(design) and baseline["context_hash"] == fingerprint(context)
    return baseline, cases, warnings, same


def _append(workspace, events, kind, payload):
    for event in events:
        if event["attempt_id"] == payload["attempt_id"] and event["kind"] == kind:
            old = {k: event[k] for k in payload}
            if old != payload:
                raise ValueError("conflicting duplicate attempt event; original retained")
            return event
    content = dict(payload, kind=kind, timestamp=_now(), seq=len(events) + 1, previous=events[-1]["digest"] if events else None)
    event = dict(content, digest=fingerprint(content))
    with (Path(workspace) / "events.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return event


def begin(workspace, design, context, attempt_id, case_id, intended_action):
    _text(attempt_id, "attempt_id")
    _text(intended_action, "intended_action")
    with _lock(workspace):
        baseline, cases, _, same = _compatible(workspace, design, context)
        if not same:
            raise ValueError("stale design/context: start a new round, never reuse old PASS")
        if case_id not in cases:
            raise ValueError("unknown case_id")
        events = read_events(workspace)
        finished = {e["attempt_id"] for e in events if e["kind"] == "FINISH"}
        if any(e["case_id"] == case_id and e["kind"] == "BEGIN" and e["attempt_id"] not in finished and e["attempt_id"] != attempt_id for e in events):
            raise ValueError("unfinished attempt exists; reconcile side effects before retry")
        return _append(workspace, events, "BEGIN", {"attempt_id": attempt_id, "case_id": case_id, "round_id": baseline["round_id"], "intended_action": intended_action})


def finish(workspace, design, context, attempt_id, status, actual, evidence_paths, assertions, cause, bug_ids, cleanup, evidence_grade=None):
    _text(attempt_id, "attempt_id")
    _text(actual, "actual")
    _text(cleanup, "cleanup")
    if status not in STATUSES or status == "NOT_RUN" or cause not in CAUSES:
        raise ValueError("invalid finished status/cause")
    if evidence_grade is not None:
        if evidence_grade not in EVIDENCE_GRADES:
            raise ValueError(f"invalid evidence_grade {evidence_grade!r}: must be one of E1/E2/E3")
        if status == "PASS" and evidence_grade in {"E2", "E3"}:
            raise ValueError(f"evidence_grade={evidence_grade} 与 status=PASS 冲突：PASS 须 E1 证据，E2/E3 请记 BLOCKED 或降级")
    if not isinstance(bug_ids, list) or any(not isinstance(x, str) or not x.strip() for x in bug_ids):
        raise ValueError("bug_ids must be string array")
    if not isinstance(assertions, list) or any(not isinstance(x, dict) or not isinstance(x.get("check"), str) or not x["check"].strip() or "actual" not in x or type(x.get("matched")) is not bool for x in assertions):
        raise ValueError("assertions require check, actual and matched boolean")
    if status == "PASS" and (not assertions or not all(x["matched"] for x in assertions) or cause != "none"):
        raise ValueError("PASS requires satisfied assertions and cause=none")
    if status == "FAIL" and (not assertions or all(x["matched"] for x in assertions)):
        raise ValueError("FAIL requires observed failed business assertion")
    if status == "FAIL" and cause == "product" and not bug_ids:
        raise ValueError("product failure requires linked bug_ids")
    if not isinstance(evidence_paths, list) or any(not isinstance(x, str) for x in evidence_paths):
        raise ValueError("evidence_paths must be string array")
    if status in {"PASS", "FAIL"} and not evidence_paths:
        raise ValueError("PASS/FAIL requires local evidence files")
    evidence = []
    for value in evidence_paths:
        path = Path(value)
        if not path.is_absolute() or not path.is_file():
            raise ValueError("evidence must be existing absolute local file")
        evidence.append({"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    with _lock(workspace):
        baseline, cases, _, same = _compatible(workspace, design, context)
        if not same:
            raise ValueError("stale design/context")
        events = read_events(workspace)
        started = next((e for e in events if e["kind"] == "BEGIN" and e["attempt_id"] == attempt_id), None)
        if not started:
            raise ValueError("BEGIN required before FINISH")
        if status in {"PASS", "FAIL"}:
            expected_checks = {item["check"] for item in cases[started["case_id"]]["assertions"]}
            actual_checks = {item["check"] for item in assertions}
            if expected_checks != actual_checks or len(actual_checks) != len(assertions):
                raise ValueError("result assertions must cover all designed checks exactly once")
        payload = {"attempt_id": attempt_id, "case_id": started["case_id"], "round_id": baseline["round_id"], "status": status, "actual": actual, "evidence": evidence, "assertions": assertions, "cause": cause, "bug_ids": bug_ids, "cleanup": cleanup}
        if evidence_grade is not None:
            payload["evidence_grade"] = evidence_grade
        return _append(workspace, events, "FINISH", payload)


RANK = {"通过": 0, "待核实": 1, "不通过": 2}
# 豁免上限：超过该用例数的批次必须执行对抗，不得凭理由跳过
WAIVER_MAX_CASES = 5


def _strictest(a, b):
    return max((a, b), key=lambda x: RANK.get(x, 1))


def _verdict_ceiling(baseline, counts, executed):
    """机械结论上限：由台账事实与对抗门禁共同限定，不由人工宣称。

    对齐 agent 口径：存在未闭环 blocker（FAIL）→ 不通过；对抗缺失/未启用或
    存在 BLOCKED/未执行 → 待核实；全部必选项已实测且对抗已验证 → 通过。
    """
    gate = (baseline.get("gate_precondition") or {})
    adversarial = gate.get("adversarial_ceiling", "待核实")
    if counts.get("FAIL"):
        return "不通过"
    fact = "待核实" if (counts.get("BLOCKED") or counts.get("NOT_RUN")
                          or counts.get("SKIPPED") or executed == 0) else "通过"
    return _strictest(fact, adversarial)


def summarize(workspace, design, context, bugs=None):
    baseline, cases, warnings, same = _compatible(workspace, design, context)
    bugs = _bugs(bugs)
    events = read_events(workspace)
    outcomes, evidence_issues, ready, reconcile, flaky_cases = {}, [], [], [], []
    executed = 0
    for case_id in cases:
        relevant = [e for e in events if e["case_id"] == case_id]
        completed = [e for e in relevant if e["kind"] == "FINISH"]
        done = {e["attempt_id"] for e in completed}
        pending = [e["attempt_id"] for e in relevant if e["kind"] == "BEGIN" and e["attempt_id"] not in done]
        valid = []
        bad_evidence = False
        if same:
            for event in completed:
                intact = True
                for evidence in event["evidence"]:
                    path = Path(evidence["path"])
                    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != evidence["sha256"]:
                        evidence_issues.append({"case_id": case_id, "attempt_id": event["attempt_id"], "path": str(path)})
                        intact = False
                if intact:
                    valid.append(event)
                else:
                    bad_evidence = True
        attempts = [e for e in valid if e["status"] in {"PASS", "FAIL"}]
        statuses = {e["status"] for e in attempts}
        flaky = statuses == {"PASS", "FAIL"}
        if attempts:
            executed += 1
        product_failure = any(e["status"] == "FAIL" and e["cause"] == "product" for e in valid)
        if not same:
            status = "NOT_RUN"
        elif product_failure:
            status = "FAIL"
        elif pending or bad_evidence or flaky:
            status = "BLOCKED"
        elif any(e["status"] == "FAIL" for e in attempts):
            status = "FAIL"
        elif completed and completed[-1]["status"] in {"BLOCKED", "SKIPPED"}:
            status = completed[-1]["status"]
        elif attempts:
            status = "PASS"
        else:
            status = "NOT_RUN"
        if pending:
            reconcile.append(case_id)
        if flaky:
            flaky_cases.append(case_id)
        if same and status == "NOT_RUN" and not pending:
            ready.append(case_id)
        outcomes[case_id] = {"status": status, "flaky": flaky, "attempt_ids": [e["attempt_id"] for e in completed], "unfinished_attempts": pending}
    counts = {status: sum(o["status"] == status for o in outcomes.values()) for status in sorted(STATUSES)}
    known = _bugs(baseline.get("known_issues"))
    open_bugs = None if bugs is None else {severity: sum(b["severity"] == severity and b["status"] != "closed" for b in bugs) for severity in sorted(SEVERITIES)}
    bug_delta = None
    if known is not None and bugs is not None:
        old_open = {b["id"] for b in known if b["status"] != "closed"}
        current_open = {b["id"] for b in bugs if b["status"] != "closed"}
        all_old = {b["id"] for b in known}
        bug_delta = {"new_ids": sorted(current_open - all_old), "still_open_ids": sorted(current_open & old_open), "closed_ids": sorted(b["id"] for b in bugs if b["id"] in old_open and b["status"] == "closed"), "missing_from_current": sorted(old_open - {b["id"] for b in bugs})}
    linked = {bug_id for event in events if event["kind"] == "FINISH" for bug_id in event["bug_ids"]}
    missing_bugs = None if bugs is None else sorted(linked - {b["id"] for b in bugs})
    return {"round_id": baseline["round_id"], "compatible": same, "baseline": baseline, "current_context": context, "context_changed": not same, "planned": len(cases), "executed": executed, "counts": counts, "execution_rate": executed / len(cases) if cases else None, "pass_rate": counts["PASS"] / executed if executed else None, "outcomes": outcomes, "ready": ready, "needs_reconcile": reconcile, "stale_cases": [] if same else list(cases), "retest_candidates": [c for c, o in outcomes.items() if o["status"] in {"FAIL", "BLOCKED", "SKIPPED"}], "flaky_cases": flaky_cases, "evidence_issues": evidence_issues, "open_bugs": open_bugs, "bug_delta": bug_delta, "missing_bug_ids": missing_bugs, "design_warnings": warnings, "verdict_ceiling": _verdict_ceiling(baseline, counts, executed), "scope": "progress only; no acceptance or release verdict; evidence content requires human/tool review"}


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "begin", "finish", "summary"):
        part = sub.add_parser(command)
        part.add_argument("workspace")
        part.add_argument("design")
        part.add_argument("context")
        if command == "init":
            part.add_argument("round_id")
            part.add_argument("--bugs")
            part.add_argument("--batches", help="case-generator 的 batches.json；未闭环时拒绝建台账")
            part.add_argument("--gate-report", help="设计门禁报告；fail 时拒绝建台账")
            part.add_argument("--adversarial-gate", help="adversarial_gate.py 结论输出；未提供且未豁免时拒绝建台账")
            part.add_argument("--adversarial-waiver", help="对抗循环豁免理由（纯只读小任务可用，留痕）")
        elif command == "begin":
            part.add_argument("attempt_id")
            part.add_argument("case_id")
            part.add_argument("intended_action")
        elif command == "finish":
            part.add_argument("result_json")
        else:
            part.add_argument("--bugs")
    args = parser.parse_args()
    try:
        design, context = _load(args.design), _load(args.context)
        if args.command == "init":
            result = initialize(args.workspace, design, context, args.round_id,
                                _load(args.bugs) if args.bugs else None,
                                _load(args.batches) if args.batches else None,
                                _load(args.gate_report) if args.gate_report else None,
                                _load(args.adversarial_gate) if args.adversarial_gate else None,
                                args.adversarial_waiver)
        elif args.command == "begin":
            result = begin(args.workspace, design, context, args.attempt_id, args.case_id, args.intended_action)
        elif args.command == "finish":
            result = finish(args.workspace, design, context, **_load(args.result_json))
        else:
            result = summarize(args.workspace, design, context, _load(args.bugs) if args.bugs else None)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except Exception as exc:
        print(f"Ledger input/state error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
