# -*- coding: utf-8 -*-
"""第九轮修复的锁定测试（F59 模式池命中 / F61 待确认渲染 / F62 容器键）。

纪律：断言走**真实 CLI 入口**而不是复刻生产逻辑；每条都配变异反向用例。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPTS = REPO / "scripts"


def _run(args, **kw):
    return subprocess.run([sys.executable] + args, capture_output=True, text=True, cwd=str(REPO), **kw)


class TestF59PatternPoolCuratedHit(unittest.TestCase):
    """已沉淀（无 fingerprint / 无 property）的模式必须能被复发缺陷命中。

    旧版：curated 条目重算指纹时没有 property，而入参缺陷几乎都带 property ⇒ 指纹恒
    不等 ⇒ 31 条模式的 hits 永不累加（一直是 1，rank 的依据是假的），且每次复发都
    追加一条 DP-AUTO 副本，池单调膨胀。
    """

    CURATED = {
        "id": "DP-ICON-RENDER", "dimension": "7.5",
        "symptom": "图标未加载：图标字体未生效或 CDN 资源不可达",
        "assertion": "图标按设计稿渲染", "severity": "P2", "source": "curated",
    }

    def _pool(self, d):
        p = os.path.join(d, "pool.json")
        json.dump({"version": "1.0", "patterns": [dict(self.CURATED)]},
                  open(p, "w", encoding="utf-8"), ensure_ascii=False)
        return p

    def _report(self, d, name, items):
        r = os.path.join(d, name)
        json.dump({"defects": items}, open(r, "w", encoding="utf-8"), ensure_ascii=False)
        return r

    def _merge(self, pool, report):
        p = _run([str(SCRIPTS / "pattern_pool.py"), "--pool", pool, "--merge", report])
        self.assertEqual(p.returncode, 0, p.stderr[-300:])
        return json.load(open(pool, encoding="utf-8"))

    def test_curated_pattern_hit_instead_of_duplicate(self):
        symptom = self.CURATED["symptom"]
        item = [{"dimension": "7.5", "property": "fontIcon", "symptom": symptom,
                 "expected": "图标渲染", "severity": "P2", "fingerprint": "fp-1"}]
        with tempfile.TemporaryDirectory() as d:
            pool = self._pool(d)
            rep = self._report(d, "r1.json", item)
            after = self._merge(pool, rep)
            self.assertEqual(len(after["patterns"]), 1,
                             "命中已有模式时不得再追加副本：%s" % [p["id"] for p in after["patterns"]])
            self.assertEqual(after["patterns"][0]["id"], "DP-ICON-RENDER")
            self.assertEqual(after["patterns"][0]["hits"], 2,
                             "复发必须累加命中次数（旧版恒为 1，rank 无依据）")

    def test_repeat_merge_accumulates_without_growth(self):
        symptom = self.CURATED["symptom"]
        item = [{"dimension": "7.5", "property": "fontIcon", "symptom": symptom,
                 "expected": "图标渲染", "severity": "P2", "fingerprint": "fp-1"}]
        with tempfile.TemporaryDirectory() as d:
            pool = self._pool(d)
            for i in range(3):
                rep = self._report(d, "r%d.json" % i, item)
                after = self._merge(pool, rep)
            self.assertEqual(len(after["patterns"]), 1, "重复并入不得增长")
            self.assertEqual(after["patterns"][0]["hits"], 4, "每次并入累加 1（缺省按 1 起算）")

    def test_unknown_pattern_still_added_and_keeps_property(self):
        item = [{"dimension": "7.39", "property": "history-back",
                 "symptom": "返回后页面空白", "expected": "内容恢复", "severity": "P1",
                 "fingerprint": "fp-new"}]
        with tempfile.TemporaryDirectory() as d:
            pool = self._pool(d)
            rep = self._report(d, "r1.json", item)
            after = self._merge(pool, rep)
            self.assertEqual(len(after["patterns"]), 2, "新模式必须入库")
            new = after["patterns"][-1]
            self.assertEqual(new["property"], "history-back",
                             "property 必须随条目落盘——否则下一轮重算指纹会再次失配")

    def test_exact_fingerprint_wins_over_legacy_key(self):
        """同一 (dimension, symptom) 既有自动条目（精确指纹）又有沉淀条目时，走精确匹配。

        兜底索引只是为了让沉淀模式「可被命中」，不能反过来抢走自动条目按属性区分的粒度。
        """
        sys.path.insert(0, str(SCRIPTS))
        try:
            import pattern_pool as P
            auto_item = {"dimension": "7.5", "property": "color", "symptom": "主色偏暗"}
            auto_fp = P.fingerprint(auto_item)
        finally:
            sys.path.remove(str(SCRIPTS))
        curated = dict(self.CURATED)
        curated["symptom"] = "主色偏暗"          # 与自动条目同 (dimension, symptom)
        auto = {"id": "DP-AUTO-0001", "dimension": "7.5", "property": "color",
                "symptom": "主色偏暗", "fingerprint": auto_fp, "hits": 1}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "pool.json")
            json.dump({"version": "1.0", "patterns": [curated, auto]},
                      open(p, "w", encoding="utf-8"), ensure_ascii=False)
            rep = self._report(d, "r1.json", [dict(auto_item, expected="同设计稿")])
            after = self._merge(p, rep)
            self.assertEqual(len(after["patterns"]), 2, "命中已有条目时不得新增")
            by_id = {x["id"]: x for x in after["patterns"]}
            self.assertEqual(by_id["DP-AUTO-0001"]["hits"], 2, "精确指纹命中应落在该条目上")
            self.assertIsNone(by_id["DP-ICON-RENDER"].get("hits"),
                              "不得被兜底索引抢走到沉淀条目上")


class TestF63ExcelFormulaGuard(unittest.TestCase):
    """Excel 交付件的公式注入防护必须挂在两处写值路径上。"""

    def test_helper_prefixes_formula_text_only(self):
        sys.path.insert(0, str(SCRIPTS))
        try:
            import gen_excel_fallback as G
            self.assertEqual(G._xl_safe('=cmd|\'/c calc\'!A1'), "'=cmd|'/c calc'!A1")
            self.assertEqual(G._xl_safe('@SUM(A1)'), "'@SUM(A1)")
            self.assertEqual(G._xl_safe('正常描述文本'), '正常描述文本')
            self.assertEqual(G._xl_safe(5), 5, "数值不得被加引号成文本")
            self.assertIsNone(G._xl_safe(None))
        finally:
            sys.path.remove(str(SCRIPTS))

    def test_both_write_sites_use_the_guard(self):
        """两条 ws.cell / pws.cell 写值路径都必须过 _xl_safe（漏一条就是漏一个 sheet）。"""
        import ast

        tree = ast.parse((SCRIPTS / "gen_excel_fallback.py").read_text(encoding="utf-8"))
        guarded = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "cell":
                if isinstance(node.keywords[-1].value, ast.Call) if node.keywords else False:
                    fn = node.keywords[-1].value.func
                    if getattr(fn, "id", None) == "_xl_safe":
                        line = node.lineno
                        args = node.args
                        for a in args:
                            if isinstance(a, ast.Call) and getattr(a.func, "id", None) == "_xl_safe":
                                line = node.lineno
                        guarded.add(line)
                for a in node.args:
                    if isinstance(a, ast.Call) and getattr(a.func, "id", None) == "_xl_safe":
                        guarded.add(node.lineno)
        self.assertGreaterEqual(len(guarded), 2,
                                "写值路径未全部挂 _xl_safe（当前命中行 %s）" % sorted(guarded))


class TestF62ViolationsContainer(unittest.TestCase):
    """generate_reports 必须认 `violations` 容器键（其余 consumer 都认）。"""

    def test_violations_only_payload_is_not_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "constraints.json")
            json.dump({"url": "https://e.test", "checkedAssertions": 8,
                       "violations": [
                           {"dimension": "7.1", "severity": "P1", "confidence": 0.9,
                            "status": "confirmed", "symptom": "断点间容器宽度跳变",
                            "expected": "跟随断点", "actual": "375px 下溢出"}]},
                      open(src, "w", encoding="utf-8"), ensure_ascii=False)
            out = os.path.join(d, "out")
            p = _run([str(SCRIPTS / "generate_reports.py"), "--input", src, "--outdir", out])
            self.assertEqual(p.returncode, 0, p.stderr[-300:])
            self.assertIn('"defects": 1', p.stdout,
                          "violations 型输入不得静默产 0 条（实测旧版 0 条且无告警）")


class TestF61PendingRenderFields(unittest.TestCase):
    """待确认条目按 report-templates.md 的字段名写时必须渲染得出来。"""

    def test_pending_checks_fields_render(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "report.json")
            json.dump({"url": "https://e.test", "defects": [],
                       "pendingChecks": [
                           {"name": "修改名称图标", "category": "图标形状(异构需视觉核对)",
                            "verdict": "需视觉核对",
                            "reason": "设计稿 SVG 与页面图标字体异构"}]},
                      open(src, "w", encoding="utf-8"), ensure_ascii=False)
            out = os.path.join(d, "out")
            p = _run([str(SCRIPTS / "generate_reports.py"), "--input", src, "--outdir", out])
            self.assertEqual(p.returncode, 0, p.stderr[-300:])
            html = (Path(out) / "report.html").read_text(encoding="utf-8")
            self.assertIn("修改名称图标", html, "name 必须落到「现象」列（旧版整列空白）")
            self.assertIn("图标形状(异构需视觉核对)", html, "category 必须落到「维度」列")


if __name__ == "__main__":
    unittest.main()
