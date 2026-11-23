"""轻量探索清单与下一步候选工具；标准库实现，不启动浏览器或访问网络。

API：summarize(data) 严格校验并返回派生摘要；export_csv(data, output) 返回同一
摘要并独占新建 UTF-8-sig CSV。两者均不修改输入，非法数据抛 ValueError；文件
访问错误抛 OSError。CLI：python exploration_state.py input.json --output new.csv，
成功向 stdout 输出 JSON 摘要，输入/工具错误向 stderr 输出原因并以 2 退出。

输入根字段仅 schema_version="1.0"、context、items。context 必须含非空字符串
 target_url/application_version/environment。items 可为空；每项必须含 id、非空
 requirement_ids 字符串数组、page_url、role、viewport（如 1440x900 的非空描述）、
 state、action、risk(high/medium/low)、status(NOT_RUN/PASS/FAIL/BLOCKED/SKIPPED)、
 observation、evidence_paths、blocked_reason、严格布尔 side_effect_pending、非空
 attributes 数组；可选 case_id。ID/引用使用 [A-Za-z0-9][A-Za-z0-9_.:-]*，项 ID
 全局唯一；数组不能重复。引用只校验形状，未加载外部需求/用例目录，不证明其存在。
属性允许 functional/visual/usability/accessibility/performance/security/reliability/
compatibility/content。其中 security/compatibility/content 为必选质量属性，被阻塞/未执行会压低结论上限。
URL 限无用户信息的绝对 http/https，page_url 与 target_url 同源
（协议、主机、有效端口）。被测对象分布在不同域时，每域各出一份同源
清单、由 phase_gate 以多份 gate 数组合并判定（取最严、缺口合并），
不允许在单份清单里混入跨域记录。不解析跳转、不执行 action、不验证
浏览器最终访问地址。

PASS/FAIL 必须有非空观察和绝对存在的本地证据文件；每次派生时重新读取并计算
SHA-256，只记录当前内容指纹，不声称判断内容真实性或发现历史篡改。BLOCKED/
SKIPPED 必须有原因。ready 只列 NOT_RUN、无阻塞原因且无待核对副作用的项，按
高风险优先、再 ID 排序。FAIL 只列 reproduction_candidates，不自动重放；任意
状态的 side_effect_pending 都进入 needs_reconcile，由调用者核对后更新自己的源。

CSV 是派生检查视图，每项可出现在多个视角，整体 planned/counts 按唯一 ID 统计。
界面样式=visual；交互易用性=usability；基础无障碍=accessibility；功能=functional/
security/data/recovery/compatibility；性能=performance。数组/证据用 JSON 单元格保存。
CSV 中疑似公式的文本单元格添加前导单引号，防止表格应用执行；JSON 摘要及源
内容保留原文。输出必须为 .csv，父目录已存在，拒绝覆盖。摘要含 gate：仍有可执行
未执行项、必查视角无记录或缺控件遍历条目（EXP-TRAV-）时 verdict_ceiling 为待核实，限制报告结论上限；门禁不
替代缺陷与证据判定。摘要只表达已提供的清单进度，不判定业务或总体门禁；正式结论
仍需断言与证据内容复核。仅有 ID/记录绝不等于业务通过。
"""
import argparse
import copy
import csv
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

STATUSES = {"NOT_RUN", "PASS", "FAIL", "BLOCKED", "SKIPPED"}
RISKS = {"high": 0, "medium": 1, "low": 2}
# E12：与 scripts/case_data.py 的七类用例属性对齐（data/recovery 归并 reliability）；
# usability 为探索域特有（交互易用性五视角必需），属领域差异而非白名单漂移。
ATTRIBUTES = {"functional", "visual", "usability", "accessibility", "performance", "security", "reliability", "compatibility", "content"}
VIEWS = {
    "界面样式": {"visual"},
    "交互易用性": {"usability"},
    "基础无障碍": {"accessibility"},
    "功能": {"functional", "security", "reliability", "compatibility"},
    "性能": {"performance"},
}
REQUIRED_VIEWS = ("界面样式", "交互易用性", "基础无障碍", "功能", "性能")
# 必选质量属性：一旦清单里出现这些属性且未执行/被阻塞，必须压低结论上限，
# 不得由同视角的一条 functional PASS 记录清空（v3 目标审计 G08/G16）。
# content=文案（术语/单位/错误提示/未渲染变量）；compatibility=兼容内核/设备；
# security=权限/会话。三者均有独立必选义务，不能被功能视角覆盖稀释。
MANDATORY_ATTRIBUTES = ("security", "compatibility", "content", "visual", "usability", "accessibility")
# 视角覆盖的最小单元数：一个"页面×状态"单元。同一页面同一状态的一条 PASS 不足以
# 证明整个视角已覆盖——界面样式尤其容易被一张首页截图清空。
MIN_VIEW_CELLS = 2
ITEM_FIELDS = {"id", "requirement_ids", "page_url", "role", "viewport", "state", "action", "risk",
               "status", "observation", "evidence_paths", "blocked_reason", "side_effect_pending",
               "attributes"}
