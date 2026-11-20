#!/usr/bin/env python3
"""attack_coverage.py — 攻击面覆盖与判据独立性检查（清单式验收的诊断与门禁）。

问题来源：2026-11-18 一次 154 项真实测试，143 项走正向路径、权限 0 项、异常 0 项、
组合 1 项，最终只产出 4 条 FAIL，其中 0 条来自为新攻击面设计的探针。原因是执行范式
是「覆盖确认」（确认功能可用）而非「失效构造」（证明它会坏）——用例数增长不等于
攻击面增长，而报告主指标是通过率，天然奖励"确认正常"。

本脚本只做三件事：
  1. 诊断：把台账摊成路径类型/判据来源/发现方式/失效假设的分布；
  2. 门禁：缺可证伪预测、判据拿不出独立通道时压结论上限；
  3. 反声明：字段填满不等于做过。surf_id 要与 plan 风险面核对、independent 要能
     指出独立通道且带证据、method 要落在方法库枚举内，否则不给"通过"。

**判据设计原则：考构造，不考配比。** 早期版本用「非正向路径 ≥30%」「方法 ≥3 种」
「designed_probe 占比」这类配额，实测可被标注操纵（改个标签就过关），而且
"如实记录未探索项"会把「通过」打成「待核实」——等于制度上劝阻诚实。现在改为：
每个高风险风险面至少要有一条可证伪预测（`hypothesis` + `hypothesis_outcome`），
「打破了几个假设」是产出而不是违规；密度、方法多样性、计划外发现全部只作披露。

纪律：
- 只按台账里**显式声明**的字段计算；字段缺失记 unknown 并列入缺口，不用启发式值
  充当机械判据（--infer 的粗分结果单列 inferred_*，仅供参考，不进 density）。
- 工具不判缺陷有无，只判"攻击面是否被真正打过"。
- 计划外风险面（surfaces_beyond_plan）计为**产出**：未知问题的定义就是"不在计划
  里"，罚它等于从制度上劝阻发现未知问题。但必须 amend 纳入后续计划，否则下一轮
  不会有假设，发现会被丢掉。

**两条独立校验轨道**：需求来源的 SURF 维持"可证伪预测"校验（hypothesis +
hypothesis_outcome）不变；攻击假设（ATK）走独立轨道——它允许模糊假设与人工验证
路径，不要求拆成单条预测。ATK 的验证状态为"未执行"时**不触发**待核实上限（允许
长期悬置，这是保留假设锐度的有意让步）；仅"命中"且影响面属功能/数据/权限时升级为
blocker/major 复核候选，若尚无复核记录则压待核实（须裁定，不是不许发现）。ATK 登记
时即为合法条目，不依赖事后 amend 纳入计划。

路径类型不是只有功能逻辑：界面与文案（ui_content）、易用性（usability）、无障碍
（accessibility）、数据一致性（data_consistency）、回归（regression）都是合法攻击
维度，与 boundary/exception 同等计入非正向路径。曾经因为只认功能五类，如实登记的
UI 类发现反而被压上限——那是制度性逆向激励。

用法：
    python scripts/attack_coverage.py --ledger ledger.jsonl
    python scripts/attack_coverage.py --ledger events.jsonl --plan adversarial-plan.json
    python scripts/attack_coverage.py --ledger ledger.csv --emit-json out.json --emit-csv out.csv
    python scripts/attack_coverage.py --ledger ledger.jsonl --residual-note "未破的攻击面…" --enforce
    python scripts/attack_coverage.py --ledger ledger.jsonl --atk atk-registry.json --reviews reviews.jsonl
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

# 路径类型：positive 表示"确认功能可用"，其余全部是攻击维度（非正向）。
ATTACK_PATH_TYPES = (
    "boundary",          # 边界/极值/超长/空值
    "exception",         # 异常/失败/超时/中断/回滚
    "permission",        # 权限/角色/越权/租户
    "combo",             # 组合/并发/时序/序列
    "ui_content",        # 界面样式 + 文案（错别字/歧义/遮挡/错位/溢出/未渲染变量）
    "usability",         # 交互易用性（缺防重/缺加载态/绕路/不可撤销/提示误导）
    "accessibility",     # 无障碍（键盘/焦点/读屏/对比度/目标尺寸）
    "data_consistency",  # 前端展示/后端数据/缓存三通道对账
    "regression",        # 变更对旧功能的影响面回归
)
PATH_TYPES = ("positive",) + ATTACK_PATH_TYPES
PATH_TYPE_GROUPS = {
    "功能逻辑": ("boundary", "exception", "permission", "combo"),
    "界面与文案": ("ui_content", "usability", "accessibility"),
    "数据与回归": ("data_consistency", "regression"),
}
DISCOVERY_MODES = ("designed_probe", "incidental", "undetermined")
ORACLE_SOURCES = ("independent", "self_attested", "unknown")

# 判据独立性的最低要求：声明 independent 时，oracle_channel 必须点名一个独立通道。
# 只写被测物自己的回显（"agent 说改了，读回一致"）不算独立——那只是同源自证。
# 只匹配"把被测物自述当通道"的短语。裸词「自述」「回显」会误伤合法描述
# （如"对比助手自述与实际页面"本就写明是拿自述做对照），故只认完整短语。
SELF_ATTEST_HINTS = ("读回一致", "读回正确", "回显一致", "agent 回复", "助手回复",
                     "助手自述", "agent 自述", "接口返回即", "页面显示一致")

SURF_ID_RE = re.compile(r"^SURF-[A-Za-z0-9][A-Za-z0-9_.:-]*$")
ATK_ID_RE = re.compile(r"^ATK-[A-Za-z0-9][A-Za-z0-9_.:-]*$")

# —— ATK 独立校验轨道 ——
# 攻击假设与需求来源的 SURF 是两条轨道，字段与判据都不同：ATK 允许模糊假设，
# 验证路径可以是人工作业、场景推演或代码走读，不要求脚本探针。
ATK_IMPACTS = ("功能", "数据", "权限", "体验", "其他")
ATK_STATUSES = ("未执行", "执行中", "命中", "未命中", "无法验证")
# 影响面属功能/数据/权限的命中才升级为严重度候选：体验类命中不压结论上限
ATK_SEVERITY_IMPACTS = ("功能", "数据", "权限")
ATK_REQUIRED = ("id", "hypothesis", "validation_path", "impact", "status")
# ATK 严重度：与缺陷严重度同一套口径。缺省按影响面推定（功能/数据/权限 → major，
# 体验/其他 → minor），登记里显式给了就以登记为准——显式登记不被静默忽略。
ATK_SEVERITIES = ("blocker", "major", "minor")
ATK_DEFAULT_SEVERITY = {"功能": "major", "数据": "major", "权限": "major",
                         "体验": "minor", "其他": "minor"}

# 阈值依据：每个功能点在正向/边界/异常/权限/组合等路径中至少有一类非正向，
# 理论下限即 1/5=20%；取 30% 作为"每 3 条至少 1 条非正向"的最低门槛（非目标值）。
DEFAULT_DENSITY_THRESHOLD = 0.30
# 判据独立性：同源自证（用被测物自己的回显当 oracle）不得占多数
DEFAULT_INDEPENDENT_RATE = 0.30
# 风险面关联：至少一半的执行记录要能追溯到已登记失效假设
DEFAULT_SURF_LINKAGE = 0.50
# 方法多样性：记录数达此规模后，至少要出现的不同方法数（现仅作披露，不作门禁）
METHOD_DIVERSITY_MIN_RECORDS = 20
METHOD_DIVERSITY_MIN_METHODS = 3
# 失效假设：无 plan 时，记录数达此规模就至少要有一条可证伪预测。
# 这条替代了原先的 attack_density / method_low_diversity 配额——配额能被凑，
# 而「如果 X 则 Y 会坏」凑不出来：写了就得能被执行出来。
HYPOTHESIS_MIN_RECORDS = 20
# 全正向台账的判定门槛：达到此规模却一条非正向路径都没有，说明本轮没构造失效条件
NO_ATTACK_MIN_RECORDS = 5

INFER_RULES = (
    ("accessibility", r"无障碍|键盘|焦点|读屏|对比度|aria|tabindex|目标尺寸|点击区域"),
    ("ui_content", r"文案|错别字|歧义|按钮语义|未渲染|占位符|\{\{|undefined|提示误导|单位|术语"),
    ("usability", r"易用|防重|重复提交|加载态|loading|骨架|绕路|重复输入|撤销|反悔|操作逻辑|可发现"),
    ("data_consistency", r"一致性|缓存|跨端|跨会话|跨视图|对账|回显|同步|脏读"),
    ("regression", r"回归|旧功能|影响面|旧版本"),
    ("boundary", r"边界|极限|超长|超限|空值|上界|下界|0 ?份|最大值|最小值|长度"),
    ("permission", r"权限|角色|越权|未登录|他人|租户|无权限|越界访问"),
    ("exception", r"异常|失败|错误|超时|中断|重试|回滚|静默"),
    ("combo", r"组合|同时|并发|交叉|序列|连续|交替"),
)


def _method_ids():
    """方法库稳定 ID：以 adversarial_data 为单一事实源，避免两处枚举漂移。"""
    from adversarial_data import METHOD_IDS
    return set(METHOD_IDS)


def _load(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".csv":
        return list(csv.DictReader(text.splitlines()))
    # 整体 JSON（数组或 {"records": [...]}）优先，其次按 JSONL 逐行解析
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return data["records"]
    records = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def _normalize_events(records):
    """执行台账事件流（BEGIN/FINISH/…）→ 仅供本脚本消费的记录列表。

    只有 FINISH 带判定与攻击字段；BEGIN 里的 case_id 用于补全记录身份。
    传入普通记录列表时原样返回。
    """
    if not records or not any(isinstance(r, dict) and r.get("kind") for r in records):
        return records
    cases = {r.get("attempt_id"): r.get("case_id") for r in records
             if isinstance(r, dict) and r.get("kind") == "BEGIN"}
    out = []
    for rec in records:
        if not isinstance(rec, dict) or rec.get("kind") != "FINISH":
            continue
        item = dict(rec)
        item.setdefault("id", rec.get("case_id") or cases.get(rec.get("attempt_id")))
        item["case_id"] = rec.get("case_id") or cases.get(rec.get("attempt_id"))
        item["desc"] = rec.get("actual")
        evidence = rec.get("evidence")
        if isinstance(evidence, list):
            item["evidence"] = [e.get("path") if isinstance(e, dict) else e for e in evidence]
        out.append(item)
    return out


def _infer_path_type(text):
    for name, pattern in INFER_RULES:
        if re.search(pattern, text):
            return name
    return "positive"


def _evidence_list(rec):
    evidence = rec.get("evidence")
    if isinstance(evidence, str):
        return [evidence.strip()] if evidence.strip() else []
    if isinstance(evidence, list):
        paths = []
        for item in evidence:
            value = item.get("path") if isinstance(item, dict) else item
            if isinstance(value, str) and value.strip():
                paths.append(value.strip())
        return paths
    return []


def _oracle_unproven(rec):
    """声明 independent 但拿不出独立通道 → 返回原因，否则 None。"""
    channel = str(rec.get("oracle_channel") or "").strip()
    if not channel:
        return "未声明 oracle_channel（独立通道是什么）"
    hit = [h for h in SELF_ATTEST_HINTS if h in channel]
    if hit:
        return f"oracle_channel 只写了被测物自述（命中「{hit[0]}」），不是独立通道"
    if not _evidence_list(rec):
        return "没有证据路径，独立判据无法复核"
    return None


def _load_atk(path):
    """ATK 登记文件：数组，或 {"assumptions"/"atk"/"entries": [...]}。

    ATK 走独立文件而非台账字段——台账的 attack 字段是白名单校验，塞入 ATK 专用
    字段会破坏既有契约；ATK 也不需要进入执行台账，它登记时即为合法条目。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        for key in ("assumptions", "atk", "entries"):
            if isinstance(data.get(key), list):
                return data[key]
        raise ValueError(f"{path}: 对象形态须含 assumptions/atk/entries 数组")
    if isinstance(data, list):
        return data
    raise ValueError(f"{path}: ATK 登记必须为数组或含数组的对象")


