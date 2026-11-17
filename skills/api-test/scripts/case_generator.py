# -*- coding: utf-8 -*-
"""用例生成器：契约 → 声明式用例（不再手写 Python main）。

流水线：解析契约 → 字段语义推断 → 适用性自动判定（出 COVERAGE_PLAN 与 N/A 证明）
      → 缺陷模式匹配生成 → 字段级变异生成（必填缺失/类型错/枚举越界/长度边界/注入）
      → 输出 cases JSON（schema 见 references/case_schema.md）

产物直接喂给 case_runner.py 执行，并自动把 COVERAGE_PLAN 装进覆盖率门禁，
从而让「做少了」被代码拦住，而不是靠人记。

用法：
  python case_generator.py --contract <契约路径> [--out cases.json] [--action 1000107]
  python case_generator.py --contract <路径> --print     # 只打印摘要不落盘
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from contract_parser import load_contract          # noqa: E402
from field_semantics import annotate, suggest_for_contract, field_location, location_of  # noqa: E402
from applicability import judge, ALL_DIMS            # noqa: E402
from companion_scan import resolve as resolve_companion  # noqa: E402
import dim_generators                                    # noqa: E402
import llm_fact_cases                                    # noqa: E402
import pattern_engine                               # noqa: E402
import common_params                                     # noqa: E402
import check_key                                    # noqa: E402
import case_signature                               # noqa: E402
import discovery_profile                            # noqa: E402
import asset_runtime                                # noqa: E402

# 超长用例上限的**默认档**数值。真正的生效值走 discovery_profile（发现档可放宽），
# 这里保留常量是为了让 check_key._MAX_LEN_CAP 与之一致（单测守这条不变量）。
BIG_CAP = discovery_profile.DEFAULTS["big_cap"]
TYPE_BAD = {
    "int": "abc", "int32": "abc", "int64": "abc",
    "integer": "abc", "number": "abc", "float": "abc", "double": "abc",
    "bool": "not_a_bool", "boolean": "not_a_bool",
    "string": 12345,
    "array": "not_a_list", "list": "not_a_list", "object": "not_an_object",
}

# string 字段的类型错误形态池。旧版所有 string 字段一律填 12345，导致
# 「creater=12345 / title=12345 / html_content=12345」这批用例打的是同一个参数校验
# 分支，产生一批重复的 PASS——既冗余，也测不到后端对其他非法类型的处理差异
# （有的后端把数字隐式转成字符串，有的直接报类型错，两者是可区分的实现缺陷）。
# 按字段序轮流取用，既不增加用例数，又让每条打在不同分支上。
_STRING_TYPE_ERRORS = [12345, ["type_error"], {"type_error": True}]
_STRING_TOKENS = ("string", "str", "text", "varchar", "char")
# 数值语义的 string 字段固定用数字：数字→数值字段的隐式转换是最容易出问题的分支
_NUMERIC_SEMS = {"id", "enum", "count", "page", "size", "amount"}


def _long_value(field, n):
    """造长度为 n 的**业务形态**内容，替代旧版的 `'x' * n`。

    `'x'*n` 只能探到「长度校验」这一个分支；业务形态的内容（标签结构、URL、标题短语）
    顺带覆盖字符集、转义、截断与存储——同样是一条用例，探测面更宽，且被接受时
    能回读验证内容是否完整（纯重复字符无法区分「截断」与「正常存储」）。

    刻意用 **ASCII 业务形态**而不是中文：中文的「按字符 vs 按字节」长度口径不一致，
    100 个中文字符是 300 字节，会让「恰好 max_len 应成功」这条边界用例在按字节校验的
    后端上必然失败——那是长度口径差异，不是缺陷，混进来会污染边界结论。
    """
    sem = set(field.get("semantics") or [])
    if sem & {"html", "content"}:
        unit = "<p>overlong</p>"
    elif sem & {"url"}:
        unit = "https://example.com/seg"
    elif sem & {"name"}:
        unit = "Overlong title "
    else:
        unit = "overlong_value_"
    if n <= 0:
        return ""
    return (unit * (n // len(unit) + 1))[:n]


# JSON 值对各类型 token 的"是否合法"。复合类型要选一个**对所有成员都非法**的值，
# 否则注入的值恰好命中某个分支被判合法 → 得到"期望拒绝却成功"的假缺陷。
_PRIMITIVE_VALID = {
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "int32": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "int64": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "double": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "string": lambda v: isinstance(v, str),
    "str": lambda v: isinstance(v, str),
    "text": lambda v: isinstance(v, str),
    "varchar": lambda v: isinstance(v, str),
    "char": lambda v: isinstance(v, str),
    "array": lambda v: isinstance(v, list),
    "list": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}

# 候选非法值，按"结构性越界程度"排序：容器类优先（对 null/bool/数值/字符串全部非法）。
_BAD_CANDIDATES = ([], {}, "__type_error__", 12345, True)


def _is_nested_path(name):
    """字段名是否为嵌套点路径（data.items[].name / data.user.name）。

    嵌套字段无法在顶层 base_params 上安全变异：pop 顶层同名键删不掉嵌套字段、
    直接赋值会改变类型结构——两类自动变异都会产出"参数≈base 原样"的无效用例，
    期望被拒却大概率成功 = 假缺陷。判为嵌套的一律降级 TODO 留痕。
    """
    return "." in str(name or "") or "[]" in str(name or "")


def type_tokens(ftype):
    """把类型串切成类型 token（与 check_key._declares_typed 同一口径）。

    ``null / boolean`` → ``['null', 'boolean']``；``integer / string(int32)`` →
    ``['integer', 'string', 'int32']``；``问卷id`` → ``['id']``（中文被切掉）。
    """
    return [t for t in re.split(r"[^a-z0-9]+", str(ftype or "").lower()) if t]


def _compound_bad_value(tokens):
    """复合类型：挑一个对**所有**成员都非法的值。

    只要存在某个成员类型能接受该值，就不能用它（会得到"期望拒绝却成功"的假缺陷，
    把合法的联合类型取值误报成缺陷）。所有候选都至少被一个成员接受时返回 None，
    宁可跳过也不造假缺陷。
    """
    for tok in tokens:
        if tok not in _PRIMITIVE_VALID:
            return None  # 有无法判定的成员 → 不冒险
    for cand in _BAD_CANDIDATES:
        if all(not _PRIMITIVE_VALID[t](cand) for t in tokens):
            return cand
    return None


def type_bad_for(field, ftype, idx=0):
    """按字段类型（含复合）与语义序位挑类型错误值。

    支持复合类型：契约写 ``null / boolean``、``integer / string(int32)`` 时，
    按成员 token 选一个对所有分支都非法的值；对单一 string 字段仍按序轮换形态
    （避免所有 string 字段统一 12345 打在同一校验分支）。
    """
    toks = [t for t in type_tokens(ftype) if t in TYPE_BAD]
    if not toks:
        return None
    if len(toks) == 1 and toks[0] in _STRING_TOKENS:
        if set(field.get("semantics") or []) & _NUMERIC_SEMS:
            return 12345
        return _STRING_TYPE_ERRORS[idx % len(_STRING_TYPE_ERRORS)]
    if len(toks) == 1:
        return TYPE_BAD[toks[0]]
    return _compound_bad_value(toks)


def _mk(cid, name, dim, category, params, expect, **kw):
    case = {
        "id": cid, "name": name, "dimension": dim, "category": category,
        "action": kw.pop("action", None), "applicability": kw.pop("applicability", "适用"),
        "params": params, "expect": expect,
        "priority": kw.pop("priority", "P1"), "source": kw.pop("source", ""),
        "enabled": kw.pop("enabled", True), "needs": kw.pop("needs", ""),
        "note": kw.pop("note", ""),
        "risk": kw.pop("risk", "medium"),
        "evidence": kw.pop("evidence", []),
        "cleanup": kw.pop("cleanup", {"required": False, "actions": []}),
    }
    case.update(kw)
    return case


# 机械探测用例的 ID 前缀：由「字段 × 固定算子」笛卡尔展开或载荷清单遍历产生，
# 不带业务语义。分层只影响报告统计口径，不影响执行与门禁。
TIER_PROBE_PREFIXES = ("B-MISS-", "B-NULL-", "B-OVER-", "B-TYPE-", "B-LEN-", "B-BIG-", "B-COMBO-", "G-")


def _tier_of(case):
    """用例分层：probe / business / manual。

    probe    机械探测——「字段 × 算子」展开与载荷遍历。有覆盖价值（确实探到了某类校验
             是否存在），但不代表业务场景被测过。
    business 业务与语义驱动——往返验证、交叉验证、状态机、越权矩阵、业务场景等。
    manual   待人工补充的占位（TODO-* / needs=manual）。

    为什么必须分层：机械展开会把「用例数」和「维度覆盖率」撑高，让人误判业务覆盖充分。
    分层的目的不是删掉 probe 用例（它们照常执行、照常判 PASS/FAIL），而是让报告能
    单独给出「业务覆盖了多少」这个真实数字。
    """
    cid = str(case.get("id") or "")
    if cid.startswith("TODO-") or case.get("needs") == "manual":
        return "manual"
    if cid.startswith(TIER_PROBE_PREFIXES):
        return "probe"
    return "business"


def tier_stats(cases):
    """按 tier 统计用例数，供报告给出「业务覆盖 vs 机械探测」的真实构成。"""
    out = {"business": 0, "probe": 0, "manual": 0}
    for c in cases:
        t = c.get("tier") or _tier_of(c)
        out[t] = out.get(t, 0) + 1
    return out


def _enrich_cases(cases, contract):
    """为旧生成分支补齐智能元数据，保持原有字段和断言方向不变。"""
    source = contract.get("source") or "contract"
    for case in cases:
        case.setdefault("tier", _tier_of(case))
        case.setdefault("risk", "high" if case.get("priority") == "P0" else "medium")
        case.setdefault("evidence", [source])
        case.setdefault("cleanup", {"required": bool(case.get("expect", {}).get("success")), "actions": []})
    return cases


# 由「字段 × 机械约束」直接推导期望方向的探针用例：机械值抽错时，它们的期望
# 整体反过来——`max_len` 抽大了会让「恰好 N 字符应成功」变成假失败，`required`
# 抽错了会让「缺失应被拒绝」把合法请求判成校验缺失。这两类进缺陷台账就是假缺陷。
_CONSTRAINT_PROBE_PREFIXES = ("B-LEN-EDGE-", "B-LEN-OVER-", "B-LEN-UNDER-",
                              "B-LEN-MIN-", "B-MISS-")


def _suspect_fields(contract):
    """本轮「机械值待核」的字段名集合。

    权威来源是 `suspect_fields`（verify 回填：基准可疑 + 原文歧义两类）；
    旧版报告只有 `suspect` 行列表时从行首字段名兜底，避免升级前后行为断裂。
    """
    report = contract.get("llm_verify_report")
    if not isinstance(report, dict):
        return set()
    names = {str(x) for x in (report.get("suspect_fields") or []) if x}
    names |= {str(r[0]) for r in (report.get("suspect") or []) if r and r[0]}
    return names


def _probe_field_of(case):
    """探针用例作用的字段名：优先 `mutation.field`，否则从 ID 前缀后缀取。

    ID 后缀是字段名的唯一可靠来源之一（`B-LEN-EDGE-<field>`），但字段名本身
    可能带下划线、不含连字符，所以按前缀整体剥除，不做 split。
    """
    f = (case.get("mutation") or {}).get("field")
    if f:
        return str(f)
    cid = str(case.get("id") or "")
    for pfx in _CONSTRAINT_PROBE_PREFIXES:
        if cid.startswith(pfx):
            return cid[len(pfx):]
    return ""


def _resp_contract_keys(contract):
    return [f["name"] for f in (contract.get("response_fields") or []) if f.get("required")]


def gen_field_mutations(contract, base, action, skipped=None):
    """字段级变异：必填缺失 / 类型错 / 长度边界 / 内容注入。

    ``skipped`` 收集「变异不成立」的字段：字段不在 base params 里时，删除它是
    空操作——发出去的请求与正常请求完全一致，却断言「应被拒绝」，必然是一条假缺陷。
    典型是 ``action``：它走请求信封而不是 body，从 base 里 pop 掉不产生任何差异。
    """
    out = []
    str_seq = 0
    for f in (contract.get("request_fields") or []):
        name, ftype = f["name"], (f.get("type") or "").lower()
        sem = f.get("semantics") or []
        # path 参数的值会被 api_test_runner._fill_path 替换进 URL 段落：改它的取值
        # 等于请求**另一个资源**，「恰好等于长度/数值上限应成功」这类正向边界用例
        # 在 path 上必然失败（资源不存在或越权），是契约给 path 参数声明了长度/
        # 数值约束时每轮稳定复现的假缺陷。必填缺失与空值四态已在下方按 path 单独
        # 处理（Mutation 不成立即留痕跳过），这里统一拦住**期望成功**的边界态。
        path_no_positive_boundary = (field_location(f) == "path")

        # 1) 必填缺失 → 应被拒绝
        if f.get("required"):
            # 【W22】嵌套点路径字段（data.items[].name）无法从顶层 base_params 里
            # pop/赋值——旧行为：base 原样发出 → 期望 success=False 却大概率成功
            # = 假缺陷。改为降级 TODO 留痕，不产无效用例。
            if field_location(f) == "path":
                # path 参数在 URL 段落里，删掉它等于换一条路由——请求要么不变、要么
                # 直接 404，两种结果都测不到「必填缺失应被拒绝」。不产用例；分母侧
                # （check_key）同步不声明这条约束，两边口径必须一致。
                if skipped is not None:
                    skipped.append({
                        "field": name, "mutation": "B-MISS",
                        "reason": "path 参数无法通过报文变异删除（URL 段落删不掉）",
                    })
            elif _is_nested_path(name):
                out.append(_mk("TODO-B-MISS-%s" % name,
                               "必填缺失（嵌套字段，需人工构造）：删除 %s 后请求应被拒绝" % name,
                               "B", "参数校验-必填", dict(base),
                               {"success": False, "err_keywords": [name]},
                               action=action, priority="P0", source="generator:required_missing:nested",
                               evidence=["contract:required"],
                               # 【审查修复】占位必须**同时**关掉可执行性。只改 id 前缀是不够的：
                               # case_runner 只按 needs=="manual"/enabled=False 跳过，此条原先两者皆空
                               # → 带着完整合法的 base 去断言「应被拒绝」→ 必然成功 → 每轮一条假 FAIL。
                               # 缺口被 derive_key 剥 TODO- 掩盖后，假缺陷就成了唯一的残留信号，
                               # 比修复前更难排查。与 pattern_engine.p07_gen 的 TODO-B-NULL 同口径。
                               enabled=False, needs="manual",
                               note="嵌套点路径字段无法在顶层参数上安全变异（自动删除会破坏结构），"
                                    "降级人工构造；直接删顶层同名键不等价——那测的是另一个字段。"
                                    "本条不参与执行（enabled=False/needs=manual），expect 仅供人工构造时参考"))
            elif base and name not in base:
                # 删除不成立：字段不在实际请求参数里（如走信封的 action），
                # base 原样发出 → 期望被拒绝却必然成功 = 每轮一条假缺陷。
                # base 为空时保持旧行为：没有可比对的基准，判断不了「这个字段能不能删」，
                # 此时跳过会把整批必填缺失变异清空，制造一片假缺口。
                if skipped is not None:
                    skipped.append({
                        "field": name, "mutation": "B-MISS",
                        "reason": "字段不在 base params 中（多为请求信封参数），删除它不改变请求",
                    })
            else:
                p = dict(base)
                p.pop(name, None)
                out.append(_mk("B-MISS-%s" % name, "必填缺失：%s（应被拒绝）" % name, "B",
                               "参数校验-必填", p,
                               {"success": False, "err_keywords": [name]},
                               action=action, priority="P0", source="generator:required_missing",
                               evidence=["contract:required"], note="必填项缺失必须被拒绝；错误文案应指明字段"))

        # 可选/可空字段的反向校验四态：合法缺省值不应被过度拒绝。
        if not f.get("required") or f.get("nullable"):
            # 【W22 同源】嵌套点路径字段无法在顶层安全变异 → 不产四态，降级 TODO。
            if field_location(f) == "path":
                # 四态里「缺失 / null / 空串」在 path 段上都是同一个 URL（删不掉、空值
                # 会被 quote 成空片段），四种构造实际只有一种请求 → 判据不成立。
                if skipped is not None:
                    skipped.append({
                        "field": name, "mutation": "B-OVER",
                        "reason": "path 参数无法构造缺省/空值等态（URL 段落形态单一）",
                    })
            elif _is_nested_path(name):
                out.append(_mk("TODO-B-OVER-%s" % name,
                               "可选/可空四态（嵌套字段，需人工构造）：验证 %s 的缺省形态不被过度拒绝" % name,
                               "B", "参数校验-过度校验", dict(base),
                               {"success": True}, action=action, priority="P1",
                               source="generator:over_validation:nested",
                               assertion_mode="over_validation",
                               # 【审查修复】同 TODO-B-MISS：占位不参与执行，否则四态断言
                               # 拿完整 base 跑，等于把「原样发一次」当成四态之一。
                               enabled=False, needs="manual",
                               note="嵌套字段缺省四态需人工在结构内构造，自动变异会破坏嵌套结构；"
                                    "本条不参与执行（enabled=False/needs=manual）"))
            else:
                # 【W25 反系统性假 FAIL】守卫：带以下约束的可选字段，缺省形态被拒是
                # **正确行为**，不是过度校验——
                #   - 有 enum：空串/null 大概率不在枚举内，被枚举校验拒绝天经地义；
                #   - min_len>=1：空串低于最小长度，被长度校验拒绝天经地义；
                #   - forbid 非空：强格式语义字段（URL/邮箱等），空值可能被格式校验拒绝。
                # 旧版无条件 expect success=True → 这批字段每轮刷 B-OVER 假缺陷。
                # missing 态保留（不传字段不应被必填校验拦截——那才是过度校验本体）；
                # null/empty_string 态改判「允许拒绝」（tolerate_reject 落到具体守卫理由）。
                guarded = bool(f.get("enum")) or (
                    f.get("min_len") and int(f["min_len"]) >= 1) or bool(f.get("forbid"))
                guard_words = []
                if f.get("enum"):
                    guard_words.append("枚举")
                if f.get("min_len") and int(f["min_len"]) >= 1:
                    guard_words.append("最小长度")
                if f.get("forbid"):
                    guard_words.append("格式/禁用")
                # header 位置只有「缺失」一种真实形态：执行层把空串/null 都解释成
                # **不带这个头**（见 build_request_headers），三态发出去是同一份请求、
                # 却会各自判一次——一个根因能刷出三条缺陷，报告里看着像三个问题。
                _states = (("missing", None),) if field_location(f) == "header" else (
                    ("missing", None), ("null", None), ("empty_string", ""))
                for state, value in _states:
                    p = dict(base)
                    if state == "missing":
                        p.pop(name, None)
                    else:
                        p[name] = value
                    if guarded and state in ("null", "empty_string"):
                        out.append(_mk(
                            "B-OVER-%s-%s" % (name, state),
                            "可选字段%s态：%s（有%s约束，允许被拒）" % (state, name, "/".join(guard_words)),
                            "B", "参数校验-过度校验", p,
                            {"success": True,
                             "tolerate_reject": ["长度", "不能为空", "不能超过", "格式", "不合法",
                                                 "枚举", "无效", "不正确", "必须", "只能", "类型"]},
                            action=action, priority="P1", source="generator:over_validation",
                            assertion_mode="over_validation", mutation_state=state,
                            evidence=["contract:optional_or_nullable", "contract:guard"],
                            cleanup={"required": True, "actions": ["delete_fixture"]},
                            note="字段带 %s 约束——缺省形态被守卫校验拒绝属正确行为，不再判过度校验；"
                                 "若拒绝文案与守卫无关（如内部错误）仍会进台账" % "/".join(guard_words)))
                    else:
                        out.append(_mk("B-OVER-%s-%s" % (name, state), "可选字段%s态：%s（不应过度校验）" % (state, name), "B",
                                       "参数校验-过度校验", p, {"success": True}, action=action,
                                       priority="P1", source="generator:over_validation", assertion_mode="over_validation",
                                       mutation_state=state,                                evidence=["contract:optional_or_nullable"],
                                       cleanup={"required": True, "actions": ["delete_fixture"]},
                                       note="合法可选/可空形态被拒绝时记录过度校验缺陷"))

        # 【审查修复·P0 嵌套变异守卫】类型/长度/枚举类**顶层**变异对嵌套点路径字段
        # （data.items[].name）不成立：p[name]=bad 会把整条点路径当顶层键，变异根本
        # 没作用到嵌套结构——请求 = 合法 base + 顶层多余键 → 大概率成功 →
        # "应被拒绝"用例批量假 FAIL 进台账；且其 check_key 被无效用例"兑现"，
        # 约束对账门禁永远查不出这类缺口（对账失真）。与 W22（B-MISS/B-OVER）
        # 同口径：降级 TODO 留痕，不产无效用例；下方 G-FORBID 分支自带同款守卫。
        if _is_nested_path(name):
            # 【审查修复·P0-2 对账对齐】占位按字段声明的约束**分型**产出（TODO-B-TYPE /
            # TODO-B-LEN-* / TODO-B-ENUM / TODO-B-NUM-*），derive_key 剥 TODO- 前缀后按
            # 既有 _MECH_PREFIXES 解析兑现对应约束 key——此前统一叫 TODO-B-MUT，前缀表
            # 无对应条目，wrong_type / 长度边界 / invalid_enum 的分母 key 永无交付可对，
            # reconcile 每轮刷嵌套字段假缺口（test_review_fixes_lock 锁定此闭环）。
            # 占位条件与 check_key.required_checks 分母声明严格同源：长度上限看发现档
            # big_cap、below_minimum 仅 min_len>=2、at_minimum 需无枚举、数值仅在无
            # 长度约束时——宁多产占位不留缺口。
            _cap = discovery_profile.get("big_cap")
            _todos = []
            if type_bad_for(f, ftype, str_seq) is not None:
                _todos.append(("B-TYPE", "类型错误"))
            if f.get("max_len") and f["max_len"] <= _cap:
                _todos.append(("B-LEN-OVER", "长度超限"))
                _todos.append(("B-LEN-EDGE", "长度恰好上限"))
            if f.get("min_len") and int(f["min_len"]) >= 2:
                _todos.append(("B-LEN-UNDER", "长度低于下限"))
            if f.get("min_len") and int(f["min_len"]) >= 1 and not f.get("enum"):
                _todos.append(("B-LEN-MIN", "长度等于下限"))
            if f.get("enum"):
                _todos.append(("B-ENUM", "枚举越界"))
            if not f.get("max_len") and not f.get("min_len"):
                _mx, _mn = f.get("max_value"), f.get("min_value")
                if isinstance(_mx, (int, float)) and not isinstance(_mx, bool):
                    _todos.append(("B-NUM-OVER", "数值超限"))
                    _todos.append(("B-NUM-EDGE", "数值恰好上限"))
                if isinstance(_mn, (int, float)) and not isinstance(_mn, bool):
                    _todos.append(("B-NUM-UNDER", "数值低于下限"))
                    _todos.append(("B-NUM-MIN", "数值等于下限"))
            for _pfx, _label in _todos:
                out.append(_mk("TODO-%s-%s" % (_pfx, name),
                               "%s（嵌套字段，需人工构造）：%s 的%s变异需在嵌套结构内进行" % (_label, name, _label),
                               "B", "参数校验-嵌套", dict(base), {"success": True}, action=action,
                               priority="P1", source="generator:field_mutation:nested",
                               enabled=False, needs="manual",
                               note="嵌套点路径字段的约束变异无法在顶层参数上安全变异，降级人工构造；"
                                    "本条不参与执行（enabled=False/needs=manual），expect 仅供人工构造时参考"))
            continue

        # 2) 类型错误 → 应被拒绝。字符串字段轮流取不同非法形态，避免全部打同一分支
        bad = type_bad_for(f, ftype, str_seq)
        if bad is not None:
            if ftype in _STRING_TOKENS:
                str_seq += 1
            p = dict(base)
            p[name] = bad
            out.append(_mk("B-TYPE-%s" % name, "类型错误：%s=%r（应被拒绝）" % (name, bad), "B",
                           "参数校验-类型", p,
                           {"success": False, "err_keywords": [name]},
                           action=action, priority="P0", source="generator:type_error",
                           evidence=["contract:type"], cleanup={"required": True, "actions": ["delete_fixture"]}))

        # 3) 长度边界：超长应被拒绝；恰好等于上限应成功。
        #    生效上限走发现档：discovery 放宽到数万，让大字段也能做真边界用例。
        cap = discovery_profile.get("big_cap")
        if f.get("max_len") and f["max_len"] <= cap:
            p = dict(base)
            p[name] = _long_value(f, f["max_len"] + 1)
            out.append(_mk("B-LEN-OVER-%s" % name, "长度超限：%s %d+1 字符（应被拒绝）" % (name, f["max_len"]),
                           "B", "参数校验-边界", p,
                           {"success": False, "err_keywords": [name]},
                           action=action, priority="P0", source="generator:length_over",
                           note="内容用业务形态填充（非重复 x）：顺带覆盖转义与截断，"
                                "且刻意用 ASCII 以免字符/字节口径差异污染边界判定"))
            if not path_no_positive_boundary:
                p2 = dict(base)
                p2[name] = _long_value(f, f["max_len"])
                out.append(_mk("B-LEN-EDGE-%s" % name, "长度边界内：%s 恰好 %d 字符（应成功）" % (name, f["max_len"]),
                               "B", "参数校验-边界", p2, {"success": True},
                               action=action, priority="P1", source="generator:length_edge",
                               note="边界内值被拒即为边界判断错误（off-by-one）。"
                                    "若后端按字节校验而本用例按字符构造，会在此暴露长度口径差异——"
                                    "记「待确认」而非直接判缺陷"))
            elif skipped is not None:
                skipped.append({"field": name, "mutation": "B-LEN-EDGE",
                                "reason": "path 参数的取值会替换进 URL 段落，"
                                          "改成长度边界值等于请求另一个资源，"
                                          "「应成功」的边界态不成立"})
        elif f.get("max_len") and discovery_profile.get("overlong_probe"):
            # 大字段（max_len 超出上限，如 html_content=200000）：够不到 max_len+1，
            # 退而构造**一条有界的大内容健壮性探针**——探"大体积合法内容后端吞不吞得下"
            # （5xx / 静默截断 / 超时）。这不是边界用例，故预期为成功，且只探一次。
            p = dict(base)
            p[name] = _long_value(f, cap)
            out.append(_mk("B-BIG-%s" % name,
                           "大内容健壮性：%s 传 %d 字符合法内容（应成功，不 5xx/不静默截断）" % (name, cap),
                           "B", "参数校验-大内容", p, {"success": True},
                           action=action, priority="P1", source="generator:big_content",
                           note="发现档专属：max_len 超出常规上限的大字段（如富文本）无法构造"
                                "边界+1 用例，此条只探大体积合法内容是否被正常接受。"
                                "5xx 或返回成功但回读内容被截断即缺陷；超时先归 ENV/网络"))

        # 3b) 长度下边界（min_len）：低于下限应被拒绝、恰好等于下限应成功。
        #     与 max_len 同样刻意用 ASCII 业务形态，避免中文按字符/按字节的口径差异污染边界判定。
        #     仅在下限 >= 2 时构造"低于下限"用例：ml-1 >= 1 才是明确的"非空但过短"；
        #     ml == 1 时低于值即空串，与可选空值/必填缺失语义重叠，交给 over_validation/missing 覆盖。
        #     "恰好等于下限应成功"仅在字段无枚举时构造——有枚举时业务形态字符串不在枚举内，
        #     会被后端以枚举理由拒绝，那不是边界缺陷，构造了就是假缺陷（分母侧同理，below_minimum
        #     只对 min_len>=2 声明，与这里的 B-LEN-UNDER 严格对齐）。
        if f.get("min_len"):
            ml = int(f["min_len"])
            if ml >= 2:
                p = dict(base)
                p[name] = _long_value(f, ml - 1)
                out.append(_mk("B-LEN-UNDER-%s" % name, "长度低于下限：%s %d-1 字符（应被拒绝）" % (name, ml),
                               "B", "参数校验-边界", p,
                               {"success": False, "err_keywords": [name]},
                               action=action, priority="P0", source="generator:length_under",
                               note="低于声明的最小长度应被拒绝；ASCII 业务形态填充，避免字符/字节口径差异"))
            # min_len=1 时「恰好下限」= 1 个字符，是有效的正长度边界，仍需构造；
            # 真正需要跳过的是「低于下限」（ml-1=0 即空串，与空值/缺失语义重叠，见上方 ml>=2 守卫）。
            if not f.get("enum"):
                if path_no_positive_boundary:
                    if skipped is not None:
                        skipped.append({"field": name, "mutation": "B-LEN-MIN",
                                        "reason": "path 参数的取值会替换进 URL 段落，"
                                                  "「恰好等于下限应成功」在 path 上不成立"})
                else:
                    p2 = dict(base)
                    p2[name] = _long_value(f, ml)
                    out.append(_mk("B-LEN-MIN-%s" % name, "长度等于下限：%s 恰好 %d 字符（应成功）" % (name, ml),
                                   "B", "参数校验-边界", p2, {"success": True},
                                   action=action, priority="P1", source="generator:length_min",
                                   note="恰好等于最小长度应被接受（off-by-one）；后端按字节校验会在此暴露口径差异，记待确认"))

        # 3c) 【审查修复·P1-3 数值边界】契约的 minLength/minimum/maximum 约束
        #     （contract_parser 提取为 min_value/max_value）此前没有任何生成器消费
        #     ——数值 off-by-one 整类漏测（上限+1 被放行、恰好上限被拒都测不出）。
        #     仅对未声明长度约束的字段构造（与 B-LEN 互斥：字符串边界走 3/3b，
        #     数值边界走这里；同字段并存两类约束属解析噪音，优先既有长度口径，
        #     check_key 分母用同款守卫，两侧承诺严格对齐）。嵌套点路径字段已被
        #     上方 _is_nested_path 守卫拦下，走不进本分支。
        if not f.get("max_len") and not f.get("min_len"):
            _mx, _mn = f.get("max_value"), f.get("min_value")
            if isinstance(_mx, (int, float)) and not isinstance(_mx, bool):
                p = dict(base)
                p[name] = _mx + 1
                out.append(_mk("B-NUM-OVER-%s" % name,
                               "数值超限：%s=%r+1（应被拒绝）" % (name, _mx), "B", "参数校验-边界", p,
                               {"success": False, "err_keywords": [name]},
                               action=action, priority="P0", source="generator:number_over",
                               note="超出契约声明的数值上限应被拒绝"))
                if path_no_positive_boundary:
                    if skipped is not None:
                        skipped.append({"field": name, "mutation": "B-NUM-EDGE",
                                        "reason": "path 参数的取值会替换进 URL 段落，"
                                                  "「恰好等于数值上限应成功」在 path 上不成立"})
                else:
                    p2 = dict(base)
                    p2[name] = _mx
                    out.append(_mk("B-NUM-EDGE-%s" % name,
                                   "数值边界内：%s 恰好 %r（应成功）" % (name, _mx), "B", "参数校验-边界", p2,
                                   {"success": True},
                                   action=action, priority="P1", source="generator:number_edge",
                                   note="恰好等于数值上限应被接受（off-by-one）；后端按字符串口径"
                                        "校验数值时在此暴露口径差异，记待确认"))
            if isinstance(_mn, (int, float)) and not isinstance(_mn, bool):
                p = dict(base)
                p[name] = _mn - 1
                out.append(_mk("B-NUM-UNDER-%s" % name,
                               "数值低于下限：%s=%r-1（应被拒绝）" % (name, _mn), "B", "参数校验-边界", p,
                               {"success": False, "err_keywords": [name]},
                               action=action, priority="P0", source="generator:number_under",
                               note="低于契约声明的数值下限应被拒绝"))
                if path_no_positive_boundary:
                    if skipped is not None:
                        skipped.append({"field": name, "mutation": "B-NUM-MIN",
                                        "reason": "path 参数的取值会替换进 URL 段落，"
                                                  "「恰好等于数值下限应成功」在 path 上不成立"})
                else:
                    p2 = dict(base)
                    p2[name] = _mn
                    out.append(_mk("B-NUM-MIN-%s" % name,
                                   "数值等于下限：%s 恰好 %r（应成功）" % (name, _mn), "B", "参数校验-边界", p2,
                                   {"success": True},
                                   action=action, priority="P1", source="generator:number_min",
                                   note="恰好等于数值下限应被接受（off-by-one）"))

        # 4) 内容类字段的安全注入用例已升级为清单驱动：见 gen_security_payload_cases()
        #    （assets/security_payloads.json，按语义标签选载荷），此处不再内联生成。

        # 5) 【W24 补消费方】规则解析出的 forbid（禁用子串）此前只有 llm 路径消费
        #    （_FACT_CONSUMERS 写明"仅 evidence 有据的禁用词"），纯规则链路解析出
        #    禁用词后**没有任何生成器使用**——解析了也不测。这里补 G/B 维禁用词用例：
        #    传入被禁子串应被拒绝（安全类，与清单驱动载荷同向、但来自契约白纸黑字）。
        for bad_word in (f.get("forbid") or [])[:5]:
            if not bad_word or _is_nested_path(name):
                continue
            p = dict(base)
            p[name] = "正常前缀%s正常后缀" % bad_word
            out.append(_mk("G-FORBID-%s-%s" % (name, bad_word[:12]),
                           "禁用词：%s 含被禁子串「%s」（契约声明应被拒绝）" % (name, bad_word[:20]),
                           "G", "契约-禁用词", p,
                           {"success": False, "tolerate_reject": ["敏感", "违规", "禁止", "非法", "不允许"]},
                           action=action, priority="P1", source="generator:contract_forbid",
                           evidence=["contract:forbid"],
                           note="契约明确声明禁用子串——被原样接受是校验缺失；被内容安全策略拦下"
                                "按「符合预期」结算（与陷阱3同向）"))
    return out


# 机械组合覆盖上限（v4.25.0）：参与组合的字段代表数 + 组合用例总数，防 n² 爆炸。
COMBO_MAX_FIELDS = 8
COMBO_MAX_CASES = 24


def gen_combination_cases(contract, base, action):
    """机械组合覆盖（v4.25.0）：多个字段同时取各自**单个就非法**的值 → 应被拒绝。

    只做 sound 的组合：每个参与字段取它单独提交就应被拒的值（类型错），组合后
    **必然应被拒绝**——不猜互斥/条件必填（契约极少声明，猜了就是假缺陷）。测的是
    "后端是否逐个校验所有参数"，而非只校验第一个就返回。

    有界：字段代表数 + 组合总数双上限；不要求错误提及特定字段（后端可能只报第一个），
    只断言"应被拒绝"。B-COMBO-* 归 probe 层、走来源型身份证、**不进对账分母**。
    """
    out = []
    fields = [f for f in (contract.get("request_fields") or [])
              if f.get("name") and type_bad_for(f, (f.get("type") or "").lower()) is not None
              # 【审查修复·P0 嵌套变异守卫】组合变异同样只在顶层成立：嵌套点路径
              # 字段 p[name]=bad 不作用于嵌套结构，组合用例 = 合法 base + 顶层
              # 多余键，"应被拒绝"断言必然批量假 FAIL。与 gen_field_mutations 同口径剔除。
              and not _is_nested_path(f["name"])]
    reps = fields[:COMBO_MAX_FIELDS]
    n = 0
    for i in range(len(reps)):
        for j in range(i + 1, len(reps)):
            if n >= COMBO_MAX_CASES:
                return out
            fa, fb = reps[i], reps[j]
            na, nb = fa["name"], fb["name"]
            ba = type_bad_for(fa, (fa.get("type") or "").lower())
            bb = type_bad_for(fb, (fb.get("type") or "").lower())
            if ba is None or bb is None:
                continue
            p = dict(base)
            p[na] = ba
            p[nb] = bb
            out.append(_mk("B-COMBO-%s__%s" % (na, nb),
                           "组合非法：%s 与 %s 同时取非法值（应被拒绝）" % (na, nb),
                           "B", "参数校验-组合", p, {"success": False},
                           action=action, priority="P2", source="generator:combination",
                           note="两字段各自单独即非法，组合后应被拒绝；测后端是否逐个校验所有"
                                "参数（而非只报第一个）。不要求错误提及特定字段，只断言应被拒绝。"))
            n += 1
    return out


SECURITY_PAYLOADS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "security_payloads.json")


def _load_security_payloads():
    """加载安全载荷清单；文件缺失/损坏时返回 None（G 维度退回人工，不静默伪造用例）。"""
    try:
        with open(SECURITY_PAYLOADS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return None


_TYPE_TOKENS = ("string", "str", "text", "varchar", "char")


def _payload_categories_for(field, lib):
    """按各载荷类别的 applies_to 为字段匹配类别（去重保序）。

    清单是唯一事实来源：类别只要写对 applies_to 就自动生效，不必改本文件——
    否则「加了类别却没人接线」等于静默失效（正是本 skill 最防的失败模式）。
    applies_to 条目分两种：类型名（string/str/text/varchar/char）按字段类型判定，
    语义标签（html/url/name…）与字段 semantics 求交。

    只对字符串型字段注入——清单内载荷全是字符串，给 int/bool 注入只会得到
    类型错误，那不是安全缺陷而是参数校验，避免污染 G 维度结论。
    """
    ftype = (field.get("type") or "").lower()
    if ftype not in _TYPE_TOKENS and ftype != "":
        return []
    sem = set(field.get("semantics") or [])
    out = []
    for cat in (lib.get("categories") or []):
        cid = cat.get("id")
        if not cid or cid in out:
            continue
        for token in (cat.get("applies_to") or []):
            if token in _TYPE_TOKENS or token in sem:
                out.append(cid)
                break
    return out


# 注入风险只存在于「内容会被存储 / 回显 / 拼接 / 渲染」的字段上。
CONTENT_SEMS = {"html", "name", "url", "content"}
NON_CONTENT_SEMS = {"id", "enum", "bool", "count", "page", "size", "status",
                    "time", "amount", "currency", "tz", "i18n"}
_CONTENT_HINT = re.compile(
    r"content|html|text|body|desc|remark|title|name|intro|detail|summary|"
    r"正文|内容|描述|备注|标题|说明|简介|详情", re.I)


def _is_content_field(field):
    """该字段是否值得注入安全载荷。

    判据是「字段内容会不会被存储 / 回显 / 拼接 / 渲染」。标识类字段
    （creater / page_type / publish / 各类 id）即使被注入并成功落库也不会产生注入风险，
    注入它们只会得到一批重复的 PASS：实测单个接口可产生 50 条载荷用例，占全部用例 38%，
    把 G 维度和报告撑大，稀释真正的问题。

    保守原则：**判定不了时按内容型处理**（宁可多测，不可漏测安全）；
    被跳过的字段一律记入 payload_skipped 留痕，不静默消失。
    """
    sem = set(field.get("semantics") or [])
    if sem & NON_CONTENT_SEMS:
        return False
    if sem & CONTENT_SEMS:
        return True
    name = (field.get("name") or "")
    doc = "%s %s" % (field.get("desc", ""), field.get("remark", ""))
    return bool(_CONTENT_HINT.search(name + " " + doc))


def gen_security_payload_cases(contract, base, action, skipped=None):
    """G 维度：清单驱动的安全载荷用例（assets/security_payloads.json）。

    替代旧版单条 script 注入：按字段语义标签/类型挑选载荷类别，每类每字段
    最多取 max_payloads_per_field_per_category 条，防止用例爆炸。
    断言方向：应被拦截（success=False + 错误关键词）；被原样接受即缺陷，
    被正确拦截记「符合预期」非缺陷（陷阱3）。

    skipped：可选列表，收集被判定为「非内容型」而未注入载荷的字段，供报告留痕。
    """
    lib = _load_security_payloads()
    if not lib:
        # 【W16 反静默消失】载荷库缺失/损坏时整个 G 维凭空蒸发——注释承诺
        # 「退回人工不静默伪造」，但旧版连 TODO 占位都没有，覆盖率门禁看到的是
        # 「G 维 0 条」。产一条 TODO-G 留痕（needs=manual），让缺口在报告点名。
        sys.stderr.write("[case_generator] 安全载荷库缺失或损坏（%s），G 维度降级人工\n"
                         % SECURITY_PAYLOADS_PATH)
        return [_mk("TODO-G", "安全载荷库不可用，需人工补充安全注入用例（XSS/SQLi/路径穿越等）",
                    "G", "安全注入", dict(base), {"success": False},
                    action=action, priority="P0", source="generator:security:lib_missing",
                    needs="manual",
                    note="assets/security_payloads.json 缺失或损坏——安全维度不得静默消失，"
                         "修复清单文件后重跑即可自动恢复")]
    cats = {c["id"]: c for c in lib.get("categories", [])}
    # 生效条数走发现档：discovery 可放宽（如 2 → 4，含"字面 + 变体绕过"两种样本）；
    # 默认档 None → 沿用清单自带 limits，与接入前一致。
    lib_limit = int((lib.get("limits") or {}).get("max_payloads_per_field_per_category", 2))
    limit = discovery_profile.get("payload_per_field_per_category") or lib_limit
    out = []
    for f in (contract.get("request_fields") or []):
        name = f["name"]
        # 【审查修复·P0 嵌套变异守卫】载荷注入只在顶层成立：嵌套点路径字段的
        # p[name]=payload 不作用于嵌套结构，"应被拦截"断言会批量假 FAIL。
        # 与 payload_skipped 同机制留痕（不静默消失），供产物点名。
        if _is_nested_path(name):
            if skipped is not None:
                skipped.append({
                    "field": name,
                    "semantics": f.get("semantics") or [],
                    "reason": "嵌套点路径字段，无法在顶层注入载荷，需人工在结构内构造",
                })
            continue
        if not _is_content_field(f):
            if skipped is not None:
                skipped.append({
                    "field": name,
                    "semantics": f.get("semantics") or [],
                    "reason": "标识/结构类字段，内容不被存储或渲染，注入无风险含义",
                })
            continue
        for cid in _payload_categories_for(f, lib):
            cat = cats.get(cid)
            if not cat:
                continue
            for i, payload in enumerate((cat.get("payloads") or [])[:limit]):
                if field_location(f) == "header" and ("\r" in payload or "\n" in payload):
                    # 头值里的 CRLF 是头注入：装载期会被 validate_headers 拒绝（整轮
                    # ValueError），而它并不是「接口是否拦截」的测试点。跳过并留痕。
                    if skipped is not None and not any(
                            s.get("field") == name and "CR/LF" in str(s.get("reason"))
                            for s in skipped):
                        skipped.append({
                            "field": name,
                            "semantics": f.get("semantics") or [],
                            "reason": "header 参数值含 CR/LF，属头注入而非接口校验，需专用通道",
                        })
                    break
                p = dict(base)
                p[name] = payload
                short = payload if len(payload) <= 40 else payload[:37] + "..."
                out.append(_mk(
                    "G-%s-%s-%d" % (cid, name, i + 1),
                    "%s：%s=%r（应被拦截）" % (cat["name"], name, short),
                    "G", "安全-%s" % cat["name"], p,
                    {"success": False, "err_keywords": cat.get("err_keywords", [])},
                    action=action, priority="P0", source="generator:security_payload",
                    note=cat.get("note") or "载荷被原样接受即缺陷；被正确拦截记「符合预期」非缺陷（陷阱3）"))
    return out


def gen_baseline(contract, base, action):
    """基线用例：主链路成功 + 契约校验。"""
    keys = _resp_contract_keys(contract)
    return [
        _mk("D01", "主链路：正常创建（校验响应契约字段）", "D", "业务-核心流程", dict(base),
            {"success": True, "contract_keys": keys},
            action=action, priority="P0", source="generator:baseline"),
    ]


def _identity_field(contract):
    """找「创建者 / 操作者」类身份字段——A 维度越权矩阵的身份注入点。

    刻意只认**字段名**，不用 semantics 里的 id 标签：id 覆盖 vid/sid/corpid 等
    资源标识，拿它当身份去注入子账号，是把合法的资源 ID 换成登录名，
    得到的是参数校验错误而不是越权结论。
    """
    tokens = ("creater", "creator", "userid", "user_id", "uid", "openid",
              "account", "username", "member", "staff")
    for f in (contract.get("request_fields") or []):
        n = (f.get("name") or "").lower()
        if any(t in n for t in tokens):
            return f
    return None


_ID_READ_TOKENS = ("id", "vid", "sid", "uid", "no", "code", "key")


def _read_probe_params(read_params):
    """读面越权用例的探测参数：优先用调用方给的 read_params。

    为空时退回一个空 dict——读列表/计数类接口本身就不需要入参，
    带着空参数去问"跨企业身份能不能读到"依然是有效探测。
    """
    return dict(read_params or {})


def gen_auth_cases(contract, base, action, read_action=None, read_params=None, declared=None):
    """A 维度：鉴权与越权矩阵（历史高发，人最容易漏）。

    矩阵按「身份 × 操作面」铺开，而不是只给一条签名错误：
      身份轴：本企业主账号（对照）/ 子账号 / 跨企业 appid / 跨企业+主账号（双重矛盾）
      操作面：写（本接口 base 请求）/ 读（配套读接口 read_action）
    越权是单接口测试里价值最高的一项，且不依赖任何其他接口的业务知识——
    身份与跨企业 appid 都可由配置直接构造，生成期就能铺满。

    declared：契约声明面（参数表字段名 ∪ 请求示例键）。A01/A02/A06 的探测
    **注入以契约为前提**——内部接口契约不声明 sign/appid，往请求里塞错签名
    或跨企业 appid 是无效探测（服务端根本不认识这些键）；未声明时降级为
    TODO 占位点名，与下方「无身份字段」的 TODO-A-越权 同一形态。

    身份与跨企业 appid 一律用占位符承载，执行期由 personal_config 解析：
    包内零个人数据，换环境只换配置，用例本身不变。
    守卫：占位符解析不到时（如未配 cross_appid），执行期点名跳过而不是发一条残缺请求。
    """
    declared = declared if declared is not None else set()
    out = []
    p = dict(base)
    if "sign" in declared:
        p["sign"] = "xwrongsignx"
        out.append(_mk("A01", "错误 sign（签名校验应生效）", "A", "鉴权-签名", p,
                       {"success": False}, action=action, priority="P0",
                       source="generator:auth_sign",
                       note="约定不校验 sign 的环境应仍成功——此时记「待确认」而非缺陷"))
    else:
        out.append(_mk(
            "TODO-A-sign", "A 维度：契约未声明 sign 键，签名校验探测需人工确认后补充",
            "A", "待补充", dict(base), {"success": False},
            action=action, enabled=False, needs="manual", priority="P1",
            source="generator:auth_placeholder",
            note="契约的参数表与请求示例都没有 sign——本接口大概率不校验签名，"
                 "强行注入错误 sign 是无效探测。若服务端确有签名校验（契约漏写），"
                 "请人工补一条错误签名用例"))

    ident = _identity_field(contract)
    ident_name = ident["name"] if ident else None
    rp = _read_probe_params(read_params)
    rp_has_id = any(any(t in k.lower() for t in ("id", "vid", "sid", "uid")) for k in rp)

    def _write(cid, name, appid_ph, creater_ph, expect_ok, priority, note):
        q = dict(base)
        if appid_ph:
            q["appid"] = appid_ph
        if creater_ph and ident_name:
            q[ident_name] = creater_ph
        return _mk(cid, name, "A", "越权矩阵", q,
                   {"success": bool(expect_ok)}, action=action, priority=priority,
                   source="generator:auth_matrix", note=note)

    def _read(cid, name, priority, note):
        """读面越权用例：主请求用**本企业身份**自证可读（对照组），
        跨企业身份的比对交给 check 做——否则读接口因缺参数失败会被误判成"越权被拦住"。
        """
        return _mk(cid, name, "A", "越权矩阵-读面", dict(rp),
                   {"success": True}, action=read_action, priority=priority,
                   check="cross_tenant_read",
                   check_args={"read_action": read_action, "read_params": dict(rp)},
                   source="generator:auth_matrix", note=note)

    # --- 写面：身份轴三档（跨企业 / 子账号 / 主账号对照）+ 双重矛盾 ---
    # build_payload 的 kwargs 覆盖语义保证 case params 里的 appid 不会被覆盖回当前 appid。
    if "appid" in declared:
        out.append(_write(
            "A02", "越权矩阵·跨企业写：用跨企业 appid 提交写请求（应被拒绝）",
            "<跨企业appid>", None, False, "P0",
            "A 维度最高价值项：跨企业身份改写本企业数据即越权缺陷。"
            "需 personal_config.cross_appid（或 WJX_CROSS_APPID）；未配置时执行期点名跳过。"
            "按环境纪律只允许在异常场景显式跨环境取参"))
    else:
        out.append(_mk(
            "TODO-A-appid", "A 维度：契约未声明 appid 键，跨企业越权写需人工确认后补充",
            "A", "待补充", dict(base), {"success": False},
            action=action, enabled=False, needs="manual", priority="P1",
            source="generator:auth_placeholder",
            note="契约的参数表与请求示例都没有 appid——本接口大概率不经应用身份隔离，"
                 "跨企业 appid 探测是无效注入。若服务端确有租户校验（契约漏写），"
                 "请人工补跨企业写用例（A06 双重越权同理）"))

    if ident_name:
        out.append(_write(
            "A04", "越权矩阵·子身份写：%s 换为子账号提交（应被拒绝或降权）" % ident_name,
            None, "<子账号>", False, "P1",
            "纵向越权：子账号身份执行主账号才允许的操作应被拒绝或降权。"
            "约定允许管理员代建（creater 可任填）的接口应仍成功——此时记「待确认」而非缺陷。"
            "需 personal_config.sub_account（或 WJX_SUB）"))
        out.append(_write(
            "A05", "越权矩阵·主身份写（A04 的对照）：%s 显式填主账号（应成功）" % ident_name,
            None, "<主账号>", True, "P2",
            "对照组：与 A04 配对判读。A04 被拒而本条成功 → 降权生效；"
            "两条都成功 → 服务端未校验身份一致性，需人工确认是否属于设计内。"
            "需 personal_config.main_account（或 WJX_MAIN）"))
        if "appid" in declared:
            out.append(_write(
                "A06", "越权矩阵·双重越权：跨企业 appid + 主账号 %s（应被拒绝）" % ident_name,
                "<跨企业appid>", "<主账号>", False, "P1",
                "身份与资源归属双重矛盾：跨企业凭证却声明本企业主账号身份，"
                "若被接受说明服务端既不校验租户也不校验身份一致性"))

        # 契约写明的权限约束——只有语义层读得出来，规则层看着字段名只能猜"这是个人名"
        roles = ((ident or {}).get("llm_constraints") or {}).get("required_role_for_subaccount") or []
        if (ident or {}).get("llm_role") == "operator_identity" and roles:
            out.append(_write(
                "A08", "权限约束·子账号缺角色：%s 填无「%s」角色的子账号（应被拒绝）"
                       % (ident_name, "」/「".join(roles)),
                None, "<无权限子账号>", False, "P0",
                "契约写明的权限约束，不是靠字段名猜的越权（evidence：%s）。"
                "需 personal_config 补充一个不具备上述角色的子账号；未配置时执行期点名跳过。"
                % ((ident or {}).get("llm_evidence") or "未提供")))
    else:
        # 没有身份字段可注入 → 纵向越权这一整列无法自动构造。点名待补，不静默缺失。
        out.append(_mk(
            "TODO-A-越权", "A 维度：契约无身份字段，纵向越权（子/主账号切换）与横向越权需人工补充",
            "A", "待补充", dict(base), {"success": False},
            action=action, enabled=False, needs="manual", priority="P0",
            source="generator:auth_placeholder",
            note="契约里没有 creater/creator/userid/account 等身份字段，自动生成器无法构造"
                 "「换一个身份操作」的请求，也无法构造「他人资源」的越权请求"
                 "（需要另一个账号真实创建的对象 ID）。"
                 "请人工补：① 身份冒用用例 ② 横向越权（拿他人资源 id 操作）用例"))

    # --- 读面：越权读取比越权写入更容易被漏掉（写入常被参数校验挡住，读取直接泄数据） ---
    if read_action and "appid" not in declared:
        # 与 A02/A06 同一门控：执行期跨企业读是把 appid 塞进请求报文显式携带，
        # 契约不声明 appid 时服务端大概率不认识这个键（或直接忽略）——“本企业读得到、
        # 跨企业也读得到”就成了对无效参数的误读，产出高危假越权缺陷。
        out.append(_mk(
            "TODO-A-读面-appid", "A 维度：契约未声明 appid 键，跨企业读探测需人工确认后补充",
            "A", "待补充", dict(rp), {"success": False},
            action=read_action, enabled=False, needs="manual", priority="P1",
            source="generator:auth_placeholder",
            note="契约的参数表与请求示例都没有 appid——本接口大概率不经应用身份隔离，"
                 "跨企业读注入是无效探测。若服务端确有租户校验（契约漏写），"
                 "请人工补跨企业读用例（A03/A07 同理）"))
    elif read_action:
        out.append(_read(
            "A03", "越权矩阵·跨企业读：本企业可读为对照，跨企业 appid 调 %s 应取不到数据" % read_action,
            "P0",
            "配对判定：本企业身份先自证可读，再用跨企业 appid 读同一资源。"
            "跨企业也读到 = 越权缺陷；本企业也读不到 = 无法归因，只记观察不判越权。"
            "需 cross_appid 与配套读接口（read_action=%s）" % read_action))
        if rp_has_id:
            out.append(_read(
                "A07", "越权矩阵·跨企业读指定资源：带本企业资源 id 用跨企业 appid 读（应取不到）",
                "P0",
                "横向越权读：带上本企业资源标识去读，跨企业身份应取不到该资源。"
                "read_params 里必须带资源 id，否则与 A03 等价"))
        else:
            out.append(_mk(
                "TODO-A-读面资源", "A 维度：read_params 未带资源标识，跨企业读指定资源的横向越权需人工补充",
                "A", "待补充", dict(rp), {"success": False},
                action=read_action, enabled=False, needs="read_params", priority="P1",
                source="generator:auth_placeholder",
                note="配套读参数里没有 id/vid/sid/uid 类键，无法构造"
                     "「拿本企业资源 id 去跨企业读」的横向越权请求。"
                     "给 read_params 补上资源 id 后本条会自动生成为 A07"))
    else:
        out.append(_mk(
            "TODO-A-读面", "A 维度：缺配套读接口，越权矩阵读面（跨企业读/横向越权读）无法自动构造",
            "A", "待补充", dict(base), {"success": False},
            action=action, enabled=False, needs="read_action", priority="P0",
            source="generator:auth_placeholder",
            note="越权**读取**往往比越权写入更致命（写入常被参数校验挡住，读取直接泄数据），"
                 "但读面用例必须打到配套读接口上。请用 --read-action <action> 指定，"
                 "或在契约里声明 read_action 后重跑——读面 A03/A07 会自动补上"))
    return out


def gen_structure_and_transport(contract, base, action):
    """Y 复杂结构返回 / AE 认证与传输补充——两者在适用性矩阵里对创建类接口为必测，
    若不生成对应用例，覆盖率门禁会把它们判成「漏测」假缺口。"""
    out = []
    if contract.get("response_fields"):
        out.append(_mk("Y01", "复杂结构返回：各子类型结构完整、无缺层", "Y", "复杂结构返回",
                       dict(base), {"success": True},
                       check="structure_complete",
                       check_args={"documented_fields": [f["name"] for f in contract["response_fields"]]},
                       action=action, priority="P1", source="generator:structure"))

        # K：响应字段的类型与枚举是否符合契约声明。
        # 只收契约真的写了的 type / enum——没写的字段不进比对，避免拿"契约沉默"当断言。
        type_map, enum_map = {}, {}
        for f in contract["response_fields"]:
            nm = f.get("name")
            if not nm:
                continue
            if f.get("type"):
                type_map[nm] = f["type"]
            # 枚举只读契约真声明的那一栏：解析器把 enum 放在字段顶层；早期写成
            # constraints.enum 是错的（全仓没有该写入点），导致 K01 的 enum_map
            # 恒为空——枚举校验从未执行却照样 PASS。两种形态都收，兼容手工契约。
            enum_vals = f.get("enum") or (f.get("constraints") or {}).get("enum")
            if enum_vals:
                enum_map[nm] = list(enum_vals)
        if type_map or enum_map:
            out.append(_mk(
                "K01", "响应字段类型与枚举取值符合契约声明", "K", "契约测试",
                dict(base), {"success": True},
                check="response_types",
                check_args={"type_map": type_map, "enum_map": enum_map},
                action=action, priority="P1", source="generator:response_types",
                note="K：只校验契约明确声明的类型与枚举，不一致记「待确认」而非缺陷——"
                     "接口把数字返回成字符串很常见，直接判缺陷会一片误报；null 跳过。"
                     "缺字段/多字段由 Y01 与 contract_diff 负责，这里不重复报。"))
    out.append(_mk("AE01", "认证与传输契约：请求头/传输语义", "AE", "认证传输补充",
                   dict(base), {"success": True},
                   check="transport_contract", action=action, priority="P1",
                   source="generator:transport",
                   note="AE：Content-Type/Accept/gzip/trace-id 等传输层约定"))
    return out


def _resp_id_field(contract):
    """响应里的资源标识字段名（交叉验证到列表里定位资源时用）。"""
    for f in (contract.get("response_fields") or []):
        n = (f.get("name") or "").lower()
        if n in ("vid", "id", "sid", "uid", "page_id"):
            return f["name"]
    for f in (contract.get("response_fields") or []):
        n = (f.get("name") or "").lower()
        if n.endswith(("id", "_id")):
            return f["name"]
    return None


def gen_cross_verify_cases(contract, base, action, ctx):
    """C1 交叉验证：不只看主接口响应，调独立接口看数据到底落没落（framework §9.4.2）。

    「响应 success 但没落库 / 落错库」只看主接口永远抓不到。列表接口与主接口越独立，
    这条断言越可信，所以只在拿到配套**列表**接口时才生成；拿不到就诚实留空，
    由 TODO 占位在报告里点名，不拿主接口的响应假装验过。
    """
    out = []
    la = ctx.get("list_action")
    op = ctx.get("op_type")
    if not la or op not in ("创建", "删除"):
        return out
    present = (op == "创建")
    delta = 1 if present else -1
    idf = _resp_id_field(contract)
    cargs_list = {"list_action": la, "expected_present": present, "list_field": "list"}
    if idf:
        cargs_list["id_field"] = idf
    out.append(_mk(
        "C-CROSS-LIST", "交叉验证·列表侧：%s后资源应在独立列表接口 %s 中%s" % (op, la, "出现" if present else "消失"),
        "C", "交叉验证", dict(base), {"success": True},
        check="cross_list", check_args=cargs_list,
        action=action, priority="P0", source="generator:cross_verify",
        note="§9.4.2：主接口说成功不算数，去独立列表接口里按资源 id 找。"
             "列表里找不到 = 没落库或落错库，是只看响应永远抓不到的缺陷。"
             "资源 id 取自主接口响应%s" % ("的 %s 字段" % idf if idf else "（未识别到 id 字段，"
                                            "可用 check_args.id_field 指定）")))
    out.append(_mk(
        "C-CROSS-COUNT", "交叉验证·计数侧：%s后列表总数应 %+d" % (op, delta),
        "C", "交叉验证", dict(base), {"success": True},
        check="count_delta",
        check_args={"count_action": la, "expected_delta": delta, "count_field": "total"},
        action=action, priority="P1", source="generator:cross_verify",
        note="§9.4.2 计数行。基线在主请求**发出前**采集（+1/-1 是操作前后的差），"
             "check 内两次取数都已落在操作后，故无法自采基线。"
             "若列表接口不返回 total，本条只记观察不判缺陷"))
    return out


# 语义事实键 → 是否有消费方。抽取出来的键不等于被用上的键：「并入 N 条事实」的口径
# 比「兑现 M 条」丰满，报告必须把两者分开，否则看起来接得很满、其实一半只是存档。
_FACT_CONSUMERS = {
    "role": "llm_fact_cases(publish_flag/state) · dim_generators(ROLE_DIM_MAP) · A08",
    "constraints.alias_of": "llm_fact_cases._alias_cases（驱动别名硬断言）",
    "constraints.required_role_for_subaccount": "case_generator A08（越权矩阵）",
    "constraints.default": "llm_fact_cases._flag_cases（publish_flag）+ _default_cases（evidence 有据）",
    "constraints.max_len": "仅交叉校验；不驱动生成（B 维度用契约原生 max_len）",
    "constraints.enum": "仅交叉校验；不驱动生成（规则层遍历契约原生 enum）",
    "constraints.forbid": "llm_fact_cases._forbid_cases（仅 evidence 有据的禁用词）",
    "constraints.relative": "不消费",
    "constraints.is_url": "不消费（URL 判定用 field_semantics 语义标签）",
    "constraints.fallback_chain": "llm_fact_cases._fallback_chain_cases（字段级兜底表达，与 relation 级去重）",
    "relations": "llm_fact_cases(fallback_chain / alias / url_compose)",
    "unknowns": "llm_fact_cases（留痕 + consumed_unknowns 去重）",
    "semantics": "不消费（供人工阅读）",
    "evidence": "报告留痕，不驱动生成",
    "confidence": "不消费（供人工阅读）",
    "test_implications": "不消费（模型的用例建议当前不自动采纳）",
}


def _load_facts_json(path):
    """读事实 JSON，容忍模型把 JSON 包在代码块或附加解释里。

    提示词要求「只输出 JSON」，但模型经常仍然加 ```json 围栏或前后缀说明。
    直接 json.loads 会把这种情况算成「语义层没接」——而它其实只是格式没听话，
    白白丢掉一整次抽取结果。故逐级退化：原样 → 剥围栏 → 截取最外层花括号。
    """
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    try:
        return json.loads(raw), ""
    except ValueError:
        pass
    m = re.search(r"```(?:json)?\s*(.+?)\s*```", raw, re.S)
    if m:
        try:
            return json.loads(m.group(1)), "已从 ```json 代码块中提取"
        except ValueError:
            pass
    i, j = raw.find("{"), raw.rfind("}")
    if i != -1 and j > i:
        try:
            return json.loads(raw[i:j + 1]), "已从混杂文本中截取最外层 JSON 对象"
        except ValueError:
            pass
    raise ValueError("事实 JSON 解析失败（已尝试原样、剥代码块、截取花括号三级退化）")


