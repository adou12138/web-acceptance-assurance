#!/usr/bin/env python3
"""attack_coverage.py — 攻击面覆盖与判据独立性检查（清单式验收的诊断与门禁）。

问题来源：2026-11-18 一次 154 项真实测试，143 项走正向路径、权限 0 项、异常 0 项、
组合 1 项，最终只产出 4 条 FAIL，其中 0 条来自为新攻击面设计的探针。原因是执行范式
是「覆盖确认」（确认功能可用）而非「失效构造」（证明它会坏）——用例数增长不等于
攻击面增长，而报告主指标是通过率，天然奖励"确认正常"。

本脚本只做三件事：
  1. 诊断：把台账摊成路径类型/判据来源/发现方式/失效假设的分布；
  2. 门禁：缺可证伪预测、判据拿不出独立通道时压结论上限；
  3. 反声明：字段填满不等于做过。surf_id 要与 plan 风险面核对、independent 要能
     指出独立通道且带证据、method 要落在方法库枚举内，否则不给"通过"。

**判据设计原则：考构造，不考配比。** 早期版本用「非正向路径 ≥30%」「方法 ≥3 种」
「designed_probe 占比」这类配额，实测可被标注操纵（改个标签就过关），而且
"如实记录未探索项"会把「通过」打成「待核实」——等于制度上劝阻诚实。现在改为：
每个高风险风险面至少要有一条可证伪预测（`hypothesis` + `hypothesis_outcome`），
「打破了几个假设」是产出而不是违规；密度、方法多样性、计划外发现全部只作披露。

纪律：
- 只按台账里**显式声明**的字段计算；字段缺失记 unknown 并列入缺口，不用启发式值
  充当机械判据（--infer 的粗分结果单列 inferred_*，仅供参考，不进 density）。
- 工具不判缺陷有无，只判"攻击面是否被真正打过"。
- 计划外风险面（surfaces_beyond_plan）计为**产出**：未知问题的定义就是"不在计划
  里"，罚它等于从制度上劝阻发现未知问题。但必须 amend 纳入后续计划，否则下一轮
  不会有假设，发现会被丢掉。

**两条独立校验轨道**：需求来源的 SURF 维持"可证伪预测"校验（hypothesis +
hypothesis_outcome）不变；攻击假设（ATK）走独立轨道——它允许模糊假设与人工验证
路径，不要求拆成单条预测。ATK 的验证状态为"未执行"时**不触发**待核实上限（允许
长期悬置，这是保留假设锐度的有意让步）；仅"命中"且影响面属功能/数据/权限时升级为
blocker/major 复核候选，若尚无复核记录则压待核实（须裁定，不是不许发现）。ATK 登记
时即为合法条目，不依赖事后 amend 纳入计划。

路径类型不是只有功能逻辑：界面与文案（ui_content）、易用性（usability）、无障碍
（accessibility）、数据一致性（data_consistency）、回归（regression）都是合法攻击
维度，与 boundary/exception 同等计入非正向路径。曾经因为只认功能五类，如实登记的
UI 类发现反而被压上限——那是制度性逆向激励。

用法：
    python scripts/attack_coverage.py --ledger ledger.jsonl
    python scripts/attack_coverage.py --ledger events.jsonl --plan adversarial-plan.json
    python scripts/attack_coverage.py --ledger ledger.csv --emit-json out.json --emit-csv out.csv
    python scripts/attack_coverage.py --ledger ledger.jsonl --residual-note "未破的攻击面…" --enforce
    python scripts/attack_coverage.py --ledger ledger.jsonl --atk atk-registry.json --reviews reviews.jsonl
"""

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

# 产物版本戳：归档复算时要能回答「这条结论是哪个判据版本判的」。
# v1.45 把「假设缺结果」从"不算未打破"收紧为缺口，同一份台账在旧版判「通过」、
# 新版判「待核实」——没有版本戳就无法区分"当时就是这么判的"与"新版本重判的"。
COVERAGE_SCHEMA = "attack-coverage/1"
# 判据口径版本：任何影响 verdict_ceiling / gaps / notices 的改动都要 +1。
# 只改输出字段（不影响判定）不 bump——版本号表达的是"判定规则变没变"。
# v3：并入 v1.46 线的判定变化（N6「解释或复打」例外、ATK 独立轨道、
# 多样性压档收紧）——同一份台账在 v2 与 v3 下可能得到不同 verdict_ceiling。
# v4：M1 证据等级门禁——ATK「未命中」新增 verified_by/attempt_id/evidence 字段；
# 推演/走读判「未命中」且影响面属功能/数据/权限时须解释或复打；practical 未命中
# 须挂 E1 执行记录或落盘证据。同一份台账在 v3 与 v4 下可能得到不同 verdict_ceiling。
TOOL_VERSION = "4"

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

# 假设结果四态。no_result 只在本脚本内作分类，不进台账枚举——
# 台账的 hypothesis_outcome 仍是三态（execution_ledger.HYPOTHESIS_OUTCOMES）。
# 缺结果单列的原因：没执行过 ≠ 假设成立，混进 held 会把未验证算成已验证。
HYPOTHESIS_NO_RESULT = "no_result"

# 判据缺口的两类（分开处理，不合并成一条）：
# - no_criterion：无判据占位——没声明独立通道，或通道只写了被测物自述；
# - no_evidence：缺真实执行证据——通道有，但拿不出可复核的证据路径。
ORACLE_GAP_CATEGORIES = ("no_criterion", "no_evidence")

# 可疑自然语言：只作提示，不压上限。合法的自然语言描述（"并发建卷时卷名重复应拒绝"）
# 不在此表——误伤合法描述会把如实登记变成违规。
SUSPICIOUS_PHRASES = ("应该没问题", "大概没问题", "似乎没问题",
                      "看起来没问题", "可能没问题", "暂时没问题", "应该正常",
                      "估计没问题", "基本上没问题")


SURF_ID_RE = re.compile(r"^SURF-[A-Za-z0-9][A-Za-z0-9_.:-]*$")
ATK_ID_RE = re.compile(r"^ATK-[A-Za-z0-9][A-Za-z0-9_.:-]*$")

# —— ATK 独立校验轨道 ——
# 攻击假设与需求来源的 SURF 是两条轨道，字段与判据都不同：ATK 允许模糊假设，
# 验证路径可以是人工作业、场景推演或代码走读，不要求脚本探针。
ATK_IMPACTS = ("功能", "数据", "权限", "体验", "其他")
ATK_STATUSES = ("未执行", "执行中", "命中", "未命中", "无法验证")
# 影响面属功能/数据/权限的命中才升级为严重度候选：体验类命中不压结论上限
ATK_SEVERITY_IMPACTS = ("功能", "数据", "权限")
ATK_REQUIRED = ("id", "hypothesis", "validation_path", "impact", "status")
# M1：ATK「未命中」的证据等级。verified_by 声明这条否定结论是怎么得来的——
# practical（实操探针）/ simulation（场景推演）/ code_walk（代码走读）/ unknown（旧台账缺省）。
# 缺省 unknown 不触发任何门禁：旧台账零改动兼容，不为"没填新字段"惩罚存量记录。
ATK_VERIFIED_BY = ("practical", "simulation", "code_walk", "unknown")
# 推演/走读属于"脑内否定"：拿它们判「未命中」时，否定结论没有可核对的对象，
# 影响面属功能/数据/权限时须解释（--replay-note 点名）或补实操探针。
ATK_UNSUPPORTED_NEGATIVE = ("simulation", "code_walk")
# practical 未命中可接受的证据等级：E1 是唯一合格等级；旧格式 FINISH 未设
# evidence_grade 按"历史兼容"处理，但降级为 notice 披露，区分"历史兼容"与"新写入规避"。
ATK_PRACTICAL_GRADE = "E1"
# ATK 严重度：与缺陷严重度同一套口径。缺省按影响面推定（功能/数据/权限 → major，
# 体验/其他 → minor），登记里显式给了就以登记为准——显式登记不被静默忽略。
ATK_SEVERITIES = ("blocker", "major", "minor")
ATK_DEFAULT_SEVERITY = {"功能": "major", "数据": "major", "权限": "major",
                         "体验": "minor", "其他": "minor"}

# 阈值依据：每个功能点在正向/边界/异常/权限/组合等路径中至少有一类非正向，
# 理论下限即 1/5=20%；取 30% 作为"每 3 条至少 1 条非正向"的最低门槛（非目标值）。
DEFAULT_DENSITY_THRESHOLD = 0.30
# 判据独立性：同源自证（用被测物自己的回显当 oracle）不得占多数
DEFAULT_INDEPENDENT_RATE = 0.30
# 风险面关联：至少一半的执行记录要能追溯到已登记失效假设
DEFAULT_SURF_LINKAGE = 0.50
# 方法多样性：记录数达此规模后，至少要出现的不同方法数（现仅作披露，不作门禁）
METHOD_DIVERSITY_MIN_RECORDS = 20
METHOD_DIVERSITY_MIN_METHODS = 3
# 失效假设：无 plan 时，记录数达此规模就至少要有一条可证伪预测。
# 这条替代了原先的 attack_density / method_low_diversity 配额——配额能被凑，
# 而「如果 X 则 Y 会坏」凑不出来：写了就得能被执行出来。
HYPOTHESIS_MIN_RECORDS = 20
# 全正向台账的判定门槛：达到此规模却一条非正向路径都没有，说明本轮没构造失效条件
NO_ATTACK_MIN_RECORDS = 5

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
        # 方案 A：不再把 evidence 的 dict 压成裸 path 字符串——压平会把 sha256
        # 当场丢掉，M1 的 practical 证据挂钩要做读时哈希复核就必须拿到完整结构。
        # 下游 _evidence_list() 同时兼容 dict 与字符串形态，既有消费不受影响。
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