def _normalise_atk_severity(value):
    """ATK 严重度归一：显式登记优先，否则按影响面推定。非法值回落到推定值。"""
    text = str(value or "").strip().lower()
    return text if text in ATK_SEVERITIES else None


def _normalise_atk(entries):
    """归一 ATK 条目并校验字段。返回 (归一列表, 错误列表)。"""
    normalised, errors = [], []
    seen = set()
    for index, item in enumerate(entries or []):
        name = f"atk[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{name}: 必须为对象")
            continue
        unknown = set(item) - set(ATK_REQUIRED) - {"linked_tc", "linked_ac", "severity", "note"}
        if unknown:
            errors.append(f"{name}: 含未知字段 {sorted(unknown)}")
            continue
        missing = [key for key in ATK_REQUIRED if key not in item]
        if missing:
            errors.append(f"{name}: 缺少必填字段 {missing}")
            continue
        ident = str(item["id"]).strip()
        if not ATK_ID_RE.match(ident):
            errors.append(f"{name}.id: 必须以 ATK- 前缀开头（实为 {ident or '缺失'}）")
            continue
        if ident in seen:
            errors.append(f"{name}.id: 重复的攻击假设 ID {ident}")
            continue
        seen.add(ident)
        hypothesis = str(item["hypothesis"]).strip()
        if not hypothesis:
            errors.append(f"{name}.hypothesis: 模糊假设不得为空")
            continue
        validation_path = str(item["validation_path"]).strip()
        if not validation_path:
            errors.append(f"{name}.validation_path: 验证路径不得为空（人工步骤/场景推演/代码走读均可）")
            continue
        impact = str(item["impact"]).strip()
        if impact not in ATK_IMPACTS:
            errors.append(f"{name}.impact: 必须为 {list(ATK_IMPACTS)} 之一")
            continue
        status = str(item["status"]).strip()
        if status not in ATK_STATUSES:
            errors.append(f"{name}.status: 必须为 {list(ATK_STATUSES)} 之一")
            continue
        normalised.append({
            "id": ident,
            "hypothesis": hypothesis,
            "validation_path": validation_path,
            "impact": impact,
            "status": status,
            "linked_tc": item.get("linked_tc"),
            "linked_ac": item.get("linked_ac"),
            "severity": _normalise_atk_severity(item.get("severity")),
            "note": str(item.get("note") or "").strip() or None,
        })
    return normalised, errors


