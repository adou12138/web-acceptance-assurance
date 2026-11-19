#!/usr/bin/env python3
"""attack_coverage.py — 攻击面覆盖与判据独立性检查（清单式验收的诊断与门禁）。

问题来源：2026-11-18 一次 154 项真实测试，143 项走正向路径、权限 0 项、异常 0 项、
组合 1 项，最终只产出 4 条 FAIL，其中 0 条来自为新攻击面设计的探针。原因是执行范式
是「覆盖确认」（确认功能可用）而非「失效构造」（证明它会坏）——用例数增长不等于
攻击面增长，而报告主指标是通过率，天然奖励"确认正常"。

本脚本只做三件事：
  1. 诊断：把台账摊成路径类型/判据来源/发现方式的分布，给出攻击密度；
  2. 门禁：密度与判据独立性不达标时压结论上限，避免"铺得全"冒充"打得深"；
  3. 反声明：字段填满不等于做过。surf_id 要与 plan 风险面核对、independent 要能
     指出独立通道且带证据、method 要落在方法库枚举内，否则不给"通过"。

纪律：
- 只按台账里**显式声明**的字段计算；字段缺失记 unknown 并列入缺口，不用启发式值
  充当机械判据（--infer 的粗分结果单列 inferred_*，仅供参考，不进 density）。
- 工具不判缺陷有无，只判"攻击面是否被真正打过"。

路径类型不是只有功能逻辑：界面与文案（ui_content）、易用性（usability）、无障碍
（accessibility）、数据一致性（data_consistency）、回归（regression）都是合法攻击
维度，与 boundary/exception 同等计入非正向路径。曾经因为只认功能五类，如实登记的
UI 类发现反而被压上限——那是制度性逆向激励。

用法：
    python scripts/attack_coverage.py --ledger ledger.jsonl
    python scripts/attack_coverage.py --ledger events.jsonl --plan adversarial-plan.json
    python scripts/attack_coverage.py --ledger ledger.csv --emit-json out.json --emit-csv out.csv
    python scripts/attack_coverage.py --ledger ledger.jsonl --residual-note "未破的攻击面…" --enforce
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

# 阈值依据：每个功能点在正向/边界/异常/权限/组合等路径中至少有一类非正向，
# 理论下限即 1/5=20%；取 30% 作为"每 3 条至少 1 条非正向"的最低门槛（非目标值）。
DEFAULT_DENSITY_THRESHOLD = 0.30
# 判据独立性：同源自证（用被测物自己的回显当 oracle）不得占多数
DEFAULT_INDEPENDENT_RATE = 0.30
# 风险面关联：至少一半的执行记录要能追溯到已登记失效假设
DEFAULT_SURF_LINKAGE = 0.50
# 方法多样性：记录数达此规模后，至少要出现的不同方法数
METHOD_DIVERSITY_MIN_RECORDS = 20
METHOD_DIVERSITY_MIN_METHODS = 3

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


def analyse(records, density_threshold=DEFAULT_DENSITY_THRESHOLD,
            independent_rate=DEFAULT_INDEPENDENT_RATE,
            residual_note=None, plan_surfaces=None,
            surf_linkage_threshold=DEFAULT_SURF_LINKAGE):
    records = _normalize_events(records)
    total = len(records)
    path_counts = {k: 0 for k in PATH_TYPES}
    path_unknown = 0
    inferred_counts = {k: 0 for k in PATH_TYPES}
    oracle = {k: 0 for k in ORACLE_SOURCES}
    modes = {k: 0 for k in DISCOVERY_MODES}
    surf_linked = 0
    surf_bad_format = []
    surf_unknown = []
    methods = {}
    declared_method_missing = 0
    method_unknown = []
    unproven = []
    probes_total = 0

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
                surf_unknown.append(surf)
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

    gaps = []
    if not total:
        gaps.append({"code": "empty_ledger", "message": "台账为空，无任何执行记录"})
    if path_unknown:
        gaps.append({"code": "path_type_missing",
                     "message": f"{path_unknown}/{total} 条未声明 path_type（"
                                + "/".join(PATH_TYPES) + "），攻击密度无法机械计算；"
                                "推断值仅供参考，不作判据"})
    if isinstance(density, float) and density < density_threshold:
        gaps.append({"code": "attack_density_low",
                     "message": f"攻击密度 {density} 低于门槛 {density_threshold}："
                                f"非正向路径 {non_positive}/{total}，本轮以确认功能可用为主"})
    if isinstance(indep_rate, float) and indep_rate < independent_rate:
        gaps.append({"code": "oracle_self_attested",
                     "message": f"判据独立性 {indep_rate} 低于门槛 {independent_rate}："
                                f"同源自证 {oracle['self_attested']} 条（用被测物自己的回显当 oracle）"})
    if unproven:
        detail = "；".join(f"{rid}: {why}" for rid, why in unproven[:5])
        gaps.append({"code": "oracle_independence_unproven",
                     "message": f"{len(unproven)} 条声明 independent 但拿不出独立通道：{detail}"})
    if not modes["designed_probe"]:
        gaps.append({"code": "no_designed_probe",
                     "message": "没有一条执行来自为失效假设设计的探针（designed_probe=0）："
                                "缺陷全是偶然撞见或方式未声明，不能算作攻击面已覆盖"})
    if total and surf_rate < surf_linkage_threshold:
        gaps.append({"code": "surf_linkage_low",
                     "message": f"仅 {surf_linked}/{total} 条关联到已登记风险面（surf_id）："
                                "攻击点未追溯到失效假设，无法判断覆盖了哪些失效方式"})
    if surf_bad_format:
        gaps.append({"code": "surf_format_invalid",
                     "message": f"surf_id 形态非法（需 SURF- 前缀 + 稳定标识）：{surf_bad_format[:5]}"})
    if surf_unknown:
        gaps.append({"code": "surf_unknown",
                     "message": f"surf_id 不在对抗 plan 的风险面清单内：{sorted(set(surf_unknown))[:5]}"})
    if declared_method_missing:
        gaps.append({"code": "method_missing",
                     "message": f"{declared_method_missing}/{total} 条未标注探针方法（方法库 ID），"
                                "无法核对方法多样性"})
    if method_unknown:
        gaps.append({"code": "method_unknown",
                     "message": f"method 不在方法库枚举内：{sorted(set(method_unknown))[:5]}"})
    if total >= METHOD_DIVERSITY_MIN_RECORDS and method_diversity < METHOD_DIVERSITY_MIN_METHODS:
        gaps.append({"code": "method_low_diversity",
                     "message": f"仅使用 {method_diversity} 种方法（记录 {total} 条，下限 "
                                f"{METHOD_DIVERSITY_MIN_METHODS}）：方法单一是「测了很多」的典型特征"})
    if not gaps and not (isinstance(residual_note, str) and residual_note.strip()):
        gaps.append({"code": "residual_note_missing",
                     "message": "结论将达「通过」但未声明未破的攻击面（--residual-note）："
                                "无法区分「真的没问题」与「没找到让它失败的输入」"})

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
        "methods": methods,
        "method_missing": declared_method_missing,
        "method_diversity": method_diversity,
        "probes_total": probes_total,
        "residual_note": (residual_note or "").strip() or None,
        "gaps": gaps,
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
                 "probes_total", "verdict_ceiling"):
        rows.append({"section": "summary", "metric": name, "value": result[name]})
    for name, value in result["path_type_groups"].items():
        rows.append({"section": "path_type_group", "metric": name, "value": value})
    for gap in result["gaps"]:
        rows.append({"section": "gap", "metric": gap["code"], "value": gap["message"]})
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


def main(argv=None):
    parser = argparse.ArgumentParser(description="攻击面覆盖与判据独立性检查")
    parser.add_argument("--ledger", required=True,
                        help="执行台账（JSONL/CSV/执行事件流 events.jsonl）")
    parser.add_argument("--plan", help="对抗 plan（提供时 surf_id 与风险面清单交叉核对）")
    parser.add_argument("--residual-note",
                        help="未破攻击面声明；结论达「通过」前必填")
    parser.add_argument("--emit-json", help="结论输出路径")
    parser.add_argument("--emit-csv", help="CSV 导出路径（UTF-8-sig）")
    parser.add_argument("--density-threshold", type=float, default=DEFAULT_DENSITY_THRESHOLD)
    parser.add_argument("--independent-rate", type=float, default=DEFAULT_INDEPENDENT_RATE)
    parser.add_argument("--surf-linkage", type=float, default=DEFAULT_SURF_LINKAGE)
    parser.add_argument("--enforce", action="store_true",
                        help="存在缺口时以退出码 2 结束（用于门禁链）")
    args = parser.parse_args(argv)

    records = _load(args.ledger)
    result = analyse(records, args.density_threshold, args.independent_rate,
                     residual_note=args.residual_note,
                     plan_surfaces=_plan_surfaces(args.plan) if args.plan else None,
                     surf_linkage_threshold=args.surf_linkage)
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
