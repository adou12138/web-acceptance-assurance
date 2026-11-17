#!/usr/bin/env python3
"""End-to-end smoke test: chain real script outputs through the whole pipeline.

为什么需要它：v4.2.0 曾出现「每个脚本用手工 fixture 单测全绿，但真实产物一串联
就断」——生产者产 severityHint 而校验端要求 severity，归一脚本只认 diffs 键而
生产者写 styleDiffs/findings。本脚本用真实脚本产物（不是手写 defect）串起：

  extract_design_fields -> compare_fields -> validate_defects --strict
                        -> normalize_evidence -> generate_reports (CSV 等级列)
  discover_scenarios (截断记录) / assert_constraints (缺 frameWidth 不崩溃)
  ignore_regions --enforce (宽泛忽略项必须拦截) / coverage_gate

任何脚本的字段契约改动都要先跑本文件：全绿才允许交付。
Exit 0 = all checks passed; exit 1 = at least one check failed.
"""
from __future__ import annotations
import csv, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
S = HERE  # scripts/ 目录

FIGMA_DUMP = {
    "nodes": {"1:1": {"document": {
        "id": "1:1", "name": "Frame", "type": "FRAME",
        "absoluteBoundingBox": {"x": 0, "y": 0, "width": 375, "height": 200},
        "children": [
            {"id": "1:2", "name": "submit", "type": "FRAME", "visible": True,
             "absoluteBoundingBox": {"x": 20, "y": 30, "width": 100, "height": 40},
             "fills": [{"type": "SOLID", "visible": True, "color": {"r": 0.1, "g": 0.4, "b": 0.9, "a": 1}}],
             "constraints": {"vertical": "TOP", "horizontal": "LEFT"},
             "children": [
                 {"id": "1:3", "name": "submit-label", "type": "TEXT", "visible": True,
                  "absoluteBoundingBox": {"x": 40, "y": 40, "width": 60, "height": 20},
                  "characters": "提交",
                  "style": {"fontFamily": "PingFang SC", "fontName": {"family": "PingFang SC", "style": "Medium"},
                            "fontWeight": 500, "fontSize": 14,
                            "lineHeight": {"unit": "PIXELS", "value": 20},
                            "letterSpacing": {"unit": "PERCENT", "value": 2}},
                  "fills": [{"type": "SOLID", "visible": True, "color": {"r": 1, "g": 1, "b": 1, "a": 1}}]},
             ]},
            {"id": "1:4", "name": "overlay", "type": "FRAME", "visible": True,
             "absoluteBoundingBox": {"x": 0, "y": 80, "width": 200, "height": 60},
             "fills": [{"type": "SOLID", "visible": True, "color": {"r": 0, "g": 0, "b": 0, "a": 0.3}}]},
            {"id": "1:5", "name": "rot-badge", "type": "FRAME", "visible": True,
             "rotation": 45,
             "absoluteBoundingBox": {"x": 220, "y": 30, "width": 80, "height": 80},
             "fills": [{"type": "SOLID", "visible": True, "color": {"r": 0.9, "g": 0.2, "b": 0.2, "a": 1}}]},
        ]}}}}

PAGE = {
    "url": "https://example.test/page", "viewport": {"w": 375, "h": 800, "dpr": 2},
    "buttons": [{"selector": "button.submit", "nth": 0, "text": "提交", "tag": "button",
                 "rect": {"x": 20, "y": 30, "w": 100, "h": 40}}],
    "containers": [{"selector": "div.overlay-panel", "nth": 0,
                    "rect": {"x": 0, "y": 80, "w": 200, "h": 60}}],
    "inputs": [{"selector": "input.name", "nth": 0, "type": "text", "placeholder": "请输入姓名",
                "rect": {"x": 20, "y": 120, "w": 200, "h": 36}}],
    "images": [], "modals": [], "scrollables": [], "emptyStateCandidates": [],
    "interactionKinds": ["tabs"], "forms": 1,
}


def run(cmd):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True)


def _find_node() -> str:
    """定位 node 可执行文件：PATH 优先，其次常见安装位置。

    Git Bash 下 `node` 可能不在 Python 子进程的 PATH 里（只有 nodejs 目录下的
    node.exe），只靠 shutil.which 会 FileNotFoundError。
    """
    found = shutil.which("node") or shutil.which("node.exe")
    if found:
        return found
    for c in (os.environ.get("NODE"), r"C:\Program Files\nodejs\node.exe",
              "/usr/local/bin/node", "/usr/bin/node"):
        if c and Path(c).exists():
            return c
    # 【F64】旧版兜底返回一个"肯定不存在"的 "node"，于是每条 node 用例都以
    # FileNotFoundError 崩在 subprocess 上——症状是 Traceback，而真因是环境缺 Node，
    # 排查方向被带偏到"脚本有 bug"。环境缺依赖要说人话。
    raise SystemExit(
        "未找到 node 可执行文件：请安装 Node.js，或设置环境变量 NODE 指向 node.exe "
        "（已探测 PATH 与 C:\\Program Files\\nodejs\\node.exe）")