def _closed_review_ids(reviews):
    """从复核记录中取出「已裁定闭环」的 finding_id 集合。

    与 references/finding-triage.md 的候选状态机对齐：
    - `accepted`：判据成立、判定为产品侧问题。**tie_to_bug 为空视为未闭环**
      ——必须补建 BUG 并关联后才能解除门禁影响；
    - `rejected`：判定为误报/判据不成立，已定性，不再压上限；
    - `unresolved`：证据不足或判据缺失，**尚未裁定**，仍须压低上限。

    verdict 缺失或非法时按未闭环处理——不得因为"有一条记录"就当作已裁定。

    复核 JSONL 为追加写入（状态机单向推进），同一 finding 可能有多条记录；
    判定只认每条 finding 的**最新**记录（latest-wins），否则历史记录会永久
    决定结果——先 accepted 未闭环、后补 tie_to_bug 的候选会被误判闭环，
    翻案后的旧 rejected 也会盖住新裁定。与 adversarial_gate 侧的折叠口径一致。
    """
    latest = {}
    for item in reviews or []:
        if not isinstance(item, dict):
            continue
        finding_id = str(item.get("finding_id") or "").strip()
        if not finding_id:
            continue
        latest[finding_id] = item
    closed = set()
    for finding_id, item in latest.items():
        verdict = str(item.get("verdict") or "").strip()
        if verdict == "rejected":
            closed.add(finding_id)
        elif verdict == "accepted":
            # accepted 但未建 BUG/未关联 → 未闭环（与 execution_ledger 的
            # accepted+tie_to_bug 为空视为未闭环同一口径）
            if str(item.get("tie_to_bug") or "").strip():
                closed.add(finding_id)
    return closed


