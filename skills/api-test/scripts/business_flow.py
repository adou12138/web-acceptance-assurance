# -*- coding: utf-8 -*-
"""知识库驱动的业务流程模型与计划生成器。

本模块只负责把已确认/待确认的业务流程知识规范化为可审阅的执行计划，
不发送请求，也不依赖 wjx-cli。普通 action 的执行仍由 wjx-api-test 负责。

流程来源约定：知识库根目录下可放置 project/business_flows/*.json。
JSON 比 YAML 更适合当前无额外依赖的 skill；文档/CLI 只能提供候选线索，
必须在 flow JSON 的 sources/approval 中留痕后才能进入正式执行。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

SCHEMA_VERSION = 1
WRITE_OPERATIONS = {
    "create", "update", "modify", "publish", "disable", "delete", "cleanup", "restore",
    "import", "export", "submit", "send", "write",
}
_REF_RE = re.compile(r"^\$\{(?P<step>[A-Za-z][A-Za-z0-9_-]*)\.(?P<field>[A-Za-z][A-Za-z0-9_.-]*)\}$")


class FlowValidationError(ValueError):
    """流程知识不符合规范，禁止生成可执行计划。"""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def flow_hash(flow: Mapping[str, Any]) -> str:
    """计算不含 approval 的稳定哈希，绑定流程内容。"""
    doc = dict(flow)
    doc.pop("approval", None)
    return hashlib.sha256(_canonical(doc)).hexdigest()


def _as_list(value: Any, field: str) -> List[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise FlowValidationError(f"{field} 必须是数组")
    return value


def _check_ref(value: Any, known_steps: Mapping[str, Mapping[str, Any]], field: str) -> None:
    if not isinstance(value, str):
        raise FlowValidationError(f"{field} 必须是字符串")
    match = _REF_RE.match(value)
    if not match:
        # Literal resource names are allowed in preconditions; references are
        # required only when a value is explicitly written as ${step.field}.
        return
    step_id = match.group("step")
    if step_id not in known_steps:
        raise FlowValidationError(f"{field} 引用了不存在或尚未声明的步骤：{step_id}")
    produces = known_steps[step_id].get("produces") or {}
    field_name = match.group("field").split(".", 1)[0]
    if field_name not in produces:
        raise FlowValidationError(
            f"{field} 引用了步骤 {step_id} 未声明的产出：{field_name}")


def _validate_sources(flow: Mapping[str, Any]) -> None:
    # 【审查修复·空集合放行】模块约定「流程必须在 flow JSON 的 sources/approval 中
    # 留痕后才能进入正式执行」，而此前只在列表非空时逐条校验——sources 缺失或为
    # 空照样通过 validate_flow，execution_ready=true 代表的「资源可用」里并不包含
    # 「来源有据」。零来源证据的流程可以一路生成计划，审计时无从追问依据。
    raw = flow.get("sources", None)
    if raw is None:
        raise FlowValidationError("缺少 sources：流程的依据来源必须留痕后才能进入执行")
    items = _as_list(raw, "sources")
    if not items:
        raise FlowValidationError(
            "sources 不能为空：至少一条带连续原文 evidence 的依据来源"
            "（例如接口契约或产品文档出处），否则无法追溯本流程的业务事实")
    for index, source in enumerate(items):
        if not isinstance(source, dict):
            raise FlowValidationError(f"sources[{index}] 必须是对象")
        if not str(source.get("source") or "").strip():
            raise FlowValidationError(f"sources[{index}] 缺少 source")
        if not str(source.get("evidence") or "").strip():
            raise FlowValidationError(f"sources[{index}] 缺少连续原文 evidence")
        confidence = source.get("confidence", "candidate")
        if confidence not in {"candidate", "confirmed", "high", "medium", "low"}:
            raise FlowValidationError(f"sources[{index}].confidence 值无效")


def validate_flow(flow: Mapping[str, Any], *, require_approval: bool = False) -> Dict[str, Any]:
    """校验流程并返回浅拷贝。

    校验重点：步骤顺序、资源引用、写操作门禁字段、来源证据和审批哈希。
    该函数不发请求，不读取凭据，也不替用户确认业务事实。
    """
    if not isinstance(flow, dict):
        raise FlowValidationError("流程必须是 JSON 对象")
    if flow.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise FlowValidationError("不支持的 schema_version")
    for field in ("flow_id", "name", "resource_type"):
        if not str(flow.get(field) or "").strip():
            raise FlowValidationError(f"缺少 {field}")
    steps = _as_list(flow.get("steps"), "steps")
    if not steps:
        raise FlowValidationError("steps 不能为空")

    known: Dict[str, Mapping[str, Any]] = {}
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise FlowValidationError(f"steps[{index}] 必须是对象")
        step_id = str(step.get("id") or "").strip()
        action = str(step.get("action") or "").strip()
        if not step_id or not re.match(r"^[A-Za-z][A-Za-z0-9_-]*$", step_id):
            raise FlowValidationError(f"steps[{index}].id 必须是合法标识符")
        if step_id in known:
            raise FlowValidationError(f"步骤 id 重复：{step_id}")
        if not action:
            raise FlowValidationError(f"steps[{index}] 缺少 action")
        operation = str(step.get("operation") or "read").strip().lower()
        if operation not in {"read", "list"} | WRITE_OPERATIONS:
            raise FlowValidationError(f"steps[{index}].operation 不支持：{operation}")
        produces = step.get("produces") or {}
        if not isinstance(produces, dict):
            raise FlowValidationError(f"steps[{index}].produces 必须是对象")
        for key, path in produces.items():
            if not re.match(r"^[A-Za-z][A-Za-z0-9_.-]*$", str(key)):
                raise FlowValidationError(f"steps[{index}].produces 键不合法：{key}")
            if not isinstance(path, str) or not path.strip():
                raise FlowValidationError(f"steps[{index}].produces.{key} 必须是字段路径")
        # Validate references against earlier steps only; self/cyclic references are invalid.
        for req_index, req in enumerate(_as_list(step.get("requires"), f"steps[{index}].requires")):
            _check_ref(req, known, f"steps[{index}].requires[{req_index}]")
        for pre_index, pre in enumerate(_as_list(step.get("preconditions"), f"steps[{index}].preconditions")):
            if not isinstance(pre, (dict, str)):
                raise FlowValidationError(f"steps[{index}].preconditions[{pre_index}] 类型无效")
            if isinstance(pre, str):
                _check_ref(pre, known, f"steps[{index}].preconditions[{pre_index}]")
        # Register only after validation so later steps can reference this step.
        known[step_id] = step
        for ver_index, ver in enumerate(_as_list(step.get("verifies"), f"steps[{index}].verifies")):
            if not isinstance(ver, (dict, str)):
                raise FlowValidationError(f"steps[{index}].verifies[{ver_index}] 类型无效")
        cleanup = step.get("cleanup")
        if cleanup is not None and not isinstance(cleanup, dict):
            raise FlowValidationError(f"steps[{index}].cleanup 必须是对象")

    _validate_sources(flow)
    approval = flow.get("approval") or {}
    if not isinstance(approval, dict):
        raise FlowValidationError("approval 必须是对象")
    if approval.get("approved"):
        approver = str(approval.get("approver") or "").strip()
        if not approver:
            raise FlowValidationError("已批准流程必须有 approver")
        if approval.get("flow_hash") != flow_hash(flow):
            raise FlowValidationError("approval.flow_hash 与当前流程不一致，必须重新审批")
    if require_approval and not approval.get("approved"):
        raise FlowValidationError("正式执行前必须先批准流程")
    return dict(flow)


def _is_flow_spec(path: Path) -> bool:
    """判断文件是否是业务流程规格（而非 manifest.json 等索引文件）。

    判据用文件内容特征（顶层含 flow_id 与 steps），不用文件名白名单——
    知识库流程文件名允许自定义，但 manifest.json 同样落在 business_flows/*.json，
    若只按通配符收集，它会被当成流程候选，导致多候选误报或“缺少 flow_id”报错。
    """
    if path.name == "manifest.json":
        return False
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(doc, dict) and bool(doc.get("flow_id")) and isinstance(doc.get("steps"), list)


def find_flow_specs(kb_root: str, query: Optional[str] = None) -> List[Path]:
    """从知识库约定目录查找流程规格；找不到时返回空，不猜流程。"""
    base = Path(kb_root) / "project" / "business_flows"
    if not base.is_dir():
        return []
    paths = [p for p in sorted(base.glob("*.json")) if _is_flow_spec(p)]
    if not query:
        return paths
    needle = query.casefold()
    hits: List[Path] = []
    for path in paths:
        if needle in path.name.casefold():
            hits.append(path)
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if needle in text.casefold():
            hits.append(path)
    return hits


def load_flow(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise FlowValidationError(f"流程文件不存在：{p}")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FlowValidationError(f"流程文件读取失败：{p}: {exc}") from exc
    return validate_flow(doc)


def build_plan(flow: Mapping[str, Any], *, environment: Optional[Mapping[str, Any]] = None,
               require_approval: bool = False) -> Dict[str, Any]:
    """输出审阅计划；不会执行 action。"""
    doc = validate_flow(flow, require_approval=require_approval)
    env = dict(environment or {})
    approved = bool((doc.get("approval") or {}).get("approved"))
    write_steps = [s for s in doc["steps"] if str(s.get("operation", "read")).lower() in WRITE_OPERATIONS]
    write_allowed = env.get("type") == "test" and env.get("write_enabled") is True
    steps = []
    for index, step in enumerate(doc["steps"], 1):
        operation = str(step.get("operation") or "read").lower()
        is_write = operation in WRITE_OPERATIONS
        steps.append({
            "order": index,
            "id": step["id"],
            "action": step["action"],
            "operation": operation,
            "requires": list(step.get("requires") or []),
            "produces": dict(step.get("produces") or {}),
            "preconditions": list(step.get("preconditions") or []),
            "verifies": list(step.get("verifies") or []),
            "status": step.get("status", "active"),
            "optional": bool(step.get("optional", False)),
            "reason": step.get("reason"),
            "execution": "blocked" if (is_write and not (approved and write_allowed)) else "eligible",
        })
    blocking = []
    if not approved:
        blocking.append("flow approval is not confirmed")
    if write_steps and not write_allowed:
        blocking.append("write steps require environment.type=test and write_enabled=true")
    placeholder_steps = [s for s in doc["steps"]
                         if str(s.get("action") or "").strip().startswith("<")]
    deferred_steps = [s for s in placeholder_steps
                      if bool(s.get("optional")) or str(s.get("status") or "").lower() in {"deferred", "disabled"}]
    unresolved_steps = [s["id"] for s in placeholder_steps if s not in deferred_steps]
    deferred_action_steps = [s["id"] for s in deferred_steps]
    if unresolved_steps:
        blocking.append("unresolved action placeholders: " + ", ".join(unresolved_steps))
    for item in steps:
        if item["id"] in unresolved_steps:
            item["execution"] = "blocked"
        elif item["id"] in deferred_action_steps:
            item["execution"] = "deferred"
    return {
        "schema_version": SCHEMA_VERSION,
        "flow_id": doc["flow_id"],
        "name": doc["name"],
        "resource_type": doc["resource_type"],
        "flow_hash": flow_hash(doc),
        "approval": dict(doc.get("approval") or {}),
        "sources": list(doc.get("sources") or []),
        "write_step_count": len(write_steps),
        "unresolved_action_steps": unresolved_steps,
        "deferred_action_steps": deferred_action_steps,
        "execution_ready": not blocking,
        "blocking_reasons": blocking,
        "steps": steps,
        "note": "该计划只来源于知识库流程定义；未调用 wjx-cli，也未发送请求。",
    }


def approve_flow(flow: Mapping[str, Any], approver: str) -> Dict[str, Any]:
    """返回带审批信息的新流程对象，不覆盖来源文件。"""
    doc = validate_flow(flow)
    if not str(approver or "").strip():
        raise FlowValidationError("approver 不能为空")
    if (doc.get("approval") or {}).get("approved"):
        raise FlowValidationError("流程已经批准，修改后必须另存新版本")
    doc["approval"] = {
        "approved": True,
        "approver": str(approver).strip(),
        "flow_hash": flow_hash(doc),
    }
    return validate_flow(doc, require_approval=True)


def _main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="从知识库业务流程定义生成审阅计划（不发送请求）")
    ap.add_argument("--flow", help="业务流程 JSON 文件")
    ap.add_argument("--kb", help="知识库根目录；查找 project/business_flows/*.json")
    ap.add_argument("--query", help="在知识库流程文件名/正文中检索")
    ap.add_argument("--plan", help="计划输出 JSON；必须是不存在的新文件")
    ap.add_argument("--environment", help="执行环境 JSON，例如 '{\"type\":\"test\",\"write_enabled\":true}'")
    ap.add_argument("--approve-by", help="另存带审批信息的流程，不覆盖源文件")
    ap.add_argument("--approved-flow", help="审批流程输出路径")
    args = ap.parse_args(list(argv) if argv is not None else None)
    if bool(args.flow) == bool(args.kb):
        ap.error("必须二选一提供 --flow 或 --kb")
    if args.kb:
        hits = find_flow_specs(args.kb, args.query)
        if not hits:
            print("知识库中未找到业务流程定义（仅支持 project/business_flows/*.json），不猜测流程。")
            return 1
        if len(hits) > 1:
            print(json.dumps({"candidates": [str(p) for p in hits]}, ensure_ascii=False, indent=2))
            return 2
        args.flow = str(hits[0])
    try:
        doc = load_flow(args.flow)
        if args.approve_by:
            if not args.approved_flow:
                ap.error("--approve-by 必须同时提供 --approved-flow")
            target = Path(args.approved_flow)
            if target.exists():
                raise FlowValidationError(f"拒绝覆盖已有审批文件：{target}")
            approved = approve_flow(doc, args.approve_by)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(approved, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"已写出审批流程：{target}")
            doc = approved
        env = json.loads(args.environment) if args.environment else {}
        plan = build_plan(doc, environment=env)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        if args.plan:
            target = Path(args.plan)
            if target.exists():
                raise FlowValidationError(f"拒绝覆盖已有计划：{target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"已写出计划：{target}")
        return 0
    except (FlowValidationError, json.JSONDecodeError) as exc:
        print(f"流程校验失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
