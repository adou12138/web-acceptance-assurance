"""Three-phase execution gate: plan -> execute -> adjudicate, across separate sessions.

设计原则：
1. **阶段之间换会话，不换会话不给过。** 一个阶段做完必须 `checkpoint` 收尾并结束本会话；
   下一阶段只能在用户**新开的会话**里用 `resume` 启动。换会话才谈得上换模型——
   同会话内确认（legacy `approve`）虽保留兼容，但一律记 `same_session`，结论上限压“待核实”。
2. 阶段推进只认状态机，不认“我觉得可以了”。`resume` 必须带用户在新会话里原样粘回的
   确认码（由 `checkpoint` 生成并打印），缺确认码即拒绝；确认文本随记录留痕。
3. 采集阶段只许记录观察，不许下判定：记录里出现判定字段或 PASS/FAIL 类枚举值时拒绝写入。
   这条把“采集方只负责点、填、读，不负责判定”落成机械约束，而不是口头约定。
4. 裁决阶段只许依据实际观察下判定：无证据引用、依据来源非 observed_*、
   或声明“由脚本补全页面行为”时拒绝。这是诚实性规则的机械投影。
5. 模型路由以“声明 + 核对”实现：每阶段必须声明实际执行模型类别，与路由表不符时
   结论上限压“待核实”，除非显式记录误配理由。本脚本不切换模型，只核对声明。
6. 本闸门只约束阶段顺序、会话边界与记录合规，不产生“通过/不通过”业务结论；
   技术结论仍由 execution_ledger / adversarial_gate 计算并按最严取齐。

诚实边界：确认码落在状态文件里，能读到文件的智能体理论上可以自行填入。确认码防的是
“没看交接包就往下跑”和“同会话蒙混过关”，**不是**防有心伪造——那等同伪造哈希链，
属于故意欺骗，脚本无法在无签名密钥的前提下证明“这行字确实是人打的”。
"""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import sys


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


# 修订分类契约的单一事实源：phase_gate 与 execution_ledger 共用，
# 两处各写一份必然漂移（判定口径不同 = 放行口径不同）。
_AMD = _sibling("amendment_class")
AMENDMENT_KINDS = tuple(_AMD.AMENDMENT_KINDS)

SCHEMA = "phase-state/2"
PHASES = ("plan", "execute", "adjudicate")
NEXT_PHASE = {"plan": "execute", "execute": "adjudicate", "adjudicate": None}
PHASE_LABELS = {"plan": "规划与用例生成", "execute": "批量执行与状态采集",
                "adjudicate": "结果裁决与缺陷分类"}
# 结论上限的合法取值。三种都要认：对抗门禁可产出「不通过」；attack_coverage 与
# exploration 目前只产出「通过 / 待核实」，但把「不通过」拒之门外只会在上游改用时
# 报一句误导性的「缺字段」。
CEILINGS = ("通过", "待核实", "不通过")


def _strictest(left, right):
    """取更严的结论上限。通过 < 待核实 < 不通过。

    用于「不得把已算出的更严结论降级」：解除阻断（豁免/关闭/弱化调用补齐）
    只解除「对抗必选」这类阻断，不放松已成立的结论。
    """
    if left not in CEILINGS or right not in CEILINGS:
        raise ValueError(f"结论上限取值非法：{left!r} / {right!r}（需为 {list(CEILINGS)}）")
    return left if CEILINGS.index(left) >= CEILINGS.index(right) else right

# 交接产物：submit 时逐条要求真实存在（不认占位路径），plan 交棒给 execute 时
# 还必须凑齐三件——需求评审、用例集、对抗 plan。缺任何一件都说明规划阶段只是走
# 了个状态流转：曾经零产物 + 三个不存在的路径也能一路推进到执行阶段。
HANDOFF_REQUIRED_ARTIFACTS = {
    "execute": ("review", "cases", "adversarial"),
}
ARTIFACT_HINT = {
    "review": "需求评审结论 review-result.json",
    "cases": "用例资产 cases-*.json",
    "adversarial": "对抗计划 adversarial-plan.json（风险面 SURF / 判据 ORC / 探针义务 ADV）",
}

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

# 交接确认文本模板：checkpoint 打印给用户，用户在**新会话**里原样粘回
HANDOFF_CONFIRM_TEMPLATE = ("续跑任务「{task}」的阶段 {phase}（{label}），确认码 {token}；"
                            "本会话执行模型 {model_hint}（{model_class}）")
HANDOFF_FILE = "handoff-{phase}.md"
HANDOFF_JSON = "handoff-{phase}.json"

# 采集阶段禁入的判定类键名（出现即视为越界判定）
# 采集阶段禁判定：同义字段必须一起禁，否则老实写 verdict 的被拦、写 status 的绕过。
# 实测过这个漏洞：verdict 触发 judgement_key，status 不触发，等于教人换个键名。
COLLECTION_FORBIDDEN_KEYS = (
    "verdict", "verdicts", "judgement", "judgment", "conclusion", "conclusions",
    "severity", "defect", "defects", "classification", "root_cause", "pass_fail",
    "status", "result", "results", "outcome", "assessment", "rating", "score",
)
# 现场怀疑的合法出口：结构化记下"哪可疑、依据是什么"，不冒充判定。
LEAD_FIELDS = {"suspicion", "basis", "where"}
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
            "sessions": state.get("sessions") or [],
            "active_session": state.get("active_session"),
            "awaiting_session_for": state.get("awaiting_session_for"),
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


def _require_session(state, session=None):
    """会话归属校验：状态同一时刻只由一个会话持有。

    换会话续跑的唯一入口是 resume；其它命令拿着别的会话标识来操作一律拒绝，
    避免两个会话交替改同一份状态。
    """
    active = state.get("active_session")
    if not active:
        raise ValueError("阶段状态未绑定会话：无法操作")
    given = _text(session, "session") if session else active
    if given != active:
        raise ValueError(f"阶段状态由会话 {active} 持有，{given} 无权操作；"
                         "跨会话续跑须在新会话执行 resume，不得直接改状态")
    return given


def _session_record(state, session):
    for item in state.get("sessions") or []:
        if item.get("id") == session:
            return item
    return None


def _record(state, action, phase=None, detail=None):
    payload = {"at": _now(), "action": action, "phase": phase, "detail": detail or {}}
    prev = _last_hash(state)
    state["history"].append(dict(payload, seq=len(state["history"]), prev=prev,
                                 hash=_entry_hash(prev, payload)))
    state["updated_at"] = payload["at"]


def new_state(task, session="SESS-1", model_class=None, model_name=None):
    task = _text(task, "task")
    session = _text(session, "session")
    return {
        "schema": SCHEMA,
        "task": task,
        "created_at": _now(),
        "updated_at": _now(),
        "active_session": session,
        "awaiting_session_for": None,
        "sessions": [{"id": session, "opened_at": _now(),
                      "model_class": (model_class.lower() if isinstance(model_class, str) else None),
                      "model_name": (model_name.strip() if isinstance(model_name, str) and model_name.strip() else None),
                      "closed_at": None, "phases": []}],
        "phases": {
            phase: {"status": "pending", "artifacts": [], "routing": None,
                    "submitted_at": None, "approval": None, "started_at": None,
                    "completed_at": None, "rejections": [], "handoff": None,
                    "session": None}
            for phase in PHASES
        },
        "amendments": [],
        "history": [],
    }


def init_state(path, task, session="SESS-1", model_class=None, model_name=None, note=None):
    """建立阶段状态机。用户下达测试任务即视为放行第一阶段（规划）。"""
    path = Path(path)
    if path.exists():
        raise ValueError(f"phase state already exists: {path}")
    if not path.parent.is_dir():
        raise ValueError("phase state parent directory must already exist")
    state = new_state(task, session, model_class, model_name)
    _record(state, "init", None, {"task": task, "session": state["active_session"],
                                  "model_class": model_class, "model_name": model_name})
    # 规划阶段无需再单独申请：用户给出测试任务本身就是放行
    entry = state["phases"]["plan"]
    entry["approval"] = {"by": "user", "via": "task_start", "mode": "task_start",
                         "at": _now(),
                         "note": (note.strip() if isinstance(note, str) and note.strip()
                                  else "用户下达测试任务，视为放行规划阶段")}
    entry["status"] = "approved"
    entry["session"] = state["active_session"]
    _record(state, "approve", "plan", entry["approval"])
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
    state.setdefault("sessions", [])
    state.setdefault("awaiting_session_for", None)
    if not state.get("active_session"):
        raise ValueError("phase state missing active_session：无法判定当前会话，须重建阶段状态")
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


