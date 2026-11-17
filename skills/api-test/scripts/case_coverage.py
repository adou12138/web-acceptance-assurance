#!/usr/bin/env python3
"""用例执行率闭环：计划 vs 实际（wjx-api-test v4.36.0）。

为什么需要（两个真实缺口 + 一项产物化增量，读实现核验后确认）：
  ① **AF 业务场景族与声明维度口径不一致**：用例维度是 AF1/AF2/AF3，声明写的是
     `AF`。等值比对会把「三族都跑了」判成「AF 一条没跑」。族匹配统一由
     `dim_matches()` 提供（数字后缀属同族，字母续缀属另一维度）。
  ② **没有"计划 vs 实际"的逐条对账**：无权威计划清单落盘；`--only` 直接改写 cases 不留声明。

  产物化增量：`coverage_gate()` **已经**按 EXECUTED_STATUSES 过滤（只有 PASS/FAIL/PRECONDITION_UNMET 算触及），
  并把"全部被跳过"的维度记成「假覆盖」缺陷——判定本身是对的。本模块把它从 **stdout 提示**
  升级为**可落盘 / 可进报告 / 可 --enforce 的产物**，并给到用例级明细。

本模块**只加对账层，不改** case_runner / coverage_gate / 任何生成器：
  - `executed.total/rate` 是**严格口径**（只有 PASS/FAIL 算真判定）；
  - `executed.looseTotal` 是**宽松口径**（任一 result 即触及，含 SKIPPED/N/A；比 coverage_gate 更宽——门禁只认 EXECUTED_STATUSES，SKIPPED-only 维度被标「假覆盖」不算触及）；
  - 两者差异通过 `skippedOnlyDimensions` 落进产物（既有门禁只 stdout 打印），供人决策。

用法：
  python case_coverage.py --plan cases.json --results <prefix>_report_full.json \
      [--out case_coverage.json] [--expect-categories AF,业务场景] \
      [--exempt-categories "AF:本轮未启用大模型链路"] [--min-rate 0.8] [--enforce]

门禁（--enforce）：
  ① 未执行（SKIPPED/N-A）却没写 reason → 失败（沿用"原因必填"纪律）；
  ② 严格执行率低于 --min-rate → 失败；
  ③ 声明的类别既没覆盖也没显式豁免 → 失败。
  计划外 id（results 里出现 plan 之外的）只告警不阻塞。

状态词表（与 api_test_runner / case_runner 写入 results 的字面量一致）：
  PASS / FAIL  → 已执行（真判定）
  PRECONDITION_UNMET → 中间态（跑了请求、拿到响应，但结论既非通过也非失败）：
      不计严格已执行、不判「非法」，reason 必填；与 coverage_gate 的「触及」口径
      不同（门禁认它算触及），差异由本模块的 partial 明细点名。
  SKIPPED / N/A → 未执行（跑了但没判定），reason 必填
只读本地文件，不与网络交互。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

EXECUTED = {"PASS", "FAIL"}

# 缺口 → 补齐动作。键是约束类型（冒号前的部分），不是完整 key：
# 完整 key 带字段名（missing_required:title），提示按类型给就够，字段名已在 key 里。
FIX_HINTS = {
    "missing_required": "补「删除必填字段」用例：断言被拒绝，且错误文案点名字段",
    "over_validation": "补四态（不传/空串/null/合法值）：合法缺省必须成功，被拒即过度校验",
    "null_value": "补「显式传 null」用例：契约允许则成功、禁止则拒绝，两方向都要有期望",
    "enum_out_of_range": "补文档未列枚举值：被接受说明文档漏列或实现过宽",
    "max_len": "补长度边界三值：恰好上限（应成功）、上限+1（应拒绝）、超长（应拒绝）",
    "min_len": "补长度下限三值：恰好下限（应成功）、下限-1（应拒绝）",
    "type_mismatch": "补类型错配用例（数字/数组/对象轮流取），断言不被隐式转换放行",
    "default_value": "补「不传该字段」用例并读回，比对契约声明的默认值",
}


def fix_hint(key):
    """缺口 key → 一句可执行的补齐动作。未知类型给通用提示，不臆造具体断言。"""
    kind = str(key or "").split(":", 1)[0].strip()
    return FIX_HINTS.get(kind, "补齐该约束的正反两条用例（应拒绝 / 应成功各一条），并说明依据")
# 【W53】中间态：api_test_runner 归因命中前置未满足时真实写入 results 的状态
# （EXECUTED_STATUSES 第三员，coverage_gate 认它算「触及」）。旧版词表没有它，
# 官方用法（--results api_test_report_full.json）对账时被误判「状态值非法」# 并拉低严格执行率——方向是 fail-safe，但错误信息让人去查数据而非查口径。
PARTIAL = {"PRECONDITION_UNMET"}
NOT_EXECUTED = {"SKIPPED", "N/A"}
KNOWN = EXECUTED | NOT_EXECUTED | PARTIAL

# 多条记录取舍的优先级：真判定 > 中间态 > 未执行。后到的记录只有严格更高才覆盖，
# 同优先级保留先到的（首条 PASS 不会被重跑环境问题写成的 PRECONDITION_UNMET 抹掉）。
_TIER = {s: 2 for s in EXECUTED}
_TIER.update({s: 1 for s in PARTIAL})
_TIER.update({s: 0 for s in NOT_EXECUTED})


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print("无法读取/解析 {}：{}".format(path, e), file=sys.stderr)
        return None


def plan_cases(plan) -> list:
    cases = plan.get("cases") if isinstance(plan, dict) else None
    return [c for c in (cases or []) if isinstance(c, dict) and c.get("id")]


def results_of(doc) -> list:
    if isinstance(doc, list):
        return [r for r in doc if isinstance(r, dict)]
    if isinstance(doc, dict):
        for key in ("results", "cases", "executions"):
            if isinstance(doc.get(key), list):
                return [r for r in doc[key] if isinstance(r, dict)]
    return []


def _norm(v) -> str:
    return str(v or "").strip()


def _tier(rec) -> int:
    """记录的优先级档位：未知状态按 0（未执行）计——真判定永不覆盖不了。"""
    return _TIER.get(_norm(rec.get("status")).upper(), 0)


def parse_exempt(items) -> list:
    """`key:reason` → [{"name","reason"}]；缺 reason 的也收下（由 unexplained 逻辑点名）。"""
    out = []
    for raw in (items or []):
        text = str(raw)
        if ":" in text or "：" in text:
            name, _, reason = text.replace("：", ":").partition(":")
            out.append({"name": name.strip(), "reason": reason.strip()})
        else:
            out.append({"name": text.strip(), "reason": ""})
    return out


# 逗号只有在「后面紧跟一个 key:」时才是条目分隔符：豁免理由里带逗号
# （"AF:本轮未启用大模型链路，接口也无业务规则"）必须留在同一条里，否则后半段
# 变成未知类别——真豁免被丢掉，报告里还多出一个查无此类的 key。
_EXEMPT_SPLIT_RE = re.compile(r"[,，](?=[^,，:：\s]{1,20}[:：])")


def split_exempt_args(items) -> list:
    """--exempt-categories 的 argv → 条目列表（兼容旧的单串逗号写法）。

    首选重复传参（每次一条）；单串仍可用，但只在逗号后确实是「新条目」时才拆。
    """
    out = []
    for raw in ([items] if isinstance(items, str) else (items or [])):
        parts = [p.strip() for p in _EXEMPT_SPLIT_RE.split(str(raw)) if p.strip()]
        if len(parts) > 1:
            sys.stderr.write(
                "[case_coverage] 提示：%r 按逗号拆成 %d 条豁免（%s）；"
                "理由里含逗号时请改用重复传参 "
                "--exempt-categories ... --exempt-categories ...\n"
                % (raw, len(parts), " / ".join(parts)))
        out.extend(parts)
    return out


def dim_matches(case_dim, case_cat, declared_key) -> bool:
    """维度族匹配的**唯一公开入口**，供 flow 的 ``--only`` 与本模块的类别覆盖、
    豁免判定共用同一口径。

    为什么必须由一处提供：CLI 各处若各自用 ``in`` 做等值比对，就会出现
    「``--only AF`` 选出 0 条」（用例维度是 AF1/AF2/AF3）、「``--exempt-categories AF``
    没豁免掉 AF1」这类**同文件内两套口径**的缺陷。判定规则见 ``_dim_match``。
    """
    return _dim_match(case_dim, case_cat, declared_key)


def _dim_match(case_dim, case_cat, key) -> bool:
    k = _norm(key)
    if not k:
        return False
    d, c = _norm(case_dim), _norm(case_cat)

    # 【审查修复·P2】前缀匹配必须落在词段边界：维度码是紧凑字母串（A/B/AB/AC…），
    # 旧版 d.startswith(key) 会让声明类别 "A" 吃掉 "AB" 维度的执行记录——A 明明
    # 一条没测、对账却显示已覆盖（类别覆盖虚高，漏测被藏住）。边界规则：前缀之后
    # **不是字母**才算同族（数字后缀是维度细分，AF1/AF2 属 AF 族，须被 "AF" 声明
    # 认领——test_case_coverage 锁定；字母续缀则是另一维度码，"AB" 不属 "A" 族）；
    # 中文类别（"参数校验"匹配"参数校验-边界"）走同一判定。
    def _hit(v: str) -> bool:
        if not v:
            return False
        if v == k:
            return True
        return v.startswith(k) and (len(v) == len(k) or not v[len(k)].isalpha())

    return _hit(d) or _hit(c)


def compute(plan, results, expect_categories, exempt_categories) -> dict:
    cases = plan_cases(plan)
    by_id = {c["id"]: c for c in cases}
    # 同一用例可能有多条记录：results 是纯 append 列表（case_runner 每次执行都 append，
    # 无覆盖机制），重跑/回归并进就会出现重复。此时**真判定优先**——只要有一条
    # PASS/FAIL 就说明这条用例真跑过，不能因为首条是 SKIPPED 就判它未执行
    # （同批 detect_flaky 也是按多条记录判不稳，两处口径必须一致）。
    res_by_id, multi = {}, {}
    for r in results:
        rid = _norm(r.get("case_no") or r.get("id"))
        if not rid:
            continue
        multi.setdefault(rid, []).append(r)
        prev = res_by_id.get(rid)
        if prev is None:
            res_by_id[rid] = r
        elif _tier(r) > _tier(prev):
            res_by_id[rid] = r          # 后到的更高优先级记录覆盖先前的
    multiple = sorted(cid for cid, rs in multi.items() if len(rs) > 1)
    flaky = sorted(cid for cid in multiple
                   if len({_norm(r.get("status")).upper() for r in multi[cid]}) > 1)

    executed, not_executed, unexplained = 0, 0, []
    exec_by_dim, skip_by_dim = {}, {}
    executed_ids, skipped_ids = set(), set()
    partial_by_dim, partial_ids = {}, set()

    for cid, case in by_id.items():
        dim = _norm(case.get("dimension")) or "(未标维度)"
        rec = res_by_id.get(cid)
        if rec is None:
            # 计划里有、results 里没有 = 静默丢失（当前 runner 每条都留痕，应为空；
            # 这条断言的价值在于将来一旦有路径绕过留痕，立刻暴露）
            not_executed += 1
            skip_by_dim[dim] = skip_by_dim.get(dim, 0) + 1
            unexplained.append("{} 计划内但 results 无任何执行记录（静默丢失）".format(cid))
            continue
        status = _norm(rec.get("status")).upper()
        if status in EXECUTED:
            executed += 1
            executed_ids.add(cid)
            exec_by_dim[dim] = exec_by_dim.get(dim, 0) + 1
        elif status in PARTIAL:
            # 【W53】中间态：确实跑了请求拿到响应，只是结论既非通过也非失败。
            # 不计严格已执行（严格执行率口径不变），但绝不判「非法」；reason 必填
            # （api_test_runner 写入该状态时必带 reason，这里守住留痕纪律）。
            not_executed += 1
            skipped_ids.add(cid)
            skip_by_dim[dim] = skip_by_dim.get(dim, 0) + 1
            partial_ids.add(cid)
            partial_by_dim[dim] = partial_by_dim.get(dim, 0) + 1
            if not _norm(rec.get("reason")):
                unexplained.append("{} 标记 {} 但未写明原因".format(cid, status))
        elif status in NOT_EXECUTED:
            not_executed += 1
            skipped_ids.add(cid)
            skip_by_dim[dim] = skip_by_dim.get(dim, 0) + 1
            if not _norm(rec.get("reason")):
                unexplained.append("{} 标记 {} 但未写明原因".format(cid, status))
        else:
            not_executed += 1
            skipped_ids.add(cid)
            skip_by_dim[dim] = skip_by_dim.get(dim, 0) + 1
            unexplained.append("{} 状态值非法：{}".format(cid, status or "(空)"))

    # 【审查修复·分母不可达】TODO 占位（enabled=False / needs=manual）按设计**永不
    # 执行**：case_runner 会以 SKIPPED+reason 跳过它们、既不静默丢弃也不假绿。它们
    # 此前恒定计入 rate 分母，于是含嵌套字段的接口上 `--min-rate 0.8 --enforce`
    # 必然失败且无法通过补测修好——正是 SKILL 明令要防的「永不通过的假门禁，
    # 最后被人关掉」。改为与 case_generator.plan_vs_actual 同口径：分母只数可执行
    # 用例，不可执行的条数单列披露，不藏。
    not_executable_ids = sorted(
        cid for cid, c in by_id.items()
        if c.get("enabled") is False or _norm(c.get("needs"))
        or (str(c.get("id") or "").startswith("TODO-")))
    countable = [cid for cid in by_id if cid not in set(not_executable_ids)]
    total = len(countable)
    rate = round(executed / total, 4) if total else 0.0

    # 宽松口径：比 coverage_gate 更宽——任一 result 即计（含 SKIPPED/N/A），只算计划内用例。
    # coverage_gate 只认 EXECUTED_STATUSES（PASS/FAIL/PRECONDITION_UNMET），SKIPPED-only 维度被标「假覆盖」不算触及。
    loose_dims = {_norm(by_id[cid].get("dimension")) or "(未标维度)"
                  for cid in res_by_id if cid in by_id}
    loose_total = sum(1 for cid in res_by_id if cid in by_id)

    # 只靠无真判定记录（SKIPPED / N/A / PRECONDITION_UNMET）撑着的维度：
    # 既有门禁会算它"已触及"，严格口径下它一条真判定都没有
    skipped_only = sorted(dim for dim in skip_by_dim
                          if dim not in exec_by_dim and dim in loose_dims)

    # 类别对账：至少一条真实执行（PASS/FAIL）才算覆盖——AF 用例全 SKIPPED 不算覆盖
    declared = [_norm(x) for x in (expect_categories or []) if _norm(x)]
    exempt = parse_exempt(exempt_categories)
    used_exempt = set()
    covered, missing = [], []
    for key in declared:
        # 【审查修复·口径同源】豁免此前用 `key in exempt_names` 精确等值，而覆盖
        # 判定走 `_dim_match`（族匹配）。同一份声明两处用法不同：写 `AF` 能覆盖
        # AF1/AF2/AF3，却豁免不掉它们——用户以为「AF 整族已豁免」，实际三条全部
        # 进 missing；反向又如 ``--expect-categories AF --exempt-categories AF1``，
        # 等值能命中，换成综合写法就静默失效。豁免是「合法绕过门禁」的唯一通道，
        # 必须与判定同口径。这里把 declared key 当作"用例维度"、豁免名当作"声明
        # 族"，复用同一个族边界规则。
        ex = next((e for e in exempt if _dim_match(key, "", e["name"])), None)
        if ex is not None:
            used_exempt.add(_norm(ex["name"]))
            continue
        hit = any(_dim_match(c.get("dimension"), c.get("category"), key) and cid in executed_ids
                  for cid, c in by_id.items())
        (covered if hit else missing).append(key)

    unknown = sorted(cid for cid in res_by_id if cid not in by_id)
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "planned": {
            "total": total,
            # 计划总数 vs 可执行总数不一致时要点名，否则 rate 的分母与 manifest
            # 的计划条数对不上，读报告的人会以为漏了一截用例。
            "declaredTotal": len(by_id),
            "notExecutable": {
                "total": len(not_executable_ids),
                "caseIds": not_executable_ids,
                "note": "TODO 占位 / enabled=false / needs 非空：按设计不参与执行，"
                        "不计入 rate 分母（与 case_generator.plan_vs_actual 同口径）。",
            },
            "byDimension": _count(cases, "dimension"),
            "byCategory": _count(cases, "category"),
            "coveragePlan": (plan.get("coverage_plan") if isinstance(plan, dict) else None) or [],
        },
        "executed": {
            "total": executed,
            "rate": rate,
            "looseTotal": loose_total,
            "byDimension": dict(sorted(exec_by_dim.items())),
            "caseIds": sorted(executed_ids),
        },
        "notExecuted": {
            "total": not_executed,
            "byDimension": dict(sorted(skip_by_dim.items())),
            "unexplained": unexplained,
            "partial": {
                "total": len(partial_ids),
                "byDimension": dict(sorted(partial_by_dim.items())),
                "caseIds": sorted(partial_ids),
                "note": "PRECONDITION_UNMET 中间态：跑了请求拿到响应但无结论，"
                        "不计严格已执行、不判非法；coverage_gate 认它算「触及」，"
                        "两者口径差异由本明细点名。",
            },
        },
        "multipleRecords": {
            "caseIds": multiple,
            "flakyCaseIds": flaky,
            "note": "同一用例在 results 里出现多条记录（重跑/回归并进）。本模块按"
                    "「真判定优先」取状态（真判定 > 中间态 > 未执行，严格更高才覆盖，"
                    "同档保留先到）；终态不一致的即不稳定，与 detect_flaky 同口径。",
        },
        "silentLost": sorted(cid for cid in by_id if cid not in res_by_id),
        "skippedOnlyDimensions": skipped_only,
        "categories": {
            "declared": declared,
            "covered": covered,
            "missing": missing,
            "exempt": exempt,
            # 【审查修复·豁免静默失效】豁免名拼错、或声明了从未参与对账的豁免时会
            # 静默 ignored——产物里照常记着「已豁免」，审计时看不出它其实没生效
            # （豁免是合法绕过门禁的唯一通道，失效而无声等于给了假的安全感）。
            "exemptUnused": sorted(_norm(e["name"]) for e in exempt
                                   if _norm(e["name"]) not in used_exempt),
            # 缺口只说「缺什么」时，拿到报告的人还得自己反推该补哪种用例；
            # 这里给一句可直接照做的补齐动作，不改判定（missing 仍是唯一判据）。
            "fixHints": [{"key": key, "hint": fix_hint(key)} for key in missing],
        },
        "unknownResults": unknown,
        "note": "严格口径：只有 PASS/FAIL 算已执行（coverage_gate 还多认 PRECONDITION_UNMET）；"
                "looseTotal 是比 coverage_gate 更宽的宽松口径（任一 result 即触及，含 SKIPPED/N/A）。"
                "差异集中在 skippedOnlyDimensions——这些维度在既有门禁里被标为「假覆盖」（不算触及），"
                "但真判定为零，请人工决定是否收紧。本模块不修改 case_runner / coverage_gate，既有门禁结论不受影响。",
    }


def _count(items, key) -> dict:
    out = {}
    for c in items:
        k = _norm(c.get(key)) or "(空)"
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


def main() -> int:
    ap = argparse.ArgumentParser(description="用例执行率对账：计划 vs 实际（wjx-api-test）")
    ap.add_argument("--plan", required=True, type=Path, help="生成的用例文件 cases.json（case_generator --out 产物）")
    ap.add_argument("--results", type=Path, help="report_full.json（含 results 数组的执行产物）")
    ap.add_argument("--out", type=Path, help="对账结果 JSON 输出")
    ap.add_argument("--expect-categories", default="", help="本轮应覆盖的用例类别，逗号分隔（如 AF,业务场景）")
    ap.add_argument("--exempt-categories", dest="exempt_categories",
                    action="append", default=None, metavar="KEY:REASON",
                    help='显式豁免的类别，格式 "key:理由"；可重复传。'
                         '旧的逗号分隔写法仍兼容（只在逗号后紧跟 "key:" 时才拆条）')
    ap.add_argument("--min-rate", type=float, default=None, help="严格执行率下限，低于则 --enforce 失败（默认不设限）")
    ap.add_argument("--enforce", action="store_true", help="门禁模式")
    args = ap.parse_args()

    plan = load_json(args.plan)
    if plan is None:
        return 2
    results = []
    if args.results:
        doc = load_json(args.results)
        if doc is None:
            return 2
        results = results_of(doc)

    expect = [x.strip() for x in str(args.expect_categories or "").split(",") if x.strip()]
    # 豁免条目是 key:理由，理由里带逗号是常态——不能无脑按逗号拆。
    exempt = split_exempt_args(args.exempt_categories)
    cov = compute(plan, results, expect, exempt)
    cov["output"] = str(args.out) if args.out else None

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(cov, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "planned": cov["planned"]["total"],
        "executed": cov["executed"]["total"],
        "rate": cov["executed"]["rate"],
        "looseTotal": cov["executed"]["looseTotal"],
        "notExecuted": cov["notExecuted"]["total"],
        "silentLost": cov["silentLost"],
        "skippedOnlyDimensions": cov["skippedOnlyDimensions"],
        "categories": cov["categories"],
        "unexplained": cov["notExecuted"]["unexplained"][:5],
        "output": cov["output"],
    }, ensure_ascii=False, indent=2))

    if not args.enforce:
        return 0
    problems = []
    if cov["notExecuted"]["unexplained"]:
        problems.append("存在未写明原因/未留痕的未执行项 {} 条（如 {}）".format(
            len(cov["notExecuted"]["unexplained"]), "、".join(cov["notExecuted"]["unexplained"][:5])))
    if args.min_rate is not None and cov["executed"]["rate"] < args.min_rate:
        problems.append("严格执行率 {:.0%} 低于下限 {:.0%}（宽松口径 {}/{}）；"
                        "只靠 SKIPPED 撑着的维度：{}".format(
                            cov["executed"]["rate"], args.min_rate,
                            cov["executed"]["looseTotal"], cov["planned"]["total"],
                            "、".join(cov["skippedOnlyDimensions"]) or "无"))
    if cov["categories"]["missing"]:
        problems.append("声明应覆盖但未覆盖的类别：{}（如确属本轮不适用，用 --exempt-categories "
                        '"key:理由" 显式豁免）'.format("、".join(cov["categories"]["missing"])))
    if problems:
        print("错误：" + "；".join(problems), file=sys.stderr)
        return 1
    if cov["unknownResults"]:
        print("警告：results 里出现计划外的用例 ID {} 个（可能来自旧用例文件），不阻塞门禁".format(
            len(cov["unknownResults"])), file=sys.stderr)
    if cov["skippedOnlyDimensions"]:
        print("提醒：维度 {} 在既有门禁里被标为「假覆盖」（不算触及）但真判定为零，请人工确认是否需要收紧"
              .format("、".join(cov["skippedOnlyDimensions"])), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())