def _oracle_gap_category(rec):
    """判据缺口的分类：no_criterion（无判据占位）/ no_evidence（缺真实执行证据）。

    分开处理的理由：两者的补法不同——前者要补一条真正的独立通道，
    后者要补可复核的证据路径。合成一条时使用方只会看到"判据不可证"，
    不知道该补什么。判据成立时返回 None。
    """
    channel = str(rec.get("oracle_channel") or "").strip()
    if not channel:
        return "no_criterion", "未声明 oracle_channel（独立通道是什么）"
    hit = [h for h in SELF_ATTEST_HINTS if h in channel]
    if hit:
        return "no_criterion", f"oracle_channel 只写了被测物自述（命中「{hit[0]}」），不是独立通道"
    if not _evidence_list(rec):
        return "no_evidence", "没有证据路径，独立判据无法复核"
    return None, None


def _suspicious_language(text):
    """可疑自然语言 → 返回命中的措辞，否则 None。

    只作提示，不压上限：措辞可疑不等于结论错。
    合法描述（"并发建卷时卷名重复应拒绝"）不命中，避免把如实登记变成违规。
    """
    value = str(text or "")
    for phrase in SUSPICIOUS_PHRASES:
        if phrase in value:
            return phrase
    return None


# —— 假设锐度质检（N5）——
# 锐度判「这条预测能不能被执行出来核对」，不判措辞漂亮与否。
# 合格信号满足其一即放行（正则从宽，宁漏勿误杀）：
# 1) 数值/长度；2) 角色/状态名；3) 条件组合；4) 对象 + 预期方向词。
_HYP_NUMERIC = re.compile(r"\d|字符|字数|长度|字节|KB|MB|GB")
_HYP_ROLE_STATE = re.compile(r"角色|管理员|租户|用户|未登录|待审核|锁定|状态|权限|账号")
_HYP_COMBO = re.compile(r"并发|同时|重复|连续|交叉|多次|组合|成对|批量")
_HYP_DIRECTION = re.compile(r"拒绝|拦截|被拒|失败|消失|保留|一致|报错|提示|回滚|禁止|"
                            r"限制|为空|越权|回显|丢弃|清空|断开|拒绝提交")
_HYP_OBJECT = re.compile(r"条目|记录|名称|卷名|输入|数据|订单|用户|权限|页面|按钮|表单|"
                         r"字段|接口|文件|消息|列表|会话|缓存|租户|角色|账号|购物车|库存|"
                         r"余额|附件|标题|描述|链接|图片|金额|密码|验证码")


_CASE_DATA = None