def analyse_atk(entries):
    """ATK 轨道的诊断与门禁判定。

    与需求 SURF 轨道的关键差异：
    - 「未执行」不触发待核实上限——允许长期悬置，这是保留假设锐度的有意让步；
    - 「命中」且影响面属功能/数据/权限 → 升级为严重度复核候选；
    - 其余状态（执行中/未命中/无法验证/体验类命中）只披露，不压上限。

    本函数只做登记侧诊断（不看复核记录），闭环判定在 analyse() 中进行——
    复核状态在此处不可见，否则子结论会与顶层结论自相矛盾。
    """
    normalised, errors = _normalise_atk(entries)
    counts = {status: 0 for status in ATK_STATUSES}
    for item in normalised:
        counts[item["status"]] += 1
    candidates = [item for item in normalised
                  if item["status"] == "命中" and item["impact"] in ATK_SEVERITY_IMPACTS]
    not_hit = [item["id"] for item in normalised if item["status"] == "未命中"]
    unexecuted = [item["id"] for item in normalised if item["status"] == "未执行"]
    return {
        "registered": len(normalised),
        "status_counts": counts,
        "hit": counts["命中"],
        "not_hit": len(not_hit),
        "not_hit_ids": not_hit,
        "unexecuted": len(unexecuted),
        "unexecuted_ids": unexecuted,
        "severity_candidates": [
            {"id": item["id"], "impact": item["impact"],
             "severity": item["severity"] or ATK_DEFAULT_SEVERITY.get(item["impact"], "minor"),
             "reason": "攻击假设命中且影响面属功能/数据/权限，须裁定并闭环"}
            for item in candidates],
        "entries": normalised,
        "errors": errors,
        "scope": "只判 ATK 假设的登记完整性与命中升级，不判缺陷有无、不替代缺陷复核与门禁",
    }


