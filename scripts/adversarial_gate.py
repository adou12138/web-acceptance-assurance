"""对抗补充门禁：按 adversarial-plan、事件与复核记录计算结论上限的补充约束。

定位：exploration gate（scripts/exploration_state.py）约束批次结论上限；本脚本在其
之上叠加对抗维度——只读输入文件，不修改原 gate 数据，也不替代缺陷、断言与证据判定。
两个门禁彼此独立：本脚本不 import exploration_state，只在调用方显式传入
--gate-summary 时读取其 gate.verdict_ceiling 作为基础上限；不传则只按对抗维度判定。

规则：
- plan 缺失或未登记任何 SURF/OBLIGATION（空 plan）：enforce=False 时视为未启用对抗，
  放行但附注；enforce=True 时为必选对抗——无豁免理由（waiver）则上限"待核实"，
  附豁免理由（waiver）则放行并在 notes 留痕，**豁免不上抬上限：
  verdict_ceiling 最高仍为"待核实"**（与 execution_ledger._adversarial_precondition
  的"豁免不得自授即通过"原则对齐；豁免是否合资格——只读且小任务——由台账侧
  机械校验，本脚本不掌握 read_only/case_count，只留痕并输出 waiver_granted 标志）；
- 覆盖面缺口（高风险 SURF 未登记探针、探针未注明 method/playbook_ref）→ 上限
  "待核实"（依赖 adversarial_data.coverage_gaps；缺字段的旧 plan 按未注明处理）；
- 必选高风险 SURF（risk=high）无对应已执行探针（ADV status=executed 且存在指向
  该 ADV 的 EVT 事件）→ 上限"待核实"；
- 存在 accepted 且严重度为 blocker/major 的未闭环复核（tie_to_bug 为空）→ 上限"不通过"；
- 存在 verdict=unresolved 的候选 → 上限"待核实"（不计有效缺陷也不计误报）；
- 抽样复核（scope 抽样）：从本轮功能面/候选风险面清单随机抽 N 项，检查是否已登记为
  SURF；抽中未登记且无豁免说明 → 上限"待核实"。豁免只解除抽样对单项的阻断，
  **不上抬其它规则已压低的上限**；门禁显式关闭时抽样只作披露不压上限；
- 最终上限取基础 gate 上限与对抗上限中更严格者（通过 < 待核实 < 不通过）。

结论函数 evaluate(plan, events, reviews, base_ceiling=None, enforce=True,
waiver=None, scenarios=None, scope=None, exemptions=None, sample_size=DEFAULT_SAMPLE_SIZE,
sample_seed=None) 为纯函数，便于单测；
CLI：python adversarial_gate.py [--plan plan.json] [--events events.jsonl]
[--reviews reviews.jsonl] [--gate-summary summary.json] [--no-enforce]
[--disable-reason 关闭理由] [--waiver 豁免理由] [--scope 功能面清单]
[--exemptions 登记豁免] [--sample-size N] [--sample-seed S]
成功输出 JSON 结论并以 0 退出，输入/解析错误以 2 退出。
产出技术结论的批次默认启用对抗门禁；确需关闭时用 --no-enforce 并给出关闭理由，
关闭状态与理由进入交付物，门禁不得被静默关闭。纯只读小任务用 --waiver 附理由豁免。
plan 读取复用 scripts/adversarial_data.py 的 load。
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

try:
    from adversarial_data import load as load_plan, coverage_gaps as plan_coverage_gaps, \
        scenario_gaps as plan_scenario_gaps
except ImportError:  # 直接脚本路径执行时回退同目录加载
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from adversarial_data import load as load_plan, coverage_gaps as plan_coverage_gaps, \
        scenario_gaps as plan_scenario_gaps

RANK = {"通过": 0, "待核实": 1, "不通过": 2}
RANK_NAME = {v: k for k, v in RANK.items()}
HIGH_SEVERITY = {"blocker", "major"}
# 抽样复核的默认抽样量：只决定复核工作量，不是登记指标
DEFAULT_SAMPLE_SIZE = 2


def _strictest(a, b):
    return RANK_NAME[max(RANK.get(a, 0), RANK.get(b, 0))]


def _normalize_scope(scope):
    """功能面/候选风险面清单 → 归一为 [{"id","surf_id"}]。

    清单是"本轮应当被挑战的功能面"，独立于 plan 的 SURF 台账——正因为独立，
    抽样才能查出"该登记却没登记"。条目可以是字符串（按 SURF id 空间解释）或
    对象（{"id": 功能面ID, "surf_id": 对应的 SURF id}）。
    """
    if scope is None:
        return []
    if not isinstance(scope, (list, tuple)):
        raise ValueError("scope 必须为数组")
    out = []
    for index, item in enumerate(scope):
        if isinstance(item, str):
            value = item.strip()
            if not value:
                raise ValueError(f"scope[{index}]: 不得为空白字符串")
            out.append({"id": value, "surf_id": value})
        elif isinstance(item, dict):
            ident = str(item.get("id") or "").strip()
            if not ident:
                raise ValueError(f"scope[{index}].id: 必须为非空字符串")
            surf_id = str(item.get("surf_id") or "").strip() or ident
            out.append({"id": ident, "surf_id": surf_id})
        else:
            raise ValueError(f"scope[{index}]: 必须为字符串或对象")
    return out


def _normalize_exemptions(exemptions):
    """登记豁免 → {功能面ID: 不登记理由}。

    豁免是"为什么不登记"的说明，不是通行证：它只解除抽样对单项的阻断，
    不上抬其它规则已压低的上限。
    """
    if exemptions is None:
        return {}
    out = {}
    if isinstance(exemptions, dict):
        items = list(exemptions.items())
    elif isinstance(exemptions, (list, tuple)):
        items = []
        for index, item in enumerate(exemptions):
            if not isinstance(item, dict):
                raise ValueError(f"exemptions[{index}]: 必须为对象")
            ident = str(item.get("id") or "").strip()
            reason = str(item.get("reason") or "").strip()
            if not ident:
                raise ValueError(f"exemptions[{index}].id: 必须为非空字符串")
            if not reason:
                raise ValueError(f"exemptions[{index}].reason: 必须为非空字符串")
            items.append((ident, reason))
    else:
        raise ValueError("exemptions 必须为数组或对象")
    for key, value in items:
        ident = str(key).strip()
        reason = str(value).strip()
        if not ident:
            raise ValueError("exemptions: 条目 id 不得为空白")
        if not reason:
            raise ValueError(f"exemptions[{ident}]: 豁免理由不得为空白")
        out[ident] = reason
    return out


def _sample_entries(entries, sample_size, seed):
    """从清单中确定性抽样（无放回）。

    抽样量只决定复核工作量，不是登记指标。同轮重跑复用同一批样本：未显式给
    sample_seed 时，种子由清单内容派生，同一份清单永远抽到同一批。
    """
    if sample_size is None:
        sample_size = DEFAULT_SAMPLE_SIZE
    if type(sample_size) is not int or sample_size < 0:
        raise ValueError("sample_size 必须为非负整数")
    if not entries:
        return [], seed
    if seed is None:
        # 种子按条目 ID 排序后派生：同一组功能面无论清单如何排列都抽到同一批，
        # 避免"仅调整清单顺序"就换掉样本、让复核结果不可比。
        digest = hashlib.sha256(
            "\n".join(sorted(e["id"] for e in entries)).encode("utf-8")).hexdigest()
        seed = int(digest[:12], 16)
    size = min(sample_size, len(entries))
    # 确定性抽样：按 sha256(种子 + 条目ID) 排序取前 N。
    # 不使用线性同余洗牌——LCG 的低阶位周期极短，实测位置分布严重偏斜
    # （3000 次抽样中第 4 位仅占 5.7%，期望 20%），抽样就查不出问题。
    # 哈希排序无放回、各位置机会均等，且同种子同清单结果恒定。
    keyed = sorted(
        entries,
        key=lambda e: hashlib.sha256(f"{seed}\x00{e['id']}".encode("utf-8")).hexdigest(),
    )
    return keyed[:size], seed


def _sampling_review(scope, exemptions, registered_ids, sample_size, sample_seed):
    """抽样复核：抽中的功能面是否已登记为 SURF。

    返回 (审查结论, 触发原因列表, 豁免说明列表)。
    - 清单缺失 → status=not_provided，**不得显示为"抽样通过"**；
    - 抽中已登记 → status=ok；
    - 抽中未登记但有豁免说明 → status=exempted，理由进交付物，不压上限；
    - 抽中未登记且无豁免 → status=gap，压"待核实"。
    """
    entries = _normalize_scope(scope)
    waiver_map = _normalize_exemptions(exemptions)
    if not entries:
        return {"status": "not_provided", "sample_size": 0, "sampled": [],
                "registered": [], "unregistered": [], "exempted": [], "unexplained": [],
                "sample_seed": sample_seed}, [], []
    picked, seed = _sample_entries(entries, sample_size, sample_seed)
    registered, unregistered = [], []
    for item in picked:
        if item["surf_id"] in registered_ids:
            registered.append(item)
        else:
            unregistered.append(item)
    exempted, unexplained = [], []
    for item in unregistered:
        reason = waiver_map.get(item["id"]) or waiver_map.get(item["surf_id"])
        if reason:
            exempted.append({"id": item["id"], "surf_id": item["surf_id"], "reason": reason})
        else:
            unexplained.append(item)
    status = "ok" if not unregistered else ("exempted" if not unexplained else "gap")
    result = {
        "status": status,
        "sample_size": len(picked),
        "scope_count": len(entries),
        "sampled": [item["id"] for item in picked],
        "registered": [item["id"] for item in registered],
        "unregistered": [item["id"] for item in unregistered],
        "exempted": exempted,
        "unexplained": [item["id"] for item in unexplained],
        "sample_seed": seed,
    }
    reasons = []
    if unexplained:
        reasons.append("抽样复核抽中未登记的功能面：" + "、".join(item["id"] for item in unexplained)
                       + "；未登记且无豁免说明，结论上限为待核实")
    return result, reasons, exempted


def evaluate(plan, events, reviews, base_ceiling=None, enforce=True, waiver=None, scenarios=None,
             scope=None, exemptions=None, sample_size=DEFAULT_SAMPLE_SIZE, sample_seed=None,
             disable_reason=None):
    """纯函数：返回对抗补充门禁结论（上限、启用标记、原因与统计）。

    enforce=True（默认）时对抗循环为必选：空 plan 无豁免理由压上限"待核实"，
    附 waiver 理由则放行留痕，但**豁免不上抬上限**——ceiling 最高仍为
    "待核实"，与台账侧豁免语义一致。enforce=False 需显式关闭（CLI 侧要求理由），
    关闭状态进入交付物，不得静默关闭。
    scenarios 为对抗场景数组（SCN-ADV），缺省按未设计场景计入缺口。
    scope 为本轮功能面/候选风险面清单（独立于 plan 台账），用于抽样复核：
    抽中未登记且无豁免说明时压上限"待核实"；门禁关闭时抽样只作披露。
    """
    reasons, notes = [], []
    waiver_granted = False
    surfaces = (plan or {}).get("surfaces") or []
    obligations = (plan or {}).get("obligations") or []
    events = list(events or [])
    reviews = list(reviews or [])

    if not enforce:
        # 门禁显式关闭：关闭状态必须可见，且理由须进交付物（无理由则留痕告警）。
        if str(disable_reason or "").strip():
            notes.append(f"对抗门禁未启用：{str(disable_reason).strip()}"
                         "（关闭状态与理由须在交付物中原样呈现）")
        else:
            notes.append("对抗门禁未启用：使用方须在交付物声明「对抗门禁未启用」及理由；"
                         "未声明的理由不得视为默认同意")

    registered_ids = {s.get("id") for s in surfaces if isinstance(s, dict) and s.get("id")}
    sampling, sampling_reasons, exempted_items = _sampling_review(
        scope, exemptions, registered_ids, sample_size, sample_seed)
    if sampling["status"] == "not_provided":
        notes.append("未提供功能面清单：未执行抽样复核（不等于抽样通过）")
    if not enforce:
        # 门禁显式关闭时抽样只作披露，不压上限
        if sampling_reasons:
            notes.append("对抗门禁未启用：抽样复核结果仅作披露，不压结论上限（"
                         + "；".join(sampling_reasons) + "）")
        sampling_reasons = []
    else:
        reasons.extend(sampling_reasons)
    if exempted_items:
        notes.append("登记豁免（不压上限，理由进交付物）：" + "；".join(
            f"{item['id']}：{item['reason']}" for item in exempted_items))

    if not surfaces and not obligations:
        if enforce and not waiver:
            empty_ceiling = _strictest("待核实", base_ceiling or "通过")
            return {
                "adversarial_enabled": False,
                "verdict_ceiling": empty_ceiling,
                "reasons": ["对抗循环未启用：未登记任何风险面与对抗义务，且未提供豁免理由"
                            f"（对抗必选）；本轮结论上限为{empty_ceiling}"] + sampling_reasons,
                "notes": notes,
                "waiver_granted": False,
                "sampling": sampling,
                "adversarial_disabled": False,
                "disable_reason": None,
                "stats": {"probes": 0, "effective_bugs": 0, "false_positives": 0, "unresolved": 0,
                          "high_risk_surfaces": 0, "high_risk_uncovered": 0},
                "scope": "只读输入的对抗补充上限；不替代缺陷、断言与证据判定，报告结论不得高于本上限",
            }
        if enforce and waiver:
            # A1 修复（审核四）：豁免只解除"对抗循环未启用"的阻断，不上抬上限——
            # 与台账 "豁免不得自授即通过" 对齐；豁免是否合资格由台账机械校验。
            waiver_granted = True
            empty_ceiling = _strictest("待核实", base_ceiling or "通过")
            notes.append(f"对抗循环豁免：{waiver}（enforce 模式下凭豁免理由放行，豁免留痕；"
                         f"豁免不上抬上限，本轮 verdict_ceiling 为 {empty_ceiling}；豁免合资格与否"
                         "——只读且小任务——由 execution_ledger 台账侧机械校验）")
        else:
            # enforce=False（显式关闭）：关闭状态必须可见，且不得看起来像
            # "已完成对抗"——门禁自报上限与台账侧（execution_ledger 对
            # disabled 状态一律压"待核实"）必须一致，否则同一批产物出现
            # 两个上限，使用方按门禁输出写"通过"而台账判"待核实"。
            empty_ceiling = _strictest(base_ceiling or "通过", "待核实")
            notes.append("空 plan：未登记任何风险面与对抗义务；对抗门禁已显式关闭，"
                         "按基础 gate 上限与「关闭态不得自证通过」取更严者")
        return {
            "adversarial_enabled": False,
            "verdict_ceiling": empty_ceiling,
            "reasons": sampling_reasons,
            "notes": notes,
            "waiver_granted": waiver_granted,
            "sampling": sampling,
            "adversarial_disabled": not enforce,
            "disable_reason": str(disable_reason).strip() if disable_reason else None,
            "stats": {"probes": 0, "effective_bugs": 0, "false_positives": 0, "unresolved": 0,
                      "high_risk_surfaces": 0, "high_risk_uncovered": 0},
        }

    events = [e for e in events if isinstance(e, dict)]
    reviews = [r for r in reviews if isinstance(r, dict)]
    # 复核 JSONL 为追加写入（finding-triage.md：状态机单向推进），同一 finding
    # 可能有多条记录（先 accepted 未闭环、补建 BUG 后追加 accepted+tie，或
    # unresolved 之后翻案）。判定只认每条 finding 的**最新**记录——逐条判会把
    # 历史记录永久压上限，已闭环候选永远洗不白。与 attack_coverage._closed_review_ids
    # 的 latest-wins 口径一致。
    latest_reviews = {}
    for item in reviews:
        fid = str(item.get("finding_id") or "").strip()
        if fid:
            latest_reviews[fid] = item
    reviews = list(latest_reviews.values())
    plan_id = (plan or {}).get("plan_id", "")
    obligation_ids = {o.get("id") for o in obligations if isinstance(o, dict)}
    executed_by_surface = {}
    for obligation in obligations:
        if not isinstance(obligation, dict) or obligation.get("status") != "executed":
            continue
        adv_id = obligation.get("id")
        if any(e.get("probe_ref") == adv_id for e in events):
            executed_by_surface.setdefault(obligation.get("surface_ref"), set()).add(adv_id)

    high_risk = [s for s in surfaces if isinstance(s, dict) and s.get("risk") == "high"]
    uncovered = [s["id"] for s in high_risk if s.get("id") not in executed_by_surface]
    if uncovered:
        reasons.append("必选高风险风险面无对应已执行探针：" + "、".join(sorted(uncovered))
                       + "；结论上限为待核实（风险面不可用单条 PASS 抵消）")

    # 方法/模板覆盖面：计划层面就要有依据，不靠执行期才发现“只用了 challenge”
    plan_gaps = [gap for gap in plan_coverage_gaps(plan or {})
                 if "未登记任何风险面" not in gap]  # 空 plan 已在上方分支处理
    method_gaps = [gap for gap in plan_gaps if "method/playbook_ref" in gap]
    if method_gaps:
        reasons.append("探针未注明方法/挑刺模板：" + "；".join(method_gaps[:6])
                       + "；结论上限为待核实（无法核对方法与模板是否有效使用）")

    # 多样性缺口：方法单一/模板过窄同样视为未达成“全面”
    diversity_gaps = [gap for gap in plan_gaps
                      if "方法过于单一" in gap or "挑刺模板覆盖过窄" in gap]
    if diversity_gaps:
        reasons.append("方法/模板多样性不足：" + "；".join(diversity_gaps)
                       + "；结论上限为待核实（多样化方法未实际使用）")

    # 场景缺口：目标要求场景设计，零场景或只有单点不算全面
    sc_gaps = plan_scenario_gaps(plan or {}, scenarios)
    if sc_gaps:
        reasons.append("对抗场景缺口：" + "；".join(sc_gaps)
                       + "；结论上限为待核实（组合/序列场景未设计）")

    unresolved = [r.get("finding_id") for r in reviews
                  if r.get("verdict") == "unresolved"]
    if unresolved:
        reasons.append("存在未裁定候选：" + "、".join(sorted(str(x) for x in unresolved))
                       + "；不计有效缺陷也不计误报，结论上限为待核实")

    unclosed_blocker = [r.get("finding_id") for r in reviews
                        if r.get("verdict") == "accepted"
                        and r.get("severity") in HIGH_SEVERITY
                        and not r.get("tie_to_bug")]
    if unclosed_blocker:
        reasons.append("存在 accepted 且严重度为 blocker/major 的未闭环缺陷候选："
                       + "、".join(sorted(str(x) for x in unclosed_blocker)) + "；结论上限为不通过")

    adversarial_ceiling = "通过"
    for reason in reasons:
        if "不通过" in reason:
            adversarial_ceiling = "不通过"
            break
        if "待核实" in reason:
            adversarial_ceiling = "待核实"

    ceiling = _strictest(adversarial_ceiling, base_ceiling) if base_ceiling else adversarial_ceiling
    if not enforce:
        # 关闭态与台账侧口径对齐：门禁显式关闭时不得自证"通过"——
        # 台账（execution_ledger）对 disabled 状态一律压"待核实"，
        # 门禁若自报"通过"，同一批产物会出现两个上限。
        ceiling = _strictest(ceiling, "待核实")
    accepted = [r for r in reviews if r.get("verdict") == "accepted"]
    stats = {
        "plan_id": plan_id,
        "probes": sum(1 for o in obligations if isinstance(o, dict) and o.get("status") == "executed"),
        "events": len(events),
        "effective_bugs": sum(1 for r in accepted if r.get("tie_to_bug")),
        "false_positives": sum(1 for r in reviews if r.get("verdict") == "rejected"),
        "unresolved": len(unresolved),
        "high_risk_surfaces": len(high_risk),
        "high_risk_uncovered": len(uncovered),
        "obligations_without_method": sum(1 for o in obligations
                                          if isinstance(o, dict) and not o.get("method") and not o.get("playbook_ref")),
        "scenarios": len(scenarios or []),
        "scenario_gaps": len(sc_gaps),
        "sampling_status": sampling["status"],
        "sampled": sampling["sample_size"],
        "sampling_unexplained": len(sampling["unexplained"]),
        "sampling_exempted": len(sampling["exempted"]),
    }
    return {
        "adversarial_enabled": True,
        "verdict_ceiling": ceiling,
        "reasons": reasons,
        "notes": notes,
        "waiver_granted": waiver_granted,
        "sampling": sampling,
        "adversarial_disabled": not enforce,
        "disable_reason": str(disable_reason).strip() if disable_reason else None,
        "stats": stats,
        "scope": "只读输入的对抗补充上限；不替代缺陷、断言与证据判定，报告结论不得高于本上限",
    }


def load_jsonl(path):
    """逐行解析 JSONL；空行跳过，重复解析错误抛 ValueError，文件错误抛 OSError。"""
    records = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} 第 {number} 行解析失败: {exc}") from exc
    return records


def _base_ceiling_from_summary(summary):
    gate = (summary or {}).get("gate") or {}
    return gate.get("verdict_ceiling") or (summary or {}).get("verdict_ceiling")


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _load_scope(path):
    """功能面/候选风险面清单：接受数组，或 {"surfaces": [...]} / {"scope": [...]}。"""
    data = _load_json(path)
    if isinstance(data, dict):
        for key in ("surfaces", "scope", "items"):
            if isinstance(data.get(key), list):
                return data[key]
        raise ValueError(f"{path}: 对象形态须含 surfaces/scope/items 数组")
    if isinstance(data, list):
        return data
    raise ValueError(f"{path}: 功能面清单必须为数组或含数组的对象")


def _load_exemptions(path):
    """登记豁免：接受数组或 {id: reason} 对象。"""
    return _load_json(path)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="计算对抗补充门禁结论；只读输入，不修改原 gate 数据")
    parser.add_argument("--plan", help="adversarial-plan.json 路径（缺省视为未提供 plan）")
    parser.add_argument("--events", help="对抗事件 JSONL 路径")
    parser.add_argument("--reviews", help="候选复核 JSONL 路径")
    parser.add_argument("--gate-summary", help="exploration_state.py 摘要 JSON 路径，读取其 gate.verdict_ceiling 作为基础上限")
    parser.add_argument("--no-enforce", dest="no_enforce", action="store_true",
                        help="显式关闭对抗门禁（默认启用）；须同时给出 --disable-reason，关闭状态进交付物")
    parser.add_argument("--enforce", dest="enforce", action="store_true",
                        help="兼容参数：对抗门禁已默认启用，此参数为显式重申（与 --no-enforce 互斥）")
    parser.add_argument("--disable-reason", help="关闭对抗门禁的理由（配合 --no-enforce 使用；理由进交付物留痕）")
    parser.add_argument("--waiver", help="豁免理由（配合 enforce 使用，豁免留痕）")
    parser.add_argument("--scenarios", help="对抗场景伴随文件（SCN-ADV 数组）；缺省按未设计场景计入缺口")
    parser.add_argument("--scope", help="本轮功能面/候选风险面清单（数组或含数组的对象）；用于抽样复核")
    parser.add_argument("--exemptions", help="登记豁免文件（数组 [{id,reason}] 或 {id:reason}）；豁免只解除抽样阻断，不上抬上限")
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE,
                        help=f"抽样复核数量（默认 {DEFAULT_SAMPLE_SIZE}；只决定复核工作量，不是登记指标）")
    parser.add_argument("--sample-seed", type=int,
                        help="抽样种子；缺省由清单内容派生，同一份清单永远抽到同一批")
    args = parser.parse_args(argv)
    try:
        if args.no_enforce and not str(args.disable_reason or "").strip():
            raise ValueError("--no-enforce 须同时给出 --disable-reason：对抗门禁不得被静默关闭")
        if args.no_enforce and args.enforce:
            raise ValueError("--enforce 与 --no-enforce 互斥：对抗门禁默认启用，关闭须走 --no-enforce")
        plan = load_plan(args.plan) if args.plan else None
        events = load_jsonl(args.events) if args.events else []
        reviews = load_jsonl(args.reviews) if args.reviews else []
        base = None
        if args.gate_summary:
            base = _base_ceiling_from_summary(_load_json(args.gate_summary))
            if base is None:
                raise ValueError("gate-summary 中未找到 gate.verdict_ceiling，无法读取基础上限")
        result = evaluate(plan, events, reviews, base, enforce=not args.no_enforce,
                          waiver=args.waiver,
                          scenarios=(_load_json(args.scenarios) if args.scenarios else None),
                          scope=_load_scope(args.scope) if args.scope else None,
                          exemptions=_load_exemptions(args.exemptions) if args.exemptions else None,
                          sample_size=args.sample_size, sample_seed=args.sample_seed,
                          disable_reason=args.disable_reason)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"对抗补充门禁输入/校验错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