# 可选字段：acknowledged_unexplored 是诚实记录的出口（带 next_action + owner 才生效），
# falsify_action 是"这条观察构造了什么让它坏的条件"，用于替代凑页面的覆盖口径。
ITEM_OPTIONAL = {"case_id", "acknowledged_unexplored", "falsify_action"}
SCOPE = "仅清单进度与人工下一步候选；不判定业务或总体门禁；证据内容和外部引用需复核；不自动执行或重放"


def _object(value, required, optional, name):
    if not isinstance(value, dict):
        raise ValueError(f"{name}: 必须为对象")
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required - optional)
    if missing or unknown:
        # 缺字段与多余字段必须分别点名：合并成一句「缺少必填字段或包含未知字段」时，
        # 执行者无法判断该补哪个字段，只能回头读源码定位（实测阻塞点）。
        parts = []
        if missing:
            parts.append(f"缺字段 {missing}")
        if unknown:
            parts.append(f"未知字段 {unknown}")
        raise ValueError(f"{name}: " + "；".join(parts))


def _text(value, name, nonempty=True):
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise ValueError(f"{name}: 必须为{'非空' if nonempty else ''}字符串")


def _ident(value, name):
    _text(value, name)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value):
        raise ValueError(f"{name}: 无效的稳定 ID")


def _strings(value, name, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{name}: 必须为{'非空' if nonempty else ''}字符串数组")
    for item in value:
        _text(item, name)
    if len(set(value)) != len(value):
        raise ValueError(f"{name}: 数组不能重复")


def _origin(value, name):
    _text(value, name)
    if "\\" in value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{name}: URL 含歧义字符")
    try:
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username is not None or url.password is not None:
            raise ValueError("仅允许无用户信息的绝对 http/https URL")
        host = url.hostname
        if "%" in host or url.netloc.endswith(":"):
            raise ValueError("无效的主机或端口")
        if ":" in host:
            host = ipaddress.IPv6Address(host).compressed
        else:
            host = host.encode("idna").decode("ascii").lower()
            if len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.rstrip(".").split(".")):
                raise ValueError("无效的主机")
        port = url.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("无效的端口")
        return url.scheme, host, port if port is not None else (443 if url.scheme == "https" else 80)
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"{name}: 无效或不安全的 URL ({exc})") from exc


def _evidence(paths, name):
    result = []
    resolved = set()
    for value in paths:
        path = Path(value)
        if not path.is_absolute() or not path.is_file():
            raise ValueError(f"{name}: 证据必须为绝对存在的本地文件")
        path = path.resolve()
        if path in resolved:
            raise ValueError(f"{name}: 证据路径指向重复文件")
        resolved.add(path)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        result.append({"path": str(path), "sha256": digest.hexdigest()})
    return result


def _acknowledged(row):
    """承认项（next_action + owner 齐备）才算出囗；缺字段的一律仍按待执行计。"""
    value = row.get("acknowledged_unexplored")
    return (isinstance(value, dict) and bool(str(value.get("next_action") or "").strip())
            and bool(str(value.get("owner") or "").strip()))


