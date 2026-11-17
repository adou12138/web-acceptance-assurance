# -*- coding: utf-8 -*-
"""统一异常归因，先排除环境和测试系统因素再判业务缺陷。

**主链路（case_runner）与冒烟链路（smoke_runner）共用本模块**，避免两处各写一套
判定、结论互相打架。早期版本只有冒烟链路调用它，主链路的失败一律直接进缺陷台账，
于是"前置条件未满足"这类假缺陷无从拦截——本模块因此补上 `PRECONDITION_UNMET`。

判定顺序（自上而下，先到先得）：前置条件未满足 → 环境/网络 → 限流 → 最终一致性
→ 契约证据不足 → 缺陷。顺序不是随意排的：越靠前的类别越"不该报成缺陷"，
把前置判定放在最前面，才能保证它优先于"断言被违反 → DEFECT"这条兜底。
"""

# 前置条件未满足：本接口没问题，只是要先调另一个接口/先把对象置于某状态。
# 与「缺陷」的根本区别是它**有外部依据**——来自契约声明或一次真实执行留下的
# 已确认依赖记录，而不是从错误文案里猜出来的。
PRECONDITION_UNMET = "PRECONDITION_UNMET"


def _text_of(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        import json
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def match_precondition(response, confirmed_deps, action=None):
    """本次失败是否命中一条**已确认**的运行实证依赖，命中则返回该依赖。

    只接受 `status=confirmed` 的依赖（调用方负责过滤，此处再兜一层）。判据顺序：
      1. `evidence` 是本次响应原文的连续子串——最硬，因为它本来就摘自同类响应；
      2. 退一步按 target_action 号在响应里出现来匹配（响应文案可能改写过）。
    两条都不中即返回 None，不做任何相似性推断。

    为什么必须挂"已确认"这道闸：这个判定会让一条失败**从缺陷台账里消失**。
    判错的代价是一个真缺陷被静默吞掉，且外观上完全合理。所以候选依赖不参与判定。

    【W39 防吞】两条加固：
      a) evidence 太短（<6 字符）不作为子串判据——「请重试」这类通用短文案会被
         任何失败响应包含，等于无条件吞掉全部失败；
      b) 匹配结果带 `match_reason`，调用方（api_test_runner 的 PRECONDITION 分支）
         据此在 observation 里点名命中的依赖与判据，翻案时有迹可循。
    """
    if not response or not confirmed_deps:
        return None
    text = _text_of(response)
    if not text:
        return None
    subject = str(action or "").strip()
    for dep in confirmed_deps:
        if dep.get("status") != "confirmed":
            continue
        dep_subject = str(dep.get("subject_action") or "").strip()
        # 依赖声明了主体就必须对得上，避免一个接口的依赖吞掉别的接口的失败
        if dep_subject and subject and dep_subject != subject:
            continue
        ev = str(dep.get("evidence") or "").strip()
        # 【W39a】短 evidence 不做子串判定（通用短文案命中一切失败=吞掉一切）
        if ev and len(ev) >= 6 and ev in text:
            out = dict(dep)
            out["match_reason"] = "evidence-substring:%s" % ev[:40]
            return out
        target = str(dep.get("target_action") or "").strip()
        # 只在服务端**错误文案**里找 action 号。旧实现拿整份响应（含 data）做子串，
        # 而接口回显 action、data 里带同号 id 都很常见——命中一次就把真缺陷判成
        # 「前置未满足」，从缺陷台账里静默消失。
        msg = ""
        if isinstance(response, dict):
            msg = str(response.get("errormsg") or response.get("message") or "")
        if target and msg and target in msg:
            out = dict(dep)
            out["match_reason"] = "target-action-in-errormsg:%s" % target
            return out
    return None


def _msg_text(response, error=None):
    """只取服务端**错误文案**（errormsg/message/msg）与网络异常文本。

    环境/限流/最终一致性三类判据此前扫的是「整份响应」（`_text_of(response)` 把
    `data` 也 json.dumps 进去）。概率很高的数据类型 verdict 误判由此而来：
      · `not found`——任意错误文案里都可能出现，判成 ENV；
      · `sync`——命中字段名 `sync_time`/`is_sync`；
      · `稍后`——命中「请稍后重试」之外的正常业务文案。
    与设计的选择同源见 ``match_precondition`` 的 target-action 匹配：那里早已只扫
    errormsg/message，环境类判据本应同等收紧。

    注意边界：**字符串响应本身就是错误文案**（调用方常直接传 ``response="服务繁忙"``
    这类纯文本），必须原样参与匹配，否则会把「明显是限流」的失败判成 DEFECT。
    真正需要剔除的只有 dict 响应里的 ``data`` 载荷——嵌套的业务数据在整体
    json.dumps 之后才是误判来源。
    """
    parts = [str(error or "")]
    if isinstance(response, str):
        parts.append(response)
    elif isinstance(response, dict):
        for key in ("errormsg", "message", "msg", "error"):
            val = response.get(key)
            if isinstance(val, str) and val.strip():
                parts.append(val)
    return " ".join(p for p in parts if p)


def attribute(error=None, response=None, assertion_ok=True, contract_confidence=1.0,
              retries=0, action=None, confirmed_deps=None, case=None):
    """归因。返回 category / confidence / reason / next_action。

    `confirmed_deps` 传入**已确认**的运行实证依赖（来自 precondition_scan），
    不传时行为与接入前完全一致——增量接入，不影响任何既有链路。
    """
    text = str(error or "") + " " + _text_of(response)
    lower = text.lower()

    # 1) 前置条件未满足：优先级最高，见模块说明。
    dep = match_precondition(response, confirmed_deps or [], action=action)
    if dep is not None:
        target = dep.get("target_action") or "（未定位）"
        cond = dep.get("condition") or "已确认的前置条件"
        params = dep.get("params") or {}
        ptxt = "、".join("%s=%s" % (k, v) for k, v in params.items())
        return {
            "category": PRECONDITION_UNMET,
            "confidence": float(dep.get("confidence", 0.8) or 0.8),
            "reason": "前置条件未满足：需先调用接口 %s%s（%s）"
                      % (target, ("，参数 " + ptxt) if ptxt else "", cond),
            "next_action": "先满足前置条件再重跑；该失败不计为缺陷",
            "evidence_action": target,
            # 【W39b】命中判据随归因结果透出，调用方在 observation 里点名（可翻案）
            "matched": {"target_action": dep.get("target_action", ""),
                        "match_reason": dep.get("match_reason", "")},
        }

    if error and retries:
        return {"category": "ENV", "confidence": 0.95,
                "reason": "network error remained after retry", "next_action": "check environment"}
    # 接口地址/网关类提示：配置里的 base_url 指错入口时，服务端回的是「不支持此操作，
    # 请使用文档指定接口地址」这类文案，与业务缺陷长得完全不像。不拦就会进缺陷台账，
    # 开发排查一圈才发现是测试环境配置问题（实测真实契约就是这么翻的）。
    # 环境/限流/最终一致性的关键词只在**错误文案**里匹配。用整份响应（含 data）
    # 会让 `not found` / `sync` / `稍后` 这些高频片段触发误判，把真缺陷判成
    # 「环境问题」并从缺陷台账里静默移除——见 _msg_text 的说明。
    msg_lower = _msg_text(response, error).lower()

    if any(word in msg_lower for word in ("接口地址不支持", "文档指定接口地址", "无效的接口地址",
                                          "接口地址错误", "method not allowed", "not found")):
        return {"category": "ENV", "confidence": 0.9,
                "reason": "endpoint/gateway rejects the request path",
                "next_action": "核对 base_url 是否为契约「接口地址」，或该 action 是否走另一个入口"}
    # 词表与 case_runner 的 RATE_LIMIT_KEYWORDS 保持一致（两边曾各写一份，「频率」
    # 「过于频繁」「throttl」只在一侧有——「请求过于频繁，请稍后重试」因此被判成
    # 下一类的 CONSISTENCY_PENDING，归因结论与台账口径打架）。
    if any(word in msg_lower for word in ("rate limit", "too many", "throttl", "限流",
                                          "频率", "过于频繁", "请求过快", "请求太频繁", "防重")):
        return {"category": "RATE_LIMIT", "confidence": 0.92,
                "reason": "rate-limit or duplicate-submit signal", "next_action": "re-run with throttle"}
    if any(word in msg_lower for word in ("sync", "eventually", "同步", "稍后")):
        return {"category": "CONSISTENCY_PENDING", "confidence": 0.7,
                "reason": "possible eventual consistency window", "next_action": "poll independent read"}
    if not assertion_ok and contract_confidence < 0.8:
        # 待确认必须写清「确认什么才能解除」：只写 confirm contract，人拿到手也无法行动。
        subject = "契约声明"
        if isinstance(case, dict) and case:
            dim = str(case.get("dimension") or "")
            cid = str(case.get("id") or "")
            subject = "%s 维度 %s 的契约约束" % (dim or "?", cid or "?")
        return {"category": "NEEDS_CONFIRMATION", "confidence": 0.55,
                "reason": "low-confidence contract evidence", "next_action": "confirm contract",
                "subject": subject,
                "resolution_condition": (
                    "确认 %s 是否在契约原文中声明（类型/枚举/长度/默认值/必填性之一）；"
                    "确认后把断言依据改为契约原文并重跑本用例" % subject)}
    return {"category": "DEFECT", "confidence": contract_confidence,
            "reason": "assertion violated trusted contract", "next_action": "create finding"}


def detect_flaky(results):
    """稳定性检测（v4.29.0）：同一用例在结果集里出现多次且终态不一致 → 记"不稳定"。

    sound 边界：只在**同一用例确有多条记录**时才判；单次结果无从比较、不判。
    不重新执行（无重跑基础设施），只分析已有结果集——回归/重跑把多轮结果并进来时自动生效。
    返回 {case_id: [出现的不同终态]}，空 dict 表示无不稳定项。

    【W41 生效边界（如实声明）】本函数在**单轮执行**里形同虚设：run() 每个用例
    只产出一条记录，`len(uniq)==1` 恒不触发。生效条件是**多轮结果合并后分析**：
    ① 手动把多轮 results CSV 合并后传入；② 调用方对 ERROR/超时用例做了重跑并把
    第二条记录并入。请勿把"本轮无 flaky"解读为"用例稳定"——没测过就不算稳定。
    """
    by_case = {}
    for r in results or []:
        if not isinstance(r, dict):
            continue
        cid = r.get("case_no") or r.get("id") or r.get("cid")
        st = r.get("status")
        if cid and st:
            by_case.setdefault(str(cid), []).append(str(st))
    flaky = {}
    for cid, sts in by_case.items():
        uniq = sorted(set(sts))
        if len(uniq) > 1:
            flaky[cid] = uniq
    return flaky
