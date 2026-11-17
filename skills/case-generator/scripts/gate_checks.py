#!/usr/bin/env python3
"""gate_checks.py — case-generator 门禁判定（只报不改）。

判据来源：config/policy.json（frozen 4.17.0）+ 专家包 scripts/case_data.py 常量。
S0 结构校验先跑；结构不过则不计算后续文本门禁（避免在非法结构上跑出假通过）。
不自动改用例、不做超标自动降级；失败输出定位到 case_id 或批次号。

检查项：S0 / G1 / G1.5 / G2 / G3 / G4 / G5 / G6 / G7 / V05 / V07 / V08 / V09 /
V11 / V12 / V13 / COV / COV-PATH / COV-UI / RATIO-SMOKE / RATIO-P0（含小样本例外与批内告警）。

退出码：0=通过；1=门禁不通过；2=用法/输入/依赖错误。
"""

import argparse
import itertools
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
try:
    import case_data as cd
except Exception as exc:  # noqa: BLE001
    print(f"工具阻塞：无法加载专家包校验器 scripts/case_data.py（{exc}）。", file=sys.stderr)
    sys.exit(2)

DEFAULT_POLICY = Path(__file__).resolve().parents[1] / "config" / "policy.json"

# —— G1 步骤具体性（抽取自 gate_checker.py gate_g1_step_concreteness 的用例化改写；
#    方案 §12 可配置化：默认值与 policy.json gate_rules.g1 保持一致，policy 段存在时以 policy 为准）——
DEFAULT_BANNED_STEP = [
    {"pattern": "进入相关功能页面", "hint": "进入具体菜单路径，如：进入首页→营销管理→协同分润"},
    {"pattern": "进入相关页面", "hint": "进入具体菜单路径，如：进入首页→营销管理→协同分润"},
    {"pattern": "进入对应页面", "hint": "进入具体菜单路径，如：进入首页→营销管理→协同分润"},
    {"pattern": "执行相关操作", "hint": "替换为具体操作动词+目标，如：点击「提交」按钮"},
]
DEFAULT_MENU_PATH = r"[\u4e00-\u9fa5a-zA-Z0-9]+(?:→|->)[\u4e00-\u9fa5a-zA-Z0-9]+(?:→|->)[\u4e00-\u9fa5a-zA-Z0-9]+"
DEFAULT_CONFIRM_BARE = r"^确认$"
DEFAULT_ACTION_VERBS = ["点击", "输入", "填写", "选择", "勾选", "上传", "下载", "导出", "导入", "删除", "提交", "打开", "进入", "切换", "搜索", "滚动", "双击", "右键"]

# —— G1.5 / G2 具体性信号（抽取自 _has_specificity_signal；审核补充 行/列/页/项/笔/份 等表格类量词）——
QUANTIFIABLE = [r"\d+\s*条", r"\d+\s*次", r"\d+\s*个", r"\d+\s*行", r"\d+\s*列", r"\d+\s*页", r"\d+\s*项", r"\d+\s*笔", r"\d+\s*份", r"\d+%", r"\d+%%", r"\d+\s*秒", r"\d+\s*分钟", r"\d+\s*px", r"共\s*\d+", r"变为\s*\d+", r"增加\s*\d+", r"减少\s*\d+"]
STRONG_UI = [r"红色边框", r"绿色边框", r"蓝色边框", r"红色提示", r"绿色提示", r"橙色警告", r"红色图标", r"绿色图标", r"弹窗关闭", r"弹窗打开", r"输入框禁用", r"输入框启用", r"按钮置灰", r"按钮高亮", r"页面跳转"]
WEAK_UI = ["弹窗", "输入框", "按钮", "下拉框", "复选框", "单选框", "列表", "表格", "菜单", "导航", "标签页", "对话框", "提示", "警告", "消息", "表单", "字段", "页面", "区域"]

# —— G2 九种模糊表述（qa 源，子串级 + 信号豁免）——
VAGUE_EXPECT = ["正常加载", "验证成功", "符合业务规则", "符合预期", "功能正常", "数据正确", "操作成功", "展示完整", "流程正确"]

