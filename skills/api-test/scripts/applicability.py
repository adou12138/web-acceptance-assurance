# -*- coding: utf-8 -*-
"""接口适用性自动判定：从契约推导「哪些维度适用」，并自动生成 COVERAGE_PLAN 与 N/A 证明。

解决的核心问题：coverage_gate（反静默缺漏门禁）依赖人手声明 COVERAGE_PLAN，
不声明就等于门禁失效、"做少了"照样静默产出完整报告。本模块把 applicability_matrix.md 的
判断逻辑代码化，让「契约 → 适用维度 → COVERAGE_PLAN」自动完成，门禁即可默认生效。

产出：
  {
    "op_type":"创建", "business_domain":"资源管理", "is_intl":false,
    "dimensions": {"A":"必测", "AA":"不适用", ...},
    "coverage_plan": ["A","B","C",...],      # 必测 + 选测，直接喂给 coverage_gate
    "na_proofs": [{"dim":"AA","reason":"..."}],  # N/A 自动证明（第 1.5 步要求列出证明）
    "notes": [...]
  }
维度编号对照见 framework.md §1：A 鉴权权限 / B 参数校验 / C 字段语义真值 / D 业务主链路 /
E 状态机 / F 组合交叉 / G 安全深化 / H 错误处理 / I 编码国际化 / J 性能稳定 / K 契约测试 /
L 兼容性 / M–Z 业务域专项 / AA 分页 / AE 认证传输补充 / AB 前端口径 / AC 幂等唯一 /
AD 事件回调 / AF 业务场景（默认参与，条件是拿到真实业务场景；拿不到就注明原因不参与）。
"""
import os
import re

ALL_DIMS = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L",
            "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z",
            "AA", "AE", "AB", "AC", "AD", "AF"]

# 操作类型识别（顺序敏感：更具体的先匹配）
OP_RULES = [
    ("导入", ["导入", "import", "批量导入"]),
    ("导出", ["导出", "下载", "export", "download"]),
    ("删除", ["删除", "移除", "清空", "delete", "remove"]),
    ("修改", ["更新", "修改", "编辑", "设置", "保存", "update", "edit", "modify", "save"]),
    ("创建", ["创建", "新增", "添加", "新建", "复制", "create", "add", "new"]),
    ("查询", ["查询", "获取", "详情", "列表", "读取", "搜索", "list", "get", "query", "detail", "search"]),
]

# 业务域专项识别（命中即把该维度提升为选测）。
# 注意：只按「接口名 + 字段名」匹配，不扫描述/备注——描述里出现"title标签""短链接编号"
# 会把 N(层级) / P(分享) 误命中，进而把无关维度塞进计划、让门禁产生假缺口。
DOMAIN_RULES = [
    ("M", ["导出", "下载", "excel", "download", "export"]),
    ("N", ["层级", "部门", "通讯录", "文件夹", "树形", "父级", "parent"]),
    ("O", ["发送", "通知", "提醒", "推送", "短信", "邮件", "notify"]),
    ("P", ["分享", "share"]),
    ("Q", ["评价", "评分", "关系", "evaluat"]),
    ("R", ["报告", "report"]),
    ("S", ["分类", "容器", "分组", "category"]),
    ("T", ["账号", "成员", "用户", "身份", "member", "user"]),
    ("U", ["导入", "import"]),
    ("W", ["附件", "文件", "上传", "图片", "attachment", "file"]),
    ("X", ["批量", "batch"]),
    ("Y", ["详情", "明细", "detail"]),
]