def submit(state, phase, artifacts=None, model_class=None, model_name=None, note=None,
           session=None):
    """申请放行某阶段（由**上一阶段所在会话**在 checkpoint 前调用）。"""
    _require_session(state, session)
    entry = _entry(state, phase)
    if entry["status"] not in {"pending", "rejected"}:
        raise ValueError(f"phase {phase} cannot be submitted from status {entry['status']}")
    ok, why = _prior_done(state, phase)
    if not ok:
        raise ValueError(why)
    artifacts = artifacts or []
    for item in artifacts:
        _text(item, "artifact")
        path = Path(item)
        if not path.is_absolute() or not path.is_file():
            raise ValueError(f"交接产物不存在或不是绝对路径：{item}"
                             "（只认真实文件，占位路径不算做过）")
    required = HANDOFF_REQUIRED_ARTIFACTS.get(phase) or ()
    if required:
        names = [Path(a).name.lower() for a in artifacts]
        missing = [key for key in required
                   if not any(key in name for name in names)]
        if missing:
            detail = "、".join(f"{key}（{ARTIFACT_HINT[key]}）" for key in missing)
            raise ValueError(f"阶段 {phase} 的交接产物缺 {detail}："
                             "规划阶段的状态流转不能代替评审/用例/对抗 plan 三件产物")
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
                                     "note": note,
                                     "session": state.get("active_session")})
    return state


def _approver(by):
    """放行人校验：必须是人工身份，智能体不得自授放行。"""
    name = _text(by, "by")
    if name.lower() in AGENT_IDENTITIES:
        raise ValueError(f"放行人不得为智能体身份 {name!r}：阶段放行必须由用户确认，"
                         "自授放行等同于取消人工闸门")
    return name


def approve(state, phase, by, note, session=None):
    """同会话放行（兼容路径，**不是推荐流程**）。

    推荐流程是 checkpoint → 结束会话 → 新会话 resume。同会话放行会记
    mode=same_session，check_verdict 与台账一律把结论上限压“待核实”——
    因为同一会话内放行既无法换模型，也失去了“人真正看过交接包”的独立性。
    """
    _require_session(state, session)
    entry = _entry(state, phase)
    if entry["status"] != "awaiting_approval":
        raise ValueError(f"phase {phase} is not awaiting approval (status={entry['status']})")
    ok, why = _prior_done(state, phase)
    if not ok:
        raise ValueError(why)
    approval = {"by": _approver(by), "at": _now(), "note": _text(note, "note"),
                "via": "same_session", "mode": "same_session",
                "session": state.get("active_session")}
    entry["approval"] = approval
    entry["status"] = "approved"
    _record(state, "approve", phase, approval)
    return state


def reject(state, phase, by, reason, session=None):
    """驳回阶段。允许在交接点由**新会话**直接驳回（用户不满意交接产物）。"""
    entry = _entry(state, phase)
    session = _text(session, "session") if session else state.get("active_session")
    active = state.get("active_session")
    at_handoff = state.get("awaiting_session_for") == phase
    if session != active:
        if not at_handoff:
            raise ValueError(f"阶段状态由会话 {active} 持有，{session} 无权驳回；"
                             "跨会话只有在交接点（checkpoint 之后、resume 之前）才可驳回")
        handoff_session = (entry.get("handoff") or {}).get("session")
        if session == handoff_session:
            raise ValueError("驳回会话与交接会话相同：交接点驳回须在新会话进行")
        if _session_record(state, session):
            raise ValueError(f"会话 {session} 已使用过：不得重复使用会话标识")
    if entry["status"] != "awaiting_approval" and not at_handoff:
        raise ValueError(f"phase {phase} 不可驳回（status={entry['status']}）")
    record = {"by": _text(by, "by"), "at": _now(), "reason": _text(reason, "reason"),
              "session": session}
    entry["rejections"].append(record)
    entry["status"] = "rejected"
    entry["handoff"] = None
    entry["approval"] = None
    state["awaiting_session_for"] = None
    if session != active:
        state["sessions"].append({"id": session, "opened_at": _now(), "model_class": None,
                                  "model_name": None, "closed_at": None, "phases": []})
        state["active_session"] = session
    _record(state, "reject", phase, record)
    return state


def start(state, phase, session=None):
    """开始阶段：必须已获放行，且前一阶段已 done；须在放行所在会话内开始。"""
    session = _require_session(state, session)
    entry = _entry(state, phase)
    if entry["status"] != "approved":
        raise ValueError(f"phase {phase} not approved for start (status={entry['status']})；"
                         "阶段推进须用户显式放行，不得自行开始")
    ok, why = _prior_done(state, phase)
    if not ok:
        raise ValueError(why)
    approval = entry.get("approval") or {}
    if approval.get("mode") == "cross_session" and approval.get("to_session") != session:
        raise ValueError(f"阶段 {phase} 的放行发生在新会话 {approval.get('to_session')}，"
                         f"当前会话 {session} 不得开始；跨会话须由放行会话自己执行")
    pending = _pending_scope_changes(state)
    if pending:
        raise ValueError("存在未批准的范围变更修订申请："
                         + "、".join(a["id"] for a in pending)
                         + "（先批后动；只读观察不阻断）")
    if entry.get("routing") is None:
        session_record = _session_record(state, session) or {}
        entry["routing"] = {
            "declared_class": session_record.get("model_class"),
            "declared_model": session_record.get("model_name"),
            "expected_class": ROUTING[phase]["model_class"],
            "model_hint": ROUTING[phase]["model_hint"],
            "ack": None,
        }
    entry["status"] = "in_progress"
    entry["started_at"] = _now()
    entry["session"] = session
    _record(state, "start", phase, {"session": session})
    return state


def _read_json_arg(path):
    """读取 CLI 上的 JSON 文件参数；未提供时返回 None。"""
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_json_file(path, label):
    """读取须登记的 JSON 产物；文件不存在或不是绝对路径时直接拒绝。"""
    text = _text(path, label)
    target = Path(text)
    if not target.is_absolute() or not target.is_file():
        raise ValueError(f"{label} 产物不存在或不是绝对路径：{text}"
                         "（只认真实文件，占位路径不算已登记）")
    return json.loads(target.read_text(encoding="utf-8-sig"))


def _read_exploration_arg(path, multi=False):
    """读取单份探索清单参数。

    兼容两种输入：exploration_state.summarize() 的**整份报告**（自动取 gate 字段），
    以及直接传 gate 摘要本身。上游换用哪种都不必改命令。multi=True 时额外接受
    JSON 数组（每份可以是整份报告或 gate 摘要，外层 source/label 透传进 sources），
    用于跨域场景的多份分域清单——被测对象域与独立核验域各有自己的 target_url，
    无法用单份清单表达。CLI 建议改用 _read_exploration_args（--exploration 可重复），
    免去手工拼合并文件。
    """
    raw = _read_json_arg(path)
    if isinstance(raw, list):
        if not multi:
            raise ValueError(
                "探索清单参数是 JSON 数组：多份分域清单请在 CLI 上重复 --exploration"
                "（每份一个文件/路径），或确认这是数组后再走多份路径；"
                "单份 gate 应是 JSON 对象而非数组")
        values = []
        for index, item in enumerate(raw):
            label = None
            if isinstance(item, dict) and "gate" in item:
                label = item.get("source") or item.get("label")
                item = item["gate"]
            if label is not None and isinstance(item, dict):
                item = dict(item, _source_label=str(label))
            if item is None or item == {}:
                raise ValueError(
                    f"exploration gate 数组第 {index + 1} 项为空/None："
                    "未生成的清单不要占位混入数组（会静默丢诊断），"
                    "只登记实际存在的清单")
            if isinstance(item, dict) and "gate" in item:
                raise ValueError(
                    f"exploration gate 数组第 {index + 1} 项仍带外层包装（gate 字段）："
                    "嵌套包装不受支持，请展平后重试")
            values.append(item)
        return values
    if isinstance(raw, dict) and "gate" in raw:
        gate = raw["gate"]
        if gate is None or gate == {}:
            # 与数组混入 None 同一口径：显式报错，不静默降级成"未提供"——
            # 否则用户以为登记了清单，实际登记成 missing，只在 ceiling 上体现。
            raise ValueError("探索清单文件的 gate 字段为空/None：未生成的清单不要登记；"
                             "单个文件请传 summarize 输出的 gate 摘要或完整报告")
        label = raw.get("source") or raw.get("label")
        if label is not None and isinstance(gate, dict):
            gate = dict(gate, _source_label=str(label))
        return gate
    return raw