def _gate(rows, executed_views, executed_cells=None, rulings=None, view_scope=None):
    """按已提供清单计算执行门禁：未消费候选、必查视角覆盖不足、缺控件遍历条目或存在 FAIL 时降低结论上限。

    SKIPPED / BLOCKED 属于"已留痕但未执行"，不能使必查视角计为已覆盖；
    FAIL 属"已实测到异常"，同样不得写"通过"。
    必选质量属性（安全/兼容/文案/界面/易用/无障碍）须逐类裁定：要么有 PASS 实测，
    要么显式写明 not_applicable 理由；整类不出现不等于不适用。

    视角覆盖按**页面×状态单元**计数，不按"有没有一条记录"：曾出现 7 条同页面同状态的
    记录各带一个属性，就把界面样式/易用性/无障碍三个视角全部点亮——等于截一张首页图
    就算"UI 已测"。每视角至少 MIN_VIEW_CELLS 个不同 (page_url, state) 单元，或在
    view_scope 里写明该视角只有 N 个单元及理由。
    """
    rulings = rulings or {}
    view_scope = view_scope or {}
    executed_cells = executed_cells or {view: set() for view in VIEWS}
    ready = [row["id"] for row in rows if row["status"] == "NOT_RUN" and not row["side_effect_pending"] and not row["blocked_reason"].strip() and not _acknowledged(row)]
    # 如实登记"看到可疑但还没设计探针"不该被罚：实测中同一份清单只把 3 条观察写成
    # NOT_RUN，结论就从「通过」掉到「待核实」——等于"写下我还没查就自己降级"。
    # 带 next_action + owner 的承认项单列跟踪，不计入 ready_pending；缺字段仍计入。
    acknowledged = [{"id": row["id"],
                     "next_action": row["acknowledged_unexplored"]["next_action"],
                     "owner": row["acknowledged_unexplored"]["owner"],
                     "risk": row["risk"]}
                    for row in rows if _acknowledged(row)]
    acknowledged_incomplete = [row["id"] for row in rows
                               if row["status"] == "NOT_RUN"
                               and isinstance(row.get("acknowledged_unexplored"), dict)
                               and not _acknowledged(row)]
    missing = [view for view in REQUIRED_VIEWS if not executed_views[view]]
    executed_actions = {}
    for view, attrs in VIEWS.items():
        executed_actions[view] = {row["falsify_action"] for row in rows
                                  if row["status"] in {"PASS", "FAIL"}
                                  and set(row["attributes"]) & attrs
                                  and row.get("falsify_action")}
    thin, thin_detail = [], []
    for view in REQUIRED_VIEWS:
        need = int((view_scope.get(view) or {}).get("cells") or MIN_VIEW_CELLS)
        cells = executed_cells.get(view) or set()
        # 覆盖口径从"凑够几个页面×状态"改为"有没有两种不同的构造动作"：
        # 前者可以靠人为多切一个状态凑满，后者凑不出来——写下的动作要能被执行。
        actions = executed_actions.get(view) or set()
        if cells and len(cells) < need and len(actions) < need:
            thin.append(view)
            thin_detail.append(f"{view}（{len(cells)}/{need} 个页面×状态单元，"
                               f"{len(actions)}/{need} 种构造动作）")
    traversal_items = [row for row in rows if str(row.get("id", "")).startswith("EXP-TRAV-")]
    traversal_pending = sum(1 for row in traversal_items if row["status"] == "NOT_RUN")
    traversal_recorded = bool([row for row in traversal_items if row["status"] != "NOT_RUN"])
    failed = [row["id"] for row in rows if row["status"] == "FAIL"]
    # 必选义务（v3 G08）：副作用未决、必选质量属性被阻塞/未执行、必选页面未遍历，
    # 都不能因其余视角已有 PASS 而放行结论上限。非必选项仍只披露不阻断。
    unreconciled_pending = [row["id"] for row in rows if row["side_effect_pending"]]
    blocked_mandatory = [row["id"] for row in rows
                         if set(row["attributes"]) & set(MANDATORY_ATTRIBUTES)
                         and row["status"] in {"BLOCKED", "NOT_RUN", "SKIPPED"}]
    # 必选质量属性逐类裁定：有 PASS 实测=覆盖；not_applicable 有理由=豁免；其余都算未闭合。
    passed_attrs = {attr for row in rows if row["status"] == "PASS" for attr in row["attributes"]}
    mandatory_status, unresolved_mandatory = {}, {}
    for attr in MANDATORY_ATTRIBUTES:
        if attr in passed_attrs:
            mandatory_status[attr] = "covered"
        elif attr in rulings:
            ruling = rulings[attr]
            if ruling["state"] == "not_applicable":
                mandatory_status[attr] = "not_applicable"
            else:
                mandatory_status[attr] = ruling["state"]
                unresolved_mandatory[attr] = ruling["state"]
        else:
            mandatory_status[attr] = "undeclared"
            unresolved_mandatory[attr] = "undeclared"
    blocking_notes = []
    if unreconciled_pending:
        blocking_notes.append("未决副作用：" + "、".join(unreconciled_pending))
    if blocked_mandatory:
        blocking_notes.append("必选质量属性未执行/被阻塞：" + "、".join(blocked_mandatory))
    if traversal_pending:
        blocking_notes.append("页面遍历未完成：" + str(traversal_pending) + " 项")
    if unresolved_mandatory:
        detail = []
        for attr, state in unresolved_mandatory.items():
            if state == "undeclared":
                detail.append(f"{attr}=未裁定")
            elif state == "blocked":
                detail.append(f"{attr}=已裁定阻塞")
            else:
                detail.append(f"{attr}=已裁定适用但无 PASS 记录")
        blocking_notes.append("必选质量属性未闭合：" + "、".join(detail)
                              + "（须实测或有理由的 not_applicable 裁定）")
    if ready:
        ceiling = "待核实"
        message = f"仍有{len(ready)}项可执行未执行，技术结论上限为待核实；需执行或逐项写明未执行理由与影响"
    elif missing:
        ceiling = "待核实"
        message = "必查视角无实测记录（PASS/FAIL）：" + "、".join(missing) + "；需补执行或写明不适用理由"
    elif thin:
        ceiling = "待核实"
        message = ("必查视角覆盖过薄（同一页面同一状态的一条记录不能清空整个视角）："
                   + "、".join(thin_detail)
                   + "；需补不同页面/状态，或在 view_scope 里写明该视角只有 N 个单元及理由")
    elif not traversal_recorded:
        ceiling = "待核实"
        message = "清单中无控件遍历条目（EXP-TRAV- 前缀）：按 exploration-runtime.md 的控件遍历约定补充条目或写明不适用理由；技术结论上限为待核实"
    elif failed:
        ceiling = "待核实"
        message = f"存在{len(failed)}项FAIL条目：不得写“通过”，须按缺陷必选性与闭环状态判为“不通过”或“待核实”"
    elif blocking_notes:
        ceiling = "待核实"
        message = "存在未关闭的必选探索义务：" + "；".join(blocking_notes) + "；须执行、解除阻塞或写明不适用与影响"
    else:
        ceiling = "通过"
        message = "无未消费候选、必查视角均有记录且含控件遍历条目；仍须按缺陷、断言和证据判定，不代表已通过"
    ready_set = set(ready)
    return {
        "ready_pending": len(ready),
        "ready_pending_ids": ready,
        "ready_high_risk": sum(1 for row in rows if row["id"] in ready_set and row["risk"] == "high"),
        "missing_views": missing,
        "thin_views": thin,
        "view_actions": {view: sorted(actions) for view, actions in executed_actions.items()},
        "acknowledged_unexplored": acknowledged,
        "acknowledged_incomplete": acknowledged_incomplete,
        "view_cells": {view: sorted(cells) for view, cells in executed_cells.items()},
        "view_scope": {view: dict(scope) for view, scope in view_scope.items()},
        "executed_views": {view: list(ids) for view, ids in executed_views.items()},
        "failed_items": failed,
        "unreconciled_pending": unreconciled_pending,
        "blocked_mandatory": blocked_mandatory,
        "skipped_views": {view: [row["id"] for row in rows if row["status"] in {"BLOCKED", "SKIPPED"} and set(row["attributes"]) & attrs]
                          for view, attrs in VIEWS.items()},
        "traversal_pending": traversal_pending,
        "traversal_recorded": traversal_recorded,
        "mandatory_attributes": mandatory_status,
        "verdict_ceiling": ceiling,
        "message": message,
    }


