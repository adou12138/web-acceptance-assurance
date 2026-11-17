# -*- coding: utf-8 -*-
"""契约解析器：把接口文档转成统一的 Contract 结构，供自动生成用例使用。

支持三种契约来源（与 doc_review.md 的四种来源对齐，本模块负责机器可读的三种）：
  1) ShowDoc Markdown（本团队主流：含「接口地址」「请求参数格式」「响应参数列表」「请求/响应示例」）
  2) OpenAPI 3.x / Swagger 2.0（JSON 或 YAML；按 operationId 或第一个 path+method 取；
     支持 $ref 解析与嵌套对象/数组递归展开，字段名用点路径表示如 data.items[].name）
  3) 极简 JSON 契约（{"action":..., "request_fields":[...], "response_fields":[...]}）

产出的 Contract 结构（dict）：
  {
    "action": "1000107", "name": "创建AI主页", "endpoint": "https://...",
    "request_fields":  [Field], "response_fields": [Field],
    "request_example": {...},   "response_example": {...},
    "source": "文件路径", "format": "showdoc|openapi|json"
  }
Field:
  {"name","type","desc","required","remark","enum":[(值,释义)],"max_len","min_len",
   "default","forbid":[禁用子串],"semantics":[语义标签]}

关键：备注列（备注/说明）里的约束是生成边界与枚举用例的金矿——
「最长100个字符」「默认值为0；0网页，1海报，2PPT」「不能包含"问卷星"」都会被抽成结构化约束。
列名写法有容错：必填列支持「必填/必选/必传/必填项/required」等变体；表头没有任何
必填列写法时把缺口写进 parse_warnings，不静默判非必填。
"""
import html
import json
import os
import re
import sys

# 【W46】含「示例」形态：示例段的标题（如「## 请求示例」）也要能被归属判定识别
REQ_HINTS = ("请求参数", "请求字段", "请求体", "输入参数", "请求示例")
RESP_HINTS = ("响应参数", "响应字段", "返回参数", "输出参数", "响应体", "响应示例",
              "成功响应", "成功返回")
# 失败/错误侧响应表的标题线索。这些表里的「必填=是」说的是**失败响应**必填，
# 拿它当成功响应的契约字段，会让成功用例断言一个成功响应里根本不存在的字段
# （典型：errormsg / errorcode 只在失败响应出现或只在失败侧必填）——接口行为
# 完全正常，报告却必然 fail。命中这里只打标记、不丢字段，字段仍可用于失败场景。
FAIL_HINTS = ("失败响应", "失败返回", "失败示例", "错误响应", "错误示例",
              "异常响应", "错误码", "错误信息", "错误返回", "fail", "error")


def _is_fail_heading(head):
    """标题是否指向「失败/错误」侧响应表。统一小写后做子串匹配（英文标题常见）。"""
    text = str(head or "").lower()
    return any(h in text for h in FAIL_HINTS)

MAX_RE = re.compile(r"(?:最长|最多|不超过|长度不能超过|长度上限|最大|长度限制|长度)\s*(\d+)")
MIN_RE = re.compile(r"(?:最短|最少|不小于|最小|至少)\s*(\d+)")
DEF_RE = re.compile(r"默认(?:值)?(?:为|是|取)?\s*[:：]?\s*([^\s；;，,。、]+)")
# 【W23】禁用词三形态：不能包含「X」/ 不得含有「X」/ 禁用词/敏感词：X
# 引号集含中英弯引号与「」『』（角括号——客户套餐名常用角括号包裹）。
FORBID_RE = re.compile(
    r"(?:不能包含|不得含有|禁止包含|不允许包含)\s*[“\"'‘’「」『』]([^”\"'’「」『』]+)[“\"'‘’「」『』]"
    r"|(?:禁用词|敏感词|违禁词)\s*[:：]?\s*([^\s；;，,。、|]+)")
ENUM_ITEM_RE = re.compile(r"(-?\d+)\s*([一-龥A-Za-z][一-龥A-Za-z0-9_]*)")
# 【W27】字符串枚举形态：「值只能是 A/B/C」「取值：basic,pro」——整数枚举正则抓不到，
# 字符串枚举此前全丢（invalid_enum 不进分母、B-ENUM 不生成）。
STR_ENUM_RE = re.compile(
    r"(?:只能(?:是|为)|取值(?:范围)?(?:为|是|[:：])?|可选值[:：]?)\s*[:：]?\s*"
    r"([A-Za-z][A-Za-z0-9_]*(?:\s*[/,，、]\s*[A-Za-z][A-Za-z0-9_]*){1,})")
# 【W27】required 真值集合扩充：√ * 必须 yes 等非常规标记此前全漏（B-MISS 不生成）。
# 归一后按**集合**判定，见 _norm_required_truthy——集合在导入期算一次，运行期只查表。
REQUIRED_TRUTHY = ("是", "Y", "y", "true", "True", "TRUE", "必填", "1",
                   "√", "✓", "*", "＊", "必须", "yes", "Yes", "YES", " required", "Required")

# 必填列里可能出现的「纯标记符号」——文档常用一串星号/对勾表示必填，
# 长度不固定（* / ** / *** / √√ 都是同一个意思）。
# 归一时前后都剥掉：`**` 剥完是空串，必须单独承认「剥空 = 全是标记 = 必填」，
# 否则 rstrip 把 `**` 削成 "" 落进非必填，B-MISS 不生成且对账分母少算（漏测）。
_REQ_MARKS = "*＊√✓✔✅"
_REQ_TRUTHY_SET = frozenset(t.strip() for t in REQUIRED_TRUTHY if t.strip())


def _norm_required_truthy(req_raw):
    """必填列归一判定：容忍标记符号在前后任意位置、任意重复。

    旧实现 `req_raw.strip().rstrip("*＊") in REQUIRED_TRUTHY` 有两个洞：
      ① `**` / `***` 被 rstrip 削成空串 → 空串不在真值表 → 判非必填；
      ② `*是`（星号在前）rstrip 不处理 → 判非必填。
    正确口径：剥掉前后所有标记符号后，剩下的文本按集合判定；
    剥完为空但原串非空（说明原串全是标记）时，同样判必填。
    """
    s = (req_raw or "").strip()
    if not s:
        return False
    core = s.strip(_REQ_MARKS).strip()
    if not core:
        # 原串非空而剥完为空 → 原串整体就是标记符号（**/***/√√…），语义为必填
        return True
    return core in _REQ_TRUTHY_SET
ACTION_RE = re.compile(r"\[\s*(\d{4,})\s*\]")
# 【审查修复·P0】JSON 写法 `"action": "1001002"` 里引号在冒号**外侧**，旧式
# \s*(?:为|是|=|:|：)?\s*[`'\"]?(\d{4,}) 要求冒号先于引号，对这种最常见的形式必失配
# ——契约明明写了 action 却解析成空串，整轮用例静默失效。改为引号/空白与分隔符
# 交错容许（分隔符仍可选，兼容「动作标识 1001002」这类纯空格写法），并用
# tests/test_action_extraction_and_guard.py 的正反例锁定。
ACTION_BODY_RE = re.compile(r"(?:动作标识|action)[\s`'\"]*(?:编号|ID|id)?[\s`'\"]*(?:为|是|=|:|：)?[\s`'\"]*(\d{4,})", re.I)
URL_RE = re.compile(r"https?://[^\s`\"'）)、,，]+")


def _unescape(s):
    return html.unescape(s or "")


# 表格竖线族：ASCII 竖线 `|`、制表符框线 `│`(U+2502) / `┃`(U+2503)、全角竖线 `｜`(U+FF5C)。
# 只认 ASCII 竖线时，用框线对齐画出来的表格整行不被识别——手写对齐的接口文档大量
# 是这种写法。后果不是"少几个字段"，而是整张参数表消失，顺序兜底转而去拿文档里
# 第一张无关表格（环境配置）当请求参数、拿覆盖率矩阵当响应字段。
_VBAR_CHARS = "|\u2502\u2503\uff5c"
_VBAR_SPLIT_RE = re.compile("[|\u2502\u2503\uff5c]")

# 表格边框行只由横线与拐角组成（┌┬┐ ├┼┤ └┴┘ ─）。这类行既不是数据行也不是正文：
# 当数据行会污染单元格，当正文则会 flush 掉整个表格块——表头之后遇 ├──┼──┤ 就断，
# 数据行全部丢失。
_BOX_H_LINE = "─━═┄┅┈┉╌╍"
_BOX_CORNER = "┌┬┐├┼┤└┴┘┏┳┓┣╋┫┗┻┛╔╦╗╠╬╣╚╩╝"
_BOX_V_LINE = "\u2502\u2503\uff5c┆┇┊┋╎╏"


def _row_cells(line):
    s = line.strip()
    if not s or s[0] not in _VBAR_CHARS:
        return None
    body = s.strip(_VBAR_CHARS)
    if not body.strip():
        return None          # 只有一个竖线的装饰行，不是单元格行
    return [c.strip() for c in _VBAR_SPLIT_RE.split(body)]