def _read_exploration_args(paths):
    """读取一个或多个探索清单路径（--exploration action='append' 的结果）。

    每个路径先按 _read_exploration_arg 读取（数组展开、source 标签透传），
    多路径结果合并为一份有序列表。只传一个非数组路径时返回单份 gate，
    保持既有登记形状不变。
    """
    if not paths:
        return None
    values = []
    for path in paths:
        result = _read_exploration_arg(path, multi=True)
        if isinstance(result, list):
            values.extend(result)
        else:
            values.append(result)
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return values


def _exploration_check(gate):
    """探索清单检查入口：单份 gate 走既有判定，多份逐份判定后合并。

    跨域场景（Agent 在 A 域、独立核验页在 B 域）无法用一份同源清单表达，按域拆成
    多份后必须一起登记。合并口径：取最严 ceiling、合并缺口、任何一份缺控件遍历都
    算整体缺——绝不因为"其中一份通过"就解锁验收。每份来源原样带出供追溯。
    """
    if isinstance(gate, list):
        if not gate:
            raise ValueError("exploration gate 数组不能为空")
        for index, item in enumerate(gate):
            if item is None or item == {}:
                raise ValueError(
                    f"exploration gate 数组第 {index + 1} 项为空/None："
                    "未生成的清单不要占位混入数组（会静默丢诊断），只登记实际存在的清单")
            if isinstance(item, dict) and "gate" in item:
                raise ValueError(
                    f"exploration gate 数组第 {index + 1} 项仍带外层包装（gate 字段）："
                    "嵌套包装不受支持，请展平后重试")
        labels = [item.get("_source_label") if isinstance(item, dict) else None for item in gate]
        checked = [_exploration_check_one(
            {k: v for k, v in item.items() if k != "_source_label"} if isinstance(item, dict) else item)
            for item in gate]
        ceiling = "通过"
        for item in checked:
            ceiling = _strictest(ceiling, item["ceiling"])
        gaps, sources = [], []
        # mandatory_attributes 逐键合并取最严：任一份对某属性未闭合，合并后仍视为未闭合；
        # 两份都已闭合但取值不一致时取 covered（not_applicable 是豁免，covered 是已测，取严）。
        # 多个"未闭合"取值之间不定义先后（blocked/unresolved 都算未闭合），合并值取先遇到的
        # 未闭合态即可——判定不受影响，因为每份的未闭合项都已逐份列入 gaps。
        closed = ("covered", "not_applicable")
        merged_attrs = {}
        for item in checked:
            for key, val in (item.get("mandatory_attributes") or {}).items():
                existing = merged_attrs.get(key)
                if existing is None:
                    merged_attrs[key] = val
                elif existing in closed and val not in closed:
                    merged_attrs[key] = val
                elif existing in closed and val == "covered":
                    merged_attrs[key] = "covered"
        for index, item in enumerate(checked):
            label = labels[index]
            tag = f"[{index + 1}{':' + label if label else ''}]"
            for gap in item["gaps"]:
                gaps.append(f"{tag} {gap}")
            source = {"index": index + 1, "ceiling": item["ceiling"],
                      "gaps": item["gaps"], "message": item.get("message")}
            if label:
                source["source"] = label
            sources.append(source)
        if ceiling != "通过" and not gaps:
            gaps.append("多份探索清单均未给出可核对缺口")
        return {"state": "provided", "ceiling": ceiling,
                "traversal_recorded": all(item["traversal_recorded"] for item in checked),
                "mandatory_attributes": merged_attrs, "gaps": gaps,
                "message": "多份分域探索清单合并判定：" + "；".join(
                    f"[{i + 1}{':' + labels[i] if labels[i] else ''}]{item['ceiling']}"
                    for i, item in enumerate(checked)),
                "reasons": [] if ceiling == "通过" else
                           [f"多份探索清单不达标（{ceiling}）：{'；'.join(gaps)}"],
                "sources": sources}

    return _exploration_check_one(gate)


def _exploration_check_one(gate):
    """执行阶段收尾时的探索清单检查（scripts/exploration_state.py 的 gate 摘要）。

    存在目的：SKILL 与 exploration-runtime.md 把「从未生成 exploration.json 的批次不得
    给出已测类结论」写成硬前置，但此前 phase_gate 里 grep 不到 exploration 字样——
    规则写在文档、强制落在脚本，两者没接上就等于只有文档。实测代价：一轮完整验收
    从未生成清单，complete/submit/checkpoint 三步全过、零告警，missing_views /
    mandatory_attributes / EXP-TRAV- 三项从未被核对，报告里却用了「已实测」的措辞。

    兼容策略：不提供 gate 时**只告警不拒绝**（存量批次与既有调用方不受破坏），但把
    ceiling 记为「待核实」并写明缺口，使"没做"与"做了且通过"在结论上可区分。
    """
    if gate is None:
        return {"state": "missing", "ceiling": "待核实", "traversal_recorded": False,
                "gaps": [], "reasons": ["execute 阶段收尾未提供探索清单 gate 结论："
                                        "missing_views / mandatory_attributes / EXP-TRAV- "
                                        "未经机械检查，已测类结论上限压待核实"]}
    if not isinstance(gate, dict):
        raise ValueError("exploration gate 必须为对象（exploration_state.summarize() 的 gate 字段）")
    if not gate:
        raise ValueError("exploration gate 为空对象：未生成清单不要登记，"
                         "单个文件请传 summarize 输出的 gate 摘要或完整报告")
    ceiling = gate.get("verdict_ceiling")
    if ceiling is None and "verdict_ceiling" not in gate \
            and "schema_version" in gate and "items" in gate:
        # 原始清单形态（schema_version/context/items，无 gate 结论）被直接当成 gate
        # 登记——不给出笼统的 verdict_ceiling 报错，指明先跑 summarize。
        raise ValueError("传入的是原始探索清单（schema_version/items 形态），尚未生成 gate 结论："
                         "请先运行 exploration_state.py summarize 生成 gate 摘要，"
                         "或直接传 summarize 的完整报告（自动取 gate 字段）")
    # 与 _attack_check 保持同一口径：verdict_ceiling 有三种合法取值。exploration_state
    # 目前只产出 通过/待核实（FAIL 项也走待核实），但只收两种取值会在上游改用「不通过」
    # 时报一句误导性的「缺 verdict_ceiling」——字段其实在，是取值不在白名单。
    if ceiling not in CEILINGS:
        raise ValueError(f"exploration gate 的 verdict_ceiling 非法（需为 {list(CEILINGS)}），"
                         f"got {ceiling!r}")
    gaps = []
    if gate.get("ready_pending"):
        gaps.append(f"ready_pending={gate['ready_pending']}")
    if gate.get("missing_views"):
        gaps.append("missing_views=" + ",".join(gate["missing_views"]))
    if gate.get("thin_views"):
        gaps.append("thin_views=" + ",".join(gate["thin_views"]))
    if gate.get("failed_items"):
        gaps.append("failed_items=" + ",".join(str(x) for x in gate["failed_items"]))
    if not gate.get("traversal_recorded"):
        gaps.append("未记录 EXP-TRAV- 控件遍历条目")
    unresolved = [k for k, v in (gate.get("mandatory_attributes") or {}).items()
                  if v not in ("covered", "not_applicable")]
    if unresolved:
        gaps.append("mandatory_attributes 未闭合=" + ",".join(unresolved))
    if ceiling != "通过" and not gaps:
        # 兜底：上游给出了待核实/不通过却没透出可核对缺口时，把原文带出来，不允许空白原因
        gaps.append(str(gate.get("message") or "gate 判定非通过（原因见 gate.message）"))
    # 上游自相矛盾（ceiling=通过 却带着缺口）时按缺口从严：门禁不能因为一句"通过"就把
    # 具体缺口放过——缺口是机械可核对的，ceiling 只是一个词。
    if ceiling == "通过" and gaps:
        ceiling = "待核实"
    return {"state": "provided", "ceiling": ceiling,
            "traversal_recorded": bool(gate.get("traversal_recorded")),
            "mandatory_attributes": gate.get("mandatory_attributes") or {},
            "gaps": gaps,
            "message": gate.get("message"),
            "reasons": [] if ceiling == "通过" else
                       [f"探索清单不达标（{ceiling}）：{'；'.join(gaps)}"]}