def analyse(records, density_threshold=DEFAULT_DENSITY_THRESHOLD,
            independent_rate=DEFAULT_INDEPENDENT_RATE,
            residual_note=None, plan_surfaces=None, plan_risks=None,
            surf_linkage_threshold=DEFAULT_SURF_LINKAGE, amend_note=None,
            atk_entries=None, reviews=None):
    records = _normalize_events(records)
    total = len(records)
    path_counts = {k: 0 for k in PATH_TYPES}
    path_unknown = 0
    inferred_counts = {k: 0 for k in PATH_TYPES}
    oracle = {k: 0 for k in ORACLE_SOURCES}
    modes = {k: 0 for k in DISCOVERY_MODES}
    surf_linked = 0
    surf_bad_format = []
    surf_new = []
    methods = {}
    declared_method_missing = 0
    method_unknown = []
    unproven = []
    probes_total = 0
    hyp_records = 0
    hyp_broken = 0
    hyp_held = 0
    hyp_blocked = 0
    hyp_without_surface = []
    per_surf = {}

    known_methods = _method_ids()

    for rec in records:
        if not isinstance(rec, dict):
            continue
        ptype = str(rec.get("path_type") or "").strip().lower()
        if ptype in PATH_TYPES:
            path_counts[ptype] += 1
        else:
            path_unknown += 1
        text = " ".join(str(rec.get(k) or "") for k in ("id", "desc", "title", "note", "text"))
        inferred_counts[_infer_path_type(text)] += 1

        source = str(rec.get("oracle_source") or "").strip().lower()
        source = source if source in ORACLE_SOURCES else "unknown"
        oracle[source] += 1
        if source == "independent":
            why = _oracle_unproven(rec)
            if why:
                unproven.append((rec.get("id") or rec.get("case_id") or "?", why))

        mode = str(rec.get("discovery_mode") or "").strip().lower()
        modes[mode if mode in DISCOVERY_MODES else "undetermined"] += 1

        surf = str(rec.get("surf_id") or "").strip()
        if surf:
            if not SURF_ID_RE.match(surf):
                surf_bad_format.append(surf)
            elif plan_surfaces is not None and surf not in plan_surfaces:
                # 计划外发现不是违规：未知问题的定义就是"不在计划里"，罚它等于
                # 从制度上劝阻发现未知问题（实测 E2）。单列计数，供 report 呈现。
                surf_new.append(surf)
            else:
                surf_linked += 1

        method = str(rec.get("method") or "").strip()
        if method:
            if method not in known_methods:
                method_unknown.append(method)
            else:
                methods[method] = methods.get(method, 0) + 1
        else:
            declared_method_missing += 1

        # —— 失效假设：每条记录可声明"如果 X 则 Y 会坏"的可证伪预测与结果 ——
        hypothesis = str(rec.get("hypothesis") or "").strip()
        if hypothesis:
            if not surf:
                hyp_without_surface.append(rec.get("id") or rec.get("case_id") or "?")
            hyp_records += 1
            outcome = str(rec.get("hypothesis_outcome") or "").strip().lower()
            if outcome not in ("broken", "held", "blocked"):
                status = str(rec.get("status") or "").strip().upper()
                outcome = {"FAIL": "broken", "BLOCKED": "blocked"}.get(status, "held")
            if outcome == "broken":
                hyp_broken += 1
            elif outcome == "blocked":
                hyp_blocked += 1
            else:
                hyp_held += 1

        if surf and SURF_ID_RE.match(surf):
            bucket = per_surf.setdefault(surf, {"records": 0, "methods": set(),
                                                "hypotheses": 0, "broken": 0, "held": 0,
                                                "blocked": 0})
            bucket["records"] += 1
            if method in known_methods:
                bucket["methods"].add(method)
            if hypothesis:
                bucket["hypotheses"] += 1
                outcome = str(rec.get("hypothesis_outcome") or "").strip().lower()
                if outcome not in ("broken", "held", "blocked"):
                    status = str(rec.get("status") or "").strip().upper()
                    outcome = {"FAIL": "broken", "BLOCKED": "blocked"}.get(status, "held")
                bucket[outcome] += 1

        probes = rec.get("probes")
        if isinstance(probes, int) and probes > 0:
            probes_total += probes

    non_positive = sum(v for k, v in path_counts.items() if k != "positive")
    # 分母只取显式声明了 path_type 的条目：字段全缺时报 NA，不以 0 冒充"一条非正向都没有"
    declared_total = total - path_unknown
    density = round(non_positive / declared_total, 4) if declared_total else "NA"
    independent_total = oracle["independent"] + oracle["self_attested"]
    indep_rate = (round(oracle["independent"] / independent_total, 4)
                  if independent_total else "NA")
    surf_rate = round(surf_linked / total, 4) if total else "NA"
    method_diversity = len(methods)
    single_method_surfs = sorted(s for s, b in per_surf.items()
                                 if len(b["methods"]) <= 1 and b["records"] >= 3)

    gaps = []
    notices = []
    if not total:
        gaps.append({"code": "empty_ledger", "message": "台账为空，无任何执行记录"})
    if path_unknown:
        gaps.append({"code": "path_type_missing",
                     "message": f"{path_unknown}/{total} 条未声明 path_type（"
                                + "/".join(PATH_TYPES) + "），攻击密度无法机械计算；"
                                "推断值仅供参考，不作判据"})
    # 绝对零而非比例：比例能被凑，而"一条非正向路径都没有"凑不出来。
    if declared_total >= NO_ATTACK_MIN_RECORDS and non_positive == 0:
        gaps.append({"code": "no_attack_paths",
                     "message": f"{declared_total} 条记录全部是 positive：没有任何"
                                "边界/异常/权限/组合/界面/一致性/回归路径，"
                                "本轮只确认了功能可用，没有构造让它坏的条件"})
    if unproven:
        detail = "；".join(f"{rid}: {why}" for rid, why in unproven[:5])
        gaps.append({"code": "oracle_independence_unproven",
                     "message": f"{len(unproven)} 条声明 independent 但拿不出独立通道：{detail}"})
    # 一条独立通道都没有，是"证据结构缺失"，与"比例偏低"不是一件事：
    # 比例可凑，零独立通道凑不出来（每条 independent 都要求点名通道 + 带证据）。
    if total >= 10 and oracle["independent"] == 0:
        gaps.append({"code": "oracle_no_independent_evidence",
                     "message": f"{total} 条记录中没有任何 independent 判据："
                                "结论全靠自证或未声明，无法排除同源假绿"})
    # —— 失效假设：替代"配额"的核心判据 ——
    required_surfaces = {s for s, risk in (plan_risks or {}).items()
                         if str(risk).strip().lower() in ("high", "critical")}
    covered = {s for s, b in per_surf.items() if b["hypotheses"] > 0}
    missing_hyp = sorted(required_surfaces - covered)
    if missing_hyp:
        gaps.append({"code": "surface_without_hypothesis",
                     "message": f"高风险风险面没有任何可证伪预测：{missing_hyp[:6]}"
                                "（每个高风险面至少要有一条「如果 X 则 Y 会坏」的构造）"})
    if plan_risks is None and total >= HYPOTHESIS_MIN_RECORDS and not hyp_records:
        gaps.append({"code": "no_falsifiable_hypothesis",
                     "message": f"{total} 条记录里没有一条声明失效假设（hypothesis）："
                                "无法区分「确认功能可用」与「构造让它坏的条件」"})
    if hyp_without_surface:
        gaps.append({"code": "hypothesis_without_surface",
                     "message": f"{len(hyp_without_surface)} 条失效假设未关联风险面："
                                f"{hyp_without_surface[:5]}（假设必须挂到 surf_id 才能判覆盖）"})
    if surf_bad_format:
        gaps.append({"code": "surf_format_invalid",
                     "message": f"surf_id 形态非法（需 SURF- 前缀 + 稳定标识）：{surf_bad_format[:5]}"})
    if declared_method_missing:
        gaps.append({"code": "method_missing",
                     "message": f"{declared_method_missing}/{total} 条未标注探针方法（方法库 ID），"
                                "无法核对方法多样性"})
    if method_unknown:
        gaps.append({"code": "method_unknown",
                     "message": f"method 不在方法库枚举内：{sorted(set(method_unknown))[:5]}"})
    if not gaps and not (isinstance(residual_note, str) and residual_note.strip()):
        gaps.append({"code": "residual_note_missing",
                     "message": "结论将达「通过」但未声明未破的攻击面（--residual-note）："
                                "无法区分「真的没问题」与「没找到让它失败的输入」"})

    # —— ATK 独立轨道：与需求 SURF 轨道分开判定 ——
    # 「未执行」不压上限（允许长期悬置）；「命中」且影响面属功能/数据/权限时
    # 升级为严重度复核候选，已裁定闭环的（见 _closed_review_ids）不再重复压上限。
    atk = analyse_atk(atk_entries)
    if atk["errors"]:
        gaps.append({"code": "atk_registry_invalid",
                     "message": f"ATK 登记存在 {len(atk['errors'])} 处问题：{atk['errors'][:5]}"
                                "（攻击假设须含 ID、模糊假设、验证路径、影响面与验证状态）"})
    reviewed = _closed_review_ids(reviews)
    pending_candidates = [c for c in atk["severity_candidates"] if c["id"] not in reviewed]
    if pending_candidates:
        gaps.append({"code": "atk_hit_unreviewed",
                     "message": "攻击假设命中且影响面属功能/数据/权限，尚未裁定闭环："
                                + "、".join(c["id"] for c in pending_candidates)
                                + "（须复核并闭环；结论上限为待核实）"})
    if atk["unexecuted"]:
        notices.append({"code": "atk_unexecuted",
                        "message": f"{atk['unexecuted']} 条攻击假设尚未执行："
                                   f"{atk['unexecuted_ids'][:6]}（允许长期悬置，不压结论上限）"})
    # ATK 子结论与顶层结论必须一致：闭环判定在上一段完成后才写子结论，
    # 否则会出现「顶层通过、atk 子结论待核实」的自相矛盾产物。
    atk["pending_candidates"] = pending_candidates
    atk["closed_candidates"] = [c for c in atk["severity_candidates"] if c["id"] in reviewed]
    atk["verdict_ceiling"] = "待核实" if pending_candidates else "通过"

    # —— 披露项：只报数，不压上限。配比可凑，凑出来的达标不代表打得深 ——
    if isinstance(density, float) and density < density_threshold:
        notices.append({"code": "attack_density_low",
                        "message": f"攻击密度 {density} 低于参考值 {density_threshold}："
                                   f"非正向 {non_positive}/{total}（仅披露，不作判据）"})
    if total and isinstance(surf_rate, float) and surf_rate < surf_linkage_threshold:
        notices.append({"code": "surf_linkage_low",
                        "message": f"{surf_linked}/{total} 条关联到计划内风险面"
                                   "（仅披露；计划外发现计入 surfaces_beyond_plan）"})
    if single_method_surfs:
        notices.append({"code": "single_method_surface",
                        "message": f"以下风险面只用了 1 种方法且记录 ≥3 条：{single_method_surfs[:5]}；"
                                   "若其假设全部「未打破」，建议换手法复打"})
    if surf_new:
        notices.append({"code": "new_surface",
                        "message": f"发现 {len(set(surf_new))} 个计划外风险面：{sorted(set(surf_new))[:6]}"
                                   "（计为产出；需 amend 纳入后续计划，否则无法累积）"})
        if not (isinstance(amend_note, str) and amend_note.strip()):
            notices.append({"code": "new_surface_amend_missing",
                            "message": "计划外风险面未提交 amend 说明（--amend-note）："
                                       "下一轮该风险面不会有假设，发现会被丢掉"})

    return {
        "total": total,
        "path_type": {**path_counts, "unknown": path_unknown},
        "path_type_groups": {name: sum(path_counts[k] for k in keys)
                             for name, keys in PATH_TYPE_GROUPS.items()},
        "inferred_path_type": inferred_counts,
        "inferred_note": "关键词粗分，仅作诊断参考，不参与门禁判定",
        "attack_density": density,
        "density_threshold": density_threshold,
        "oracle_source": {**oracle, "independent_rate": indep_rate,
                          "unproven_independent": len(unproven)},
        "discovery_mode": modes,
        "surf_linked": surf_linked,
        "surf_linkage_rate": surf_rate,
        "surf_linkage_threshold": surf_linkage_threshold,
        "surf_plan_checked": plan_surfaces is not None,
        "surfaces_beyond_plan": len(set(surf_new)),
        "surfaces_beyond_plan_ids": sorted(set(surf_new)),
        "amend_note": (amend_note or "").strip() or None,
        "hypotheses": {"declared": hyp_records, "broken": hyp_broken,
                       "held": hyp_held, "blocked": hyp_blocked,
                       "break_rate": (round(hyp_broken / hyp_records, 4)
                                      if hyp_records else None)},
        "surface_coverage": {s: {**b, "methods": sorted(b["methods"])}
                             for s, b in sorted(per_surf.items())},
        "single_method_surfaces": single_method_surfs,
        "methods": methods,
        "method_missing": declared_method_missing,
        "method_diversity": method_diversity,
        "probes_total": probes_total,
        "residual_note": (residual_note or "").strip() or None,
        "atk": atk,
        "gaps": gaps,
        "notices": notices,
        "verdict_ceiling": "待核实" if gaps else "通过",
        "scope": "只判攻击面覆盖与判据独立性，不判缺陷有无、不替代对抗门禁与探索门禁",
    }