def _border_kind(line):
    """框线行判定：'top' / 'mid' / 'bottom'；不是框线返回 None。

    top 与 bottom 都是表格边界（前者结束上一张表、后者结束本张表）；
    mid 是表内分隔行（├──┼──┤ 或纯 ───），必须**跳过但不打断**当前块。
    """
    s = line.strip()
    if not s:
        return None
    if any(ch in _BOX_V_LINE for ch in s):
        return None          # 含竖线 → 数据行/表头行，交给 _row_cells
    if not all(ch in _BOX_H_LINE or ch in _BOX_CORNER or ch.isspace() for ch in s):
        return None
    if not any(ch in _BOX_CORNER for ch in s):
        # 纯横线（─────）是正文里的横线分隔，不是表格边框。它必须**打断**当前表格块，
        # 否则上下两张无关的表会被拼成一张（第二张的表头变成第一张的数据行）。
        return None
    if not any(ch in _BOX_H_LINE for ch in s):
        return None          # 只有拐角没有横线，不像边框
    if any(ch in "└┴┘┗┻┛╚╩╝" for ch in s):
        return "bottom"
    if any(ch in "┌┬┐┏┳┓╔╦╗" for ch in s):
        return "top"
    return "mid"


def _is_sep(cells):
    """判定 markdown 表格分隔行（如 |:---|:------|---|-|--------|）。
    注意：必需列可能只写一个短横 `-`，故短横数量下限为 1 而非 2。"""
    body = [c for c in cells if c]
    return bool(body) and all(re.fullmatch(r":?-{1,}:?", c.replace(" ", "")) for c in body)


def _heading_of(line):
    """识别一行标题，返回标题文本；不是标题返回空串。

    真实 ShowDoc 导出有三种标题写法，只认第一种会让后续表格按出现顺序猜归属：
      1. markdown：`# 2.2.1 请求参数格式`
      2. 列表包裹：`- # 2.2.1 请求参数格式`
      3. 锚点 + 裸编号：`<div id="2.2.2.1.1"></div>2.2.2.1.1 题目查询条件（condition）`
    第三种是子对象表的标题（condition / answer / answer_item），漏认就会把子对象
    字段平铺成顶层请求参数，围绕 q_index/opt/val 生成一批永远发不对的用例。
    """
    t = re.sub(r"^[-*+>]\s+", "", line.strip())
    if t.startswith("#"):
        return t.lstrip("#").strip()
    plain = re.sub(r"<[^>]+>", "", t).strip()
    if not plain or any(ch in plain for ch in _VBAR_CHARS):
        return ""  # 表格行/空行（框线表格同样按表格行排除，不能当标题）
    # 编号标题：2.2.2.1.1 xxx。正文段落也可能以编号开头，用长度上限排除
    if len(plain) <= 60 and re.match(r"^\d+(?:\.\d+)+\s+\S", plain):
        return plain
    return ""


def _is_sub_object_heading(heading):
    """子对象/子结构表：字段属于某个参数内部，不能当顶层参数发。

    ShowDoc 用「xxx对象参数（answer）」「题目查询条件（condition）」这类标题描述
    嵌套结构；把它们的字段平铺进 request_fields，B-MISS/B-TYPE 会围绕一个顶层不存在
    的参数生成用例，服务端忽略未知参数后判「校验缺失」——一批假缺陷。
    """
    h = str(heading or "")
    return ("对象参数" in h or "子对象" in h or "（condition）" in h
            or "(condition)" in h.lower())


def _extract_tables(text):
    """抽取 markdown 表格，返回 [{"heading","header","rows"}]。

    两种表格画法都要认：ASCII 竖线（| a | b |）与制表符框线（│ a │ b │ 加
    ┌┬┐ / ├┼┤ / └┴┘）。框线表格的边框行按"表格边界/分隔"处理，不参与单元格。
    """
    tables, heading, block = [], "", []

    def flush():
        if len(block) >= 2:
            rows = [r for r in block[1:] if not _is_sep(r)]
            tables.append({"heading": heading, "header": block[0], "rows": rows})
        del block[:]

    for line in text.splitlines():
        s = line.strip()
        kind = _border_kind(s)
        if kind == "bottom":
            flush()
            continue
        if kind == "top":
            if block:
                flush()
            continue
        if kind == "mid":
            continue                 # 表内分隔行：跳过，不打断当前块
        hd = _heading_of(s)
        if hd:
            flush()
            heading = hd
            continue
        cells = _row_cells(s)
        if cells:
            block.append(cells)
        else:
            flush()
    flush()
    return tables


def _col_index(header, keywords, fallback):
    for i, h in enumerate(header):
        for k in keywords:
            if k in h:
                return i
    return fallback if fallback < len(header) else None


# 参数位置列的列名写法。**不复用 _col_index**：它对关键词做子串匹配，单字母
# "in" 会命中 index / info / 初始化；且它的 fallback 语义是「回退到某一列」，
# 用 -1 作哨兵会命中 cells[-1]（最后一个单元格），把备注误当位置。
_LOC_COL_KEYWORDS = ("参数位置", "传参位置", "传参方式", "位置", "location",
                     "param_location", "参数来源")
# 位置取值归一：团队文档的写法远多于 OpenAPI 的四个词。
_LOC_ALIASES = {
    "body": "body", "form": "body", "formdata": "body", "requestbody": "body",
    "请求体": "body", "报文": "body", "表单": "body", "body参数": "body",
    "query": "query", "querystring": "query", "url": "query", "url参数": "query",
    "查询": "query", "查询串": "query", "get参数": "query", "地址栏参数": "query",
    "header": "header", "headers": "header", "请求头": "header", "头部": "header",
    "path": "path", "urlpath": "path", "路径": "path", "路径参数": "path",
    "url路径": "path", "restful": "path",
}


def _loc_col_index(header):
    """定位「参数位置」列；没有该列返回 None（None ≠ 回退到某列）。"""
    for i, cell in enumerate(header):
        hl = str(cell or "").strip().lower()
        if hl == "in":                      # 只认整格 "in"，不做子串匹配
            return i
        for keyword in _LOC_COL_KEYWORDS:
            if keyword in hl:
                return i
    return None


def _norm_location(raw):
    """位置单元格 → 枚举值；识别不了返回空串（由调用方留痕，不猜）。"""
    key = re.sub(r"[\s\-_/]+", "", str(raw or "").strip().lower())
    return _LOC_ALIASES.get(key, "")


# 必填列的列名写法归一。团队主流写「必填」，外部文档常见「是否必选 / 是否必传 /
# 必填项 / Required」等变体；只认少数写法会让整列错位，所有字段判非必填，
# 必填缺失用例整批不生成且无告警（静默漏测）。「选填」不在此列——它表达可选。
_REQUIRED_COL_KEYWORDS = ("必填", "必选", "必传", "必须", "必需",
                          "required", "mandatory")


def _required_col_index(header):
    """定位必填列，返回 (index, matched)。

    matched=False 表示表头没有任何必填列写法：index 回退到第 4 列以保持既有
    文档的输出不变，但调用方必须留痕——整列判非必填会让必填缺失用例整批
    不生成且无告警，出声比静默安全。
    """
    for i, h in enumerate(header):
        hl = (h or "").lower()
        for k in _REQUIRED_COL_KEYWORDS:
            if k in hl:
                return i, True
    return (3 if 3 < len(header) else None), False


# 参数名/字段名列表头的写法。判定「这张表是不是参数表」只认**整格精确命中**
# （去掉空白与括号后），不做子串匹配——「环境名」「接口信息表」这类说明性表头
# 含"名"字，子串匹配会把环境配置表也当成参数表。
_NAME_HEADER_EXACT = frozenset((
    "参数", "参数名", "参数名称", "参数名（必填）",
    "字段", "字段名", "字段名称", "属性", "属性名", "键", "键名", "名称",
    "name", "key", "field", "fieldname", "param", "paramname", "parameter",
    "parametername", "property", "attribute", "arg", "argument",
))


def _looks_like_param_table(header):
    """表头是否「像参数表」：必须有一列是参数名/字段名。

    用途是挡住文档里的**说明性表格**（环境配置表、覆盖率矩阵、产出物清单）——
    这些表没有参数名列。它们一旦被当成参数表，字段名会变成「本地环境类」
    「A 鉴权权限」「测试计划」，围绕这些词生成的用例连接口都打不中。
    """
    for cell in header or []:
        s = re.sub(r"[\s*`＊()（）\[\]【】:：]", "", str(cell or "")).lower()
        if not s:
            continue
        if s in _NAME_HEADER_EXACT:
            return True
        if any(k in s for k in ("参数", "字段", "属性")):
            return True
    return False


# 标题里点名的动作号：「### 动作 1000108 —— 更新 AI 主页」。多接口合并文档常用
# 这种写法把两张参数表排在一起，全部并进同一个接口会让另一个接口的字段变成
# 本接口的请求参数（homepage_id 发给创建接口，服务端忽略后判"校验缺失"）。
_HEADING_ACTION_RE = re.compile(r"(?:动作|action)\s*(?:编号|ID|id)?\s*[:：]?\s*(\d{4,})", re.I)


def _heading_action(head):
    """从表格标题里取动作号；取不到返回空串。"""
    m = _HEADING_ACTION_RE.search(str(head or ""))
    return m.group(1) if m else ""


# 自检用的「声称必填」表述与「明确说可选」的反向表述。反向表述命中时不再判为
# 声称必填——「可选（非必填）」不该被报成冲突。
_REQUIRED_CLAIM_RE = re.compile(r"(必填|不能为空|不可为空|必传|必须填写)")
_OPTIONAL_CLAIM_RE = re.compile(r"(非必填|不必填|选填|可选)")
# 明确表达「非必填」的取值：自检据此区分「文档标了可选」与「取值没被识别」。
_OPTIONAL_TRUTHY = frozenset((
    "否", "N", "n", "false", "False", "FALSE", "非必填", "不必填", "选填", "可选",
    "×", "✗", "0", "no", "No", "NO", "-", "—",
))
# 表头里出现的约束列写法——有这类列却没抽出任何长度/枚举时值得点名。
_BOUND_COL_RE = re.compile(r"(长度|最长|字数|枚举|取值|可选值)")