def _fact_utilization(clean):
    """「抽取到的键」与「真正有消费方的键」分开列，避免并入量虚高。"""
    produced = set()
    for f in (clean.get("fields") or {}).values():
        for k in (f.get("constraints") or {}):
            produced.add("constraints.%s" % k)
        for k in ("role", "semantics", "evidence", "confidence", "test_implications"):
            if f.get(k) not in (None, "", [], {}):
                produced.add(k)
    if clean.get("relations"):
        produced.add("relations")
    if clean.get("unknowns"):
        produced.add("unknowns")
    consumed = {k for k in produced
                if k in _FACT_CONSUMERS and "不消费" not in _FACT_CONSUMERS[k]}
    return {
        "produced": sorted(produced),
        "consumed": sorted(consumed),
        "archived": sorted(produced - consumed),
    }


def facts_shape_error(facts):
    """事实文件形状判据——**单一来源**：生成期与 flow 严格模式预检共用。

    返回空串表示形状可校验；非空串是可直接展示给人的原因。两处各写一份必然漂移：
    flow 认为「已交作业」放过去、生成期却报形状错，严格模式就成了「没比对就生成」。
    """
    if not isinstance(facts, dict):
        return "事实 JSON 顶层必须是对象，实际是 %s" % type(facts).__name__
    if not any(k in facts for k in ("fields", "relations", "unknowns")):
        return ("事实 JSON 缺少 fields/relations/unknowns 任一顶层键"
                "（当前顶层键：%s）。若这是 --mode merge 的 --out 产物"
                "（合并后契约），请改传模型产出的原始事实文件"
                % "、".join(sorted(str(k) for k in facts)[:8]))
    return ""


