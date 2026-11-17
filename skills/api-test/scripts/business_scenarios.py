# -*- coding: utf-8 -*-
"""业务场景补充生成（AF 维度族）：从契约生成业务语义用例。

为什么需要：自动生成走的是「字段 × 变异算子」的机械展开（如 creater=12345、
空值四连），能保证结构覆盖，但产不出"用户实际怎么用"的业务场景。本模块让 LLM
读契约生成业务语义用例，产出与 references/case_schema.md 完全一致的声明式 JSON，
直接喂给 case_runner 执行，不需要任何转换层。

主链不再自造 AF 用例：AF 的参与条件就是**有真实业务场景**——本模块产出经 `--af-cases`
传入才进计划（来源 `business_scenario`），拿不到就在 N/A 证明里写明「没有真实业务场景」
并转不参与。机械拼出的合法请求只能证明字段能发，不能算业务场景覆盖。要强制关 AF 走
`--exclude-dims AF`。

门禁设计：
  底座 = wjx 自带三层门禁（覆盖率 / 浅用例 / 空转，由 case_runner 执行，无需在此重复）
  从 req2testcase 借用四项 + 一项改造：
    1) 数量纪律    生成条数必须等于 expected_case_count，不多不少
    2) 占位符检测  模板短语直接 reject，杜绝空洞用例
    3) 去重率      > 50% 拒绝接受
    4) C7.1 改造   契约实体覆盖（实体来源从"需求文本"换成"契约解析出的字段与枚举"）
  结构化用例专用两项：
    5) 维度合法性  dimension 必须属于 AF1 / AF2 / AF3
    6) 异常断言    expect.success=false 的用例必须给 err_keywords

用法：
  python business_scenarios.py --contract <契约路径> --prep [--out prompt.md]
  python business_scenarios.py --contract <契约路径> --save --agent-output <json> [--out af_cases.json]
  python business_scenarios.py --merge --from af_cases.json --into generated_cases.json
"""
import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from contract_parser import load_contract                 # noqa: E402
from case_generator import _mk, _tier_of, tier_stats      # noqa: E402
import case_signature                                     # noqa: E402
import discovery_profile                                  # noqa: E402

# 条数下限：太少的业务场景没有意义。
AF_MIN_CASES = 3


def af_max_cases():
    """当前生效的 AF 条数上限：环境变量 > 发现档 > 默认。

    业务场景每条都可能真实发请求（写接口会落数据），必须有硬上限；上限数值由
    `discovery_profile` 集中管理（默认档 8、发现档 20），环境变量 `WJX_AF_MAX_CASES`
    可临时覆盖。发现档放大的是"能生成多少条"，写操作仍受 flow 的写操作门禁约束。
    """
    env = os.environ.get("WJX_AF_MAX_CASES")
    if env:
        return int(env)
    return discovery_profile.get("af_max_cases")
AF_DIMS = {
    "AF1": "主业务流：典型用户操作路径，端到端走得通",
    "AF2": "业务规则校验：业务语义上的合法性约束（区别于 B 维度的技术参数校验）",
    "AF3": "业务组合场景：多个字段组合后产生的业务含义",
}

# 借自 req2testcase V4.12.2 占位符检测：出现这些短语说明用例是套模板的空洞产物
PLACEHOLDER_PHRASES = [
    "进入功能页面执行测试点", "验证操作结果", "页面或数据发生相应变化",
    "执行测试点", "正常显示", "功能正常", "操作成功", "符合预期即可",
    "相关功能正常", "页面正常", "数据正确",
]

DEDUP_REJECT_RATE = 0.50      # 重复率预警线（超线只 WARNING，不整批否决）
ENTITY_COVER_WARN_RATE = 0.60  # C7.1 改造后为 WARNING 级，不阻断


def _fields(contract, key):
    return contract.get(key) or []


