# -*- coding: utf-8 -*-
"""公共参数（encode / appid / action / sign / ts）拼装规则的单一来源。

深度流程（api_test_runner）与冒烟（smoke_runner）共用同一套规则：
完整请求报文 = 公共参数 + action + 业务参数，且后传的业务参数覆盖公共参数。
独立脚本可用 complete_request_body() 补齐台账「请求Body」列，免于漏掉公共参数（陷阱 18）。

本模块同时是**公共参数键名**的单一来源：
  - DEFAULT_EXCLUDE_PARAM_NAMES  生成期排除集的缺省值（问卷星信封约定 7 键），
    pattern_engine.base_params 据此把信封参数剔出变异基线；
  - NAMED_PAYLOAD_KEYS           build_request_payload 以具名 kwargs 承载的信封键
    （ts 走 ts_value 动态生成），smoke_runner 的透传分流与 SojumpAdapter 的
    残缺请求防线都引用它，不再各自维护字面量。
其他项目报文约定不同时，通过 --exclude-params（可重复传，name 或 name=value）
追加/覆盖，或用排除参数配置文件（load_exclude_config，条目语法与 CLI 一致）；
两层合并语义见 resolve_exclude_entries。详见 case_generator._apply_exclude_params。

本模块只依赖标准库，不 import 任何运行器模块，避免循环依赖与导入期副作用。
"""

import json
import os
import re
import sys
import time

# 台账「请求Body」中 ts 的占位写法：ts 每次请求都不同，不写死具体取值。
TS_PLACEHOLDER = "<请求时unix时间戳>"

# 生成期排除集缺省值（问卷星报文约定）。键值全部外置配置（personal_config /
# WJX_* / smoke_config），这里只固定**键名**。
DEFAULT_EXCLUDE_PARAM_NAMES = ("encode", "appid", "action", "ts", "sign", "corpid", "sysid")

# build_request_payload 以具名 kwargs 承载的信封键（ts 经 ts_value 动态生成，
# 不从静态配置透传）。smoke_runner 的 passthrough 分流与站点适配器（本包未随附，
# 自装后其 REQUIRED_PUBLIC）都从这里派生，漂移由单测锁定。
NAMED_PAYLOAD_KEYS = ("encode", "appid", "sign", "ts")

# 引擎可注入的信封键全集（= 出厂缺省 7 键）。_apply_exclude_params 的 engine
# 分支据此判定「裸名排除的键是否由执行期注入」——语义与 DEFAULT_EXCLUDE_PARAM_NAMES
# 相同但独立命名：缺省集可以被 default.json 数据替换（方案乙），而「引擎能注入什么」
# 由执行期静态值决定，两者不该被同一个名字绑死。
ENGINE_INJECTABLE_KEYS = ("encode", "appid", "action", "ts", "sign", "corpid", "sysid")