# —— G6 步骤原子性（抽取自 gate_g6_step_atomicity）——
MERGE_PATTERNS = [
    (re.compile(r"点击.{1,20}并(?:验证|检查|确认)"), "点击+验证合并"),
    (re.compile(r"输入.{1,20}(?:后|然后|之后).{0,5}(?:点击|提交|选择)"), "输入+操作合并"),
    (re.compile(r"登录.{1,20}并进入"), "登录+导航合并"),
    (re.compile(r"选择.{1,20}(?:并|后).{0,5}(?:填写|输入)"), "选择+输入合并"),
]
ALLOWED_COMPOUND = [re.compile(r"[\u4e00-\u9fa5]+(?:→|->)[\u4e00-\u9fa5]+"), re.compile(r"导航至")]
ALLOWED_MERGE = [
    re.compile(r"(?:点击|按下).{1,15}(?:导出|下载).{1,15}(?:检查|确认|查看).{1,20}(?:行数|内容|数据|文件)"),
    re.compile(r"点击.{1,20}(?:后|并)确认.{1,20}(?:弹窗|对话框|提示)"),
]

# —— V08 三要素信号 ——
V08_ENV = ("环境", "系统", "浏览器", "版本", "端", "测试地址", "域名")
V08_ACCOUNT = ("账号", "角色", "登录", "用户", "权限", "身份")
V08_DATA = ("数据", "记录", "条目", "参数", "配置", "样例", "预置")

# —— V09 显式序号引用 ——
FWD_REF_RE = re.compile(r"(?:回到|跳转到|见|参见|参考)第(\d+)步")


def _has_signal(text):
    if re.search(r"「[^」]+」", text):
        return True
    for pat in QUANTIFIABLE:
        if re.search(pat, text):
            return True
    for pat in STRONG_UI:
        if re.search(pat, text):
            return True
    if sum(1 for el in WEAK_UI if el in text) >= 2:
        return True
    return False


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def _bigrams(text):
    cleaned = re.sub(r"\s+", "", str(text))
    return {cleaned[i:i + 2] for i in range(max(0, len(cleaned) - 1))} if len(cleaned) >= 2 else ({cleaned} if cleaned else set())


def _fingerprint_case(tc, fields):
    return _bigrams("||".join(str(tc.get(f, "")) for f in fields))