def _self_check_table(fields, header, req_vals, req_idx, req_matched):
    """规则抽取后的机械自检：把「规则自己没把握」的地方亮出来，不覆盖任何值。

    只做三类可机械判定的冲突检测，结论进 self_check 供报告点名：
      SC1 必填列有未能识别的取值，且没有任何字段被判必填（取值写法未覆盖/列错位）；
      SC2 字段说明或备注声称必填，但必填列判为非必填（列与文字互相矛盾）；
      SC3 表头有长度/枚举类约束列，但一个约束都没抽出来（约束写在别处或写法未覆盖）。

    刻意不做「猜一个正确值」的修正：自检的产出是留痕，不是覆盖。猜错会把一个
    可疑值变成一条看起来确定的假边界用例，比不修更糟。
    """
    items = []
    if req_matched and req_idx is not None:
        vals = [str(v).strip() for v in (req_vals or []) if str(v or "").strip()]
        unknown = [v for v in vals
                   if not _norm_required_truthy(v) and v not in _OPTIONAL_TRUTHY]
        if unknown and not any(f.get("required") for f in fields):
            items.append({
                "check": "SC1", "level": "warn",
                "message": "必填列有 %d 个取值未能识别（如「%s」），且没有任何字段被判必填"
                           "——取值写法未覆盖或必填列错位（表头：%s）"
                           % (len(unknown), unknown[0][:12], "|".join(header)),
            })
    claimed = []
    for f in fields:
        if f.get("required"):
            continue
        text = "%s %s" % (f.get("desc") or "", f.get("remark") or "")
        if _REQUIRED_CLAIM_RE.search(text) and not _OPTIONAL_CLAIM_RE.search(text):
            claimed.append(f["name"])
    if claimed:
        items.append({
            "check": "SC2", "level": "warn",
            "message": "字段说明写明必填但必填列判为非必填：%s" % "、".join(claimed[:10]),
        })
    bound_cols = [h for h in header if h and _BOUND_COL_RE.search(h)]
    if bound_cols and not any(f.get("max_len") or f.get("enum") for f in fields):
        items.append({
            "check": "SC3", "level": "info",
            "message": "表头含约束列「%s」，但未抽出任何长度/枚举约束——"
                       "约束可能写在别的列或用了未覆盖的写法" % "|".join(bound_cols),
        })
    return items


def _pick(cells, idx):
    if idx is None or idx >= len(cells):
        return ""
    return cells[idx]


def _parse_constraints(remark, desc=""):
    """从备注/说明里抽结构化约束：最大长度、最小长度、默认值、枚举、禁用子串。"""
    text = "%s %s" % (remark or "", desc or "")
    out = {"max_len": None, "min_len": None, "default": None, "enum": [], "forbid": [],
           # 【审查修复·P1 数值边界承载位】此前 minimum/maximum 没有任何字段承载位，
           # OpenAPI 声明的数值范围在解析层整体蒸发——数值边界用例与对账分母
           # 双侧一致不产（off-by-one 缺陷整类漏测且无告警）。新增 min_value/max_value。
           "min_value": None, "max_value": None}
    m = MAX_RE.search(text)
    if m:
        out["max_len"] = int(m.group(1))
    m = MIN_RE.search(text)
    if m:
        out["min_len"] = int(m.group(1))
    m = DEF_RE.search(text)
    if m:
        out["default"] = m.group(1).strip("。，,;；")
    # 【W23】FORBID_RE 双捕获组（引用形态 / 词表形态）——findall 返回 tuple，
    # 展平并去空、去重保序。
    _fb = [next((g for g in grp if g), "") for grp in FORBID_RE.findall(text)]
    out["forbid"] = [w for i, w in enumerate(_fb) if w and w not in _fb[:i]]
    # 枚举：形如「0网页，1海报，2PPT」——至少 2 组且值不重复才认定为枚举
    items = ENUM_ITEM_RE.findall(text)
    if len(items) >= 2:
        vals = [int(v) for v, _ in items]
        if len(set(vals)) == len(vals):
            out["enum"] = [(int(v), lab) for v, lab in items]
    # 【W27】字符串枚举：「只能是 A/B/C」「取值：basic,pro」——整数枚举正则抓不到。
    # 字符串值没有天然释义，释义列留空由人工确认（标「疑似枚举」语义，先入分母）。
    if not out["enum"]:
        m = STR_ENUM_RE.search(text)
        if m:
            vals = [v.strip() for v in re.split(r"[/,，、]", m.group(1)) if v.strip()]
            if len(vals) >= 2 and len(set(vals)) == len(vals) \
                    and not _is_format_placeholder(vals):
                out["enum"] = [(v, "") for v in vals]
    return out


def _is_format_placeholder(vals):
    """日期/时间**格式占位符**家族（yyyy/MM/dd、MM/dd、HH/mm）：每个 token 都由
    同一字母重复构成。它们描述的是值的书写格式，不是合法取值——当成枚举会让
    B-ENUM 拿 yyyy/MM/dd 当合法值生成用例、分母多出一条假枚举项。真实枚举
    （basic/pro、GET/POST）不会整组都是同字母重复，判据不会误伤。"""
    return bool(vals) and all(
        len(v) >= 2 and v.lower() == v.lower()[0] * len(v) for v in vals)


def _mk_field(cells, header):
    i_name = _col_index(header, ("参数", "字段", "名称", "name", "key"), 0)
    i_type = _col_index(header, ("类型", "type"), 1)
    i_desc = _col_index(header, ("说明", "描述", "desc", "含义"), 2)
    i_req, _ = _required_col_index(header)
    i_remark = _col_index(header, ("备注", "remark", "约束"), 4)
    name = _pick(cells, i_name).strip()
    if not name or name in ("参数", "字段"):
        return None
    remark = _pick(cells, i_remark)
    desc = _pick(cells, i_desc)
    req_raw = _pick(cells, i_req)
    # 【W27】required 真值集合扩充：√/*/*＊/必须/yes 等非常规标记此前全漏
    # （B-MISS 不生成、对账分母少算）。归一后按集合判定，strip 已在 _pick 完成。
    # 【审查修复】标记符号前后都可能有且可重复（`**`/`***`/`√√`/`*是`），
    # 交由 _norm_required_truthy 统一处理。
    required = _norm_required_truthy(req_raw)
    cons = _parse_constraints(remark, desc)
    field = {
        "name": name,
        "type": (_pick(cells, i_type) or "").strip().lower(),
        "desc": desc,
        "required": required,
        "remark": remark,
        "enum": cons["enum"],
        "max_len": cons["max_len"],
        "min_len": cons["min_len"],
        "default": cons["default"],
        "forbid": cons["forbid"],
        "semantics": [],
    }
    # 位置只写非 body：整表 request_fields 会进产物 contract_model，而它是回归基线的
    # 哈希输入——无条件写 "body" 会让所有历史契约的 baseline 一次性变 STALE。
    i_loc = _loc_col_index(header)
    if i_loc is not None:
        raw_loc = _pick(cells, i_loc).strip()
        loc = _norm_location(raw_loc) if raw_loc else "body"
        if loc and loc != "body":
            field["location"] = loc
        elif not loc:
            # 有值但认不出来：留痕，不按 body 静默处理——它会被写进请求体，
            # 服务端忽略未知键时用例照样"成功"，配错位置永远发现不了。
            field["location_unknown"] = raw_loc
    return field


def _extract_json_blocks(text, _with_pos=None, _warn=None):
    """提取 ```json 代码块。_with_pos 传 list 时顺带收集 (start_offset, block)，
    供 W46 的「示例按最近标题归属」消费（此前该属性从未被写入，归属修复是死代码）。

    【审查修复】_warn 传 list 时收集**畸形块**（json.loads 失败）留痕——旧实现
    裸 `except: continue` 静默丢弃，调用方声明的 _warn_json_blocks 从未被写入：
    契约里写坏的示例块既不进 stderr 也不进 parse_warnings，读报告的人以为
    「文档没写示例」，实际是「写了但解析不了」。两种情况排查方向完全不同。
    """
    blocks = []
    for m in re.finditer(r"```(.*?)```", text, re.S):
        raw = _unescape(m.group(1)).replace("\xa0", " ").strip()
        # 语言标签（```csharp / ```java / ```php）与 ShowDoc 常带的 Content-Type 行
        # 都不属于报文。旧实现只认 ```json，遇到其它标签整块被判「不是 JSON」并
        # 静默 continue——连畸形块告警都进不去，于是「文档写了示例」与「文档没写
        # 示例」在产物上完全一样（实测真实契约 3 个块全丢、信封键判定归零）。
        # 现在统一截到第一个 `{`，解析成功即采用。
        if not raw.startswith("{"):
            idx = raw.find("{")
            if idx < 0:
                continue  # 确实不是 JSON 内容块（bash/sql 等），无需告警
            raw = raw[idx:]
        try:
            blk = json.loads(raw)
        except Exception as exc:
            # 只对「看起来像 JSON」的块告警，避免把普通代码块记成示例解析失败
            if _warn is not None and ('":' in raw or '":"' in raw):
                _warn.append("示例 JSON 块解析失败（第 %d 字偏移，%s）：%s"
                             % (m.start(), type(exc).__name__, raw[:60].replace("\n", " ")))
            continue
        blocks.append(blk)
        if _with_pos is not None:
            _with_pos.append((m.start(), blk))
    return blocks