def summarize(data):
    """校验单份探索输入，返回当前进度、候选 ID、检查视角和带证据指纹的记录。"""
    _object(data, {"schema_version", "context", "items"}, {"attribute_rulings", "view_scope"}, "root")
    if data["schema_version"] != "1.0":
        raise ValueError("schema_version: 必须为字符串 1.0")
    rulings = data.get("attribute_rulings")
    if rulings is not None:
        if not isinstance(rulings, dict):
            raise ValueError("attribute_rulings: 必须为对象")
        unknown = set(rulings) - set(MANDATORY_ATTRIBUTES)
        if unknown:
            raise ValueError(f"attribute_rulings: 未知属性 {sorted(unknown)}")
        for attr, ruling in rulings.items():
            name = "attribute_rulings." + attr
            _object(ruling, {"state", "reason"}, set(), name)
            _text(ruling["state"], name + ".state")
            _text(ruling["reason"], name + ".reason")
            if ruling["state"] not in {"applicable", "not_applicable", "blocked"}:
                raise ValueError(f"{name}.state: 必须为 applicable/not_applicable/blocked")
    view_scope = data.get("view_scope")
    if view_scope is not None:
        if not isinstance(view_scope, dict):
            raise ValueError("view_scope: 必须为对象")
        unknown = set(view_scope) - set(REQUIRED_VIEWS)
        if unknown:
            raise ValueError(f"view_scope: 未知视角 {sorted(unknown)}")
        for view, scope in view_scope.items():
            name = "view_scope." + view
            _object(scope, {"cells", "reason"}, set(), name)
            if type(scope["cells"]) is not int or not 1 <= scope["cells"] <= MIN_VIEW_CELLS:
                raise ValueError(f"{name}.cells: 必须为 1~{MIN_VIEW_CELLS} 的整数（缩小覆盖下限须留痕）")
            _text(scope["reason"], name + ".reason")
    context = data["context"]
    # 严格同源：单份清单只表达一个域。跨域混入会换掉视角覆盖的统计池
    # （"每域各自达标"变"跨域合计达标"，一域的记录可补齐另一域的缺口），
    # v1.47.2 实测证实该旁路能把覆盖不足洗成通过，故不允许——跨域场景
    # 用每域一份清单 + phase_gate 多份 gate 合并（取最严）表达。
    _object(context, {"target_url", "application_version", "environment"},
            set(), "context")
    for key in ("target_url", "application_version", "environment"):
        _text(context[key], "context." + key)
    origin = _origin(context["target_url"], "context.target_url")
    if not isinstance(data["items"], list):
        raise ValueError("items: 必须为数组")
    rows, seen = [], set()
    for number, item in enumerate(data["items"]):
        name = f"items[{number}]"
        _object(item, ITEM_FIELDS, ITEM_OPTIONAL, name)
        _ident(item["id"], name + ".id")
        if item["id"] in seen:
            raise ValueError(f"{name}.id: 重复的稳定 ID")
        seen.add(item["id"])
        if "case_id" in item:
            _ident(item["case_id"], name + ".case_id")
        _strings(item["requirement_ids"], name + ".requirement_ids", True)
        for ident in item["requirement_ids"]:
            _ident(ident, name + ".requirement_ids")
        for key in ("role", "viewport", "state", "action", "risk", "status"):
            _text(item[key], name + "." + key)
        if item["risk"] not in RISKS or item["status"] not in STATUSES:
            raise ValueError(f"{name}: 无效的风险或状态")
        page_origin = _origin(item["page_url"], name + ".page_url")
        if page_origin != origin:
            raise ValueError(f"{name}.page_url: 必须与 target_url 同源（跨域请每域各出一份清单，用 phase_gate 多份 gate 合并登记）")
        for key in ("observation", "blocked_reason"):
            _text(item[key], name + "." + key, False)
        if "falsify_action" in item:
            _text(item["falsify_action"], name + ".falsify_action")
        if "acknowledged_unexplored" in item:
            if item["status"] != "NOT_RUN":
                raise ValueError(f"{name}.acknowledged_unexplored: 只适用于 NOT_RUN")
            _object(item["acknowledged_unexplored"], {"next_action", "owner"}, set(),
                    name + ".acknowledged_unexplored")
            for key in ("next_action", "owner"):
                # 允许空串：半填不算结构性错误，按"未完成"计入待执行（不给免罚）
                _text(item["acknowledged_unexplored"][key],
                      name + ".acknowledged_unexplored." + key, False)
        if type(item["side_effect_pending"]) is not bool:
            raise ValueError(f"{name}.side_effect_pending: 必须为布尔值")
        _strings(item["attributes"], name + ".attributes", True)
        if set(item["attributes"]) - ATTRIBUTES:
            raise ValueError(f"{name}.attributes: 未知属性")
        _strings(item["evidence_paths"], name + ".evidence_paths")
        if item["status"] in {"PASS", "FAIL"}:
            _text(item["observation"], name + ".observation")
            if not item["evidence_paths"]:
                raise ValueError(f"{name}.evidence_paths: PASS/FAIL 必须提供证据")
        if item["status"] in {"BLOCKED", "SKIPPED"}:
            _text(item["blocked_reason"], name + ".blocked_reason")
        row = copy.deepcopy(item)
        row["evidence"] = _evidence(item["evidence_paths"], name + ".evidence_paths")
        rows.append(row)
    rows.sort(key=lambda row: (RISKS[row["risk"]], row["id"]))
    report = {
        "schema_version": "1.0",
        "context": copy.deepcopy(context),
        "planned": len(rows),
        "counts": {status: sum(row["status"] == status for row in rows) for status in sorted(STATUSES)},
        "ready": [row["id"] for row in rows if row["status"] == "NOT_RUN" and not row["side_effect_pending"] and not row["blocked_reason"].strip() and not _acknowledged(row)],
        "reproduction_candidates": [row["id"] for row in rows if row["status"] == "FAIL"],
        "needs_reconcile": [row["id"] for row in rows if row["side_effect_pending"]],
        "views": {view: [row["id"] for row in rows if set(row["attributes"]) & attributes] for view, attributes in VIEWS.items()},
        "executed_views": {view: [row["id"] for row in rows if row["status"] in {"PASS", "FAIL"} and set(row["attributes"]) & attributes]
                           for view, attributes in VIEWS.items()},
        "executed_cells": {view: sorted({f'{row["page_url"]}|{row["state"]}' for row in rows
                                         if row["status"] in {"PASS", "FAIL"} and set(row["attributes"]) & attributes})
                           for view, attributes in VIEWS.items()},
        "items": rows,
        "scope": SCOPE,
    }
    report["gate"] = _gate(rows, report["executed_views"],
                           {view: set(cells) for view, cells in report["executed_cells"].items()},
                           data.get("attribute_rulings"), data.get("view_scope"))
    return report