def _enrich_llm_facts(contract, facts_path, contract_text, enabled=True, extra_known_fields=None):
    """并入大模型语义层事实（可选）。

    ``enabled=False`` 是「关闭语义层」的**唯一**开关：连 WJX_LLM_FACTS 环境变量
    也不读。只把 flow 侧的路径置空是不够的——本函数自己还会去读环境变量，
    那样「关闭」就成了半个开关（声明纯机械、实际仍并入事实）。
    """
    if not enabled:
        return contract, None
    path = facts_path or os.environ.get("WJX_LLM_FACTS") or ""
    if not path:
        return contract, None
    try:
        import llm_semantics
        facts, note = _load_facts_json(path)
        # 顶层必须是对象：模型可能直接返回数组或裸字符串。这类输入无法校验，
        # 必须降级返回 error，不能让 AttributeError 把整个生成流程打断。
        # 【形状守卫】merge 的 --out 写的是**合并后的契约**（顶层 request_fields /
        # response_fields），而 --llm-facts 要吃**事实 JSON**（顶层 fields /
        # relations / unknowns）。把前者误传进来时 verify 会拿到空 fields，
        # 于是零事实并入、不报错、不出声——产物看着完整，事实其实一条没进，
        # 正是 producer→consumer 形状漂移最典型的假通过。
        # 三者全无才拦：只产 unknowns（没提交任何 field）是合法事实文件，
        # 要求必须有 fields 会把它误杀。
        _shape_err = facts_shape_error(facts)
        if _shape_err:
            return contract, {"path": path, "error": _shape_err}
        _meta = {}
        rows, clean = llm_semantics.verify(facts, contract, contract_text or "",
                                           out_meta=_meta,
                                           extra_known_fields=extra_known_fields)
        llm_semantics.merge(contract, clean)
    except (OSError, ValueError, ImportError, AttributeError, TypeError) as e:
        return contract, {"path": path, "error": str(e)}
    # 拒收判定认「保留态」：事实进了 clean 的都算未拒收，包括新增的 SUSPECT_BASE
    # （基准可疑——事实保留、只是不覆盖契约值）。用 PASS_STATES 判定会把它
    # 误算成拦截，与 merged_fields 的实际值自相矛盾。
    rejected = [r for r in rows if r[2] not in llm_semantics.RETAINED_STATES]
    # 「按契约值核验一致」「放行采信」「仅验证据」三种结论必须分开记账：
    # 前两者有契约值参与判定，第三者只校验了字段存在与 evidence 是原文子串。
    # 混在一起会让「核验通过 N 条」被读成 N 项都验过值。
    verified = [r for r in rows if r[2] == llm_semantics.PASS_VERIFIED]
    unverified = [r for r in rows if r[2] == llm_semantics.PASS_UNVERIFIED]
    evidence_only = [r for r in rows if r[2] == llm_semantics.PASS_EVIDENCE_ONLY]
    suspect = [r for r in rows if r[2] == llm_semantics.SUSPECT_BASE]
    return contract, {"path": path,
                      "note": note,
                      "merged_fields": len(clean["fields"]),
                      "merged_relations": len(clean["relations"]),
                      "unknowns": len(clean["unknowns"]),
                      "verified_count": len(verified),
                      "unverified_count": len(unverified),
                      "unverified": unverified,
                      "evidence_only_count": len(evidence_only),
                      "evidence_only": evidence_only,
                      "suspect_count": len(suspect),
                      "suspect": suspect,
                      # 机械值本身待核的字段名（基准可疑 + 原文歧义），生成侧据此把
                      # 探针用例标 base_suspect，执行期降级判定。
                      "suspect_fields": _meta.get("suspect_fields") or [],
                      "rejected_count": len(rejected),
                      "rejected": rejected,
                      "fact_utilization": _fact_utilization(clean)}