def business_facts(contract):
    """从契约提取业务事实：字段名、类型、必填、备注、约束、枚举。

    只输出契约里真实存在的内容——不推断、不补全，避免把臆造字段喂给 LLM。
    """
    facts = {
        "name": contract.get("name") or "",
        "action": str(contract.get("action") or ""),
        "endpoint": contract.get("endpoint") or "",
        "request": [],
        "response": [],
        "entities": [],
    }
    for f in _fields(contract, "request_fields"):
        item = {
            "name": f.get("name"),
            "type": f.get("type"),
            "required": bool(f.get("required")),
            "remark": (f.get("remark") or f.get("desc") or "")[:120],
            "constraints": f.get("constraints") or {},
            "semantics": f.get("semantics") or [],
        }
        facts["request"].append(item)
    for f in _fields(contract, "response_fields"):
        facts["response"].append({
            "name": f.get("name"),
            "type": f.get("type"),
            "required": bool(f.get("required")),
            "remark": (f.get("remark") or f.get("desc") or "")[:120],
        })
    # C7.1 改造：业务实体 = 字段名 + 枚举取值，作为语义覆盖的检查基线
    ents = []
    for f in _fields(contract, "request_fields") + _fields(contract, "response_fields"):
        n = f.get("name")
        if n:
            ents.append(str(n))
        for v in (f.get("constraints") or {}).get("enum") or []:
            ents.append("%s=%s" % (n, v))
    facts["entities"] = ents
    return facts


def expected_count(contract):
    """期望条数：按契约复杂度给，受生效档位放大，再受硬上限约束。

    发现档会让业务场景也"多探"——`want` 按复杂度给的基数不变，但整体放大后再与
    放大后的上限取小。只放大上限而不放大基数，实际条数不会变（want 本来就 ≤8）。
    """
    n = len(_fields(contract, "request_fields"))
    if n <= 5:
        want = 4
    elif n <= 15:
        want = 6
    else:
        want = 8
    if discovery_profile.is_discovery():
        want = int(want * 2.5)
    return max(AF_MIN_CASES, min(af_max_cases(), want))


# L5 知识注入的规模约束：prompt 膨胀会稀释契约事实的注意力，限量是硬要求。
L5_MAX_DOCS = 4          # 最多注入的命中文档数
L5_SNIPPET_CHARS = 600   # 每条 snippet 截断长度

# 兄弟 skill 缺失只提醒一次：批量生成时每个接口都报一遍等于刷屏，反而淹没真警告。
_l5_warned = set()


def _warn_once(key, msg):
    if key not in _l5_warned:
        _l5_warned.add(key)
        sys.stderr.write(msg)