def _attack_check(coverage):
    """执行阶段收尾时的攻击覆盖检查（scripts/attack_coverage.py 的结论）。

    存在目的：一次 154 项的“全量逐项”执行，143 项走正向路径、风险面关联 0 条、
    方法标注 0 条，只因它没走过这里——清单式验收直接进了裁决，把“铺得全”当成
    “打得深”。收集阶段收尾必须交结论，缺失即压上限，不拒终结阶段。
    """
    if coverage is None:
        return {"state": "missing", "ceiling": "待核实", "density": None, "gaps": [],
                "reasons": ["execute 阶段收尾未提供 attack_coverage 结论："
                            "攻击密度与判据独立性未经机械检查"]}
    if not isinstance(coverage, dict):
        raise ValueError("attack_coverage must be a JSON object（attack_coverage.py 的输出）")
    ceiling = coverage.get("verdict_ceiling")
    if ceiling not in CEILINGS:
        raise ValueError(f"attack_coverage 缺 verdict_ceiling（需为 {list(CEILINGS)}）")
    gaps = coverage.get("gaps")
    if not isinstance(gaps, list):
        raise ValueError("attack_coverage 缺 gaps 数组（缺口须原样呈现）")
    # 上游自相矛盾（ceiling=通过 却带着缺口）时按缺口从严：缺口机械可核对，ceiling 只是一个词。
    if ceiling == "通过" and gaps:
        ceiling = "待核实"
    # 弱化调用留痕：上游没做 plan 交叉核对（未提供 plan 或风险面集合为空）时，
    # 攻击面覆盖结论无法证明"与计划对过账"，按缺口压上限。
    # 不拒绝文件本身——与"压上限而非拒绝"的口径一致。
    reasons = [] if ceiling == "通过" else \
              [f"攻击覆盖不达标（{ceiling}）：{[g.get('code') if isinstance(g, dict) else g for g in gaps]}"]
    plan_state = coverage.get("plan_check_state")
    if plan_state is not None and plan_state != "checked":
        ceiling = _strictest(ceiling, "待核实")
        reasons.append(
            "攻击覆盖未与 plan 交叉核对（plan_check_state=%s）：属弱化调用，"
            "攻击面覆盖结论无法证明与计划对过账，结论上限压待核实" % plan_state)
    return {"state": "provided", "ceiling": ceiling,
            "density": coverage.get("attack_density"), "gaps": list(gaps),
            "plan_check_state": plan_state,
            "atk_checked": coverage.get("atk_checked"),
            # 判据版本随结论一起冻结：阶段收尾快照要在几个月后还能说明
            # "这个通过是哪一版判据判的"，否则复算差异无从归因。
            "tool_version": coverage.get("tool_version"),
            "schema": coverage.get("schema"),
            "reasons": reasons}


def revise(state, phase, attack_coverage=None, exploration=None, note=None, session=None,
           allow_relax=False):
    """对已完成阶段的机械结论做**补正**（不改阶段状态、不改 completed_at）。

    存在目的：execute 在 S2 收尾时冻结 attack_coverage / exploration 快照；交接包明写
    「下一阶段补 SURF-xx」，S3 补齐后重跑门禁得「通过」，却因 complete 只能调一次而
    无法回写——check-verdict 永远读过期快照，报「不达标」而实际已达标。实测代价：一轮
    完整验收的最终门禁结论被锁在过期快照上。

    约束（缺一即拒）：
    1. 只有 execute 阶段有这两个字段——check_verdict 也只读 execute，写到别的阶段等于
       制造门禁看不见的死数据，且可被误当作"已补正"的凭据。
    2. 不得把未通过的结论**升级为「通过」**：那是自行解锁验收。补正的正当用途是"补齐探针
       后重新机械判定"，重跑既可能抬升也可能压低，但抬到「通过」这一步意味着报告可以收尾，
       必须留一个人能看到的开关。确需如此时带 allow_relax=True，并在 note 写明依据。
       「不通过 ↔ 待核实」之间的调整不拦——那是缺陷归因变化的正常产物，拦住会把合法补正
       一起堵死。
    3. 必须留痕：history 记 revise 事件，其中 transitions 记下每次 ceiling 迁移。
    """
    session = _require_session(state, session)
    if phase != "execute":
        raise ValueError(f"revise 只支持 execute 阶段的机械结论（attack_coverage / exploration）；"
                         f"当前 phase={phase}：check_verdict 只读 execute，写到其他阶段是死数据")
    entry = _entry(state, phase)
    if entry["status"] != "done":
        raise ValueError(f"阶段 {phase} 尚未完成（status={entry['status']}）："
                         "revise 只用于补正已完成阶段的机械结论")
    if attack_coverage is None and exploration is None:
        raise ValueError("revise 至少要提供 --attack-coverage 或 --exploration 之一")

    changed, relaxed = [], {}
    for field, given, checker in (("attack_coverage", attack_coverage, _attack_check),
                                  ("exploration", exploration, _exploration_check)):
        if given is None:
            continue
        old = (entry.get(field) or {}).get("ceiling")
        checked = checker(given)
        new = checked.get("ceiling")
        # 只拦「升级为通过」这一种迁移。真正有害的是把未通过的结论改成通过（解锁验收），
        # 而「不通过 ↔ 待核实」之间的调整是重跑门禁的正常产物（例如缺陷被归因到环境），
        # 一律拦住会把合法补正也堵死——这正是本条修复第一版的错误。
        if new == "通过" and old and old != "通过" and not allow_relax:
            raise ValueError(
                f"{field} 结论不能从「{old}」升级为「通过」：补正不得自行解锁验收结论。"
                "若确属补齐探针后重跑门禁所得，请带 --allow-relax 并在 note 写明依据")
        if old and old != new:
            relaxed[field] = f"{old} -> {new}"
        checked["revised"] = True
        entry[field] = checked
        changed.append(field)

    _record(state, "revise", phase, {"fields": changed, "note": note,
                                     "session": session, "transitions": relaxed})
    return state


def complete(state, phase, note=None, session=None, attack_coverage=None,
             exploration=None, artifacts=None, assumptions=None, regression=None,
             collection=None):
    session = _require_session(state, session)
    entry = _entry(state, phase)
    if entry["status"] != "in_progress":
        raise ValueError(f"phase {phase} is not in progress (status={entry['status']})")
    if entry.get("session") and entry["session"] != session:
        raise ValueError(f"阶段 {phase} 在会话 {entry['session']} 中开始，不得在 {session} 中结束")
    if phase == "execute":
        entry["attack_coverage"] = _attack_check(attack_coverage)
        entry["exploration"] = _exploration_check(exploration)
        # v1.49.0：收尾消费真实采集。结构违规 = 采集事实不成立，拒绝收尾（修正后再交）；
        # 未提供 = 保留"受限收尾"能力（预算耗尽/外部依赖仍可交付已取得证据），但逐项
        # 留痕并把该缺口带进 check_verdict——"没交"与"交了且合规"在结论上可区分。
        if collection is not None:
            records = _load_records(collection)
            violations = scan_collection_records(records)
            if violations:
                raise ValueError(
                    f"采集记录存在 {len(violations)} 处结构违规，未收尾："
                    f"{violations[0].get('code')}（{violations[0].get('message', '')[:80]}）；"
                    "修正采集记录后重新 complete——结果台账不能改名充当采集输入")
            entry["collection"] = {"state": "provided", "records": len(records),
                                   "violations": [], "checked_at": _now()}
        else:
            entry["collection"] = {"state": "not_provided", "checked_at": _now()}
    elif attack_coverage is not None or exploration is not None or collection is not None:
        # 这两个结论只对 execute 有意义，check_verdict 也只读 execute。静默丢弃会让
        # 调用方以为"交了、被记录了"，实际没进任何门禁——收尾时最不该有的错觉。
        extra = [n for n, v in (("--attack-coverage", attack_coverage),
                                ("--exploration", exploration),
                                ("--collection", collection)) if v is not None]
        raise ValueError(f"{' / '.join(extra)} 只对 execute 阶段生效（当前 phase={phase}）："
                         "这两个结论是执行期的机械检查，check_verdict 只读 execute")
    # 裁决前置的两份清单：允许在 execute 收尾时提前登记，让「终检会索要什么」在检查点
    # 就可见。此前只有 check_verdict 才索要，执行期结束毫无提示，被迫在下一会话补登
    # ——与「每阶段停会话换模型」直接冲突。登记只做结构校验，不替代终检的独立判断：
    # 终检仍以自身入参为准，登记过不代表假设已证伪、影响面已评估。
    for name, raw, checker in (("assumptions", assumptions, _assumption_reasons),
                               ("regression", regression, _regression_reasons)):
        if raw is None:
            continue
        if phase == "plan":
            raise ValueError(f"{name} 只对 execute/adjudicate 阶段生效"
                             "（裁决前置清单须在执行产出后登记）")
        payload = _read_json_file(raw, name)
        problems = checker(payload, structure_only=True)
        if problems:
            raise ValueError(f"{name} 结构不合规，未登记：{problems[0]}")
        entry[name] = {"registered": True, "path": str(Path(raw).resolve()), "at": _now()}
    # 产物归属：此前 complete 不接受 --artifact，plan.artifacts 恒为 []，交接包「本阶段
    # 产物」输出「（未登记产物路径）」——交接包让下一会话看不到上一阶段交了什么；而
    # submit 下一阶段时登记的文件被记到下一阶段名下，产物跨阶段串账。
    # 与 submit 同一强度校验：只认真实存在的绝对路径。收尾产物若允许占位路径，「本阶段
    # 产物」就会列出一堆不存在的文件，交接包比空着更误导。
    if artifacts:
        merged = list(entry.get("artifacts") or [])
        for item in artifacts:
            text = _text(item, "artifact")
            path = Path(text)
            if not path.is_absolute() or not path.is_file():
                raise ValueError(f"本阶段产物不存在或不是绝对路径：{text}"
                                 "（只认真实文件，占位路径不算做过）")
            if text not in merged:
                merged.append(text)
        entry["artifacts"] = merged
    entry["status"] = "done"
    entry["completed_at"] = _now()
    _record(state, "complete", phase, {"note": note, "session": session,
                                       "artifacts": len(artifacts or [])})
    return state