def parse_exclude_params(entries, source="--exclude-params"):
    """解析排除参数条目 → 有序 dict {name: value_or_None}。

    每条形如 ``name``（不参与生成，值走契约默认/请求示例/引擎注入）或 ``name=value``
    （固定值，进每条用例的请求参数）。可重复传意味着值可以任意复杂——
    含 = 、逗号、JSON 数组均可（每条参数占一个 argv，不做任何拆分）；值两侧空白
    会被去掉，值内部的空白与符号原样保留。

    同名条目：**带值的覆盖前者**（包括先出现的裸名）；裸名只登记名字，不清空已有值
    （裸名 = 「不赋值」，不是「赋空值」）。
    空条目 / 无名条目 / 等号后为空（``name=``）一律抛 ValueError：``name=`` 的语义
    是「发送空串」，与本机制的「排除并固定一个值」不是一回事，静默照做会让数值字段
    报出与根因无关的类型错误、字符串字段整轮带着空串发出去。
    """
    if entries is None:
        return {}
    if isinstance(entries, str):
        entries = [entries]
    spec = {}
    for raw in entries:
        text = str(raw).strip()
        if not text:
            raise ValueError("%s 存在空条目，请检查逗号残留或多余空白" % source)
        if "=" in text:
            name, _, value = text.partition("=")
            name = name.strip()
            value = value.strip()
            if not name:
                raise ValueError("%s 条目 %r 缺少参数名（形如 name=value）" % (source, raw))
            if not value:
                raise ValueError(
                    "%s 条目 %r 的等号后没有值：`name=` 不表示「发送空串」，本轮拒绝生成。\n"
                    "  两条出路：\n"
                    "    ① 只想排除、不提供值 → 只写参数名 %r（值依次取请求示例 / 契约默认值；"
                    "信封键由执行期引擎注入）；\n"
                    "    ② 确实要固定为空串 → 在契约参数说明或请求示例里把 %r 写成空串，"
                    "再只写参数名 %r。"
                    % (source, raw, name, name, name))
            _warn_if_multi_entry(raw, value, source)
            spec[name] = value
        else:
            # 裸名只登记，不清空已登记的固定值：CLI 写一个裸名就把配置侧的值抹掉，
            # 会让「只想确保它被排除」变成「值没了」，随后要么整轮报错、要么改用
            # 请求示例里那个多半属于别的环境的旧值。
            spec.setdefault(text, None)
    return spec


# 「一条里写了多个条目」的判据：逗号后紧跟 `标识符=`。
# 只用于**告警**，绝不自动拆分——值里带逗号与等号是明确支持的能力（JSON 值就会
# 出现 `{"a":1,"b":2}`），自动拆等于静默改写请求内容。JSON 用冒号不用等号，不命中。
_MULTI_ENTRY_RE = re.compile(r"[,，]\s*[^\s,，=]{1,40}\s*=")
_MULTI_ENTRY_WARNED = set()


def _warn_if_multi_entry(raw, value, source):
    """值形如「逗号后又是 name=」时提示可能把多条写进了一条（去重后只报一次）。"""
    if raw in _MULTI_ENTRY_WARNED or not _MULTI_ENTRY_RE.search(value):
        return
    _MULTI_ENTRY_WARNED.add(raw)
    sys.stderr.write(
        "[common_params] ⚠️ %s 条目 %r 的值形如「逗号后又是 name=」，疑似把多条写进了一条。\n"
        "  本工具不拆分（值里的逗号与等号是合法的，拆了会静默改请求内容）：\n"
        "  如确是多条，请重复传参（配置里请拆成多个数组元素）；如这条值本就含逗号，忽略本提示。\n"
        % (source, raw))


