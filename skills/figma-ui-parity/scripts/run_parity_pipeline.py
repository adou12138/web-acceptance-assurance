#!/usr/bin/env python3
"""Chain the deterministic parity scripts into one runnable pipeline.

This orchestrator only launches local scripts with explicit arguments. It does
not read credentials, does not touch the network itself, and never mutates the
page. Every stage records `ran` / `skipped` with a reason so reports can state
the real coverage.
"""
from __future__ import annotations
import argparse, json, re, shutil, subprocess, sys, tempfile, time
from contextlib import nullcontext
from pathlib import Path

HERE = Path(__file__).resolve().parent

_SENSITIVE_KEY = re.compile(
    r"(?i)(password|passwd|pwd|token|secret|apikey|api[_-]?key|access[_-]?key|"
    r"credential|authorization|session|sig|signature|ticket|cookie)")


def redact_text(text: str) -> str:
    """pipeline-log 的 command 会原样留存：URL 里的凭据值必须打码。"""
    s = str(text or "")
    s = re.sub(r"([?&;])([^=&;\s]+)=([^&;\s]*)",
               lambda m: m.group(1) + m.group(2) + "="
               + ("[REDACTED]" if _SENSITIVE_KEY.search(m.group(2)) else m.group(3)), s)
    s = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._\-]+", r"\1[REDACTED]", s)
    s = re.sub(r"(?i)(Basic\s+)[A-Za-z0-9+/=]+", r"\1[REDACTED]", s)
    return s


def resolve_node() -> str:
    return shutil.which("node") or "node"


def stage(name: str, command: list, dry_run: bool, log: dict, stdout_path: Path = None) -> None:
    printable = redact_text(" ".join(command))
    if dry_run:
        log[name] = {"status": "planned", "command": printable}
        print("[plan] {}".format(printable))
        return
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=600)
    except FileNotFoundError:
        log[name] = {"status": "notRun", "reason": "命令不存在", "command": printable}
        print("[skip] {} -> 命令不存在".format(name))
        return
    except subprocess.TimeoutExpired:
        log[name] = {"status": "notRun", "reason": "执行超时", "command": printable}
        print("[skip] {} -> 执行超时".format(name))
        return
    except OSError as exc:
        log[name] = {"status": "notRun", "reason": "命令无法启动：{}".format(exc), "command": printable}
        print("[skip] {} -> 命令无法启动".format(name))
        return
    log[name] = {
        "status": "ran" if proc.returncode == 0 else "failed",
        "returncode": proc.returncode,
        "command": printable,
        "stdoutTail": (proc.stdout or "")[-400:],
        "stderrTail": (proc.stderr or "")[-400:],
    }
    # 【审查修复·P2-12】此前 stage 只在 pipeline-log 里留 stdoutTail 400 字符，
    # journey_coverage 的完整执行率矩阵（行×变体×verdict）出了管线就蒸发，
    # 事后无法对账。stdout_path 指定时把完整 stdout 落盘（journey_coverage.cjs
    # 输出纯 JSON，可直接作为产物消费）。仅 rc=0 且 stdout 非空时写，失败不产
    # 半截产物。
    if stdout_path is not None and proc.returncode == 0 and (proc.stdout or "").strip():
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text(proc.stdout, encoding="utf-8")
        log[name]["stdoutFile"] = str(stdout_path)
    print("[{}] {} (rc={})".format("ok" if proc.returncode == 0 else "fail", name, proc.returncode))


def abort_design_input(log: dict, name: str, outdir: Path) -> int:
    reason = "设计输入阶段失败短路：{}".format(name)
    for downstream in ("extract_design_fields", "design_input_publish", "stabilize", "diff_score",
                       "state_scenarios", "page_probes", "capture_aria", "button_audit", "journeys",
                       "performance", "a11y_audit", "keyboard_order", "design_contrast",
                       "compare_fields", "discover_scenarios", "assemble_report", "generate_reports",
                       "capture_defect_shots", "design_compare", "baseline_regression"):
        if downstream not in log:
            log[downstream] = {"status": "notRun", "reason": reason}
    log["pipelineStatus"] = "degraded"
    log["failedStages"] = [name]
    outdir.mkdir(parents=True, exist_ok=True)
    log_file = outdir / "pipeline-log.json"
    log_file.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[abort] {}；日志: {}".format(reason, log_file), file=sys.stderr)
    return 1


def publish_design_input(converted: Path, design_model: Path, design_dir: Path, raw: Path) -> None:
    converted_data = json.loads(converted.read_text(encoding="utf-8"))
    model = json.loads(design_model.read_text(encoding="utf-8"))
    if not isinstance(converted_data, dict) or not isinstance(model, dict):
        raise ValueError("设计输入产物必须是 JSON 对象")
    if not isinstance(model.setdefault("meta", {}), dict):
        raise ValueError("设计模型 meta 必须是 JSON 对象")
    model["source"] = str(design_dir / "figma-mcp.json")
    model["meta"]["sourceInput"] = str(raw)
    design_model.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    outputs = [(converted, design_dir / "figma-mcp.json"),
               (design_model, design_dir / "design_model.json")]
    backups = {}
    for _, target in outputs:
        backup = converted.parent / ("previous-" + target.name)
        if target.exists():
            shutil.copy2(target, backup)
            backups[target] = backup
    promoted = []
    try:
        for source, target in outputs:
            source.replace(target)
            promoted.append(target)
    except OSError:
        # 第二个替换失败时恢复已发布文件，首次发布则撤去孤立的新文件。
        for target in reversed(promoted):
            if target in backups:
                backups[target].replace(target)
            else:
                target.unlink()
        raise


# --no-* 跳过旗 → manifest 中被跳过驱动对应的维度自动豁免。
# 驱动按 manifest 的 driver 字段反查维度（单一事实源，此处不另抄映射表）。
# struct_checks 的输入（alignment/order）由 page_probes 采集，跳 probes 时
# struct 一族同样无法执行，一并豁免——理由写明输入依赖，供报告降级说明引用。
_SKIP_FLAG_DRIVERS = {
    "no_performance": ("run_performance",),
    "no_a11y": ("run_a11y_audit",),
    "no_keyboard": ("run_keyboard_order",),
    "no_design_contrast": ("design_contrast",),
    "no_probes": ("run_page_probes", "struct_checks"),
    "no_button_audit": ("run_button_audit",),
    "no_journeys": ("run_journeys",),
}