class GateRunner:
    def __init__(self, policy):
        self.policy = policy
        self.checks = []
        self._compile_g1()

    def _compile_g1(self):
        """G1 规则集：policy.gate_rules.g1 优先，缺省用内置默认（方案 §12 可配置化）。"""
        cfg = (self.policy.get("gate_rules", {}) or {}).get("g1") or {}
        self.g1_banned = [(re.compile(rule["pattern"]), rule["hint"])
                          for rule in (cfg.get("banned_step_patterns") or DEFAULT_BANNED_STEP)]
        self.g1_menu = re.compile(cfg.get("menu_path_pattern") or DEFAULT_MENU_PATH)
        self.g1_verbs = tuple(cfg.get("action_verbs") or DEFAULT_ACTION_VERBS)
        self.g1_confirm_bare = re.compile((cfg.get("confirm") or {}).get("bare_pattern") or DEFAULT_CONFIRM_BARE)

    def record(self, gid, name, level, issues, passed_msg):
        status = "passed" if not issues else ("warning" if level == "WARN" else "failed")
        self.checks.append({
            "id": gid, "name": name, "level": level, "status": status,
            "issues": issues if len(issues) <= 50 else issues[:50] + [f"…共 {len(issues)} 条，仅显示前 50"],
            "passed_msg": passed_msg,
        })

    def run(self, cases, scope_leaf_ids=None):
        t0 = time.monotonic()
        th = self.policy.get("thresholds", {})
        tcs = [tc for tc in cases.get("testcases", []) if isinstance(tc, dict)]
        errors, _ = cd.validate(cases)

        # S0：结构校验先跑
        s0_issues = [f"S0 case_data.validate: {e}" for e in errors]
        self.record("S0", "结构校验（case_data.validate）", "BLOCK", s0_issues, "结构与引用合法")
        if s0_issues:
            return self._summary(cases, t0)

        self._g1(tcs)
        self._g1_5(tcs)
        self._g2(tcs)
        self._g3(tcs)
        self._g4(tcs)
        self._g5(tcs)
        self._g6(tcs)
        self._g7(cases, tcs, scope_leaf_ids)
        self._v_checks(tcs)
        self._cov(cases, tcs)
        self._ratios(cases, tcs, th)
        self._cov_path(cases)
        self._cov_ui(cases, tcs)
        return self._summary(cases, t0)

    # G1 步骤具体性（规则集可由 policy.gate_rules.g1 覆盖）
    def _g1(self, tcs):
        issues = []
        for tc in tcs:
            cid = tc.get("id", "?")
            for i, step in enumerate(tc.get("steps", [])):
                action = str(step.get("action", ""))
                for pat, hint in self.g1_banned:
                    if pat.search(action):
                        issues.append({"case_id": cid, "step": i + 1, "line": action[:80], "why": hint})
                if self.g1_confirm_bare.match(action.strip()):
                    issues.append({"case_id": cid, "step": i + 1, "line": action[:80], "why": "纯「确认」无具体对象；写出确认的具体内容"})
                concrete = self.g1_menu.search(action) or re.search(r"「[^」]+」", action) or any(v in action for v in self.g1_verbs)
                if not concrete:
                    issues.append({"case_id": cid, "step": i + 1, "line": action[:80],
                                   "why": "步骤缺具体路径/按钮/输入值（菜单路径 A→B→C、「」对象、或 操作动词+目标）"})
        self.record("G1", "步骤具体性", "BLOCK", issues, "全部步骤含具体路径、按钮或输入值")

    # G1.5 末位步骤可观测
    def _g1_5(self, tcs):
        issues = []
        for tc in tcs:
            steps = tc.get("steps", [])
            if not steps:
                continue
            last = steps[-1]
            text = str(last.get("expected", "")) + str(last.get("action", ""))
            if not _has_signal(text):
                issues.append({"case_id": tc.get("id", "?"), "why": f"末位步骤缺可观测对象：{text[:60]}"})
        self.record("G1.5", "末位步骤可观测", "BLOCK", issues, "末位步骤均可观测")

    # G2 期望断言性
    def _g2(self, tcs):
        issues = []
        for tc in tcs:
            cid = tc.get("id", "?")
            for i, a in enumerate(tc.get("assertions", [])):
                for key in ("check", "oracle", "expect"):
                    text = a.get(key)
                    if not isinstance(text, str):
                        continue
                    if cd.is_noncommittal(text):
                        issues.append({"case_id": cid, "at": f"assertions[{i}].{key}", "why": "整句空话"})
                    if cd.has_placeholder(text):
                        issues.append({"case_id": cid, "at": f"assertions[{i}].{key}", "why": "含占位内容"})
                    for vague in VAGUE_EXPECT:
                        if vague in text and not _has_signal(text):
                            issues.append({"case_id": cid, "at": f"assertions[{i}].{key}",
                                           "why": f"模糊表述「{vague}」且无具体性信号"})
            for i, s in enumerate(tc.get("steps", [])):
                exp = s.get("expected")
                if isinstance(exp, str):
                    if cd.is_noncommittal(exp):
                        issues.append({"case_id": cid, "at": f"steps[{i}].expected", "why": "整句空话"})
                    if cd.has_placeholder(exp):
                        issues.append({"case_id": cid, "at": f"steps[{i}].expected", "why": "含占位内容"})
        self.record("G2", "期望断言性", "BLOCK", issues, "断言无空话/占位/无信号模糊表述")

    # G3 业务流程覆盖（policy 定稿：A 类 ≥3 步；B 类不得标冒烟）
    def _g3(self, tcs):
        issues = []
        min_a_steps = 3
        for tc in tcs:
            cid = tc.get("id", "?")
            case_class = str(tc.get("case_class", "")).upper()
            n_steps = len(tc.get("steps", []))
            if case_class == "A" and n_steps < min_a_steps:
                issues.append({"case_id": cid, "why": f"A 类用例仅 {n_steps} 步（要求 ≥{min_a_steps}）"})
            if case_class == "B" and "smoke" in (tc.get("suites") or []):
                issues.append({"case_id": cid, "why": "B 类用例不得标冒烟"})
        self.record("G3", "业务流程覆盖", "BLOCK", issues, "A 类步数达标且 B 类未标冒烟")

    # G4 冒烟来源可追溯
    def _g4(self, tcs):
        issues = []
        for tc in tcs:
            if "smoke" in (tc.get("suites") or []):
                src = str(tc.get("smoke_source", "") or "")
                if not src:
                    issues.append({"case_id": tc.get("id", "?"), "why": "冒烟用例缺 smoke_source（须指向 A 类来源）"})
                elif "A" not in src.upper():
                    issues.append({"case_id": tc.get("id", "?"), "why": f"smoke_source={src!r} 未指向 A 类来源"})
        self.record("G4", "冒烟来源可追溯", "WARN", issues, "冒烟均可追溯 A 类来源")

    # G5 禁止模式与去重
    def _g5(self, tcs):
        issues = []
        dedupe = self.policy.get("thresholds", {}).get("dedupe", {})
        reject = dedupe.get("reject_over", 0.5)
        warn = dedupe.get("warn_over", 0.15)
        fields = dedupe.get("compare_fields", ["title", "steps", "assertions.expect"])
        fps = []
        for tc in tcs:
            parts = []
            for f in fields:
                if f == "steps":
                    parts.append(" ".join(str(s.get("action", "")) + str(s.get("expected", "")) for s in tc.get("steps", [])))
                elif f == "assertions.expect":
                    parts.append(" ".join(str(a.get("expect", "")) for a in tc.get("assertions", [])))
                else:
                    parts.append(str(tc.get(f, "")))
            fps.append((tc.get("id", "?"), _bigrams("||".join(parts))))
        for (id1, s1), (id2, s2) in itertools.combinations(fps, 2):
            sim = _jaccard(s1, s2)
            if sim >= reject:
                issues.append({"case_id": f"{id1}~{id2}", "why": f"重复对（Jaccard={sim:.2f} ≥ {reject}）", "level": "BLOCK"})
            elif sim >= warn:
                issues.append({"case_id": f"{id1}~{id2}", "why": f"相似提示（Jaccard={sim:.2f} ≥ {warn}）", "level": "WARN"})
        placeholders = list(self.policy.get("authority", {}).get("mirrored_constants", {}).get("PLACEHOLDERS", cd.PLACEHOLDERS))
        placeholders += self.policy.get("structure", {}).get("forbidden_patterns", {}).get("unresolved_placeholder", [])
        placeholders = sorted(set(placeholders))
        for tc in tcs:
            cid = tc.get("id", "?")
            texts = [str(tc.get("title", "")), str(tc.get("remarks", ""))]
            texts += [str(s.get("action", "")) + str(s.get("expected", "")) for s in tc.get("steps", [])]
            texts += [str(a.get("check", "")) + str(a.get("oracle", "")) + str(a.get("expect", "")) for a in tc.get("assertions", [])]
            joined = "||".join(texts)
            for word in placeholders:
                if word and word in joined:
                    issues.append({"case_id": cid, "why": f"含占位/未落实内容「{word}」", "level": "BLOCK"})
        blocks = [i for i in issues if i.get("level") == "BLOCK"]
        warns = [i for i in issues if i.get("level") == "WARN"]
        self.record("G5", "禁止模式与去重", "BLOCK", blocks, "无重复对与占位内容")
        self.record("G5-W", "相似提示", "WARN", warns, "无高相似对")

    # G6 步骤原子性
    def _g6(self, tcs):
        issues = []
        for tc in tcs:
            cid = tc.get("id", "?")
            for i, step in enumerate(tc.get("steps", [])):
                action = str(step.get("action", ""))
                if any(p.search(action) for p in ALLOWED_COMPOUND) or any(p.search(action) for p in ALLOWED_MERGE):
                    continue
                for pat, desc in MERGE_PATTERNS:
                    if pat.search(action):
                        issues.append({"case_id": cid, "step": i + 1, "line": action[:80], "why": f"步骤非原子：{desc}"})
                        break
        self.record("G6", "步骤原子性", "BLOCK", issues, "步骤均为原子操作")

    # G7 用例到场景可追溯
    def _g7(self, cases, tcs, scope_leaf_ids):
        issues = []
        leaves = {c.get("id") for top in cases.get("features", []) for c in top.get("children", [])}
        for tc in tcs:
            cid = tc.get("id", "?")
            fids = tc.get("feature_ids") or []
            aids = tc.get("acceptance_ids") or []
            if not fids or not aids:
                issues.append({"case_id": cid, "why": "引用链不完整（feature_ids/acceptance_ids 至少各一）"})
            if scope_leaf_ids is not None:
                out_of_scope = [f for f in fids if f not in scope_leaf_ids]
                if out_of_scope:
                    issues.append({"case_id": cid, "why": f"引用了不可生成范围的功能点 {out_of_scope}"})
            if fids and leaves and not set(fids).issubset(leaves):
                issues.append({"case_id": cid, "why": "feature_ids 含未知功能点"})
        self.record("G7", "用例到场景可追溯", "BLOCK", issues, "FP/AC/SCN/PATH 引用逐级可追溯")

    # V05/V07/V08/V09/V11/V12/V13（p5_validate 的用例化改写）
    def _v_checks(self, tcs):
        v05 = []
        for tc in tcs:
            total = len(str(tc.get("title", "")))
            total += sum(len(str(s.get("action", ""))) + len(str(s.get("expected", ""))) for s in tc.get("steps", []))
            total += sum(len(str(a.get("check", ""))) + len(str(a.get("oracle", ""))) for a in tc.get("assertions", []))
            if total < 20:
                v05.append({"case_id": tc.get("id", "?"), "why": f"description 合成长度 {total}，要求 ≥ 20"})
        self.record("V05", "描述完整度", "BLOCK", v05, "描述长度达标")

        v07 = []
        for tc in tcs:
            if tc.get("priority") not in {"P0", "P1", "P2"}:
                v07.append({"case_id": tc.get("id", "?"), "why": f"priority={tc.get('priority')!r} 非法"})
        self.record("V07", "优先级有效值", "BLOCK", v07, "优先级均为 P0/P1/P2")

        v08 = []
        for tc in tcs:
            text = " ".join(str(p) for p in tc.get("preconditions", []))
            missing = []
            if not any(w in text for w in V08_ENV):
                missing.append("环境")
            if not any(w in text for w in V08_ACCOUNT):
                missing.append("账号")
            if not any(w in text for w in V08_DATA):
                missing.append("数据")
            if missing:
                v08.append({"case_id": tc.get("id", "?"), "why": f"预置条件缺三要素: {'/'.join(missing)}"})
        self.record("V08", "预置条件三要素", "BLOCK", v08, "预置条件覆盖环境/账号/数据")

        v09 = []
        for tc in tcs:
            cid = tc.get("id", "?")
            steps = tc.get("steps", [])
            if not steps:
                continue
            for i, step in enumerate(steps):
                for m in FWD_REF_RE.finditer(str(step.get("action", ""))):
                    if int(m.group(1)) > i + 1:
                        v09.append({"case_id": cid, "why": f"步骤 {i+1} 前向引用第 {m.group(1)} 步（操作链跳跃）"})
        self.record("V09", "步骤连贯性", "WARN", v09, "步骤操作链无跳跃")

        v11 = []
        for tc in tcs:
            menu = str(tc.get("menu_path", "") or "")
            if not menu:
                continue
            parts = [p.strip() for p in re.split(r"→|->|>", menu) if p.strip()]
            core = parts[-1] if parts else ""
            first_action = str(tc.get("steps", [{}])[0].get("action", "")) if tc.get("steps") else ""
            if core and len(core) >= 2 and core not in first_action and not any(p in first_action for p in parts if len(p) >= 2):
                v11.append({"case_id": tc.get("id", "?"), "why": f"menu_path 末级「{core}」与首步操作不一致"})
        self.record("V11", "无矛盾", "WARN", v11, "menu_path 与步骤一致")

        v12 = [{"case_id": tc.get("id", "?"), "why": "risk_flag 缺失，判待核实"}
               for tc in tcs if "risk_flag" not in tc]
        self.record("V12", "风险点已识别", "WARN", v12, "risk_flag 全覆盖（缺失判待核实）")
        v13 = [{"case_id": tc.get("id", "?"), "why": "pci_flag 缺失，判待核实"}
               for tc in tcs if "pci_flag" not in tc]
        self.record("V13", "PCI 已识别", "WARN", v13, "pci_flag 全覆盖（缺失判待核实）")

    # COV：四层覆盖与方法见证
    def _cov(self, cases, tcs):
        issues = []
        methods = {m["id"]: m for m in self.policy.get("structure", {}).get("design_methods", [])}
        for tc in tcs:
            cid = tc.get("id", "?")
            me = tc.get("method_evidence")
            if isinstance(me, dict):
                for mid, witness in me.items():
                    if mid in methods and (witness is None or witness == "" or witness == {} or witness == []):
                        issues.append({"case_id": cid, "why": f"method_evidence.{mid} 只有方法名没有见证（{methods[mid]['evidence_kind']}）"})
            reason = str(tc.get("classification_reason", ""))
            for word in cd.METHOD_CLAIMS:
                if word in reason and not isinstance(me, dict):
                    issues.append({"case_id": cid, "why": f"classification_reason 声明「{word}」但 method_evidence 缺失"})
                    break
        exploratory = sum(1 for tc in tcs if "exploratory" in (tc.get("suites") or []))
        if tcs and exploratory / len(tcs) > self.policy.get("structure", {}).get("budget_policy", {}).get("exploratory_ratio_limit", 0.2):
            issues.append({"case_id": "_ratio_", "why": f"探索性用例占比 {exploratory}/{len(tcs)} 超预算上限（不计入已确认规则覆盖）", "level": "WARN"})
        self.record("COV", "四层覆盖与方法见证", "BLOCK", [i for i in issues if i.get("level") != "WARN"],
                    "L1 主流程（S0 保证）+ L3 方法见证合格")
        self.record("COV-W", "探索性预算", "WARN", [i for i in issues if i.get("level") == "WARN"], "探索性占比在预算内")

    # RATIO-SMOKE / RATIO-P0（三层 + 小样本例外）
    def _ratios(self, cases, tcs, th):
        smoke_th = th.get("smoke", {})
        p0_th = th.get("p0", {})
        small = th.get("small_sample", {})
        n = len(tcs)
        smoke_n = sum(1 for tc in tcs if "smoke" in (tc.get("suites") or []))
        p0_n = sum(1 for tc in tcs if tc.get("priority") == "P0")
        below = n < small.get("case_count_below", 20)

        # 冒烟三层
        t = smoke_th.get("target_ratio", {})
        issues = []
        if n and t and not (t.get("min", 0) <= smoke_n / n <= t.get("max", 1)):
            pass  # guidance 不计入门禁，仅备注
        bw = smoke_th.get("batch_warn_ratio", {})
        if n and bw and smoke_n / n > bw.get("max", 1):
            issues.append({"case_id": "_ratio_", "why": f"批内冒烟浓度 {smoke_n}/{n} 超提示线 {bw.get('max')}（多为该模块 P0 过密，交人工判断，不自动降级）"})
        if not below:
            dr = smoke_th.get("delivery_ratio", {})
            if dr and n and not (dr.get("min", 0) <= smoke_n / n <= dr.get("max", 1)):
                issues.append({"case_id": "_ratio_", "why": f"交付冒烟占比 {smoke_n}/{n}={smoke_n/n:.0%} 不在 {dr.get('min')}~{dr.get('max')}（gate）"})
        else:
            # 方案语义：每「模块」≥1 条冒烟、每「需求」≥1 条（模块= features 顶层 id，
            # 需求= 叶子功能点的 requirement_ids），不按功能点粒度。
            leaf_module, leaf_reqs = {}, {}
            for top in cases.get("features", []):
                for child in top.get("children", []):
                    leaf_module[child.get("id")] = top.get("id")
                    leaf_reqs[child.get("id")] = set(child.get("requirement_ids", []) or [])
            smoke_mods, smoke_reqs = set(), set()
            for tc in tcs:
                if "smoke" in (tc.get("suites") or []):
                    for fid in tc.get("feature_ids", []):
                        if leaf_module.get(fid):
                            smoke_mods.add(leaf_module[fid])
                        smoke_reqs |= leaf_reqs.get(fid, set())
            all_mods = {leaf_module.get(fid) for tc in tcs for fid in tc.get("feature_ids", [])} - {None}
            all_reqs = set()
            for tc in tcs:
                for fid in tc.get("feature_ids", []):
                    all_reqs |= leaf_reqs.get(fid, set())
            per_mod = small.get("absolute_rules", {}).get("smoke_per_module_min", 1)
            short_mods = sorted(all_mods - smoke_mods)
            if short_mods:
                issues.append({"case_id": "_ratio_", "why": f"小样本例外：模块 {short_mods} 冒烟少于每模块 {per_mod} 条"})
            if smoke_n == 0:
                issues.append({"case_id": "_ratio_", "why": "小样本例外：至少 1 条冒烟（每需求）"})
        self.record("RATIO-SMOKE", "冒烟占比（三层+小样本）", "BLOCK", [i for i in issues if "不在" in i["why"] or "少于" in i["why"] or "至少" in i["why"]],
                    "冒烟占比符合交付门禁")
        self.record("RATIO-SMOKE-W", "冒烟批内告警", "WARN", [i for i in issues if "提示线" in i["why"]], "批内浓度正常")

        # P0
        issues_p = []
        if n:
            ratio = p0_n / n
            if not below and ratio > p0_th.get("delivery_max_ratio", {}).get("max", 1):
                issues_p.append({"case_id": "_ratio_", "why": f"P0 交付占比 {p0_n}/{n}={ratio:.0%} 超上限 {p0_th.get('delivery_max_ratio', {}).get('max')}（gate）"})
            elif below:
                leaf_module = {}
                for top in cases.get("features", []):
                    for child in top.get("children", []):
                        leaf_module[child.get("id")] = top.get("id")
                p0_mods = {leaf_module.get(fid) for tc in tcs if tc.get("priority") == "P0"
                           for fid in tc.get("feature_ids", [])} - {None}
                all_mods_p = {leaf_module.get(fid) for tc in tcs for fid in tc.get("feature_ids", [])} - {None}
                short_mods_p = sorted(all_mods_p - p0_mods)
                if short_mods_p:
                    issues_p.append({"case_id": "_ratio_", "why": f"小样本例外：模块 {short_mods_p} 无 P0 用例（每模块 ≥1 条）"})
            bwp = p0_th.get("batch_warn_ratio", {})
            if bwp and ratio > bwp.get("max", 1):
                issues_p.append({"case_id": "_ratio_", "why": f"批内 P0 浓度 {ratio:.0%} 超告警线 {bwp.get('max')}（不自动降级）"})
        self.record("RATIO-P0", "P0 占比（gate+小样本）", "BLOCK", [i for i in issues_p if "超上限" in i["why"] or "至少" in i["why"]],
                    "P0 占比符合交付门禁")
        self.record("RATIO-P0-W", "P0 批内告警", "WARN", [i for i in issues_p if "告警线" in i["why"]], "批内 P0 浓度正常")

    # COV-PATH：异常/权限路径配比与豁免举证（方案 E7；豁免对象形态契约见 scripts/case_data.py 的 E5 校验）
    def _cov_path(self, cases):
        meta = cases.get("meta") or {}
        scenes = [s for s in (cases.get("scenarios") or []) if isinstance(s, dict)]
        if meta.get("flow_kind") not in {"workflow", "stateful"} or not scenes:
            self.record("COV-PATH", "异常与权限路径配比", "BLOCK", [],
                        "flow_kind=simple 无场景实体，路径配比不适用（覆盖由 FP/AC 引用链承担）")
            return
        tcs = cases.get("testcases", [])
        small_floor = (self.policy.get("thresholds", {}).get("small_sample", {}) or {}).get("case_count_below", 20)
        min_ratio = (self.policy.get("thresholds", {}).get("path_coverage", {}) or {}).get("exception_coverage_min", 0.5)
        below = len(tcs) < small_floor
        issues = []
        exception_ok = 0
        for scene in scenes:
            sid = scene.get("id", "?")
            branches = scene.get("paths") or {}
            na = scene.get("not_applicable") or {}
            for kind in ("exception", "permission"):
                has_paths = bool(branches.get(kind))
                entry = na.get(kind)
                evidenced = isinstance(entry, dict) and isinstance(entry.get("reason"), str) and entry["reason"].strip()
                if not has_paths and not evidenced:
                    why = f"{kind} 路径为空且 not_applicable 未按对象形态举证（reason/business_object/checked_at）"
                    if isinstance(entry, str) and entry.strip():
                        why = f"{kind} 豁免仍为字符串形态，未按对象形态举证，不得进入执行"
                    issues.append({"case_id": sid, "why": why})
            if branches.get("exception"):
                exception_ok += 1
        coverage = exception_ok / len(scenes) if scenes else 0.0
        if coverage < min_ratio:
            if below:
                issues.append({"case_id": "_coverage_",
                               "why": f"exception_coverage={coverage:.0%} 低于 {min_ratio}（小样本例外：降为告警，不阻断）"})
            else:
                issues.append({"case_id": "_coverage_",
                               "why": f"exception_coverage={exception_ok}/{len(scenes)}={coverage:.0%} 低于 {min_ratio}（有异常路径的场景占比过低）"})
        if below:
            self.record("COV-PATH", "异常与权限路径配比", "WARN", issues,
                        f"小样本例外生效（{len(tcs)} 条 < {small_floor}）：exception_coverage={coverage:.0%}，未举证项仍须修订")
        else:
            self.record("COV-PATH", "异常与权限路径配比", "BLOCK", issues,
                        f"exception_coverage={coverage:.0%} ≥ {min_ratio} 且豁免均已按对象形态举证")

    # COV-UI：界面/无障碍维度为零时必须逐类裁定，不得静默跳过（E4）
    def _cov_ui(self, cases, tcs):
        rule = self.policy.get("gate_rules", {}).get("cov_ui", {})
        attrs = rule.get("required_attributes", ["visual", "accessibility"])
        exempt = set(rule.get("exempt_states", ["not_applicable", "insufficient_evidence", "deferred"]))
        applicability = cases.get("applicability") if isinstance(cases.get("applicability"), dict) else {}
        issues = []
        for attr in attrs:
            count = sum(1 for tc in tcs if attr in (tc.get("attributes") or []))
            if count:
                continue
            entry = applicability.get(attr)
            state = entry.get("state") if isinstance(entry, dict) else None
            reason = entry.get("reason") if isinstance(entry, dict) else None
            if state in exempt and isinstance(reason, str) and reason.strip():
                continue
            issues.append({"case_id": "_ui_", "why": f"{attr} 用例为 0 且 applicability 未给出 {sorted(exempt)} 裁定与理由"})
        self.record("COV-UI", "界面与无障碍零覆盖举证", "BLOCK", issues,
                    f"{'/'.join(attrs)} 有用例或已逐类裁定不覆盖")

    def _summary(self, cases, t0):
        tcs = cases.get("testcases", [])
        failed = [c for c in self.checks if c["status"] == "failed"]
        warned = [c for c in self.checks if c["status"] == "warning"]
        return {
            "status": "fail" if failed else ("warning" if warned else "pass"),
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "counts": {
                "testcases": len(tcs),
                "smoke": sum(1 for tc in tcs if "smoke" in (tc.get("suites") or [])),
                "p0": sum(1 for tc in tcs if tc.get("priority") == "P0"),
            },
            "checks": self.checks,
        }