def _nearest_example_side(seg):
    """判定示例块归属请求侧还是响应侧：**取 seg 中各标题最后一次出现的位置比谁更近**。

    【审查修复】旧判定是「窗口内含任一响应词，且不含任一请求词」。W46 给
    REQ_HINTS 加了「请求示例」、给 RESP_HINTS 加了「响应示例」之后，两个示例块
    相距 400 字以内时，响应块前的窗口**必然包含前面的「请求示例」标题** →
    `not any(REQ_HINTS)` 恒为 False → 响应示例永远抓不到，随后又被
    `elif not req_ex` 静默吞掉。整窗口包含判定在标题相邻时结构上必然失效。

    改为比较「最后一个请求标题位置」与「最后一个响应标题位置」，谁更靠后谁更近：
      · 两个标题都没出现 → None（交给调用方走顺序兜底）
      · 只有一侧出现   → 那一侧（块前最近的那个标题就是它的归属声明）
      · 两侧都出现     → 取位置更靠后的那个（即离本块最近的标题）
    """
    last_req = last_resp = -1
    for h in REQ_HINTS:
        i = seg.rfind(h.lower())
        if i > last_req:
            last_req = i
    for h in RESP_HINTS:
        i = seg.rfind(h.lower())
        if i > last_resp:
            last_resp = i
    if last_req < 0 and last_resp < 0:
        return None
    if last_req < 0:
        return "resp"
    if last_resp < 0:
        return "req"
    return "resp" if last_resp > last_req else "req"


_METHOD_LINE_RE = re.compile(
    r"(?:请求方式|请求方法|请求类型|http\s*方法|HTTP\s*Method|method)\s*[:：|｜]?\s*"
    r"`?(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)`?", re.I)
# 无「请求方式」字样时，认 OpenAPI 文档里常见的示例行：`GET /openapi/x`
_METHOD_SAMPLE_RE = re.compile(r"(?m)^\s*(GET|POST|PUT|DELETE|PATCH)\s+/\S+", re.I)
_CT_LINE_RE = re.compile(
    r"content-type\s*[:：]\s*([A-Za-z0-9.+/_-]+(?:\s*;\s*charset=[A-Za-z0-9_-]+)?)", re.I)
_BODY_FMT_LINE_RE = re.compile(
    r"(?:请求格式|报文格式|请求体格式|body\s*格式|数据类型)\s*[:：|｜]?\s*"
    r"(json|表单|form|xml|text|纯文本|原始|raw)", re.I)


def _extract_method(text):
    """从契约正文抽请求方法；没写返回空串（由调用方回落 POST，不猜）。"""
    m = _METHOD_LINE_RE.search(text) or _METHOD_SAMPLE_RE.search(text)
    return m.group(1).upper() if m else ""


# 响应区标题。请求体格式**只在请求侧找**：响应区同样常写 `Content-Type: text/html`，
# 一旦被当成请求格式，整轮请求会按 raw + text/html 发出去（报文成了 Python 文本），
# 而契约里根本没有这个意思——这种误判不报错，只表现为「接口全挂」。
_RESP_SECTION_RE = re.compile(
    r"(?m)^\s*(?:#{1,6}\s*)?[*_]{0,2}\s*(?:返回|响应|输出)[^\n]{0,12}"
    r"(?:参数|字段|说明|示例|数据|头|结果|报文)")


def _request_side_text(text):
    """截到第一个「响应区」标题之前；没有响应区标题则整篇都算请求侧。"""
    m = _RESP_SECTION_RE.search(text)
    return text[:m.start()] if m else text


def _extract_body_format(text):
    """从契约正文抽请求体格式，返回 (body_kind, content_type, warnings)。

    默认 ("json", "", []) —— 契约没写就是 JSON（改造前行为）。
    """
    head = _request_side_text(text)
    m = _CT_LINE_RE.search(head)
    if m:
        ct = m.group(1).strip()
        low = ct.lower()
        if "json" in low:
            return "json", "", []
        if "x-www-form-urlencoded" in low:
            return "form", "application/x-www-form-urlencoded", []
        return "raw", ct, []
    m = _BODY_FMT_LINE_RE.search(head)
    if m:
        val = m.group(1).lower()
        if val == "json":
            return "json", "", []
        if val in ("表单", "form"):
            return "form", "application/x-www-form-urlencoded", []
        if val in ("xml",):
            return "raw", "application/xml", []
        return "raw", "text/plain", []
    # 请求区没写、但全文（只可能落在响应区）有：点名，**不采纳**。宁可回落到
    # 默认 JSON，也不拿响应格式去编码请求报文。
    stray = _CT_LINE_RE.search(text)
    if stray:
        return "json", "", [
            "契约只在响应区声明了 Content-Type: %s，未作为请求体格式采纳"
            "（若它就是请求格式，请写到请求参数/请求示例所在的区段）" % stray.group(1)]
    return "json", "", []


def _guess_raw_field(fields):
    """raw 体下哪个参数承载整包报文：唯一一个 string 类型参数就是它。

    只在 **body 位置**的字段里找：query/header/path 的参数不可能承载整包报文，
    把它们认成 raw_body_field 会让请求体变成一个 header 值（真实报文彻底走样），
    而且没有任何报错——服务端只会说「参数错误」，看起来像接口缺陷。
    多于一个 string 字段时不猜，返回空串（猜错比不猜更糟）。
    """
    from field_semantics import field_location
    candidates = [str(f.get("name") or "") for f in (fields or [])
                  if field_location(f) == "body"
                  and str(f.get("type") or "").lower() in ("string", "str", "text")
                  and str(f.get("name") or "")]
    return candidates[0] if len(candidates) == 1 else ""