def _l5_skill_root():
    """定位兄弟 skill qa-req2testcase-generator 的根目录（tools/knowledge 所在）。

    剥离适配：专家包 skills/api-test 内无该兄弟目录，候选必不存在、返回 None，调用方按 _warn_once 降级处理——属预期行为，不视为故障。

    与 kb_load_contract.default_kb_candidates 同一套回退逻辑：skill 可能经
    「真实目录」或「junction」两种路径被调用，分别基于 abspath 与 realpath
    计算候选，取第一个实际存在的目录。找不到返回 None。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    skill_parent = os.path.dirname(os.path.dirname(here))
    cands = [os.path.join(skill_parent, "qa-req2testcase-generator")]
    real_parent = os.path.dirname(os.path.dirname(os.path.realpath(here)))
    cand2 = os.path.join(real_parent, "qa-req2testcase-generator")
    if os.path.normpath(cand2) != os.path.normpath(cands[0]):
        cands.append(cand2)
    for c in cands:
        if os.path.isdir(os.path.join(c, "tools", "knowledge")):
            return c
    return None


def _l5_knowledge(facts):
    """从 L5 知识库检索与该接口相关的产品知识（页面路径/按钮名/开关名/业务规则）。

    检索失败或知识库不可用时返回空串，prompt 与未接入时完全一致——
    接入是增量不是依赖；失败在 stderr 出声，不静默。
    """
    try:
        skill_dir = _l5_skill_root()
        if not skill_dir:
            _warn_once("no_skill", "警告：未找到 qa-req2testcase-generator 的 knowledge 模块，"
                                   "AF prompt 不含 L5 知识注入（本次会话不再重复提示）\n")
            return ""
        tools_dir = os.path.join(skill_dir, "tools")
        if tools_dir not in sys.path:
            # append 而非 insert(0)：兄弟 skill 的 tools 目录不该遮蔽本 skill
            # 及标准库的同名模块——检索失败可降级，模块被遮蔽是更隐蔽的坏。
            sys.path.append(tools_dir)
        from knowledge import l5_retrieval as L  # noqa: E402

        query = " ".join(x for x in (facts.get("name"), facts.get("action")) if x)
        hits = L.retrieve(skill_dir, query, step="P6", max_docs=L5_MAX_DOCS)
        lines = []
        for h in hits:
            snippet = (h.get("snippet") or "").strip()
            if not snippet:
                continue
            title = h.get("title") or ""
            src = h.get("source") or "kb"
            lines.append("- 【%s】%s\n  %s" % (src, title, snippet[:L5_SNIPPET_CHARS]))
        if not lines:
            return ""
        return "\n".join(lines)
    except Exception as e:  # 检索任何异常都不阻断 AF 主流程
        _warn_once("retrieve_fail", "警告：L5 业务知识检索失败，AF prompt 将不含知识注入"
                                    "（本次会话不再重复提示）：%s\n" % e)
        return ""


# 业务流程注入：按 action 反查本接口在已审批流程里的位置。
# 与 L5 注入的分工——L5 是文档片段（可能过时），flow 带 approval + evidence 且
# 经 validate_flow 校验，可信度更高；两者都只是参考素材，不改变契约事实。
_FLOW_REF_RE = re.compile(r"^\$\{([A-Za-z][A-Za-z0-9_-]*)\.([A-Za-z][A-Za-z0-9_.-]*)\}$")
_NUMERIC_ACTION_RE = re.compile(r"^\d+$")


def _flow_files():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return sorted(glob.glob(os.path.join(root, "assets", "business_flow.*.json")))


def _upstream(by_id, step, seen=None):
    """顺着 requires / preconditions 里的 ${step.field} 反向展开上游链。

    必须反向展开：测 update 时 action 只能查到 update 这一条，真正要紧的
    “它前面要先 create / 先 pause”全藏在 requires 与 preconditions 里。
    """
    seen = set() if seen is None else seen
    out = []
    refs = list(step.get("requires") or [])
    refs += [p for p in (step.get("preconditions") or []) if isinstance(p, str)]
    for ref in refs:
        m = _FLOW_REF_RE.match(str(ref))
        if not m:
            continue
        sid = m.group(1)
        if sid in seen or sid not in by_id:
            continue
        seen.add(sid)
        up = by_id[sid]
        out.append({"step": sid, "operation": up.get("operation"),
                    "action": str(up.get("action") or "")})
        out.extend(_upstream(by_id, up, seen))
    return out


def flow_context(contract):
    """按 action 从业务流程知识里提取步骤、状态约束与上游链（只读，不发请求）。

    action 必须是纯数字：流程里存在 `<delete-response-action-not-found>` 这类
    未确定占位符，按它反查会把占位符当成真实接口。
    同一 action 可能对应多个步骤（1000102 同时是 publish / disable / delete），
    因此这里全部返回并标注 operation，由模型按语义区分，不做猜测性收敛。
    """
    action = str(contract.get("action") or "")
    if not _NUMERIC_ACTION_RE.match(action):
        return []
    found = []
    for path in _flow_files():
        if "example" in os.path.basename(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                flow = json.load(f)
        except Exception:
            continue
        steps = [s for s in (flow.get("steps") or []) if isinstance(s, dict)]
        by_id = {s.get("id"): s for s in steps}
        for step in steps:
            if str(step.get("action") or "") != action:
                continue
            found.append({
                "flow": flow.get("name") or flow.get("flow_id") or os.path.basename(path),
                "step": step.get("id"),
                "operation": step.get("operation"),
                "constraints": [str(p) for p in (step.get("preconditions") or [])],
                "upstream": _upstream(by_id, step),
            })
    return found


def _flow_section(entries):
    """渲染 prompt 片段。刻意不给裸表达式任何“可以当字段名”的暗示。"""
    if not entries:
        return ""
    lines = []
    for e in entries:
        lines.append("- 流程「" + str(e["flow"]) + "」中的步骤 `" + str(e["step"])
                     + "`（operation=" + str(e["operation"]) + "）")
        if e["constraints"]:
            lines.append("    状态约束：" + "；".join(e["constraints"]))
        if e["upstream"]:
            lines.append("    上游步骤：" + "、".join(
                str(u["step"]) + "(" + str(u["operation"]) + ")" for u in e["upstream"]))
    return (
        "\n## 业务流程位置（来自已审批的流程知识，仅作理解背景）\n"
        "本接口出现在下列业务流程中。这些信息用来说明“在什么状态下会发生什么”，"
        "**不是**新的请求字段。\n"
        "注意：`resource.state != published` 这类是流程执行器的**约束表达式**，"
        "既不是字段名也不是字段值，**禁止**出现在 params 的键或值里。\n"
        + "\n".join(lines) + "\n"
        "\n### 由此产生的硬性边界\n"
        "- 依赖“上游已构造状态”才能成立的场景，**不要**生成本接口的 `success=false` 用例。\n"
        "  本接口无法自证该状态：实际执行时状态不成立，接口会正常成功，"
        "期望拒绝却成功 → 被判成 P0 缺陷。\n"
        "- 这类场景写进对应用例的 note（写明需要什么前置状态），"
        "用例本身只断言当前可独立验证的口径。\n")


def build_prompt(contract):
    """生成给 LLM 的 prompt：契约事实 + 流程位置 + L5 产品知识 + 输出格式 + 边界要求。"""
    facts = business_facts(contract)
    want = expected_count(contract)
    dim_desc = "\n".join("  - %s %s" % (k, v) for k, v in AF_DIMS.items())
    req = json.dumps(facts["request"], ensure_ascii=False, indent=2)
    resp = json.dumps(facts["response"], ensure_ascii=False, indent=2)

    kb_block = _l5_knowledge(facts)
    kb_section = ""
    if kb_block:
        kb_section = (
            "\n## L5 产品知识参考（检索自知识库）\n"
            "用途：对齐真实产品行为——页面路径、按钮名、开关名、已上线功能的口径。\n"
            "**与契约冲突时一律以契约为准；这里没有提到的知识不要臆造，"
            "参数只能用上面契约字段。**\n%s\n" % kb_block
        )

    # flow_section 是作为 .format 的**参数值**插入的，format 不会对参数值二次解析，
    # 所以 ${step.field} 里的花括号原样保留——不要预转义（转义后会变成 {{...}}）。
    flow_section = _flow_section(flow_context(contract))

    return """你是接口测试设计专家。请基于下面的接口契约，设计业务场景用例。

