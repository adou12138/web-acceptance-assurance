# -*- coding: utf-8 -*-
import json
import re
from pathlib import Path

# 敏感键词表（**单一来源**：文本正则与整键判定都由它拼出，避免两处各写一份后漂移）。
#
# 只收**高置信度凭据/个人数据**键。曾经踩过的教训（W59）：键名判定必须是整键匹配，
# 用子串搜会误伤 design / assign / signalPresent / subtoken 这类缺陷定位字段——
# 它们被打码后现场无法复现，收益为零、代价很大。所以：
#   * 「裸 session」「裸 pwd」这类可能是业务字段的一律不收；
#   * 只收带限定词的形态（session_id / access_token / private_key …）。
_SECRET_WORDS = (
    "sign", "token", "api[_-]?key", "cookie", "password", "passwd", "secret",
    # 凭据载体：Authorization 头与各类访问令牌
    "authorization", "access[_-]?token", "refresh[_-]?token", "id[_-]?token",
    "signature", "private[_-]?key", "access[_-]?key",
    # 会话与跨站防护凭证（不收裸 session：可能是业务「场次」字段）
    "session[_-]?(?:id|key|token)", "csrf[_-]?token", "xsrf[_-]?token",
    "csrf", "xsrf",
    # 个人数据（PII）：手机号与身份证
    "phone", "mobile", "id[_-]?card", "idcard",
)

# 敏感值匹配。注意两处易错点：
#   1. 字符类里的 `\s` 不能多转义成 `\\s`——那样匹配的是"字面反斜杠 + 字母 s"，
#      会把敏感值在第一个 s/S 处截断，导致 `REALSIGNVALUE123` → `***SIGNVALUE123`：
#      脱敏表面上做了，实际把**大半个密钥明文写进了证据包**。
#   2. `(?i)` 对字符类同样生效，所以字符类里不要放裸字母（放了就等于把它连大小写一起排除）。
#   3. 值必须**连同引号整体**捕获。旧版只吃非空白片段，于是
#      `{"Authorization": "Bearer eyJ..."}` 被脱成 `{"Authorization": "*** eyJ..."}`
#      ——Bearer 后面的真令牌原样留下，脱了个寂寞。
_SECRET = re.compile(
    r"(?i)([\"']?(?<![\w])(?:" + "|".join(_SECRET_WORDS) + r")(?![\w])[\"']?\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^,}\s\"']+)")

# 【W59】键名判定必须**整键匹配**。旧版用 re.search 在键里找敏感词子串，于是
#   design / assign / signalPresent / subtoken 这类普通字段全被打码（实测 {"design":"x"}
#   → {"design":"***"}）——它们恰恰是缺陷定位信息，打码后现场无法复现；而对早已落盘
#   的字符串型证据（本就已包含在 issues 里），这场"过度脱敏"没有任何收益。
_SENSITIVE_KEY = re.compile(
    r"(?i)^(?:%s)$" % "|".join(_SECRET_WORDS))

# 请求头形态的 Authorization（`Authorization: Bearer xxx` 不带引号、值里有空格），
# 上面的正则只能吃到 `Bearer` 一词，令牌会残留——这里是它的补漏。
_BEARER = re.compile(r"(?i)((?:authorization)\s*[:=]\s*)(?:bearer|basic)\s+\S+")


def _masked_placeholder(value_token):
    """按值的引号形态还原同形状的占位符（保留引号，只换内容）。"""
    if value_token.startswith('"'):
        return '"***"'
    if value_token.startswith("'"):
        return "'***'"
    return "***"


def _mask_value(value):
    """递归打码，返回**同形状的 Python 对象**（容器不再是字符串）。

    【W59】旧版在 dict/list 分支里对子值调用 redact（返回值已经是 json 文本）再整体
    json.dumps ⇒ 嵌套结构被二次转义：`request.data` 变成 `"{\\"a\\": \\"1\\"}"` 这种
    字符串套字符串，证据包里的请求体既不可读、也不能直接拿来复现，等于样本的证据价值
    被渲染层吃掉。现在只在最外层序列化一次。
    """
    if isinstance(value, dict):
        return {k: ("***" if _SENSITIVE_KEY.search(str(k)) else _mask_value(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_value(v) for v in value]
    if isinstance(value, str):
        # 顺序要紧：先补漏再走主正则。反过来的话主正则会把 `Bearer` 一词替换成
        # `***`，_BEARER 就再也匹配不到「scheme + 令牌」的形态，令牌原样留在产物里。
        out = _BEARER.sub(lambda m: m.group(1) + "***", value)
        return _SECRET.sub(lambda m: m.group(1) + _masked_placeholder(m.group(2)), out)
    return value


def redact_value(value):
    """脱敏并保留原始容器类型，供结构化证据消费者复用。"""
    return _mask_value(value)


def redact(value):
    """脱敏后返回 str（保持外部既有契约：write_bundle 直接 write_text）。

    容器先在 _mask_value 里逐层处理、最后 json.dumps 一次；非容器照旧走文本替换。
    """
    masked = redact_value(value)
    if isinstance(masked, (dict, list)):
        return json.dumps(masked, ensure_ascii=False)
    return str(masked) if not isinstance(masked, str) else masked


def write_bundle(out_dir, run_id, case_id, payload):
    path = Path(out_dir) / run_id
    path.mkdir(parents=True, exist_ok=True)
    target = path / ("%s.json" % case_id)
    target.write_text(redact(payload), encoding="utf-8")
    return str(target)