def parse_showdoc(text, source="", action=None):
    """解析 ShowDoc 风格的 Markdown 契约。"""
    text = _unescape(text)
    title_m = re.search(r"^#\s+(.+)$", text, re.M)
    title = title_m.group(1).strip() if title_m else ""
    name = re.sub(r"\[\s*\d+\s*\]", "", title).strip()
    name = re.sub(r"^[\d.]+\s*", "", name).strip()
    if not action:
        am = ACTION_RE.search(title) or ACTION_RE.search(os.path.basename(source or ""))
        if not am:
            am = ACTION_BODY_RE.search(text)
        action = am.group(1) if am else ""

    ep_m = re.search(r"接口地址[\s\S]{0,300}?(https?://[^\s`\"'）)、,，]+)", text)
    endpoint = ep_m.group(1) if ep_m else (URL_RE.search(text).group(0) if URL_RE.search(text) else "")

    req_fields, resp_fields = [], []
    _saw_resp_heading = False     # 是否有表被标题明确判为响应侧（决定是否启用对半劈兜底）
    _subs = []                    # 子对象表（不展开为顶层字段，只登记结构）
    _warn_no_heading_table = []  # 【W47】无标题表格留痕
    _warn_json_blocks = []       # 【W46】畸形 JSON 块留痕
    _warn_missing_required_col = []  # 请求表未识别到必填列的留痕
    _warn_resp_side = []             # 响应侧按成功/失败拆分后的留痕
    _warn_skipped_table = []         # 说明性表格（无参数名列）被跳过
    _warn_other_action = []          # 属于其他动作号的参数表被跳过
    _self_check = []                 # 规则抽取后的机械自检（只留痕，不覆盖任何值）
    _deferred = []                   # 像参数表却拿不准归属的表格，兜底时再用

    def _absorb(t, is_req, is_resp, is_fail):
        """把一张表的行并入请求/响应字段（原地追加）。"""
        _req_idx, _req_matched = _required_col_index(t["header"])
        # 请求参数表二次校验：表头识别不到任何必填列写法时出声。否则整列字段被判
        # 非必填，必填缺失用例整批不生成且无告警（静默漏测）。
        if is_req and not is_resp and not _req_matched:
            _warn_missing_required_col.append(
                "请求参数表未识别到必填列（表头：%s），全部字段按非必填处理——"
                "必填缺失用例不会生成，请人工确认表头写法（支持 必填/必选/必传/required 等）"
                % "|".join(t["header"]))
        _t_fields, _t_req_vals = [], []
        for row in t["rows"]:
            f = _mk_field(row, t["header"])
            if not f:
                continue
            if is_req and not is_resp:
                req_fields.append(f)
            else:
                # 只给失败侧打标：成功侧是 response_fields 的默认语义，再写一个
                # "success" 键等于给每条契约无条件多一个默认键——那会让所有已批准
                # 套件的 cases_hash 失效（黄金对比测试专门盯这一点）。
                if is_fail:
                    f["resp_side"] = "fail"
                resp_fields.append(f)
            if is_req and not is_resp:
                _t_fields.append(f)
                _t_req_vals.append(_pick(row, _req_idx) if _req_idx is not None else "")
        if is_req and not is_resp and _t_fields:
            _self_check.extend(
                _self_check_table(_t_fields, t["header"], _t_req_vals, _req_idx, _req_matched))

    for t in _extract_tables(text):
        head = t["heading"]
        if _is_sub_object_heading(head):
            # 子对象表（condition / answer / answer_item …）：字段属于某个参数的
            # 内部结构，不是顶层参数。平铺进 request_fields 会让 B-MISS/B-TYPE
            # 围绕 q_index/opt/val 这类顶层不存在的键生成用例——服务端忽略未知
            # 参数后返回成功，于是被判成「校验缺失」假缺陷。这里只登记结构，
            # 让报告点名「该子对象需人工构造」，不参与顶层变异。
            _subs.append({"heading": head,
                          "fields": [f["name"] for f in
                                     (_mk_field(row, t["header"]) for row in t["rows"]) if f]})
            continue
        _head_action = _heading_action(head)
        if action and _head_action and _head_action != str(action):
            # 多接口合并文档：另一张参数表属于别的动作。并进本接口会让另一个接口的
            # 字段变成这里要发的请求参数（homepage_id 发给创建接口，服务端忽略未知
            # 参数后返回成功 → 被判「校验缺失」假缺陷）。
            _warn_other_action.append(
                "表格「%s」属于动作 %s，与本次解析的 action %s 不一致，已跳过——"
                "要测该动作请用 --action %s 单独解析"
                % (head, _head_action, action, _head_action))
            continue
        is_req = any(h in head for h in REQ_HINTS)
        is_resp = any(h in head for h in RESP_HINTS)
        is_fail = _is_fail_heading(head)
        if is_fail:
            # 失败侧一定是响应侧。注意标题可能只写「失败响应」而**不含**任何
            # RESP_HINTS 词（响应参数/响应字段/示例…），不显式置位就会落到
            # 「无标题表格」兜底里，被当成请求侧或按出现顺序乱劈。
            is_resp = True
        if is_resp:
            _saw_resp_heading = True
        if not (is_req or is_resp):
            # 标题没说归属时，先看表头有没有参数名列。文档里的说明性表格
            # （环境配置 / 覆盖率矩阵 / 产出物清单）没有这一列，把它们当参数表
            # 会让字段名变成「本地环境类」「A 鉴权权限」，用例连接口都打不中。
            if not _looks_like_param_table(t["header"]):
                _deferred.append(t)
                _warn_skipped_table.append(
                    "表格「%s」表头无参数名列（%s），判为说明性表格，已跳过、不产出字段"
                    % (head or "(无标题)", "|".join(t["header"])))
                continue
            # 无明确标题时按出现顺序兜底：先请求后响应
            is_req = not resp_fields and not req_fields
            # 【W47】无标题表格：对半劈/顺序猜都会错劈。留痕供报告点名，
            # 保守归请求——比劈错方向（响应字段进请求 → 全套假用例）安全。
            # 告警要自解释：只贴一行标题时读报告的人看不出发生了什么。
            _warn_no_heading_table.append(
                "表格「%s」标题未写明请求/响应侧，按出现顺序归为%s侧——请确认归属"
                % (head or "|".join(t.get("header") or []), "请求" if is_req else "响应"))
        _absorb(t, is_req, is_resp, is_fail)
    # 兜底：整份文档一张参数表都没认出来时，退回历史口径（第 1 张当请求、其余当响应）。
    # 目的是不让「表头写法没覆盖」变成静默产出空契约——那样每条用例都没有字段，
    # 比字段归属可疑更难查。退回去的同时必须点名，让人知道这份契约没被读懂。
    if _deferred and not req_fields and not resp_fields:
        for _i, _t in enumerate(_deferred):
            _absorb(_t, _i == 0, _i != 0, False)
        _warn_skipped_table.append(
            "全文档未识别出参数表（表头都没有参数名列），已按历史口径把第 1 张表当请求表、"
            "其余当响应表（%d 张）——字段归属不可信，请核对契约表头后重新解析"
            % len(_deferred))
    # 兜底：若全被判成请求，且存在两张表，则第二张视为响应。
    # 只在「没有任何表被标题识别为响应侧」且请求字段够多时才劈：字段只有 1 个时
    # half=0 会把唯一的字段整个劈到响应侧，请求侧变空——所有请求用例一起消失。
    if (resp_fields == [] and len(req_fields) >= 2 and not _saw_resp_heading
            and text.count("|参数|") >= 2):
        half = len(req_fields) // 2
        resp_fields = req_fields[half:]
        req_fields = req_fields[:half]
        # 【W47】劈了就必须出声：这个劈分是硬猜，报告必须点名让人工确认
        _warn_no_heading_table.append(
            "无标题表格按 |参数| 出现次数对半劈分（请求 %d 字段 / 响应 %d 字段）——请人工确认归属"
            % (len(req_fields), len(resp_fields)))

    _warn_loc_conflict = []  # 同名参数跨位置声明（合并时丢位置，必须点名）

    def _dedup_fields(fields, side_aware=False):
        """同名字段合并：ShowDoc 契约常在主表之外再贴一张「子对象/查询条件」表，
        两张表字段大量重叠。不去重会让 B-MISS/B-TYPE 对同一字段生成两遍用例、
        check_key 分母重复计数，报告里的字段数也虚高。合并口径取**更严**的一侧
        （任一处判必填即必填，约束取先出现的非空值），不丢信息。

        ``side_aware`` 用于**响应侧**：成功响应表与失败响应表常重复列举同名字段
        （errormsg / result / code），但两侧的「必填」是两回事——失败侧必填不代表
        成功响应里该字段有值。按 (name, resp_side) 去重，同侧取更严、跨侧不合并；
        跨侧合并会把失败侧的 required 升级到成功侧，成功用例因此断言不存在的字段。

        唯一的例外是**位置**：同名字段声明在不同位置时，合并只能保留先出现的那个，
        另一处永远发不出去。这里不强行拆成两个字段（扁平报文放不下同名两值），
        但必须留痕——否则执行期只剩一处位置，看起来像「契约就这么写的」。
        """
        seen, out = {}, []
        for f in fields:
            nm = f.get("name")
            key = (nm, f.get("resp_side") or "") if side_aware else nm
            if key in seen:
                prev = seen[key]
                _prev_loc = str(prev.get("location") or "body").strip().lower()
                _this_loc = str(f.get("location") or "body").strip().lower()
                if _prev_loc != _this_loc:
                    _warn_loc_conflict.append(
                        "参数 %r 同时声明在多个位置（%s / %s）：合并后只保留 %s，另一处"
                        "不会出现在请求里——请把同名参数改名，或修正表格里的位置列"
                        % (nm, _prev_loc, _this_loc, _prev_loc))
                prev["required"] = bool(prev.get("required")) or bool(f.get("required"))
                for k in ("max_len", "min_len", "enum", "default", "forbid"):
                    if prev.get(k) in (None, "", []) and f.get(k) not in (None, "", []):
                        prev[k] = f[k]
                continue
            seen[key] = f
            out.append(f)
        return out

    req_fields = _dedup_fields(req_fields)
    resp_fields = _dedup_fields(resp_fields, side_aware=True)
    # 失败侧字段从成功契约里拆出：成功用例只断言成功响应真有的字段。
    # 只在「确实识别到失败侧」时才拆——没有任何侧别线索的契约（老契约、纯 JSON
    # 契约）保持全量进 response_fields，与改造前逐字节一致，历史基线不受影响。
    failure_fields = [f for f in resp_fields if f.get("resp_side") == "fail"]
    if failure_fields:
        resp_fields = [f for f in resp_fields if f.get("resp_side") != "fail"]
        _warn_resp_side.append(
            "响应侧按标题拆分为成功 %d / 失败 %d 个字段：失败侧必填字段不进成功用例的"
            "契约断言（errormsg/errorcode 这类字段只在失败响应出现或只在失败侧必填）"
            % (len(resp_fields), len(failure_fields)))

    # 【W46】示例归属按「最近标题」而非纯下标：块前最近的 请求/响应 关键词决定归属。
    # _extract_json_blocks 同趟收集位置（旧实现挂函数属性从未被写入——死代码）。
    # 【审查修复】畸形 JSON 块同趟留痕进 _warn_json_blocks（此前声明后从未被写入）。
    _pos = []
    blocks = _extract_json_blocks(text, _with_pos=_pos, _warn=_warn_json_blocks)
    req_ex, resp_ex = {}, {}
    for start, blk in _pos:
        seg = text[max(0, start - 400):start].lower()
        side = _nearest_example_side(seg)
        if side == "resp" and not resp_ex:
            resp_ex = blk
        elif side == "req" and not req_ex:
            req_ex = blk
        elif not req_ex and not resp_ex:
            # 归属不明：按出现顺序兜底（先到先得），不静默丢弃
            req_ex = blk
    # 回退：带标题归属一无所获时按旧下标约定（第 1 块请求、第 2 块响应）
    if not req_ex and not resp_ex:
        req_ex = blocks[0] if len(blocks) > 0 else {}
        resp_ex = blocks[1] if len(blocks) > 1 else {}
    _sub_warnings = []
    if _subs:
        # 子对象表不进顶层字段，但必须点名：否则「这些字段为什么没被测」无从解释，
        # 人工补用例时也不知道该往哪个参数的内部结构里填。
        _sub_warnings.append(
            "子对象表未展开为嵌套结构（%s）：其字段不参与顶层变异，需人工按父参数构造"
            % "、".join(s["heading"] for s in _subs))
    _all_warnings = (list(_warn_other_action) + list(_warn_skipped_table)
                     + list(_warn_missing_required_col) + list(_warn_no_heading_table)
                     + list(_warn_json_blocks) + _sub_warnings + list(_warn_loc_conflict)
                     + list(_warn_resp_side))
    for _f in req_fields:
        if _f.get("location_unknown"):
            _all_warnings.append(
                "参数 %s 的「位置」取值「%s」未识别，按请求体处理——若它实际在 "
                "query/header/path，该参数等于没测到" % (_f.get("name"), _f["location_unknown"]))
    # 多页合并文件（kb_load_contract --allow-merge 或人工拼接）在这里会露馅：
    # 各页的请求表/响应表被当成同一个接口的字段，第二个接口的字段会变成第一个
    # 接口的断言前提。上游虽然已在写出前拦截，但人工拼接的文件仍可能进来，
    # 所以解析侧再认一次横幅，确保坏文件不会「静默可用」。
    if "页契约合并" in text and "<!--" in text:
        _all_warnings.append(
            "本文件是多页契约的合并产物（检测到合并横幅）：各页字段被当作同一接口解析，"
            "断言会引用其他接口的字段，请按接口拆分为单契约文件后再生成用例")
    if _all_warnings:
        import sys as _sys
        _sys.stderr.write("[contract_parser] %s：%s\n" % (
            action or name or "未命名接口",
            "；".join(str(w) for w in _all_warnings[:4])))
    if _self_check:
        import sys as _sys
        _sys.stderr.write("[contract_parser][自检] %s：%d 条可疑项（不覆盖原值，请人工确认）\n"
                          % (action or name or "未命名接口", len(_self_check)))
        for _sc in _self_check[:4]:
            _sys.stderr.write("   - [%s] %s\n" % (_sc.get("check"), _sc.get("message")))
    result = {
        "action": action, "name": name, "endpoint": endpoint,
        "request_fields": req_fields, "response_fields": resp_fields,
        "request_example": req_ex if isinstance(req_ex, dict) else {},
        "response_example": resp_ex if isinstance(resp_ex, dict) else {},
        # 【W46/W47】解析告警随产物透出（报告消费方按 parse_warnings 点名）。
        # 不截断：截断会让「真实 8 条」变成 6 条，而调用方再按结构累加就出现
        # 「6×2=12 条」这种既不完整又重复的数字，排查时容易误判成两批问题。
        "parse_warnings": [str(w) for w in _all_warnings],
        # 子对象表（condition / answer / answer_item）：只登记结构，不进顶层字段。
        "sub_objects": _subs,
        # 自检结论独立一栏：与 parse_warnings（解析降级/归属告警）语义不同，
        # 这里是「规则抽出来了但自己没把握」的可疑项，供报告点名、不覆盖原值。
        "self_check": _self_check,
        "source": source, "format": "showdoc",
    }
    # 失败侧字段只在真的拆出来时才落盘：没有失败响应表的契约产物必须与改造前
    # 逐字节一致，否则历史 cases_hash / 契约哈希整批失效（同下方 method 的处理口径）。
    if failure_fields:
        result["failure_fields"] = failure_fields
    # 请求形态只在「非默认」时才写：纯 POST + JSON 契约的产物必须与改造前逐字节一致，
    # 否则历史 cases_hash 与回归基线会整批失效。
    _method = _extract_method(text)
    if _method and _method != "POST":
        result["method"] = _method
    _kind, _ct, _fmt_warn = _extract_body_format(text)
    if _fmt_warn:
        result.setdefault("parse_warnings", []).extend(_fmt_warn)
    if _kind != "json":
        result["body_kind"] = _kind
        if _ct:
            result["content_type"] = _ct
        if _kind == "raw":
            _raw_field = _guess_raw_field(req_fields)
            if _raw_field:
                result["raw_body_field"] = _raw_field
            else:
                result.setdefault("parse_warnings", []).append(
                    "契约声明原始报文（%s），但请求参数表里没有唯一的 string 字段可承载整包——"
                    "无法确定 raw_body_field，执行时会按 JSON 文本发送" % (_ct or "text/plain"))
    return result