# 操作类型 -> (必测, 选测)。未列出的维度一律判「不适用」并给出自动证明。
OP_MATRIX = {
    "创建": (
        ["A", "B", "C", "D", "H", "K", "Y", "AB", "AC", "AE"],
        ["E", "F", "G", "I", "J", "L", "V", "W", "Z", "AD", "N", "Q"],
    ),
    "修改": (
        ["A", "B", "C", "D", "E", "H", "K", "Y", "AB", "AC", "AE"],
        ["F", "G", "I", "J", "L", "V", "W", "Z", "AD", "N", "Q"],
    ),
    "删除": (
        ["A", "B", "D", "E", "H", "K", "AB", "AC", "AE"],
        ["C", "F", "G", "I", "J", "V", "AD", "N"],
    ),
    "查询": (
        ["A", "B", "C", "D", "H", "K", "Y", "AB", "AE"],
        ["F", "G", "I", "J", "L", "V", "W", "Z", "AA", "N", "T"],
    ),
    "导出": (
        ["A", "B", "C", "D", "H", "K", "M", "AB", "AE"],
        ["AA", "F", "G", "I", "J", "V", "W", "Z"],
    ),
    "导入": (
        ["A", "B", "C", "D", "H", "K", "U", "AB", "AC", "AE"],
        ["F", "G", "I", "J", "V", "W", "X", "Z", "AD"],
    ),
}

# N/A 自动证明：不适用维度的理由（按维度给默认理由，再由操作类型细化）
NA_REASON = {
    "AA": "非列表/分页接口，无页码与条数边界可测",
    "AB": "该接口无对应前端页面操作，无页面基线可比对",
    "AC": "非写接口，不存在重复提交与业务唯一性",
    "AD": "该操作不触发事件/回调，无推送可验",
    "AE": "认证与传输契约由公共层统一处理，本接口无特殊约定",
    "C": "无落库数据可回读，字段语义真值不适用",
    "E": "不产生状态流转，无状态机可测",
    "F": "单参数接口，无字段组合交叉可测",
    "G": "无内容/URL/附件类输入，安全深化场景有限",
    "I": "无多语言文案，编码与国际化仅做轻量校验",
    "J": "性能与并发稳定性为选测项（非压测）",
    "L": "兼容性（协议/SDK/上游）非本轮范围",
    "M": "不涉及导出下载",
    "N": "非层级资源接口",
    "O": "无异步通知/发送",
    "P": "无分享链接参数",
    "Q": "无多角色关系",
    "R": "非报告分享",
    "S": "非容器/分类管理",
    "T": "非账号身份信息接口",
    "U": "非批量导入",
    "V": "请求标识与追踪由公共层保证，本轮不单列",
    "W": "无文件/附件字段",
    "X": "非批量操作",
    "Y": "无复杂结构返回",
    "Z": "国内版测试，国际版维度 N/A（非国际版）",
    "AF": "本轮没有真实业务场景可用（未提交 af_cases.json），机械拼出的合法请求不算业务场景覆盖",
}

# AF 不参与的统一理由。必须点名**缺的是哪份输入**，否则「条件不满足」四字会让人
# 以为接口天然不需要业务场景，而不是「这份材料这轮没交」。
AF_NO_SCENARIO = (
    "本轮没有真实业务场景可用（未提交 af_cases.json）：按 business_scenarios 三步"
    "产出业务用例后经 --af-cases 传入才会参与；机械拼出的合法请求只验证字段可发，"
    "不能算业务场景覆盖")

INTL_HINTS = ["国际版", "海外", "多语言", "国际化", "i18n", "language", "locale", "currency", "timezone"]