def _case_data():
    """按文件路径加载同目录 case_data，不依赖 sys.path。

    本脚本会被别的门禁/台账按文件路径加载；裸 `import case_data` 在 sys.path
    未含 scripts 时会 ImportError，把整轮结论变成崩溃——导入失败等于丢掉全部
    覆盖信号，比多一层加载代价大得多。口径与 execution_ledger._sibling 一致。
    """
    global _CASE_DATA
    if _CASE_DATA is None:
        try:
            import case_data as module
        except ImportError:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "case_data", Path(__file__).with_name("case_data.py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        _CASE_DATA = module
    return _CASE_DATA


def _hypothesis_vague_reason(text):
    """假设不够锐利 → 返回原因；锐利 → None。

    复用 case_data 的占位/非承诺词表；合法自然语言假设（「并发建卷时卷名重复应拒绝」）
    必须通过——误伤如实登记的假设等于惩罚诚实自检。
    """
    cd = _case_data()
    has_placeholder, is_noncommittal = cd.has_placeholder, cd.is_noncommittal

    value = str(text or "").strip()
    if not value:
        return "假设为空"
    if has_placeholder(value):
        return "含占位/待补充词，不是可核对的预测"
    if is_noncommittal(value):
        return "整句为非承诺空话（正常/成功/无异常等）"
    if (_HYP_NUMERIC.search(value) or _HYP_ROLE_STATE.search(value)
            or _HYP_COMBO.search(value)):
        return None
    # 对象 + 预期方向词：「删除后条目应消失」「越界输入应被拒绝」这类必须放行
    if _HYP_DIRECTION.search(value) and _HYP_OBJECT.search(value):
        return None
    return "无可核对的取值/对象/状态，也没有明确预期方向"


def _hypothesis_outcome(rec):
    """失效假设的结果判定。

    返回 broken / held / blocked / no_result。
    **缺结果单列 no_result，不计 held**：没跑出结果不等于假设没被打破——
    混进 held 会让"未验证"冒充"已验证"，那正是这套门禁要防的事。

    优先级：显式 hypothesis_outcome > 由 status 派生 > 缺结果。
    """
    declared = str(rec.get("hypothesis_outcome") or "").strip().lower()
    if declared in ("broken", "held", "blocked"):
        return declared
    status = str(rec.get("status") or "").strip().upper()
    derived = {"FAIL": "broken", "BLOCKED": "blocked", "PASS": "held"}.get(status)
    return derived or HYPOTHESIS_NO_RESULT


def _load_atk(path):
    """ATK 登记文件：数组，或 {"assumptions"/"atk"/"entries": [...]}。

    ATK 走独立文件而非台账字段——台账的 attack 字段是白名单校验，塞入 ATK 专用
    字段会破坏既有契约；ATK 也不需要进入执行台账，它登记时即为合法条目。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        for key in ("assumptions", "atk", "entries"):
            if isinstance(data.get(key), list):
                return data[key]
        raise ValueError(f"{path}: 对象形态须含 assumptions/atk/entries 数组")
    if isinstance(data, list):
        return data
    raise ValueError(f"{path}: ATK 登记必须为数组或含数组的对象")


def _plan_check_state(plan_surfaces):
    """本轮是否真的做了「与 plan 交叉核对」。

    三态而非布尔：`surf_plan_checked` 只说明「传入了风险面集合」，
    空集合也会为真——那等于没核对却声称核对过。区分：
    - not_provided：未提供 plan，无从核对；
    - empty：提供了 plan 但风险面集合为空，等同未核对；
    - checked：提供了非空风险面集合且完成交叉核对。
    """
    if plan_surfaces is None:
        return "not_provided"
    if not plan_surfaces:
        return "empty"
    return "checked"


def _residual_state(residual_note, plan_surfaces, atk_entries, per_surf):
    """残余风险声明的可核对状态（供门禁侧判定，不在此处压上限）。

    三态：
    - absent：未提供声明；
    - unstructured：提供了但无法机械对应到任何已登记风险面或攻击假设；
    - anchored：提供了且至少引用了一个已登记编号。

    结构化声明（dict/list）不走本函数：它的逐条核对由 _parse_residual_declaration
    + _actual_residual_status 负责（见 residual_declaration 字段）。
    """
    if isinstance(residual_note, (dict, list)):
        return "structured"
    text = str(residual_note or "").strip()
    if not text:
        return "absent"
    known = set(per_surf)
    for item in (atk_entries or []):
        if isinstance(item, dict):
            ident = str(item.get("id") or "").strip()
            if ident:
                known.add(ident)
    import re as _re
    referenced = set(_re.findall(r"(?:SURF|ATK)-[A-Za-z0-9_\-]+", text))
    if referenced and referenced & known:
        return "anchored"
    if referenced:
        return "unresolved_ref"
    return "unstructured"


# —— 残余风险声明：结构化三态（M5）——
# 旧文本（--residual-note 自由文本）只能判"有没有引用已登记编号"，判不了
# "这条声明说的是不是真的"。结构化声明把每条残余风险写成
# {id, status}，才能逐条核对：
# - id 必须在已登记清单里（否则是虚构编号）；
# - status 必须与该编号的实际验证状态一致（否则是关联错误）。
RESIDUAL_STATUSES = ("unbroken", "broken", "not_verified")
RESIDUAL_STATUS_LABELS = {"unbroken": "未破", "broken": "已破", "not_verified": "尚未验证"}


def _parse_residual_declaration(note):
    """把残余风险声明解析为结构化条目；不是结构化则返回 None。

    **何时可解析为结构化声明**（明确边界，避免"以为核过了"）：
    - 内容是 JSON 对象（含 items 数组）或 JSON 数组；
    - 内容是 JSON 文本（以 { 或 [ 开头且能解析成功）。
    其余一律回落为旧文本证据（自由文本），按 anchored/unstructured 判定，
    不做逐条核对——自由文本里写"SURF-01 未破"无法区分它指的是哪个观察。

    返回 (items, error)：items 为 [{id, status}]；解析失败时 error 非空。
    """
    if isinstance(note, (dict, list)):
        payload = note
    else:
        text = str(note or "").strip()
        if not text:
            return [], None
        if not text.startswith("{") and not text.startswith("["):
            return None, None
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return None, "残余风险声明形似结构化但无法解析为 JSON，按旧文本证据处理"
    if isinstance(payload, dict):
        raw = payload.get("items")
        if raw is None:
            return None, "结构化残余风险声明缺 items 数组"
        if not isinstance(raw, list):
            return None, "结构化残余风险声明的 items 必须是数组"
    elif isinstance(payload, list):
        raw = payload
    else:
        return None, "结构化残余风险声明必须是对象或数组"
    items, error = [], None
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            error = error or f"items[{index}] 不是对象"
            continue
        ident = str(entry.get("id") or "").strip()
        status = str(entry.get("status") or "").strip().lower()
        if not ident:
            error = error or f"items[{index}] 缺 id"
            continue
        if status not in RESIDUAL_STATUSES:
            error = error or (f"items[{index}].status 必须是 "
                              + "/".join(RESIDUAL_STATUSES) + f" 之一，实为 {status!r}")
            continue
        items.append({"id": ident, "status": status})
    return items, error


def _actual_residual_status(ident, per_surf, atk_entries):
    """编号的实际验证状态：unbroken / broken / not_verified；不在册则 None。

    判定依据只有已登记事实：
    - SURF 面：出现过 broken 假设 → 已破；有可采信验证且未破 → 未破；
      没打或验证不可采信 → 尚未验证。
    - ATK 条目：命中 → 已破；未命中 → 未破；未执行/执行中/无法验证 → 尚未验证。
    """
    for item in (atk_entries or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "").strip() != ident:
            continue
        status = str(item.get("status") or "").strip()
        if status == "命中":
            return "broken"
        if status == "未命中":
            return "unbroken"
        return "not_verified"
    bucket = per_surf.get(ident)
    if bucket is None:
        return None
    if bucket.get("broken", 0) > 0:
        return "broken"
    if bucket.get("supported", 0) > 0:
        return "unbroken"
    return "not_verified"


# —— M6：ATK 停滞披露 ——
# 「允许长期悬置」是刻意的让步，但长期悬置若一直不被看见，就退化成"登记了就算做过"。
# 提供上一可比批次的 ATK 快照后，同范围、同稳定 ID、仍未执行、验证路径无实质变化的
# 条目判为停滞——只披露，不改变上限（停滞本身不是缺陷，是过程事实）。
ATK_STAGNANT_STATUS = "未执行"


def _atk_scope_key(entry):
    """ATK 条目的"范围"键：同一稳定 ID + 同一验证路径才算同一件事。

    **旧 ID 被复用**（同一 ID 换了验证路径）不是停滞——那是换了一条假设，
    拿它说"停滞"是误判。
    """
    return (str(entry.get("id") or "").strip(),
            str(entry.get("validation_path") or "").strip())


def _find_stagnant_atk(current_entries, prev_entries):
    """找出停滞的 ATK：同范围同 ID、仍未执行、上一批也未执行。

    返回 (stagnant_ids, reason_by_id)：
    - 当前轮已执行的（状态不是「未执行」）→ 不算停滞（有进展）；
    - 上一批没有该 ID 同验证路径 → 不算停滞（新登记或换了路径，无从比较）；
    - 上一批该 ID 已执行过 → 不算停滞（曾经动过，不是持续悬置）。
    """
    if not prev_entries:
        return [], {}
    prev_by_key = {}
    for item in prev_entries:
        if not isinstance(item, dict):
            continue
        prev_by_key[_atk_scope_key(item)] = item
    stagnant, reasons = [], {}
    for item in current_entries or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "").strip() != ATK_STAGNANT_STATUS:
            continue
        key = _atk_scope_key(item)
        prev = prev_by_key.get(key)
        if prev is None:
            continue
        if str(prev.get("status") or "").strip() != ATK_STAGNANT_STATUS:
            continue
        ident = key[0]
        stagnant.append(ident)
        reasons[ident] = "上一可比批次同 ID 同验证路径同样未执行，本轮仍无进展（验证路径无实质变化）"
    return stagnant, reasons




def _normalise_atk_severity(value):
    """ATK 严重度归一：显式登记优先，否则按影响面推定。非法值回落到推定值。"""
    text = str(value or "").strip().lower()
    return text if text in ATK_SEVERITIES else None


def _normalise_atk(entries):
    """归一 ATK 条目并校验字段。返回 (归一列表, 错误列表)。"""
    normalised, errors = [], []
    seen = set()
    for index, item in enumerate(entries or []):
        name = f"atk[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{name}: 必须为对象")
            continue
        unknown = set(item) - set(ATK_REQUIRED) - {
            "linked_tc", "linked_ac", "severity", "note",
            "verified_by", "evidence", "attempt_id"}
        if unknown:
            errors.append(f"{name}: 含未知字段 {sorted(unknown)}")
            continue
        missing = [key for key in ATK_REQUIRED if key not in item]
        if missing:
            errors.append(f"{name}: 缺少必填字段 {missing}")
            continue
        ident = str(item["id"]).strip()
        if not ATK_ID_RE.match(ident):
            errors.append(f"{name}.id: 必须以 ATK- 前缀开头（实为 {ident or '缺失'}）")
            continue
        if ident in seen:
            errors.append(f"{name}.id: 重复的攻击假设 ID {ident}")
            continue
        seen.add(ident)
        hypothesis = str(item["hypothesis"]).strip()
        if not hypothesis:
            errors.append(f"{name}.hypothesis: 模糊假设不得为空")
            continue
        validation_path = str(item["validation_path"]).strip()
        if not validation_path:
            errors.append(f"{name}.validation_path: 验证路径不得为空（人工步骤/场景推演/代码走读均可）")
            continue
        impact = str(item["impact"]).strip()
        if impact not in ATK_IMPACTS:
            errors.append(f"{name}.impact: 必须为 {list(ATK_IMPACTS)} 之一")
            continue
        status = str(item["status"]).strip()
        if status not in ATK_STATUSES:
            errors.append(f"{name}.status: 必须为 {list(ATK_STATUSES)} 之一")
            continue
        verified_by = str(item.get("verified_by") or "unknown").strip().lower()
        if verified_by not in ATK_VERIFIED_BY:
            errors.append(f"{name}.verified_by: 必须为 {list(ATK_VERIFIED_BY)} 之一")
            continue
        attempt_id = str(item.get("attempt_id") or "").strip()
        evidence_raw = item.get("evidence")
        evidence = None
        if evidence_raw is not None:
            if not isinstance(evidence_raw, list) or not evidence_raw:
                errors.append(f"{name}.evidence: 须为非空数组（元素含 path 与 sha256）")
                continue
            bad = [i for i, ev in enumerate(evidence_raw)
                   if not isinstance(ev, dict) or not str(ev.get("path") or "").strip()
                   or not str(ev.get("sha256") or "").strip()]
            if bad:
                errors.append(f"{name}.evidence: 第 {bad} 项缺 path 或 sha256（落盘证据必须带哈希，"
                              "不是 Path.exists() 的放宽版）")
                continue
            evidence = [{"path": str(ev["path"]).strip(), "sha256": str(ev["sha256"]).strip()}
                        for ev in evidence_raw]
        normalised.append({
            "id": ident,
            "hypothesis": hypothesis,
            "validation_path": validation_path,
            "impact": impact,
            "status": status,
            "linked_tc": item.get("linked_tc"),
            "linked_ac": item.get("linked_ac"),
            "severity": _normalise_atk_severity(item.get("severity")),
            "note": str(item.get("note") or "").strip() or None,
            "verified_by": verified_by,
            "attempt_id": attempt_id or None,
            "evidence": evidence,
        })
    return normalised, errors


# —— M1：practical「未命中」的证据挂钩 ——
# 证据两级，优先级从高到低：首选 attempt_id 绑定台账 FINISH（E1 等级 + case_id 关联 +
# 读时哈希复核，复刻 execution_ledger 的读时校验）；降级为 evidence 落盘文件+哈希，
# 仅当该 ATK 确实不依附任何执行时使用（note 须写明为何没有 attempt_id）。
# 一句话解释（--replay-note）不能证明"做了实操"，故 practical 未命中不可豁免。
def _evidence_hash_ok(evidence):
    """落盘证据完整性：is_file() + sha256 重算比对，逐项全过才算合格。

    畸形输入（元素非对象、路径不可访问）一律判不合格，**不得抛异常**——
    门禁自身被脏台账打崩，比"证据不合格"严重得多：它会让整轮审查没有结论。
    """
    for item in evidence or []:
        if not isinstance(item, dict):
            return False
        try:
            path = Path(str(item.get("path") or ""))
            digest = str(item.get("sha256") or "").strip().lower()
            if not path.is_file():
                return False
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                return False
        except (OSError, ValueError):
            return False
    return True


def _practical_negative_evidence(entry, attempt_index):
    """核验 practical「未命中」条目的证据挂钩。

    返回 (state, detail)：state 取
    - "bound"        attempt_id 绑定台账 FINISH 且全部核验通过；
    - "bound_legacy" 绑定通过但 FINISH 为旧格式（未设 evidence_grade）——历史兼容，
                      降级为 notice 披露，区分"历史兼容"与"新写入规避"；
    - "file"         evidence 落盘证据核验通过（降级路径）；
    - None           无有效证据（detail 说明原因，供 gap 消息引用）。
    """
    attempt_id = str(entry.get("attempt_id") or "").strip()
    if attempt_id:
        event = attempt_index.get(attempt_id)
        if event is None:
            return None, f"attempt_id {attempt_id} 不在本轮台账 FINISH 事件中"
        status = str(event.get("status") or "").strip().upper()
        if status not in ("PASS", "FAIL"):
            return None, (f"attempt_id {attempt_id} 的执行未完成"
                          f"（status={event.get('status') or '缺失'}，非中途弃置才算执行完成）")
        grade_raw = event.get("evidence_grade")
        grade = str(grade_raw or "").strip().upper()
        # 旧格式判定只认「键缺失或 null」：execution_ledger 写入侧保证 evidence_grade
        # 要么不写、要么是合法 E1/E2/E3（非法值当场 raise）。键存在但为空串/乱值
        # 只能来自手工拼 JSON 绕过工具写入——那是"新写入规避"，不是历史兼容，
        # 不得洗成 notice 放行。
        legacy = "evidence_grade" not in event or grade_raw is None
        if not legacy and grade not in ("E1", "E2", "E3"):
            return None, (f"attempt_id {attempt_id} 携带无效证据等级 {grade_raw!r}"
                          "（合法值仅 E1/E2/E3；空值等同未携带，不得冒充旧格式）")
        if not legacy and grade != ATK_PRACTICAL_GRADE:
            return None, (f"attempt_id {attempt_id} 的证据等级为 {grade or '缺失'}，"
                          f"仅 {ATK_PRACTICAL_GRADE} 合格")
        evidence = event.get("evidence")
        if not isinstance(evidence, list) or not evidence or not _evidence_hash_ok(evidence):
            return None, f"attempt_id {attempt_id} 的证据文件缺失或哈希不符，无法复核"
        linked_tc = str(entry.get("linked_tc") or "").strip()
        if linked_tc and str(event.get("case_id") or "").strip() != linked_tc:
            return None, (f"attempt_id {attempt_id} 的 case_id={event.get('case_id')} 与 "
                          f"linked_tc={linked_tc} 不匹配（不得拿任意 E1 记录挂攻击面）")
        return ("bound_legacy" if legacy else "bound"), f"绑定台账执行记录 {attempt_id}"
    evidence = entry.get("evidence")
    if isinstance(evidence, list) and evidence and _evidence_hash_ok(evidence):
        return "file", "绑定落盘证据（降级路径：note 须写明为何没有 attempt_id）"
    return None, None


def _closed_review_ids(reviews):
    """从复核记录中取出「已裁定闭环」的 finding_id 集合。

    与 references/finding-triage.md 的候选状态机对齐：
    - `accepted`：判据成立、判定为产品侧问题。**tie_to_bug 为空视为未闭环**
      ——必须补建 BUG 并关联后才能解除门禁影响；
    - `rejected`：判定为误报/判据不成立，已定性，不再压上限；
    - `unresolved`：证据不足或判据缺失，**尚未裁定**，仍须压低上限。

    verdict 缺失或非法时按未闭环处理——不得因为"有一条记录"就当作已裁定。

    复核 JSONL 为追加写入（状态机单向推进），同一 finding 可能有多条记录；
    判定只认每条 finding 的**最新**记录（latest-wins），否则历史记录会永久
    决定结果——先 accepted 未闭环、后补 tie_to_bug 的候选会被误判闭环，
    翻案后的旧 rejected 也会盖住新裁定。与 adversarial_gate 侧的折叠口径一致。
    """
    latest = {}
    for item in reviews or []:
        if not isinstance(item, dict):
            continue
        finding_id = str(item.get("finding_id") or "").strip()
        if not finding_id:
            continue
        latest[finding_id] = item
    closed = set()
    for finding_id, item in latest.items():
        verdict = str(item.get("verdict") or "").strip()
        if verdict == "rejected":
            closed.add(finding_id)
        elif verdict == "accepted":
            # accepted 但未建 BUG/未关联 → 未闭环（与 execution_ledger 的
            # accepted+tie_to_bug 为空视为未闭环同一口径）
            if str(item.get("tie_to_bug") or "").strip():
                closed.add(finding_id)
    return closed


def analyse_atk(entries):
    """ATK 轨道的诊断与门禁判定。

    与需求 SURF 轨道的关键差异：
    - 「未执行」不触发待核实上限——允许长期悬置，这是保留假设锐度的有意让步；
    - 「命中」且影响面属功能/数据/权限 → 升级为严重度复核候选；
    - 其余状态（执行中/未命中/无法验证/体验类命中）只披露，不压上限。

    本函数只做登记侧诊断（不看复核记录），闭环判定在 analyse() 中进行——
    复核状态在此处不可见，否则子结论会与顶层结论自相矛盾。
    """
    normalised, errors = _normalise_atk(entries)
    counts = {status: 0 for status in ATK_STATUSES}
    for item in normalised:
        counts[item["status"]] += 1
    candidates = [item for item in normalised
                  if item["status"] == "命中" and item["impact"] in ATK_SEVERITY_IMPACTS]
    not_hit = [item["id"] for item in normalised if item["status"] == "未命中"]
    # M1：推演/走读判出的「未命中」单列——否定结论的验证方式必须可见，
    # 且影响面属功能/数据/权限时在 analyse() 中升级为证据义务。
    unsupported_negative = [item["id"] for item in normalised
                            if item["status"] == "未命中"
                            and item["verified_by"] in ATK_UNSUPPORTED_NEGATIVE
                            and item["impact"] in ATK_SEVERITY_IMPACTS]
    # M3 披露口径：ATK 未命中的验证方式分布（各方式 N 条，只披露不判定）。
    not_hit_verified_by = {vb: sum(1 for item in normalised
                                   if item["status"] == "未命中"
                                   and item["verified_by"] == vb)
                           for vb in ATK_VERIFIED_BY}
    # 目标导向补漏：「无法验证」此前完全不可见——把「未命中」改标成「无法验证」
    # 即可绕开 M1 的证据义务。只加披露、不加义务：让读者看得见这种转移。
    unable_verified_by = {vb: sum(1 for item in normalised
                                  if item["status"] == "无法验证"
                                  and item["verified_by"] == vb)
                          for vb in ATK_VERIFIED_BY}
    unexecuted = [item["id"] for item in normalised if item["status"] == "未执行"]
    return {
        "registered": len(normalised),
        "status_counts": counts,
        "hit": counts["命中"],
        "not_hit": len(not_hit),
        "not_hit_ids": not_hit,
        "not_hit_verified_by": not_hit_verified_by,
        "unable_verified_by": unable_verified_by,
        "unsupported_negative": unsupported_negative,
        "unexecuted": len(unexecuted),
        "unexecuted_ids": unexecuted,
        "severity_candidates": [
            {"id": item["id"], "impact": item["impact"],
             "severity": item["severity"] or ATK_DEFAULT_SEVERITY.get(item["impact"], "minor"),
             "reason": "攻击假设命中且影响面属功能/数据/权限，须裁定并闭环"}
            for item in candidates],
        "entries": normalised,
        "errors": errors,
        "scope": "只判 ATK 假设的登记完整性与命中升级，不判缺陷有无、不替代缺陷复核与门禁",
    }


def analyse(records, density_threshold=DEFAULT_DENSITY_THRESHOLD,
            independent_rate=DEFAULT_INDEPENDENT_RATE,
            residual_note=None, plan_surfaces=None, plan_risks=None,
            surf_linkage_threshold=DEFAULT_SURF_LINKAGE, amend_note=None,
            atk_entries=None, reviews=None, prev_atk_entries=None,
            atk_absent_reason=None, replay_notes=None):

    records = _normalize_events(records)
    total = len(records)
    # M1：FINISH 事件索引（attempt_id → 归一记录），供 practical 未命中的
    # attempt_id 绑定核验。归一记录保留 evidence 原始 {path, sha256} 结构（方案 A）。
    attempt_index = {}
    for rec in records:
        if isinstance(rec, dict) and rec.get("kind") == "FINISH":
            attempt_index[str(rec.get("attempt_id") or "")] = rec
    path_counts = {k: 0 for k in PATH_TYPES}
    path_unknown = 0
    inferred_counts = {k: 0 for k in PATH_TYPES}
    oracle = {k: 0 for k in ORACLE_SOURCES}
    modes = {k: 0 for k in DISCOVERY_MODES}
    surf_linked = 0
    surf_bad_format = []
    surf_new = []
    methods = {}
    declared_method_missing = 0
    method_unknown = []
    unproven = []
    oracle_gaps = {name: [] for name in ORACLE_GAP_CATEGORIES}
    probes_total = 0
    hyp_records = 0
    hyp_broken = 0
    hyp_held = 0
    hyp_blocked = 0
    hyp_no_result = 0
    hyp_no_result_ids = []
    hyp_without_surface = []
    hyp_vague = []
    hyp_vague_broken = []
    suspicious_wording = []
    per_surf = {}

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
                category, _reason = _oracle_gap_category(rec)
                oracle_gaps[category].append((rec.get("id") or rec.get("case_id") or "?", why))

        mode = str(rec.get("discovery_mode") or "").strip().lower()
        modes[mode if mode in DISCOVERY_MODES else "undetermined"] += 1

        surf = str(rec.get("surf_id") or "").strip()
        if surf:
            if not SURF_ID_RE.match(surf):
                surf_bad_format.append(surf)
            elif plan_surfaces is not None and surf not in plan_surfaces:
                # 计划外发现不是违规：未知问题的定义就是"不在计划里"，罚它等于
                # 从制度上劝阻发现未知问题（实测 E2）。单列计数，供 report 呈现。
                surf_new.append(surf)
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

        # —— 失效假设：每条记录可声明"如果 X 则 Y 会坏"的可证伪预测与结果 ——
        hypothesis = str(rec.get("hypothesis") or "").strip()
        # 可疑自然语言只作提示：在断言与假设两处取样，措辞可疑 ≠ 结论错。
        wording = _suspicious_language(hypothesis) or _suspicious_language(
            str(rec.get("actual") or rec.get("desc") or ""))
        if wording:
            suspicious_wording.append((rec.get("id") or rec.get("case_id") or "?", wording))
        outcome = None
        vague_reason = None
        if hypothesis:
            vague_reason = _hypothesis_vague_reason(hypothesis)
            if not surf:
                hyp_without_surface.append(rec.get("id") or rec.get("case_id") or "?")
            hyp_records += 1
            outcome = _hypothesis_outcome(rec)
            # 笼统假设的两种命运必须分开：拿来冒充覆盖的要拦，
            # 真的打破了的是产出（表述笼统也该记功，不该按缺口压档）。
            if vague_reason and outcome == "broken":
                hyp_vague_broken.append((rec.get("id") or rec.get("case_id") or "?", vague_reason))
            elif vague_reason:
                hyp_vague.append((rec.get("id") or rec.get("case_id") or "?", vague_reason))
            if outcome == "broken":
                hyp_broken += 1
            elif outcome == "blocked":
                hyp_blocked += 1
            elif outcome == HYPOTHESIS_NO_RESULT:
                # 缺结果不计 held：没跑出结果 ≠ 假设没被打破。
                # 混进 held 会让"未验证"冒充"已验证"，正是这套门禁要防的事。
                hyp_no_result += 1
                hyp_no_result_ids.append(rec.get("id") or rec.get("case_id") or "?")
            else:
                hyp_held += 1

        if surf and SURF_ID_RE.match(surf):
            bucket = per_surf.setdefault(surf, {"records": 0, "methods": set(),
                                                "hypotheses": 0, "broken": 0, "held": 0,
                                                "blocked": 0, "no_result": 0,
                                                "supported": 0, "paths": set(),
                                                "observations": [], "broken_ids": []})
            bucket["records"] += 1
            if method in known_methods:
                bucket["methods"].add(method)
            if ptype in PATH_TYPES:
                bucket["paths"].add(ptype)
            bucket["observations"].append(
                {"id": rec.get("id") or rec.get("case_id") or "?",
                 "path_type": ptype, "method": method,
                 "outcome": outcome if hypothesis else None,
                 "hypothesis": bool(hypothesis),
                 "sharp": vague_reason is None if hypothesis else None})
            if hypothesis:
                bucket["hypotheses"] += 1
                bucket[outcome] += 1
                if outcome == "broken":
                    bucket["broken_ids"].append(rec.get("id") or rec.get("case_id") or "?")
                # 支持覆盖 = 有结果 + 独立判据 + 有证据 + 假设锐利。缺任一项都不算覆盖：
                # "写了一句预测"不等于"这个面被打过"；空话假设不计覆盖但保留披露。
                if (outcome in ("broken", "held", "blocked") and vague_reason is None
                        and source == "independent" and _oracle_unproven(rec) is None):
                    bucket["supported"] += 1

        probes = rec.get("probes")
        if isinstance(probes, int) and probes > 0:
            probes_total += probes

    # v1.48.4：按现场证据去重的独立执行数。同一批证据文件被多条记录引用（补录、
    # 台账更正、同一操作的多视角登记）只算一次执行；真正以同输入复跑会带新现场
    # 证据，正常计数。无申报值（旧批次）记 None——"没登记"与"登记了 0"不同。
    evidence_runs = set()
    probes_declared_missing = 0
    for rec in records:
        declared = rec.get("probes")
        evidence = _evidence_list(rec)
        if not isinstance(declared, int):
            probes_declared_missing += 1
        if not evidence:
            continue  # 无证据身份，不参与执行归并
        # 现场身份 = 证据集合的包含归并：同一操作多视角登记时证据文件常有子集
        # （主记录全量、引用记录只带其中一张）。把互为子集的证据组归并为同一次
        # 执行；真正复跑的全新文件集自然落进新组。
        evidence_runs.add(frozenset(evidence))
    # 归并互为子集的证据组：只保留极大组（等集已在 set 去重时合并）
    evidence_runs_count = sum(
        1 for run in evidence_runs
        if not isinstance(run, frozenset)
        or not any(isinstance(other, frozenset) and run < other
                   for other in evidence_runs if other is not run))

    non_positive = sum(v for k, v in path_counts.items() if k != "positive")
    # 分母只取显式声明了 path_type 的条目：字段全缺时报 NA，不以 0 冒充"一条非正向都没有"
    declared_total = total - path_unknown
    density = round(non_positive / declared_total, 4) if declared_total else "NA"
    independent_total = oracle["independent"] + oracle["self_attested"]
    indep_rate = (round(oracle["independent"] / independent_total, 4)
                  if independent_total else "NA")
    surf_rate = round(surf_linked / total, 4) if total else "NA"
    method_diversity = len(methods)
    single_method_surfs = sorted(s for s, b in per_surf.items()
                                 if len(b["methods"]) <= 1 and b["records"] >= 3)

    gaps = []
    notices = []
    if not total:
        gaps.append({"code": "empty_ledger", "message": "台账为空，无任何执行记录"})
    if path_unknown:
        gaps.append({"code": "path_type_missing",
                     "message": f"{path_unknown}/{total} 条未声明 path_type（"
                                + "/".join(PATH_TYPES) + "），攻击密度无法机械计算；"
                                "推断值仅供参考，不作判据"})
    # 绝对零而非比例：比例能被凑，而"一条非正向路径都没有"凑不出来。
    if declared_total >= NO_ATTACK_MIN_RECORDS and non_positive == 0:
        gaps.append({"code": "no_attack_paths",
                     "message": f"{declared_total} 条记录全部是 positive：没有任何"
                                "边界/异常/权限/组合/界面/一致性/回归路径，"
                                "本轮只确认了功能可用，没有构造让它坏的条件"})
    if unproven:
        detail = "；".join(f"{rid}: {why}" for rid, why in unproven[:5])
        gaps.append({"code": "oracle_independence_unproven",
                     "message": f"{len(unproven)} 条声明 independent 但拿不出独立通道：{detail}"})
    # 判据缺口分两类披露（补法不同：前者补独立通道，后者补可复核证据）：
    for category, title, advice in (
            ("no_criterion", "无判据占位", "须补一条真正的独立通道（不得只写被测物自述）"),
            ("no_evidence", "缺真实执行证据", "须补可复核的证据路径（截图/接口响应/日志）")):
        items = oracle_gaps.get(category) or []
        if items:
            detail = "；".join(f"{rid}: {why}" for rid, why in items[:5])
            gaps.append({"code": f"oracle_{category}",
                         "message": f"{len(items)} 条判据属「{title}」：{detail}"
                                    f"（{advice}）"})
    # 一条独立通道都没有，是"证据结构缺失"，与"比例偏低"不是一件事：
    # 比例可凑，零独立通道凑不出来（每条 independent 都要求点名通道 + 带证据）。
    if total >= 10 and oracle["independent"] == 0:
        gaps.append({"code": "oracle_no_independent_evidence",
                     "message": f"{total} 条记录中没有任何 independent 判据："
                                "结论全靠自证或未声明，无法排除同源假绿"})
    # —— 失效假设：替代"配额"的核心判据 ——
    required_surfaces = {s for s, risk in (plan_risks or {}).items()
                         if str(risk).strip().lower() == "high"}
    # 覆盖 = 有结果 + 独立判据 + 有证据的假设。只写了一句预测不算覆盖——
    # 缺结果、缺独立通道、缺证据任一都撑不起"这个面被打过"。
    covered = {s for s, b in per_surf.items() if b["supported"] > 0}
    missing_hyp = sorted(required_surfaces - covered)
    if missing_hyp:
        # 区分"根本没写预测"与"写了但没验证"：两种缺口的补法不同。
        never_stated = [s for s in missing_hyp if per_surf.get(s, {}).get("hypotheses", 0) == 0]
        stated_unverified = [s for s in missing_hyp if s not in never_stated]
        if never_stated:
            gaps.append({"code": "surface_without_hypothesis",
                         "message": f"高风险风险面没有任何可证伪预测：{never_stated[:6]}"
                                    "（每个高风险面至少要有一条「如果 X 则 Y 会坏」的构造）"})
        if stated_unverified:
            gaps.append({"code": "surface_hypothesis_unverified",
                         "message": f"高风险风险面写了预测但没有可采信的验证结果：{stated_unverified[:6]}"
                                    "（缺结果、缺独立判据或缺证据，均不算已验证）"})
    if hyp_no_result:
        gaps.append({"code": "hypothesis_without_result",
                     "message": f"{hyp_no_result} 条失效假设没有结果：{hyp_no_result_ids[:5]}"
                                "（缺结果不算「未打破」，不得计入已验证）"})
    if plan_risks is None and total >= HYPOTHESIS_MIN_RECORDS and not hyp_records:
        gaps.append({"code": "no_falsifiable_hypothesis",
                     "message": f"{total} 条记录里没有一条声明失效假设（hypothesis）："
                                "无法区分「确认功能可用」与「构造让它坏的条件」"})
    if hyp_without_surface:
        gaps.append({"code": "hypothesis_without_surface",
                     "message": f"{len(hyp_without_surface)} 条失效假设未关联风险面："
                                f"{hyp_without_surface[:5]}（假设必须挂到 surf_id 才能判覆盖）"})
    if hyp_vague:
        detail = "；".join(f"{rid}: {why}" for rid, why in hyp_vague[:5])
        gaps.append({"code": "hypothesis_vague",
                     "message": f"{len(hyp_vague)} 条假设不够锐利，不计入覆盖：{detail}"
                                "（须含可核对的取值/对象/状态或明确预期方向；记录保留披露，不删除）"})
    if hyp_vague_broken:
        detail = "；".join(f"{rid}: {why}" for rid, why in hyp_vague_broken[:5])
        notices.append({"code": "hypothesis_vague_but_broken",
                        "message": f"{len(hyp_vague_broken)} 条假设表述笼统但确实被打破：{detail}"
                                   "（打破即产出，计为缺陷线索，不压结论上限；"
                                   "建议补记具体取值/对象，便于复现）"})
    if surf_bad_format:
        gaps.append({"code": "surf_format_invalid",
                     "message": f"surf_id 形态非法（需 SURF- 前缀 + 稳定标识）：{surf_bad_format[:5]}"})
    if declared_method_missing:
        gaps.append({"code": "method_missing",
                     "message": f"{declared_method_missing}/{total} 条未标注探针方法（方法库 ID），"
                                "无法核对方法多样性"})
    if method_unknown:
        gaps.append({"code": "method_unknown",
                     "message": f"method 不在方法库枚举内：{sorted(set(method_unknown))[:5]}"})

    # —— 高风险面方法单一：与"整体方法多样性低"分开处理 ——
    # 整体多样性只是分布描述（可被凑）；但**某个高风险面**只用了一种方法，
    # 是这个面没被真正打透的信号——换个面换方法不算覆盖这个面。
    #
    # N6 口径（政策反转，替代旧「单一方法只提示不压上限」）：高风险面记录 ≥3、
    # 仅 1 种方法、可采信结果全部 held（无 broken）时，属「解释或复打」义务——
    # 要么追加一条不同方法的探针（记录 hypothesis + 结果），要么提交点名该面的
    # 不复打理由（--replay-note）。两者皆无才压上限；这不是方法多样性配额，
    # 不要求凑够 N 种方法，只要求对「为什么不复打」给出可核对回应。
    # 建议、披露与义务都落在高风险面（risk=high，枚举与对抗 plan / 探索清单一致：
    # high/medium/low）。未定级面不在 required_surfaces 内，由 single_method_surface
    # 泛提示另作披露。
    single_method_all = []
    for surf in sorted(required_surfaces):
        bucket = per_surf.get(surf)
        if not bucket or bucket["records"] < 3:
            continue
        if len(bucket["methods"]) > 1:
            continue
        single_method_all.append({
            "surface": surf,
            "risk": str((plan_risks or {}).get(surf) or "未定级").strip().lower(),
            "method": sorted(bucket["methods"]),
            "records": bucket["records"],
            "hypotheses": bucket["hypotheses"],
            "supported": bucket["supported"],
            "broken": bucket["broken"],
            "observations": bucket["observations"][:5],
            "existing_paths": sorted(bucket["paths"]),
        })
    # required_surfaces 已只取 risk=high；medium/low/未定级不进单一方法义务判定。
    single_method_high_risk = single_method_all
    replay_notes = replay_notes or {}

    def _needs_replay(item):
        # 已 broken 的面不触发；supported==0 由「假设未验证」既有缺口处理，不重复压档。
        bucket = per_surf[item["surface"]]
        if bucket["broken"] > 0 or bucket["supported"] == 0:
            return False
        return not str(replay_notes.get(item["surface"]) or "").strip()

    unreplayed_unexplained = [item for item in single_method_high_risk if _needs_replay(item)]
    if single_method_high_risk:
        detail = "；".join(
            f"{item['surface']}（{item['records']} 条记录仅用 {item['method']}，"
            f"可采信验证 {item['supported']} 条）"
            for item in single_method_high_risk[:5])
        notices.append({"code": "high_risk_surface_single_method",
                        "message": f"{len(single_method_high_risk)} 个高风险风险面只用了单一方法：{detail}"
                                   "（该面未被真正打透；建议换方法复打，见 retest_suggestions）"})
    if unreplayed_unexplained:
        detail = "；".join(item["surface"] for item in unreplayed_unexplained[:6])
        gaps.append({"code": "surface_unreplayed_unexplained",
                     "message": f"{len(unreplayed_unexplained)} 个高风险面单一方法且结果全部 held，"
                                f"既未换方法复打也未提交不复打理由：{detail}"
                                "（须追加不同方法探针并记录假设/结果，或 --replay-note 点名该面并引用既有观察）"
                                "；--replay-note 传的是 JSON 文件路径，不是文本，"
                                "文件内容形如 {\"SURF-01\": \"不复打理由\"}"})


    if not gaps and not (isinstance(residual_note, str) and residual_note.strip()) \
            and not isinstance(residual_note, (dict, list)):
        gaps.append({"code": "residual_note_missing",
                     "message": "结论将达「通过」但未声明未破的攻击面（--residual-note）："
                                "无法区分「真的没问题」与「没找到让它失败的输入」"})

    # —— 弱化调用留痕：门禁链必须能看出「本轮到底核对到什么」——
    # 原先 surf_plan_checked 只说明「传入了风险面集合」，空集合也为真，
    # 等于没核对却声称核对过。这里按三态如实披露，压不压上限由门禁侧决定。
    plan_state = _plan_check_state(plan_surfaces)
    if plan_state != "checked":
        notices.append({"code": "plan_cross_check_absent",
                        "message": ("未与对抗 plan 交叉核对风险面（未提供 plan 或 plan 风险面为空）"
                                    if plan_state == "not_provided" else
                                    "提供的 plan 风险面集合为空，等同未做交叉核对")
                                   + "：弱化调用，结论上限由门禁侧压待核实"})
    # 空数组等同零登记：传了 `--atk` 但一条都没登记，仍是"本轮零登记"。
    # 只判 `is None` 会给空文件留一条免解释的旁路，等于给"什么都没写"发核对过的凭证。
    if not atk_entries:
        absent_reason = str(atk_absent_reason or "").strip()
        if absent_reason:
            notices.append({"code": "atk_not_submitted",
                            "message": f"未提交 ATK 攻击假设登记（--atk）：本轮未登记攻击假设，"
                                       f"理由：{absent_reason}（非违规，披露备查）"})
        else:
            gaps.append({"code": "atk_absent_unexplained",
                         "message": "未提交 ATK 攻击假设登记（--atk）或提交为空且未说明理由："
                                    "零登记本身不是问题，未解释才是；请补充 --atk-absent-reason"})
    residual_state = _residual_state(residual_note, plan_surfaces, atk_entries, per_surf)
    if residual_state in ("unstructured", "unresolved_ref"):
        notices.append({"code": "residual_note_unanchored",
                        "message": ("残余风险声明未引用任何已登记编号（SURF-/ATK-）："
                                    if residual_state == "unstructured" else
                                    "残余风险声明引用的编号在已登记清单中不存在：")
                                   + "无法机械核对所声明的未破攻击面"})

    # —— M5：残余风险声明的三态与逐条核对 ——
    # 旧文本（自由文本）判不了"这条声明说的是不是真的"，只保留 anchored 判定；
    # 结构化声明才能逐条核对：编号在不在册、状态与实际验证状态是否一致。
    residual_items, residual_parse_error = _parse_residual_declaration(residual_note)
    residual_declaration = {
        "mode": "text" if residual_items is None else "structured",
        "state": None,
        "items": [],
        "reasons": [],
    }
    if residual_parse_error:
        notices.append({"code": "residual_declaration_unparsed",
                        "message": f"残余风险声明无法解析：{residual_parse_error}"
                                   "（按旧文本证据处理，不做逐条核对）"})
    if residual_items is not None:
        unmatched, mismatched, checked = [], [], []
        for entry in residual_items:
            ident, claimed = entry["id"], entry["status"]
            actual = _actual_residual_status(ident, per_surf, atk_entries)
            if actual is None:
                unmatched.append({"id": ident, "claimed": claimed,
                                  "label": RESIDUAL_STATUS_LABELS[claimed]})
                continue
            # not_verified 是**保守声明**（少报覆盖），不按状态不符记缺口——
            # 如实登记不该被惩罚。承重的 unbroken/broken 才做严格一致性校验。
            if claimed == "not_verified":
                checked.append({"id": ident, "status": claimed,
                                "label": RESIDUAL_STATUS_LABELS[claimed],
                                "actual": actual,
                                "actual_label": RESIDUAL_STATUS_LABELS[actual]})
                continue
            if actual != claimed:
                mismatched.append({"id": ident, "claimed": claimed,
                                   "actual": actual,
                                   "label": RESIDUAL_STATUS_LABELS[claimed],
                                   "actual_label": RESIDUAL_STATUS_LABELS[actual]})
                continue
            checked.append({"id": ident, "status": claimed,
                            "label": RESIDUAL_STATUS_LABELS[claimed]})
        # 三态：有未破项 / 无未破项 / 尚未验证
        # - 有未破项：声明了至少一条 unbroken 且该条核对相符；
        # - 无未破项：声明为空、或所有条目都已 broken 且核对相符；
        # - 尚未验证：存在无法核对（不在册/状态不符）的条目，或声明为 not_verified。
        if unmatched or mismatched:
            state = "not_verified"
        elif any(item["status"] == "unbroken" for item in checked):
            state = "has_unbroken"
        elif any(item["status"] == "not_verified" for item in checked):
            state = "not_verified"
        else:
            state = "no_unbroken"
        residual_declaration["state"] = state
        residual_declaration["items"] = [
            {"id": item["id"], "status": item["status"], "label": item["label"],
             "verdict": "matched"} for item in checked] + [
            {"id": item["id"], "status": item["claimed"], "label": item["label"],
             "verdict": "not_registered"} for item in unmatched] + [
            {"id": item["id"], "status": item["claimed"], "label": item["label"],
             "actual": item["actual"], "actual_label": item["actual_label"],
             "verdict": "status_mismatch"} for item in mismatched]
        if unmatched:
            gaps.append({"code": "residual_ref_not_registered",
                         "message": "残余风险声明引用了未登记编号："
                                    + "、".join(item["id"] for item in unmatched[:6])
                                    + "（编号不在本轮已登记清单，无法核对）"})
        if mismatched:
            detail = "；".join(f"{item['id']} 声明「{item['label']}」实际「{item['actual_label']}」"
                               for item in mismatched[:6])
            gaps.append({"code": "residual_status_mismatch",
                         "message": f"残余风险声明的状态与实际验证状态不符：{detail}"
                                    "（须与风险面、对应观察及验证状态一致）"})
        if state == "not_verified" and not unmatched and not mismatched:
            declared_unverified = [item["id"] for item in checked
                                    if item["status"] == "not_verified"]
            notices.append({"code": "residual_not_verified",
                            "message": "残余风险声明存在「尚未验证」的条目："
                                       + "、".join(declared_unverified[:6])
                                       + "（如实登记，不压结论上限）"})

    # —— ATK 独立轨道：与需求 SURF 轨道分开判定 ——
    # 「未执行」不压上限（允许长期悬置）；「命中」且影响面属功能/数据/权限时
    # 升级为严重度复核候选，已裁定闭环的（见 _closed_review_ids）不再重复压上限。
    atk = analyse_atk(atk_entries)
    if atk["errors"]:
        gaps.append({"code": "atk_registry_invalid",
                     "message": f"ATK 登记存在 {len(atk['errors'])} 处问题：{atk['errors'][:5]}"
                                "（攻击假设须含 ID、模糊假设、验证路径、影响面与验证状态）"})
    reviewed = _closed_review_ids(reviews)
    pending_candidates = [c for c in atk["severity_candidates"] if c["id"] not in reviewed]
    if pending_candidates:
        gaps.append({"code": "atk_hit_unreviewed",
                     "message": "攻击假设命中且影响面属功能/数据/权限，尚未裁定闭环："
                                + "、".join(c["id"] for c in pending_candidates)
                                + "（须复核并闭环；结论上限为待核实）"})
    if atk["unexecuted"]:
        notices.append({"code": "atk_unexecuted",
                        "message": f"{atk['unexecuted']} 条攻击假设尚未执行："
                                   f"{atk['unexecuted_ids'][:6]}（允许长期悬置，不压结论上限）"})
    # —— M1：ATK「未命中」的证据等级门禁 ——
    # 推演/走读判出的否定没有可核对对象：影响面属功能/数据/权限时，须 --replay-note
    # 点名该 ATK 并引用既有观察（解释为什么不复打——这是 replay-note 的本职），
    # 或补一条实操探针。unknown（旧台账缺省）不触发，不为"没填新字段"罚存量记录。
    unexplained = [i for i in atk["unsupported_negative"]
                   if not str((replay_notes or {}).get(i) or "").strip()]
    if unexplained:
        gaps.append({"code": "atk_negative_by_simulation_unexplained",
                     "message": f"{len(unexplained)} 条攻击假设判「未命中」，"
                                f"但验证方式为推演/代码走读且影响面属功能/数据/权限："
                                f"{unexplained[:6]}"
                                "（须 --replay-note 点名该 ATK 并引用既有观察，"
                                "或补一条实操探针）"})
    # —— 目标导向补漏（只披露，不加义务）——
    # 逃逸口一：把「未命中」改标成「无法验证」，M1 的证据义务就不适用（未命中专属）。
    # 「无法验证」此前完全不可见——现在按验证方式分布披露，读者能看见这种转移。
    if any(atk["unable_verified_by"].values()):
        detail = "、".join(f"{vb}：{n}" for vb, n in atk["unable_verified_by"].items() if n)
        notices.append({"code": "atk_unable_to_verify",
                        "message": f"{sum(atk['unable_verified_by'].values())} 条攻击假设判「无法验证」"
                                   f"（验证方式分布：{detail}）——「无法验证」不受 M1 证据义务约束，"
                                   "若实为「已试但未破」请改标「未命中」并挂证据"})
    # 逃逸口二：不填 verified_by（缺省 unknown）即绕过 M1。旧台账无法区分"历史登记"
    # 与"本轮规避"，但新格式台账（FINISH 已携带 evidence_grade）可给出一条线索：
    # 此时未声明验证方式应被逐条确认。只披露，不压上限。
    ledger_has_grade = any("evidence_grade" in rec for rec in attempt_index.values())
    if ledger_has_grade:
        unset = [entry["id"] for entry in atk["entries"]
                 if entry["status"] == "未命中" and entry["verified_by"] == "unknown"]
        if unset:
            notices.append({"code": "atk_negative_verified_by_unset",
                            "message": f"{len(unset)} 条攻击假设判「未命中」但未声明验证方式"
                                       f"（verified_by 缺省 unknown）：{unset[:6]}"
                                       "（本台账 FINISH 已携带证据等级格式，无法自动区分"
                                       "历史登记与本轮未声明——请逐条确认）"})
    # —— M1 扩展：practical「未命中」的证据挂钩 ——
    # 三重交集（状态=未命中 × verified_by=practical × 影响面属功能/数据/权限），
    # 一个不能少——体验类未命中不扩大义务。可用 --replay-note 豁免的是推演/走读；
    # practical 声称"做了实操"，一句话解释证明不了，必须挂可关联的执行记录或落盘证据。
    practical_negative = [entry for entry in atk["entries"]
                          if entry["status"] == "未命中"
                          and entry["verified_by"] == "practical"
                          and entry["impact"] in ATK_SEVERITY_IMPACTS]
    unproven_practical, legacy_grade, link_manual = [], [], []
    for entry in practical_negative:
        state, detail = _practical_negative_evidence(entry, attempt_index)
        # linked_ac 非空但 linked_tc 为空：绑定仍可用，但覆盖关系无法自动核验——
        # 计划 2.4 点名的形式门禁退化点。无论绑定成败，人工核对义务都要显式披露：
        # 失败时附加进 gap/notice 消息，成功时单列 notice（只披露，不压结论上限）。
        manual_note = (not str(entry.get("linked_tc") or "").strip()
                       and str(entry.get("linked_ac") or "").strip())
        if state is None:
            if manual_note:
                detail += ("；linked_tc 为空，无法从 case_id 自动验证覆盖关系，"
                           "须人工核对 AC 与执行记录的对应性")
            unproven_practical.append((entry["id"], detail))
        elif state == "bound_legacy":
            if manual_note:
                detail += ("；linked_tc 为空，无法从 case_id 自动验证覆盖关系，"
                           "须人工核对 AC 与执行记录的对应性")
            legacy_grade.append((entry["id"], detail))
        elif manual_note:
            link_manual.append((entry["id"], detail))
    if link_manual:
        detail = "；".join(f"{ident}: {why}" for ident, why in link_manual[:6])
        notices.append({"code": "atk_practical_negative_link_manual",
                        "message": f"{len(link_manual)} 条 practical 未命中的执行记录绑定"
                                   f"已核验通过，但 linked_tc 为空，覆盖关系未经自动核验：{detail}"
                                   "（须人工核对 AC 与执行记录的对应性；只披露，不压结论上限）"})
    if unproven_practical:
        detail = "；".join(f"{ident}: {why}" for ident, why in unproven_practical[:6])
        gaps.append({"code": "atk_practical_negative_unproven",
                     "message": f"{len(unproven_practical)} 条攻击假设声称实操验证"
                                f"（verified_by=practical）但判「未命中」且拿不出合格证据：{detail}"
                                "（须绑定 attempt_id 指向证据等级为 E1 的 FINISH 执行记录"
                                "——linked_tc 非空时 case_id 必须匹配——或在 note 写明为何没有 run "
                                "并附 evidence 落盘文件+sha256；--replay-note 不能替代实操证据）"})
    if legacy_grade:
        detail = "；".join(f"{ident}: {why}" for ident, why in legacy_grade[:6])
        notices.append({"code": "atk_practical_negative_grade_unset",
                        "message": f"{len(legacy_grade)} 条 practical 未命中绑定的执行记录"
                                   f"未携带证据等级（旧格式台账，按历史兼容处理）：{detail}"
                                   "（只披露，不压结论上限；新写入的 FINISH 应携带 evidence_grade）"})
    # —— M6：ATK 停滞披露（只披露，不改上限）——
    # 与「未执行」提示分开：未执行是本轮事实，停滞是**跨轮**事实（上一批同样悬置）。
    stagnant_ids, stagnant_reasons = _find_stagnant_atk(atk["entries"], prev_atk_entries)
    if stagnant_ids:
        # M3：按 impact 分组呈现——功能/数据/权限单列，让读者一眼看出
        # 烂掉的是不是要命的那批。notice 不改判定。
        impact_of = {str(e.get("id") or ""): str(e.get("impact") or "其他").strip()
                     for e in atk["entries"] if isinstance(e, dict)}
        group_order = ("功能", "数据", "权限", "体验", "其他")
        grouped = {name: [i for i in stagnant_ids if impact_of.get(i, "其他") == name]
                   for name in group_order}
        detail = "；".join(
            f"{name}：{len(ids)} 条（{'、'.join(ids[:6])}）"
            for name, ids in ((n, grouped[n]) for n in group_order) if ids)
        notices.append({"code": "atk_stagnant",
                        "message": f"{len(stagnant_ids)} 条攻击假设跨轮持续未执行（按影响面分组）：{detail}"
                                   f"（{stagnant_reasons[stagnant_ids[0]]}；"
                                   "仅披露，不压结论上限）"})

    # ATK 子结论与顶层结论必须一致：闭环判定在上一段完成后才写子结论，
    # 否则会出现「顶层通过、atk 子结论待核实」的自相矛盾产物。
    atk["pending_candidates"] = pending_candidates
    atk["closed_candidates"] = [c for c in atk["severity_candidates"] if c["id"] in reviewed]
    atk["verdict_ceiling"] = "待核实" if pending_candidates else "通过"

    # N7：结论将达「通过」时，残余风险声明必须是结构化声明（可逐条对账）。
    # 必须放在 ATK 轨道之后判定：ATK 命中未闭环、登记非法这些缺口同样会把结论压到
    # 待核实，此前判定会一边报「结论将达通过、文本声明不合格」，一边报「ATK 未闭环」，
    # 自相矛盾且误罚——本来就到不了通过，不该再追加一条声明义务。
    # 已有其他缺口时不追加：待核实档保留文本入口，不抬高诚实登记成本。
    if residual_declaration["mode"] == "text" and not gaps:
        gaps.append({"code": "residual_declaration_required_for_pass",
                     "message": "结论将达「通过」但残余风险声明为自由文本：须改为结构化声明"
                                "（--residual-declaration，逐条 {id,status} 对账已实现）"})

    # —— 披露项：只报数，不压上限。配比可凑，凑出来的达标不代表打得深 ——
    if isinstance(density, float) and density < density_threshold:
        notices.append({"code": "attack_density_low",
                        "message": f"攻击密度 {density} 低于参考值 {density_threshold}："
                                   f"非正向 {non_positive}/{total}（仅披露，不作判据）"})
    if total and isinstance(surf_rate, float) and surf_rate < surf_linkage_threshold:
        notices.append({"code": "surf_linkage_low",
                        "message": f"{surf_linked}/{total} 条关联到计划内风险面"
                                   "（仅披露；计划外发现计入 surfaces_beyond_plan）"})
    if single_method_surfs:
        notices.append({"code": "single_method_surface",
                        "message": f"以下风险面只用了 1 种方法且记录 ≥3 条：{single_method_surfs[:5]}；"
                                   "若其假设全部「未打破」，建议换手法复打"})

    # —— 复测建议：把"建议换手法"落到具体动作 ——
    # 三要素缺一不可：针对哪个风险面（surface）、已有观察是什么（observations）、
    # 本轮可用的是什么（可用方法与路径类型取自已登记数据，不臆造）。
    retest_suggestions = []
    for item in single_method_all:
        used_methods = set(item["method"])
        used_paths = set(item["existing_paths"])
        retest_suggestions.append({
            "surface": item["surface"],
            "risk": item["risk"],
            "reason": "高风险面仅用单一方法，未被真正打透",
            "observations": item["observations"],
            "used_methods": sorted(used_methods),
            "used_paths": sorted(used_paths),
            "available_methods": sorted(m for m in known_methods if m not in used_methods)[:6],
            "available_paths": sorted(p for p in ATTACK_PATH_TYPES if p not in used_paths)[:6],
            "suggestion": ("在该面追加一条与现有方法不同、且落在已登记路径类型内的探针；"
                           "追加后须记录 hypothesis 与结果，否则该面仍算未验证"),
        })

    # 可疑自然语言：只作提示，不压上限（措辞可疑 ≠ 结论错）。
    if suspicious_wording:
        detail = "；".join(f"{rid}: 命中「{phrase}」" for rid, phrase in suspicious_wording[:5])
        notices.append({"code": "suspicious_wording",
                        "message": f"{len(suspicious_wording)} 条记录的表述含可疑措辞：{detail}"
                                   "（仅提示，不压上限；请确认断言写的是可观测事实而非主观判断）"})
    if surf_new:
        notices.append({"code": "new_surface",
                        "message": f"发现 {len(set(surf_new))} 个计划外风险面：{sorted(set(surf_new))[:6]}"
                                   "（计为产出；需 amend 纳入后续计划，否则无法累积）"})
        if not (isinstance(amend_note, str) and amend_note.strip()):
            notices.append({"code": "new_surface_amend_missing",
                            "message": "计划外风险面未提交 amend 说明（--amend-note）："
                                       "下一轮该风险面不会有假设，发现会被丢掉"})

    return {
        "schema": COVERAGE_SCHEMA,
        "tool_version": TOOL_VERSION,
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
        "plan_check_state": _plan_check_state(plan_surfaces),
        "atk_checked": bool(atk_entries),
        "surfaces_beyond_plan": len(set(surf_new)),
        "surfaces_beyond_plan_ids": sorted(set(surf_new)),
        "amend_note": (amend_note or "").strip() or None,
        "hypotheses": {"declared": hyp_records, "broken": hyp_broken,
                       "held": hyp_held, "blocked": hyp_blocked,
                       "no_result": hyp_no_result,
                       "vague": len(hyp_vague) + len(hyp_vague_broken),
                       "vague_but_broken": len(hyp_vague_broken),
                       "vague_ids": [rid for rid, _ in hyp_vague[:6]],
                       "no_result_ids": hyp_no_result_ids[:6],
                       "break_rate": (round(hyp_broken / hyp_records, 4)
                                      if hyp_records else None)},
        "surface_coverage": {s: {**b, "methods": sorted(b["methods"]),
                                  "paths": sorted(b["paths"])}
                             for s, b in sorted(per_surf.items())},
        "single_method_surfaces": single_method_surfs,
        "high_risk_single_method_surfaces": [item["surface"] for item in single_method_high_risk],
        "retest_suggestions": retest_suggestions,
        "methods": methods,
        "method_missing": declared_method_missing,
        "method_diversity": method_diversity,
        "probes_total": probes_total,
        "evidence_runs_deduped": evidence_runs_count,
        "probes_declared_missing_runs": probes_declared_missing,
        "residual_note": (str(residual_note or "").strip() or None)
        if not isinstance(residual_note, (dict, list)) else None,
        "residual_declaration_raw": residual_note
        if isinstance(residual_note, (dict, list)) else None,
        "residual_note_checked": _residual_state(residual_note, plan_surfaces,
                                                 atk_entries, per_surf),
        "residual_declaration": residual_declaration,
        "oracle_gaps": {name: len(items) for name, items in oracle_gaps.items()},
        "atk": atk,
        "atk_stagnant_ids": stagnant_ids,
        "atk_stagnant_checked": prev_atk_entries is not None,
        "gaps": gaps,
        "notices": notices,
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
                 "probes_total", "evidence_runs_deduped",
                 "probes_declared_missing_runs", "surfaces_beyond_plan",
                 "verdict_ceiling", "tool_version"):
        # 版本键用 get：产物一旦缺该键（旧版结果被复用），CSV 应留空而不是整条崩掉。
        rows.append({"section": "summary", "metric": name, "value": result.get(name, "")})
    for name, value in result["hypotheses"].items():
        rows.append({"section": "hypothesis", "metric": name, "value": value})
    for name, value in result["path_type_groups"].items():
        rows.append({"section": "path_type_group", "metric": name, "value": value})
    for name in ("registered", "hit", "not_hit", "unexecuted", "verdict_ceiling"):
        rows.append({"section": "atk", "metric": name, "value": result["atk"][name]})
    # M3 披露口径：未命中的验证方式分布与推演/走读否定清单随 CSV 导出，
    # 报告 5.8 取数不必回读 JSON。
    for method, count in result["atk"]["not_hit_verified_by"].items():
        rows.append({"section": "atk_not_hit_verified_by", "metric": method, "value": count})
    for ident in result["atk"]["unsupported_negative"]:
        rows.append({"section": "atk_unsupported_negative", "metric": ident, "value": 1})
    for candidate in result["atk"]["pending_candidates"]:
        rows.append({"section": "atk_candidate_pending", "metric": candidate["id"],
                     "value": f"{candidate['impact']}/{candidate['severity']}：{candidate['reason']}"})
    for candidate in result["atk"]["closed_candidates"]:
        rows.append({"section": "atk_candidate_closed", "metric": candidate["id"],
                     "value": f"{candidate['impact']}/{candidate['severity']}：已裁定闭环"})
    for entry in result["atk"]["entries"]:
        links = "、".join(x for x in (entry.get("linked_tc"), entry.get("linked_ac")) if x)
        rows.append({"section": "atk_entry", "metric": entry["id"],
                     "value": f"{entry['status']}/{entry['impact']}/{entry['severity'] or ''}"
                              f"：{entry['hypothesis']}"
                              + (f"（关联 {links}）" if links else "（未关联 TC/AC——不构成排除理由）")
                              + (f"｜{entry['note']}" if entry.get("note") else "")})
    for gap in result["gaps"]:
        rows.append({"section": "gap", "metric": gap["code"], "value": gap["message"]})
    for notice in result.get("notices") or []:
        rows.append({"section": "notice", "metric": notice["code"], "value": notice["message"]})
    # C2（5.8 披露第 9 项）：高风险面攻击路径分布——逐面列已执行 path_type，
    # 报告 5.8 取数不必回读 JSON。result.get 兜底旧产物：缺该键时整段跳过，不崩。
    for sid, info in sorted((result.get("surface_coverage") or {}).items()):
        paths = (info or {}).get("paths") or []
        if paths:
            rows.append({"section": "surface_path", "metric": sid,
                         "value": "、".join(paths)})
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


def _plan_risks(path):
    """风险面 → 风险等级。高风险面必须至少有一条可证伪预测，这是"设计过攻击"的判据。"""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    surfaces = data.get("surfaces")
    if not isinstance(surfaces, list):
        raise ValueError("plan 缺 surfaces 数组")
    return {str(s.get("id")): s.get("risk") for s in surfaces
            if isinstance(s, dict) and s.get("id")}


def main(argv=None):
    parser = argparse.ArgumentParser(description="攻击面覆盖与判据独立性检查")
    parser.add_argument("--ledger", required=True,
                        help="执行台账（JSONL/CSV/执行事件流 events.jsonl）")
    parser.add_argument("--plan", help="对抗 plan（提供时 surf_id 与风险面清单交叉核对）")
    parser.add_argument("--residual-note",
                        help="未破攻击面声明（旧文本入口）；结论达「通过」前必填。"
                             "内容为 JSON 时按结构化声明逐条核对")
    parser.add_argument("--residual-declaration",
                        help="结构化残余风险声明 JSON 文件（{\"items\":[{\"id\",\"status\"}]}）；"
                             "status 取 unbroken/broken/not_verified。与 --residual-note 同时给时以此为准")
    parser.add_argument("--amend-note",
                        help="计划外风险面的纳入说明；缺省时只提示，不压上限")
    parser.add_argument("--emit-json", help="结论输出路径")
    parser.add_argument("--emit-csv", help="CSV 导出路径（UTF-8-sig）")
    parser.add_argument("--atk", help="ATK 攻击假设登记文件（数组或含数组的对象）；走独立校验轨道")
    parser.add_argument("--atk-prev",
                        help="上一可比批次的 ATK 登记快照；提供后披露跨轮持续未执行（atk_stagnant），"
                             "只提示不改上限")
    parser.add_argument("--reviews", help="候选复核 JSONL 路径；用于判断命中的 ATK 是否已裁定闭环")
    parser.add_argument("--atk-absent-reason",
                        help="未提交 --atk 时的解释理由；零登记不是问题，未解释才是")
    parser.add_argument("--replay-note",
                        help="不复打理由 JSON：{\"SURF-01\": \"…\"} 或 [{\"surface\",\"reason\"}]；"
                             "点名风险面并引用既有观察，供 N6 单一方法高风险面对账")
    parser.add_argument("--density-threshold", type=float, default=DEFAULT_DENSITY_THRESHOLD)
    parser.add_argument("--independent-rate", type=float, default=DEFAULT_INDEPENDENT_RATE)
    parser.add_argument("--surf-linkage", type=float, default=DEFAULT_SURF_LINKAGE)
    parser.add_argument("--enforce", action="store_true",
                        help="存在缺口时以退出码 2 结束（用于门禁链）")
    args = parser.parse_args(argv)
    try:
        records = _load(args.ledger)
        atk_entries = _load_atk(args.atk) if args.atk else None
        reviews = []
        if args.reviews:
            for line in Path(args.reviews).read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{args.reviews} 解析失败: {exc}") from exc
                reviews.append(item)
        residual = args.residual_note
        if args.residual_declaration:
            residual = json.loads(Path(args.residual_declaration).read_text(encoding="utf-8-sig"))
        replay_notes = None
        if args.replay_note:
            raw = json.loads(Path(args.replay_note).read_text(encoding="utf-8-sig"))
            if isinstance(raw, dict):
                replay_notes = {str(k): str(v) for k, v in raw.items()}
            elif isinstance(raw, list):
                replay_notes = {
                    str(x.get("surface") or x.get("id") or ""): str(x.get("reason") or x.get("note") or "")
                    for x in raw if isinstance(x, dict)}
        result = analyse(records, args.density_threshold, args.independent_rate,
                         residual_note=residual,
                         plan_surfaces=_plan_surfaces(args.plan) if args.plan else None,
                         plan_risks=_plan_risks(args.plan) if args.plan else None,
                         surf_linkage_threshold=args.surf_linkage,
                         amend_note=args.amend_note,
                         atk_entries=atk_entries,
                         reviews=reviews,
                         prev_atk_entries=_load_atk(args.atk_prev) if args.atk_prev else None,
                         atk_absent_reason=args.atk_absent_reason,
                         replay_notes=replay_notes)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        # 输入/解析错误以 2 退出——与包内其它脚本（adversarial_gate /
        # execution_ledger / challenge_summary / finding_summary）口径一致，
        # 不因裸抛异常变成退出码 1。
        print(f"攻击覆盖输入/校验错误: {exc}", file=sys.stderr)
        return 2
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