MAX_SCHEMA_DEPTH = 4  # 嵌套展开深度上限，防止病态契约拖垮生成


def _resolve_ref(spec, ref):
    """解析 $ref 指针（如 '#/components/schemas/Pet'、'#/definitions/Pet'）。"""
    if not (isinstance(ref, str) and ref.startswith("#/")):
        return {}
    node = spec
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return {}
    return node if isinstance(node, dict) else {}


def _deref(spec, node):
    """若 node 是 $ref 则解析为实际 schema（单层，调用方负责递归）。

    【W18 兑现兄弟键】Swagger 2.0 常见「{$ref, maxLength, enum, description}」写法：
    旧版只返回目标 schema，$ref 旁的兄弟约束键整体丢失 → 长度/枚举用例不生成、
    对账分母缺项。改为合并：兄弟键优先（局部声明覆盖引用目标）。
    """
    if isinstance(node, dict) and "$ref" in node:
        resolved = _resolve_ref(spec, node["$ref"])
        siblings = {k: v for k, v in node.items() if k != "$ref"}
        if siblings:
            merged = dict(resolved)
            merged.update(siblings)
            return merged
        return resolved
    return node if isinstance(node, dict) else {}


def _merge_alloff(schema, spec, depth=0):
    """【W19】展开 allOf：合并各分支的 properties/required（继承/混入拼装请求体在
    OpenAPI 3.x 极常见，旧版只读顶层 properties → 字段一个都解析不出且无告警）。
    oneOf/anyOf 语义无法机械判定主形态，不展开——留给 TODO 留痕（由 _schema_to_fields
    的 unknown_combo 标记）。"""
    if not isinstance(schema, dict) or depth > MAX_SCHEMA_DEPTH:
        return schema if isinstance(schema, dict) else {}
    branches = schema.get("allOf")
    if not isinstance(branches, list) or not branches:
        return schema
    merged = {k: v for k, v in schema.items() if k != "allOf"}
    props = dict(merged.get("properties") or {})
    req = list(merged.get("required") or [])
    for br in branches:
        br = _deref(spec, br)
        if not isinstance(br, dict):
            continue
        br = _merge_alloff(br, spec, depth + 1)
        for k, v in (br.get("properties") or {}).items():
            props.setdefault(k, v)
        for r in br.get("required") or []:
            if r not in req:
                req.append(r)
    if props:
        merged["properties"] = props
    if req:
        merged["required"] = req
    return merged


def _schema_to_fields(schema, required_names=None, spec=None, prefix="", depth=0, seen=None):
    """把 JSON Schema 展开为字段列表。

    - 递归展开嵌套 object 与 array.items，字段名用点路径（data.items[].name）
    - 解析 $ref（spec 为整个契约文档；缺失时 $ref 按空 schema 处理）；$ref 兄弟键合并
    - allOf 展开合并（oneOf/anyOf 无法机械判定时产 unknown_combo 留痕）
    - depth/seen 双重防环：病态递归契约不会拖垮生成
    """
    spec = spec if isinstance(spec, dict) else {}
    seen = seen or set()
    required_names = required_names or []
    schema = _deref(spec, schema)
    schema = _merge_alloff(schema, spec)
    if depth == 0 and isinstance(schema, dict) and (
            "oneOf" in schema or "anyOf" in schema):
        # oneOf/anyOf：多形态无法机械判定「哪个分支是请求主形态」，静默跳过 = 整类漏测。
        # 顶部留一条 TODO 字段（不带真实字段语义，needs 由消费方识别），点名需人工确认。
        combo = "oneOf" if "oneOf" in schema else "anyOf"
        fields = [{
            "name": ("%s<%s_combo>" % (prefix, combo)) if prefix else "<%s_combo>" % combo,
            "type": "unknown", "desc": "契约使用 %s 多形态，需人工确认主形态后拆分字段" % combo,
            "required": False, "remark": "oneOf/anyOf 未自动展开（无法机械判定主形态）",
            "enum": [], "max_len": None, "min_len": None,
            "default": None, "forbid": [], "semantics": [],
        }]
        return fields
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    fields = []
    # 【W18 配套】body schema 本身是 scalar（string/integer + maxLength 等）且带参数名时，
    # 没有 properties 可展开——按单个字段产出（参数名取 body 参数名或 action），
    # 否则标量 body 的约束（最常见：字符串 + maxLength）整体不可见。
    if not props and isinstance(schema, dict) and schema.get("type") in (
            "string", "integer", "number", "boolean") and (
            schema.get("maxLength") or schema.get("minimum") is not None
            or schema.get("maximum") is not None or schema.get("enum")):
        cons = _parse_constraints(str(schema.get("description", "")), "")
        if "enum" in schema:
            cons["enum"] = [(e, str(e)) for e in schema["enum"]]
        if schema.get("maxLength") is not None:
            cons["max_len"] = schema["maxLength"]
        # 【审查修复·P1】OpenAPI 标准约束键全量落地：minLength/minimum/maximum
        # 此前被丢弃（scalar body 分支的触发条件认了 minimum/maximum，结果却不搬——
        # 名实不符比不支持更糟），下边界整类静默不测。
        if schema.get("minLength") is not None:
            cons["min_len"] = schema["minLength"]
        if schema.get("maximum") is not None:
            cons["max_value"] = schema["maximum"]
        if schema.get("minimum") is not None:
            cons["min_value"] = schema["minimum"]
        if schema.get("default") is not None:
            cons["default"] = schema["default"]
        fields.append({
            "name": (schema.get("x-body-param-name") or prefix.rstrip(".") or "body"),
            "type": str(schema.get("type", "")),
            "desc": str(schema.get("description", "")), "required": bool(required_names),
            "remark": str(schema.get("description", "")),
            "enum": cons["enum"], "max_len": cons["max_len"], "min_len": cons["min_len"],
            "min_value": cons["min_value"], "max_value": cons["max_value"],
            "default": cons["default"], "forbid": cons["forbid"], "semantics": [],
            "nullable": bool(schema.get("nullable")) or "null" in str(schema.get("type", "")).split(","),
        })
        return fields
    for k, v in props.items():
        ref = v.get("$ref") if isinstance(v, dict) else None
        v = _deref(spec, v)
        cons = _parse_constraints(str(v.get("description", "")), "")
        if "enum" in v:
            cons["enum"] = [(e, str(e)) for e in v["enum"]]
        if v.get("maxLength") is not None:
            cons["max_len"] = v["maxLength"]
        # 【审查修复·P1】属性分支同步搬 minLength/minimum/maximum（与 scalar body 一致）
        if v.get("minLength") is not None:
            cons["min_len"] = v["minLength"]
        if v.get("maximum") is not None:
            cons["max_value"] = v["maximum"]
        if v.get("minimum") is not None:
            cons["min_value"] = v["minimum"]
        if v.get("default") is not None:
            cons["default"] = v["default"]
        full = "%s%s" % (prefix, k)
        fields.append({
            "name": full, "type": str(v.get("type", "")), "desc": str(v.get("description", "")),
            "required": k in required_names, "remark": str(v.get("description", "")),
            "enum": cons["enum"], "max_len": cons["max_len"], "min_len": cons["min_len"],
            "min_value": cons["min_value"], "max_value": cons["max_value"],
            "default": cons["default"], "forbid": cons["forbid"], "semantics": [],
            # 【W21】nullable 双口径：OpenAPI 3.x 的 nullable / JSON Schema 的 type 含 "null"。
            # 下游 check_key / case_generator 都读 f.get("nullable")，解析侧不产出
            # 等于「显式 null 处理」整类漏测且分母少算、门禁恒过。
            "nullable": bool(v.get("nullable")) or "null" in str(v.get("type", "")).split(","),
        })
        # 嵌套展开：object 直接递归；array 展开 items 并加 [] 路径标记
        if depth < MAX_SCHEMA_DEPTH:
            if v.get("type") == "object" and isinstance(v.get("properties"), dict):
                guard = ref or id(v)
                if guard not in seen:
                    seen.add(guard)
                    fields += _schema_to_fields(v, v.get("required") or [], spec,
                                                prefix=full + ".", depth=depth + 1, seen=seen)
                    seen.discard(guard)
            elif v.get("type") == "array" and isinstance(v.get("items"), dict):
                items = v["items"]
                guard = items.get("$ref") or id(items)
                if guard not in seen:
                    seen.add(guard)
                    # 【W20】数组元素对象的必填约束必须传递：旧版硬编码 []，
                    # B-MISS 对数组内字段整类不生成、不进对账分母。
                    fields += _schema_to_fields(items, items.get("required") or [], spec,
                                                prefix=full + "[].", depth=depth + 1, seen=seen)
                    seen.discard(guard)
        elif v.get("type") in ("object", "array"):
            # 【W45 反静默截断】超过 MAX_SCHEMA_DEPTH 的嵌套字段被整枝砍掉且无声——
            # 那批字段既不用例也不进对账分母。stderr 出声 + 产物留痕（字段名带
            # <truncated> 标记，needs 由消费方识别），让缺口在报告点名。
            import sys as _sys
            _truncated = full + (".<truncated>" if v.get("type") == "object" else "[].<truncated>")
            _sys.stderr.write(
                "[contract_parser] 嵌套深度超过 %d，字段 %s 起被截断（该子树不用例、"
                "不进分母）——请人工确认是否需要拆层或提高上限\n" % (MAX_SCHEMA_DEPTH, _truncated))
            fields.append({
                "name": _truncated, "type": "unknown",
                "desc": "嵌套超过 %d 层被截断，需人工展开" % MAX_SCHEMA_DEPTH,
                "required": False, "remark": "深度截断留痕（W45）",
                "enum": [], "max_len": None, "min_len": None,
                "default": None, "forbid": [], "semantics": [], "nullable": False,
            })
    return fields