def _emit_csv(result, path):
    rows = []
    for key in ("path_type", "oracle_source", "discovery_mode"):
        for name, value in result[key].items():
            rows.append({"section": key, "metric": name, "value": value})
    for name in ("total", "attack_density", "density_threshold", "surf_linked",
                 "surf_linkage_rate", "method_missing", "method_diversity",
                 "probes_total", "surfaces_beyond_plan", "verdict_ceiling"):
        rows.append({"section": "summary", "metric": name, "value": result[name]})
    for name, value in result["hypotheses"].items():
        rows.append({"section": "hypothesis", "metric": name, "value": value})
    for name, value in result["path_type_groups"].items():
        rows.append({"section": "path_type_group", "metric": name, "value": value})
    for name in ("registered", "hit", "not_hit", "unexecuted", "verdict_ceiling"):
        rows.append({"section": "atk", "metric": name, "value": result["atk"][name]})
    for candidate in result["atk"]["pending_candidates"]:
        rows.append({"section": "atk_candidate_pending", "metric": candidate["id"],
                     "value": f"{candidate['impact']}/{candidate['severity']}：{candidate['reason']}"})
    for candidate in result["atk"]["closed_candidates"]:
        rows.append({"section": "atk_candidate_closed", "metric": candidate["id"],
                     "value": f"{candidate['impact']}/{candidate['severity']}：已裁定闭环"})
    for entry in result["atk"]["entries"]:
        links = "、".join(x for x in (entry.get("linked_tc"), entry.get("linked_ac")) if x)
        rows.append({"section": "atk_entry", "metric": entry["id"],
                     "value": f"{entry['status']}/{entry['impact']}/{entry['severity'] or ''}"
                              f"：{entry['hypothesis']}"
                              + (f"（关联 {links}）" if links else "（未关联 TC/AC——不构成排除理由）")
                              + (f"｜{entry['note']}" if entry.get("note") else "")})
    for gap in result["gaps"]:
        rows.append({"section": "gap", "metric": gap["code"], "value": gap["message"]})
    for notice in result.get("notices") or []:
        rows.append({"section": "notice", "metric": notice["code"], "value": notice["message"]})
    with Path(path).open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=["section", "metric", "value"])
        writer.writeheader()
        writer.writerows(rows)