def _skip_flag_exempt_args(args) -> list[str]:
    """把用户显式 --no-* 跳过的驱动映射为 plan_run 的 --exempt 维度=理由。"""
    manifest_path = HERE.parent / "assets" / "dimension_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []  # manifest 不可读时交由 plan_run 自己报错，此处不吞
    driver_dims: dict[str, list[str]] = {}
    for dim, spec in (manifest.get("dimensions") or {}).items():
        driver = str(spec.get("driver") or "")
        if driver:
            # manifest 的 driver 带路径与扩展名（scripts/node/run_x.cjs），
            # 按去扩展名的文件名归组，旗名映射才不依赖路径写法
            driver_dims.setdefault(Path(driver).stem, []).append(dim)
    out: list[str] = []
    for flag, tokens in _SKIP_FLAG_DRIVERS.items():
        if not getattr(args, flag, False):
            continue
        dims = sorted({d for t in tokens for d in driver_dims.get(t, [])})
        note = "用户显式跳过（--{} 旗，本轮决定不测）".format(flag.replace("_", "-"))
        if "struct_checks" in tokens:
            note += "；该族维度的输入由 page_probes 采集，随之无法执行"
        for d in dims:
            out += ["--exempt", f"{d}={note}"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="按序执行parity确定性脚本")
    ap.add_argument("--workdir", default="./parity-test", type=Path)
    ap.add_argument("--page-url", default=None, help="稳定化与提取阶段的目标页面 URL")
    design_input = ap.add_mutually_exclusive_group()
    design_input.add_argument("--design-json", default=None, type=Path, help="extract_design_fields.py 产物；与 --page-json 同时提供时触发逐字段比对链路")
    design_input.add_argument("--figma-mcp-raw", default=None, type=Path,
                              help="Figma MCP YAML-ish 原始文本；自动转换并生成 design-json")
    figma_selection = ap.add_mutually_exclusive_group()
    figma_selection.add_argument("--figma-node-id", default=None, help="透传转换器的 --node-id，选择设计节点")
    figma_selection.add_argument("--figma-root-name", default=None, help="透传转换器的 --root-name，选择设计画布")
    ap.add_argument("--page-json", default=None, type=Path, help="collect_page_inventory.js 产物")
    ap.add_argument("--figma-png", default=None, type=Path)
    ap.add_argument("--page-png", default=None, type=Path)
    ap.add_argument("--enhanced-diff", action="store_true",
                    help="diff_score 用增强模式（抗锯齿/孤立噪点抑制）；口径与基础模式不同，跨模式比基线前先确认同模式")
    ap.add_argument("--report", default=None, type=Path, help="上游 report.json")
    ap.add_argument("--outdir", default=None, type=Path)
    ap.add_argument("--patterns", default=None, type=Path,
                    help="缺陷模式池 JSON；默认 assets/defect_patterns.json。"
                         "池为空时行为与不传完全一致（冷启动零影响）")
    ap.add_argument("--no-patterns", action="store_true", help="关掉模式池接入（不传 --patterns、不跑 merge）")
    ap.add_argument("--combo-depth", default=None, type=int,
                    help="透传给 discover_scenarios：1=单点（默认）；2=加深异常数据与逐断点展开")
    ap.add_argument("--roles", default=None, help="透传给 discover_scenarios：逗号分隔角色，用于 7.34")
    ap.add_argument("--breakpoints", default=None, help="透传给 discover_scenarios：组合用断点，逗号分隔")
    ap.add_argument("--perf-throttle", default="fast4g",
                    help="7.40 性能采集的网络档：none / fast3g / fast4g（默认 fast4g。"
                         "无节流时耗时类指标只记 warn，不作为缺陷证据）")
    ap.add_argument("--no-performance", action="store_true", help="跳过 7.40 性能采集")
    ap.add_argument("--no-a11y", action="store_true", help="跳过 7.41 可访问性规则审计")
    ap.add_argument("--axe", default=None, help="axe-core 脚本路径（可选，用来补强 7.41 的规则覆盖）")
    ap.add_argument("--no-keyboard", action="store_true", help="跳过 7.43 键盘可达性与焦点顺序")
    ap.add_argument("--keyboard-open", default=None,
                    help="7.43 采样前先点开该 CSS 选择器对应的弹层（用于检查焦点陷阱；不传则只在页面自带弹层时生效）")
    ap.add_argument("--no-design-contrast", action="store_true",
                    help="跳过 7.42 设计稿侧文本对比度（需要 --design-json）")
    ap.add_argument("--no-flaky", action="store_true", help="跳过跨轮 flaky 统计")
    ap.add_argument("--baseline-dir", default=None, type=Path,
                    help="baseline_regression 的基线目录（flaky 统计读其 runs/）；缺省 <workdir>/baseline")
    ap.add_argument("--network-conditions", default="500,offline,slow",
                    help="7.33 网络条件矩阵：逗号分隔，可选 500 / offline / slow（默认全开）")
    # 【全量覆盖修复】登录站的 profile 此前不透传：管线里的 node 驱动全都支持
    # --profile，但管线从不转发，导致登录后的页面只能靠人手工逐个驱动跑，
    # 漏一个就是一片 notRun。这里统一透传，不改任何判定。
    # 注意与上面的 --profile（执行档位 smoke/standard/full）**不是同一个东西**：
    # 档位回答"打算跑哪些维度"，这里回答"用哪个浏览器资料目录登录"，故改名为
    # --browser-profile，转发给 node 驱动时再映射回它们约定的 --profile。
    ap.add_argument("--browser-profile", default=None,
                    help="浏览器持久化资料目录（登录站必传）。透传给 state_scenarios / performance / "
                         "a11y / keyboard / probes / aria / button_audit / journeys")
    ap.add_argument("--no-probes", action="store_true",
                    help="跳过页面注入型探针（覆盖 7.4/7.5/7.6/7.7/7.9/7.13-7.15/7.20/7.24/7.27-7.31/"
                         "7.35-7.38/7.45 共 18 个维度）")
    ap.add_argument("--probe-breakpoints", default="1920,1440,1280,768,375",
                    help="7.4 响应式实测断点，逗号分隔宽度")
    ap.add_argument("--no-aria", action="store_true", help="跳过 7.19 ARIA 快照采集（结构 diff 的输入）")
    ap.add_argument("--no-button-audit", action="store_true", help="跳过 7.22/7.44 按钮可用性审计执行")
    ap.add_argument("--no-journeys", action="store_true", help="跳过 7.39 多页用户旅程")
    ap.add_argument("--journey-hops", default=1, type=int, help="7.39 最大跳数（默认 1）")
    ap.add_argument("--journey-visits", default=12, type=int, help="7.39 最大访问页数（默认 12）")
    ap.add_argument("--shot-viewport", default="1440x900",
                    help="缺陷网页截图视口，需与设计稿画布一致时显式指定（如 1520x1013）")
    ap.add_argument("--align-sidebar", default=0, type=int,
                    help="截图前将左侧栏对齐到指定宽度；0 表示不执行状态对齐")
    ap.add_argument("--no-defect-shots", action="store_true",
                    help="跳过缺陷网页截图及设计稿并排对比（不建议用于正式 full 运行）")
    ap.add_argument("--safety-policy", default="safe",
                    choices=["all", "safe", "mockWrites", "staticOnly"],
                    help="交互安全策略，透传给 button_audit / state_scenarios / journeys")
    ap.add_argument("--test-environment-confirmed", action="store_true",
                    help="用户已确认当前为隔离测试环境；--safety-policy all 时必须显式给出，否则拒绝执行")
    ap.add_argument("--profile", default=None,
                    help="执行档位 smoke / standard / full（见 assets/run_profiles.json），"
                         "透传给 assemble_report。只改未执行维度的原因写法，不改判定")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # 安全策略 all 的隔离环境确认必须在管线入口就生效：驱动层只拦 danger，
    # caution（保存/提交/创建）会真实点击，因此不能只靠 start_test 的上游把关。
    if args.safety_policy == "all" and not args.test_environment_confirmed:
        print("[abort] --safety-policy all 需要 --test-environment-confirmed"
              "（用户确认当前是隔离测试环境），否则拒绝执行 caution/danger 交互",
              file=sys.stderr)
        return 2

    # --report 给了但文件不存在：属于路径拼写错误，必须显式失败。
    # 旧版落到「缺少 --report」分支并 rc=0，用户看到"成功"却没有报告。
    if args.report and not args.report.exists() and not args.dry_run:
        print("[abort] --report 指向的文件不存在: {}".format(args.report), file=sys.stderr)
        return 2

    # 【F53】--patterns 与 --no-patterns 同时给时，旧版 pool 计算静默忽略 --patterns
    # （用户以为指定了池文件，实际跑的是"关闭模式池"）。两个意图相反，必须显式冲突，
    # 不让任何一个被静默吞掉。
    if args.no_patterns and args.patterns:
        ap.error("--patterns 与 --no-patterns 互斥：显式关闭模式池时不应再指定池文件")

    node = resolve_node()
    workdir: Path = args.workdir
    outdir: Path = args.outdir or workdir / "reports"
    baseline_dir: Path = args.baseline_dir or (workdir / "baseline")
    log: dict = {"workdir": str(workdir), "dryRun": args.dry_run}
    run_started = time.time()
    # 两步都使用本轮隔离产物；旧文件存在不能证明本轮执行成功。
    # 完整提取成功后才替换正式产物，失败不消费或覆盖上一轮的模型。
    if args.figma_mcp_raw:
        design_dir = workdir / "design"
        if not args.dry_run:
            design_dir.mkdir(parents=True, exist_ok=True)
        scratch = (nullcontext(str(design_dir)) if args.dry_run else
                   tempfile.TemporaryDirectory(prefix="figma-input-", dir=design_dir))
        with scratch as temporary:
            converted = Path(temporary) / "figma-mcp.json"
            design_model = Path(temporary) / "design_model.json"
            convert_cmd = [sys.executable, str(HERE / "figma_mcp_to_json.py"),
                           "--input", str(args.figma_mcp_raw), "--output", str(converted)]
            if args.figma_node_id:
                convert_cmd += ["--node-id", args.figma_node_id]
            if args.figma_root_name:
                convert_cmd += ["--root-name", args.figma_root_name]
            extract_cmd = [sys.executable, str(HERE / "extract_design_fields.py"),
                           "--input", str(converted), "--output", str(design_model)]
            for name, command, output in (("figma_mcp_to_json", convert_cmd, converted),
                                           ("extract_design_fields", extract_cmd, design_model)):
                stage(name, command, args.dry_run, log)
                if args.dry_run:
                    continue
                if log[name]["status"] == "ran" and not output.is_file():
                    log[name].update(status="failed", reason="命令成功退出但未生成本轮产物")
                if log[name]["status"] != "ran":
                    return abort_design_input(log, name, outdir)
            if not args.dry_run:
                try:
                    publish_design_input(converted, design_model, design_dir, args.figma_mcp_raw)
                except (OSError, ValueError) as exc:
                    log["design_input_publish"] = {"status": "failed", "reason": "设计输入发布失败：{}".format(exc)}
                    return abort_design_input(log, "design_input_publish", outdir)
                log["design_input_publish"] = {"status": "ran"}
            else:
                log["design_input_publish"] = {"status": "planned"}
            args.design_json = design_dir / "design_model.json"
    # 【F2】pool 只在 compare_fields 分支里赋值，而尾部 pattern_pool_merge 在
    # `if args.report` 分支用它——「只重出报告」用法（--report 不带 --design-json/
    # --page-json，最常见）会 UnboundLocalError 崩溃，且崩溃发生在 pipeline-log
    # 写入之前，失败无痕。提到 main() 开头初始化。
    pool = None if args.no_patterns else (args.patterns or (HERE.parent / "assets" / "defect_patterns.json"))
    # 登录态：所有 node 驱动共用同一个持久化资料目录。为空时不加参数（各驱动自会
    # 退回 .auth 里注入的 storage state 或匿名访问）。
    prof = ["--profile", str(args.browser_profile)] if args.browser_profile else []
    safety = ["--safety-policy", args.safety_policy]

    if args.page_url:
        stage("stabilize", [node, str(HERE / "stabilize.js"), "--url", args.page_url], args.dry_run, log)

    # 【门禁 1/2】跑前计划门禁：45 维度先排程后开跑。
    # manual 维度用 manifest 预登记豁免理由；退出码 2 = 必测维度既未排程也无合规
    # 豁免 → 管线当场熔断（此前两类遗漏——产物漏传、驱动漏排——都是跑完才暴露）。
    # 用户显式 --no-* 跳过的驱动，其维度自动转豁免（理由带旗名留痕）——否则
    # 「显式决定不测」与「漏测」无法区分，skip 旗会永远触发熔断。
    plan_cmd = [sys.executable, str(HERE / "plan_run.py"),
                "--profile", args.profile or "full",
                # 计划与产物必须同根：所有驱动产物落在 workdir，outdir 只装报告。
                # 旧版传 outdir（默认 workdir/reports）会让 pre_assemble_gate 在
                # 报告子目录里找不到任何驱动产物，默认跑法每轮都在汇总前熔断。
                "--run-dir", str(workdir), "--write"]
    if args.design_json:
        plan_cmd += ["--design", str(args.design_json)]
    plan_cmd += _skip_flag_exempt_args(args)
    stage("plan_run", plan_cmd, args.dry_run, log)
    if not args.dry_run and log.get("plan_run", {}).get("status") in ("failed", "notRun"):
        log["plan_run"]["action"] = "pipeline-aborted: 必测维度未排程且无合规豁免"
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "pipeline-log.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[abort] 跑前计划门禁未通过（详见 plan_run stderr 与 pipeline-log），管线终止", file=sys.stderr)
        return 2

    if args.figma_png and args.page_png:
        # --enhanced-diff 是 opt-in：开启后 diff_score 用抗锯齿/孤立噪点抑制，
        # diffScore 口径与基础模式不同，跨模式比基线前要先确认基线同模式。
        stage("diff_score", [node, str(HERE / "diff_score.js"), "--a", str(args.figma_png), "--b", str(args.page_png),
                             "--out", str(outdir / "mask.png")] + (["--enhanced"] if args.enhanced_diff else []),
              args.dry_run, log)
    else:
        log["diff_score"] = {"status": "notRun", "reason": "缺少 --figma-png 或 --page-png"}

    # 【F12】run_state_scenarios 此前没有任何管线入口调用它——7.25/7.26/7.32/7.33
    # 四个维度「有场景、有 runner、无执行者」，产出恒 notRun。在 compare_fields
    # 之后自动跑一轮页面级状态场景（安全策略内，无需登录态之外的配置），
    # state_scenarios.json 存在时 assemble_report 已有接线会消费它。
    # 【审查修复·P2-16】dry-run + --page-url 时旧版落进 else 记「缺少 --page-url」
    # ——reason 与事实相反，按 reason 排障会去查一个不存在的缺失参数。对齐
    # stabilize 的行为：有 page-url 就进 stage（dry-run 由 stage 记 planned，
    # 呈现真实计划形态；真跑才执行）。
    if args.page_url:
        stage("state_scenarios", [node, str(HERE / "node" / "run_state_scenarios.cjs"),
                                  "--url", args.page_url,
                                  "--out", str(workdir),
                                  "--network-conditions", args.network_conditions] + prof + safety,
              args.dry_run, log)
    else:
        log["state_scenarios"] = {
            "status": "notRun", "reason": "缺少 --page-url（页面级状态场景需要目标 URL）"}

    # 【全量覆盖修复】页面注入型探针统一驱动：一次性覆盖 7.4 / 7.5 / 7.6 / 7.7 / 7.9 /
    # 7.13-7.15 / 7.20 / 7.24 / 7.27-7.31 / 7.35-7.38 / 7.45。
    # 此前这些探针只靠人手工 browser_evaluate 注入，漏一次就是整片 notRun，
    # 报告里只写「无产物覆盖」，分不清是漏跑还是页面不适用。
    if args.page_url and not args.no_probes:
        stage("page_probes", [node, str(HERE / "node" / "run_page_probes.cjs"),
                              "--url", args.page_url, "--out", str(workdir),
                              "--breakpoints", args.probe_breakpoints] + prof + safety,
              args.dry_run, log)
    elif not args.page_url:
        log["page_probes"] = {"status": "notRun", "reason": "缺少 --page-url"}
    else:
        log["page_probes"] = {"status": "notRun", "reason": "按 --no-probes 显式跳过"}

    # 7.19 ARIA 快照：capture_aria.js 自带 CLI（--url / --diff 两种模式），不是注入片段。
    # 快照是结构 diff 的输入；首次运行没有基线时 struct_checks 会记 dataMissing 并写明原因。
    if args.page_url and not args.no_aria:
        stage("capture_aria", [node, str(HERE / "capture_aria.js"),
                               "--url", args.page_url,
                               "--out", str(workdir / "aria.json")] + prof,
              args.dry_run, log)
    elif not args.page_url:
        log["capture_aria"] = {"status": "notRun", "reason": "缺少 --page-url"}
    else:
        log["capture_aria"] = {"status": "notRun", "reason": "按 --no-aria 显式跳过"}

    # 【全量覆盖修复】按钮审计此前只有 gen_button_report（读已有产物），没有任何
    # 执行者——7.22 恒 notRun，7.44 恒 dataMissing。这里补上真正的执行入口。
    audit_dir = workdir / "button-audit"
    if args.page_url and not args.no_button_audit:
        audit_cmd = [node, str(HERE / "node" / "run_button_audit.cjs"),
                     "--url", args.page_url, "--out", str(audit_dir)] + prof + safety
        # 不加 --click 只做静态盘点：7.22 拿不到"点击是否生效"，7.44 首响应时延
        # 也无数据可测。除 staticOnly 外都应真点（safe 点 safe 级；mockWrites 拦截
        # 写请求后点；all 需用户已确认测试环境，由上游把关）。
        if args.safety_policy != "staticOnly":
            audit_cmd.append("--click")
        if args.safety_policy == "all":
            audit_cmd.append("--include-danger")
        if args.design_json and args.design_json.exists():
            # 设计侧按钮清单存在才能做 7.23 双向配对；没有就只做页面侧可用性。
            db = workdir / "design_buttons.json"
            if db.exists():
                audit_cmd += ["--design-buttons", str(db)]
        stage("button_audit", audit_cmd, args.dry_run, log)
    elif not args.page_url:
        log["button_audit"] = {"status": "notRun", "reason": "缺少 --page-url"}
    else:
        log["button_audit"] = {"status": "notRun", "reason": "按 --no-button-audit 显式跳过"}

    # 7.39 多页用户旅程：全程 GET 导航 + 同源，不提交表单。
    # 默认收窄到 1 跳 / 12 次访问：默认值（2 跳 / 30 次）在真实站点上会把
    # stage 跑过 600s 上限被记「执行超时」，等于 7.39 又没结论。有界结论
    # 比跑不完强，需要更深时用 --journey-visits 放开。
    if args.page_url and not args.no_journeys and args.safety_policy != "staticOnly":
        stage("journeys", [node, str(HERE / "node" / "run_journeys.cjs"),
                           "--url", args.page_url, "--out", str(workdir),
                           "--max-hops", str(args.journey_hops),
                           "--max-visits", str(args.journey_visits)] + prof + safety,
              args.dry_run, log)
    elif not args.page_url:
        log["journeys"] = {"status": "notRun", "reason": "缺少 --page-url"}
    else:
        log["journeys"] = {"status": "notRun",
                           "reason": "按 --no-journeys 显式跳过" if args.no_journeys else "staticOnly 策略下不导航"} 

    # 7.40 性能 / 7.41 可访问性规则审计：两者只需要 --page-url，不需要设计稿。
    # 放在状态场景之后：性能要在页面首次加载时测（驱动自己 addInitScript 装观察器，
    # 不受前面 stage 影响），可访问性要在渲染稳定后测。
    if args.page_url and not args.no_performance:
        stage("performance", [node, str(HERE / "node" / "run_performance.cjs"),
                             "--url", args.page_url, "--out", str(workdir),
                             "--throttle", args.perf_throttle] + prof,
              args.dry_run, log)
    elif not args.page_url:
        log["performance"] = {"status": "notRun", "reason": "缺少 --page-url"}
    else:
        log["performance"] = {"status": "notRun", "reason": "按 --no-performance 显式跳过"}

    if args.page_url and not args.no_a11y:
        a11y_cmd = [node, str(HERE / "node" / "run_a11y_audit.cjs"),
                    "--url", args.page_url, "--out", str(workdir)] + prof
        if args.axe:
            a11y_cmd += ["--axe", args.axe]
        stage("a11y_audit", a11y_cmd, args.dry_run, log)
    elif not args.page_url:
        log["a11y_audit"] = {"status": "notRun", "reason": "缺少 --page-url"}
    else:
        log["a11y_audit"] = {"status": "notRun", "reason": "按 --no-a11y 显式跳过"}
    # 7.43 键盘可达性与焦点顺序：只需要 --page-url。只按 Tab，无副作用，
    # 因此 safe 策略下即可执行；staticOnly 由驱动自己记 notRun。
    if args.page_url and not args.no_keyboard:
        kb_cmd = [node, str(HERE / "node" / "run_keyboard_order.cjs"),
                  "--url", args.page_url, "--out", str(workdir)] + prof
        if args.keyboard_open:
            kb_cmd += ["--open", args.keyboard_open]
        stage("keyboard_order", kb_cmd, args.dry_run, log)
    elif not args.page_url:
        log["keyboard_order"] = {"status": "notRun", "reason": "缺少 --page-url"}
    else:
        log["keyboard_order"] = {"status": "notRun", "reason": "按 --no-keyboard 显式跳过"}

    # 7.42 设计稿侧文本对比度：需要设计模型（extract_design_fields.py 产物，含 tree）。
    # 放在可访问性之后：页面侧 7.41 与设计侧 7.42 是同一件事的两端，报告里相邻更易对账。
    if args.design_json and (args.dry_run or args.design_json.exists()) and not args.no_design_contrast:
        stage("design_contrast", [sys.executable, str(HERE / "design_contrast.py"),
                                  "--design", str(args.design_json),
                                  "--out", str(workdir / "design_contrast.json")],
              args.dry_run, log)
    elif args.no_design_contrast:
        log["design_contrast"] = {"status": "notRun", "reason": "按 --no-design-contrast 显式跳过"}
    else:
        log["design_contrast"] = {"status": "notRun",
                                  "reason": "缺少 --design-json（设计稿侧对比度需要设计模型，页面侧由 7.41 覆盖）"}

    # audit_dir 已在按钮审计执行 stage 定义；这里只消费它，重复赋值会让
    # 「执行阶段换了目录」时两处悄悄不一致。
    if audit_dir.exists():
        stage("button_report", [node, str(HERE / "gen_button_report.cjs"), "--dir", str(audit_dir), "--out", str(outdir)],
              args.dry_run, log)
    else:
        log["button_report"] = {"status": "notRun", "reason": "缺少 {}".format(audit_dir)}

    journey = workdir / "journey.json"
    coverage = workdir / "coverage.json"
    if journey.exists() and coverage.exists():
        # 【审查修复·P2-12】journey_coverage 的完整 JSON 结果落盘到 outdir——
        # 旧行为只留 stdoutTail 400 字符，执行率矩阵出了管线就没了。
        stage("journey_coverage", [node, str(HERE / "journey_coverage.cjs"), "--journey", str(journey),
                                   "--coverage", str(coverage)],
              args.dry_run, log, stdout_path=outdir / "journey_coverage.json")
    else:
        log["journey_coverage"] = {"status": "notRun", "reason": "缺少 journey.json 或 coverage.json"}

    design_json = args.design_json
    page_json = args.page_json
    style_diffs = workdir / "styleDiffs.json"
    if (design_json and page_json and
            (args.dry_run or (design_json.exists() and page_json.exists()))):
        stage("compare_fields", [sys.executable, str(HERE / "compare_fields.py"),
                                 "--design", str(design_json), "--page", str(page_json),
                                 "--page-url", args.page_url or "",
                                 "--output", str(style_diffs)], args.dry_run, log)
        # 模式池接入：默认把 assets/defect_patterns.json 传给 discover_scenarios，
        # 让「历史高频翻车的维度必被覆盖」在标准管线里真的发生。池为空时
        # pattern_weights 返回 {}、applied=false，产物与不传完全一致（冷启动零影响）。
        # pool 已在 main() 开头初始化（F2），这里只做局部覆盖说明。
        patterns_args = [] if pool is None else ["--patterns", str(pool)]
        passthrough = []
        if args.combo_depth:
            passthrough += ["--combo-depth", str(args.combo_depth)]
        if args.roles:
            passthrough += ["--roles", args.roles]
        if args.breakpoints:
            passthrough += ["--breakpoints", args.breakpoints]
        stage("discover_scenarios", [sys.executable, str(HERE / "discover_scenarios.py"),
                                     "--design", str(design_json), "--page", str(page_json),
                                     "--pairs", str(style_diffs),
                                     "--output", str(workdir / "scenarios.json")] + patterns_args + passthrough,
              args.dry_run, log)
    else:
        # 【审查修复·P2-9】陈旧产物消费至少要留痕：本轮没跑 compare_fields，但
        # workdir 里残留的旧 styleDiffs.json 仍会被下方 assemble_report 消费——
        # 不短路（设计取舍：只重出报告是合法用法），但 reason 必须说清证据来源
        # 是上一轮的，报告不得冒充本轮实测。
        stale = "；注意：检测到旧 styleDiffs.json，assemble_report 将把它当陈旧证据消费（非本轮实测）" \
            if style_diffs.exists() else ""
        log["compare_fields"] = {"status": "notRun", "reason": "缺少 --design-json 或 --page-json" + stale}
        log["discover_scenarios"] = {"status": "notRun", "reason": "缺少 --design-json 或 --page-json"}

    if args.report and args.report.exists():
        # 先把各驱动产物的维度证据汇总进 report 的 checks 段，再进门禁——否则
        # coverage_gate 会因为「维度缺失 / notRun 未写原因」直接非零退出，而那份
        # 39 行的维度→状态→原因表此前只能靠 agent 手工拼装。
        # 只收管线与同目录下已存在的产物，缺的就少一路证据（该维度会落到 notRun）。
        # **不判断有无产物**：report 里没有手写 checks 时，也要靠本阶段生成一份
        # 全 notRun 的维度表，否则 coverage_gate 会因「维度缺失」直接非零退出。
        # 写操作台账：先把「未清理的写操作」并入待确认，再汇总 checks——
        # 顺序反了的话 7.12 统计不到这些条目，报告会显示「无待确认项」，
        # 而实际环境里还留着上一轮造的测试数据。
        ledger = workdir / "write_ops_ledger.json"
        if ledger.exists():
            stage("write_ops_merge", [sys.executable, str(HERE / "write_ops_ledger.py"),
                                      "--file", str(ledger), "--merge-pending",
                                      "--report", str(args.report)], args.dry_run, log)
        else:
            log["write_ops_merge"] = {"status": "notRun",
                                      "reason": "缺少 write_ops_ledger.json（本轮未登记写操作）"}
        af = []
        if style_diffs.exists():
            af += ["--style-diffs", str(style_diffs)]
        # 7.4 响应式约束断言此前无任何管线入口——assert_constraints 的 violations 是
        # 7.4 唯一的缺陷产出通道，不接上该维度永远不出缺陷（SKILL.md 却声明它是 7.4 的
        # 执行手段）。仅在 measurements.json 存在时跑：没有实测坐标就跳过，行为与接入前
        # 一致，不猜坐标。
        if (workdir / "measurements.json").exists() and args.design_json:
            stage("assert_constraints", [
                sys.executable, str(HERE / "assert_constraints.py"),
                "--design", str(args.design_json),
                "--measurements", str(workdir / "measurements.json"),
                "--output", str(workdir / "constraints.json")], args.dry_run, log)
        # 结构化观察（对齐偏差 / 控件遮挡 / 可疑文案 / 首屏外主操作 / 可访问性结构）
        # 此前只有 alignment 一路接进来，其余三路同样"采集后无人读取"：
        # 脚本 D 的 needScrollActions（7.9）与 capture_aria 的 diff（7.19）都没落账通道。
        # 产物不存在就整段跳过，行为与接入前完全一致。
        def _first(*candidates):
            return next((p for p in candidates if p.exists()), None)

        align_src = _first(workdir / "alignment.json", workdir / "extract" / "alignment.json")
        order_src = _first(workdir / "order.json", workdir / "extract" / "order.json")
        aria_diff_src = _first(workdir / "aria_diff.json", workdir / "extract" / "aria_diff.json")
        # 只有拿不到 diff 时才退到快照：首次运行要记 dataMissing 并写明"无基线"，
        # 不能因为"有快照"就当成结构未变化。
        aria_snap_src = None if aria_diff_src else _first(
            workdir / "aria.json", workdir / "extract" / "aria.json")
        struct_inputs = []
        if align_src is not None:
            struct_inputs += ["--alignment", str(align_src)]
        if order_src is not None:
            struct_inputs += ["--order", str(order_src)]
        if aria_diff_src is not None:
            struct_inputs += ["--aria-diff", str(aria_diff_src)]
        if aria_snap_src is not None:
            struct_inputs += ["--aria-snapshot", str(aria_snap_src)]
        # 设计侧盘点：design 提供时先推导顺序/图标/分隔线清单，7.6/7.7 才有对照物。
        # 没有这一步，struct_checks 永远产不出这两个维度的条目（凭页面单侧数据
        # 下结论就是编造——清单必须来自 design_inventory 的机械推导）。
        design_inventory_src = None
        if args.design_json and args.design_json.exists():
            design_inventory_src = workdir / "design_inventory.json"
            stage("design_inventory", [sys.executable, str(HERE / "design_inventory.py"),
                                       "--design", str(args.design_json),
                                       "--output", str(design_inventory_src)],
                  args.dry_run, log)
        if struct_inputs:
            struct_out = workdir / "struct_findings.json"
            struct_cmd = [sys.executable, str(HERE / "struct_checks.py")] + struct_inputs
            if design_inventory_src is not None:
                struct_cmd += ["--design-inventory", str(design_inventory_src)]
            stage("struct_checks", struct_cmd + ["--output", str(struct_out)],
                  args.dry_run, log)
            # 不管文件此刻是否存在都要挂上：dry-run 下 stage 不执行，靠 exists() 判断会把
            # 这一路证据静默漏掉，计划与实际不一致。
            af += ["--findings", str(struct_out)]
        # 场景执行率：coverage_gate 只回答"该维度跑过没有"，逐条场景有没有跑只有这里能答。
        # 旧版管线从不生成该产物（只把它当可选输入消费），执行率门禁事实不生效，
        # 生成 300 条场景跑 3 条也照样"覆盖良好"。这里用 --from-report 回收执行痕迹；
        # 硬门禁（--enforce）不默认开，避免把"本轮只跑了部分场景"的中间态直接判死。
        scenarios_src = _first(workdir / "scenarios.json", workdir / "extract" / "scenarios.json")
        if scenarios_src is not None:
            sc_out = workdir / "scenario_coverage.json"
            stage("scenario_coverage", [sys.executable, str(HERE / "scenario_coverage.py"),
                                        "--plan", str(scenarios_src),
                                        "--from-report", str(args.report),
                                        "--out", str(sc_out)], args.dry_run, log)
            af += ["--scenario-coverage", str(sc_out)]
        for name, flag in (("findings.json", "--findings"),
                           ("performance.json", "--findings"),
                           ("a11y_audit.json", "--findings"),
                           ("runtime.json", "--findings"),
                           ("journey_findings.json", "--findings"),
                           ("constraints.json", "--findings"),
                           ("design_contrast.json", "--findings"),
                           ("keyboard_order.json", "--findings"),
                           # 页面注入型探针的产物：不接进来等于「采了不读」，
                           # 7.4 / 7.20 / 7.24 / 7.27-7.31 仍会落 notRun。
                           ("responsive.json", "--findings"),
                           ("extra_checks.json", "--findings"),
                           # 「声明检查过但没问题」的维度补 pass：不接这条，
                           # 探测干净反而被判成没跑，覆盖率长期偏低。
                           ("probe_coverage.json", "--findings"),
                           # 探针整体失败时的留痕：只有这一路能把「浏览器起不来 / 探针炸了」
                           # 与「页面不适用」区分开。
                           ("page_probes_failed.json", "--findings"),
                           ("button-audit/response_findings.json", "--findings"),
                           # 7.22 的维度结论：button_audit.json 是没有 dimension 的盘点表，
                           # 只消费它就等于"点了也不落账"，7.22 恒 notRun。
                           ("button-audit/button_findings.json", "--findings"),
                           # 设计侧人工/半自动比对结论：不接这条，重跑时 7.1 的
                           # 设计稿差异只能靠 meta.checks 里的手写结论撑着，
                           # 换个工作区就整段丢失。
                           ("design_findings.json", "--findings"),
                           ("state_scenarios.json", "--state-scenarios")):
            candidate = workdir / name
            # 只收本轮产出的证据：上一轮残留文件会让「本轮 notRun」与「上一轮 pass」
            # 同时进报告，同一维度出现互斥结论（幂等性要求按 mtime 过滤）。
            if candidate.exists() and candidate.stat().st_mtime >= run_started - 1:
                af += [flag, str(candidate)]
        # 档位只透传，不改管线跑什么：跑哪些维度仍由已有产物决定，
        # 档位负责的是把「没打算跑」与「打算跑但漏了」在报告里分开写。
        # 【门禁 2/2】汇总前接线门禁：双向对账+深度验真。
        # 把本轮收集到的 assemble 入参写盘给门禁对账——run 目录里有产物却不在
        # 入参里（漏传）或计划产物空壳/缺键（假跑）→ 拦截并终止。
        given_args_path = outdir / "assemble-args.given.json"
        if not args.dry_run:
            given_args_path.parent.mkdir(parents=True, exist_ok=True)
            given_args_path.write_text(json.dumps(
                [a for a in af if not a.startswith("--")], ensure_ascii=False, indent=1), encoding="utf-8")
        gate_cmd = [sys.executable, str(HERE / "pre_assemble_gate.py"),
                    "--plan", str(workdir / "run-plan.json"),
                    "--run-dir", str(workdir),
                    "--assemble-args", str(given_args_path),
                    "--write-suggested"]
        if args.report:
            gate_cmd += ["--report", str(args.report)]
        stage("pre_assemble_gate", gate_cmd, args.dry_run, log)
        if not args.dry_run and log.get("pre_assemble_gate", {}).get("status") in ("failed", "notRun"):
            log["pre_assemble_gate"]["action"] = "pipeline-aborted: 产物对账/验真未通过"
            outdir.mkdir(parents=True, exist_ok=True)
            (outdir / "pipeline-log.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
            print("[abort] 汇总前接线门禁未通过（详见 pre_assemble_gate stderr），管线终止", file=sys.stderr)
            return 2
        assemble_cmd = [sys.executable, str(HERE / "assemble_report.py"),
                        "--report", str(args.report)] + af + ["--write"]
        if args.profile:
            assemble_cmd += ["--profile", args.profile]
        # 无设计稿时由措辞门禁机械拦截「还原度」：把本轮是否有设计稿如实传给报告。
        assemble_cmd += ["--design-provided", "true" if args.design_json else "false"]
        stage("assemble_report", assemble_cmd, args.dry_run, log)
        # 交付纪律：门禁后、出报告前先做缺陷收口富化（复现步骤到按键级、来源标注），
        # 顺序在 assemble 之后、缺陷截图之前。旧版管线漏了这一步，assemble 的占位
        # 模板会直接进入最终 HTML/CSV，违反 SKILL 第 9 步的两步顺序。
        if args.report:
            enrich_cmd = [sys.executable, str(HERE / "enrich_defects.py"),
                          "--report", str(args.report)]
            if args.design_json:
                enrich_cmd += ["--design", str(args.design_json)]
            stage("enrich_defects", enrich_cmd, args.dry_run, log)
        stage("validate_defects", [sys.executable, str(HERE / "validate_defects.py"),
                                   "--report", str(args.report), "--strict"], args.dry_run, log)
        stage("coverage_gate", [sys.executable, str(HERE / "coverage_gate.py"),
                                "--report", str(args.report), "--enforce", "--write"], args.dry_run, log)
        # 【F3】门禁失败必须短路后续报告阶段——SKILL.md「校验不通过不得出报告」。
        # 旧行为：validate/coverage 失败只记 status=failed，继续 generate_reports 并
        # return 0，坏数据照常产出三件套报告（最危险的静默假通过）。
        # 【审查修复】notRun 的门禁 stage 同样必须短路：stage 失败有两种形态——
        # rc≠0 记 failed，命令本身没跑成（FileNotFound/超时）记 notRun。
        # 「coverage_gate 根本没跑成」比「跑了判失败」更危险，却绕过下面的 failed 检查
        # 继续出报告。notRun 一并算门禁未通过。
        #
        # 【区别对待】短路只覆盖「数据不合法 / 汇总没跑成」两类，**不含覆盖率不足**：
        # 覆盖率回答"检查了多少"，与"已有结论是否可信"是两个问题。覆盖率不达阈值时
        # 把整份报告全部扣住，用户看到的是"什么都没有"，而不是"查了 24 项、20 项
        # 因以下原因未执行"——后者才是他要的信息，且达标与否的事实已由 meta.coverage
        # 与报告横幅如实写出，不存在把不足伪装成通过的风险。
        # coverage_gate 本身仍以非零码退出（事实保留），但不阻断出报告。
        gate_failed = [n for n in ("validate_defects", "assemble_report")
                       if log.get(n, {}).get("status") in ("failed", "notRun")]
        cov = log.get("coverage_gate", {})
        coverage_blocked = cov.get("status") in ("failed", "notRun")
        # coverage_gate 的退出码分两类事实：1=覆盖率不足（可照常出报告）；
        # 4/5=维度状态非法或 notRun 未写原因（数据不可信，必须短路）。
        # 旧版把所有非零统一降级为 warn，等于把硬门禁当建议，违反 gates.md。
        if coverage_blocked and cov.get("returncode") in (4, 5):
            gate_failed.append("coverage_gate")
        if coverage_blocked and "coverage_gate" not in gate_failed:
            log["coverageIncomplete"] = {
                "status": "warn",
                "reason": "覆盖率门禁未通过，报告照常产出但结论不完整；"
                          "缺口维度与原因见 meta.coverage 与 meta.checks",
            }
            print("[warn] coverage_gate 未通过：报告照常产出，按「结论不完整」标注")
        if gate_failed and not args.dry_run:
            for n in gate_failed:
                print("[abort] {} 失败，按「校验不通过不得出报告」短路后续阶段".format(n))
            log["generate_reports"] = {
                "status": "notRun",
                "reason": "门禁失败短路：{}".format(", ".join(gate_failed)),
            }
            log["baseline_regression"] = {
                "status": "notRun",
                "reason": "门禁失败短路：{}".format(", ".join(gate_failed)),
            }
            if "pattern_pool_merge" not in log:
                log["pattern_pool_merge"] = {
                    "status": "notRun",
                    "reason": "门禁失败短路：{}".format(", ".join(gate_failed)),
                }
            outdir.mkdir(parents=True, exist_ok=True)
            log_file = outdir / "pipeline-log.json"
            log_file.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
            print("日志: {}".format(log_file))
            return 1
        ignore_file = workdir / "ignoredRegions.json"
        if ignore_file.exists():
            stage("ignore_regions", [sys.executable, str(HERE / "ignore_regions.py"),
                                     "--file", str(ignore_file),
                                     "--output", str(outdir / "ignore-audit.json"), "--enforce"], args.dry_run, log)
        # 清理审计：不加 --enforce。未清理的写操作说明环境有残留，必须出现在日志与
        # 报告里，但不该阻断"出报告"这件事本身——先让人看到，再由人决定是否重跑。
        if ledger.exists():
            stage("write_ops_audit", [sys.executable, str(HERE / "write_ops_ledger.py"),
                                      "--file", str(ledger), "--audit"], args.dry_run, log)
        # 缺陷网页截图与设计稿整屏对比必须发生在本轮汇总后、报告生成前。
        # capture_defect_shots 使用本轮 report 的 selector/nth，并在 goto 前恢复 auth；
        # design_compare 再把网页 fullPage 图取首屏，与设计稿整屏按同一视口并排。
        # 这样不会把旧轮截图或元素 bbox 直接拼到新报告中。
        if args.page_url and not args.no_defect_shots:
            shot_cmd = [node, str(HERE / "node" / "capture_defect_shots.cjs"),
                        "--report", str(args.report), "--url", args.page_url,
                        "--out", str(workdir), "--viewport", args.shot_viewport,
                        "--max", "60"] + safety
            if args.browser_profile:
                shot_cmd += ["--profile", str(args.browser_profile)]
            if args.align_sidebar and args.safety_policy != "staticOnly":
                shot_cmd += ["--align-sidebar", str(args.align_sidebar)]
            prepared = args.dry_run
            if not args.dry_run:
                try:
                    report = json.loads(args.report.read_text(encoding="utf-8"))
                    for defect in report.get("defects", []):
                        defect["screenshots"] = []
                        for field in ("shotBox", "shotNote", "designNote", "shotError"):
                            defect.pop(field, None)
                    report.pop("defectShots", None)
                    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                    prepared = True
                except (OSError, ValueError) as exc:
                    log["capture_defect_shots"] = {
                        "status": "failed", "reason": "截图前撤除旧证据失败：{}".format(exc)}
            if prepared:
                stage("capture_defect_shots", shot_cmd, args.dry_run, log)
            shot_result = log["capture_defect_shots"]
            if args.align_sidebar and args.safety_policy == "staticOnly":
                shot_result["alignmentReason"] = "staticOnly 策略下不执行侧栏交互对齐"
            if shot_result["status"] not in ("ran", "planned"):
                reason = "capture_defect_shots 未成功，本轮不复用旧截图：{}".format(
                    shot_result.get("reason") or shot_result.get("stderrTail") or shot_result["status"])
                log["design_compare"] = {"status": "notRun", "reason": reason}
                # 命令未启动或超时时，截图脚本自身可能来不及撤除旧证据。
                try:
                    report = json.loads(args.report.read_text(encoding="utf-8"))
                    summary = report.get("defectShots", {})
                    captured = summary.get("captured", 0) if isinstance(summary, dict) else 0
                    current_evidence = any(
                        shot.get("kind") in ("crop", "page") and shot.get("path") and Path(shot["path"]).is_file()
                        for defect in report.get("defects", [])
                        for shot in defect.get("screenshots", []) if isinstance(shot, dict))
                    if prepared and isinstance(captured, (int, float)) and captured > 0 and current_evidence:
                        summary.update(status="partial", reason=reason)
                        shot_result["evidenceReason"] = "保留本轮已生成的有效截图；部分失败不执行设计对比"
                    else:
                        for defect in report.get("defects", []):
                            defect["screenshots"] = []
                            for field in ("shotBox", "shotNote", "designNote"):
                                defect.pop(field, None)
                            defect["shotError"] = reason
                        report["defectShots"] = {"status": "failed", "reason": reason, "count": 0}
                    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                except (OSError, ValueError) as exc:
                    log["capture_defect_shots_cleanup"] = {
                        "status": "failed", "reason": "撤除旧截图引用失败：{}".format(exc)}
            elif args.figma_png:
                stage("design_compare", [sys.executable, str(HERE / "design_compare.py"),
                                          "--report", str(args.report),
                                          "--design-png", str(args.figma_png),
                                          "--viewport", args.shot_viewport], args.dry_run, log)
            else:
                log["design_compare"] = {"status": "notRun", "reason": "缺少 --figma-png"}
        else:
            log["capture_defect_shots"] = {"status": "notRun", "reason": "按 --no-defect-shots 显式跳过"}
            log["design_compare"] = {"status": "notRun", "reason": "未提供页面 URL 或按 --no-defect-shots 跳过"}
        stage("baseline_regression", [node, str(HERE / "baseline_regression.cjs"), "--current", str(args.report),
                                      "--baseline-dir", str(baseline_dir), "--out", str(outdir / "regression.json")],
              args.dry_run, log)
        # 跨轮不稳定性量化：只读 baseline/runs 历史快照，不改任何判定逻辑。
        # 放在基线回归之后——本轮快照刚写入，统计里就能包含这一轮。
        if not args.no_flaky:
            stage("flaky_report", [sys.executable, str(HERE / "flaky_report.py"),
                                   "--baseline-dir", str(baseline_dir),
                                   "--out", str(outdir / "flaky.json"),
                                   "--csv", str(outdir / "flaky.csv")],
                  args.dry_run, log)
        else:
            log["flaky_report"] = {"status": "notRun", "reason": "按 --no-flaky 显式跳过"}
        stage("generate_reports", [sys.executable, str(HERE / "generate_reports.py"), "--input", str(args.report),
                                   "--outdir", str(outdir)], args.dry_run, log)
        # 历史学习闭环的最后一环：把本轮缺陷按指纹并回池（幂等，无新模式不写盘）。
        # 只累积与排序，不自动生成用例——见 SKILL.md 对 bug_pattern_pool 的纪律。
        if pool is not None:
            stage("pattern_pool_merge", [sys.executable, str(HERE / "pattern_pool.py"),
                                         "--merge", str(args.report), "--pool", str(pool)],
                  args.dry_run, log)
    else:
        log["baseline_regression"] = {"status": "notRun", "reason": "缺少 --report"}
        log["generate_reports"] = {"status": "notRun", "reason": "缺少 --report"}

    log_file = outdir / "pipeline-log.json"
    if not args.dry_run:
        outdir.mkdir(parents=True, exist_ok=True)
        log_file.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        print("日志: {}".format(log_file))
    # 【审查修复·P2-9】非门禁 stage 失败仍 return 0——stabilize 打不开页面、
    # compare_fields 崩掉时管线"绿着"退出，下游只看到全 notRun 的报告还以为
    # 一切正常。失败必须反映在退出码与 pipeline-log（pipelineStatus=degraded
    # + failedStages 清单），不静默。dry-run（planned）不算失败；主动跳过
    # 的 notRun 没有 command，实际调用却无法启动/超时的 notRun 必须算失败。
    failed_stages = sorted(n for n, v in log.items()
                           if isinstance(v, dict) and (v.get("status") == "failed" or
                               (v.get("status") == "notRun" and v.get("command"))))
    if failed_stages and not args.dry_run:
        log["pipelineStatus"] = "degraded"
        log["failedStages"] = failed_stages
        log_file.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        print("警告：{} 个 stage 失败：{}（pipelineStatus=degraded）".format(
            len(failed_stages), ", ".join(failed_stages)), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