def _pending_verdict_inputs(state):
    """裁决终检将索要、但尚未登记的前置清单。

    这两份清单此前只在 check_verdict 才被索要，执行期结束没有任何提示，补登只能
    发生在下一会话——与「每阶段停会话换模型」直接冲突。交接包显式列出它们，让下
    一会话在开工前就知道要交什么；登记与否不影响终检的独立判定。
    """
    labels = (("assumptions", "风险假设清算（check-verdict --assumptions）"),
              ("regression", "回归影响面结论（check-verdict --regression）"))
    pending = []
    for name, label in labels:
        registered = any((state["phases"][phase].get(name) or {}).get("registered")
                         for phase in PHASES)
        if not registered:
            pending.append(label)
    return pending


def checkpoint(state, phase, handoff_dir, note=None, artifacts=None, session=None):
    """阶段收尾：写交接包并停在 awaiting_new_session——**本会话到此结束**。

    这是“每阶段停止会话、换会话换模型”的机械落点：
    - 必须先把下一阶段 submit 好（最后一阶段除外），否则交接包没有明确的下一步；
    - 生成确认码并打印确认文本，用户需在**新会话**里原样粘回；
    - 状态置 awaiting_new_session 后，本会话任何推进命令都会被拒。
    """
    session = _require_session(state, session)
    entry = _entry(state, phase)
    if entry["status"] not in {"in_progress", "done"}:
        raise ValueError(f"phase {phase} 不能收尾（status={entry['status']}）；"
                         "须先 start 并完成本阶段工作")
    pending = _pending_scope_changes(state)
    if pending:
        raise ValueError("存在未批准的范围变更修订申请："
                         + "、".join(a["id"] for a in pending)
                         + "（先处理范围变更再 checkpoint；只读观察不阻断）")
    handoff_dir = Path(handoff_dir)
    if not handoff_dir.is_dir():
        raise ValueError(f"交接目录必须已存在：{handoff_dir}")
    nxt = NEXT_PHASE[phase]
    if nxt:
        nxt_status = state["phases"][nxt]["status"]
        if nxt_status != "awaiting_approval":
            raise ValueError(f"下一阶段 {nxt} 尚未提交申请（status={nxt_status}）："
                             f"须先 submit {nxt} 说明下一步要做什么，再 checkpoint")
    artifacts = [a for a in (artifacts or []) if isinstance(a, str)]
    token = hashlib.sha256((_state_digest(state) + phase + _now()).encode("utf-8")).hexdigest()[:8].upper()
    confirm_text = None
    if nxt:
        confirm_text = HANDOFF_CONFIRM_TEMPLATE.format(
            task=state.get("task"), phase=nxt, label=PHASE_LABELS[nxt], token=token,
            model_hint=ROUTING[nxt]["model_hint"], model_class=ROUTING[nxt]["model_class"])
    handoff = {
        "schema": "phase-handoff/1",
        "task": state.get("task"),
        "from_phase": phase,
        "from_session": session,
        "next_phase": nxt,
        "next_phase_label": PHASE_LABELS[nxt] if nxt else None,
        "token": token,
        "confirm_text": confirm_text,
        "next_routing": ({"model_class": ROUTING[nxt]["model_class"],
                          "model_hint": ROUTING[nxt]["model_hint"]} if nxt else None),
        "artifacts": entry.get("artifacts") or [],
        "extra_artifacts": artifacts,
        "pending_inputs": _pending_verdict_inputs(state),
        "note": (note.strip() if isinstance(note, str) and note.strip() else None),
        "created_at": _now(),
        "resume_command": (f"python scripts/phase_gate.py resume {phase}-state --phase {nxt} "
                           f"--session <新会话标识> --user-confirm \"<粘贴确认文本>\" "
                           f"--model-class {ROUTING[nxt]['model_class']}"
                           if nxt else None),
    }
    md_path = handoff_dir / HANDOFF_FILE.format(phase=phase)
    json_path = handoff_dir / HANDOFF_JSON.format(phase=phase)
    md_path.write_text(_render_handoff_md(handoff), encoding="utf-8")
    json_path.write_text(json.dumps(handoff, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    entry["handoff"] = {"at": _now(), "session": session, "token": token,
                        "markdown": str(md_path), "json": str(json_path),
                        "consumed": False, "next_phase": nxt}
    entry["status"] = "done"
    entry["completed_at"] = entry.get("completed_at") or _now()
    # 交接未消费：下一个阶段只能在**新会话**里 resume，本会话已无权推进
    state["awaiting_session_for"] = nxt
    _record(state, "checkpoint", phase, {"session": session, "token": token,
                                         "next_phase": nxt,
                                         "markdown": str(md_path),
                                         "note": handoff["note"]})
    return state


def _render_handoff_md(handoff):
    lines = [
        f"# 阶段交接包 · {handoff['task']}",
        "",
        f"- 已结束阶段：`{handoff['from_phase']}`（{PHASE_LABELS[handoff['from_phase']]}）",
        f"- 交接会话：`{handoff['from_session']}`",
        f"- 下一阶段：`{handoff['next_phase']}`（{handoff['next_phase_label'] or '无'}）",
        f"- 生成时间：{handoff['created_at']}",
        "",
    ]
    if handoff["next_routing"]:
        lines += [
            f"- **下一阶段建议模型**：{handoff['next_routing']['model_hint']}"
            f"（类别 `{handoff['next_routing']['model_class']}`）",
            "",
        ]
    lines += ["## 本阶段产物", ""]
    items = (handoff.get("artifacts") or []) + (handoff.get("extra_artifacts") or [])
    lines += [f"- `{item}`" for item in items] or ["- （未登记产物路径）"]
    pending_inputs = handoff.get("pending_inputs") or []
    if pending_inputs:
        lines += ["", "## 下一阶段需提交的前置清单", "",
                  "裁决终检会索要以下输入，本阶段尚未登记；下一会话须先补齐再给技术结论：", ""]
        lines += [f"- {item}" for item in pending_inputs]
    if handoff.get("note"):
        lines += ["", "## 交接说明", "", handoff["note"]]
    lines += [
        "",
        "## 操作步骤（必须由用户完成）",
        "",
        "1. **结束当前会话**；",
        f"2. 新开一个会话，把执行模型切换为 {handoff['next_routing']['model_hint'] if handoff['next_routing'] else '任意'}"
        "（同会话内换模型不视为跨阶段交接）；",
        "3. 在新会话里把下面这段确认文本原样发出：",
        "",
        "```text",
        handoff["confirm_text"] or "（无下一阶段，本任务三阶段已结束）",
        "```",
        "",
        "4. 新会话据此执行 resume 后，才允许 start 下一阶段。",
        "",
        "## 确认码",
        "",
        f"`{handoff['token']}`",
        "",
    ]
    return "\n".join(lines)


def resume(state, phase, new_session, user_confirm, model_class=None, model_name=None,
           note=None):
    """新会话续跑：凭用户粘贴的确认文本放行某阶段。这是换会话（换模型）的唯一入口。"""
    entry = _entry(state, phase)
    waiting_for = state.get("awaiting_session_for")
    if waiting_for != phase:
        if entry["status"] == "awaiting_approval":
            prior = PHASES[PHASES.index(phase) - 1] if PHASES.index(phase) else "（起点）"
            raise ValueError(f"阶段 {phase} 处于 awaiting_approval：须先由上一阶段"
                             f"（{prior}）的会话执行 checkpoint 生成交接包，再在新会话 resume")
        raise ValueError(f"phase {phase} 不在等待新会话状态（awaiting_session_for={waiting_for}, "
                         f"status={entry['status']}）；跨会话续跑只能接在 checkpoint 之后")
    if entry.get("status") not in {"awaiting_approval", "pending", "rejected"}:
        raise ValueError(f"phase {phase} 状态为 {entry['status']}：交接点应停在待放行，"
                         "不应已放行或已开始；疑似跨会话推进被绕过")
    prior = PHASES[PHASES.index(phase) - 1]
    prior_entry = state["phases"][prior]
    handoff = prior_entry.get("handoff") or {}
    if handoff.get("next_phase") != phase:
        raise ValueError(f"上一阶段 {prior} 的交接包未指向 {phase}：交接链断裂，须重新 checkpoint {prior}")
    if handoff.get("consumed"):
        raise ValueError(f"阶段 {prior} 的交接包已被消费：不得重复放行")
    new_session = _text(new_session, "session")
    if new_session == handoff.get("session"):
        raise ValueError(f"新会话标识与交接会话 {new_session} 相同："
                         "阶段之间必须换会话——同会话续跑等于没换模型，不给过")
    if _session_record(state, new_session):
        raise ValueError(f"会话 {new_session} 已使用过：不同阶段必须各自使用新会话")
    ok, why = _prior_done(state, phase)
    if not ok:
        raise ValueError(why)
    # 范围变更先批后动：跨会话 resume 同样是"继续往前做"，未批准的范围变更
    # 不得借换会话绕过。只读观察凭据齐备即可继续，不在此列。
    pending = _pending_scope_changes(state)
    if pending:
        raise ValueError("存在未批准的范围变更修订申请："
                         + "、".join(a["id"] for a in pending)
                         + "（先批后动；不得借跨会话 resume 绕过）")
    confirm = _text(user_confirm, "user_confirm")
    token = str(handoff.get("token") or "").upper()
    if not token:
        raise ValueError("交接包缺少确认码：该阶段未正常 checkpoint，须重新收尾")
    if token not in confirm.upper():
        raise ValueError(f"用户确认文本未包含交接确认码 {token}："
                         "须把交接包里的确认文本原样粘回，不得由智能体代填或改写")
    approval = {"by": "user", "via": "new_session", "mode": "cross_session",
                "at": _now(), "note": confirm,
                "from_session": handoff.get("session"), "to_session": new_session,
                "confirm_token": token}
    entry["approval"] = approval
    entry["routing"] = {
        "declared_class": (_text(model_class, "model_class").lower() if model_class else None),
        "declared_model": (model_name.strip() if isinstance(model_name, str) and model_name.strip()
                           else None),
        "expected_class": ROUTING[phase]["model_class"],
        "model_hint": ROUTING[phase]["model_hint"],
        "ack": None,
    }
    entry["status"] = "approved"
    handoff["consumed"] = True
    handoff["consumed_at"] = _now()
    handoff["consumed_by_session"] = new_session
    prior_entry["handoff"] = handoff
    state["awaiting_session_for"] = None
    for item in state["sessions"]:
        if item.get("id") == handoff.get("session"):
            item["closed_at"] = _now()
    state["sessions"].append({"id": new_session, "opened_at": _now(),
                              "model_class": entry["routing"]["declared_class"],
                              "model_name": entry["routing"]["declared_model"],
                              "closed_at": None, "phases": []})
    state["active_session"] = new_session
    _record(state, "resume", phase, dict(approval, note=confirm,
                                         declared_class=entry["routing"]["declared_class"],
                                         declared_model=entry["routing"]["declared_model"]))
    return state


def _pending_scope_changes(state):
    """尚未批准的范围变更修订（须先批后动的那些）。

    只读观察（`readonly_lead`）**不在此列**——固定授权范围内的只读观察
    凭据齐备即可继续，阻断它等于让"如实登记只读线索"变得比"先做后说"更糟。

    判定走分类契约的单一事实源（scripts/amendment_class.py 的 `is_blocking`），
    与 execution_ledger 同一口径：类别决定"需不需要先批"，状态决定"批了没有"。
    """
    return [a for a in state.get("amendments") or [] if _AMD.is_blocking(a)]


def request_amendment(state, phase, by, note, session=None, declaration=None):
    """阶段中发现的计划缺口：登记修订申请。

    **分类决定处置**（契约见 scripts/amendment_class.py，单一事实源）：
    - `readonly_lead`：固定授权范围内的只读观察，凭据齐备 → **登记后即可继续**，
      状态记 `approved_readonly`，不阻断阶段推进；
    - `scope_change`：新增写入、角色或权限扩展、环境变更、超预算、
      属性未知或凭据不全 → **先批后动**，状态记 `awaiting_approval`，阻断推进。

    旧调用（不传 declaration）保持原行为：一律按 `scope_change` 处理并等待批准——
    没有分类信息时从严，是"先批后动"而非"先做后说"。

    分类由登记事实机械判定，不认自然语言自述：`path_type`、HTTP 方法或
    「未看到写操作」单独都不能作为只读凭据。
    """
    _require_session(state, session)
    _phase(phase)
    ok, message = _AMD.validate_declaration(declaration or {})
    if not ok:
        raise ValueError(message)
    kind, reasons = _AMD.classify(declaration or {})
    readonly = kind == "readonly_lead"
    record = {"id": f"AMD-{len(state['amendments']) + 1}", "phase": phase,
              "kind": kind, "status": "approved_readonly" if readonly else "awaiting_approval",
              "requested_by": _text(by, "by"),
              "requested_at": _now(), "note": _text(note, "note"),
              "declaration": declaration or None,
              "classification_reasons": reasons,
              "approval": None}
    if readonly:
        # 只读观察可继续，但这**不是人工放行**：单独记 auto_approval，不占用
        # approval 字段——approval 的语义是"人确认过"，两者混同一字段会让审计
        # 分不清哪些走的是人工闸门、哪些是分类契约按凭据机械放行。
        # 这里的 by 是登记该线索的执行方（可以是智能体），不是放行人，
        # 故不过 _approver()：没有人工放行发生，就不该留下人工放行的痕迹。
        record["auto_approval"] = {"by": record["requested_by"], "at": _now(),
                                   "mode": "readonly_lead", "auto": True,
                                   "basis": "分类契约按凭据机械放行：scope/environment/role/"
                                            "allowed_actions/no_state_change 齐备且类型合格",
                                   "reasons": reasons}
    state["amendments"].append(record)
    _record(state, "amendment_request", phase,
            {"id": record["id"], "note": record["note"], "kind": kind,
             "status": record["status"], "reasons": reasons})
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


def _assumption_reasons(payload, structure_only=False):
    """风险假设清算：假设必须逐条给出证伪动作与结果，未证伪的必须留成显式风险。

    只有需求可疑点有机械门禁是不够的——「应该没问题」「暂不涉及」这类判断如果不需要
    落成可证伪条目，就会一直停在文字里，既不验证也不登记缺口。空清单允许，但必须
    显式声明 reviewed=true，避免"没写"与"没有"被混为一谈。

    structure_only=True 时只校验结构（字段齐备、result 取值合法），不因 result=open
    报错——那是「假设尚未证伪」的内容状态，允许提前登记，但会压结论上限。
    """
    if payload is None:
        if structure_only:
            return []
        return ["未提供风险假设清算（check-verdict --assumptions）："
                "无法区分「本来没有假设」与「没审」"]
    if not isinstance(payload, dict):
        raise ValueError("assumptions 必须为 JSON 对象")
    if payload.get("reviewed") is not True:
        return ["风险假设清单未声明 reviewed=true：空数组不等于已审阅"]
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("assumptions.items 必须为数组（无假设时给空数组）")
    reasons = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            reasons.append(f"assumptions.items[{index}] 不是对象")
            continue
        missing = [k for k in ("id", "assumption", "basis", "falsify_action", "result")
                   if not str(item.get(k) or "").strip()]
        if missing:
            reasons.append(f"assumptions.items[{index}] 缺 {'/'.join(missing)}")
            continue
        result = item["result"]
        if result == "open":
            if structure_only:
                continue
            reasons.append(f"风险假设 {item['id']} 仍未证伪（result=open）：{item['assumption']}")
        elif result not in {"confirmed", "refuted"}:
            reasons.append(f"风险假设 {item['id']} 的 result 必须为 confirmed/refuted/open")
    return reasons


def _regression_reasons(payload, structure_only=False):
    """回归影响面：有变更就得有回归候选，或者写明为什么认为无影响。

    变更 → 依赖 → 旅程 → 用例这条链此前只存在于文档：不生成回归清单也能拿到"通过"。

    structure_only=True 时只校验结构（候选带依据），不因"有变更面却无候选"报错——
    那是内容缺口，允许提前登记，但会在裁决终检压结论上限。
    """
    if payload is None:
        if structure_only:
            return []
        return ["未提供回归影响面结论（check-verdict --regression）："
                "「本次改动是否破坏旧功能」没有机械要求"]
    if not isinstance(payload, dict):
        raise ValueError("regression 必须为 JSON 对象")
    changed = payload.get("changed_areas", [])
    candidates = payload.get("candidates", [])
    if not isinstance(changed, list) or not isinstance(candidates, list):
        raise ValueError("regression 的 changed_areas/candidates 必须为数组")
    reason = str(payload.get("no_impact_reason") or "").strip()
    if changed and not candidates and not reason:
        if structure_only:
            return []
        return [f"声明了 {len(changed)} 个变更面但回归候选为空，且未写 no_impact_reason："
                "影响面未被评估"]
    if changed and candidates:
        empty_basis = [str(c.get("id") or "?") for c in candidates
                       if not isinstance(c, dict) or not str(c.get("basis") or "").strip()]
        if empty_basis:
            # 候选缺依据是结构问题：两种口径下都阻断登记
            return [f"回归候选缺依据（basis，指向依赖/旅程来源）：{empty_basis[:5]}"]
    if not changed and not reason:
        if structure_only:
            return []
        return ["未声明变更面（changed_areas 空）且未写 no_impact_reason"]
    return []


def check_verdict(state, assumptions=None, regression=None):
    """裁决前置检查：返回是否允许给技术结论，以及阶段层面的结论上限。"""
    reasons, warnings = [], []
    for phase in PHASES:
        status = state["phases"][phase]["status"]
        if status != "done":
            reasons.append(f"{phase}（{PHASE_LABELS[phase]}）状态为 {status}，未完成")
    for amendment in state["amendments"]:
        # 只读观察不阻断裁决——它凭据齐备即可执行；
        # 未批准的范围变更（awaiting_approval）阻断，保留先批后动。
        # 判定走分类契约单一事实源，与 start/checkpoint/resume 及台账摘要同口径。
        if _AMD.is_blocking(amendment):
            reasons.append(f"阶段修订申请 {amendment['id']} 未获批准"
                           f"（分类：{amendment.get('kind') or 'scope_change'}）：{amendment['note']}")
    # 同会话放行：无法换模型，也没有独立的“人看过交接包”环节
    same_session_phases = [phase for phase in PHASES
                           if ((state["phases"][phase].get("approval") or {}).get("mode")
                               == "same_session")]
    for phase in same_session_phases:
        reasons.append(f"{phase}（{PHASE_LABELS[phase]}）为同会话放行：未跨会话交接，"
                       "不满足「每阶段停止会话、新会话换模型续跑」的要求")
    if state.get("awaiting_session_for"):
        reasons.append(f"交接未消费：阶段 {state['awaiting_session_for']} 仍等新会话 resume，"
                       "本会话不得继续推进")
    # 攻击覆盖率：执行阶段必须交出机械结论，否则“铺得全”会当成“打得深”过门
    attack = (state["phases"].get("execute") or {}).get("attack_coverage") or {}
    if attack.get("state") != "provided":
        reasons.append("execute 阶段未提供攻击覆盖结论（attack_coverage）："
                       "无法区分“用例铺得全”与“攻击面打得深”，结论上限压待核实")
    elif attack.get("ceiling") != "通过":
        reasons.extend(attack.get("reasons") or ["攻击覆盖不达标，结论上限压待核实"])
    # 探索清单：SKILL 写成硬前置却长期没有检查点（DEV-1119-08）。缺失只告警不拒绝，
    # 但缺失与「做了且通过」必须在结论上可区分——否则规则仍是纸面的。
    explor = (state["phases"].get("execute") or {}).get("exploration") or {}
    if explor.get("state") != "provided":
        reasons.append("execute 阶段未提供探索清单 gate 结论："
                       "missing_views / mandatory_attributes / EXP-TRAV- 未经机械检查，"
                       "已测类结论上限压待核实")
    elif explor.get("ceiling") != "通过":
        reasons.extend(explor.get("reasons") or ["探索清单不达标，结论上限压待核实"])
    # 采集合规（v1.49.0）：收尾未消费采集 = 采集事实未经结构校验，裁决无法按轨迹复核。
    # 不拒绝历史批次（旧状态文件没有该字段），但把缺口写进原因与上限。
    collection = (state["phases"].get("execute") or {}).get("collection")
    if collection is None:
        reasons.append("execute 阶段未登记采集合规结论（旧批次状态文件，按原契约披露，"
                       "本轮采集未经 scan-collection 校验）")
    elif collection.get("state") != "provided":
        reasons.append("execute 阶段收尾未提供采集记录（--collection）：采集事实未经校验，"
                       "裁决复核与「轨迹为准」缺少输入，结论上限压待核实")
    reasons.extend(_assumption_reasons(assumptions))
    reasons.extend(_regression_reasons(regression))
    routing_warnings = _routing_warnings(state)
    warnings.extend(routing_warnings)
    if any(w["code"] == "routing_mismatch" for w in routing_warnings):
        reasons.append("存在未确认的模型路由误配：阶段质量前提不成立，结论上限压待核实")
    # 未放行时至少压到「待核实」；若上游已算出更严的「不通过」，不得降级成待核实。
    ceiling = "待核实"
    if attack.get("ceiling") in CEILINGS:
        ceiling = _strictest(ceiling, attack["ceiling"])
    if explor.get("ceiling") in CEILINGS:
        ceiling = _strictest(ceiling, explor["ceiling"])
    allowed = not reasons
    return {
        "schema": "phase-verdict-check/1",
        "task": state.get("task"),
        "verdict_allowed": allowed,
        "phase_ceiling": None if allowed else ceiling,
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
    waiting_for = state.get("awaiting_session_for")
    if waiting_for:
        prior = PHASES[PHASES.index(waiting_for) - 1]
        handoff = (state["phases"][prior].get("handoff") or {})
        return (f"阶段 {prior} 已收尾，交接包 {handoff.get('markdown')}："
                f"**请结束本会话**，新开会话（切换为 {ROUTING[waiting_for]['model_hint']}）后执行 "
                f"resume {waiting_for}，并把交接包里的确认文本原样发回")
    for phase in PHASES:
        entry = state["phases"][phase]
        status = entry["status"]
        if status == "pending":
            return f"提交阶段 {phase}（{PHASE_LABELS[phase]}）申请：由上一阶段会话 submit {phase}"
        if status == "rejected":
            return f"阶段 {phase} 被驳回：按驳回意见修订后重新 submit {phase}"
        if status == "awaiting_approval":
            return (f"阶段 {phase}（{PHASE_LABELS[phase]}）等待放行：先完成上一阶段 checkpoint，"
                    f"再在新会话 resume {phase}")
        if status == "approved":
            return (f"阶段 {phase}（{PHASE_LABELS[phase]}）已获放行，"
                    f"由会话 {entry.get('approval', {}).get('to_session') or state.get('active_session')} 开始")
        if status == "in_progress":
            return (f"阶段 {phase}（{PHASE_LABELS[phase]}）进行中：完成后 complete、"
                    f"submit 下一阶段申请，再 checkpoint 结束本会话")
    return "三阶段均已完成且交接已消费，可按台账口径汇总结论"


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
        lead = record.get("lead")
        if lead is not None:
            if not isinstance(lead, dict):
                violations.append({"index": index, "id": ident, "code": "lead_not_object",
                                   "message": "lead 必须是对象 {suspicion, basis, where}"})
            else:
                unknown = set(lead) - LEAD_FIELDS
                if unknown:
                    violations.append({"index": index, "id": ident, "code": "lead_unknown_field",
                                       "keys": sorted(unknown),
                                       "message": f"lead 只接受 {sorted(LEAD_FIELDS)}，"
                                                  "不得夹带判定字段"})
                for key in ("suspicion", "basis"):
                    if not str(lead.get(key) or "").strip():
                        violations.append({"index": index, "id": ident,
                                           "code": "lead_missing_basis",
                                           "key": key,
                                           "message": f"lead.{key} 不能为空：现场怀疑必须写清依据"})
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
        "active_session": state.get("active_session"),
        "awaiting_session_for": state.get("awaiting_session_for"),
        "sessions": state.get("sessions") or [],
        "phases": {phase: {"label": PHASE_LABELS[phase],
                           "status": state["phases"][phase]["status"],
                           "artifacts": state["phases"][phase].get("artifacts"),
                           "routing": state["phases"][phase].get("routing"),
                           "approval": state["phases"][phase].get("approval"),
                           "handoff": state["phases"][phase].get("handoff")}
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
    p.add_argument("--session", default="SESS-1", help="本会话标识（用于跨会话交接校验）")
    p.add_argument("--model-class", help="本会话执行模型类别：strong-reasoning / fast")
    p.add_argument("--model-name", help="本会话实际模型名（留痕用）")
    p.add_argument("--note", help="任务放行说明")

    p = sub.add_parser("submit", help="提交阶段申请（由上一阶段会话发起）")
    p.add_argument("state")
    p.add_argument("--phase", required=True)
    p.add_argument("--artifact", action="append", default=None)
    p.add_argument("--model-class", help="实际执行模型类别：strong-reasoning / fast")
    p.add_argument("--model-name", help="实际模型名（留痕用）")
    p.add_argument("--note")
    p.add_argument("--session", help="本会话标识")

    p = sub.add_parser("checkpoint", help="阶段收尾：写交接包并要求结束本会话")
    p.add_argument("state")
    p.add_argument("--phase", required=True)
    p.add_argument("--handoff-dir", help="交接包目录（默认状态文件同级的 handoff/）")
    p.add_argument("--artifact", action="append", default=None)
    p.add_argument("--note")
    p.add_argument("--session", help="本会话标识")

    p = sub.add_parser("resume", help="新会话续跑：凭用户粘贴的确认文本放行下一阶段")
    p.add_argument("state")
    p.add_argument("--phase", required=True)
    p.add_argument("--session", required=True, help="**新会话**标识（不得与交接会话相同）")
    p.add_argument("--user-confirm", required=True,
                   help="用户在新会话中原样粘回的确认文本（须含交接确认码）")
    p.add_argument("--model-class", help="新会话执行模型类别：strong-reasoning / fast")
    p.add_argument("--model-name", help="新会话实际模型名（留痕用）")
    p.add_argument("--note")

    p = sub.add_parser("approve", help="同会话放行（兼容路径；结论上限会压待核实）")
    p.add_argument("state")
    p.add_argument("--phase", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--note", required=True)
    p.add_argument("--session", help="本会话标识")

    p = sub.add_parser("reject", help="驳回阶段（交接点可由新会话驳回）")
    p.add_argument("state")
    p.add_argument("--phase", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--session", help="驳回所在会话标识")

    p = sub.add_parser("start", help="开始已放行的阶段")
    p.add_argument("state")
    p.add_argument("--phase", required=True)
    p.add_argument("--session", help="本会话标识（跨会话放行须由放行会话开始）")

    p = sub.add_parser("complete", help="完成进行中的阶段")
    p.add_argument("state")
    p.add_argument("--phase", required=True)
    p.add_argument("--note")
    p.add_argument("--session", help="本会话标识")
    p.add_argument("--attack-coverage",
                   help="attack_coverage.py 结论输出；execute 阶段收尾必填，缺失则结论上限压待核实")
    p.add_argument("--exploration", action="append",
                   help="exploration_state.summarize() 输出的 gate 字段（或整份报告，自动取 gate）；"
                        "可重复传多份分域清单的文件路径（取最严 ceiling、缺口合并），"
                        "单个文件也可含 JSON 数组；"
                        "execute 阶段收尾提供，缺失则告警并压结论上限")
    p.add_argument("--artifact", action="append", dest="artifacts", default=None,
                   help="本阶段产物路径，可重复；登记后交接包「本阶段产物」才列得出来")
    p.add_argument("--assumptions",
                   help="风险假设清算 JSON 路径；execute/adjudicate 阶段可提前登记，"
                        "让裁决终检的前置要求在检查点就可见（登记不等于已证伪）")
    p.add_argument("--regression",
                   help="回归影响面结论 JSON 路径；同上（登记不等于已评估）")
    p.add_argument("--collection",
                   help="本轮采集记录 JSONL/JSON 数组路径；execute 阶段收尾提供则做 "
                        "scan-collection 结构校验，有违规即拒绝收尾（修正后重试）；"
                        "未提供则保留受限收尾能力，但结论上限压待核实")

    p = sub.add_parser("revise", help="补正已完成阶段的机械结论（不改状态、留痕）")
    p.add_argument("state")
    p.add_argument("--phase", required=True,
                   help="仅支持 execute：check_verdict 只读该阶段的机械结论")
    p.add_argument("--note")
    p.add_argument("--session", help="本会话标识")
    p.add_argument("--attack-coverage", help="补正用的 attack_coverage.py 结论")
    p.add_argument("--exploration", action="append",
                   help="补正用的 exploration gate（或整份报告，自动取 gate）；"
                        "可重复传多份分域清单的文件路径（取最严 ceiling、缺口合并）")
    p.add_argument("--allow-relax", action="store_true",
                   help="允许把未通过结论升级为「通过」。默认禁止；仅在补齐探针后重跑门禁"
                        "确实得「通过」时使用，note 须写明依据——history 的 transitions 字段留痕")

    p = sub.add_parser("amend", help="登记计划修订申请")
    p.add_argument("state")
    p.add_argument("--phase", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--note", required=True)
    p.add_argument("--session", help="本会话标识")
    p.add_argument("--declaration",
                   help="修订分类声明 JSON 文件（kind + 只读凭据/范围变更信号）。"
                        "不提供则一律按 scope_change 处理（先批后动）")

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
    p.add_argument("--assumptions",
                   help="风险假设清算 JSON（reviewed + items[id/assumption/basis/falsify_action/result]）")
    p.add_argument("--regression",
                   help="回归影响面 JSON（changed_areas + candidates[basis] 或 no_impact_reason）")

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
            result = init_state(args.state, args.task, args.session, args.model_class,
                                args.model_name, args.note)
            print(json.dumps(status(result), ensure_ascii=False, indent=2))
            return 0

        state = load(args.state)
        if args.command == "submit":
            submit(state, args.phase, args.artifact, args.model_class, args.model_name,
                   args.note, args.session)
        elif args.command == "checkpoint":
            handoff_dir = (Path(args.handoff_dir) if args.handoff_dir
                           else Path(args.state).parent / "handoff")
            handoff_dir.mkdir(parents=True, exist_ok=True)
            checkpoint(state, args.phase, handoff_dir, args.note, args.artifact, args.session)
        elif args.command == "resume":
            resume(state, args.phase, args.session, args.user_confirm, args.model_class,
                   args.model_name, args.note)
        elif args.command == "approve":
            approve(state, args.phase, args.by, args.note, args.session)
        elif args.command == "reject":
            reject(state, args.phase, args.by, args.reason, args.session)
        elif args.command == "start":
            start(state, args.phase, args.session)
        elif args.command == "complete":
            complete(state, args.phase, args.note, args.session,
                     _read_json_arg(args.attack_coverage),
                     exploration=_read_exploration_args(args.exploration),
                     artifacts=args.artifacts,
                     assumptions=args.assumptions,
                     regression=args.regression,
                     collection=args.collection)
        elif args.command == "revise":
            revise(state, args.phase,
                   attack_coverage=_read_json_arg(args.attack_coverage),
                   exploration=_read_exploration_args(args.exploration),
                   note=args.note, session=args.session,
                   allow_relax=args.allow_relax)
        elif args.command == "amend":
            request_amendment(state, args.phase, args.by, args.note, args.session,
                              declaration=_read_json_arg(args.declaration))
        elif args.command == "approve-amendment":
            approve_amendment(state, args.id, args.by, args.note)
        elif args.command == "ack-routing":
            acknowledge_routing(state, args.phase, args.note)
        if args.command != "status":
            save(args.state, state)
        if args.command == "check-verdict":
            check = check_verdict(
                state,
                json.loads(Path(args.assumptions).read_text(encoding="utf-8-sig"))
                if getattr(args, "assumptions", None) else None,
                json.loads(Path(args.regression).read_text(encoding="utf-8-sig"))
                if getattr(args, "regression", None) else None)
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