def _select_operation(spec, action):
    """按 operationId == action 选 operation，否则取第一个 path+method。"""
    paths = spec.get("paths", {}) if isinstance(spec, dict) else {}
    chosen = None
    for p, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for m, op in methods.items():
            if m.lower() not in ("get", "post", "put", "delete", "patch"):
                continue
            op = op if isinstance(op, dict) else {}
            if action and str(op.get("operationId", "")) == str(action):
                return p, m, op
            if chosen is None:
                chosen = (p, m, op)
    return chosen


def _pick_request_content(content):
    """按能力顺序挑一个请求体 content。返回 (body_kind, content_type, obj, note)。

    优先级 json > x-www-form-urlencoded > multipart（降级为 form 并留痕）> 其它（raw）。
    """
    if not isinstance(content, dict) or not content:
        return "json", "", {}, ""
    low = {str(k).lower(): k for k in content}
    if "application/json" in low:
        return "json", "", content[low["application/json"]] or {}, ""
    if "application/x-www-form-urlencoded" in low:
        key = low["application/x-www-form-urlencoded"]
        return "form", "application/x-www-form-urlencoded", content[key] or {}, ""
    if "multipart/form-data" in low:
        key = low["multipart/form-data"]
        return ("form", "application/x-www-form-urlencoded", content[key] or {},
                "契约的请求体是 multipart/form-data，本 skill 不支持文件上传，已按 "
                "x-www-form-urlencoded 降级发送：普通字段的校验类用例仍成立，"
                "文件字段必然失败，需人工单独验证")
    key = low.get("text/plain") or low.get("application/xml") or low.get("text/xml") \
        or next(iter(content))
    return "raw", str(key), content[key] or {}, ""


def _pick_response_content(content):
    """挑响应 content 并给出 response_kind。返回 (obj, kind)；无 content 时为 json。"""
    if not isinstance(content, dict) or not content:
        return {}, "json"
    low = {str(k).lower(): k for k in content}
    if "application/json" in low:
        return content[low["application/json"]] or {}, "json"
    key = next(iter(low))
    ctype = key
    if "xml" in ctype:
        kind = "xml"
    elif "html" in ctype:
        kind = "html"
    elif any(token in ctype for token in ("octet-stream", "image/", "pdf", "zip", "binary")):
        kind = "binary"
    else:
        kind = "text"
    return content[low[key]] or {}, kind


