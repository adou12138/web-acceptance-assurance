"""Single-writer execution journal and resume summary; never executes tests or grants release."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
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


def _sibling(name):
    """按文件路径加载同目录脚本，不依赖 sys.path（CLI 与测试导入都能用）。"""
    try:
        return __import__(name)
    except ImportError:
        import importlib.util
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


# 枚举以 attack_coverage / adversarial_data 为单一事实源：路径类型、判据来源、发现方式
# 与方法库 ID 都从那里取，避免生产端与消费端各写一份然后悄悄漂移。
_ATTACK = _sibling("attack_coverage")
_ADV = _sibling("adversarial_data")
# 修订分类契约的单一事实源：phase_gate 与 execution_ledger 共用同一判定函数，
# 两端各判一次必然漂移（分类口径不同 = 放行口径不同）。
_AMD = _sibling("amendment_class")
ATTACK_PATH_TYPES = tuple(_ATTACK.PATH_TYPES)
ORACLE_SOURCES = tuple(_ATTACK.ORACLE_SOURCES)
DISCOVERY_MODES = tuple(_ATTACK.DISCOVERY_MODES)
METHOD_IDS = frozenset(_ADV.METHOD_IDS)

STATUSES = {"PASS", "FAIL", "BLOCKED", "NOT_RUN", "SKIPPED"}
SEVERITIES = {"blocker", "major", "normal", "minor"}
# 首屏事实的 schema 版本。报告渲染器与交付模板消费同一份 headline，
# 版本号不同即视为旧摘要（缺字段）→ 回退并如实标注，不静默按旧口径推断。
HEADLINE_SCHEMA = "ledger-headline/1"
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
                                        "请运行 scripts/adversarial_gate.py（门禁默认启用）并传入 --adversarial-gate"
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
    # 门禁被显式关闭（--no-enforce）与"对抗未启用"是两件事：前者是使用方的选择，
    # 须在交付物中声明关闭状态与理由，不能与"未登记对抗义务"混算。
    disabled = bool(adversarial_gate.get("adversarial_disabled"))
    disable_reason = adversarial_gate.get("disable_reason")
    info = {"adversarial_state": "verified" if enabled else "not_enabled",
            "adversarial_ceiling": ceiling, "adversarial_enabled": bool(enabled),
            "adversarial_reasons": list(reasons)}
    if disabled:
        info["adversarial_state"] = "disabled"
        info["adversarial_disabled"] = True
        info["adversarial_disable_reason"] = disable_reason
        # 关闭门禁只是"未启用对抗"，不得掩盖门禁已算出的更严结论：
        # 门禁算出"不通过"（存在已确认未闭环的 blocker/major）时，关闭状态
        # 不得把它降级为"待核实"。取更严者：通过 < 待核实 < 不通过。
        info["adversarial_ceiling"] = _strictest(ceiling, "待核实")
        info["adversarial_reasons"] = list(reasons) + [
            "对抗门禁已显式关闭（--no-enforce）："
            + (f"关闭理由：{disable_reason}；" if disable_reason else "未提供关闭理由；")
            + "关闭状态与理由须在交付物中原样呈现，未声明不得视为默认同意；"
              "关闭只解除「对抗未启用」的阻断，不放松门禁已算出的结论——"
              f"本轮对抗上限为{info['adversarial_ceiling']}"]
        return info
    if not enabled:
        # 空 plan 送到门禁（对抗义务未登记）不得当作已完成对抗。
        # 但"对抗未启用"只解除"对抗必选"的阻断，不得把门禁或基础 gate
        # 已算出的更严结论降级——取更严者：通过 < 待核实 < 不通过。
        if ok:
            info["adversarial_state"] = "waived"
            info["adversarial_ceiling"] = _strictest(ceiling, "待核实")  # 豁免不得抬高到通过
            info["adversarial_waiver"] = reason
            info["adversarial_reasons"] = list(reasons) + [
                f"豁免成立（只读且 {case_count} 条）：{reason}；但对抗未执行，"
                f"本轮对抗上限为{info['adversarial_ceiling']}"]
        else:
            info["adversarial_ceiling"] = _strictest(ceiling, "待核实")
            info["adversarial_reasons"] = list(reasons) + [
                f"对抗未启用（adversarial_enabled=false）且豁免不成立：本轮对抗上限为{info['adversarial_ceiling']}"
                + (f"（{why}）" if reason else "")]
    return info


def _applicable_missing(gate_inputs):
    """返回本轮**适用**且尚未提供的门禁证据项。

    只记「未提供」这一事实，不代替上游产物做合格性判断：建账记初始状态，
    收尾复核同轮最终产物，缺项在收尾时披露。

    适用性判据（机械，不看理由文本）：

    - **走了三阶段流程**（本轮提供了 phase_state）：阶段完整性、跳间校验、
      攻击覆盖三项职责都由 phase_gate 承担——它的 submit 强制校验评审/用例/
      对抗 plan 三件交接产物，execute 阶段收尾强制交攻击覆盖结论。此时台账
      再索要 --batches/--gate-report/--attack-coverage 就是**同一职责计两次
      缺失**，会把合法三阶段流程误压成待核实。三项均不计缺口。
    - **旧工作流**（未提供 phase_state）：三阶段闸门未走，按专家入口契约须压
      待核实；跳间校验与攻击覆盖的适用入口只有 --batches/--gate-report/
      --attack-coverage，缺任一即记缺口。

    对抗门禁不在此列：它的缺失由 _adversarial_precondition 单独判定并留痕
    （adversarial_state / adversarial_reasons），此处重复计会让同一个缺口
    出现两套原因码。
    """
    if gate_inputs.get("phase_state"):
        return []
    missing = []
    for name in ("phase_state", "batches", "gate_report", "attack_coverage"):
        if not gate_inputs.get(name):
            missing.append(name)
    return missing


# 缺失输入的中文说明（收尾披露用）。对抗门禁缺失另有 _adversarial_precondition
# 的专门文案，此处只补充其余四项，不重复对抗门禁的理由。
_MISSING_INPUT_LABELS = {
    "phase_state": "三阶段闸门状态（--phase-state）",
    "batches": "批次闭环校验（--batches）",
    "gate_report": "设计门禁报告（--gate-report）",
    "attack_coverage": "攻击面覆盖结论（--attack-coverage）",
}


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


def _attack_precondition(attack_coverage):
    """攻击面覆盖进门条件：密度/判据独立性不达标时压结论上限。

    attack_coverage 为 scripts/attack_coverage.py 的结论输出；不传时返回空字典，
    baseline 结构保持旧样（向后兼容）。只认该脚本机械算出的 verdict_ceiling，
    不接受人工改写的"已充分覆盖"声明。

    存在目的：154 项真实测试里 143 项走正向路径、surf 关联 0、方法标注 0，
    最终只产出 4 条 FAIL——
    "铺得全"曾冒充"打得深"，此处在建账阶段就把它分开计。
    """
    if attack_coverage is None:
        return {}
    if not isinstance(attack_coverage, dict):
        raise ValueError("attack_coverage must be a JSON object（attack_coverage.py 的结论输出）")
    ceiling = attack_coverage.get("verdict_ceiling")
    if ceiling not in {"通过", "待核实", "不通过"}:
        raise ValueError("attack_coverage 缺 verdict_ceiling（需为 通过/待核实/不通过）")
    gaps = attack_coverage.get("gaps")
    if not isinstance(gaps, list):
        raise ValueError("attack_coverage 缺 gaps 数组（须原样呈现，不得隐矞缺口）")
    info = {
        "attack_coverage_ceiling": ceiling,
        "attack_density": attack_coverage.get("attack_density"),
        "attack_gaps": list(gaps),
    }
    # 弱化调用留痕：与 phase_gate._attack_check 同一口径——未做 plan 交叉核对
    # （未提供 plan 或风险面集合为空）时，攻击面覆盖结论无法证明与计划对过账。
    # 压上限而非拒绝文件，与其余门禁口径一致。
    plan_state = attack_coverage.get("plan_check_state")
    if plan_state is not None and plan_state != "checked":
        info["attack_coverage_ceiling"] = _strictest(ceiling, "待核实")
        info["attack_plan_check_state"] = plan_state
        info["attack_reasons"] = [
            "攻击覆盖未与 plan 交叉核对（plan_check_state=%s）：属弱化调用，"
            "结论上限压待核实" % plan_state]
    if "atk_checked" in attack_coverage:
        info["attack_atk_checked"] = bool(attack_coverage.get("atk_checked"))
    return info


def _phase_precondition(phase_state):
    """三阶段闸门进门条件：未走完放行流程时压结论上限。

    phase_state 为 phase_gate.py 状态文件内容；不传时返回空字典，
    baseline 结构保持旧样（向后兼容，不留空字段）。
    只认机械事实：三阶段均 done、无未批准修订、无未确认路由误配。
    """
    if phase_state is None:
        return {}
    phases = phase_state.get("phases") or {}
    reasons = []
    for phase in ("plan", "execute", "adjudicate"):
        status = (phases.get(phase) or {}).get("status")
        if status != "done":
            reasons.append(f"阶段 {phase} 状态为 {status}，未走完提交-放行-开始-完成流程")
    for amendment in phase_state.get("amendments") or []:
        # 修订分类契约与 phase_gate 共用（scripts/amendment_class.py 单一事实源）：
        # 判定走同一个 is_blocking()——类别决定"需不需要先批"，状态决定"批了没有"。
        # 只有未批准的范围变更（scope_change）阻断；只读观察（readonly_lead）
        # 凭据齐备即可执行，不压结论上限——否则如实登记只读线索反被惩罚。
        if _AMD.is_blocking(amendment):
            kind = str(amendment.get("kind") or "scope_change")
            reasons.append(f"阶段修订 {amendment.get('id')} 未获批准"
                           f"（分类：{kind}）：{amendment.get('note')}")
    for phase, entry in phases.items():
        routing = (entry or {}).get("routing") or {}
        declared = routing.get("declared_class")
        expected = routing.get("expected_class")
        if declared and expected and declared != expected and not routing.get("ack"):
            reasons.append(f"阶段 {phase} 模型路由误配未确认（声明 {declared} / 要求 {expected}）")
    # 跨会话交接：同会话放行 = 没换会话，也就没换模型，结论不得高于待核实
    for phase, entry in phases.items():
        approval = (entry or {}).get("approval") or {}
        if approval.get("mode") == "same_session":
            reasons.append(f"阶段 {phase} 为同会话放行：未跨会话交接（未换模型），"
                           "不满足阶段间停会话换模型的要求")
    waiting_for = phase_state.get("awaiting_session_for")
    if waiting_for:
        reasons.append(f"交接未消费：阶段 {waiting_for} 仍等新会话 resume")
    return {"phase_state": "incomplete" if reasons else "complete",
            "phase_ceiling": "待核实" if reasons else None,
            "phase_reasons": reasons}


def _write_entry_refs(entry):
    """写台账条目的可关联引用集合（条目 ID 与显式关联字段）。"""
    refs = {str(entry.get("id") or "")}
    for key in ("linked_records", "linked_tc", "linked_ac", "case_id", "attempt_id"):
        value = entry.get(key)
        if isinstance(value, list):
            refs.update(str(x) for x in value)
        elif value:
            refs.add(str(value))
    refs.discard("")
    return refs


def _readonly_write_crosscheck(phase_state, write_ops_ledger):
    """只读修订 × 写台账交叉核对（N8）。

    只对**显式提供了 `linked_records`** 的 `approved_readonly` 修订做机械对账：
    关联到写台账中 caution/danger 级别的写记录 → 压待核实并首屏披露。

    不罚诚实：写台账缺失、或修订未显式关联条目 → 只披露「未做机械对账」，
    不压上限。时间窗/会话兜底关联仅作线索，不作为压档依据。
    """
    out = {"state": "not_checked", "mismatches": [], "not_linked": [], "notice": None}
    if not isinstance(phase_state, dict):
        return out
    readonly = [a for a in (phase_state.get("amendments") or [])
                if isinstance(a, dict) and a.get("kind") == "readonly_lead"
                and a.get("status") == "approved_readonly"]
    if not readonly:
        return out
    if write_ops_ledger is None:
        out["state"] = "not_checked"
        out["notice"] = "存在 approved_readonly 修订，但未提供写操作台账，未做机械对账（只披露不压档）"
        return out
    entries = [e for e in (write_ops_ledger.get("entries") or []) if isinstance(e, dict)]
    entry_refs = [(e, _write_entry_refs(e)) for e in entries]
    not_linked, mismatches = [], []
    for am in readonly:
        links = {str(x) for x in (am.get("linked_records") or [])}
        if not links:
            not_linked.append(str(am.get("id") or "?"))
            continue
        for entry, refs in entry_refs:
            if refs & links and str(entry.get("safetyLevel") or "") in ("caution", "danger"):
                mismatches.append({"amendment": str(am.get("id") or "?"),
                                   "write_entry": str(entry.get("id") or "?"),
                                   "safetyLevel": str(entry.get("safetyLevel"))})
    out["not_linked"] = not_linked
    out["mismatches"] = mismatches
    if mismatches:
        out["state"] = "mismatch"
    elif not_linked:
        out["state"] = "not_linked"
    else:
        out["state"] = "ok"
    return out


def initialize(workspace, design, context, round_id, known_issues=None, batches=None, gate_report=None,
               adversarial_gate=None, adversarial_waiver=None, phase_state=None,
               attack_coverage=None):
    cases, warnings = _design(design)
    _context(context)
    _text(round_id, "round_id")
    _bugs(known_issues)
    gate_precondition = _gate_precondition(batches, gate_report, list(cases), design)
    # 只读豁免条件从 context.dependency_mode 与用例数机械判定，不信理由文本
    read_only = context.get("dependency_mode") in {"replay", "mock"}
    gate_precondition.update(_adversarial_precondition(
        adversarial_gate, adversarial_waiver, read_only=read_only, case_count=len(cases)))
    gate_precondition.update(_phase_precondition(phase_state))
    gate_precondition.update(_attack_precondition(attack_coverage))
    # 缺失即留痕：可选参数缺失时不再"保持旧行为无痕迹"，而是逐项记录，
    # 收尾据此披露并压上限（与对抗门禁缺失同口径）。
    # 记录的只是「本轮未提供」，不预判上游产物是否合格——最终判定看收尾复核。
    # 「另记」：缺口不混进 gate_precondition（那是门禁判定结果），单独冻结在
    # baseline 顶层，收尾据此刷新。
    gate_inputs_present = {"phase_state": phase_state is not None,
                           "batches": batches is not None,
                           "gate_report": gate_report is not None,
                           "attack_coverage": attack_coverage is not None}
    missing_inputs = _applicable_missing(gate_inputs_present)
    workspace = Path(workspace)
    if not workspace.parent.is_dir():
        raise ValueError("workspace parent must already exist")
    workspace.mkdir(exist_ok=False)
    baseline = {"schema_version": "1.0", "round_id": round_id, "created_at": _now(), "design_hash": fingerprint(design), "context_hash": fingerprint(context), "context": context, "case_ids": list(cases), "known_issues": known_issues, "design_warnings": warnings, "gate_precondition": gate_precondition, "gate_inputs_present": gate_inputs_present, "missing_inputs": missing_inputs}
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


ATTACK_FIELDS = ("path_type", "surf_id", "method", "oracle_source", "oracle_channel",
                 "discovery_mode", "probes", "hypothesis", "hypothesis_outcome")
# hypothesis / hypothesis_outcome 必须走 finish() 落进台账：attack_coverage.py 从台账读它们
# 判「高风险面是否有可证伪预测」。若只由调用方手工回写 JSONL，会破坏 digest 哈希链
# （每一步 _append 都以整条 payload 计算 digest），等于用违规换取门禁通过。
HYPOTHESIS_OUTCOMES = ("broken", "held", "blocked")
SURF_ID_RE = re.compile(r"^SURF-[A-Za-z0-9][A-Za-z0-9_.:-]*$")


def _attack_fields(attack, evidence_paths):
    """校验并归一每次执行的攻击面字段（path_type/surf_id/method/oracle_source/…）。

    存在目的：这些字段是 attack_coverage.py 判「攻击面是否真被打过」的唯一输入。
    此前 finish() 根本不产这些字段，全仓只有消费者没有生产者——守规矩执行的人
    必然被压「待核实」，而手搓 JSONL 反而能通过。门禁不能惩罚守规矩的人。

    independent 在此处就要能指出独立通道并带上证据：等到门禁阶段再查，
    声明已经写进台账了，改口成本变高。
    """
    if attack is None:
        return {}
    if not isinstance(attack, dict):
        raise ValueError("attack 必须为对象（path_type/surf_id/method/oracle_source/…）")
    unknown = set(attack) - set(ATTACK_FIELDS)
    if unknown:
        raise ValueError(f"attack 含未知字段 {sorted(unknown)}；允许 {list(ATTACK_FIELDS)}")
    out = {}
    if "path_type" in attack:
        value = str(attack["path_type"]).strip().lower()
        if value not in ATTACK_PATH_TYPES:
            raise ValueError(f"attack.path_type 必须为 {list(ATTACK_PATH_TYPES)} 之一")
        out["path_type"] = value
    if "surf_id" in attack:
        value = str(attack["surf_id"]).strip()
        if not SURF_ID_RE.match(value):
            raise ValueError("attack.surf_id 必须形如 SURF-XXX（关联对抗 plan 的风险面）")
        out["surf_id"] = value
    if "method" in attack:
        value = str(attack["method"]).strip()
        if value not in METHOD_IDS:
            raise ValueError(f"attack.method 必须为方法库稳定 ID {sorted(METHOD_IDS)} 之一")
        out["method"] = value
    if "discovery_mode" in attack:
        value = str(attack["discovery_mode"]).strip().lower()
        if value not in DISCOVERY_MODES:
            raise ValueError(f"attack.discovery_mode 必须为 {list(DISCOVERY_MODES)} 之一")
        out["discovery_mode"] = value
    if "oracle_source" in attack:
        value = str(attack["oracle_source"]).strip().lower()
        if value not in ORACLE_SOURCES:
            raise ValueError(f"attack.oracle_source 必须为 {list(ORACLE_SOURCES)} 之一")
        out["oracle_source"] = value
    if "oracle_channel" in attack:
        value = attack["oracle_channel"]
        _text(value, "attack.oracle_channel")
        out["oracle_channel"] = value.strip()
    if out.get("oracle_source") == "independent":
        if not out.get("oracle_channel"):
            raise ValueError("oracle_source=independent 必须同时给出 oracle_channel（哪个独立通道）")
        if not evidence_paths:
            raise ValueError("oracle_source=independent 必须有证据文件，否则独立判据无法复核")
    if "probes" in attack:
        probes = attack["probes"]
        if type(probes) is not int or probes < 0:
            raise ValueError("attack.probes 必须为非负整数（本用例实际发出的探针数）")
        out["probes"] = probes
    if "hypothesis" in attack:
        _text(attack["hypothesis"], "attack.hypothesis")
        out["hypothesis"] = attack["hypothesis"].strip()
    if "hypothesis_outcome" in attack:
        value = str(attack["hypothesis_outcome"]).strip().lower()
        if value not in HYPOTHESIS_OUTCOMES:
            raise ValueError(f"attack.hypothesis_outcome 必须为 {list(HYPOTHESIS_OUTCOMES)} 之一")
        out["hypothesis_outcome"] = value
    if out.get("hypothesis") and not out.get("hypothesis_outcome"):
        raise ValueError("给了 hypothesis 就必须给 hypothesis_outcome（broken/held/blocked）")
    if out.get("hypothesis_outcome") and not out.get("hypothesis"):
        raise ValueError("给了 hypothesis_outcome 就必须给 hypothesis（原预测文本）")
    return out


def finish(workspace, design, context, attempt_id, status, actual, evidence_paths, assertions, cause, bug_ids, cleanup, evidence_grade=None, attack=None):
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
    attack_fields = _attack_fields(attack, evidence_paths)
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
        payload.update(attack_fields)
        return _append(workspace, events, "FINISH", payload)


RANK = {"通过": 0, "待核实": 1, "不通过": 2}
# 豁免上限：超过该用例数的批次必须执行对抗，不得凭理由跳过
WAIVER_MAX_CASES = 5


def _strictest(a, b):
    return max((a, b), key=lambda x: RANK.get(x, 1))


def _verdict_ceiling(baseline, counts, executed, final_precondition=None):
    """机械结论上限：由台账事实与各门禁共同限定，不由人工宣称。

    对齐 agent 口径：存在未闭环 blocker（FAIL）→ 不通过；对抗缺失/未启用或
    存在 BLOCKED/未执行 → 待核实；全部必选项已实测且对抗已验证 → 通过。

    final_precondition 为**收尾复核**时读到的同轮最终产物（阶段/覆盖/对抗结论）。
    建账时写入的只是初始状态——本轮覆盖结论要到采集后才产生，若只认建账时的
    快照，收尾就会沿用过期结论。故收尾可刷新对应类别，且：
    - 刷新只放宽「本轮尚未完成」造成的待核实，不放宽已成立的更严结论；
    - 已成立的「不通过」在任何刷新下都保持不通过。
    """
    gate = (baseline.get("gate_precondition") or {})
    final_precondition = final_precondition or {}
    adversarial = final_precondition.get("adversarial_ceiling",
                                         gate.get("adversarial_ceiling", "待核实"))
    phase_ceiling = final_precondition.get("phase_ceiling", gate.get("phase_ceiling"))
    if counts.get("FAIL"):
        return "不通过"
    fact = "待核实" if (counts.get("BLOCKED") or counts.get("NOT_RUN")
                          or counts.get("SKIPPED") or executed == 0) else "通过"
    ceiling = _strictest(fact, adversarial)
    if phase_ceiling:
        ceiling = _strictest(ceiling, phase_ceiling)
    attack_ceiling = final_precondition.get("attack_coverage_ceiling",
                                            gate.get("attack_coverage_ceiling"))
    if attack_ceiling:
        ceiling = _strictest(ceiling, attack_ceiling)
    readonly_write_ceiling = final_precondition.get("readonly_write_ceiling",
                                                     gate.get("readonly_write_ceiling"))
    if readonly_write_ceiling:
        ceiling = _strictest(ceiling, readonly_write_ceiling)
    # 建账时缺失的可选输入：收尾复核后仍未补齐的，留痕并压上限。
    # 与对抗门禁缺失同口径——弱化调用不得自证通过。
    # 只压到「待核实」，不放松已算出的更严结论（上面已取过更严者）。
    missing = final_precondition.get("missing_inputs",
                                     baseline.get("missing_inputs") or [])
    if missing:
        ceiling = _strictest(ceiling, "待核实")
    return ceiling


def _headline_checks(baseline, final_precondition, counts, evidence_issues,
                     flaky_cases, missing_inputs, read_only):
    """逐项必查项状态。返回 [{id, label, status, detail}]。

    status 四取值：`ok`（该项无缺口且未压上限）/ `limited`（该项把上限压到待核实）/
    `missing`（证据根本没提供）/ `unknown`（无法判定，如实标注而不猜）。

    判定口径与 `_applicable_missing`、`_verdict_ceiling` 同一套：走三阶段流程时
    攻击覆盖由 phase_gate 在 execute 收尾强制校验，台账不再重复索要——
    否则同一职责计两次缺失，既误压上限，也让首屏出现与实际不符的"缺攻击覆盖"。
    """
    gate = (baseline.get("gate_precondition") or {})
    checks = []

    # 1) 三阶段闸门
    phase_state = final_precondition.get("phase_state")
    if phase_state is None:
        checks.append({"id": "phase_state", "label": "三阶段闸门",
                       "status": "missing",
                       "detail": "未提供阶段状态：三阶段闸门未走，阶段完整性未机械核对"})
    elif phase_state == "complete":
        checks.append({"id": "phase_state", "label": "三阶段闸门", "status": "ok",
                       "detail": "三阶段均按流程走完（跨会话交接与放行记录齐备）"})
    else:
        checks.append({"id": "phase_state", "label": "三阶段闸门", "status": "limited",
                       "detail": "；".join(final_precondition.get("phase_reasons") or [])
                                or "阶段流程未走完"})

    # 2) 对抗门禁
    adversarial_state = final_precondition.get("adversarial_state",
                                              gate.get("adversarial_state"))
    adversarial_reasons = final_precondition.get("adversarial_reasons",
                                                 gate.get("adversarial_reasons") or [])
    adversarial_ceiling = final_precondition.get("adversarial_ceiling",
                                                  gate.get("adversarial_ceiling"))
    if adversarial_state == "verified" and adversarial_ceiling == "通过":
        checks.append({"id": "adversarial_gate", "label": "对抗门禁", "status": "ok",
                       "detail": "对抗义务已登记且门禁通过"})
    elif adversarial_state == "missing":
        checks.append({"id": "adversarial_gate", "label": "对抗门禁", "status": "missing",
                       "detail": "；".join(adversarial_reasons) or "未提供对抗门禁结论"})
    else:
        label = {"not_enabled": "对抗未启用", "waived": "对抗经豁免跳过",
                 "disabled": "对抗门禁已显式关闭"}.get(str(adversarial_state),
                                                    "对抗结论受限")
        checks.append({"id": "adversarial_gate", "label": "对抗门禁", "status": "limited",
                       "detail": (label + "：" + "；".join(adversarial_reasons)).strip("：")
                                or label})

    # 3) 攻击面覆盖：走三阶段时由 phase_gate 承担，不重复计缺失
    attack_ceiling = final_precondition.get("attack_coverage_ceiling",
                                            gate.get("attack_coverage_ceiling"))
    phase_complete = phase_state == "complete"
    if attack_ceiling:
        checks.append({"id": "attack_coverage", "label": "攻击面覆盖",
                       "status": "ok" if attack_ceiling == "通过" else "limited",
                       "detail": (f"攻击覆盖结论上限 {attack_ceiling}")
                                + ("；" + "；".join(final_precondition.get("attack_reasons") or [])
                                   if final_precondition.get("attack_reasons") else "")})
    elif phase_complete:
        checks.append({"id": "attack_coverage", "label": "攻击面覆盖", "status": "ok",
                       "detail": "由三阶段闸门在 execute 收尾强制校验（台账不重复索要）"})
    else:
        checks.append({"id": "attack_coverage", "label": "攻击面覆盖", "status": "missing",
                       "detail": "未提供攻击覆盖结论：无法区分「用例铺得全」与「攻击面打得深」"})

    # 3b) 只读修订 × 写台账交叉核对（N8）：只披露显式关联的对账结果
    rw_check = final_precondition.get("readonly_write_check") or {}
    if rw_check.get("state") == "mismatch":
        detail = "；".join(
            f"{m['amendment']} 关联写记录 {m['write_entry']}（{m['safetyLevel']}）"
            for m in rw_check.get("mismatches") or [])
        checks.append({"id": "readonly_write", "label": "只读申报核对", "status": "limited",
                       "detail": "只读申报与写记录不符：" + detail})
    elif rw_check.get("state") == "not_linked":
        checks.append({"id": "readonly_write", "label": "只读申报核对", "status": "missing",
                       "detail": "只读修订未显式关联写记录，未做机械对账"})
    elif rw_check.get("notice"):
        checks.append({"id": "readonly_write", "label": "只读申报核对", "status": "missing",
                       "detail": rw_check["notice"]})
    elif rw_check.get("state") == "ok":
        checks.append({"id": "readonly_write", "label": "只读申报核对", "status": "ok",
                       "detail": "只读申报关联记录在写台账中无 caution/danger 写操作"})

    # 4) 证据链完整性
    if evidence_issues:
        checks.append({"id": "evidence_integrity", "label": "证据链完整性",
                       "status": "limited",
                       "detail": f"{len(evidence_issues)} 条证据文件缺失或哈希不符，无法复核"})
    elif flaky_cases:
        checks.append({"id": "evidence_integrity", "label": "证据链完整性",
                       "status": "limited",
                       "detail": f"{len(flaky_cases)} 条用例结果不稳定（同一用例既有 PASS 又有 FAIL），须复核"})
    else:
        checks.append({"id": "evidence_integrity", "label": "证据链完整性", "status": "ok",
                       "detail": "全部证据文件存在且哈希一致，无不稳定用例"})

    # 5) 门禁输入齐备性
    if missing_inputs:
        checks.append({"id": "gate_inputs", "label": "门禁输入齐备性", "status": "missing",
                       "detail": "本轮未提供：" + "、".join(
                           _MISSING_INPUT_LABELS.get(name, name) for name in missing_inputs)})
    else:
        checks.append({"id": "gate_inputs", "label": "门禁输入齐备性", "status": "ok",
                       "detail": "适用且须提供的门禁证据均已提供"})
    return checks


def _build_headline(baseline, counts, executed, cases, final_precondition, ceiling,
                    evidence_issues, flaky_cases, context, missing_inputs):
    """首屏事实（单一来源）：完成范围、必查项状态、限档原因。

    为什么要有这一份：报告渲染器（render_report_html.py）与交付模板
    （references/output-templates.md）此前各自从原始字段推断「完成了什么、
    差在哪、各自的结论上限是多少」——各推断一次必然漂移，且漂移方向通常是
    报好看的那个。现在两者都消费这份事实。

    ceiling 由 `_verdict_ceiling` 唯一算出后传入，此处**不重算**——
    首屏写什么上限与台账上限必须是同一个值。
    """
    read_only = context.get("dependency_mode") in {"replay", "mock"}
    checks = _headline_checks(baseline, final_precondition, counts, evidence_issues,
                              flaky_cases, missing_inputs, read_only)
    planned = len(cases)
    not_run = counts.get("NOT_RUN", 0)
    blocked = counts.get("BLOCKED", 0)
    failed = counts.get("FAIL", 0)
    skipped = counts.get("SKIPPED", 0)

    reasons = []
    if failed:
        reasons.append({"code": "failure_detected",
                        "text": f"{failed} 项用例判定为失败：已证实的失败保留更严结论，"
                                "不得因其他维度齐备而放宽"})
    if blocked:
        reasons.append({"code": "blocked_cases",
                        "text": f"{blocked} 项用例处于阻塞状态，未取得结论"})
    if not_run:
        reasons.append({"code": "execution_incomplete",
                        "text": f"{not_run} 项用例未执行，验收范围未覆盖"})
    if skipped:
        reasons.append({"code": "skipped_cases",
                        "text": f"{skipped} 项用例被跳过，未取得结论"})
    for check in checks:
        if check["status"] == "missing":
            reasons.append({"code": "gate_missing",
                            "text": f"{check['label']}未提供证据：{check['detail']}"})
        elif check["status"] == "limited":
            reasons.append({"code": "gate_limited",
                            "text": f"{check['label']}受限：{check['detail']}"})
    # 仅授权限制：其他维度都齐备，但结论只在授权范围内成立。
    # 只有这时才允许写「只读范围内已完成」——否则就是拿授权当遮羞布。
    if not reasons and read_only:
        reasons.append({"code": "authorization_only",
                        "text": "本轮为只读授权范围（dependency_mode=%s），"
                                "结论仅在授权范围内成立"
                                % context.get("dependency_mode")})

    all_ok = all(c["status"] == "ok" for c in checks)
    can_claim_readonly_complete = bool(
        read_only and all_ok and not failed and not blocked and not not_run
        and not skipped and not missing_inputs)

    if not planned:
        scope_text = "本轮无计划用例，未产生验收范围"
    elif executed >= planned and not (not_run or blocked or skipped):
        scope_text = f"全部 {planned} 项用例已执行并取得结论"
    else:
        scope_text = (f"计划 {planned} 项，已执行 {executed} 项；"
                      f"未执行 {not_run} 项、阻塞 {blocked} 项、跳过 {skipped} 项")

    return {
        "schema": HEADLINE_SCHEMA,
        "verdict_ceiling": ceiling,
        "completion_scope": {
            "planned": planned,
            "executed": executed,
            "not_run": not_run,
            "blocked": blocked,
            "failed": failed,
            "skipped": skipped,
            "coverage": "full" if (planned and executed >= planned
                                   and not (not_run or blocked or skipped)) else "partial",
            "text": scope_text,
            "readonly_only": read_only,
            "can_claim_readonly_complete": can_claim_readonly_complete,
        },
        "required_checks": checks,
        "limitation_reasons": reasons,
    }


def _final_precondition(baseline, phase_state=None, attack_coverage=None,
                        adversarial_gate=None, read_only=False, case_count=None,
                        write_ops_ledger=None):
    """收尾复核：读取同轮最终产物，重算各门禁的结论上限。

    只重算「本轮最终产物覆盖的类别」，未提供的类别沿用建账时状态。
    每一项都走与建账同一套判定函数，避免建账与收尾两套口径漂移。

    缺口刷新：收尾补齐了某类证据，缺口即消除（临时缺口）；但已成立的更严
    结论不因此放松——放松与否由 _verdict_ceiling 取更严者决定。
    """
    gate = (baseline.get("gate_precondition") or {})
    out = {}
    if adversarial_gate is not None:
        out.update(_adversarial_precondition(
            adversarial_gate, gate.get("adversarial_waiver"),
            read_only=read_only, case_count=case_count))
    if phase_state is not None:
        out.update(_phase_precondition(phase_state))
    if attack_coverage is not None:
        out.update(_attack_precondition(attack_coverage))
    readonly_write = _readonly_write_crosscheck(phase_state, write_ops_ledger)
    out["readonly_write_check"] = readonly_write
    if readonly_write["state"] == "mismatch":
        out["readonly_write_ceiling"] = "待核实"
    # 缺口按同一适用性判据重算：建账时未提供、收尾仍未提供的才算缺口。
    present = dict(baseline.get("gate_inputs_present") or {})
    if phase_state is not None:
        present["phase_state"] = True
    if attack_coverage is not None:
        present["attack_coverage"] = True
    missing = _applicable_missing(present)
    out["missing_inputs"] = missing
    return out


def summarize(workspace, design, context, bugs=None, phase_state=None,
              attack_coverage=None, adversarial_gate=None, write_ops_ledger=None):
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
    # 收尾复核：同轮最终产物可刷新建账时的初始状态（覆盖结论往往采集后才产生）。
    final_precondition = _final_precondition(
        baseline, phase_state=phase_state, attack_coverage=attack_coverage,
        adversarial_gate=adversarial_gate,
        read_only=context.get("dependency_mode") in {"replay", "mock"},
        case_count=len(cases), write_ops_ledger=write_ops_ledger)
    missing_inputs = final_precondition.get("missing_inputs",
                                            baseline.get("missing_inputs") or [])
    missing_reasons = [
        "未做%s：可选门禁输入缺失，结论上限压待核实"
        % _MISSING_INPUT_LABELS.get(name, name) for name in missing_inputs]
    # 结论上限只由 _verdict_ceiling 算一次；headline 消费这个值，不重算——
    # 首屏写的上限与台账上限必须是同一个值。
    verdict_ceiling = _verdict_ceiling(baseline, counts, executed, final_precondition)
    headline = _build_headline(baseline, counts, executed, cases, final_precondition,
                                verdict_ceiling, evidence_issues, flaky_cases,
                                context, missing_inputs)
    return {"round_id": baseline["round_id"], "compatible": same, "baseline": baseline, "current_context": context, "context_changed": not same, "planned": len(cases), "executed": executed, "counts": counts, "execution_rate": executed / len(cases) if cases else None, "pass_rate": counts["PASS"] / executed if executed else None, "outcomes": outcomes, "ready": ready, "needs_reconcile": reconcile, "stale_cases": [] if same else list(cases), "retest_candidates": [c for c, o in outcomes.items() if o["status"] in {"FAIL", "BLOCKED", "SKIPPED"}], "flaky_cases": flaky_cases, "evidence_issues": evidence_issues, "open_bugs": open_bugs, "bug_delta": bug_delta, "missing_bug_ids": missing_bugs, "design_warnings": warnings, "missing_inputs": missing_inputs, "missing_input_reasons": missing_reasons, "final_precondition": final_precondition, "headline": headline, "verdict_ceiling": verdict_ceiling, "scope": "progress only; no acceptance or release verdict; evidence content requires human/tool review"}


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
            part.add_argument("--batches", help="case-generator 的 batches.json；提供且未闭环时拒绝建台账（不提供则留痕并压上限）")
            part.add_argument("--gate-report", help="设计门禁报告；提供且 fail 时拒绝建台账（不提供则留痕并压上限）")
            part.add_argument("--adversarial-gate", help="adversarial_gate.py 结论输出；未提供且无合资格豁免时结论上限压待核实（不拒绝建账）")
            part.add_argument("--adversarial-waiver", help="对抗循环豁免理由（纯只读小任务可用，留痕）")
            part.add_argument("--phase-state",
                              help="phase_gate.py 状态文件；未提供时留痕并压上限，阶段未走完放行流程时结论上限压待核实")
            part.add_argument("--attack-coverage",
                              help="attack_coverage.py 结论输出；未提供时留痕并压上限，攻击密度/判据独立性不达标时压上限")
        elif command == "begin":
            part.add_argument("attempt_id")
            part.add_argument("case_id")
            part.add_argument("intended_action")
        elif command == "finish":
            part.add_argument("result_json")
        else:
            part.add_argument("--bugs")
            part.add_argument("--phase-state", help="收尾复核用：同轮最终的 phase_gate.py 状态文件")
            part.add_argument("--attack-coverage", help="收尾复核用：同轮最终的 attack_coverage.py 结论输出")
            part.add_argument("--adversarial-gate", help="收尾复核用：同轮最终的 adversarial_gate.py 结论输出")
            part.add_argument("--write-ops-ledger",
                              help="写操作台账（figma write_ops_ledger.py 产物）；可选，仅对显式关联的只读修订对账")
    args = parser.parse_args()
    try:
        design, context = _load(args.design), _load(args.context)
        if args.command == "init":
            result = initialize(args.workspace, design, context, args.round_id,
                                _load(args.bugs) if args.bugs else None,
                                _load(args.batches) if args.batches else None,
                                _load(args.gate_report) if args.gate_report else None,
                                _load(args.adversarial_gate) if args.adversarial_gate else None,
                                args.adversarial_waiver,
                                _load(args.phase_state) if getattr(args, "phase_state", None) else None,
                                _load(args.attack_coverage) if getattr(args, "attack_coverage", None) else None)
        elif args.command == "begin":
            result = begin(args.workspace, design, context, args.attempt_id, args.case_id, args.intended_action)
        elif args.command == "finish":
            result = finish(args.workspace, design, context, **_load(args.result_json))
        else:
            result = summarize(args.workspace, design, context, _load(args.bugs) if args.bugs else None,
                               phase_state=_load(args.phase_state) if args.phase_state else None,
                               attack_coverage=_load(args.attack_coverage) if args.attack_coverage else None,
                               adversarial_gate=_load(args.adversarial_gate) if args.adversarial_gate else None,
                               write_ops_ledger=_load(args.write_ops_ledger) if args.write_ops_ledger else None)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except Exception as exc:
        print(f"Ledger input/state error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