def main():
    ap = argparse.ArgumentParser(description="case-generator 门禁判定（只报不改）")
    ap.add_argument("--cases", required=True, help="cases-<short>.json 路径")
    ap.add_argument("--policy", default=str(DEFAULT_POLICY), help="policy.json（默认包内）")
    ap.add_argument("--scope", help="范围清单 JSON（G7 校验可生成范围）")
    ap.add_argument("--json", action="store_true", help="输出结构化 JSON 报告")
    args = ap.parse_args()

    path = Path(args.cases)
    if not path.is_file():
        print(f"输入不存在：{path}", file=sys.stderr)
        sys.exit(2)
    try:
        cases = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"输入解析失败：{exc}", file=sys.stderr)
        sys.exit(2)
    try:
        policy = json.loads(Path(args.policy).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"policy 解析失败：{exc}", file=sys.stderr)
        sys.exit(2)
    scope_leaf_ids = None
    if args.scope:
        try:
            scope_leaf_ids = set(json.loads(Path(args.scope).read_text(encoding="utf-8-sig")).get("leaf_ids", []))
        except (OSError, json.JSONDecodeError):
            scope_leaf_ids = None

    report = GateRunner(policy).run(cases, scope_leaf_ids)
    report["file"] = str(path)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"gate: {report['status']}  counts: {json.dumps(report['counts'], ensure_ascii=False)}  elapsed: {report.get('elapsed_ms')}ms")
        for c in report["checks"]:
            mark = {"passed": "PASS", "warning": "WARN", "failed": "FAIL"}[c["status"]]
            print(f"  [{mark}] {c['id']} {c['name']}")
            for i in c["issues"][:10]:
                print(f"        - {i.get('case_id', '')}: {i.get('why', '')}")
    sys.exit(0 if report["status"] == "pass" else (1 if report["status"] == "fail" else 0))


if __name__ == "__main__":
    main()