def parse_openapi(spec, action=None):
    """解析 OpenAPI 3.x / Swagger 2.0（dict）。

    - Swagger 2.0：body 参数取 parameters[in=body].schema，地址拼 schemes+host+basePath，
      $ref 指向 definitions
    - OpenAPI 3.x：requestBody.content，地址取 servers[0].url，$ref 指向 components
    - 两者共用 $ref 解析与嵌套递归展开
    """
    sel = _select_operation(spec, action)
    if not sel:
        return None
    path, method, op = sel
    is_v2 = "swagger" in spec

    # 请求字段：path/query/header 参数 + body
    req_fields = []
    params = list((spec.get("paths", {}).get(path, {}) or {}).get("parameters", []) or [])
    params += list(op.get("parameters", []) or [])
    body_schema, body_required = None, []
    _warn_cookie = []
    _warn_body = []
    for prm in params:
        prm = _deref(spec, prm)
        if not isinstance(prm, dict):
            continue
        if is_v2 and prm.get("in") == "body":
            # 【W18】body 参数名要带下去：scalar body 走 _schema_to_fields 的单字段
            # 分支时用它当字段名。schema 的 $ref 兄弟键由 _deref 合并保留。
            prm_name = str(prm.get("name") or "")
            body_schema = _deref(spec, prm.get("schema", {}))
            if prm_name and isinstance(body_schema, dict) and not body_schema.get("properties"):
                body_schema.setdefault("x-body-param-name", prm_name)
            body_required = (body_schema or {}).get("required", [])
            continue
        if str(prm.get("in") or "").strip().lower() == "cookie":
            # Cookie 参数在黑盒里无法可靠伪造（要走 Cookie 头且与浏览器语义冲突）。
            # 当成 body 字段发出去 = 服务端忽略未知键 + 用例照样"成功"，等于没测。
            _warn_cookie.append(str(prm.get("name") or ""))
            continue
        # Swagger 2.0 的 formData/query/path/header 参数统一走下方通用字段处理
        sc = _deref(spec, prm.get("schema", {})) or prm
        cons = _parse_constraints(str(prm.get("description", "")), "")
        if "enum" in sc:
            cons["enum"] = [(e, str(e)) for e in sc["enum"]]
        # 【审查修复·P2】Swagger 2.0 query/formData/path 参数的约束键此前被丢弃：
        # 非 body 参数的 maxLength/minLength/maximum/minimum 直接落在参数对象（或其
        # schema）上，旧代码只靠 description 正则——同一解析器 body 与参数两种口径
        # 不一致（body 会提取 maxLength，参数不会）。与 body 分支统一：两处都查、搬全。
        for _src in (sc, prm):
            if not isinstance(_src, dict):
                continue
            if cons["max_len"] is None and _src.get("maxLength") is not None:
                cons["max_len"] = _src["maxLength"]
            if cons["min_len"] is None and _src.get("minLength") is not None:
                cons["min_len"] = _src["minLength"]
            if cons["max_value"] is None and _src.get("maximum") is not None:
                cons["max_value"] = _src["maximum"]
            if cons["min_value"] is None and _src.get("minimum") is not None:
                cons["min_value"] = _src["minimum"]
        req_fields.append({
            "name": prm.get("name", ""), "type": str(sc.get("type", "")),
            "desc": str(prm.get("description", "")), "required": bool(prm.get("required")),
            "remark": str(prm.get("description", "")), "enum": cons["enum"],
            "max_len": cons["max_len"], "min_len": cons["min_len"],
            "min_value": cons["min_value"], "max_value": cons["max_value"],
            "default": cons["default"],
            "forbid": cons["forbid"], "semantics": [],
            "nullable": bool(sc.get("nullable")) or "null" in str(sc.get("type", "")).split(","),
        })
        _loc = {"query": "query", "header": "header", "path": "path",
                "formdata": "body", "body": "body"}.get(
                    str(prm.get("in") or "").strip().lower(), "body")
        if _loc != "body":
            req_fields[-1]["location"] = _loc
    body_kind, body_ct = "json", ""
    if not is_v2:
        _content = ((op.get("requestBody", {}) or {}).get("content", {}) or {})
        body_kind, body_ct, body, _body_note = _pick_request_content(_content)
        if _body_note:
            _warn_body.append(_body_note)
        body_schema = _deref(spec, body.get("schema", {}))
        # 【W19 配套】allOf 的 required 合并发生在 _merge_alloff 里，此处先展开一次，
        # 否则 required_names 取到的是合并前的顶层（allOf 分支里的 required 丢失）。
        body_schema = _merge_alloff(body_schema, spec)
        body_required = (body_schema or {}).get("required", [])
    if is_v2:
        _consumes = [str(c).lower() for c in (op.get("consumes") or spec.get("consumes") or [])]
        if any("x-www-form-urlencoded" in c for c in _consumes) or any(
                str((p or {}).get("in") or "").lower() == "formdata"
                for p in params if isinstance(p, dict)):
            body_kind, body_ct = "form", "application/x-www-form-urlencoded"
    if body_schema:
        req_fields += _schema_to_fields(body_schema, body_required, spec)

    # 响应字段：200（或第一个 2xx 码；键可能是字符串或整数）
    responses = op.get("responses", {}) or {}
    resp_obj = responses.get("200") or responses.get(200)
    if resp_obj is None:
        for code in sorted(str(c) for c in responses):
            if code.startswith("2"):
                resp_obj = responses.get(code)
                if resp_obj is None and code.isdigit():
                    resp_obj = responses.get(int(code))
                break
    resp_obj = _deref(spec, resp_obj) if isinstance(resp_obj, dict) else {}
    response_kind = "json"
    if is_v2:
        resp_schema = resp_obj.get("schema", {})
    else:
        rcontent, response_kind = _pick_response_content(resp_obj.get("content", {}) or {})
        resp_schema = rcontent.get("schema", {})
    resp_fields = _schema_to_fields(_deref(spec, resp_schema),
                                    (_deref(spec, resp_schema) or {}).get("required", []), spec)

    # 地址
    if is_v2:
        scheme = ((spec.get("schemes", []) or ["https"])[0])
        server = "%s://%s%s" % (scheme, spec.get("host", ""), spec.get("basePath", ""))
    else:
        server = ((spec.get("servers", []) or [{}])[0] or {}).get("url", "")
    result = {
        "action": action or str(op.get("operationId", "")), "name": str(op.get("summary", "")),
        "endpoint": server + path, "request_fields": req_fields, "response_fields": resp_fields,
        "request_example": {}, "response_example": {},
        "source": "", "format": "openapi", "method": method.upper(),
    }
    # 请求体格式与响应形态同样只写非默认值（纯 JSON 契约的产物保持与改造前一致）。
    if body_kind != "json":
        result["body_kind"] = body_kind
        if body_ct:
            result["content_type"] = body_ct
        if body_kind == "raw":
            _raw_field = _guess_raw_field(req_fields)
            if _raw_field:
                result["raw_body_field"] = _raw_field
            else:
                _warn_body.append(
                    "契约声明原始报文（%s），但参数表里没有唯一的 string 字段可承载整包——"
                    "执行时会按 JSON 文本发送" % (body_ct or "text/plain"))
    if response_kind != "json":
        result["response_kind"] = response_kind
        if not resp_fields:
            _warn_body.append(
                "响应为 %s 且未能解析出字段结构：K/Y 维度将无断言依据，只能做状态码级校验"
                % response_kind)
    if _warn_cookie:
        _warn_body.append("OpenAPI 的 cookie 参数无法在黑盒中伪造，已剔除：%s"
                          % "、".join(_warn_cookie))
    if _warn_body:
        result["parse_warnings"] = list(_warn_body)
    return result


def _load_yaml(raw, path):
    """解析 YAML 契约；缺 PyYAML 时明确报错（不静默降级到 showdoc 解析）。"""
    try:
        import yaml
    except ImportError:
        raise SystemExit("契约是 YAML 格式但缺少 PyYAML：pip install pyyaml（文件：%s）" % path)
    obj = yaml.safe_load(raw)
    return obj if isinstance(obj, dict) else None


_ALLOWED_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")
_ALLOWED_RESPONSE_KINDS = ("json", "text", "xml", "html", "binary")
_JSON_LOC_KEYS = ("location", "in", "位置", "参数位置", "param_location", "传参方式")


def _json_contract_profile(contract, source_obj):
    """归一 JSON 契约的请求形态，返回**只含非默认值**的增量键。

    JSON 契约的作者是我们自己（不是第三方文档），所以非法值当场 ValueError 而不是
    降级猜测：猜错的位置会让参数被静默发进请求体，服务端忽略未知键时用例照样"成功"。
    """
    action = str(contract.get("action") or "")
    fields = contract.get("request_fields")
    if not isinstance(fields, list):
        raise ValueError("契约 %s 的 request_fields 必须是数组" % (action or "<未命名>"))
    for field in fields:
        if not isinstance(field, dict):
            continue
        raw = ""
        for key in _JSON_LOC_KEYS:
            if field.get(key) not in (None, ""):
                raw = str(field[key])
                break
        for key in _JSON_LOC_KEYS:
            field.pop(key, None)
        loc = _norm_location(raw) if raw else "body"
        if raw and not loc:
            raise ValueError("契约 %s 字段 %s 的位置取值非法：%r（允许 body/query/header/path）"
                             % (action, field.get("name"), raw))
        if loc != "body":
            field["location"] = loc
    out = {}
    method = str(source_obj.get("method") or "").strip().upper()
    if method and method not in _ALLOWED_METHODS:
        raise ValueError("契约 %s 的 method 非法：%r（允许 %s）"
                         % (action, method, "/".join(_ALLOWED_METHODS)))
    if method and method != "POST":
        out["method"] = method
    kind = str(source_obj.get("body_kind") or "").strip().lower()
    if kind:
        if kind not in ("json", "form", "raw"):
            raise ValueError("契约 %s 的 body_kind 非法：%r（允许 json/form/raw）" % (action, kind))
        if kind != "json":
            out["body_kind"] = kind
    for key in ("content_type", "raw_body_field"):
        value = source_obj.get(key)
        if value:
            out[key] = str(value)
    rkind = str(source_obj.get("response_kind") or "").strip().lower()
    if rkind:
        # 与 method/body_kind 同口径：作者是我们自己，写错就当场报错。放到执行期才报，
        # 整轮会在装载时抛异常，前面几轮已经跑完的产物白等。
        if rkind not in _ALLOWED_RESPONSE_KINDS:
            raise ValueError("契约 %s 的 response_kind 非法：%r（允许 %s）"
                             % (action, rkind, "/".join(_ALLOWED_RESPONSE_KINDS)))
        if rkind != "json":
            out["response_kind"] = rkind
    if out.get("body_kind") in ("form", "raw") and "content_type" not in out:
        out["content_type"] = ("application/x-www-form-urlencoded" if out["body_kind"] == "form"
                               else "text/plain")
    return out


def load_contract(path, action=None):
    """按内容自动分派解析器。path 可为 .md / .json / .yaml / .yml / .txt。

    用 utf-8-sig：契约文件常由 Windows 工具导出并带 BOM，其余读取点（如命令行
    传入的契约、kb_load_contract 写出）都已按惯用 utf-8-sig/utf-8-sig 处理；
    这里若用裸 utf-8，BOM 会变成首字符 `\ufeff` 混进标题行，标题识别整体失效。
    """
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        raw = f.read()
    stripped = raw.lstrip()
    obj = None
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            obj = json.loads(stripped)
        except Exception as e:
            # 【审查修复·P2】坏 JSON 零提示降级：文件长得像 JSON 但解析失败（截断/
            # 编码损坏/手写逗号错），旧版静默落到 parse_showdoc——把 JSON 文本当
            # markdown 硬解析，产出 0 字段契约，后续"用例全是必填缺失"的排查无从
            # 下手。降级照旧（文本里可能真混着文档段落），但先打出第一现场。
            print("⚠️ 契约文件形如 JSON 但解析失败（%s: %s），降级按 showdoc 文档解析：%s"
                  % (type(e).__name__, e, path), file=sys.stderr)
            obj = None
    elif str(path).lower().endswith((".yaml", ".yml")) or re.match(r"^(openapi|swagger)\s*:", stripped):
        obj = _load_yaml(raw, path)
    if isinstance(obj, dict):
        if "paths" in obj or "openapi" in obj or "swagger" in obj:
            c = parse_openapi(obj, action)
            if c:
                c["source"] = path
                return c
        if "request_fields" in obj or "response_fields" in obj:
            c = {
                "action": str(obj.get("action", action or "")), "name": obj.get("name", ""),
                "endpoint": obj.get("endpoint", ""), "request_fields": obj.get("request_fields", []),
                "response_fields": obj.get("response_fields", []),
                "request_example": obj.get("request_example", {}),
                "response_example": obj.get("response_example", {}),
                # 配套接口声明（交叉验证用）：写进契约即可让生成器自动做真往返/分页校验，
                # 不必每次在命令行重复传 --read-action / --list-action。
                "read_action": obj.get("read_action"), "read_params": obj.get("read_params") or {},
                "list_action": obj.get("list_action"),
                "source": path, "format": "json",
            }
            c.update(_json_contract_profile(c, obj))
            return c
    return parse_showdoc(raw, source=path, action=action)