def _plan_surfaces(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    surfaces = data.get("surfaces")
    if not isinstance(surfaces, list):
        raise ValueError("plan 缺 surfaces 数组")
    return {str(s.get("id")) for s in surfaces if isinstance(s, dict) and s.get("id")}


def _plan_risks(path):
    """风险面 → 风险等级。高风险面必须至少有一条可证伪预测，这是"设计过攻击"的判据。"""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    surfaces = data.get("surfaces")
    if not isinstance(surfaces, list):
        raise ValueError("plan 缺 surfaces 数组")
    return {str(s.get("id")): s.get("risk") for s in surfaces
            if isinstance(s, dict) and s.get("id")}


def main(argv=None):
    parser = argparse.ArgumentParser(description="攻击面覆盖与判据独立性检查")
    parser.add_argument("--ledger", required=True,
                        help="执行台账（JSONL/CSV/执行事件流 events.jsonl）")
    parser.add_argument("--plan", help="对抗 plan（提供时 surf_id 与风险面清单交叉核对）")
    parser.add_argument("--residual-note",
                        help="未破攻击面声明；结论达「通过」前必填")
    parser.add_argument("--amend-note",
                        help="计划外风险面的纳入说明；缺省时只提示，不压上限")
    parser.add_argument("--emit-json", help="结论输出路径")
    parser.add_argument("--emit-csv", help="CSV 导出路径（UTF-8-sig）")
    parser.add_argument("--atk", help="ATK 攻击假设登记文件（数组或含数组的对象）；走独立校验轨道")
    parser.add_argument("--reviews", help="候选复核 JSONL 路径；用于判断命中的 ATK 是否已裁定闭环")
    parser.add_argument("--density-threshold", type=float, default=DEFAULT_DENSITY_THRESHOLD)
    parser.add_argument("--independent-rate", type=float, default=DEFAULT_INDEPENDENT_RATE)
    parser.add_argument("--surf-linkage", type=float, default=DEFAULT_SURF_LINKAGE)
    parser.add_argument("--enforce", action="store_true",
                        help="存在缺口时以退出码 2 结束（用于门禁链）")
    args = parser.parse_args(argv)
    try:
        records = _load(args.ledger)
        atk_entries = _load_atk(args.atk) if args.atk else None
        reviews = []
        if args.reviews:
            for line in Path(args.reviews).read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{args.reviews} 解析失败: {exc}") from exc
                reviews.append(item)
        result = analyse(records, args.density_threshold, args.independent_rate,
                         residual_note=args.residual_note,
                         plan_surfaces=_plan_surfaces(args.plan) if args.plan else None,
                         plan_risks=_plan_risks(args.plan) if args.plan else None,
                         surf_linkage_threshold=args.surf_linkage,
                         amend_note=args.amend_note,
                         atk_entries=atk_entries,
                         reviews=reviews)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        # 输入/解析错误以 2 退出——与包内其它脚本（adversarial_gate /
        # execution_ledger / challenge_summary / finding_summary）口径一致，
        # 不因裸抛异常变成退出码 1。
        print(f"攻击覆盖输入/校验错误: {exc}", file=sys.stderr)
        return 2
    if args.emit_json:
        Path(args.emit_json).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    if args.emit_csv:
        _emit_csv(result, args.emit_csv)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.enforce and result["gaps"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
