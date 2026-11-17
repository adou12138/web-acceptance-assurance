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


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


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


def _gate_precondition(batches, gate_report):
    """校验第 2→3 跳进门条件；两者均为可选，未提供时保持旧行为。"""
    info = {}
    if batches is not None:
        if not isinstance(batches, dict) or not isinstance(batches.get("batches"), list) or not batches["batches"]:
            raise ValueError("batches must be a batch state object with a nonempty batches array")
        bad = [str(b.get("unit_id", "?")) for b in batches["batches"]
               if not isinstance(b, dict) or b.get("status") != "done" or b.get("gate") not in {"pass", "waived"}]
        if bad:
            raise ValueError("design gate not closed: " + ", ".join(bad) + "（批次未完成或门禁未通过）")
        info["batches_closed"] = True
    if gate_report is not None:
        if not isinstance(gate_report, dict) or gate_report.get("status") not in {"pass", "warning"}:
            raise ValueError("gate report must be pass/warning; fail or missing status blocks execution")
        failed = [str(c.get("id", "?")) for c in gate_report.get("checks", [])
                  if isinstance(c, dict) and c.get("status") == "failed"]
        if failed:
            raise ValueError("gate report has failed checks: " + ", ".join(failed))
        info["gate_report_status"] = gate_report["status"]
    return info


def initialize(workspace, design, context, round_id, known_issues=None, batches=None, gate_report=None):
    cases, warnings = _design(design)
    _context(context)
    _text(round_id, "round_id")
    _bugs(known_issues)
    gate_precondition = _gate_precondition(batches, gate_report)
    workspace = Path(workspace)
    if not workspace.parent.is_dir():
        raise ValueError("workspace parent must already exist")
    workspace.mkdir(exist_ok=False)
    baseline = {"schema_version": "1.0", "round_id": round_id, "created_at": _now(), "design_hash": fingerprint(design), "context_hash": fingerprint(context), "context": context, "case_ids": list(cases), "known_issues": known_issues, "design_warnings": warnings, "gate_precondition": gate_precondition}
    _write_json_new(workspace / "cases.snapshot.json", design)
    _write_json_new(workspace / "baseline.json", baseline)
    (workspace / "events.jsonl").touch(exist_ok=False)
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


def finish(workspace, design, context, attempt_id, status, actual, evidence_paths, assertions, cause, bug_ids, cleanup):
    _text(attempt_id, "attempt_id")
    _text(actual, "actual")
    _text(cleanup, "cleanup")
    if status not in STATUSES or status == "NOT_RUN" or cause not in CAUSES:
        raise ValueError("invalid finished status/cause")
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
        return _append(workspace, events, "FINISH", {"attempt_id": attempt_id, "case_id": started["case_id"], "round_id": baseline["round_id"], "status": status, "actual": actual, "evidence": evidence, "assertions": assertions, "cause": cause, "bug_ids": bug_ids, "cleanup": cleanup})


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
    return {"round_id": baseline["round_id"], "compatible": same, "baseline": baseline, "current_context": context, "context_changed": not same, "planned": len(cases), "executed": executed, "counts": counts, "execution_rate": executed / len(cases) if cases else None, "pass_rate": counts["PASS"] / executed if executed else None, "outcomes": outcomes, "ready": ready, "needs_reconcile": reconcile, "stale_cases": [] if same else list(cases), "retest_candidates": [c for c, o in outcomes.items() if o["status"] in {"FAIL", "BLOCKED", "SKIPPED"}], "flaky_cases": flaky_cases, "evidence_issues": evidence_issues, "open_bugs": open_bugs, "bug_delta": bug_delta, "missing_bug_ids": missing_bugs, "design_warnings": warnings, "scope": "progress only; no acceptance or release verdict; evidence content requires human/tool review"}


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
                                _load(args.gate_report) if args.gate_report else None)
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