def _coerce_typed_value(value, ftype):
    """按契约声明的类型把 --exclude-params 的字符串值转为请求所需形态。

    只做保守转换：int 类的纯数字串 → int，float 类 → float，bool 类按
    true/false/1/0 识别，array/object 类尝试 JSON 解析（失败保持字符串，
    因为契约的 array 声明也可能被解析噪音污染）；声明类型但值转不动
    → ValueError（点名，不让非法固定值悄悄进每条请求）；无声明或转换表
    不覆盖 → 原样字符串。
    """
    text = str(value)
    toks = {t for t in re.split(r"[^a-z0-9]+", str(ftype or "").lower()) if t}
    if toks & {"int", "int32", "int64", "integer"}:
        try:
            return int(text)
        except ValueError:
            raise ValueError("--exclude-params 固定值 %r 与契约声明的整数类型不符" % value)
    if toks & {"number", "float", "double"}:
        try:
            return float(text) if "." in text else int(text)
        except ValueError:
            raise ValueError("--exclude-params 固定值 %r 与契约声明的数值类型不符" % value)
    if toks & {"bool", "boolean"}:
        low = text.strip().lower()
        if low in ("true", "1", "yes"):
            return True
        if low in ("false", "0", "no"):
            return False
        raise ValueError("--exclude-params 固定值 %r 与契约声明的布尔类型不符" % value)
    if toks & {"array", "list", "object", "struct"}:
        try:
            parsed = json.loads(text)
        except ValueError:
            return value  # JSON 解析失败保持字符串（契约 array 声明可能是解析噪音）
        # json.loads("123") 会返回 int——只有解析出真正的容器才算强转成功
        return parsed if isinstance(parsed, (list, dict)) else value
    return value


