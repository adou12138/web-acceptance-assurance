#!/usr/bin/env python3
"""amendment_class.py — 修订申请分类契约（单一事实源）。

存在目的：修订申请分两类，处置完全相反，判错就是"没批就写了"或"该批的不批"。

- `readonly_lead`：**固定授权范围内的只读观察**。已获授权、无业务状态变化、
  可凭计划与通道记录核对 → **可继续执行**，不必先批。
- `scope_change`：**范围变更**（新增写入、角色或权限扩展、环境变更、超预算、
  属性未知或凭据不全）→ **保留先批后动**，未批准不得执行。

为什么必须机械判：§3.2 明确「`path_type`、HTTP 方法或『未看到写操作』单独均不能作为凭据」——
凭"看着像只读"放行，等于把先批后动变成先做后说。

判定只认**已登记事实**（申报字段本身），不认自然语言自述：
`readonly_lead` 须同时具备 scope / environment / role / allowed_actions / no_state_change
五项凭据；缺任何一项 → 归 `scope_change`（从严）。

凭据校验判**类型与取值**，不只判真假。「有值」不等于「是有效值」：`no_state_change`
写成字符串 `"false"`、`allowed_actions` 写成空数组、`scope` 填空白，在真值判断下
都算"已填"，而它们恰恰表示"会发生状态变化 / 没有任何已授权动作 / 范围空着"。
只做真假判断时，一条明确写着会发生状态变化的申报会被判成只读观察并放行——
本模块存在的理由被反向利用。
"""

# 修订分类枚举（唯一事实源；phase_gate 与 execution_ledger 都从这里取）
AMENDMENT_KINDS = ("readonly_lead", "scope_change")

# readonly_lead 必填凭据：缺任一项即从严归为 scope_change
READONLY_LEAD_REQUIRED = ("scope", "environment", "role", "allowed_actions", "no_state_change")

# 触发 scope_change 的正向信号（命中任一即从严，不看其余字段是否"看起来只读"）
SCOPE_CHANGE_SIGNALS = (
    "writes",              # 新增写入
    "role_change",         # 角色或权限扩展
    "environment_change",  # 环境变更
    "over_budget",         # 超预算
    "unknown_attributes",  # 属性未知
    "incomplete_evidence", # 凭据不全
)

# 分类契约的字段名（申报记录里用这些键）
# `linked_records` 是可选关联字段（关联台账/写操作条目 ID 列表），不是只读凭据本身；
# 只用于 N8 收尾对账，缺省不阻断、不压档。
DECLARATION_FIELDS = ("kind", "scope", "environment", "role", "allowed_actions",
                      "no_state_change", "linked_records") + SCOPE_CHANGE_SIGNALS


def _nonempty_text(value):
    """非空白字符串。数字、布尔、数组、空白串都不算。"""
    return isinstance(value, str) and bool(value.strip())


def _actions_ok(value):
    """非空字符串数组（至少一个非空白动作名）。空数组表示"没有已授权动作"。"""
    return (isinstance(value, (list, tuple)) and bool(value)
            and all(_nonempty_text(item) for item in value))


def _type_label(value):
    """把实际取值的形态写进原因里，便于申报方自查（不猜意图，只报形态）。"""
    if value is None:
        return "缺失"
    if isinstance(value, bool):
        return f"布尔 {value}"
    if isinstance(value, str):
        return f"字符串 {value!r}"
    if isinstance(value, (list, tuple)):
        return f"{type(value).__name__}（{len(value)} 项）"
    return type(value).__name__


def _readonly_credential_problems(declaration):
    """逐项校验只读凭据的类型与取值，返回不合格原因列表（空列表 = 齐备）。"""
    problems = []
    for name in ("scope", "environment", "role"):
        value = declaration.get(name)
        if not _nonempty_text(value):
            problems.append(f"{name} 须为非空字符串（当前 {_type_label(value)}）")
    actions = declaration.get("allowed_actions")
    if not _actions_ok(actions):
        problems.append(f"allowed_actions 须为非空字符串数组（当前 {_type_label(actions)}）")
    if declaration.get("no_state_change") is not True:
        problems.append("no_state_change 须为布尔 true（当前 %s）"
                        % _type_label(declaration.get("no_state_change")))
    return problems