## 接口
- 名称：{name}
- action：{action}
- 地址：{endpoint}

## 请求字段（契约原文，只使用这些字段，禁止臆造）
{req}

## 响应字段
{resp}
{flow_section}{kb_section}
## 你要产出的东西
业务场景用例——即"用户实际会怎么用这个接口"的场景。

## 边界（重要，不要越界）
- **不要**做技术参数校验：类型错误、长度越界、枚举越界、必填缺失、空值这些由 B 维度自动生成覆盖，你不要重复。
- **不要**臆造字段：只能使用上面列出的字段名与取值。
- **不要**写空洞描述："验证操作结果""功能正常"这类会被直接拒绝。

## 维度（必须三选一填到 dimension 字段）
{dim_desc}

## 输出格式（JSON 数组，严格 {want} 条，不多不少）
[
  {{
    "id": "AF1-01",
    "name": "业务场景名（现象口吻，如：创建可发布的活动主页后能拿到访问链接）",
    "dimension": "AF1",
    "category": "业务场景-主业务流",
    "params": {{"字段名": "符合业务语义的取值"}},
    "expect": {{
      "success": true,
      "contract_keys": ["响应中必须出现的字段名"],
      "err_keywords": []
    }},
    "priority": "P0",
    "note": "这个场景对应的业务意图"
  }}
]