def _case_hint(names, declared):
    """未命中的名字与某个声明名只差大小写时点名这一对。

    这是最常见的拼写错误形态（`Token` vs `token`），但两个名字在报告里看起来
    完全不同，只说「未声明」等于没说。只做严格的大小写折叠比较，不做编辑距离/
    前缀猜测——猜错会给出误导性的「你是不是想写 XXX」。
    """
    pairs = []
    for name in names:
        for decl in declared:
            if decl != name and str(decl).lower() == str(name).lower():
                pairs.append("（提示：%r 与契约里的 %r 仅大小写不同）" % (name, decl))
    return "".join(pairs)


def _apply_exclude_params(contract, entries, source="--exclude-params"):
    """把排除参数落到契约副本上：过滤字段 + 解析固定值。

    返回 (filtered_contract, seeded_values, trace)：
    - filtered_contract：deepcopy 后从 request_fields / request_example 剔除被排除
      字段——下游 9 个生成器、check_key 分母、模式引擎**自动**不围绕它们生成，
      无需逐生成点改造（84 个 _mk 调用点一个不动）；
    - seeded_values：{name: value}，种进 base_params 让每条请求仍携带合法值，
      否则其他维度的用例会死在缺参上（全量假 FAIL）；
    - trace：[{name, value_source, value?}]，随产物 excluded_params 留痕。

    值优先级：CLI 显式值 > 引擎注入（仅缺省 7 键信封参数）> 请求示例值 > 契约
    default 字段 > 报错拒绝。报错而非塞占位值：值缺失会让全量用例假失败，
    比不生成更糟。

    「碰不到」分两种，处置不同（同一句文案会让用户不知道该补值还是该删配置）：
    - 名字**不在声明面**（参数表 ∪ 请求示例）：忽略 + 点名（带值与裸名同口径）；
    - 名字**在声明面**但没有任何值来源：报错拒绝生成，只列这一组。
    """
    spec = common_params.parse_exclude_params(entries, source=source)
    if not spec:
        return contract, {}, []
    import copy as _copy
    # 值解析读**原契约**（被排除字段的 default/示例值在副本里已删除，先过滤就取不到）
    fields = {str(f.get("name") or "").strip(): f
              for f in (contract.get("request_fields") or [])}
    example = contract.get("request_example")
    example = example if isinstance(example, dict) else {}
    # 「引擎能注入哪些键」用 ENGINE_INJECTABLE_KEYS，不用 DEFAULT_EXCLUDE_PARAM_NAMES：
    # 后者是站点约定（可能被项目级配置替换），前者才是执行期的真实能力。
    engine_names = set(common_params.ENGINE_INJECTABLE_KEYS)

    seeded, trace, unresolved = {}, [], []
    # 契约声明面（参数表 ∪ 请求示例）：判定「这个参数接口到底认不认」。
    # 请求示例的键也要 strip：契约里出现 " token" 这种带空白的键时，声明面判定、
    # 取值、过滤三处必须与参数表同口径，否则同一条配置在两处得到相反结论。
    example_by_name = {str(k).strip(): v for k, v in example.items()}
    declared = set(fields) | set(example_by_name)
    ignored, ignored_bare = [], []
    for name, val in spec.items():
        if val is not None and name not in declared:
            # 契约里没有这个参数却给了固定值：旧实现照样种进 base，于是每条请求都带
            # 一个服务端不认识的键——它是假参数的来源，也会污染「报文字段」类断言
            # （服务端忽略未知键时用例看着照样成功，没人会发现配错了名字）。
            # 这里直接忽略，并在 trace / 产物 / stderr 三处点名。
            ignored.append(name)
            trace.append({"name": name, "value_source": "ignored_undeclared",
                          "value": val})
            continue
        if val is not None:
            ftype = (fields.get(name) or {}).get("type")
            seeded[name] = _coerce_typed_value(val, ftype)
            trace.append({"name": name, "value_source": "cli", "value": seeded[name]})
        elif name in engine_names:
            # 信封键：值由执行期引擎注入，**不从示例播种**。示例是文档里的举例值
            # （appid/sign/ts 多半属于另一个环境或已过期），一旦种进 base，它经
            # build_request_payload 的 extra 会覆盖执行期注入的正确值——实测会把
            # 示例里的旧 appid 发给当前环境，整轮请求全被拒。
            trace.append({"name": name, "value_source": "engine"})
        elif name in example_by_name:
            seeded[name] = example_by_name[name]
            trace.append({"name": name, "value_source": "request_example",
                          "value": example_by_name[name]})
        elif fields.get(name) and fields[name].get("default") is not None:
            seeded[name] = fields[name]["default"]
            trace.append({"name": name, "value_source": "contract_default",
                          "value": seeded[name]})
        elif name in declared:
            unresolved.append(name)
        else:
            # 裸名碰不到声明面：与「带值但未声明」同口径——忽略 + 点名，不硬失败。
            # 硬失败会把「拼错一个名字」升级成整轮不生成，且与「声明了但没值」混在
            # 同一句文案里，用户无从判断该补值还是该删配置。
            ignored_bare.append(name)
            trace.append({"name": name, "value_source": "ignored_undeclared_bare"})
    if unresolved:
        raise ValueError(
            "%s中，以下参数**已在契约声明面内**，但没有可用的值来源"
            "（请求示例没有、契约默认值没有，也不是引擎可注入的信封键）：%s——"
            "这类参数会让每条请求都缺它，故拒绝生成；请补齐契约/请求示例里的值，"
            "或改用 name=value 显式提供。%s"
            % (source, "、".join(unresolved), _case_hint(unresolved, declared)))
    if ignored or ignored_bare:
        names = ignored + ignored_bare
        sys.stderr.write(
            "[case_generator] ⚠️ 排除参数中以下 %d 个不在契约声明面（参数表与请求示例都没有），"
            "已忽略、不注入请求、也不参与用例生成：%s%s\n"
            "  请确认是否拼写错误；若该参数确属接口需要，应在契约/请求示例里声明它。"
            "（带值条目与裸名同口径：都不会进请求。）\n"
            % (len(names), "、".join(names), _case_hint(names, declared)))

    filtered = _copy.deepcopy(contract)
    # spec 的键在解析期已 strip；这里再归一一次，防止别处构造的 spec 带空白，
    # 导致「同一份排除集」在字段过滤处生效、在示例过滤处失效。
    excluded = {str(n).strip() for n in spec}
    filtered["request_fields"] = [f for f in (filtered.get("request_fields") or [])
                                  if str(f.get("name") or "").strip() not in excluded]
    if isinstance(filtered.get("request_example"), dict):
        filtered["request_example"] = {k: v for k, v in filtered["request_example"].items()
                                       if str(k).strip() not in excluded}
    # 种子值 deepcopy：它们引用原契约对象，浅拷贝 dict(base) 会让所有用例共享
    # 同一个 list/dict，下游原地修改即跨用例污染——别名必须在此切断
    seeded = _copy.deepcopy(seeded)
    return filtered, seeded, trace