def load_exclude_config(path):
    """读排除参数配置文件 → 条目列表（条目语法与 --exclude-params 完全一致）。

    文件形态：``{"exclude_params": ["name", "name=value", ...]}``；
    以 ``_`` 开头的键（如 ``_readme``）一律忽略，供示例文件写说明用。
    结构不对/条目非法 → ValueError 点名文件路径，不静默吞。
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except ValueError as e:  # JSON 解析失败（含文件不存在以外的读错误由调用方兜）
        raise ValueError("排除参数配置 %s 不是合法 JSON：%s" % (path, e))
    if not isinstance(data, dict) or not isinstance(data.get("exclude_params"), list):
        raise ValueError("排除参数配置 %s 结构不对：应为 {\"exclude_params\": [条目, ...]}"
                         % path)
    out = []
    for raw in data["exclude_params"]:
        text = str(raw).strip()
        if not text:
            raise ValueError("排除参数配置 %s 存在空条目" % path)
        out.append(text)
    # 配置通道与 CLI 同口径：无名条目、等号后为空都在这儿拦下，文案带文件路径。
    # 不校验的话，错误会推迟到「基底+CLI 合并后重解析」时才抛，而那条文案只会说
    # `--exclude-params`——配置根本不是 CLI 来的，用户会去翻命令行。
    parse_exclude_params(out, source="排除参数配置 %s" % path)
    return out


def safe_project_name(name):
    """项目名用于拼接配置文件名（exclude_params_<project>.json），必须过安全校验。

    拒绝路径分隔符、``..``、空白与空串——project 来自 CLI/环境变量，
    拼进路径前不校验就是路径穿越入口。
    """
    text = str(name or "").strip()
    if not text or os.path.basename(text) != text or not re.fullmatch(r"[A-Za-z0-9._\-]+", text):
        raise ValueError("项目名不合法：%r（仅允许字母/数字/点/下划线/连字符，"
                         "不含路径分隔符与空白）" % name)
    return text


_DEFAULT_ENTRIES_CACHE = None


def load_default_exclude_entries():
    """读出厂兜底排除集（assets/exclude_params.default.json），模块级缓存。

    这是排除集语义的**数据源**（方案乙：站点约定从代码常量降级为数据文件）。
    文件随仓库分发，缺失/非法 = 安装损坏，ValueError 点名——绝不静默返回空，
    否则 fresh clone 上信封参数会被当业务字段变异（假用例风暴）。
    """
    global _DEFAULT_ENTRIES_CACHE
    if _DEFAULT_ENTRIES_CACHE is None:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "assets", "exclude_params.default.json")
        try:
            _DEFAULT_ENTRIES_CACHE = load_exclude_config(path)
        except OSError:
            raise ValueError("安装损坏：缺少出厂排除参数文件 %s" % path)
    return list(_DEFAULT_ENTRIES_CACHE)


def resolve_exclude_entries(cli_entries=None, config_path=None,
                            assets_dir=None, project=None):
    """解析有效排除集：四级回退取**基底清单**（替换语义），CLI 只追加。

    基底 = 四级中**第一个存在**的文件（层与层之间不合并——替换语义）：
      1. config_path   显式指定（缺失即 ValueError，不给回退）
      2. <assets_dir>/exclude_params_<project>.json   项目级（project 合法时）
      3. <assets_dir>/exclude_params.json             通用/单项目约定
      4. <assets_dir>/exclude_params.default.json     出厂兜底（缺失=安装损坏）
    CLI 条目追加在基底之后交给 parse_exclude_params：名字并集只增不减、
    同名条目**带值的覆盖基底值、裸名不清空基底值**（裸名只声明「不围绕它生成」）。
    返回 (条目列表, 装载说明)。
    """
    if config_path:
        if not os.path.isfile(config_path):
            raise ValueError("指定的排除参数配置不存在：%s" % config_path)
        base_path, layer = config_path, "explicit"
    else:
        candidates = []
        proj = safe_project_name(project) if project else None
        if proj and assets_dir:
            candidates.append((os.path.join(assets_dir, "exclude_params_%s.json" % proj),
                               "project"))
        if assets_dir:
            candidates.append((os.path.join(assets_dir, "exclude_params.json"), "local"))
        candidates.append((os.path.join(assets_dir or ".", "exclude_params.default.json"),
                           "default"))
        base_path, layer = None, None
        for path, lv in candidates:
            if os.path.isfile(path):
                base_path, layer = path, lv
                break
        if base_path is None:
            raise ValueError("安装损坏：找不到任何排除参数基底文件（最后尝试：%s）"
                             % candidates[-1][0])
    base_entries = load_exclude_config(base_path)
    merged = list(base_entries) + list(cli_entries or [])
    # 裸名只声明排除、不带值：配置侧同名条目若已有值，值仍按配置——这里出声，避免
    # 「我明明在命令行写了一遍却没生效」与「配置里那行是不是被覆盖了」两种误解。
    if cli_entries:
        _base_spec = parse_exclude_params(base_entries,
                                          source="排除参数配置 %s" % base_path)
        _cli_spec = parse_exclude_params(cli_entries)
        _kept = [n for n, v in _cli_spec.items()
                 if v is None and _base_spec.get(n) is not None]
        if _kept:
            sys.stderr.write(
                "[common_params] 提示：命令行里的裸名 %s 只声明「不围绕它生成」，"
                "不带值；配置侧已有的值保持不变（%s）。\n"
                % ("、".join(_kept),
                   "、".join("%s=%r" % (n, _base_spec[n]) for n in _kept)))
    parts = ["%s:%s(%d条)" % (layer, base_path, len(base_entries))]
    if cli_entries:
        parts.append("cli(%d条)" % len(cli_entries))
    return merged, "；".join(parts)


def load_payload_hook(path):
    """装载动态签名钩子（--payload-hook），返回模块；失败抛 ValueError。

    装载点原本在 flow.py 与 case_runner.py 各抄一份，两处的错误处理与提示会各自
    漂移（实测一处改了另一处没改）。收成单一来源后，两个入口行为必然一致。

    钩子是用户提供的 Python 文件，会在本进程内 exec_module——等同任意代码执行，
    这是设计选择（动态签名必须能按业务参数现算）。因此失败必须**可读地报错并
    终止**：文件不存在/语法错误/缺少 compute_envelope 时若继续跑，整轮请求都会
    缺签名，失败原因会被误读成接口缺陷。
    """
    import importlib.util
    if not os.path.isfile(path):
        raise ValueError("--payload-hook 文件不存在：%s" % path)
    try:
        spec = importlib.util.spec_from_file_location("wjx_payload_hook", path)
        if spec is None or spec.loader is None:
            raise ValueError("--payload-hook 无法作为 Python 模块装载：%s" % path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("--payload-hook 装载失败（%s: %s）：%s"
                         % (type(exc).__name__, exc, path))
    if not callable(getattr(mod, "compute_envelope", None)):
        raise ValueError("--payload-hook 模块缺少可调用的 compute_envelope("
                         "action, business_params, personal)：%s" % path)
    return mod


def generate_ts():
    """当前 unix 时间戳（字符串），供需要动态 ts 的调用方复用。"""
    return str(int(time.time()))


def build_request_payload(envelope=None, action_param="action", extra=None):
    """组装完整请求报文：信封参数 → 业务参数（后传覆盖前值）。

    envelope 是调用方解析好的 {信封键: 值}——**注入哪些键由调用方决定**
    （有效排除集 ∩ 契约声明），本函数不做任何键名假设：
    - 值为 None 视为未提供、不落键（空配置不会凭空塞入 null 字段）；
    - 键 ``"action"`` 特殊映射到 ``payload[action_param]``（接口标识键按
      各项目报文约定可改名）；envelope 不含 action 时报文里就没有它——
      这是「内部接口不需要公共参数」场景的门控落点。
    """
    payload = {}
    for key, value in (envelope or {}).items():
        if value is None:
            continue
        payload[action_param if key == "action" else key] = value
    payload.update(extra or {})
    return payload


def complete_request_body(body, *, encode=None, appid=None, sign=None,
                          ts_placeholder=TS_PLACEHOLDER, extra_common=None,
                          envelope_keys=None):
    """给「原始请求报文」（含 action 键）补齐**缺失**的公共参数，不覆盖已显式提供的值。

    extra_common：其他项目信封约定的额外公共键（如 {"token": "..."}），
    与 encode/appid/sign 同一补齐语义——只填缺失项、不覆盖已显式传的值。
    envelope_keys：本轮有效注入键集（doc["envelope_keys"]）。给定时只补
    清单内的键（清单外键不是信封、补了反而与生成期口径矛盾）；None = 旧行为。
    返回补齐后的新 dict，不修改入参；非报文（无 action 键）原样返回。
    """
    if not isinstance(body, dict) or "action" not in body:
        return body
    fillers = {"encode": encode, "appid": appid, "sign": sign, "ts": ts_placeholder}
    fillers.update(extra_common or {})
    if envelope_keys is not None:
        fillers = {k: v for k, v in fillers.items() if k in envelope_keys}
    merged = dict(body)
    for k, v in fillers.items():
        if k not in merged:
            merged[k] = v
    return merged