def export_csv(data, output):
    """先完整校验，再独占新建 CSV；各检查视角复用同一项，不增加整体计数。"""
    report = summarize(data)
    output = Path(output)
    if output.suffix.lower() != ".csv" or not output.parent.is_dir():
        raise ValueError("输出必须为 CSV，父目录必须已存在")
    fields = ["view", "id", "requirement_ids", "case_id", "target_url", "application_version", "environment", "page_url", "role", "viewport", "state", "action", "risk", "status", "attributes", "observation", "evidence_paths", "evidence", "blocked_reason", "side_effect_pending"]
    indexed = {row["id"]: row for row in report["items"]}
    with output.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for view, ids in report["views"].items():
            for ident in ids:
                row = dict(indexed[ident], view=view, **report["context"])
                for key in ("requirement_ids", "attributes", "evidence_paths", "evidence", "side_effect_pending"):
                    row[key] = json.dumps(row[key], ensure_ascii=False, allow_nan=False)
                for key, value in row.items():
                    if isinstance(value, str) and (value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))):
                        row[key] = "'" + value
                writer.writerow(row)
    return report


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"JSON 对象包含重复字段: {key}")
        value[key] = item
    return value


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="校验探索清单，输出派生 CSV 与 JSON 摘要；不执行浏览器操作")
    parser.add_argument("input")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        data = json.loads(Path(args.input).read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object)
        report = export_csv(data, args.output)
        print(json.dumps(report, ensure_ascii=True, allow_nan=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(f"探索清单输入/工具错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