_CHECK_KEY_DOC_KEYS = ("required", "required_keys", "delivered", "missing", "missing_keys")


def _stable_check_key(rec):
    """check_key 对账结果的稳定投影：cases_hash 覆盖整份用例文档，往里加一个键
    就会让所有已批准套件要求重新批准（test_golden_post_json 锁这条）。
    reconcile 可以返回更多诊断键，但落进文档的只保留既有五个、顺序不变；
    例外是 manual_only_keys——仅当真的存在「只有人工占位兑现」的约束时才追加，
    纯 body 契约（无占位）因此仍然逐字节不变，避免牵连已批准套件。"
    """
    out = {k: rec[k] for k in _CHECK_KEY_DOC_KEYS if k in rec}
    if rec.get("manual_only_keys"):
        out["manual_only_keys"] = rec["manual_only_keys"]
    return out


def _normalize_dim(dim):
    """用例维度归一：AF1 → AF、A01 → A，便于按维度字母精确排除。

    不能用「startswith」：排除单字母 A 会连带吃掉 AA / AE / AB / AC / AD / AF 全族。
    """
    s = str(dim or "").strip()
    core = s.rstrip("0123456789")
    return core or s


def _load_af_cases(src, contract):
    """读取**真实业务场景**（business_scenarios 产出的 af_cases）。

    接受三种形态：路径、`{"cases": [...]}`、裸数组。返回 ``(cases, error)``。
    形状不对时 error 非空并在调用方出声——静默当成「没提供」会让人以为交了材料
    却没生效，和「压根没交」在产物上长得一样。

    归一化复用 ``business_scenarios.normalize``（单一来源）：AF 用例必须与主链用例
    字段完全一致，尤其 ``tier``——手抄一份映射迟早漂移。
    """
    if src in (None, "", []):
        return [], ""
    if isinstance(src, (list, tuple)):
        raw = list(src)
    elif isinstance(src, dict):
        raw = src.get("cases")
        if raw is None:
            return [], ("af_cases 缺少 cases 数组（当前顶层键：%s）"
                        % "、".join(sorted(str(k) for k in src)[:6]))
    else:
        try:
            with open(str(src), "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            return [], "af_cases 读取失败：%s（%s）" % (src, exc)
        return _load_af_cases(data, contract)
    if not isinstance(raw, list):
        return [], "af_cases.cases 必须是数组，实际是 %s" % type(raw).__name__
    bad = [i for i, c in enumerate(raw) if not isinstance(c, dict)]
    if bad:
        return [], "af_cases.cases 第 %s 项不是对象" % "、".join(map(str, bad[:5]))
    if not raw:
        return [], "af_cases 里一条业务场景都没有"
    try:
        import business_scenarios as BS
        return BS.normalize(raw, contract), ""
    except Exception as exc:  # noqa: BLE001
        return [], "af_cases 归一化失败：%s: %s" % (type(exc).__name__, exc)


def generate(contract_path, action=None, read_action=None, list_action=None, read_params=None,
             auto_companion=True, llm_facts=None, dedup=False, exclude_entries=None,
             exclude_note="", envelope_override=None, llm_enabled=True, exclude_dims=None,
             af_cases=None):
    """契约 → 用例文档。

    ``llm_enabled=False``：关闭语义层（连 WJX_LLM_FACTS 环境变量都不读），
    由 flow.py 的 --no-llm-facts 显式传入；别的调用方不传时保持增量接入。

    read_action / list_action / read_params 是**交叉验证的配套接口**声明，透传给缺陷模式
    引擎与越权矩阵：给了就能做真往返（读→原样回写）与列表侧「创建后出现/计数+1」校验。

    exclude_entries：有效排除集条目（四级回退基底 + CLI 追加解析后的合并结果；
    None 时取出厂兜底 assets/exclude_params.default.json）。被排除的参数不参与
    任何用例生成（契约副本上过滤——**总是执行**，check_key 分母自动同步，
    信封键的 missing_required 永久假缺口由此消除），固定值/默认值种进 base
    让每条请求仍携带；明细随产物 excluded_params 留痕。

    envelope_override：信封注入键覆写（None=按判定；"no_inject"=全不注入；
    键列表=显式指定，必须 ⊆ 有效排除集）。判定规则：
    envelope_keys = 有效排除集 ∩（契约参数表字段名 ∪ 请求示例键），
    随 doc["envelope_keys"] 落盘，执行期据此决定注入哪些信封键（全链路联动）。

    三个来源按优先级：命令行显式指定 > 契约文档显式声明 > 同级目录扫描（companion_scan）。
    扫描只在**业务对象名对得上**时才采用，并把候选清单与置信度写进产物供审计；猜不出来的
    时候宁可空着让人指定，也不猜一个出来把 ENV 失败伪装成"往返已验证"。
    """
    contract = load_contract(contract_path, action=action)
    # 有效排除集：调用方未给（直接调 generate/测试）时取出厂兜底——过滤**总是执行**，
    # 信封键从生成范围退场是本机制的常态而非 CLI 附加项。
    if exclude_entries is None:
        entries = common_params.load_default_exclude_entries()
        exclude_note = exclude_note or "default(出厂兜底)"
    else:
        entries = list(exclude_entries)
    spec = common_params.parse_exclude_params(
        entries, source="排除参数（CLI 与配置文件合并后）")
    # 注入判定用「原契约」的声明面（参数表 ∪ 请求示例）——过滤前的完整事实；
    # 过滤后副本里信封键已被删，再取就只剩残缺声明面。
    declared = {str(f.get("name") or "").strip()
                for f in (contract.get("request_fields") or [])}
    ex_raw = contract.get("request_example")
    if isinstance(ex_raw, dict):
        # 示例键统一 strip：与参数表同口径，否则 " appid" 这类键既不进信封集合，
        # 又会被 _apply_exclude_params 当成「已声明」，两处结论分叉。
        declared |= {str(k).strip() for k in ex_raw}
    envelope_keys = sorted(set(spec) & declared)
    # 自检：示例没解析出来时，声明面只剩参数表——信封键集体判不到，整轮请求会因
    # 缺公共参数被服务端拒绝。契约示例块的格式问题（语言标签/HTML 实体/非 JSON）
    # 与「接口本来就不用信封」在产物上长得一样，必须点名区分。
    if not envelope_keys and spec and not ex_raw:
        sys.stderr.write(
            "[case_generator] ⚠️ 契约未解析出请求示例，信封注入判定为 0——若该接口需要公共参数"
            "（encode/appid/sign 等），请求会因缺参被拒。请检查契约示例块格式"
            "（```json 之外的标签、HTML 实体、非 JSON 正文都会让示例被丢弃）\n")
    if envelope_override == "no_inject":
        envelope_keys = []
    elif envelope_override is not None:
        bad = set(envelope_override) - set(spec)
        if bad:
            raise ValueError("--envelope-keys 以下键不在有效排除集内（信封值不允许"
                             "覆盖业务字段）：%s——有效排除集：%s"
                             % ("、".join(sorted(bad)), "、".join(sorted(spec)) or "（空）"))
        envelope_keys = sorted(set(envelope_override))
    seeded, excluded_trace = {}, []
    # 位置登记必须取**过滤前**的字段表：被排除的 header/query/path 参数仍会以固定值
    # 进入每条请求，位置一旦随字段一起被删掉，执行期查不到位置就按 body 发——参数
    # 明明配了值，服务端却报「缺少该参数」，而台账看不出是位置丢了。
    _loc_fields = list(contract.get("request_fields") or [])
    if spec:
        contract, seeded, excluded_trace = _apply_exclude_params(
            contract, entries, source="有效排除集")
    # 被忽略的排除参数（契约没声明）：单独列出来，报告侧与依赖 excluded_params 的
    # 下游都能一眼看到「这条配置没生效」。带值条目与裸名同口径，只是取值不同——
    # 两者在报告里必须可区分：带值是配置错误，裸名多半是契约缺声明。
    _IGNORED_SOURCES = ("ignored_undeclared", "ignored_undeclared_bare")
    ignored_excluded = [t["name"] for t in excluded_trace
                        if t.get("value_source") in _IGNORED_SOURCES]
    if exclude_note:
        sys.stderr.write("[case_generator] 排除参数来源：%s；注入判定：%d 个信封键（%s）\n"
                         % (exclude_note, len(envelope_keys),
                            "、".join(envelope_keys) or "无——本轮不注入公共参数"))
    contract = annotate(contract)
    # AF（业务场景）：只有**真实业务场景**才算参与。机械拼出的合法请求只能证明
    # 字段可发，证明不了「用户实际怎么用」，拿它顶业务场景等于把假覆盖写进分母。
    # 没有真场景时 judge 会把 AF 标不参与并写明缺哪份输入（AF_NO_SCENARIO）。
    _af_list, _af_err = _load_af_cases(af_cases, contract)
    if _af_err:
        contract["af_cases"] = []
    else:
        contract["af_cases"] = _af_list
    app = judge(contract)
    # 【显式关闭维度】只有调用方明确给出才排除，默认一个都不关。排除必须同时做三件：
    # 出覆盖率分母、出用例集合、在 N/A 证明里留痕——只改分母会留下「不参与却仍生成
    # 用例」的假象，只删用例会让门禁报假缺口。
    excluded = [str(d).strip().upper() for d in (exclude_dims or []) if str(d).strip()]
    unknown = [d for d in excluded if d not in ALL_DIMS]
    if unknown:
        raise ValueError("--exclude-dims 含未知维度：%s（合法值：%s）"
                         % ("、".join(unknown), ",".join(ALL_DIMS)))
    if excluded:
        ex = set(excluded)
        app["coverage_plan"] = [d for d in app["coverage_plan"] if d not in ex]
        app["dimensions"].update({d: "显式排除" for d in excluded})
        app["na_proofs"] = [p for p in app["na_proofs"] if p["dim"] not in ex]
        app["na_proofs"].extend(
            {"dim": d, "reason": "本轮由 --exclude-dims 显式关闭，非条件不满足"} for d in excluded)
        app["notes"].append("本轮显式关闭维度：%s（必须写明理由，不得当成默认不适用）"
                            % "、".join(excluded))
    # 非 body 参数的落点：只登记 query/header/path（body 是缺省，不落键）。
    # 用**数组**而不是 {name: location} 映射：映射的键是参数名，当参数名叫 token /
    # cookie / sign 时，asset_runtime._credential_paths 的整树凭据扫描会把「参数名」
    # 当成敏感键、把 "query" 当成它的值 → 批准直接失败，而报错文案指向「含真实凭据」，
    # 几乎不可能被正确定位。
    param_locations = []
    _all_locs, _loc_conflicts = {}, []
    for _field in _loc_fields:
        _name = str(_field.get("name") or "").strip()
        _loc = str(_field.get("location") or "body").strip().lower()
        if not _name:
            continue
        # 冲突判定覆盖**全部**位置（含 body）：同一名字在契约里写两行，本身就是歧义源。
        # body 不进 param_locations，所以「一处 body、一处 header」只有生成期看得见。
        _all_locs.setdefault(_name, []).append(_loc)
        if _loc not in ("query", "header", "path"):
            continue
        if (_name, _loc) in {(p["name"], p["location"]) for p in param_locations}:
            continue          # 完全重复的行（同名同位置）：去重即可，不算冲突
        param_locations.append({"name": _name, "location": _loc})
    for _n, _locs in _all_locs.items():
        _distinct = sorted(set(_locs))
        if len(_distinct) > 1:
            _loc_conflicts.append((_n, _distinct))
    base = pattern_engine.base_params(contract, common_names=set(spec))
    # 排除参数的固定值/默认值种进基线：它们不参与变异，但每条请求仍要携带合法值
    if seeded:
        base.update(seeded)
    # 【W31 配套】_needs_manual_baseline 是元信息标记，不是请求参数——剥离并
    # 升到产物顶层，让「这些字段的基线是猜的」在报告可见。
    needs_manual = base.pop("_needs_manual_baseline", [])

    raw = ""
    try:
        with open(contract_path, "r", encoding="utf-8-sig", errors="ignore") as fh:
            raw = fh.read()
    except OSError:
        raw = ""

    contract, llm_report = _enrich_llm_facts(
        contract, llm_facts, raw, enabled=llm_enabled,
        # 过滤前的字段表（含被排除集拿走的信封键 action/appid/sign）：事实校验的
        # 「字段存在」判据必须是契约全量声明面，否则智能体对信封参数写的合法
        # 事实会被误判成「臆造字段」。
        extra_known_fields={str(f.get("name") or "").strip() for f in _loc_fields})
    if llm_report:
        contract["llm_verify_report"] = llm_report
        if llm_report.get("error"):
            # 语义层接入失败必须出声。静默降级会让「这次没接语义层」和
            # 「接了但没产出」看起来一样，使用者以为事实已经生效。
            sys.stderr.write("[llm] 语义层接入失败，已按纯规则继续：%s\n"
                             % llm_report["error"])
        else:
            if llm_report.get("note"):
                print("[llm] %s" % llm_report["note"])
            # 被拒收的事实必须出声：它们只落在产物的 llm_verify_report.rejected 里，
            # 不在屏幕上说的话，「交完作业」与「作业一条没用上」看起来完全一样。
            _rejected = llm_report.get("rejected_count") or 0
            if _rejected:
                sys.stderr.write(
                    "[llm] 大模型理解：并进 %d 个字段、拒收 %d 条（证据对不上或数值无据）——"
                    "明细见产物 llm_verify_report.rejected；本轮用例不含被拒的那部分\n"
                    % (llm_report.get("merged_fields") or 0, _rejected))

    comp = resolve_companion(contract_path, contract_text=raw, cli_read=read_action,
                             cli_list=list_action, cli_read_params=read_params,
                             main_action=contract.get("action"), auto=auto_companion)
    ra = comp["read_action"] or contract.get("read_action")
    la = comp["list_action"] or contract.get("list_action")
    rp = comp["read_params"] or contract.get("read_params") or {}

    ctx = {
        "base": base,
        "op_type": app["op_type"],
        "is_intl": app["is_intl"],
        "is_list_query": app.get("is_list_query"),
        "coverage_plan": app["coverage_plan"],
        "read_action": ra,
        "read_params": rp,
        "list_action": la,
        "companion": comp,
        # 【审查修复·pick_by_family 纯化】字段族退化的告警收集器：生成器不再直接
        # 写 stderr，改由这里统一收集 → 落产物 gen_warnings + stderr 摘要。
        "gen_warnings": [],
        "param_locations": param_locations,
    }
    for _n, _locs in _loc_conflicts:
        if "body" in _locs:
            # 跨类冲突（body 与非 body）：body 位置不进 param_locations，装载期
            # 看不到、不会拦——文案必须如实说「body 那处不进请求」，不能套用
            # 「装载时会拒绝」，否则用户按提示等一个不会发生的报错。
            _others = "/".join(l for l in _locs if l != "body")
            ctx["gen_warnings"].append(
                "参数 %r 同时声明在 body 与 %s：执行期只按 %s 发送，body 那处不会"
                "出现在请求里——请把其中一处改名，或修正契约里的位置列后重新生成"
                % (_n, _others, _others))
        else:
            ctx["gen_warnings"].append(
                "参数 %r 在契约里同时声明在多个位置（%s）：执行期只按参数名分流，"
                "无法同时落两处，装载时会直接拒绝执行——请把其中一处改名，"
                "或修正契约里的位置列后重新生成" % (_n, "/".join(_locs)))
    cases = []
    cases += gen_baseline(contract, base, contract.get("action"))
    cases += gen_auth_cases(contract, base, contract.get("action"),
                            read_action=ra, read_params=rp, declared=declared)
    cases += gen_cross_verify_cases(contract, base, contract.get("action"), ctx)
    mutation_skipped = []
    cases += gen_field_mutations(contract, base, contract.get("action"),
                                 skipped=mutation_skipped)
    cases += gen_combination_cases(contract, base, contract.get("action"))
    payload_skipped = []
    cases += gen_security_payload_cases(contract, base, contract.get("action"),
                                        skipped=payload_skipped)
    cases += gen_structure_and_transport(contract, base, contract.get("action"))
    cases += dim_generators.run_all(contract, base, contract.get("action"), ctx)
    cases += llm_fact_cases.run(contract, base, contract.get("action"), ctx)
    pcases, matched = pattern_engine.run(contract, ctx)
    cases += pcases
    # 真实业务场景用例直接并入（来源 business_scenario）。没有场景时 AF 已在
    # judge 里转不参与，这里不做任何兜底——绝不凭空造 AF 用例充数。
    if _af_err:
        ctx["gen_warnings"].append("AF 未参与：%s" % _af_err)
    if _af_list and "AF" in app["coverage_plan"]:
        cases += _af_list

    # 必测维度兜底：生成器确实覆盖不了的维度，显式生成一条「需人工补充」占位，
    # 让它在报告里被点名，而不是静默缺失（N/A 必须列出证明已判断）。
    covered = {_normalize_dim(c.get("dimension")) for c in cases}
    for d in app["coverage_plan"]:
        if d in covered:
            continue
        cases.append(_mk("TODO-%s" % d, "维度 %s：生成器未自动覆盖，需人工补充用例" % d, d,
                         "待补充", dict(base), {"success": True},
                         action=contract.get("action"), enabled=False, needs="manual",
                         priority="P1", source="generator:placeholder",
                         note="自动生成的占位：执行时跳过并在报告中点名，避免静默漏测"))

    # 选测维度零用例时只在产物里点名，不生成占位用例：占位会撑长清单却不执行，
    # 而「本轮哪些选测维度是空的」这个信息本身要可审计。
    #
    # 点名必须带原因。只列维度字母时，「契约确实没有该特征」与「有特征但响应侧无法
    # 构造请求」「维度压根没实现」三者在报告里长得一样，能力缺口会被读成「无需测」。
    uncovered_optional = [d for d in (app.get("optional_plan") or []) if d not in covered]
    _reasons = dim_generators.explain_zero_output(contract, uncovered_optional)
    uncovered_optional_detail = [
        {"dimension": d, "reason": _reasons.get(d, dim_generators.REASON_NO_FEATURE),
         "reason_text": dim_generators.REASON_TEXT.get(
             _reasons.get(d, dim_generators.REASON_NO_FEATURE), "")}
        for d in uncovered_optional]

    cases = _enrich_cases(cases, contract)
    if excluded:
        ex = set(excluded)
        cases = [c for c in cases if _normalize_dim(c.get("dimension")) not in ex]

    # 去重 id
    seen, uniq = {}, []
    for c in cases:
        cid = c.get("id") or "CASE"
        if cid in seen:
            seen[cid] += 1
            c["id"] = "%s#%d" % (cid, seen[cid])
        else:
            seen[cid] = 0
        uniq.append(c)

    # 【条件不满足的最终判据】预设参与是按契约事实推的，能不能真跑要等生成完才知道：
    # 只有 TODO 占位的维度不是「参与」，是「声明了却跑不了」（缺签名/身份字段、无 trace
    # 语义、缺页面基线、缺回调地址）。按「只有条件不满足才不参与」的口径，这些维度必须
    # 转不参与并写明缺什么——否则执行档每轮拿它们报假缺口，默认全开就变成默认必挂。
    _executable = {_normalize_dim(c.get("dimension")) for c in uniq
                   if _is_executable_case(c)}
    _demoted = []
    for _d in list(app["coverage_plan"]):
        if _d in _executable:
            continue
        _why = ""
        for _c in uniq:
            if (_normalize_dim(_c.get("dimension")) == _d
                    and not _is_executable_case(_c)):
                _why = str(_c.get("note") or "").strip().replace("\n", " ")
                break
        app["coverage_plan"].remove(_d)
        app["dimensions"][_d] = "不适用"
        app["na_proofs"].append({
            "dim": _d,
            "reason": ("本轮无法构造可执行用例：%s"
                       % (_why or "缺必要输入，需人工补充"))[:200]})
        _demoted.append(_d)
    if _demoted:
        # 占位用例**保留**：它标了 enabled=False/needs=manual，执行时只记 SKIPPED、
        # 不记 COV-* 缺陷，同时把「要补什么」留在清单里给人看。删掉它等于把人工
        # 补充点从产物里抹掉，只剩一句 na_proofs 理由，信息反而更少。
        _order = {d: i for i, d in enumerate(ALL_DIMS)}
        app["na_proofs"].sort(key=lambda p: _order.get(p["dim"], 999))
        app["notes"].append(
            "本轮 %d 个维度因条件不满足转不参与（缺什么见 na_proofs）：%s"
            % (len(_demoted), "、".join(_demoted)))
        sys.stderr.write(
            "[applicability] %d 个维度条件不满足，已转不参与并写明原因：%s\n"
            % (len(_demoted), "、".join(_demoted)))

    # 去重签名：判等而非打分（为什么不用相似度见 case_signature 模块头）。
    # 默认只统计不折叠——折叠会改变用例总数，让历史基线与覆盖率口径被动变化；
    # 显式 dedup=True 才真折叠，并把被合并用例的意图并入首条，不丢证据。
    dup_report = case_signature.duplicate_stats(uniq)
    conflicts = case_signature.expectation_conflicts(uniq)
    collapsed = 0
    if dedup:
        uniq, collapsed = case_signature.collapse(uniq)
    dup_report["collapsed"] = collapsed

    # 【嫌疑字段降级】机械值本身可疑的字段，其探针用例的期望方向不可靠：
    # 执行期把「缺陷」降为「待确认」，避免解析抽错直接变成假缺陷进台账。
    # 打标放在去重/签名之后——判重结果完全不受影响；也只在真有可疑项时才写
    # 这个键，默认路径（无事实或无可疑项）产物逐字节不变。
    _sus = _suspect_fields(contract)
    if _sus:
        for c in uniq:
            if c.get("base_suspect") is None and _probe_field_of(c) in _sus:
                c["base_suspect"] = True

    # 计划 vs 实际：coverage_plan 是「必测维度」的声明。此前只统计已生成的维度，
    # 某个必测维度一条都没生成时报告里看不出来——这里把分母固定成计划本身。
    plan_vs_actual = plan_reconcile(app["coverage_plan"], uniq)
    for gap in plan_vs_actual["gaps"]:
        ctx["gen_warnings"].append("必测维度 %s 在计划内，但本轮实际生成 0 条" % gap)

    # 优先级分布：wjx 的 P0 含义是「必测/断言明确」，机械探测也大量标 P0，
    # 所以 P0 占比天然偏高。这里只做失真预警，不让 P0 数量替业务覆盖背书。
    counts_pri = {}
    for c in uniq:
        key = str(c.get("priority") or "P2")
        counts_pri[key] = counts_pri.get(key, 0) + 1
    total_n = len(uniq) or 1
    p0_ratio = counts_pri.get("P0", 0) / float(total_n)
    probe_p0 = sum(1 for c in uniq if str(c.get("priority")) == "P0"
                   and (c.get("tier") or _tier_of(c)) == "probe")
    pri_warning = None
    if p0_ratio >= 0.35:
        pri_warning = ("P0 占比 %.0f%%（其中机械探测 %d / %d 条）：wjx 的 P0 是「必测」"
                       "而非「核心业务」，不能用 P0 数量当业务覆盖证据"
                       % (p0_ratio * 100, probe_p0, counts_pri.get("P0", 0)))
    elif len(counts_pri) == 1:
        pri_warning = "优先级只出现 %s 一档，分级失效" % list(counts_pri)[0]
    priority_stats = {"counts": counts_pri, "total": len(uniq),
                      "p0_ratio": round(p0_ratio, 4), "p0_probe": probe_p0,
                      "warning": pri_warning}
    if pri_warning:
        ctx["gen_warnings"].append(pri_warning)

    # 用例身份证：全量用例（含语义/人工）纳入编号体系后，再与契约约束对账。
    # 用派生而非逐点手改：9 个生成器、84 个 _mk 调用点，只有集中派生才不会漏。
    check_key.assign_keys(uniq)
    ck_rec = check_key.reconcile(uniq, contract, warnings=ctx["gen_warnings"])

    if not contract.get("request_fields") and not contract.get("response_fields"):
        sys.stderr.write(
            "警告：契约未解析出任何请求/响应字段（格式不符或空契约？）——本轮用例将以占位为主，"
            "不代表设计覆盖。\n"
            "  请检查：① 契约格式是否为 ShowDoc Markdown / OpenAPI 3.x / Swagger 2.0；\n"
            "  ② 或改用声明式 JSON 契约显式给出 request_fields；详见 references/auto_generation.md。\n")

    # 【审查修复·pick_by_family 纯化】字段族退化告警在生成层统一出声：
    # 生成器（pattern_engine / check_key）只往 ctx["gen_warnings"] 收集，
    # 由这里决定输出——既是它们的输出层，也让告警随产物 gen_warnings 可审计。
    for w in ctx["gen_warnings"]:
        sys.stderr.write("[case_generator] %s\n" % w)

    # 契约哈希的输入必须与 contract_model.load_contract_model 的口径一致：两处
    # 各写一份必然漂移（同一契约在 CLI 与 flow 里算出两个哈希，基线永远对不上）。
    # 失败侧字段只在非空时才进模型——没有失败响应表的老契约哈希逐字节不变。
    _contract_model = {
        "action": contract.get("action"),
        "request_fields": contract.get("request_fields") or [],
        "response_fields": contract.get("response_fields") or [],
    }
    if contract.get("failure_fields"):
        _contract_model["failure_fields"] = list(contract["failure_fields"])

    doc = {
        "action": contract.get("action"),
        "name": contract.get("name"),
        "endpoint": contract.get("endpoint"),
        "method": contract.get("method"),
        "format": contract.get("format"),
        "source": contract.get("source"),
        # 【审查修复·P1-5】回归基线的契约哈希此前无契约内容可算（flow 的
        # doc.get("contract_model") 恒 None，只拿到 action+coverage_plan 的
        # fallback）。此处落规范化契约子集，供 write_regression_artifacts /
        # retest_report 同源取哈希——契约字段任何增删/约束变化都改变哈希，
        # STALE_BASELINE 判定才真正可达。
        "contract_model": _contract_model,
        "op_type": app["op_type"],
        "is_intl": app["is_intl"],
        "business_domains": app["business_domains"],
        "coverage_plan": app["coverage_plan"],
        "optional_plan": app.get("optional_plan", []),
        "uncovered_optional": uncovered_optional,
        "uncovered_optional_detail": uncovered_optional_detail,
        "na_proofs": app["na_proofs"],
        "dimensions": app["dimensions"],
        "notes": app["notes"],
        "matched_patterns": matched,
        "field_suggestions": suggest_for_contract(contract),
        "base_params": base,
        # 解析层结论随产物落盘：契约字段可信度、规则自检可疑项。此前只写 stderr，
        # CLI 落盘产物里查不到——读报告的人无法判断「这份用例基于一份降级解析结果」。
        "parse_warnings": list(contract.get("parse_warnings") or []),
        "self_check": list(contract.get("self_check") or []),
        # --exclude-params 留痕：被排除的参数不参与生成，contract_model 与
        # check_key 分母同样不含它们（对回归基线哈希的影响是有意的——排除
        # 意味着该字段移出测试范围）。
        "excluded_params": excluded_trace,
        "ignored_exclude_params": ignored_excluded,
        # 本轮有效注入键集（有效排除集 ∩ 契约声明面）：执行期据此决定注入哪些
        # 信封键——内部接口契约不声明信封键 → 自动不注入，零开关按接口区分。
        "envelope_keys": envelope_keys,
        "needs_manual_baseline": needs_manual,
        "read_action": ctx["read_action"],
        "list_action": ctx["list_action"],
        "read_params": ctx["read_params"],
        "companion": comp,
        "tier_stats": tier_stats(uniq),
        "duplicate_report": dup_report,
        "expectation_conflicts": conflicts,
        "plan_vs_actual": plan_vs_actual,
        "priority_stats": priority_stats,
        "mutation_skipped": mutation_skipped,
        "payload_skipped": payload_skipped,
        # 【稳定性】这里写的是 check_key 的**稳定投影**：cases_hash 覆盖整份用例
        # 文档，往里面加一个键就会让所有已批准套件要求重新批准（黄金样例
        # test_golden_post_json 就是锁这条的）。reconcile 可以返回更多诊断键，
        # 但落进文档的只保留既有五个、且顺序不变。
        "check_key": _stable_check_key(ck_rec),
        # 【审查修复·pick_by_family 纯化】生成期告警随产物透出（谁被字段族限额砍掉）
        "gen_warnings": ctx["gen_warnings"],
        "llm_verify_report": contract.get("llm_verify_report"),
        "cases": uniq,
    }
    # 【验收硬判据】「事实有没有真的参与生成」不能只看 fact_utilization.consumed：
    # 它把 constraints.max_len / enum 这类「仅交叉校验、不驱动生成」的键也算作
    # 有消费方——模型只交一个长度事实、没有任何用例用它，consumed 依然非空。
    # 这里直接数产物里由语义事实兑付出来的用例：fact_cases 是真正参与执行的，
    # fact_trace_cases 是不执行、只留痕的 unknowns 缺口。
    _report = doc.get("llm_verify_report")
    if isinstance(_report, dict):
        _fact_rows = [c for c in uniq
                      if str(c.get("source") or "").startswith("llm_fact:")]
        _active = [c for c in _fact_rows if c.get("enabled") is not False]
        _report["fact_cases"] = len(_active)
        _report["fact_trace_cases"] = len(_fact_rows) - len(_active)
        _report["fact_case_ids"] = [c.get("id") for c in _fact_rows][:20]
    # 请求形态：**只写非默认值**。纯 POST + JSON + 全 body 的契约不得多出任何键，
    # 否则 cases_hash 变了，所有已批准套件都要重新批准（回归基线同理）。
    if param_locations:
        doc["param_locations"] = param_locations
    _body_kind = str(contract.get("body_kind") or "json").strip().lower()
    if _body_kind != "json":
        doc["body_kind"] = _body_kind
        if contract.get("content_type"):
            doc["content_type"] = str(contract["content_type"])
        if _body_kind == "raw":
            doc["raw_body_field"] = str(contract.get("raw_body_field")
                                        or _guess_raw_field(contract.get("request_fields")) or "")
    if str(contract.get("response_kind") or "json").strip().lower() != "json":
        doc["response_kind"] = str(contract["response_kind"])
    # 只在真有显式排除时写这个键：默认产物多写一个空键也会改 cases_hash，
    # 让所有已批准套件一次性要求重批（见 tests/test_golden_post_json.py）。
    if excluded:
        doc["dim_excluded"] = excluded
    return doc


def _guess_raw_field(fields):
    """raw 体下承载整包报文的参数：唯一一个 **body 位置**的 string 字段；否则留空（不猜）。"""
    names = [str(f.get("name") or "") for f in (fields or [])
             if field_location(f) == "body"
             and str(f.get("type") or "").lower() in ("string", "str", "text")
             and str(f.get("name") or "")]
    return names[0] if len(names) == 1 else ""


def plan_reconcile(coverage_plan, cases):
    """必测维度的「计划 vs 实际」对账。

    分母固定为 coverage_plan（必测声明），不是「已生成的维度」——后者会让
    「某必测维度一条都没生成」这件事在报告里彻底消失。

    只数**可执行**用例：TODO 占位（enabled=False / needs 非空）也带维度字母，
    把它们算进 actual 会让「A 维 4 条全是要人工补的占位」显示成 ok=True、
    gaps=[]——报告说全绿，实际一条能跑的都没有。
    """
    counts = {}
    for case in cases or []:
        if not _is_executable_case(case):
            continue
        key = _normalize_dim(case.get("dimension"))
        counts[key] = counts.get(key, 0) + 1
    planned = [str(d) for d in (coverage_plan or [])]
    rows = [{"dimension": d, "actual": counts.get(d, 0), "ok": counts.get(d, 0) > 0}
            for d in planned]
    return {"planned": planned, "dimensions": rows,
            "gaps": [r["dimension"] for r in rows if not r["ok"]],
            "actual_total": sum(1 for c in (cases or []) if _is_executable_case(c)),
            "total": len(cases or []),
            "placeholder_total": sum(1 for c in (cases or []) if not _is_executable_case(c))}


def _is_executable_case(case):
    """该用例是否**真的会发请求**：占位/停用/带 needs 的都不算。

    三个判据缺一不可：`enabled` 是显式开关；`TODO-` 前缀是各生成器共用的占位
    命名约定；`needs` 非空表示缺外部输入（read_action/read_params/人工补）。
    只认其中一个都会把某类占位漏算成「已覆盖」。
    """
    return (bool(case.get("enabled", True))
            and not str(case.get("id") or "").startswith("TODO-")
            and not case.get("needs"))


def summary(doc):
    by_dim = {}
    for c in doc["cases"]:
        by_dim.setdefault(c.get("dimension") or "-", 0)
        by_dim[c.get("dimension") or "-"] += 1
    return by_dim


def _print_tier(doc):
    """打印分层构成：业务驱动 vs 机械探测。

    机械探测用例照常执行，但报告必须让人看到「业务覆盖」的真实条数——
    否则「132 条用例、维度全绿」会掩盖「业务场景只覆盖了 58 条」这个事实。
    """
    ts = doc.get("tier_stats") or tier_stats(doc.get("cases") or [])
    total = sum(ts.values()) or 1
    biz = ts.get("business", 0)
    probe = ts.get("probe", 0)
    manual = ts.get("manual", 0)
    print("分层：业务驱动 %d（%.0f%%）· 机械探测 %d（%.0f%%）· 待人工 %d"
          % (biz, biz * 100.0 / total, probe, probe * 100.0 / total, manual))
    if probe > biz:
        print("  ⚠️ 机械探测多于业务驱动：用例总数与维度覆盖率会被撑高，"
              "真实业务覆盖以「业务驱动 %d 条」为准" % biz)
    _print_signature(doc)


def _print_signature(doc):
    """打印重复与矛盾：这两个数决定「用例条数」能不能当覆盖证据用。

    重复不折叠时用例照常执行，但同一个请求跑两遍只会得到两条一样的结果，
    条数虚高；同请求一次期望成功一次期望失败则必有一条产出假缺陷。
    """
    rep = doc.get("duplicate_report") or {}
    if rep.get("groups"):
        print("重复：%d 组、多出 %d 条同「维度+参数+期望」的用例%s"
              % (rep["groups"], rep.get("extra", 0),
                 "（已折叠）" if rep.get("collapsed") else "（未折叠，加 --dedup 可折叠）"))
    conflicts = (doc.get("expectation_conflicts") or {}).get("conflicts") or []
    for item in conflicts:
        print("  ❌ 同请求不同期望：%s 期望成功 / %s 期望失败 —— 两者不可能同时成立"
              % ("、".join(item.get("expect_success") or []),
                 "、".join(item.get("expect_failure") or [])))


def main():
    ap = argparse.ArgumentParser(description="契约驱动的声明式用例生成器")
    ap.add_argument("--contract", required=True, help="契约文档路径（ShowDoc md / OpenAPI json）")
    ap.add_argument("--action", help="显式指定 action（契约里取不到时用）")
    ap.add_argument("--read-action", dest="read_action",
                    help="配套读接口 action：给了才能做真往返（读→原样回写→再回读）")
    ap.add_argument("--read-params", dest="read_params",
                    help="读接口所需参数，JSON 字符串（如 '{\"id\": 123}'）")
    ap.add_argument("--list-action", dest="list_action",
                    help="配套列表接口 action：用于「创建后验列表出现 / 计数+1」交叉验证")
    ap.add_argument("--no-auto-companion", dest="no_auto_companion", action="store_true",
                    help="关闭同级目录扫描自动推断配套接口（只用显式声明/命令行指定）")
    ap.add_argument("--llm-facts", dest="llm_facts",
                    help="语义层事实 JSON（scripts/llm_semantics.py 产出，可选）")
    ap.add_argument("--profile", default=None,
                    help="生成档位：conservative（默认）| discovery（放宽探测广度，"
                         "集中放宽超长上限/载荷条数/字段族配额）。也可用 WJX_PROFILE 指定")
    ap.add_argument("--dedup", action="store_true",
                    help="折叠「同维度+同参数+同期望」的重复用例（默认只统计不折叠："
                         "折叠会改变用例总数，历史基线的覆盖率口径会跟着变）")
    ap.add_argument("--exclude-params", dest="exclude_params", action="append",
                    metavar="NAME[=VALUE]",
                    help="排除参数：不围绕它生成任何用例（可重复传）。带值（name=value）"
                         "时该值进每条请求；不带值依次取请求示例值→契约默认值；缺省信封"
                         "参数（encode/appid/action/ts/sign/corpid/sysid）不带值时由引擎"
                         "注入。无值可用且非信封参数时报错拒绝生成")
    ap.add_argument("--exclude-config", dest="exclude_config", default=None,
                    metavar="JSON_PATH",
                    help="排除参数配置文件（{\"exclude_params\": [条目,...]}，条目语法与"
                         "--exclude-params 一致）。四级回退：本参数 > "
                         "exclude_params_<project>.json > exclude_params.json > "
                         "exclude_params.default.json（出厂兜底）；第一个存在的文件即"
                         "基底清单（替换语义，层间不合并），CLI 只追加")
    ap.add_argument("--envelope-keys", dest="envelope_keys", default=None,
                    metavar="K1,K2",
                    help="显式指定信封注入键（逗号分隔），取代自动判定（有效排除集 ∩ "
                         "契约声明面）；必须 ⊆ 有效排除集否则报错")
    ap.add_argument("--no-inject", dest="no_inject", action="store_true",
                    help="本轮不注入任何公共参数（envelope_keys=[]）；与 --envelope-keys 互斥")
    ap.add_argument("--out", help="显式输出用例文件；存在时拒绝覆盖；默认按接口批次归档")
    ap.add_argument("--project", help="资产项目名")
    ap.add_argument("--version", help="可选版本目录")
    ap.add_argument("--print", dest="do_print", action="store_true", help="只打印摘要不落盘")
    args = ap.parse_args()

    # 档位必须在 generate() 之前激活——限额是生成期读取的。
    active = discovery_profile.activate(args.profile or discovery_profile.env_profile())
    if active == "discovery":
        print("发现档：探测广度已放宽（超长上限=%s 载荷/字段/类=%s 字段族配额已放大）；"
              "断言口径与门禁不变。" % (discovery_profile.get("big_cap"),
                                    discovery_profile.get("payload_per_field_per_category")))

    read_params = None
    if args.read_params:
        try:
            read_params = json.loads(args.read_params)
        except ValueError as e:
            ap.error("--read-params 需为合法 JSON：%s" % e)

    if args.envelope_keys and args.no_inject:
        ap.error("--envelope-keys 与 --no-inject 互斥")
    envelope_override = "no_inject" if args.no_inject else (
        [k.strip() for k in args.envelope_keys.split(",") if k.strip()]
        if args.envelope_keys else None)

    try:
        project = common_params.safe_project_name(
            args.project or os.environ.get("WJX_PROJECT_NAME")) \
            if (args.project or os.environ.get("WJX_PROJECT_NAME")) else None
        merged_excludes, excl_src = common_params.resolve_exclude_entries(
            cli_entries=args.exclude_params,
            config_path=args.exclude_config,
            assets_dir=os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets"),
            project=project)
        doc = generate(args.contract, args.action, read_action=args.read_action,
                       list_action=args.list_action, read_params=read_params,
                       auto_companion=not args.no_auto_companion,
                       llm_facts=args.llm_facts, dedup=args.dedup,
                       exclude_entries=merged_excludes, exclude_note=excl_src,
                       envelope_override=envelope_override)
    except ValueError as e:  # --exclude-params/--exclude-config 解析失败：点名拒绝，不产残缺套件
        ap.error(str(e))
    comp = doc.get("companion") or {}
    if args.do_print:
        print("action=%s name=%s op=%s intl=%s" % (doc["action"], doc["name"], doc["op_type"], doc["is_intl"]))
        print("COVERAGE_PLAN(必测，门禁):", doc["coverage_plan"])
        print("optional_plan(建议):", doc["optional_plan"])
        if doc.get("uncovered_optional_detail"):
            print("  ⚠️ 选测维度本轮无用例（不进门禁，仅提示）：")
            for item in doc["uncovered_optional_detail"]:
                print("     %-3s %s" % (item["dimension"], item["reason_text"]))
                if item["reason"] == dim_generators.REASON_NOT_IMPLEMENTED:
                    print("          → 属能力缺口，不是本接口无需测；勿据此判定覆盖完整")
        print("命中模式:", [(m["id"], m["cases"]) for m in doc["matched_patterns"]])
        print("配套接口: read_action=%s list_action=%s（来源=%s 置信=%s）"
              % (doc["read_action"], doc["list_action"], comp.get("source"), comp.get("confidence")))
        cand = comp.get("candidates") or {}
        if (cand.get("read") or cand.get("list")) and comp.get("confidence") == "none":
            print("  扫描到候选但未自动采用：%s"
                  % {k: ["%s(%s)" % (x["action"], x["title"]) for x in v] for k, v in cand.items() if v})
        print("用例数=%d  按维度=" % len(doc["cases"]), summary(doc))
        _print_tier(doc)
        if doc.get("check_key"):
            check_key.report(doc["check_key"])
        return
    if args.out:
        asset_runtime.write_json(args.out, doc)
    else:
        run_dir, _ = asset_runtime.prepare_run_dir(
            project=args.project, interface=asset_runtime.stable_interface_id(doc), version=args.version)
        manifest = asset_runtime.create_manifest(
            run_dir, asset_runtime.stable_interface_id(doc),
            __import__("regression").contract_hash(doc["contract_model"]),
            cases_doc=doc, inputs={"contract": args.contract})
        args.out = os.path.join(run_dir, "generated_cases.json")
        try:
            asset_runtime.write_json(args.out, doc)
            asset_runtime.update_manifest(manifest, "planned", artifacts=[args.out])
        except BaseException:
            asset_runtime.update_manifest(manifest, "failed")
            raise
    print("已生成 %d 条用例 -> %s" % (len(doc["cases"]), args.out))
    print("COVERAGE_PLAN(必测，门禁): %s" % ", ".join(doc["coverage_plan"]))
    print("命中缺陷模式 %d 条：%s" % (len(doc["matched_patterns"]),
                                 ", ".join("%s(%d)" % (m["id"], m["cases"]) for m in doc["matched_patterns"])))
    print("按维度：%s" % summary(doc))
    _print_tier(doc)
    if doc.get("check_key"):
        check_key.report(doc["check_key"])
    if not doc["read_action"]:
        print("提示：无配套读接口（来源=%s），往返验证按「写入后回读一致性」退化执行，"
              "越权矩阵的读面以 TODO 占位点名" % comp.get("source"))
        for k in ("read", "list"):
            cs = (comp.get("candidates") or {}).get(k) or []
            if cs:
                print("  扫描到的 %s 候选（未采用，可用 --%s-action 指定）：%s"
                      % (k, k, ", ".join("%s(%s)" % (x["action"], x["title"]) for x in cs)))
    if not doc["list_action"]:
        print("提示：无配套列表接口，§9.4.2 的「创建后验列表出现 / 计数+1」交叉验证未生成"
              "（可用 --list-action 指定）")


if __name__ == "__main__":
    main()