## 硬性要求
- AF1 / AF2 / AF3 三族**各至少 1 条**：主流程、业务规则、字段组合是三个不同的风险面，只写主流程等于没测业务规则。某族确实不成立时，在 note 里写明理由。
- 期望被拒绝的场景（success=false）**必须**在 err_keywords 里给出期望命中的错误关键词，否则视为无效用例。
- contract_keys 只填上面响应字段里真实存在的字段名。
- 直接输出 JSON 数组，不要 markdown 代码块，不要解释。
""".format(name=facts["name"], action=facts["action"], endpoint=facts["endpoint"],
           req=req, resp=resp, dim_desc=dim_desc, want=want, kb_section=kb_section,
           flow_section=flow_section)


def validate(cases, contract, want):
    """AF 门禁：数量纪律 / 占位符 / 重复率 / 维度合法性 / 异常断言 / 契约实体覆盖。"""
    issues = []
    warns = []

    if not isinstance(cases, list) or not cases:
        return {"passed": False, "issues": [{"check": "format", "msg": "用例为空或不是数组"}],
                "warns": [], "stats": {}}

    # 1) 数量纪律
    if len(cases) != want:
        issues.append({"check": "count", "msg": "生成 %d 条，期望 %d 条（不多不少）" % (len(cases), want)})

    # 2) 条数上限（即使 want 被改小，也不允许超上限）
    if len(cases) > af_max_cases():
        issues.append({"check": "cap", "msg": "超过条数上限 %d（可用 WJX_AF_MAX_CASES 或 --profile discovery 调整）"
                       % af_max_cases()})

    seen_ids = set()
    for i, c in enumerate(cases):
        cid = str(c.get("id") or ("#%d" % i))

        # 3) 维度合法性
        dim = str(c.get("dimension") or "")
        if dim not in AF_DIMS:
            issues.append({"check": "dimension", "case": cid,
                           "msg": "dimension=%r 不合法，必须是 AF1/AF2/AF3" % dim})

        # 4) 占位符检测
        blob = json.dumps(c, ensure_ascii=False)
        hit = [p for p in PLACEHOLDER_PHRASES if p in blob]
        if hit:
            issues.append({"check": "placeholder", "case": cid,
                           "msg": "命中空洞短语 %s，该用例被拒绝" % "、".join(hit[:3])})

        # 5) 异常用例必须给 err_keywords
        exp = c.get("expect") or {}
        if exp.get("success") is False and not (exp.get("err_keywords") or []):
            issues.append({"check": "err_keywords", "case": cid,
                           "msg": "应被拒绝的用例未给 err_keywords，只验 result=false 会放行语义不准"})

        # 6) 契约字段不得臆造
        known = {f.get("name") for f in _fields(contract, "request_fields")}
        for k in (c.get("params") or {}).keys():
            if known and k not in known and not k.startswith("_"):
                warns.append({"check": "unknown_field", "case": cid,
                              "msg": "参数 %s 不在契约请求字段中，疑似臆造" % k})

        if cid in seen_ids:
            issues.append({"check": "dup_id", "case": cid, "msg": "用例 id 重复"})
        seen_ids.add(cid)

    # 7) 重复率：按「维度+参数+期望」签名判等，不打分。
    # 相似度两个方向都会错——实测真实 133 条上，同族不同字段判重率 93~95%（误判），
    # 而 20 组真重复一组都没判出（0.68~0.75 未达 0.80 线）。见 case_signature 模块头。
    # 只预警不否决：整批拒绝会让「少量真重复」的批次因误判被整体退回。
    dup = case_signature.duplicate_stats(cases)["extra"]
    dedup_rate = dup / float(len(cases)) if cases else 0.0
    if dedup_rate > DEDUP_REJECT_RATE:
        warns.append({"check": "dedup",
                      "msg": "重复率 %.0f%% 超过 %.0f%%：%d 条与更早用例同维度+同参数+同期望"
                             % (dedup_rate * 100, DEDUP_REJECT_RATE * 100, dup)})

    # 8) 族内覆盖留痕：只记账，不阻断。
    # 为什么不强制每族至少一条：BLOCK 会逼模型为过门禁编造场景，编出来的用例
    # 通过校验后变成「已验证覆盖」——比静默缺失更坏，因为缺失还看得见。
    # 缺口在这里落账，判定交给覆盖率门禁（--expect-categories AF1,AF2,AF3）。
    family_counts = {k: 0 for k in AF_DIMS}
    for c in cases:
        d = str(c.get("dimension") or "")
        if d in family_counts:
            family_counts[d] += 1
    family_gaps = [k for k, v in family_counts.items() if v == 0]
    if family_gaps:
        warns.append({"check": "family_gap",
                      "msg": "族内覆盖缺口：%s 一条用例都没有（只留痕不阻断；"
                             "要按族对账请传 --expect-categories AF1,AF2,AF3）"
                             % "、".join(family_gaps)})

    # 9) C7.1 改造：契约实体覆盖（WARNING，不阻断）
    ents = business_facts(contract)["entities"]
    blob_all = " ".join(json.dumps(c, ensure_ascii=False) for c in cases)
    covered = [e for e in ents if str(e).lower() in blob_all.lower()]
    cover_rate = (len(covered) / float(len(ents))) if ents else 1.0
    if cover_rate < ENTITY_COVER_WARN_RATE:
        warns.append({"check": "entity_coverage",
                      "msg": "契约实体覆盖率 %.0f%%（%d/%d），低于 %.0f%%；未覆盖：%s"
                             % (cover_rate * 100, len(covered), len(ents),
                                ENTITY_COVER_WARN_RATE * 100,
                                "、".join([e for e in ents if e not in covered][:8]))})

    return {
        "passed": not issues,
        "issues": issues,
        "warns": warns,
        "stats": {
            "count": len(cases),
            "expected": want,
            "dedup_rate": round(dedup_rate, 3),
            "entity_coverage": round(cover_rate, 3),
            "entities_total": len(ents),
            "family_counts": family_counts,
            "family_gaps": family_gaps,
        },
    }


def normalize(cases, contract):
    """用 case_generator._mk 统一成型，保证与自动生成用例字段完全一致。"""
    action = str(contract.get("action") or "")
    out = []
    for c in cases:
        out.append(_mk(
            str(c.get("id")), c.get("name") or "", str(c.get("dimension") or "AF1"),
            c.get("category") or "业务场景", c.get("params") or {}, c.get("expect") or {},
            action=action, applicability="适用",
            priority=c.get("priority") or "P1",
            source="business_scenario",
            enabled=True, needs="", note=c.get("note") or "",
            # AF 用例本身就是业务场景，必须显式带 tier——合并进主用例文件后若不重算
            # tier_stats，这批次会在分层统计里变成 None 而被漏计。
            tier="business",
        ))
    return out


def main():
    ap = argparse.ArgumentParser(description="业务场景补充生成（AF 维度族）")
    ap.add_argument("--contract")
    ap.add_argument("--action", default=None)
    ap.add_argument("--prep", action="store_true", help="生成给 LLM 的 prompt")
    ap.add_argument("--save", action="store_true", help="校验并落盘 LLM 产出")
    ap.add_argument("--agent-output", default=None, help="LLM 产出的 JSON 文件")
    ap.add_argument("--out", default=None)
    ap.add_argument("--merge", action="store_true", help="把 AF 用例合并进主用例文件")
    ap.add_argument("--from", dest="src", default=None)
    ap.add_argument("--into", dest="dst", default=None)
    ap.add_argument("--profile", default=None,
                    help="生成档位：conservative（默认）| discovery（放大 AF 条数上限）。"
                         "也可用 WJX_PROFILE 指定")
    args = ap.parse_args()

    # 档位必须在 expected_count() 之前激活——条数上限是生成期读取的。
    discovery_profile.activate(args.profile or discovery_profile.env_profile())

    if args.merge:
        if not (args.src and args.dst):
            print("ERROR: --merge 需要同时给 --from 和 --into")
            return 2
        with open(args.src, "r", encoding="utf-8") as f:
            af = json.load(f)
        with open(args.dst, "r", encoding="utf-8") as f:
            doc = json.load(f)
        af_cases = af.get("cases") if isinstance(af, dict) else af
        doc.setdefault("cases", [])
        existing = {c.get("id") for c in doc["cases"]}
        added = [c for c in af_cases if c.get("id") not in existing]
        # src 可能是改造前生成的旧文件（用例无 tier 字段）。合并时统一补齐，
        # 否则产物里同一批用例有的带 tier 有的不带，下游按字段取会取到 None。
        for c in added:
            if not c.get("tier"):
                c["tier"] = _tier_of(c)
        doc["cases"].extend(added)
        # AF 一旦有真实场景就参与：三处状态必须同时改，只加分母会留下
        # 「计划里要测、N/A 证明里又说不测」的自相矛盾产物。
        #   ① coverage_plan 加 AF（门禁分母）
        #   ② na_proofs 去掉 AF（「没有真实业务场景」的前提已不成立）
        #   ③ dimensions 标必测（报告展示口径）
        if "AF" not in doc.get("coverage_plan", []):
            doc.setdefault("coverage_plan", []).append("AF")
        if doc.get("na_proofs"):
            doc["na_proofs"] = [p for p in doc["na_proofs"] if p.get("dim") != "AF"]
        doc.setdefault("dimensions", {})["AF"] = "必测"
        # 合并后必须重算分层统计：否则 tier_stats 仍是合并前的旧值，
        # 新并入的业务用例不会体现在「业务驱动」条数里（串链字段契约）。
        doc["tier_stats"] = tier_stats(doc["cases"])
        # 族内缺口随合并带过去：af_cases.json 的顶层字段在合并后就丢了，
        # 下游（覆盖率/报告）只看得到主用例文件。
        if isinstance(af, dict) and af.get("family_gaps"):
            doc["af_family_gaps"] = af["family_gaps"]
        with open(args.dst, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        print("合并完成：新增 %d 条 AF 用例 -> %s（coverage_plan 已加入 AF）" % (len(added), args.dst))
        return 0

    if not args.contract:
        print("ERROR: 需要 --contract")
        return 2
    contract = load_contract(args.contract, action=args.action)
    want = expected_count(contract)

    if args.prep:
        prompt = build_prompt(contract)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(prompt)
            print("prompt 已写入 %s（期望 %d 条，上限 %d）" % (args.out, want, af_max_cases()))
        else:
            print(prompt)
        return 0

    if args.save:
        if not args.agent_output:
            print("ERROR: --save 需要 --agent-output")
            return 2
        with open(args.agent_output, "r", encoding="utf-8") as f:
            raw = f.read()
        try:
            cases = json.loads(raw)
        except Exception as e:
            print("ERROR: agent-output 不是合法 JSON：%s" % e)
            return 2
        result = validate(cases, contract, want)
        print("\n=== AF 门禁 ===")
        print("统计：%s" % json.dumps(result["stats"], ensure_ascii=False))
        if result["issues"]:
            print("❌ BLOCK 级问题 %d 项：" % len(result["issues"]))
            for it in result["issues"][:20]:
                print("  - [%s] %s %s" % (it.get("check"), it.get("case", ""), it.get("msg")))
        else:
            print("✅ BLOCK 级全部通过")
        if result["warns"]:
            print("⚠️ WARNING %d 项：" % len(result["warns"]))
            for it in result["warns"][:20]:
                print("  - [%s] %s %s" % (it.get("check"), it.get("case", ""), it.get("msg")))
        if not result["passed"]:
            print("\n未通过门禁，不落盘。请修复后重试。")
            return 1
        doc = {
            "action": str(contract.get("action") or ""),
            "name": contract.get("name") or "",
            "dimension_family": "AF",
            "expected_case_count": want,
            "gate": result["stats"],
            # 族内缺口提到顶层：合并进主用例文件后 gate 会被覆盖/忽略，
            # 缺口必须留在能被人一眼看到的位置。
            "family_gaps": result["stats"].get("family_gaps") or [],
            # 流程约束落盘留痕：进 prompt 的是"背景"，这里留的是"依据"。
            # 需要上游构造状态的那些场景不会变成可执行用例（见 prompt 硬边界），
            # 但必须有人看得见它们存在，否则等于静默丢弃。
            "flow_constraints": flow_context(contract),
            "cases": normalize(cases, contract),
        }
        out = args.out or "af_cases.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        print("\n已落盘 %d 条 AF 用例 -> %s" % (len(doc["cases"]), out))
        return 0

    print(build_prompt(contract))
    return 0


if __name__ == "__main__":
    sys.exit(main())