def classify(declaration):
    """按登记事实判定修订分类 → (kind, reasons)。

    从严规则：**先看有没有触发 scope_change 的信号**，再看只读凭据是否齐备且合格。
    顺序不能反——先判只读再判信号，会让"有写入信号但也填了只读字段"的申报蒙混过关。

    未能提供任何分类信息的申报 → scope_change（须先批），并给出缺项原因。
    """
    if not isinstance(declaration, dict) or not declaration:
        return "scope_change", ["修订申请未提供分类信息：从严按 scope_change 处理，须先批"]

    reasons = []
    declared = str(declaration.get("kind") or "").strip()
    if declared not in AMENDMENT_KINDS:
        return "scope_change", [
            f"修订申请未声明有效 kind（须为 {'/'.join(AMENDMENT_KINDS)} 之一）：从严按 scope_change 处理"]

    # 1) 正向信号优先：命中任一即 scope_change，不看其余字段
    hits = [name for name in SCOPE_CHANGE_SIGNALS if declaration.get(name)]
    if hits:
        return "scope_change", [f"申报命中范围变更信号：{'、'.join(hits)}"]

    # 2) 申报只读 → 核对只读凭据是否齐备且类型取值合格
    if declared == "readonly_lead":
        problems = _readonly_credential_problems(declaration)
        if problems:
            return "scope_change", [
                "申报 readonly_lead 但只读凭据不合格：" + "；".join(problems)
                + "（缺项或类型取值不符均从严归 scope_change）"]
        return "readonly_lead", []

    # 3) 申报 scope_change：直接成立，不额外要求字段
    return "scope_change", []


def requires_approval(kind):
    """该分类是否必须先批后动。scope_change → True；readonly_lead → False。"""
    return str(kind) != "readonly_lead"


def is_blocking(record):
    """一条修订记录当前是否阻断阶段推进（phase_gate 与 execution_ledger 共用）。

    口径：**类别决定"需不需要先批"，状态决定"批了没有"**。

    - `readonly_lead`（固定授权只读观察）凭据齐备即可继续，任何情况下都不阻断——
      阻断它等于让"如实登记只读线索"比"先做后说"更糟；但**凭据本身要复验**：
      记录里的 `kind` 是可以被改写/手填的，若它宣称只读而 `declaration` 里的凭据
      不合格（例如 `no_state_change` 写的是 `"false"`），说明这条记录不该被放行，
      按须先批处理；
    - `scope_change` 只有在 `status == "awaiting_approval"`（尚未批准）时阻断，
      批准后不再阻断；
    - 历史状态文件里没有 `kind` 字段（登记于分类契约之前）→ 从严按 scope_change 处理。

    为什么把判定放在这里而不是各端各判：`phase_gate` 与 `execution_ledger` 若各写一份
    "kind == readonly_lead 就放行"，两边口径迟早漂移——**分类口径不同 = 放行口径不同**。
    同理，判定不能只依赖 status：`status` 是登记时的处置结论，`kind` 是分类事实，
    只看 status 时 kind 字段无人消费，退化成死字段。
    """
    if not isinstance(record, dict):
        return False
    kind = str(record.get("kind") or "scope_change")
    if not requires_approval(kind):
        declaration = record.get("declaration")
        if isinstance(declaration, dict) and _readonly_credential_problems(declaration):
            return True
        return False
    return record.get("status") == "awaiting_approval"


def validate_declaration(declaration):
    """校验申报载荷本身：必须是对象，字段名受控。返回 (ok, message)。

    只校验**形状**（是不是对象、有没有未知字段），不判分类——
    分类由 classify() 负责，两者分开，避免一处判错两处都错。
    """
    if not isinstance(declaration, dict):
        return False, "修订申请声明必须是对象"
    unknown = sorted(set(declaration) - set(DECLARATION_FIELDS))
    if unknown:
        return False, (f"修订申请声明含未知字段：{unknown}"
                       f"（允许 {list(DECLARATION_FIELDS)}）")
    return True, ""
