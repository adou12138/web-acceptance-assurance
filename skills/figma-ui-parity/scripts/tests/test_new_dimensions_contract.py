#!/usr/bin/env python3
"""新维度与配套能力的不变式测试（7.40–7.45 / 档位 / 清理台账 / 定位可信度）。

锁的是什么：
1. 每个新维度在维度表里登记，且 owner 指向真实存在的 reference（防止"文档没写"）；
2. 页内脚本全文无反斜杠（MCP 传参会吃掉一层反斜杠，静默改语义）；
3. 可访问性产物必须自报覆盖边界，不得让调用方误以为"通过 WCAG AA"；
4. 执行档位引用的维度必须都在维度表里（档位写错编号会让该维度静默不跑）；
5. 清理台账必须拒收"没声明 reversible"的条目（缺省任何一种语义都是错的）；
6. 定位可信度分级与幂等。
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def _dims():
    data = json.loads((ROOT / "schemas" / "check-dimensions.json").read_text(encoding="utf-8"))
    return {d["id"]: d for d in data["dimensions"]}


class NewDimensionContractTests(unittest.TestCase):
    def test_740_to_745_registered_with_existing_owner(self):
        dims = _dims()
        for dim_id, owner, script in (
                ("7.40", "references/performance.md", "run_performance.cjs"),
                ("7.41", "references/a11y-rules.md", "run_a11y_audit.cjs"),
                ("7.42", "references/design-contrast.md", "design_contrast.py"),
                ("7.43", "references/keyboard-a11y.md", "run_keyboard_order.cjs"),
                ("7.44", "references/performance.md", "response_timer.cjs"),
                ("7.45", "references/runtime-observability.md", "runtime_observability.js")):
            self.assertIn(dim_id, dims, "维度 {} 未登记".format(dim_id))
            self.assertEqual(dims[dim_id]["owner"], owner)
            self.assertTrue((ROOT / owner).exists(), "owner 指向的文档不存在：{}".format(owner))
            self.assertIn(script, dims[dim_id]["script"], "{} 的执行脚本应指向 {}".format(dim_id, script))

    def test_733_scope_covers_network_matrix(self):
        """7.33 的范围声明必须写明三种网络条件：只写"接口异常"会让 code 只实现 500 而不被发现。"""
        dims = _dims()
        scope = dims["7.33"]["scope"]
        for token in ("500", "断网", "弱网"):
            self.assertIn(token, scope, "7.33 scope 缺少网络条件：{}".format(token))
        ref = (ROOT / "references" / "bug-driven-checks.md").read_text(encoding="utf-8")
        self.assertIn("http-500", ref)
        self.assertIn("offline", ref)
        self.assertIn("catch", ref, "必须写明断网走的是 catch 分支，否则会被当成 500 的重复项")

    def test_network_matrix_never_passes_with_unexecuted_condition(self):
        """部分条件没跑成不能判 pass——把"没测过"记成通过是最难发现的一类假通过。"""
        src = (SCRIPTS / "node" / "run_state_scenarios.cjs").read_text(encoding="utf-8")
        self.assertIn("if (skipped.length && status === 'pass') status = 'pending';", src)
        self.assertIn("全部网络条件均未执行", src)

    def test_response_timer_is_not_inp(self):
        """首响应时延不是 INP：文档与实现都必须自己声明这一点。"""
        ref = (ROOT / "references" / "performance.md").read_text(encoding="utf-8")
        self.assertIn("这不是 INP", ref)
        note = (SCRIPTS / "node" / "response_timer.cjs").read_text(encoding="utf-8")
        self.assertIn("不是 INP 数值", note)
        self.assertIn("MutationObserver", note,
                      "必须用真实 DOM 变更事件计时；轮询 + 差值阈值会把小 toast 这类真实响应压掉")
        self.assertIn("单向", note, "必须写明偏差方向，否则读报告的人会当成精确值")

    def test_payload_kind_gate_present(self):
        """非字符串 body（FormData/Blob）不得参与载荷判定：字符串化产物是采集侧造的。"""
        src = (SCRIPTS / "runtime_observability.js").read_text(encoding="utf-8")
        self.assertIn("bodyKind", src)
        self.assertIn("[object FormData]", (ROOT / "references" / "runtime-observability.md").read_text(encoding="utf-8"))

    def test_inpage_scripts_have_no_backslash(self):
        """页内注入脚本全文不得出现反斜杠：MCP 传参吃一层会让正则/转义静默改语义。"""
        for name in ("performance_probe.js", "a11y_audit.js", "keyboard_order.js"):
            src = (SCRIPTS / name).read_text(encoding="utf-8")
            self.assertNotIn("\\", src, "{} 出现了反斜杠".format(name))

    def test_a11y_audit_declares_coverage_boundary(self):
        """内置规则是 WCAG 子集，产物必须写明"不是全量合规结论"——否则等于假合规。"""
        src = (SCRIPTS / "a11y_audit.js").read_text(encoding="utf-8")
        self.assertIn("不是全量合规结论", src)
        self.assertIn("rulesCoveredCount", src)
        ref = (ROOT / "references" / "a11y-rules.md").read_text(encoding="utf-8")
        self.assertIn("不得声称", ref)
        self.assertIn("7.19", ref, "必须写清与 7.19 的分工，否则会被当成重复维度")

    def test_performance_documents_throttling_discipline(self):
        """口径必须写进文档（这里是文档契约检查；**行为**由 performance_probe_smoke.cjs 锁）。

        刻意不做"源码里出现某某字符串"这类断言：注释里写一句就能让它恒真，
        等于没测。真正的行为断言在 Node 冒烟里真跑 install→collect 两阶段。
        """
        ref = (ROOT / "references" / "performance.md").read_text(encoding="utf-8")
        self.assertIn("只记 `warn`", ref)
        self.assertIn("CLS 没有 buffered 入口", ref, "必须写明为什么分两阶段，否则会被合并成一次采集")
        self.assertIn("lcpFinalized", ref, "必须写明 LCP 采集时刻可能未 finalize，判定偏乐观")
        src = (SCRIPTS / "performance_probe.js").read_text(encoding="utf-8")
        self.assertIn("emitVitals", src, "判定入口应集中在一处，便于口径审计")

    def test_run_profiles_only_reference_known_dimensions(self):
        """档位里写错维度编号不会报错，只会让那个维度静默不跑或被凭空排除。"""
        dims = set(_dims())
        profiles = json.loads((ROOT / "assets" / "run_profiles.json").read_text(encoding="utf-8"))
        for name, entry in profiles["profiles"].items():
            raw = entry.get("dimensions")
            if raw == "all":
                continue
            self.assertIsInstance(raw, list, "档位 {} 的 dimensions 必须是数组或 all".format(name))
            unknown = [d for d in raw if d not in dims]
            self.assertEqual(unknown, [], "档位 {} 引用了不存在的维度：{}".format(name, unknown))

    def test_smoke_profile_is_subset_of_standard(self):
        """冒烟档必须被标准档覆盖：否则"先跑冒烟再跑标准"会丢掉冒烟专属的维度。"""
        profiles = json.loads((ROOT / "assets" / "run_profiles.json").read_text(encoding="utf-8"))["profiles"]
        smoke = set(profiles["smoke"]["dimensions"])
        standard = set(profiles["standard"]["dimensions"])
        self.assertTrue(smoke.issubset(standard),
                        "冒烟档有标准档未包含的维度：{}".format(sorted(smoke - standard)))


class LocatorQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, str(SCRIPTS))
        import locator_quality
        cls.LQ = locator_quality

    def test_grading(self):
        cases = [
            ("[data-testid=submit]", 0, "stable"),
            ("a[role=button]", 0, "stable"),
            ("input[name=phone]", 0, "stable"),
            ("#submitBtn", 0, "semi"),
            ("button.btn-primary", 0, "semi"),
            ("div", 0, "fragile"),
            (".css-1x2y3z", 0, "fragile"),
            ("#:r1:", 0, "fragile"),
            ("", 0, "none"),
        ]
        for selector, nth, expected in cases:
            self.assertEqual(self.LQ.grade(selector, nth)["quality"], expected,
                             "{} 应为 {}".format(selector or "(空)", expected))

    def test_nth_degrades_quality(self):
        """nth>0 靠序号区分同类元素：语义锚点也只能降级。"""
        self.assertEqual(self.LQ.grade("[data-testid=item]", 2)["quality"], "semi")
        self.assertEqual(self.LQ.grade("#btn", 1)["quality"], "fragile")

    def test_enrich_is_idempotent_and_respects_existing(self):
        once = self.LQ.enrich_element({"selector": "div", "nth": 0})
        twice = self.LQ.enrich_element(once)
        self.assertEqual(once, twice, "enrich_element 必须幂等")
        declared = self.LQ.enrich_element({"selector": "div", "nth": 0, "locatorQuality": "stable"})
        self.assertEqual(declared["locatorQuality"], "stable", "producer 已声明的判断不得被覆盖")

    def test_fragile_only_gets_advice(self):
        fragile = self.LQ.grade("div", 0)
        stable = self.LQ.grade("[data-testid=x]", 0)
        self.assertTrue(fragile["advice"])
        self.assertEqual(stable["advice"], "", "稳定定位不该再给建议（噪音）")


class WriteOpsLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, str(SCRIPTS))
        import write_ops_ledger
        cls.ledger = write_ops_ledger

    def test_requires_explicit_reversible(self):
        problems = self.ledger.validate({"action": "创建", "target": "/x", "safetyLevel": "danger"})
        self.assertTrue(any("reversible" in p for p in problems), problems)

    def test_reversible_requires_cleanup(self):
        problems = self.ledger.validate({"action": "创建", "target": "/x", "safetyLevel": "danger",
                                         "reversible": True})
        self.assertTrue(any("cleanup" in p for p in problems), problems)

    def test_cleanup_method_must_be_known(self):
        problems = self.ledger.validate({
            "action": "创建", "target": "/x", "safetyLevel": "danger", "reversible": True,
            "cleanup": {"method": "magic", "target": "somewhere"}})
        self.assertTrue(any("cleanup.method" in p for p in problems), problems)

    def test_irreversible_needs_no_cleanup(self):
        problems = self.ledger.validate({"action": "清空回收站", "target": "/recycle",
                                         "safetyLevel": "danger", "reversible": False})
        self.assertEqual(problems, [], "不可恢复项不该被要求提供 cleanup")

    def test_bad_safety_level_rejected(self):
        problems = self.ledger.validate({"action": "x", "target": "/y", "safetyLevel": "safe",
                                         "reversible": False})
        self.assertTrue(any("safetyLevel" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