def _split_words(text):
    """把标识符切成词：先按非字母数字切，再按驼峰切。

    【审查修复】W30 的词边界把**复合词里的操作名**也一起杀了：接口名 batchImport /
    importData / updateTitle / removeItem 是 wjx 契约的主流命名，lower 之后
    `import` 前面紧邻 `h`，词边界判否 → 操作类型退回「待确认」→ P03 事件推送 /
    P08 重复数据 / S1 并发 / S3 终态这四个以 op_type 为门控的模式**整块不产**
    （实测同一份契约：编辑问卷命中 17 条用例，改名 updateTitle 后只剩 13 条）。
    按驼峰切词后整词比对，既保留反误命中（address 不含 add、forget 不含 get），
    又不伤复合词。
    """
    words = []
    for part in re.split(r"[^A-Za-z0-9]+", str(text or "")):
        if not part:
            continue
        words.extend(w.lower() for w in
                     (re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", part) or [part]))
    return [w for w in words if w]


def _word_hit(keyword, text):
    """英文关键词按驼峰分词做**整词**匹配，中文仍用子串（W30 + 审查修复）。

    W30 原意是修子串误命中（add→address、get→target/forget、new→renew），但它用的是
    原始子串两侧的边界判定，于是把 batchImport 这类真实操作名一并杀掉。这里改成
    先分词再整词比对，两个目标同时满足。"""
    if not keyword or not text:
        return False
    if re.match(r"^[a-z][a-z0-9_-]*$", keyword):
        kw = keyword.replace("-", "").replace("_", "")
        return any(w.replace("_", "") == kw for w in _split_words(text))
    return keyword in text


def _detect_op_type(contract):
    """操作类型识别。

    【W30】英文关键词按词边界匹配；全部未命中时**不再默认「查询」**——返回 None，
    由调用方记「操作类型待确认」（创建/删除被误判查询会让幂等/状态机整块 N/A 漏测，
    宁可待确认不可错向）。仅当接口名含明确的列表/分页信号时才保守判查询。"""
    # 【审查修复】传**原始大小写**：驼峰是复合词的操作信号（batchImport = batch + import），
    # 先 lower 再匹配就再也分不出词边界了。
    name = (contract.get("name") or "") + " " + str(contract.get("action") or "")
    for op, keys in OP_RULES:
        for k in keys:
            if _word_hit(k, name):
                return op
    # 无命中：列表/分页强信号才判查询，否则待确认
    if _is_list_query(contract):
        return "查询"
    return None


def _is_list_query(contract):
    """查询类接口细分：列表 or 详情。"""
    name = (contract.get("name") or "").lower()
    if any(k in name for k in ("列表", "清单", "list", "分页")):
        return True
    for f in contract.get("response_fields", []) or []:
        n = (f.get("name") or "").lower()
        s = f.get("semantics") or []
        if n in ("list", "items", "total", "rows", "records", "count") or "count" in s or "page" in s:
            return True
    return False


def _detect_domains(contract):
    """只按「接口名 + 字段名」判定业务域，避免描述里的词（如"标签""短链接"）造成误命中。"""
    text = (contract.get("name") or "") + " " + " ".join(
        (f.get("name") or "") for f in
        (contract.get("request_fields", []) or []) + (contract.get("response_fields", []) or [])
    )
    text = text.lower()
    hits = []
    for dim, keys in DOMAIN_RULES:
        if any(k.lower() in text for k in keys):
            hits.append(dim)
    return hits


def _detect_intl(contract):
    if any(k in (contract.get("name") or "") for k in INTL_HINTS):
        return True
    for f in (contract.get("request_fields", []) or []) + (contract.get("response_fields", []) or []):
        if set(f.get("semantics") or []) & {"i18n", "tz", "currency"}:
            return True
    return False


# 维度中文名：不参与理由要能读成「具体哪个条件没满足」，不能只写字母。
DIM_CN = {
    "M": "导出下载", "N": "层级归属", "O": "异步通知", "P": "分享链接",
    "Q": "多角色关系", "R": "报告分享", "S": "容器分类", "T": "账号身份",
    "U": "批量导入", "W": "文件附件", "X": "批量操作", "Z": "国际版",
}

# 这些维度不依赖契约特征：只要能发请求就有对应的检查面，恒参与。
ALWAYS_ON_DIMS = ("A", "D", "H", "J", "L", "V", "AE", "Y", "AD")


def _dim_conditions(contract, op, resolved, intl, af_available=False):
    """**默认全开**：逐维度检查参与条件，只有条件不满足才给出可核对的不参与理由。

    与旧版按操作类型预设「必测/选测/不适用」矩阵相反：矩阵没列的维度直接判 N/A，
    会把「暂不支持」永久化，而且理由写成「该操作类型不涉及」这种无法核对的话。
    这里的判据一律来自契约事实（有没有请求参数、几个参数、有没有状态/分页/附件字段、
    是不是写操作、业务域能不能构造请求），并复用 dim_generators._domain_dim_resolve
    作为业务域维度的单一事实源。
    """
    req = [f for f in (contract.get("request_fields") or []) if isinstance(f, dict)]
    resp = [f for f in (contract.get("response_fields") or []) if isinstance(f, dict)]
    names = " ".join(str(f.get("name") or "") for f in req + resp).lower()
    text = names + " " + str(contract.get("name") or "").lower()
    str_req = [f for f in req
               if not re.search(r"int|num|bool|float|long|double",
                                str(f.get("type") or "").lower())]
    is_write = op in ("创建", "修改", "删除", "导入")
    paginate = ("page", "pagesize", "page_size", "page_no", "pageno", "limit", "offset",
                "页码", "每页", "分页")
    state_tokens = ("status", "state", "publish", "enable", "audit", "draft",
                    "状态", "草稿", "发布", "审核")
    domain_tokens = {"M": ("导出", "下载", "excel", "export", "download"),
                     "X": ("批量", "batch")}
    conds = {}

    def put(d, ok, reason=""):
        conds[d] = (bool(ok), reason)

    for d in ALWAYS_ON_DIMS:
        put(d, True)
    put("B", bool(req), "契约未声明请求参数，没有可校验的字段")
    put("C", bool(req or resp), "契约未声明任何字段，无字段语义真值可测")
    put("E", is_write or any(t in text for t in state_tokens),
        "非写操作且契约未声明状态/发布/审核类字段，无状态流转可测")
    put("F", len(req) >= 2, "请求参数少于 2 个，无字段组合交叉可测")
    put("G", bool(str_req), "没有字符串/内容类入参，安全载荷无处注入")
    put("I", bool(str_req), "没有文本类入参，编码与国际化无对象")
    put("K", bool(resp), "契约未声明响应字段，无法做响应契约断言")
    put("AA", any(t in text for t in paginate), "没有页码/条数/分页参数，分页边界不适用")
    put("AB", bool(req or resp), "契约未声明任何展示字段，无前端口径可比对")
    put("AC", is_write, "非写接口，不存在重复提交与业务唯一性")
    # AF 的「条件」就是**有没有真实业务场景**。机械拼出的合法请求只能证明字段能发，
    # 证明不了「用户实际怎么用」——拿它顶业务场景等于把假覆盖写进分母。没有真场景
    # 就如实转不参与并写明缺哪份输入；有场景（--af-cases / af_cases.json）才参与。
    put("AF", af_available, AF_NO_SCENARIO)
    for d in ("M", "N", "O", "P", "Q", "R", "S", "T", "U", "W", "X", "Z"):
        if d == "Z":
            put("Z", intl, "非国际版契约，多语言/时区/多币种条件未满足")
            continue
        info = resolved.get(d) or {}
        if info.get("hit"):
            put(d, True)
            continue
        tokens = domain_tokens.get(d) or ()
        hit = bool(tokens) and any(t in text for t in tokens)
        # 零产出归因是代号（no_feature / response_side_only …），直接写进 N/A 证明
        # 会让人看不懂「到底哪个条件没满足」。转成中文并点名是哪一类业务特征。
        from dim_generators import REASON_TEXT
        raw = info.get("reason")
        reason = REASON_TEXT.get(raw, raw) if raw else None
        if reason:
            reason = "%s：%s" % (DIM_CN.get(d, d), reason)
        put(d, hit, reason
            or "契约未声明%s相关字段，也没有可传参的请求字段" % DIM_CN.get(d, d))
    return conds


def judge(contract, with_business=None, af_available=None):
    """主入口：**默认全维度参与**，条件不满足的逐个写明理由并标不适用。

    返回 dimensions / coverage_plan / optional_plan / na_proofs / notes。
    `optional_plan` 保留空列表以兼容下游读取（新口径不再有「选测」这一档：
    选测不进覆盖率分母，等于「声明了但不参与」，与默认全开矛盾）。

    ``af_available``：本轮有没有**真实业务场景**（business_scenarios 产出的
    af_cases）。None 时看 contract 上有没有挂 af_cases。没有就如实转不参与——
    AF 的「条件」是有没有真实场景，不是「主链能不能拼出合法请求」。
    """
    # 兼容旧调用方：with_business / WJX_WITH_BUSINESS 只作为「是否额外合并外部
    # AF 用例」的历史开关保留，不再决定 AF 是否参与（AF 已默认参与）。
    op = _detect_op_type(contract)
    # 【W30 配套】操作类型待确认：不默认查询（错向 = 幂等/状态机整块漏测），
    # 用「查询」的保守基线做计划，但在 coverage_plan 留痕供人工复核。
    op_unconfirmed = op is None
    if op_unconfirmed:
        op = "查询"
    intl = _detect_intl(contract)
    domains = _detect_domains(contract)
    from dim_generators import _domain_dim_resolve
    try:
        _resolved = _domain_dim_resolve(contract)
    except Exception as _exc:  # noqa: BLE001
        # 异常降级必须出声：解析结果为空会让业务域维度整片消失，而报告一切正常。
        import sys as _sys
        _sys.stderr.write("[applicability] 业务域维度解析失败（%s: %s），本轮按契约字段条件兜底判定\n"
                          % (type(_exc).__name__, _exc))
        _resolved = {}

    # 默认全开：先假设每个维度都参与，再由条件判据逐个剔除，理由必须可核对。
    # AF 与其余维度同口径，但它的条件是「有没有真实业务场景」：有才参与，
    # 拿不到就写明缺什么、转不参与（不许用机械基线冒充）。要显式关掉仍走
    # --exclude-dims AF。with_business 参数只为兼容旧调用方，不再影响判定。
    if af_available is None:
        af_available = bool(contract.get("af_cases"))
    conds = _dim_conditions(contract, op, _resolved, intl, af_available)

    dims, plan, na_proofs, notes = {}, [], [], []
    for d in ALL_DIMS:
        ok, reason = conds.get(d, (True, ""))
        if ok:
            dims[d] = "必测"
            plan.append(d)
        else:
            dims[d] = "不适用"
            na_proofs.append({"dim": d, "reason": reason or "条件不满足"})
    notes.append("默认全维度参与：本轮 %d 个参与、%d 个因条件不满足不参与"
                 "（每个不参与维度的具体理由见 na_proofs）"
                 % (len(plan), len(na_proofs)))
    if dims.get("AB") == "必测":
        notes.append("AB 为必测：需先取页面基线（references/ui_baseline.md），无基线不得凭空填预期值")
    if intl:
        notes.append("判定为国际版场景：Z 及 §1.3 国际版扩展维度已加载")
    else:
        notes.append("非国际版：Z 因「未满足国际版启用条件」不参与，不计入覆盖率分母")
    if op_unconfirmed:
        notes.append("操作类型待确认：接口名未命中任何操作关键词（也不含列表信号），"
                     "按「查询」保守基线出计划——请人工确认真实操作类型后重判，"
                     "错向的幂等/状态机用例会成片假 FAIL")
    return {
        "op_type": op,
        "op_unconfirmed": op_unconfirmed,
        "is_list_query": _is_list_query(contract) if op == "查询" else False,
        "business_domains": domains,
        "is_intl": intl,
        "dimensions": dims,
        "coverage_plan": plan,
        "optional_plan": [],
        "na_proofs": na_proofs,
        "notes": notes,
    }