class Checker:
    def __init__(self):
        self.results = []

    def check(self, name, ok, detail=""):
        self.results.append((name, bool(ok), detail))
        print("[{}] {}{}".format("PASS" if ok else "FAIL", name, " — " + detail if detail and not ok else ""))
        return bool(ok)

    def summary(self) -> int:
        failed = [r for r in self.results if not r[1]]
        print("\n==== 串链冒烟：{} 通过 / {} 失败 ====".format(len(self.results) - len(failed), len(failed)))
        return 1 if failed else 0


def main() -> int:
    ck = Checker()
    tmp = Path(tempfile.mkdtemp(prefix="fup_smoke_"))
    (tmp / "figma.json").write_text(json.dumps(FIGMA_DUMP, ensure_ascii=False), encoding="utf-8")
    (tmp / "page.json").write_text(json.dumps(PAGE, ensure_ascii=False), encoding="utf-8")
    py = sys.executable

    # 1) 设计侧建模
    r = run([py, S / "extract_design_fields.py", "--input", tmp / "figma.json", "--output", tmp / "design.json"])
    if not ck.check("extract_design_fields rc=0", r.returncode == 0, r.stderr[-300:]):
        return ck.summary()
    design = json.loads((tmp / "design.json").read_text(encoding="utf-8"))
    flat = {n["id"]: n for n in design["flat"]}
    ck.check("字重优先 API 数值（500 而非名称映射）", flat.get("1:3", {}).get("text", {}).get("fontWeight") == 500)
    ck.check("rotation 已记录（45）", flat.get("1:5", {}).get("rotation") == 45)

    # 2) compare_fields：真产物必须带 severity/confidence，expected/actual 必须是 string
    r = run([py, S / "compare_fields.py", "--design", tmp / "design.json", "--page", tmp / "page.json",
             "--page-url", PAGE["url"], "--output", tmp / "styleDiffs.json"])
    if not ck.check("compare_fields rc=0", r.returncode == 0, r.stderr[-300:]):
        return ck.summary()
    sd = json.loads((tmp / "styleDiffs.json").read_text(encoding="utf-8"))
    defects = sd.get("defects") or []
    ck.check("compare_fields 产出缺陷非空", len(defects) > 0, json.dumps(sd["summary"], ensure_ascii=False))
    contract_ok = all(
        isinstance(d.get("severity"), str) and d.get("severity") in ("P0", "P1", "P2", "P3")
        and isinstance(d.get("confidence"), (int, float))
        and isinstance(d.get("expected"), str) and isinstance(d.get("actual"), str)
        for d in defects)
    ck.check("缺陷契约：severity/confidence 齐全且 expected/actual 为 string", contract_ok)
    # P0-4：按钮 FRAME 应通过文本子节点 credit 或二次机会配上，不再假报 designOnly
    design_only_ids = [u.get("nodeId") for u in sd["unmatched"]["designOnly"]]
    ck.check("P0-4 按钮 FRAME 不再假报 designOnly", "1:2" not in design_only_ids, str(design_only_ids))
    ck.check("P0-4 matchedIndirect 或二次机会生效",
             sd["summary"].get("matchedIndirect", 0) > 0 or sd["summary"].get("pairRoles", {}).get("box-to-text-item", 0) > 0,
             json.dumps(sd["summary"], ensure_ascii=False))
    # P1-6：半透明填充不参与颜色比对
    ck.check("P1-6 半透明填充颜色比对被跳过", sd["summary"].get("colorCompareSkipped", 0) >= 1)
    # P1-13：旋转节点不产生位置/尺寸缺陷
    rotated_defects = [d for d in defects if d.get("designRef", {}).get("nodeId") == "1:5"
                       and d.get("property") in ("x", "y", "w", "h")]
    ck.check("P1-13 旋转节点不产出位置/尺寸缺陷", len(rotated_defects) == 0)
    # P0-3：配对数不足时不做中位归一
    ck.check("P0-3 少配对时 positionBasis=raw-insufficient-pairs",
             sd.get("positionBasis") in ("raw-insufficient-pairs", "raw", "explicit-frame-origin"),
             str(sd.get("positionBasis")))

    # 3) validate_defects --strict：真产物必须能过门禁（P0-1 核心断言）
    report = {"defects": defects}
    (tmp / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    r = run([py, S / "validate_defects.py", "--report", tmp / "report.json", "--strict"])
    ck.check("P0-1 真产物通过 validate_defects --strict", r.returncode == 0, r.stderr[-400:])

    # 4) normalize_evidence：必须读得到 styleDiffs（P0-2）
    r = run([py, S / "normalize_evidence.py", tmp / "styleDiffs.json", tmp / "norm.json"])
    ok = r.returncode == 0
    if ok:
        norm = json.loads((tmp / "norm.json").read_text(encoding="utf-8"))
        ck.check("P0-2 normalize_evidence 读 styleDiffs 非空", norm.get("total", 0) > 0,
                 "total={} sourceKey={}".format(norm.get("total"), norm.get("sourceKey")))
        r2 = run([py, S / "normalize_evidence.py", tmp / "report.json", tmp / "norm2.json"])
        norm2 = json.loads((tmp / "norm2.json").read_text(encoding="utf-8")) if r2.returncode == 0 else {}
        ck.check("P0-2 normalize_evidence 读 defects 非空", norm2.get("total", 0) > 0)
    else:
        ck.check("P0-2 normalize_evidence 读 styleDiffs 非空", False, r.stderr[-200:])

    # 5) generate_reports：CSV 等级列必须有值
    payload = dict(sd)
    payload["defects"] = defects
    (tmp / "payload.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    r = run([py, S / "generate_reports.py", "--input", tmp / "payload.json", "--outdir", tmp / "out"])
    ok = r.returncode == 0
    if ok:
        rows = list(csv.reader(open(tmp / "out" / "defects.csv", encoding="utf-8-sig")))
        grades = [row[1] for row in rows[1:]]
        ck.check("CSV 等级列全部非空", len(rows) > 1 and all(g.strip() for g in grades), str(grades))
    else:
        ck.check("CSV 等级列全部非空", False, r.stderr[-300:])

    # 6) discover_scenarios：截断记录存在
    r = run([py, S / "discover_scenarios.py", "--design", tmp / "design.json", "--page", tmp / "page.json",
             "--pairs", tmp / "styleDiffs.json", "--output", tmp / "scenarios.json"])
    ok = r.returncode == 0
    if ok:
        sc = json.loads((tmp / "scenarios.json").read_text(encoding="utf-8"))
        ck.check("discover_scenarios 产出场景且含截断字段", len(sc.get("scenarios", [])) > 0 and "truncation" in sc,
                 "scenarios={}".format(len(sc.get("scenarios", []))))
        # P2-16：第二个输入框的用例起点应轮转（单输入框场景验证存在异常数据即可）
        data_cases = [s for s in sc["scenarios"] if s.get("source") == "anomaly-data"]
        ck.check("异常数据场景已生成", len(data_cases) > 0)
        # v4.14.0：状态类场景必须带 runner（指向状态场景驱动），未接驱动的场景必须为 null
        runner_dims = {"7.25", "7.26", "7.32", "7.33"}
        bad_runner = [s["id"] for s in sc["scenarios"]
                      if (s.get("runner") == "node/run_state_scenarios.cjs") != (s["dimension"] in runner_dims)]
        ck.check("状态场景 runner 字段与维度一一对应", not bad_runner, str(bad_runner[:5]))
    else:
        ck.check("discover_scenarios 产出场景且含截断字段", False, r.stderr[-300:])

    # 6b) 运行期观测（7.35–7.38）：findings 形状必须能被 normalize_evidence 消费
    runtime_findings = {"url": PAGE["url"], "findings": [
        {"dimension": "7.35", "symptom": "运行期抛异常", "expected": "无未捕获错误", "actual": "onerror: boom",
         "severityHint": "P1", "severity": "P1", "confidence": 0.85, "status": "confirmed",
         "element": {"selector": "#a", "nth": 0}, "evidence": [{"type": "error-hook"}], "sources": ["runtime-observability"]},
    ]}
    (tmp / "runtime.json").write_text(json.dumps(runtime_findings, ensure_ascii=False), encoding="utf-8")
    r = run([py, S / "normalize_evidence.py", tmp / "runtime.json", tmp / "runtime_norm.json"])
    ok = r.returncode == 0 and json.loads((tmp / "runtime_norm.json").read_text(encoding="utf-8")).get("total", 0) > 0
    ck.check("运行期观测 findings 能被 normalize_evidence 消费", ok, r.stderr[-200:])

    # 6c) pattern_pool：风险排序产出 Top 列表 + 并入幂等
    r = run([py, S / "pattern_pool.py", "--rank", tmp / "payload.json", "--top", "3", "--out", tmp / "top.md"])
    ok = r.returncode == 0 and (tmp / "top.md").exists() and "Top" in (tmp / "top.md").read_text(encoding="utf-8")
    ck.check("pattern_pool --rank 产出 Top 列表", ok, r.stderr[-200:])
    pool_tmp = tmp / "pool.json"
    r1 = run([py, S / "pattern_pool.py", "--pool", pool_tmp, "--merge", tmp / "payload.json"])
    r2 = run([py, S / "pattern_pool.py", "--pool", pool_tmp, "--merge", tmp / "payload.json"])
    ok = r1.returncode == 0 and r2.returncode == 0 and "新增 0 条模式" in r2.stdout
    ck.check("pattern_pool --merge 幂等（二次并入新增 0 条）", ok, r2.stdout[-200:])

    # 6d) 7.40 / 7.41 产物 → assemble checks；档位记账；定位可信度
    # 新维度的核心风险是"产出了但没人消费"：driver 写 findings，assemble 不认，
    # checks 里就没有这两维，coverage_gate 判缺失、rc=5 整条链断掉。
    dims_path = S.parent / "schemas" / "check-dimensions.json"
    dim_ids = [d["id"] for d in json.loads(dims_path.read_text(encoding="utf-8"))["dimensions"]]
    new_ids = ("7.40", "7.41", "7.42", "7.43", "7.44", "7.45")
    ck.check("维度表含 7.40–7.45 且共 45 项",
             all(d in dim_ids for d in new_ids) and len(dim_ids) == 45,
             "n={} last={}".format(len(dim_ids), dim_ids[-1] if dim_ids else None))

    perf_payload = {"url": PAGE["url"], "findings": [{
        "dimension": "7.40", "status": "confirmed", "property": "vitals-lcp",
        "severity": "P1", "severityHint": "P1", "confidence": 0.85,
        "symptom": "LCP 5200ms，超出差阈值", "expected": "LCP <= 2500ms",
        "actual": "实测 5200ms（网络档 fast3g）",
        "element": {"selector": "", "nth": 0, "bbox": {"x": 0, "y": 0, "w": 0, "h": 0},
                    "anchor": "page-metrics"},
        "evidence": [{"type": "performance-entry"}], "sources": ["performance-probe"],
    }]}
    a11y_payload = {"url": PAGE["url"], "findings": [{
        "dimension": "7.41", "status": "pass", "property": "a11y-no-violation",
        "severity": "P3", "severityHint": "P3", "confidence": 0.8,
        "symptom": "内置规则集未发现违规", "expected": "已检查的 15 条规则全部通过",
        "actual": "img-alt(3)，button-name(1)", "element": None,
        "evidence": [{"type": "a11y-rule"}], "sources": ["a11y-audit"],
    }]}
    (tmp / "performance.json").write_text(json.dumps(perf_payload, ensure_ascii=False), encoding="utf-8")
    (tmp / "a11y_audit.json").write_text(json.dumps(a11y_payload, ensure_ascii=False), encoding="utf-8")
    (tmp / "rep2.json").write_text(json.dumps({"defects": [], "url": PAGE["url"]}, ensure_ascii=False),
                                  encoding="utf-8")
    r = run([py, S / "assemble_report.py", "--report", tmp / "rep2.json",
             "--findings", tmp / "performance.json", "--findings", tmp / "a11y_audit.json",
             "--profile", "smoke", "--write"])
    if ck.check("assemble_report --profile 跑通（含新维度产物）", r.returncode == 0, r.stderr[-300:]):
        rep2 = json.loads((tmp / "rep2.json").read_text(encoding="utf-8"))
        checks = rep2["meta"]["checks"]
        ck.check("7.40 由性能产物汇总为 fail", checks.get("7.40", {}).get("status") == "fail",
                 json.dumps(checks.get("7.40"), ensure_ascii=False))
        ck.check("7.41 由可访问性产物汇总为 pass（首次运行即可判定）",
                 checks.get("7.41", {}).get("status") == "pass",
                 json.dumps(checks.get("7.41"), ensure_ascii=False))
        rp2 = rep2["meta"].get("runProfile") or {}
        ck.check("档位把「要求但漏跑」单列（requiredButMissing）",
                 "7.22" in (rp2.get("requiredButMissing") or []),
                 json.dumps(rp2.get("requiredButMissing"), ensure_ascii=False))
        ck.check("档位外的维度原因写明「未纳入」",
                 "未纳入" in str(checks.get("7.26", {}).get("reason")),
                 str(checks.get("7.26", {}).get("reason"))[:100])
        ck.check("档位不改已执行维度的判定（7.41 仍 pass）",
                 checks.get("7.41", {}).get("status") == "pass")
        ck.check("缺陷元素补齐 locatorQuality",
                 all("locatorQuality" in (x.get("element") or {}) for x in rep2.get("defects", [])),
                 json.dumps([(x.get("element") or {}).get("locatorQuality") for x in rep2.get("defects", [])],
                            ensure_ascii=False))

    # 6e) 写操作清理台账：登记校验 / 审计拦截 / 并入待确认幂等
    (tmp / "op.json").write_text(json.dumps({
        "action": "创建测试问卷", "target": "/survey/create", "safetyLevel": "danger",
        "reversible": True,
        "cleanup": {"method": "api", "target": "DELETE /api/survey/{id}", "evidence": "列表页 0 条"},
    }, ensure_ascii=False), encoding="utf-8")
    r = run([py, S / "write_ops_ledger.py", "--file", tmp / "ledger.json",
             "--add-file", tmp / "op.json", "--audit", "--enforce"])
    ck.check("台账：未清理的可回收写操作被 --enforce 拦截", r.returncode == 1,
             "rc={} err={}".format(r.returncode, (r.stderr or "")[-160:]))
    (tmp / "op_bad.json").write_text(json.dumps({
        "action": "删除配置项", "target": "/admin/config", "safetyLevel": "caution",
    }, ensure_ascii=False), encoding="utf-8")
    r = run([py, S / "write_ops_ledger.py", "--file", tmp / "ledger.json",
             "--add-file", tmp / "op_bad.json"])
    ck.check("台账：缺 reversible 的条目被拒收（rc=2）", r.returncode == 2, "rc={}".format(r.returncode))
    r1 = run([py, S / "write_ops_ledger.py", "--file", tmp / "ledger.json",
              "--merge-pending", "--report", tmp / "rep2.json"])
    r2 = run([py, S / "write_ops_ledger.py", "--file", tmp / "ledger.json",
              "--merge-pending", "--report", tmp / "rep2.json"])
    ok = r1.returncode == 0 and r2.returncode == 0
    merged2 = json.loads(r2.stdout).get("merged") if ok and r2.stdout.strip() else None
    ck.check("台账 --merge-pending 幂等（二次并入 0 条）", ok and merged2 == 0,
             "rc={}/{} merged2={} {}".format(r1.returncode, r2.returncode, merged2, (r2.stderr or "")[-160:]))
    rep3 = json.loads((tmp / "rep2.json").read_text(encoding="utf-8"))
    ck.check("台账未清理项进入 report.pendingChecks",
             any("写操作" in str(x.get("name")) for x in rep3.get("pendingChecks", [])),
             json.dumps(rep3.get("pendingChecks"), ensure_ascii=False)[:200])

    # 6f) 页内探针的行为验证必须进串链：Python 侧只能查"文档口径写没写"，
    # "无节流不许确认缺陷""缺观察器 CLS 必须是 null"这类判据只有真跑才能锁住。
    for smoke, label in (("performance_probe_smoke.cjs", "性能探针"),
                         ("a11y_audit_smoke.cjs", "可访问性审计"),
                         ("keyboard_order_smoke.cjs", "键盘顺序")):
        path = S / "tests" / smoke
        if not path.exists():
            ck.check("{} 冒烟脚本存在（{}）".format(label, smoke), False, "文件缺失")
            continue
        try:
            node_bin = _find_node()
        except SystemExit as exc:
            # 环境缺 Node 不该让整条冒烟链自己崩掉（那样连"哪一项没过"都看不到）
            ck.check("{} 行为冒烟全绿（{}）".format(label, smoke), False, str(exc))
            continue
        r = run([node_bin, str(path)])
        ck.check("{} 行为冒烟全绿（{}）".format(label, smoke), r.returncode == 0,
                 (r.stdout or "")[-300:] + (r.stderr or "")[-200:])

    # 6g) 7.42 设计稿侧对比度：设计模型 → findings → assemble checks
    # 这条链的风险是"产出了但没人消费"：设计侧 findings 的维度号写错、
    # 或 assemble 不认这个产物，7.42 就会永远停在 notRun 而没人发现。
    design_model = {"rootId": "root", "tree": {
        "id": "root", "name": "页", "type": "FRAME", "visible": True, "opacity": 1,
        "rect": {"x": 0, "y": 0, "w": 375, "h": 400},
        "fills": [{"type": "SOLID", "visible": True, "opacity": 1,
                   "color": {"rgb": [255, 255, 255], "hex": "#ffffff", "alpha": 1, "css": "rgba(255,255,255,1)"}}],
        "children": [
            {"id": "t1", "name": "灰字", "type": "TEXT", "visible": True, "opacity": 1,
             "rect": {"x": 10, "y": 10, "w": 100, "h": 20},
             "fills": [{"type": "SOLID", "visible": True, "opacity": 1,
                        "color": {"rgb": [153, 153, 153], "hex": "#999999", "alpha": 1, "css": "rgba(153,153,153,1)"}}],
             "text": {"characters": "提交订单", "fontSize": 14, "fontWeight": 400}},
            {"id": "banner", "name": "Banner", "type": "FRAME", "visible": True, "opacity": 1,
             "rect": {"x": 0, "y": 40, "w": 375, "h": 120},
             "fills": [{"type": "IMAGE", "visible": True, "opacity": 1, "imageRef": "x", "scaleMode": "FILL"}],
             "children": [
                 {"id": "t2", "name": "图上字", "type": "TEXT", "visible": True, "opacity": 1,
                  "rect": {"x": 10, "y": 50, "w": 100, "h": 20},
                  "fills": [{"type": "SOLID", "visible": True, "opacity": 1,
                             "color": {"rgb": [255, 255, 255], "hex": "#ffffff", "alpha": 1, "css": "rgba(255,255,255,1)"}}],
                  "text": {"characters": "限时特惠", "fontSize": 20, "fontWeight": 700}}]},
        ]}}
    (tmp / "design_model.json").write_text(json.dumps(design_model, ensure_ascii=False), encoding="utf-8")
    dc_out = tmp / "design_contrast.json"
    r = run([py, S / "design_contrast.py", "--design", tmp / "design_model.json", "--out", dc_out])
    if ck.check("design_contrast 跑通（设计模型 → findings）", r.returncode == 0, r.stderr[-300:]):
        dc = json.loads(dc_out.read_text(encoding="utf-8"))
        confirmed = [f for f in dc["findings"] if f["status"] == "confirmed"]
        pending = [f for f in dc["findings"] if f["status"] == "pending"]
        ck.check("7.42 纯色底灰字被确认（2.85:1）",
                 len(confirmed) == 1 and confirmed[0]["raw"]["ratio"] == 2.85,
                 json.dumps([f["raw"].get("ratio") for f in confirmed], ensure_ascii=False))
        ck.check("7.42 图片底文字降级 pending（不按白底硬判）",
                 len(pending) == 1 and pending[0]["raw"].get("skipReason") == "image-backdrop",
                 json.dumps([f["raw"] for f in pending], ensure_ascii=False))
        (tmp / "rep42.json").write_text(json.dumps({"defects": [], "url": PAGE["url"]}, ensure_ascii=False),
                                        encoding="utf-8")
        r = run([py, S / "assemble_report.py", "--report", tmp / "rep42.json",
                 "--findings", dc_out, "--write"])
        if ck.check("design_contrast 产物能被 assemble 消费", r.returncode == 0, r.stderr[-300:]):
            checks42 = json.loads((tmp / "rep42.json").read_text(encoding="utf-8"))["meta"]["checks"]
            ck.check("7.42 由设计产物汇总为 fail", checks42.get("7.42", {}).get("status") == "fail",
                     json.dumps(checks42.get("7.42"), ensure_ascii=False))
            dims42 = [d.get("dimension") for d in json.loads((tmp / "rep42.json").read_text(encoding="utf-8"))["defects"]]
            ck.check("7.42 的 confirmed 条目进了缺陷清单", "7.42" in dims42, json.dumps(dims42, ensure_ascii=False))

    # 6h) flaky：历史快照 → 翻转识别 → CSV 交付
    # 这条链最怕的是"把 notRun 当成状态变化"或"把常年 fail 报成 flaky"——
    # 两种错都会让团队开始忽略整个稳定性报表。
    flaky_base = tmp / "baseline"
    runs_dir = flaky_base / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    key = "f" * 16
    for idx, (label, checks_map) in enumerate((
            ("2026-10-24T00-00-00", {"7.1": "pass", "7.41": "pass", "7.33": "fail"}),
            ("2026-10-25T00-00-00", {"7.1": "pass", "7.41": "notRun", "7.33": "fail"}),
            ("2026-10-26T00-00-00", {"7.1": "pass", "7.41": "fail", "7.33": "fail"}),
            ("2026-10-27T00-00-00", {"7.1": "pass", "7.41": "pass", "7.33": "fail"}))):
        (runs_dir / "{}.{}.json".format(key, label)).write_text(
            json.dumps({"_run": label, "_key": "k", "checks": checks_map}, ensure_ascii=False), encoding="utf-8")
    (runs_dir / "{}.2026-10-23T00-00-00.json".format(key)).write_text(
        json.dumps({"_run": "2026-10-23T00-00-00"}, ensure_ascii=False), encoding="utf-8")
    flaky_json = tmp / "flaky.json"
    flaky_csv = tmp / "flaky.csv"
    r = run([py, S / "flaky_report.py", "--baseline-dir", flaky_base,
             "--out", flaky_json, "--csv", flaky_csv])
    if ck.check("flaky_report 跑通（历史快照 → 稳定性报表）", r.returncode == 0, r.stderr[-300:]):
        fl = json.loads(flaky_json.read_text(encoding="utf-8"))
        ck.check("flaky 识别出翻转维度（7.41）",
                 fl["summary"]["flakyDimensions"] == ["7.41"],
                 json.dumps(fl["summary"], ensure_ascii=False))
        ck.check("flaky 不把 notRun 缺口算成翻转（7.41 翻转 2 次而非 3 次）",
                 fl["flakyRanking"] and fl["flakyRanking"][0]["flips"] == 2,
                 json.dumps(fl["flakyRanking"][:1], ensure_ascii=False))
        verdicts = {d["dimension"]: d["verdict"] for d in fl["byKey"][key]["dimensions"]}
        ck.check("常年 fail 判为 stable-fail 而不是 flaky",
                 verdicts.get("7.33") == "stable-fail", json.dumps(verdicts, ensure_ascii=False))
        ck.check("缺 checks 的旧快照被点名（legacyRuns）",
                 fl["source"]["legacyRuns"] == 1, json.dumps(fl["source"], ensure_ascii=False))
        ck.check("flaky CSV 交付为 utf-8-sig",
                 flaky_csv.exists() and flaky_csv.read_bytes().startswith(b"\xef\xbb\xbf"))

    # 7) assert_constraints：缺 frameWidth 的视口必须跳过而不是除零崩溃
    measurements = {"base": "375", "viewports": {
        "375": {"frameWidth": 375, "frameHeight": 200, "items": {"1:2": {"x": 20, "y": 30, "w": 100, "h": 40}}},
        "bad": {"items": {"1:2": {"x": 10, "y": 30, "w": 100, "h": 40}}},
        "768": {"frameWidth": 768, "frameHeight": 400,
                "items": {"1:2": {"x": 20, "y": 30, "w": 100, "h": 40}}},
    }}
    (tmp / "measurements.json").write_text(json.dumps(measurements, ensure_ascii=False), encoding="utf-8")
    r = run([py, S / "assert_constraints.py", "--design", tmp / "design.json",
             "--measurements", tmp / "measurements.json", "--output", tmp / "constraints.json"])
    ok = r.returncode == 0
    if ok:
        con = json.loads((tmp / "constraints.json").read_text(encoding="utf-8"))
        ck.check("P1-8 缺 frameWidth 的视口被跳过（无崩溃）",
                 any(s["viewport"] == "bad" for s in con.get("skippedViewports", [])),
                 json.dumps(con.get("summary"), ensure_ascii=False))
        con_v = con.get("violations", [])
        contract_ok = all(isinstance(v.get("actual"), str) and v.get("severity") in ("P0", "P1", "P2", "P3")
                          for v in con_v)
        ck.check("constraints 违规记录契约齐全（actual 为 string、severity 存在）", contract_ok)
    else:
        ck.check("P1-8 缺 frameWidth 的视口被跳过（无崩溃）", False, r.stderr[-300:])

    # 8) ignore_regions --enforce：宽泛/缺到期信息必须拦截（P1-7）
    ignore = {"regions": [{"id": "no-reason", "selector": ".container", "changeFingerprint": "x|y",
                           "reason": "整块忽略"}]}
    (tmp / "ignoredRegions.json").write_text(json.dumps(ignore, ensure_ascii=False), encoding="utf-8")
    r = run([py, S / "ignore_regions.py", "--file", tmp / "ignoredRegions.json", "--enforce"])
    ck.check("P1-7 宽泛/缺到期信息的忽略项被 enforce 拦截", r.returncode == 1, "rc={}".format(r.returncode))

    # 9) coverage_gate：缺维度时 enforce 拦截
    # 【F23 配套】report.json 只写了 defects、没有任何 checks——旧版只判覆盖率低
    # （rc=1），新版把「维度整体缺失」计入 unexplained，先以 rc=5（未写明原因）拦截。
    # 两种 rc 都是「被拦住」；这里断言"必须非 0"，并显式锁 F23 的新语义 rc=5。
    r = run([py, S / "coverage_gate.py", "--report", tmp / "report.json", "--enforce"])
    ck.check("coverage_gate 缺维度被 enforce 拦截（F23: 缺失=未说明, rc=5）",
             r.returncode == 5, "rc={}".format(r.returncode))

    # 10) diff_score 增强模式：默认口径不变 + 边缘/孤立抑制 + 平坦区真缺陷不被压掉（v4.16.0）
    node_exe = _find_node()
    probe_js = tmp / "diff_probe.cjs"
    probe_js.write_text(
        "const {writePNG, diff} = require(process.argv[2]);\n"
        "function img(w,h,f){const b=Buffer.alloc(w*h*4);for(let y=0;y<h;y++)for(let x=0;x<w;x++)"
        "{const c=typeof f==='function'?f(x,y):f;const i=(y*w+x)*4;b[i]=c[0];b[i+1]=c[1];b[i+2]=c[2];b[i+3]=c[3];}"
        "return writePNG(b,w,h);}\n"
        "const flat=img(20,20,[255,255,255,255]);\n"
        "const dot=img(20,20,(x,y)=>(x===10&&y===10)?[0,0,0,255]:[255,255,255,255]);\n"
        "const block=img(20,20,(x,y)=>(x>=8&&x<11&&y>=8&&y<11)?[0,0,0,255]:[255,255,255,255]);\n"
        "const big=img(40,40,(x,y)=>(x>=10&&x<30&&y>=10&&y<30)?[200,200,200,255]:[255,255,255,255]);\n"
        "const flatImg=img(40,40,[255,255,255,255]);\n"
        "console.log(JSON.stringify({plain_dot:diff(flat,dot,{}).changedPixels,"
        "dot:diff(flat,dot,{enhanced:true}),block:diff(flat,block,{enhanced:true}),"
        "big:diff(flatImg,big,{enhanced:true})}));\n",
        encoding="utf-8")
    r = run([node_exe, str(probe_js), str(S / "diff_score.js")])
    ok = r.returncode == 0
    d = json.loads(r.stdout) if ok else {}
    ck.check("diff_score 基础模式口径不变（孤立像素仍计入）", ok and d.get("plain_dot") == 1, r.stderr[-200:])
    ck.check("diff_score 增强模式抑制孤立噪点并留痕",
             d.get("dot", {}).get("changedPixels") == 0 and d.get("dot", {}).get("suppressed", {}).get("isolated") == 1,
             json.dumps(d.get("dot", {}).get("suppressed", {})))
    ck.check("diff_score 成片差异保留", d.get("block", {}).get("changedPixels") == 9, json.dumps(d.get("block", {}).get("suppressed", {})))
    ck.check("diff_score 平坦区整块变色不被压掉（真缺陷不漏）",
             d.get("big", {}).get("changedPixels") == 400 and d.get("big", {}).get("suppressed", {}).get("edgeNoise") == 0,
             json.dumps({"c": d.get("big", {}).get("changedPixels"), "s": d.get("big", {}).get("suppressed", {})}))

    # 11) scenario_coverage：真实产物串链（计划 → 回填 → 门禁），v4.26.0
    # 【审查修复·P2-14】旧版直接 read_text(scenarios.json)：第 6 步 discover_scenarios
    # 失败（或场景不足 2 条）时，这里 FileNotFoundError / IndexError 把整个冒烟链
    # 崩掉——冒烟链要报的上游失败被它自己的崩溃吞掉。先守卫：缺文件/场景不足
    # 记 check 失败并跳过本段，不再进入串链。
    sc_res = tmp / "sc_results.json"
    sc_out = tmp / "sc_cov.json"
    if not (tmp / "scenarios.json").exists():
        ck.check("scenario_coverage 串链前置：scenarios.json 存在", False,
                 "第 6 步 discover_scenarios 未产出 scenarios.json（P2-14 守卫）")
    else:
        sc_plan = json.loads((tmp / "scenarios.json").read_text(encoding="utf-8"))
        sc_ids = [s["id"] for s in sc_plan.get("scenarios", [])]
        if len(sc_ids) < 2:
            ck.check("scenario_coverage 串链前置：场景数 >= 2", False,
                     "scenarios.json 场景数 {}，无法按计划回填（P2-14 守卫）".format(len(sc_ids)))
        else:
            # 只回填前两条 → 执行率必须反映"计划 vs 实际"，而不是"维度跑过就算全覆盖"
            sc_res.write_text(json.dumps({"results": [{"scenarioId": sc_ids[0], "status": "pass"},
                                                      {"scenarioId": sc_ids[1], "status": "fail"}]},
                                         ensure_ascii=False), encoding="utf-8")
            r = run([py, S / "scenario_coverage.py", "--plan", tmp / "scenarios.json", "--results", sc_res,
                     "--out", sc_out, "--enforce", "--min-rate", "0.9"])
            ck.check("scenario_coverage 执行率低于下限时 enforce 拦截", r.returncode == 1, "rc={}".format(r.returncode))
            cov = json.loads(sc_out.read_text(encoding="utf-8")) if sc_out.exists() else {}
            ck.check("scenario_coverage 产出计划 vs 实际（rate=2/N）",
                     cov.get("executed", {}).get("total") == 2 and 0 < cov.get("executed", {}).get("rate", 0) < 1,
                     json.dumps(cov.get("executed", {}), ensure_ascii=False))
            ck.check("scenario_coverage 无执行记录的场景进 unexplained",
                     len(cov.get("notExecuted", {}).get("unexplained", [])) == max(0, len(sc_ids) - 2),
                     json.dumps(cov.get("notExecuted", {}).get("unexplained", [])[:3], ensure_ascii=False))
            # 全量回填 → 应通过
            sc_res.write_text(json.dumps({"results": [{"scenarioId": i, "status": "pass"} for i in sc_ids]},
                                         ensure_ascii=False), encoding="utf-8")
            r = run([py, S / "scenario_coverage.py", "--plan", tmp / "scenarios.json", "--results", sc_res, "--enforce"])
            ck.check("scenario_coverage 全量回填后 enforce 通过", r.returncode == 0, r.stderr[-200:])

    # ---- S5 管线级行为端到端（真端到端升级）----
    if node_exe:
        # F2：只重出报告用法（--report 不带 --design-json/--page-json）不得 UnboundLocalError
        # 【审查修复】此前用 node_exe 跑这个 .py —— node 直接 SyntaxError 退出 rc=1，
        # 而断言写的是 `returncode in (0, 1, 2)`，恰好把 1 算作合法 ⇒ F2 修没修都绿（恒真）。
        # 改用 py；断言改为「不崩」这一性质本身：门禁 abort 时返回非零是**预期行为**，
        # 不能断言 rc==0；而 UnboundLocalError / Traceback 才是 F2 回归的确凿标志。
        tmp2 = Path(tempfile.mkdtemp(prefix="smoke_chain_f2_"))
        try:
            (tmp2 / "r.json").write_text(json.dumps({"defects": [], "url": "https://p.test/"}), encoding="utf-8")
            r = run([py, S / "run_parity_pipeline.py", "--report", tmp2 / "r.json",
                     "--workdir", tmp2 / "wd", "--outdir", tmp2 / "out", "--no-patterns"])
            _err = r.stderr or ""
            ck.check("F2 只重出报告用法不崩（pool UnboundLocal 修复）",
                     ("UnboundLocalError" not in _err) and ("Traceback" not in _err),
                     "rc={} err={}".format(r.returncode, _err[-300:]))
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)

    return ck.summary()


if __name__ == "__main__":
    sys.exit(main())
