# -*- coding: utf-8 -*-
"""一键流水线：一条命令打通「载入契约 → 判适用性 → 生成用例 → 执行 → 出报告」。

解决的问题：原本要人工走七步（0.5 载入契约 / 1 梳理契约 / 1.5 判适用性 /
2 设计覆盖 / 2.5 过缺陷模式 / 3 收紧断言 / 5 执行），每一步都可能"做少了"。
本脚本把可自动化的部分（0.5 + 1 + 1.5 + 2 + 2.5 + 5）串成一条命令。

用法：
  # 从契约文档直接跑（只生成，不执行）
  python flow.py --contract <契约路径>
  # 从知识库按 action 取契约并生成
  python flow.py --action 1000107 [--kb <KB根目录>]
  # 生成并真实执行
  python flow.py --contract <路径> --execute
  # 只跑指定维度
  python flow.py --contract <路径> --execute --only B,H

产物（默认按项目/接口/版本/唯一批次归档；--out-dir 显式目录不得复用）：
  generated_cases.json   声明式用例（含 COVERAGE_PLAN / N/A 证明 / 命中模式）
  intelligent_test_plan.json   智能计划（契约模型 + 关系 + 显式依赖 + 风险 + 资产 + 阻塞项）
  dependencies.json      契约正文显式声明的跨接口依赖（含原文证据与行号）
  plan.md                人可读计划（用例总览 / 待确认问题 / 前置依赖与关系 / 风险与不适用凭据）
  api_test_issues.csv    问题台账（16 列统一口径：基础 9 列 + 置信度/HTTP状态码/请求ID/TraceID/证据路径/归因/复测状态）
  api_test_report_full.json
  api_test_issues_enhanced.md   增强报告（聚类去重 + 复现命令 + 修复建议）
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import case_generator  # noqa: E402
import case_runner     # noqa: E402
import common_params   # noqa: E402
import api_test_runner as R  # noqa: E402
import report_enhance  # noqa: E402
import discovery_profile  # noqa: E402
from asset_registry import register  # noqa: E402
from contract_model import load_contract_model  # noqa: E402
from relationship_inference import infer_relations  # noqa: E402
from risk_engine import score as score_risk  # noqa: E402
from plan_builder import build_plan  # noqa: E402
import dependency_scan  # noqa: E402
import plan_render  # noqa: E402
import precondition_scan  # noqa: E402
import artifact_readback  # noqa: E402
import asset_runtime  # noqa: E402
import case_coverage  # noqa: E402


def verify_artifacts(out_dir, csv_path):
    """产物回读校验：有 error 级问题就出声，并把机器可读结果落盘。

    返回 error 数，主流程将非零 error 纳入 failed 判定并返回 2。
    warning 默认告警；WJX_STRICT_READBACK=1 将 warning 也升级为 exit(2)。
    校验必须在报告增强后执行，避免核验即将被覆盖的旧台账。
    """
    issues, summary = artifact_readback.run_readback(dirpath=out_dir, issues_csv=csv_path)
    errors = [i for i in issues if i["severity"] == artifact_readback.ERROR]
    warnings = [i for i in issues if i["severity"] == artifact_readback.WARNING]

    rb_path = os.path.join(out_dir, "artifact_readback.json")
    try:
        with open(rb_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print("⚠️ 回读结果落盘失败（%s），校验结论仍见上方输出" % e)

    if not errors and not warnings:
        print("\n产物回读通过：台账编码 / 表头 / 列宽 / 编号 / 行数一致，可交付。")
        return 0
    print("\n产物回读发现问题（详见 %s）：" % rb_path)
    for it in issues:
        icon = "✗" if it["severity"] == artifact_readback.ERROR else "!"
        print("  %s [%s] %s" % (icon, it["severity"].upper(), it["message"]))
    if errors:
        print("  → 台账结构有问题，交付前必须修正（脚本执行成功 ≠ 产物可交付）。")
    if os.environ.get("WJX_STRICT_READBACK") == "1" and (errors or warnings):
        print("  → WJX_STRICT_READBACK=1，升级为失败退出。")
        sys.exit(2)
    return len(errors)

# 写操作类接口：未通过测试环境写门禁时，一律不自动执行（只出计划并要求显式授权）。
WRITE_OPS = ("创建", "新增", "修改", "更新", "删除", "发布", "下线", "导入", "订阅", "清空")
# 只读操作词汇表（与 applicability._OP_RULES 的“查询”类一致）。写门禁据此区分只读与写；
# 空/未知操作一律按“需授权”处理，防止借未知 op_type 绕过（v3 目标审计 G12）。
READ_OPS = ("查询", "获取", "详情", "列表", "读取", "搜索", "统计", "计数", "导出",
            "list", "get", "query", "detail", "search")

# 执行档位 → 用例筛选规则。plan 档位不执行（在 main 中提前返回）。
PROFILES = {
    # 冒烟档位：只跑 P0 且启用的用例（快速判活）
    # 说明：这里的 enabled 过滤是 select_by_profile 自身的契约（有测试锁定）。
    # 「禁用用例是否算档位收窄」在调用处判定，见 main 的 disabled_dropped。
    "smoke": lambda c: str(c.get("priority")) == "P0" and bool(c.get("enabled", True)),
    "full": lambda c: bool(c.get("enabled", True)),
    "exploratory": lambda c: True,  # 含探索性/低置信度用例
    # 发现档：全选（与 exploratory 同），区别在**生成期**——本档会先激活
    # discovery_profile，放宽超长上限/载荷条数/字段族配额/AF 条数。选择规则
    # 只是"全选"，真正的"多探"发生在限额上，见 discovery_profile。
    "discovery": lambda c: True,
}


def resolve_adapter_path(name, external_dir=None):
    """解析适配器源文件路径：优先包外目录（WJX_ADAPTER_DIR / 显式 external_dir），再回包内。

    v3 G13：站点适配器必须以包外扩展方式加载，禁止为挂载反复改写已安装专家包。
    返回 Path（不导入），便于 dry-run 在真正加载前展示来源。
    """
    import os as _os
    from pathlib import Path as _Path
    ext = external_dir or _os.environ.get("WJX_ADAPTER_DIR")
    if ext:
        candidate = _Path(ext) / (str(name) + ".py")
        if candidate.is_file():
            return candidate
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    return _Path(root) / "adapters" / (str(name) + ".py")


def build_adapter(name):
    """按 --adapter 选择适配器。

    支持：rest / sojump / graphql / grpc；站点适配器可由 WJX_ADAPTER_DIR 指向包外目录加载。
    name 未匹配且包外有同名模块时优先加载包外扩展；否则 fallback 到 rest。
    grpc 缺依赖时实例化即报错（懒加载防呆，不静默降级）。

    两点容易误读的地方：
    - 主流程未显式指定时注入的是 **sojump**（见 main 的 adapter_name 默认值），
      好让写操作门禁默认生效——这是有意的保守默认，不能按"rest 默认"去理解。
    - 适配器在当前主链路**只用于写操作门禁**（is_write_allowed）；公共参数由
      api_test_runner 负责、实际发请求也走它，build_request/send 等是预留扩展点。
    """
    import importlib.util as _ilu
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)  # adapters/ 与 scripts/ 平级，需把 skill 根加入搜索路径
    # 包外扩展优先（v3 G13）：WJX_ADAPTER_DIR 下的同名模块先于包内同名适配器加载，
    # sojump 等站点适配器由此可外置，无需改写已安装专家包。
    ext = os.environ.get("WJX_ADAPTER_DIR")
    if ext:
        candidate = os.path.join(ext, str(name) + ".py")
        if os.path.isfile(candidate):
            spec = _ilu.spec_from_file_location("ext_adapter_" + str(name), candidate)
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            cls = getattr(mod, "ApiAdapter", None) or getattr(mod, "Adapter", None)
            for attr in ("SojumpAdapter", "RestAdapter", "GraphqlAdapter", "GrpcAdapter"):
                cls = getattr(mod, attr, None) or cls
            if cls is None:
                raise SystemExit("包外适配器 %s 未暴露 ApiAdapter/Adapter 类" % candidate)
            return cls()
    if name == "sojump":
        try:
            from adapters.sojump import SojumpAdapter
        except ImportError as exc:
            raise SystemExit("适配器 sojump 未随本包分发（站点适配）；请设置 WJX_ADAPTER_DIR 指向包外适配器目录，"
                             "或改用 --adapter rest，或按 references/adapter_contract.md 自行放置。原因: %s" % exc)
        return SojumpAdapter()
    if name == "graphql":
        from adapters.graphql import GraphqlAdapter
        return GraphqlAdapter()
    if name == "grpc":
        from adapters.grpc import GrpcAdapter
        return GrpcAdapter()
    from adapters.rest import RestAdapter
    return RestAdapter()


def load_exec_config(path):
    """加载可选执行配置（缺省返回空配置，不影响默认行为）。"""
    if not path:
        return {}
    if not os.path.isfile(path):
        sys.stderr.write("执行配置不存在：%s\n" % path)
        sys.exit(2)
    with open(path, "r", encoding="utf-8") as f:
        return validate_exec_config(json.load(f), path)


_ADAPTERS = {"rest", "sojump", "graphql", "grpc"}
_PROFILE_NAMES = {"plan", "smoke", "full", "exploratory", "discovery"}


def validate_exec_config(cfg, label="执行配置"):
    """配置的单一校验入口：flow 与多接口预检共用，避免「预检过了、执行时才炸」。"""
    if cfg is None:
        return {}
    if not isinstance(cfg, dict):
        raise ValueError("%s 必须是 JSON 对象" % label)
    adapter = cfg.get("adapter")
    if adapter is not None and adapter not in _ADAPTERS:
        # v3 G13：包外 WJX_ADAPTER_DIR 存在同名模块时允许该适配器名
        ext = os.environ.get("WJX_ADAPTER_DIR")
        external_ok = bool(ext and os.path.isfile(os.path.join(ext, str(adapter) + ".py")))
        if not external_ok:
            raise ValueError("%s adapter 必须是 %s（或由 WJX_ADAPTER_DIR 提供包外扩展）"
                             % (label, "/".join(sorted(_ADAPTERS))))
    env = cfg.get("environment")
    if env is not None:
        if not isinstance(env, dict):
            raise ValueError("%s environment 必须是对象" % label)
        if "type" in env and not isinstance(env["type"], str):
            raise ValueError("%s environment.type 必须是字符串" % label)
        if "write_enabled" in env and not isinstance(env["write_enabled"], bool):
            raise ValueError("%s environment.write_enabled 必须是 bool" % label)
    ex = cfg.get("execution")
    if ex is not None:
        if not isinstance(ex, dict):
            raise ValueError("%s execution 必须是对象" % label)
        if "profile" in ex and ex["profile"] not in _PROFILE_NAMES:
            raise ValueError("%s execution.profile 必须是 %s" % (label, "/".join(sorted(_PROFILE_NAMES))))
        # 只校验非负不够：这些值来自外部 --config，没有上界时
        # request_interval_ms=99999999 会让 200 条用例跑几十天、
        # retry_network_errors 配大数会让每个请求真打几万次。执行侧必须自己
        # 设天花板——把「配置写错」拦在启动阶段，而不是让它变成一轮跑不完的任务。
        _EXEC_LIMITS = {"max_cases": 20000, "request_interval_ms": 60000,
                        "retry_network_errors": 10}
        for key, _cap in _EXEC_LIMITS.items():
            if key in ex:
                value = ex[key]
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError("%s execution.%s 必须是非负整数" % (label, key))
                if value > _cap:
                    raise ValueError("%s execution.%s=%d 超过上限 %d（过大会让整轮执行"
                                     "不可控，请确认是否写错单位）" % (label, key, value, _cap))
        for key in ("cleanup_enabled", "minimize_findings"):
            if key in ex and not isinstance(ex[key], bool):
                raise ValueError("%s execution.%s 必须是 bool" % (label, key))
        if "cleanup_delete_action" in ex and not isinstance(ex["cleanup_delete_action"], str):
            raise ValueError("%s execution.cleanup_delete_action 必须是字符串" % label)
    ins = cfg.get("inputs")
    if ins is not None:
        if not isinstance(ins, dict):
            raise ValueError("%s inputs 必须是对象" % label)
        contracts = ins.get("contracts")
        if contracts is not None and (not isinstance(contracts, list)
                                      or any(not isinstance(c, str) for c in contracts)):
            raise ValueError("%s inputs.contracts 必须是字符串列表" % label)
        history = ins.get("historical_issues")
        if history is not None and not isinstance(history, (str, list)):
            raise ValueError("%s inputs.historical_issues 必须是路径或列表" % label)
        baseline = ins.get("ui_baseline")
        if baseline is not None and not isinstance(baseline, str):
            raise ValueError("%s inputs.ui_baseline 必须是路径字符串" % label)
    return cfg


def write_gate(adapter, adapter_name, exec_cfg, op_type):
    """写操作门禁（适配器无关，v3 目标审计 G12）。

    返回 (allowed, reason)。授权由「操作语义 + 执行配置的环境声明 + 适配器拒绝策略」共同决定，
    与适配器名称无关：

    - 明确的只读操作（READ_OPS）：放行；
    - 写操作（WRITE_OPS）或空/未知操作：需 environment.type=="test" 且
      environment.write_enabled==true 的显式声明；若适配器实现了 is_write_allowed，
      其拒绝（False）具有否决权，适用于任何适配器名称，不再硬编码 sojump。
    """
    env = (exec_cfg or {}).get("environment") or {}
    op = str(op_type or "").strip()
    if op in READ_OPS:
        return True, ""
    env_authorized = env.get("type") == "test" and env.get("write_enabled") is True
    # 适配器的显式拒绝具有否决权（与名称无关）。未实现该方法不视为拒绝。
    adapter_denies = False
    checker = getattr(adapter, "is_write_allowed", None)
    if callable(checker):
        try:
            adapter_denies = not bool(checker(env))
        except Exception:
            adapter_denies = True  # 门禁判定自身出错时 fail closed
    if env_authorized and not adapter_denies:
        return True, ""
    keys = []
    if not env_authorized:
        keys.append("environment.type == \"test\" 且 environment.write_enabled == true")
    if adapter_denies:
        keys.append("适配器 is_write_allowed(env) 放行")
    return False, (
        "写操作门禁未放行：接口操作类型为「%s」（未知/空操作按写处理），执行配置未满足：%s。\n"
        "  仅凭域名或 HTTP 方法不足以信任；见 references/intelligence_policy.md"
        % (op or "未知", "；".join(keys)))


def select_by_profile(cases, profile):
    """按档位筛选用例：smoke 只跑 P0，full 跑全部启用项，exploratory 额外纳入探索性用例。"""
    rule = PROFILES.get(profile)
    if not rule:
        return list(cases)
    return [c for c in cases if rule(c)]


def apply_exec_policy(exec_cfg):
    """把执行配置里的策略字段**真正接线**，返回执行期需要的时间/条数策略。

    assets/intelligent_config.example.json 一直声明着 max_cases / request_interval_ms /
    retry_network_errors / minimize_findings，但此前没有任何代码读取它们——配置项形同虚设，
    用户以为设了限流与条数上限，实际全部按硬编码值跑。这里逐项落到真实执行路径上。
    """
    ex = (exec_cfg or {}).get("execution") or {}
    policy = {
        "max_cases": int(ex.get("max_cases") or 0),
        "request_interval_ms": int(ex.get("request_interval_ms") or 0),
        "cleanup_enabled": bool(ex.get("cleanup_enabled")),
    }
    retries = ex.get("retry_network_errors")
    if retries is not None:
        R.RETRY_COUNT = max(0, int(retries))
    if ex.get("minimize_findings") is not None:
        case_runner.MINIMIZE_ENABLED = bool(ex.get("minimize_findings"))
    return policy


def cap_cases(cases, policy):
    """按 max_cases 截断（0 表示不限）。截断必须**说出来**，不能静默少跑。"""
    limit = policy.get("max_cases") or 0
    if limit and len(cases) > limit:
        # 截断按优先级保留：不看 priority 的"前 N 条"会把 P0 全截掉，留下的是错误的子集
        rank = {"P0": 0, "P1": 1, "P2": 2}
        ranked = sorted(cases, key=lambda c: rank.get(str(c.get("priority") or "P2"), 2))
        print("⚠️ max_cases=%d 生效：按优先级保留 %d 条执行，%d 条未执行"
              "（提高 max_cases 或分批运行）" % (limit, limit, len(cases) - limit))
        return ranked[:limit]
    return cases


def order_cases_for_exec(cases):
    """执行顺序治理：限流观测置尾——连发可能触发限流，污染后续所有用例的判定。"""
    tail = [c for c in cases if (c.get("check") or "") == "rate_limit"]
    if not tail:
        return list(cases)
    return [c for c in cases if (c.get("check") or "") != "rate_limit"] + tail


def cleanup_step(exec_cfg, out_dir):
    """执行收尾的残留治理：清单必产（可见性优先），启用清理且提供删除 action 时真删除。

    concurrency / rate_limit / enum 遍历每轮会对写接口真实多次提交，
    残留若不可见，等于没有治理过。"""
    ex = (exec_cfg or {}).get("execution") or {}
    res = list(getattr(R, "CREATED_RESOURCES", []) or [])
    manifest = os.path.join(out_dir, "created_resources.json")
    with open(manifest, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    if not res:
        print("\n[清理] 本次执行未创建任何资源。")
        return
    delete_action = ex.get("cleanup_delete_action")
    if not (ex.get("cleanup_enabled") and delete_action):
        print("\n[清理] 残留 %d 条资源（清单: %s）；未启用自动清理"
              "（需 execution.cleanup_enabled + cleanup_delete_action）" % (len(res), manifest))
        return
    env = (exec_cfg or {}).get("environment") or {}
    if env.get("type") != "test" or env.get("write_enabled") is not True:
        print("\n[清理] 未显式授权测试环境写操作，仅保留残留清单，不执行删除。")
        return
    owned = [item for item in res if item.get("created") is True
             and item.get("run_id") == getattr(R, "RUN_ID", None)
             and item.get("run_id") and item.get("case_id")
             and item.get("op_type") in {"创建", "新增"}]
    if len(owned) != len(res):
        print("\n[清理] %d 条资源缺少本轮创建归属证据，保留清单、不自动删除。" % (len(res) - len(owned)))
    ok, fail = 0, []
    for item in owned:
        _c, _r, parsed = R.call(R.build_payload(delete_action, id=item.get("id")))
        if isinstance(parsed, dict) and parsed.get("result") is True:
            ok += 1
        else:
            fail.append(item)
    print("\n[清理] 自动删除 %d/%d 条残留资源（删除 action=%s）%s"
          % (ok, len(res), delete_action,
             ("；失败 %d 条，见清单" % len(fail)) if fail else ""))


def _contract_hash_of(doc):
    """契约哈希的单一出处（写基线/复测两侧必须同源，否则 STALE_BASELINE 永不触发）。"""
    import regression as REG
    # 【审查修复·P1-5】doc.get("contract_model") 此前恒为 None（generate 不产该键），
    # 哈希 fallback 只含 action+coverage_plan——契约字段增删改不影响哈希，
    # STALE_BASELINE 判定（regression.py 声明的核心保护）不可达。case_generator
    # 已补产 contract_model（规范化契约子集），此键命中后契约任何字段/约束变化
    # 都会换哈希；老产物无该键时回落原 fallback 口径。
    model = doc.get("contract_model") or {
        k: doc.get(k) for k in ("action", "coverage_plan") if k in doc}
    return REG.contract_hash(model)


def write_regression_artifacts(out_dir, doc):
    """只保存本轮实际执行的用例与期望，不修改历史批准基线。"""
    import regression as REG
    executed = {r.get("case_no"): r for r in (R.results or [])
                if r.get("status") in {"PASS", "FAIL", "ERROR", "PRECONDITION_UNMET"}}
    cases = [c for c in (doc.get("cases") or []) if c.get("id") in executed]
    chash = _contract_hash_of(doc)
    # 【审查修复·P1-6】expectations 此前只写 priority/dimension，last_error 恒空
    # ——REGRESSED（修复引入新回归，四态之一）永不触发。补记本轮失败原因：
    # 上轮失败原因 A、本轮失败原因 B≠A 才算"新回归"；通过/未执行记空串。
    last_err = {}
    for r in (R.results or []):
        if r.get("status") in ("FAIL", "ERROR"):
            last_err[r.get("case_no")] = str(r.get("reason") or "")
    entries = [{"case_id": c.get("id", ""), "contract_hash": chash,
                "last_status": executed[c["id"]].get("status"),
                "case_hash": REG.case_fingerprint(c),
                "expectations": {"priority": c.get("priority", ""),
                                 "dimension": c.get("dimension", ""),
                                 "expect": c.get("expect") or {},
                                 "last_error": last_err.get(c.get("id"), "")}}
               for c in cases]
    path = os.path.join(out_dir, "regression_baseline.json")
    asset_runtime.write_json(path, {
        "version": 1, "status": "snapshot", "snapshot": True,
        "cases": cases, "cases_hash": asset_runtime.cases_hash({"cases": cases}),
        "entries": entries, "source_run": str(Path(out_dir).resolve()),
        "case_statuses": {r.get("case_no"): r.get("status") for r in R.results},
        "coverage_blocked": bool(getattr(R, "coverage_blocked", False)),
        "findings_count": len(R.findings),
    })
    print("回归 snapshot -> %s（%d 条；需显式 --promote-baseline 才成为正式基线）" % (path, len(entries)))

    # 待确认项单独落盘：它们**没有**进缺陷台账，报告里若只有 findings，
    # 这批会整体消失（既不显示缺陷，也不显示待办）。见 pending_confirmation 模块头。
    import pending_confirmation as PCI
    pci = PCI.collect(R.findings, cases)
    pci_path = os.path.join(out_dir, "pending_confirmations.json")
    asset_runtime.write_json(pci_path, pci)
    if pci["total"]:
        print("待确认项 -> %s（%d 组 / %d 条；未计入缺陷台账）"
              % (pci_path, pci["groups"], pci["total"]))


def retest_report(baseline_path, results, doc=None):
    """复测判定：FIXED / STILL_FAILING / REGRESSED / STALE_BASELINE / UNBASELINED / BLOCKED_ENV。"""
    import regression as REG
    # 【审查修复·P1-5】复测必须传当前契约哈希，否则 STALE_BASELINE 分支（契约
    # 已变、旧快照不可信）被 None 短路，永远不可达。与写基线同源取哈希。
    chash = _contract_hash_of(doc) if isinstance(doc, dict) else None
    return REG.retest_verdicts(REG.load_baseline(baseline_path), results,
                               current_contract_hash=chash,
                               current_cases=doc.get("cases") if isinstance(doc, dict) else None)


def resolve_contract_from_kb(action, kb=None):
    """用 kb_load_contract 的能力按 action 在知识库里定位契约文件。

    解析顺序与 kb_load_contract.main 一致：显式 --kb > KB_PATH 环境变量 >
    与 skill 同级的默认候选（default_kb_candidates 的 docstring 声明
    "实际仅 KB_PATH 生效"，依赖此回退）。全部不可用时返回 None，
    由调用方按 doc_review 登记节的降级纪律处理，不猜测契约。
    """
    try:
        import kb_load_contract as KB
    except Exception as e:
        print("无法导入 kb_load_contract：%s" % e)
        return None
    if kb:
        roots = [kb]
    else:
        roots = []
        env_kb = os.environ.get("KB_PATH")
        if env_kb:
            roots.append(env_kb)
        roots.extend(KB.default_kb_candidates())
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            hits = KB.find_by_filesystem(root, str(action)) or []
        except Exception as exc:  # noqa: BLE001
            # 目录存在但读取失败（权限/编码/并发锁）与"确实没有该契约"必须可区分：
            # 前者静默当成空会让调用方误判"知识库无记录"，掩盖环境问题。
            print("知识库检索失败（%s）：%s" % (root, exc), file=sys.stderr)
            continue
        if hits:
            return hits[0] if isinstance(hits[0], str) else str(hits[0])
    return None


def find_local_dep_contract(action, near=None):
    """在同级目录找该 action 的契约文件（"契约集放一起"是本 skill 推荐的用法）。

    只按文件名校验 action 号是否出现，与 kb_load_contract 的文件系统匹配同一标准，
    不做相似性推断。找不到返回 None。
    """
    if not action or not near:
        return None
    d = os.path.dirname(os.path.abspath(near))
    if not os.path.isdir(d):
        return None
    try:
        names = sorted(os.listdir(d))
    except Exception as exc:  # noqa: BLE001
        # 目录存在但读不了：此前静默 return None，与「目录里没有匹配文件」不可区分，
        # 依赖契约静默降级到 KB 查找。出声点名目录与原因。
        print("[dep] 读依赖契约目录失败：%s（%s），降级到 KB 查找" % (d, exc), file=sys.stderr)
        return None
    for n in names:
        if n.lower().endswith(".md") and str(action) in n:
            return os.path.join(d, n)
    return None


def load_confirmed_runtime_deps(path):
    """加载**已确认**的运行实证依赖，供执行期做前置判定。

    只取 `status=confirmed`：候选是"模型说可能有依赖"，未经人核对就让它吞掉失败，
    等于把真缺陷从台账里静默移除。文件不存在 / 解析失败一律返回空列表——
    与不传该参数的行为完全一致，不阻断执行。
    """
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception as exc:
        sys.stderr.write("WARN: 运行实证依赖读取失败，本次不做前置判定：%s\n" % exc)
        return []
    return precondition_scan.confirmed_only(doc.get("dependencies") or [])


def contract_deps_from_dependency_file(path):
    """从 ``dependencies.json``（契约声明通道）取出可参与前置判定的依赖。

    只取 ``kind == "depends_on"``：这类条目来自「请先调用接口 X」这类触发词，
    语义上是前置条件。``companion_read``（配套读接口）不是前置条件——把它当
    前置判定会让「失败归因到配套接口」整类误判，坚决不进。

    契约声明通道**不需要人工确认**即可参与判定：它不是从错误文案里猜出来的，
    而是契约正文白纸黑字写出的（这与运行实证通道必须走「候选→人工确认」是
    两条不同的纪律——后者判错的代价是吞掉真缺陷，前者没有这个风险面）。
    文件不存在 / 解析失败一律返回空列表，行为与未接入完全一致。
    """
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception as exc:
        sys.stderr.write("WARN: 契约声明依赖读取失败，本次不参与前置判定：%s\n" % exc)
        return []
    self_action = str(doc.get("self_action") or "").strip()
    out = []
    for dep in doc.get("dependencies") or []:
        if str(dep.get("kind") or "") != "depends_on":
            continue
        target = str(dep.get("target_action") or "").strip()
        if not target:
            continue
        out.append({
            "status": "confirmed",
            "subject_action": self_action,
            "target_action": target,
            "target_name": dep.get("target_name") or "",
            "evidence": dep.get("evidence") or "",
            "condition": dep.get("reason") or "契约显式声明的前置接口",
            "confidence": dep.get("confidence", 1.0),
            "source": "contract",
        })
    return out


def merge_dependency_channels(runtime_deps, contract_deps):
    """两条依赖通道合并，同一对 (subject_action, target_action) **运行实证优先**。

    运行实证来自真实失败响应，比契约描述更贴近当前接口行为；契约声明在两者
    都存在时只作补充。返回 (合并列表, 运行实证胜出的条数)。
    """
    runtime_deps = list(runtime_deps or [])
    contract_deps = list(contract_deps or [])
    if not contract_deps:
        return runtime_deps, 0
    runtime_keys = {(str(d.get("subject_action") or "").strip(),
                     str(d.get("target_action") or "").strip())
                    for d in runtime_deps}
    merged = list(runtime_deps)
    overridden = 0
    for dep in contract_deps:
        key = (str(dep.get("subject_action") or "").strip(),
               str(dep.get("target_action") or "").strip())
        if key in runtime_keys:
            overridden += 1
            continue
        merged.append(dep)
    return merged, overridden


def ensure_runtime_dep_contracts(deps, out_dir, kb=None, near=None):
    """执行前确认已确认依赖的目标契约可用，并落盘清单。

    confirmed 依赖的 target_action 没有契约，Agent 构造前置请求时只能靠猜。
    解析顺序：① 主契约同级目录（"契约集放一起"是推荐用法，本地命中不查库）
    → ② 知识库按 action 定位。取不到的进 missing 点名留痕，不猜、不伪造、不阻断执行。
    无 confirmed 依赖时不产出任何文件，行为与未接入时完全一致（增量接入）。
    """
    if not deps:
        return None
    records, seen = [], set()
    for dep in deps:
        act = str(dep.get("target_action") or "").strip()
        if not act or act in seen:
            continue
        seen.add(act)
        local = find_local_dep_contract(act, near)
        path, origin = (local, "local") if local else (resolve_contract_from_kb(act, kb) or "", "kb")
        records.append({
            "target_action": act,
            "target_name": dep.get("target_name") or "",
            "subject_action": dep.get("subject_action") or "",
            "contract_path": path,
            "origin": origin if path else "",
        })
    if not records:
        return None
    missing = [r["target_action"] for r in records if not r["contract_path"]]
    local_n = len([r for r in records if r["origin"] == "local"])
    out = {"contracts": records, "missing": missing}
    path = os.path.join(out_dir, "runtime_dep_contracts.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("依赖契约清单 -> %s（本地 %d 条 / 知识库 %d 条 / 缺失 %d 条）"
          % (path, local_n, len(records) - len(missing) - local_n, len(missing)))
    return out


def reset_runtime():
    """清空一次执行的全局结果，避免同一进程多次运行相互污染。"""
    for name in ("results", "findings"):
        value = getattr(R, name, None)
        if isinstance(value, list):
            value.clear()
    if hasattr(R, "shallow_passes"):
        R.shallow_passes = 0
    if hasattr(R, "coverage_blocked"):
        R.coverage_blocked = False
    if hasattr(R, "CREATED_RESOURCES"):
        R.CREATED_RESOURCES.clear()
    R.COVERAGE_PLAN = []


def load_history(spec):
    """历史问题输入：可写成 CSV 路径，也可内联列表；用于风险排序（不单独作为硬断言）。"""
    if not spec:
        return []
    if isinstance(spec, str):
        if not os.path.isfile(spec):
            sys.stderr.write("历史问题文件不存在，已忽略：%s\n" % spec)
            return []
        with open(spec, "r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    if isinstance(spec, list):
        return spec
    return []


def resolve_inputs(exec_cfg, contract=None):
    """把执行配置 `inputs` 段接进流程。

    此前该段被整段忽略：连 `ui_baseline` 都没有传下去，导致**即使用户在配置里指了基线
    文件，plan_builder 依旧认为"缺少页面基线"而阻塞 AB 维度**——配了也白配。
    """
    ins = (exec_cfg or {}).get("inputs") or {}
    contracts = ins.get("contracts") or []
    chosen = contract or (contracts[0] if contracts else None)
    return {
        "contract": chosen,
        "ui_baseline": ins.get("ui_baseline"),
        "history": load_history(ins.get("historical_issues")),
    }


DOC_REVIEW_ARTIFACTS = ("api_test_doc_issues.csv", "api_test_doc_review_report.md")
DOC_REVIEW_EXIT_CODE = 3


def find_doc_review_artifact(contract_path, explicit=None):
    """按约定位置查找文档审查产物：显式路径 → 契约同目录 → 当前目录。"""
    candidates = []
    if explicit:
        candidates.append(explicit)
    if contract_path:
        d = os.path.dirname(os.path.abspath(contract_path))
        candidates += [os.path.join(d, n) for n in DOC_REVIEW_ARTIFACTS]
    candidates += [os.path.join(os.getcwd(), n) for n in DOC_REVIEW_ARTIFACTS]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def _fmt_mtime(path):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(path)))
    except OSError:
        return "时间未知"


def _artifact_stale(artifact, contract_path):
    """产物是否早于契约最后修改时间。

    审查结论只对当时那份契约成立：契约改过之后，旧产物不能证明新契约已审过
    （本轮 include_flow 就踩过——契约 v2 出来后，v1 的审查台账仍能让新契约静默放行）。
    时间戳取不到时按「不判过期」处理并出声，避免因权限问题误阻断。
    """
    if not contract_path:
        return False
    try:
        return os.path.getmtime(artifact) < os.path.getmtime(contract_path)
    except OSError as exc:
        sys.stderr.write(
            "[doc-review] 无法比较产物与契约的修改时间（%s），按未过期处理\n" % exc)
        return False


def _doc_review_block(reason, where, detail=""):
    """门禁统一阻断出口：说明理由、检查位置与两条出路后 exit。"""
    sys.stderr.write(
        "[doc-review] 行为测试被阻断（exit=%d）：%s\n"
        "  硬性步骤：先对契约做文档审查（不发请求），再进入用例生成与执行——\n"
        "  文档里的坑（示例矛盾、不可测约束、缺失错误码）必须在设计用例前暴露。\n"
        "  已检查位置：%s 与当前目录下的 %s\n%s"
        "  两条路：\n"
        "    ① 完成文档审查（方法见 references/doc_review.md），在上述位置产出\n"
        "       api_test_doc_issues.csv 或 api_test_doc_review_report.md 后重跑；\n"
        "       产物须晚于契约最后修改时间——契约改过就要重审，旧结论不顶用；\n"
        "    ② 显式跳过：--skip-doc-review（或环境变量 WJX_SKIP_DOC_REVIEW=1），\n"
        "       留痕于计划 docReview 字段与 plan.md 的「文档审查」行。\n"
        % (DOC_REVIEW_EXIT_CODE, reason, where,
           ", ".join(DOC_REVIEW_ARTIFACTS), detail))
    raise SystemExit(DOC_REVIEW_EXIT_CODE)


def enforce_doc_review_gate(contract_path, skip=False, explicit=None):
    """文档审查前置门禁（硬性步骤）。

    设计依据（2026-10-29 include_flow 轮复盘）：文档审查是行为测试的前置输入，
    先测后审只能靠实测反推「实现与文档冲突」类问题；文档自洽性问题（示例与正文
    矛盾、笔误、缺失的错误码清单）若实现恰好错在同一处，会双双漏过。
    以下三种情形以 DOC_REVIEW_EXIT_CODE 阻断：未发现产物、产物早于契约修改时间、
    显式指定的路径失效（显式路径失效时不得静默退回约定位置，否则调用方会误以为
    自己的产物已经生效）。
    """
    where = (os.path.dirname(os.path.abspath(contract_path))
             if contract_path else "<当前目录>")
    if explicit and not os.path.isfile(explicit):
        _doc_review_block("--doc-review 指定的产物不存在或不是文件：%s" % explicit, where)
    found = find_doc_review_artifact(contract_path, explicit)
    stale = bool(found) and _artifact_stale(found, contract_path)
    if found and not stale:
        sys.stderr.write("[doc-review] 前置文档审查已完成：%s\n" % found)
        return {"performed": True, "source": found, "stale": False}
    if skip or os.environ.get("WJX_SKIP_DOC_REVIEW") == "1":
        if stale:
            sys.stderr.write(
                "[doc-review] 产物早于契约修改时间（%s，契约 %s），已显式跳过——"
                "本轮结论不覆盖该契约的文档审查，留痕于计划 docReview 字段与 plan.md。\n"
                % (_fmt_mtime(found), _fmt_mtime(contract_path)))
        else:
            sys.stderr.write(
                "[doc-review] 未发现文档审查产物，已显式跳过（--skip-doc-review / "
                "WJX_SKIP_DOC_REVIEW=1）——文档自洽性问题（示例矛盾/笔误/缺失错误码）"
                "不会被本轮发现，留痕于计划 docReview 字段与 plan.md。\n")
        return {"performed": False, "source": found, "skipped": True, "stale": stale}
    if stale:
        _doc_review_block(
            "文档审查产物早于契约最后修改时间，旧结论不覆盖本次契约变更。", where,
            "  产物：%s（%s）\n  契约：%s（%s）\n" % (
                found, _fmt_mtime(found),
                os.path.abspath(contract_path), _fmt_mtime(contract_path)))
    _doc_review_block("未发现文档合理性审查产物。", where)


def _semantic_dir(contract_path, prep_dir=None):
    """语义层工作目录：默认放在契约同目录，可用 --prep-dir 指到别处。"""
    return prep_dir or os.path.join(os.path.dirname(os.path.abspath(contract_path)),
                                    "llm_semantic_prep")


def _semantic_names(contract_path, action):
    """提纲与作业文件的命名片段（契约名 + action）。

    文件名必须同时带两段：同一目录常有多份契约，只按 action 命名会互相覆盖，
    让人拿着另一份契约的提纲去抽事实。两段都要消毒（点号一并替换，杜绝 ".."）。
    """
    stem = os.path.splitext(os.path.basename(contract_path))[0]
    stamp = re.sub(r"[^\w-]", "_", stem) or "contract"
    act = re.sub(r"[^\w-]", "_", str(action)) if action else "default"
    return stamp, act


def strict_facts_target(contract_path, action, prep_dir=None):
    """严格语义模式下「作业」应该放到哪：约定文件名，便于重跑时自动认领。"""
    stamp, act = _semantic_names(contract_path, action)
    return os.path.join(_semantic_dir(contract_path, prep_dir),
                        "facts_%s_%s.json" % (stamp, act))


def strict_facts_path(contract_path, action, prep_dir=None):
    """严格语义模式：按约定位置找已交的作业；没有返回空串（不猜、不扫全盘）。"""
    stamp, act = _semantic_names(contract_path, action)
    for name in ("facts_%s_%s.json" % (stamp, act), "facts.json"):
        p = os.path.join(_semantic_dir(contract_path, prep_dir), name)
        if os.path.isfile(p):
            return p
    return ""


def strict_review_path(contract_path, action, prep_dir=None):
    """复核结论的**约定目标位置**：提示语「写到哪」用它（不做存在性搜索）。"""
    stamp, act = _semantic_names(contract_path, action)
    return os.path.join(_semantic_dir(contract_path, prep_dir),
                        "review_%s_%s.json" % (stamp, act))


def strict_review_file(contract_path, action, prep_dir=None):
    """复核结论的**认领**：约定名命中即用；没写约定名时认通用名 review.json——
    与事实文件的双别名兜底对称。实测教训：复核只有约定名一个认领入口，智能体
    按 facts.json 的风格写成通用名就永远停在 exit 5 重复出争议单（单分片实测
    复现的死循环）。返回找到的路径；都没写返回空串。"""
    d = _semantic_dir(contract_path, prep_dir)
    target = strict_review_path(contract_path, action, prep_dir)
    if os.path.isfile(target):
        return target
    generic = os.path.join(d, "review.json")
    if os.path.isfile(generic):
        return generic
    return ""


def strict_review_names(contract_path, action, prep_dir=None):
    """争议单与修订后事实文件的约定位置（同契约同 action 共用一套命名）。"""
    stamp, act = _semantic_names(contract_path, action)
    d = _semantic_dir(contract_path, prep_dir)
    return (os.path.join(d, "dispute_%s_%s.md" % (stamp, act)),
            os.path.join(d, "facts_reviewed_%s_%s.json" % (stamp, act)))


def semantic_precheck(contract_path, facts_path, action=None):
    """生成用例**之前**跑一次比对，把不一致的字段连原文片段一起挑出来。

    脚本不会等人，所以「先比对」只能落成「先预检、有争议就停下」。返回
    (争议列表, 原始事实, 错误信息)；错误一律上抛，绝不带着没比对的作业往下生成。
    """
    try:
        import llm_semantics as LS
        with open(contract_path, "r", encoding="utf-8-sig") as fh:
            text = fh.read()
        with open(facts_path, "r", encoding="utf-8-sig") as fh:
            facts = json.load(fh)
        contract = LS.load_contract(contract_path, action=action)
        rows, _clean = LS.verify(facts, contract, text)
        return LS.collect_disputes(rows, facts, contract, text), facts, ""
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return [], {}, "%s: %s" % (type(exc).__name__, exc)


def write_dispute_review(contract_path, facts_path, action, prep_dir, disputes, expected_sha):
    """把争议单写给会话智能体，并打印下一步该做什么。"""
    import llm_semantics as LS
    dispute_md, revised_target = strict_review_names(contract_path, action, prep_dir)
    review_target = strict_review_path(contract_path, action, prep_dir)
    os.makedirs(os.path.dirname(os.path.abspath(dispute_md)), exist_ok=True)
    with open(dispute_md, "w", encoding="utf-8") as fh:
        fh.write(LS.build_review_prompt(disputes, facts_path, review_target,
                                        expected_sha, contract_path))
    print("=" * 70)
    print("比对不一致 %d 条，本轮先不生成用例：两边说法要回原文再确认一次。" % len(disputes))
    print("1) 争议单（含契约原文相关整段，请逐条看）：%s" % dispute_md)
    print("2) 看完把判断写到：%s" % review_target)
    print("3) 写完再跑**一模一样的这条命令**；机器会重新比对，只采纳有原文依据的修订。")
    print("=" * 70)
    return dispute_md, review_target, revised_target


def run_semantic_prep(contract_path, action=None, prep_dir=None):
    """默认严格语义：停下时产出事实抽取提示词（prep 不调用大模型）。

    llm_semantics --mode prep 只产出提示词；事实由会话智能体或人工按提示词填充，
    经 --mode merge 机械校验（evidence 必须是契约原文子串）后生成用例。
    prep 写不出来时**不得降级**：返回空串，由调用方点名失败，绝不静默继续。
    """
    try:
        import llm_semantics as LS
    except Exception as exc:  # ImportError 及其它导入期问题一律降级
        sys.stderr.write("[semantic] llm_semantics 导入失败（%s），语义层本轮未生效\n" % exc)
        return None
    # 文件名同时带契约名与 action：同目录常有多份契约，只按 action 命名会互相覆盖，
    # 让人拿着另一份契约的提示词去抽事实。两段都要消毒（点号一并替换，杜绝 ".."）。
    safe_stem, safe_action = _semantic_names(contract_path, action)
    # 【审查修复·P2】prep 目录可用 --prep-dir 显式指定：默认仍写契约同目录
    # llm_semantic_prep/（行为被 tests/test_flow_doc_gate_semantic.py 锁定，不改），
    # 但契约目录只读或不想污染工作区时可以指到别处。
    out_dir = _semantic_dir(contract_path, prep_dir)
    out_path = os.path.join(out_dir, "prep_prompt_%s_%s.md" % (safe_stem, safe_action))
    try:
        # makedirs 也在 try 内：契约目录只读时降级而非崩溃（与「prep 失败不阻断」一致）
        os.makedirs(out_dir, exist_ok=True)
        argv = ["llm_semantics.py", "--contract", contract_path, "--mode", "prep",
                "--out", out_path]
        if action:
            argv += ["--action", action]
        old_argv = sys.argv
        sys.argv = argv
        try:
            LS.main()
        finally:
            sys.argv = old_argv
    except SystemExit as exc:
        sys.stderr.write("[semantic] prep 未完成（exit=%s），语义层本轮未生效\n" % exc)
        return None
    except OSError as exc:
        sys.stderr.write("[semantic] 提示词目录不可写（%s），语义层本轮未生效\n" % exc)
        return None
    except Exception as exc:
        sys.stderr.write("[semantic] prep 失败（%s），语义层本轮未生效\n" % exc)
        return None
    sys.stderr.write(
        "[semantic] 本轮不生成用例：还差智能体对接口文档的理解（先比对、后生成）。\n"
        "  提纲：%s\n"
        "  交作业：把事实写到 %s\n"
        "  交完再跑**一模一样的这条命令**，才会生成用例；"
        "确实不要这层理解，才加 --no-llm-facts。\n"
        % (out_path, strict_facts_target(contract_path, action, prep_dir)))
    return out_path


def build_intelligence_artifacts(contract_path, out_dir, action=None, history=None,
                                 ui_baseline=None, doc_review=None, semantic_mode=None):
    """构建统一的契约模型、关系、风险与可审计执行计划。"""
    model = load_contract_model(contract_path, action=action)
    model.relations = infer_relations(model)
    # 显式声明的跨接口依赖：只认契约正文白纸黑字写出的前置接口，不做相似性推断。
    # 追加进 relations（不改动上面推断出的操作类型关系），并单独落 dependencies 字段。
    # 真实解析结果必须先取再传：read_action/list_action 声明在解析结果上，
    # 传桩对象会让「契约声明配套接口」这条通道恒不命中（见 dependency_scan.annotate）。
    _contract = case_generator.load_contract(contract_path, action=action)
    dep_result = dependency_scan.annotate(model, contract_path, contract=_contract)
    _annotated = case_generator.annotate(_contract)
    applicability = case_generator.judge(_annotated)
    # 契约解析告警与「操作类型待确认」必须出声：两者此前都是「写了没人读」的死字段——
    #   * parse_warnings（contract_parser 产出，注释明写「报告消费方按 parse_warnings 点名」，
    #     而全仓消费方并不存在）：解析降级只进一次 stderr 快照、不随产物走，下游无从判断
    #     解析出的字段可不可信；
    #   * op_unconfirmed（applicability 只把告警写进 notes，而 notes 无任何消费方）：
    #     操作类型没识别出来时，错向的幂等/状态机用例会成片假 FAIL，这个预警此前被静默丢弃。
    _parse_warnings = []
    _self_check = []
    _self_check_seen = set()
    for _src in (_contract, _annotated, applicability):
        if isinstance(_src, dict):
            _parse_warnings += [str(w) for w in (_src.get("parse_warnings") or [])]
            for _sc in (_src.get("self_check") or []):
                if not isinstance(_sc, dict):
                    continue
                _key = (_sc.get("check"), _sc.get("message"))
                if _key in _self_check_seen:
                    continue
                _self_check_seen.add(_key)
                _self_check.append(_sc)
    if _parse_warnings:
        print("[contract] 契约解析告警 %d 条（字段可信度下降，请人工核对）："
              % len(_parse_warnings), file=sys.stderr)
        for _w in _parse_warnings[:6]:
            print("   - %s" % _w, file=sys.stderr)
    if _self_check:
        print("[contract][自检] %d 条可疑项（规则抽取结果存疑，不覆盖原值，请人工确认）："
              % len(_self_check), file=sys.stderr)
        for _sc in _self_check[:6]:
            print("   - [%s] %s" % (_sc.get("check", "?"), _sc.get("message", "")),
                  file=sys.stderr)
    if applicability.get("op_unconfirmed"):
        print("[applicability] 操作类型待确认：接口名未命中任何操作关键词，本次按「查询」"
              "保守出计划、E 维（终态/幂等）已降选测——错向的用例会成片假 FAIL，"
              "请人工确认真实操作类型后重判", file=sys.stderr)
    assets = [register("contract", contract_path, trust="high")]
    if ui_baseline:
        assets.append(register("ui_baseline", ui_baseline, trust="medium"))
    risk = score_risk(model, history=history or [])
    plan = build_plan(model, applicability=applicability, risk=risk, assets=assets,
                      dependencies=dep_result.get("dependencies"),
                      dependency_unknowns=dep_result.get("unknowns"))
    plan["contract"] = model.to_dict()
    plan["contract"]["source"] = contract_path
    # 两个死字段随产物透出（此前全仓 0 消费方，报告层无从点名；仅此还不够，
    # 上面已同步把关键告警打到 stderr，避免"解析降级"只存在于一次日志快照里）。
    plan["contractParseWarnings"] = _parse_warnings
    plan["contractSelfCheck"] = _self_check
    plan["applicabilityNotes"] = list(applicability.get("notes") or [])
    # 用 asdict 而非 __dict__：关系一旦带上 evidence（Evidence 对象）就不是
    # JSON 可序列化的了。历史实现因关系证据恒为空而从未暴露此问题。
    plan["relations"] = [asdict(r) for r in model.relations]
    plan["assets"] = [a.to_dict() for a in assets]
    if doc_review is not None:
        plan["docReview"] = doc_review
    if semantic_mode is not None:
        plan["semanticMode"] = semantic_mode
    os.makedirs(out_dir, exist_ok=True)
    plan_path = os.path.join(out_dir, "intelligent_test_plan.json")
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    dep_path = os.path.join(out_dir, "dependencies.json")
    with open(dep_path, "w", encoding="utf-8") as f:
        json.dump({"self_action": dep_result.get("self_action") or model.operation,
                   "contract": contract_path,
                   "dependencies": plan["dependencies"],
                   "unknowns": plan["dependency_unknowns"]},
                  f, ensure_ascii=False, indent=2)
    return {"model": model, "applicability": applicability, "risk": risk,
            "plan": plan, "plan_path": plan_path, "dependency_path": dep_path,
            "dependencies": plan["dependencies"]}


def _load_cases_doc(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def execute_cases_file(cases_path, out_dir, only=None, dry_run=False, out_prefix="api_test", baseline=None, config=None):
    """经统一入口执行已存用例，复用环境门禁、执行策略与完整收尾。

    这是**供外部脚本编程调用**的入口（仓库内部无调用者，改它等于改对外契约）；
    命令行用户走 `flow.py --cases`。
    """
    argv = ["--cases", str(cases_path), "--out-dir", str(out_dir),
            "--execute", "--out-prefix", out_prefix]
    for flag, value in (("--only", only), ("--retest", baseline), ("--config", config)):
        if value:
            argv.extend([flag, str(value)])
    if dry_run:
        argv.append("--dry-run")
    code = main(argv)
    return {"returncode": code or 0, "doc": _load_cases_doc(cases_path),
            "results": list(R.results), "prefix": os.path.join(out_dir, out_prefix)}


_ACTIVE_MANIFEST = None


def _main(argv=None):
    global _ACTIVE_MANIFEST
    ap = argparse.ArgumentParser(description="wjx-api-test 一键流水线")
    ap.add_argument("--contract", help="契约文档路径（ShowDoc md / OpenAPI json）")
    ap.add_argument("--action", help="action ID；与 --kb 配合从知识库定位契约")
    ap.add_argument("--require-action", dest="require_action", action="store_true",
                    help="契约解析不出 action 时以 exit 2 阻断（默认只告警并随 parse_warnings 留痕；"
                         "无 action 契约的结构类测试依赖放行路径，故默认不阻断）")
    ap.add_argument("--prep-dir", dest="prep_dir", default=None,
                    help="语义 prep 提示词输出目录（默认：契约同目录 llm_semantic_prep/；"
                         "契约目录只读或不想污染工作区时用它指到别处）")
    ap.add_argument("--kb", help="知识库根目录；省略时回退 KB_PATH 或 skill 同级目录的常见位置")
    ap.add_argument("--out-dir", help="显式产物目录（非空时拒绝覆盖）；默认包外按项目/接口/版本/批次归档")
    ap.add_argument("--execute", action="store_true", help="真实执行（默认只生成用例）")
    ap.add_argument("--plan-only", action="store_true", help="只生成智能计划和用例，不进入执行阶段")
    ap.add_argument("--dry-run", action="store_true", help="列出执行计划但不发起请求")
    ap.add_argument("--profile", choices=("plan", "smoke", "full", "exploratory", "discovery"), default=None,
                    help="执行档位；省略时取 --config 的 execution.profile → WJX_PROFILE，再回退 full。"
                         "discovery=发现档（生成期放宽探测广度：超长上限/载荷条数/字段族配额/AF 条数）")
    ap.add_argument("--config", help="可选执行配置 JSON")
    ap.add_argument("--adapter", choices=("rest", "sojump", "graphql", "grpc"), default=None,
                    help="适配器类型；sojump 为站点适配器（未随本包分发，自装后可用）；省略时取 --config 的 adapter，再回退 rest")
    ap.add_argument("--only", help="只跑指定维度，逗号分隔")
    ap.add_argument("--exclude-dims", dest="exclude_dims",
                    help="**显式关闭**指定维度（逗号分隔，如 B,G）：默认所有适用维度都参与生成，"
                         "只有这里点名才不参与；排除会出覆盖率分母、出用例集合并在 N/A 证明里留痕")
    ap.add_argument("--af-cases", dest="af_cases",
                    help="真实业务场景用例（business_scenarios --save 产物，含 cases 数组）："
                         "AF 只有拿到它才参与生成；不给则 AF 注明「没有真实业务场景」不参与，"
                         "不会用机械拼的合法请求冒充业务场景")
    ap.add_argument("--out-prefix", default="api_test", help="产物文件名前缀")
    ap.add_argument("--llm-facts", help="可选：大模型语义层事实 JSON（llm_semantics --mode prep 产出、"
                                        "经 --mode merge 校验后的那份）。不传则纯规则链路，行为不变")
    ap.add_argument("--no-llm-facts", dest="no_llm_facts", action="store_true",
                    help="唯一显式关闭入口：不用智能体读文档、不比对、不出提纲，纯机械抽取生成用例。"
                         "不加此开关时一律「先比对、后生成」，无人值守场景也不自动降级")
    ap.add_argument("--llm-semantics", dest="llm_semantics", action="store_true",
                    help="显式重申严格语义模式（已是默认，加上效果相同）：没有可认领的事实时"
                         "只出提纲并停下（exit 4），交完作业再跑同一条命令才生成用例。")
    ap.add_argument("--skip-doc-review", dest="skip_doc_review", action="store_true",
                    help="显式跳过文档审查前置门禁（留痕于计划 docReview 字段，报告会点名）。"
                         "硬性步骤：行为测试前应先完成文档合理性审查（不发请求）")
    ap.add_argument("--doc-review", dest="doc_review", default=None,
                    help="文档审查产物路径（api_test_doc_issues.csv 或 "
                         "api_test_doc_review_report.md）；缺省按 契约同目录 → 当前目录 约定查找")
    ap.add_argument("--runtime-deps", default="runtime_dependencies.json",
                    help="运行实证依赖记录（precondition_scan 产出）。只采信 status=confirmed "
                         "的记录做前置判定；文件不存在则不做任何前置判定，行为与不传时一致")
    ap.add_argument("--retest", help="对照回归基线做复测判定（传基线 JSON 路径）")
    ap.add_argument("--exclude-params", dest="exclude_params", action="append",
                    metavar="NAME[=VALUE]",
                    help="排除参数：不围绕它生成任何用例（可重复传，透传给 case_generator）。"
                         "带值时该值进每条请求；不带值依次取请求示例值→契约默认值→"
                         "（缺省信封参数）引擎注入；无值可用且非信封参数时报错拒绝生成")
    ap.add_argument("--exclude-config", dest="exclude_config", default=None,
                    metavar="JSON_PATH",
                    help="排除参数配置文件（{\"exclude_params\": [条目,...]}）。四级回退："
                         "本参数 > exclude_params_<project>.json > exclude_params.json > "
                         "exclude_params.default.json（出厂兜底）；第一个存在的文件即基底"
                         "清单（替换语义），CLI 只追加")
    ap.add_argument("--envelope-keys", dest="envelope_keys", default=None,
                    metavar="K1,K2",
                    help="显式指定信封注入键（逗号分隔），取代自动判定（有效排除集 ∩ "
                         "契约声明面）；必须 ⊆ 有效排除集否则报错")
    ap.add_argument("--no-inject", dest="no_inject", action="store_true",
                    help="本轮不注入任何公共参数（envelope_keys=[]）；与 --envelope-keys 互斥")
    ap.add_argument("--payload-hook", dest="payload_hook", default=None,
                    metavar="PY_PATH",
                    help="动态签名钩子：模块须暴露 compute_envelope(action, business_params,"
                         " personal) -> {信封键: 值}；产出键必须 ⊆ 本轮信封注入键。未配置 = "
                         "静态注入（personal_config 的固定值）")
    ap.add_argument("--cases", help="直接执行已存 cases JSON；不重新生成")
    ap.add_argument("--out", help="兼容 --out，等同显式输出目录；目录非空时拒绝覆盖")
    ap.add_argument("--project", help="资产项目名（默认 WJX_PROJECT_NAME 或当前目录名）")
    ap.add_argument("--version", help="资产版本目录（可选）")
    ap.add_argument("--approve-cases", help="显式批准正式 cases JSON")
    ap.add_argument("--suite-out", help="批准后正式用例的独立保存路径")
    ap.add_argument("--approve-by", help="批准人标识；批准命令必填")
    ap.add_argument("--promote-baseline", nargs=2, metavar=("SNAPSHOT", "TARGET"), help="将未批准 snapshot 晋升为正式基线")
    args = ap.parse_args(argv)

    if args.approve_cases:
        if not args.approve_by:
            ap.error("--approve-cases 必须同时提供 --approve-by")
        target = asset_runtime.approve_cases(args.approve_cases, args.approve_by, args.suite_out, project=args.project)
        print("正式 cases 已批准：%s" % target)
        return
    if args.promote_baseline:
        if not args.approve_by:
            ap.error("--promote-baseline 必须同时提供 --approve-by")
        asset_runtime.promote_baseline(args.promote_baseline[0], args.promote_baseline[1], args.approve_by)
        print("正式基线已批准：%s" % args.promote_baseline[1])
        return

    if args.cases and (args.contract or args.action):
        ap.error("--cases 与 --contract/--action 互斥，保存用例执行不得重新生成")
    if args.envelope_keys and args.no_inject:
        ap.error("--envelope-keys 与 --no-inject 互斥")
    if args.cases and (args.exclude_params or args.exclude_config
                       or args.envelope_keys or args.no_inject):
        # --cases 不重新生成，排除/注入判定已在生成期生效并固化进用例文件；此处只能提醒
        print("⚠️ --exclude-params/--exclude-config/--envelope-keys/--no-inject 与 --cases "
              "同时给出时被忽略：已存用例的排除与注入判定在生成期生效，如需调整请重新生成",
              file=sys.stderr)
    if args.out and args.out_dir:
        ap.error("--out 与 --out-dir 只能指定一个")
    if Path(args.out_prefix).name != args.out_prefix or args.out_prefix in {"", ".", ".."}:
        ap.error("--out-prefix 必须是单个安全文件名前缀")

    exec_cfg = load_exec_config(args.config)
    # 档位必须在**生成之前**解析并激活：限额（超长上限/载荷条数/字段族配额）是
    # 生成期读取的，晚于生成再设置就等于没设。优先级：命令行 > 执行配置 > 环境变量 > full。
    profile = (args.profile or (exec_cfg.get("execution") or {}).get("profile")
               or discovery_profile.env_profile() or "full")
    discovery_profile.activate(profile if profile in ("conservative", "discovery") else "")
    if discovery_profile.is_discovery():
        print("发现档（discovery）：探测广度已放宽 → %s；断言口径与门禁不变（增量接入）。"
              % {k: discovery_profile.get(k) for k in (
                  "big_cap", "payload_per_field_per_category", "null_state_fields",
                  "url_fields", "ui_proxy_fields", "af_max_cases", "overlong_probe")})
    inputs = resolve_inputs(exec_cfg, args.contract)

    contract_path = None if args.cases else (args.contract or inputs["contract"])
    if not contract_path and not args.cases and args.action:
        contract_path = resolve_contract_from_kb(args.action, args.kb)

    # 【默认严格语义】不加任何开关就是「先比对、后生成」：没有可认领的事实时只出提纲
    # 并停下（exit 4）。**不存在无人值守自动降级**：脚本没有等人输入的能力，定时任务/
    # 批量跑必须由外部先放好事实文件，否则整批停在 exit 4，绝不偷偷用机械结果冒充。
    # 唯一关闭方式是显式 --no-llm-facts（纯机械：不出提纲、也不用事实）。
    strict_sem = not args.no_llm_facts
    if args.llm_semantics and args.no_llm_facts:
        print("⚠️ --llm-semantics 与 --no-llm-facts 同时给出：按显式关闭处理（纯机械）",
              file=sys.stderr)
    # 事实来源：显式 --llm-facts > WJX_LLM_FACTS > 严格模式的约定位置。
    # 环境变量必须算进来：case_generator 本来就认它，strict 若无视会让「已交作业」
    # 的运行被误判成「没交」，白白停在第一步。
    facts_path = ""
    if not args.no_llm_facts:
        facts_path = args.llm_facts or os.environ.get("WJX_LLM_FACTS") or ""
        if strict_sem and not facts_path and contract_path and os.path.isfile(contract_path):
            facts_path = strict_facts_path(contract_path, args.action, args.prep_dir)
        if strict_sem and facts_path:
            # 严格模式承诺「先比对、后生成」：路径读了不算数，文件在且能解析才算。
            # 形状是否合法仍由 case_generator 的形状守卫（单一来源）判定，此处不重复。
            if not os.path.isfile(facts_path):
                ap.error("严格语义模式：事实文件不存在：%s\n"
                         "请交作业到约定位置，或去掉 --llm-facts 重新出提纲" % facts_path)
            try:
                with open(facts_path, "r", encoding="utf-8-sig") as _fh:
                    _shape_err = case_generator.facts_shape_error(json.load(_fh))
            except (OSError, ValueError) as _exc:
                ap.error("严格语义模式：事实文件不是可解析的 JSON：%s（%s）"
                         % (facts_path, _exc))
            if _shape_err:
                ap.error("严格语义模式：%s\n请交一份合格的事实文件再重跑" % _shape_err)
    waiting_for_facts = bool(strict_sem and not facts_path and not args.cases)

    # 配置防呆前移到**建目录之前**：缺 base_url/appid 这类配置根本跑不了任何用例，
    # 先跑这一步，失败时不产生任何目录。只在「会真实发请求」时做——计划档与
    # --dry-run 不发请求，空配置本来就该能出计划审阅；严格模式「停下等作业」这一步
    # 同样不发请求，不能让缺配置在拿到提纲之前就 exit 2 把人拦住。
    # 幂等：真实执行分支那次调用靠 _CONFIG_VERIFIED 短路。
    if (args.execute and not args.plan_only and profile != "plan"
            and not args.dry_run and not waiting_for_facts):
        case_runner.ensure_runnable_config()

    facts_consumed = bool(facts_path)
    semantic_info = {"factsConsumed": facts_consumed,
                     "strict": strict_sem, "disputes": 0,
                     "disabled": bool(args.no_llm_facts), "prepPrompt": None}
    if args.cases and strict_sem:
        print("⚠️ --cases 是执行已审套件、不重新生成用例，本轮不做语义抽取与比对；"
              "要重做抽取请去掉 --cases 走契约生成，明确不要智能体则加 --no-llm-facts",
              file=sys.stderr)
    if args.cases and args.exclude_dims:
        print("⚠️ --exclude-dims 只作用于生成阶段；--cases 不重新生成，本轮已忽略该开关"
              "（要排除维度请走契约生成并重新批准套件）", file=sys.stderr)
    if args.cases and args.af_cases:
        print("⚠️ --af-cases 只作用于生成阶段；--cases 直接执行已存套件，本轮已忽略"
              "（要合并业务场景请对已存套件跑 business_scenarios --merge）", file=sys.stderr)
    if args.af_cases and not os.path.isfile(args.af_cases):
        ap.error("--af-cases 指的文件不存在：%s（要跳过业务场景就不要传，AF 会注明"
                 "「没有真实业务场景」不参与）" % args.af_cases)
    if args.cases:
        doc = _load_cases_doc(args.cases)
    else:
        if not contract_path or not os.path.isfile(contract_path):
            ap.error("必须提供存在的契约路径、可定位的 action，或 --cases 用例文件")
        # 文档审查前置门禁（硬性步骤）：在用例设计之前完成/留痕
        doc_review_info = enforce_doc_review_gate(
            contract_path, skip=args.skip_doc_review, explicit=args.doc_review)
        if waiting_for_facts:
            # 脚本没有「等人输入」的能力（全仓无 input/stdin），所以「等」只能落成
            # 「停下 + 重跑」：这一次只出提纲，交完作业再跑同一条命令才生成用例。
            _prompt = run_semantic_prep(contract_path, action=args.action,
                                        prep_dir=args.prep_dir)
            _shards = [p for p in Path(_semantic_dir(contract_path, args.prep_dir)).glob("*.md")
                       if "shard" in p.name]
            print("=" * 70)
            print("已停下：本轮不生成用例（默认要先让智能体读文档、并把两边理解比对完）。")
            print("1) 提纲（交给智能体读的那份）：%s" % (_prompt or "未写出，见上方告警"))
            print("2) 交作业：把事实 JSON 写到 %s"
                  % strict_facts_target(contract_path, args.action, args.prep_dir))
            if _shards:
                print("   提纲分成了 %d 片：每片各交一份后，先用 --mode combine 合并到上面这个文件"
                      % len(_shards))
            print("3) 交完再跑**一模一样的这条命令**，才会「先比对、后生成」。")
            print("4) 明确不要这层理解，才加 --no-llm-facts（纯机械）。")
            print("=" * 70)
            if not _prompt:
                sys.stderr.write("[semantic] 提纲没写出来，不能让人对着空气交作业；"
                                 "本轮不生成用例\n")
                return 2
            return 4
        # 生成前的比对预检：把两边不一致的字段连**契约原文相关整段**一起挑出来。
        # 有争议就停下出单，等会话智能体看完片段再判一次；复核结论交回后才生成。
        # 纯机械（--no-llm-facts）与执行已审套件（--cases）不走这条。
        revised_facts_path = facts_path
        if strict_sem and facts_path and contract_path:
            try:
                import llm_semantics as LS
            except ImportError as _exc:
                ap.error("语义层模块不可用，本轮不生成用例：%s" % _exc)
            _disputes, _raw_facts, _pre_err = semantic_precheck(
                contract_path, facts_path, args.action)
            if _pre_err:
                ap.error("语义比对预检失败（本轮不生成用例）：%s" % _pre_err)
            semantic_info["disputes"] = len(_disputes)
            if _disputes:
                _review_p = strict_review_file(contract_path, args.action, args.prep_dir)
                # 指纹必须同时锁住事实文件与契约原文：只锁事实时，改了契约而作业没动，
                # 旧复核结论会被直接套用，争议单里贴的还是旧原文（实测 exit 0 出用例）。
                _sha = LS.dispute_fingerprint(facts_path, contract_path)
                if not os.path.isfile(_review_p):
                    write_dispute_review(contract_path, facts_path, args.action,
                                         args.prep_dir, _disputes, _sha)
                    return 5
                try:
                    with open(_review_p, "r", encoding="utf-8-sig") as _fh:
                        _review = json.load(_fh)
                except (OSError, ValueError) as _exc:
                    ap.error("复核结论不是可解析的 JSON：%s（%s）" % (_review_p, _exc))
                if not isinstance(_review, dict) or _review.get("facts_sha256") != _sha:
                    # 作业改过 / 换了契约：旧复核结论不能套用，必须重做。
                    _md, _rt, _ = write_dispute_review(
                        contract_path, facts_path, args.action, args.prep_dir,
                        _disputes, _sha)
                    print("事实或契约已变化，原复核结论失效；请按新争议单重做判断并覆盖 %s" % _rt)
                    return 5
                _revised, _applied, _rejected = LS.apply_review(
                    _review, _disputes, _raw_facts, expected_sha=_sha)
                if _rejected:
                    sys.stderr.write("[semantic] 复核结论拒收 %d 条：%s\n"
                                     % (len(_rejected), "；".join(_rejected)))
                _applied_names = {a.split("：", 1)[0] for a in _applied}
                _dispute_names = {d["field"] for d in _disputes}
                if _rejected or _applied_names != _dispute_names:
                    # 复核不完整就**重出争议单**：每条不一致都要由智能体看着原文再判
                    # 一次，不能让机器把没确认的争议单方面丢掉后照样生成用例。
                    # 重出单同时解决「review 文件还在、争议集合却变了」时争议单过期。
                    _md, _rt, _ = write_dispute_review(
                        contract_path, facts_path, args.action, args.prep_dir,
                        _disputes, _sha)
                    print("复核结论不完整（争议 %d 条，有效采纳 %d 条，拒收 %d 条），"
                          "本轮不生成用例。"
                          % (len(_disputes), len(_applied), len(_rejected)))
                    print("请按新争议单逐条重做判断并覆盖：%s" % _rt)
                    return 5
                sys.stderr.write("[semantic] 复核采纳 %d 条，修订后的事实会再跑一遍机械校验\n"
                                 % len(_applied))
                _dispute_md, _revised_target = strict_review_names(
                    contract_path, args.action, args.prep_dir)
                with open(_revised_target, "w", encoding="utf-8") as _fh:
                    json.dump(_revised, _fh, ensure_ascii=False, indent=2)
                revised_facts_path = _revised_target
        try:
            project = common_params.safe_project_name(
                args.project or os.environ.get("WJX_PROJECT_NAME")) \
                if (args.project or os.environ.get("WJX_PROJECT_NAME")) else None
            merged_excludes, excl_src = common_params.resolve_exclude_entries(
                cli_entries=args.exclude_params,
                config_path=args.exclude_config,
                assets_dir=os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets"),
                project=project)
            dim_excluded = [d.strip().upper() for d in
                            (args.exclude_dims or "").split(",") if d.strip()]
            if dim_excluded:
                sys.stderr.write("[applicability] 本轮显式关闭维度：%s"
                                 "（已出覆盖率分母与用例集合，N/A 证明留痕）\n"
                                 % "、".join(dim_excluded))
            envelope_override = "no_inject" if args.no_inject else (
                [k.strip() for k in args.envelope_keys.split(",") if k.strip()]
                if args.envelope_keys else None)
            doc = case_generator.generate(contract_path, args.action, llm_facts=revised_facts_path,
                                          llm_enabled=not args.no_llm_facts,
                                          exclude_entries=merged_excludes,
                                          exclude_note=excl_src,
                                          envelope_override=envelope_override,
                                          exclude_dims=dim_excluded,
                                          af_cases=args.af_cases)
        except ValueError as _exc:  # --exclude-params/--exclude-config 解析失败：点名拒绝
            ap.error(str(_exc))
    # 【审查修复·P0】action 解析失败曾静默放行：标题/文件名/正文三处规则都取不到时，
    # 整轮用例 action 为空、产物外观完整、事后极难归因（实测一份含 "action": "1001002"
    # 的契约因正则不认 JSON 写法而解析为空，165 条用例全部无效）。这里强制出声并随
    # parse_warnings 留痕；--require-action 升级为阻断（CI 门禁用）——默认不阻断，
    # 因为无 action 契约的结构类测试依赖放行路径。
    if contract_path and not args.cases \
            and not str(doc.get("action") or "").strip() and not str(args.action or "").strip():
        print("[contract][重要] 契约解析不出 action（标题/文件名/正文三处规则都未命中）："
              "没有 action 的用例整轮无法定位接口，多半会成片失败。"
              "确认契约声明了 action（如 \"action\": \"1001002\" 或标题含 [1001002]），"
              "或用 --action 显式指定；要升级为阻断加 --require-action。",
              file=sys.stderr)
        doc.setdefault("parse_warnings", []).append(
            "action 解析为空：标题/文件名/正文三处规则均未命中；本轮用例不含 action，"
            "请人工确认契约是否声明 action，或用 --action 显式指定")
        if args.require_action:
            sys.exit(2)
    asset_runtime.validate_cases_doc(doc)
    # 默认严格语义下「生成成功但没消费事实」这条路径已不可达：没事实就在上面的
    # waiting_for_facts 分支停下（exit 4），有事实则 facts_consumed=True。原先挂在
    # 生成之后的那次 prep（先出用例、提纲随产物落盘）随之删除——它正是「以为用上了
    # 其实没有」的根源，也和「不许无人值守自动降级」冲突。保留提示词产出只在停下那一步。
    explicit_dir = args.out_dir or args.out
    baseline_cache = None
    if args.retest:
        import regression as REG
        baseline_file = Path(args.retest).resolve()
        if explicit_dir and baseline_file.is_relative_to(Path(explicit_dir).resolve()):
            ap.error("复测基线不得位于本轮输出目录内")
        if not baseline_file.is_file():
            ap.error("复测基线文件不存在：%s" % baseline_file)
        baseline_cache = REG.load_baseline(baseline_file)
        if not baseline_cache:
            ap.error("复测基线为空，禁止假复测")
    try:
        args.out_dir, _explicit = asset_runtime.prepare_run_dir(
            project=args.project, interface=asset_runtime.stable_interface_id(doc),
            version=args.version, out_dir=explicit_dir)
    except OSError as exc:
        # 输出目录不可写（权限 / 只读盘 / 被占用）时给出可读结论与退出码，
        # 而不是把 PermissionError 的 traceback 抛给用户。
        ap.error("输出目录不可用（%s）：%s" % (explicit_dir or "默认资产根", exc))
    manifest_path = asset_runtime.create_manifest(
        args.out_dir, asset_runtime.stable_interface_id(doc), _contract_hash_of(doc),
        cases_doc=doc, environment=exec_cfg.get("environment"),
        inputs={"contract": contract_path, "source_cases": args.cases,
                "cases": str(Path(args.out_dir) / "generated_cases.json"),
                "baseline": args.retest, "profile": profile})
    _ACTIVE_MANIFEST = manifest_path
    reset_runtime()
    if inputs["ui_baseline"]:
        os.environ["WJX_UI_BASELINE"] = str(inputs["ui_baseline"])
    intelligence = None
    if not args.cases:
        intelligence = build_intelligence_artifacts(
            contract_path, args.out_dir, action=args.action,
            history=inputs["history"], ui_baseline=inputs["ui_baseline"],
            doc_review=doc_review_info, semantic_mode=semantic_info)
        print("智能计划：%s | 风险=%s(%s)" % (
            intelligence["plan_path"], intelligence["risk"]["score"], intelligence["risk"]["level"]))

    # AF 的参与条件是「有真实业务场景」：有 --af-cases 才进计划，否则在
    # na_proofs 写明缺什么。这里只做「计划里有 AF 就必须真有用例」的对账——
    # 占位化或全部跳过时要出声，否则覆盖率会显示已覆盖而实际一条没跑。
    if "AF" in (doc.get("coverage_plan") or []):
        af_live = [c for c in (doc.get("cases") or [])
                   if str(c.get("dimension") or "").startswith("AF") and c.get("enabled")]
        if not af_live:
            sys.stderr.write(
                "警告：AF 已因提供了业务场景而列入计划，但本轮没有任何可执行的 AF 用例"
                "（占位或全部跳过），覆盖率门禁会报 AF 缺口。\n"
                "  请检查 --af-cases 文件里的用例是否 enabled；要关 AF 就显式 "
                "--exclude-dims AF。\n")
        else:
            # 族内缺口随 --merge 带进主用例文件。AF 在覆盖率里是通配口径
            # （AF1 命中即算 AF 覆盖），不出声就会"有 AF 用例 ⇒ 显示已覆盖"。
            gaps = doc.get("af_family_gaps") or []
            if gaps:
                sys.stderr.write(
                    "提示：AF 用例缺族 %s。按族对账请传 --expect-categories AF1,AF2,AF3，"
                    "不适用族用 --exempt-categories 写明理由；只见 \"AF\" 时缺口不会体现在覆盖率上。\n"
                    % "、".join(gaps))

    cases_path = os.path.join(args.out_dir, "generated_cases.json")
    if os.path.exists(cases_path):
        raise SystemExit("拒绝覆盖已有用例文件：%s（请使用新的运行目录）" % cases_path)
    with open(cases_path, "x", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    asset_runtime.update_manifest(manifest_path, "planned", cases_hash=asset_runtime.cases_hash(doc))

    print("=" * 70)
    print("接口：%s（action=%s）  操作类型=%s  国际版=%s" %
          (doc.get("name"), doc.get("action"), doc.get("op_type"), doc.get("is_intl")))
    print("生成用例 %d 条 -> %s" % (len(doc.get("cases") or []), cases_path))
    print("COVERAGE_PLAN（门禁必测）: %s" % ", ".join(doc.get("coverage_plan") or []))
    print("建议覆盖（选测）: %s" % ", ".join(doc.get("optional_plan") or []))
    print("命中缺陷模式 %d 条: %s" % (len(doc.get("matched_patterns") or []),
                                 ", ".join("%s(%d)" % (m["id"], m["cases"]) for m in doc.get("matched_patterns") or [])))
    print("=" * 70)

    # 人可读计划：执行前先让人看一眼，避免跑完才发现场景设计不对。
    # 在 plan 档位同样产出，这正是它最有用的场合。
    plan_md_path = os.path.join(args.out_dir, "plan.md")
    plan_md = (plan_render.render_plan_md(
        intelligence["plan"], doc=doc, contract_path=contract_path,
        generated_at=time.strftime("%Y-%m-%d %H:%M:%S")) if intelligence else
        "# 已保存用例执行计划\n\n保留原用例，不重新生成。\n\n用例数：%d\n" % len(doc["cases"]))
    with open(plan_md_path, "w", encoding="utf-8") as f:
        f.write(plan_md)
    pending = plan_render.collect_pending(intelligence["plan"], doc) if intelligence else []
    asset_runtime.update_manifest(manifest_path, "planned", artifacts=[
        str(p) for p in Path(args.out_dir).rglob("*") if p.is_file()])
    print("人可读计划 -> %s（待确认问题 %d 项）" % (plan_md_path, len(pending)))

    # profile 已在生成之前解析并激活（见上），此处直接复用，不再重复解析。
    # 默认 rest（剥离改造）：随包适配器均无 is_write_allowed，写保护由 R2 授权与
    # environment 配置承担；用户自装站点适配器（如 sojump）后其门禁自动生效。
    adapter_name = args.adapter or exec_cfg.get("adapter") or "rest"
    if args.plan_only or profile == "plan" or not args.execute:
        print("\n未执行（计划模式，profile=%s）。用例已生成，可直接审阅或人工裁剪后再跑。"
              % profile)
        return

    if manifest_path:
        asset_runtime.update_manifest(manifest_path, "running")
    adapter = build_adapter(adapter_name)
    op_type = doc.get("op_type")
    if args.cases and adapter_name == "sojump" and op_type not in {"查询", *WRITE_OPS}:
        # 保存用例可能缺少生成器元信息；未知操作不能自动当只读而绕过写门禁。
        op_type = "修改"
    allowed, why = write_gate(adapter, adapter_name, exec_cfg, op_type)
    if not allowed and not args.dry_run:
        sys.stderr.write("\n" + why + "\n")
        asset_runtime.update_manifest(manifest_path, "failed", failure_reason=why)
        return 2

    # 【审查修复·adapter 真正接线】此前 adapter 只喂给写门禁，从不参与请求与响应
    # 判定——而 REST 之外协议的判定差异恰恰在响应分类上（GraphQL 以 HTTP 200 +
    # body.errors 表达失败）。装配做成一次注入而非十余个调用点逐个透传，正是为了
    # 避免「漏传一处就退化成死代码」。REST/sojump 不注入，默认路径行为不变。
    if adapter_name in ("graphql", "grpc"):
        R.CALL_ADAPTER = adapter
        print("已装配 %s 响应分类器：非 ok 状态将归一为 result=false，"
              "避免 HTTP 200 携带 errors 时的假通过。" % adapter_name)

    # 【W34】真实执行前统一配置校验（与 case_runner.exec_case 内的钩子同源、幂等）。
    # 校验只写在 api_test_runner.main() 时推荐入口会绕过它，空配置跑完全程出空报告。
    if not args.dry_run:
        case_runner.ensure_runnable_config()

    suite_cases = doc.get("cases") or []
    declared_cases = list(suite_cases)
    if args.only:
        wanted = {d.strip() for d in args.only.split(",") if d.strip()}
        # 【审查修复】族匹配而非等值匹配：业务场景用例的维度是 AF1/AF2/AF3，
        # 等值比对下 ``--only AF`` 会选出 0 条——后续被当成「筛选后没有用例」
        # 判 failed，真实原因（维度族未按预期认领）被掩盖；多接口批次里更会在
        # 预检阶段就抛「没有任何匹配维度」，整批零请求。统一走 case_coverage
        # 的维度族口径（数字后缀是同一族的细分）。
        declared_cases = [c for c in declared_cases
                          if any(case_coverage.dim_matches(c.get("dimension"),
                                                           c.get("category"), w)
                                 for w in wanted)]
    # 禁用用例（人工占位 TODO-*/enabled=False/needs=manual）本就不参与执行，属
    # **范围外因素**。此前它挂在档位规则里，于是任何含占位的套件（生成器默认就
    # 产一批）都满足 len(profile_cases) < len(declared_cases) → truncated=True
    # → 即便全部通过也只能标 partial，completed 不可达；而 --promote-baseline
    # 硬性要求源 manifest 为 completed，等于未经批准的套件永远无法晋升基线。
    # 单独统计为 disabled_dropped，只让档位收窄与 max_cases 截断进截断判定。
    exec_declared = [c for c in declared_cases if bool(c.get("enabled", True))]
    disabled_dropped = len(declared_cases) - len(exec_declared)
    profile_cases = select_by_profile(exec_declared, profile)
    cases = profile_cases
    scoped_count = len(cases)
    policy = apply_exec_policy(exec_cfg)
    cases = cap_cases(cases, policy)
    cases = order_cases_for_exec(cases)
    profile_limited = len(profile_cases) < len(exec_declared)
    cap_dropped = scoped_count - len(cases)
    selection = {
        "profile": profile, "only": args.only,
        "requested": len(suite_cases), "declared": len(declared_cases),
        "scoped": scoped_count,
        "selected": len(cases), "cap_dropped": cap_dropped,
        "disabled_dropped": disabled_dropped,
        "profile_limited": profile_limited,
        "truncated": bool(cap_dropped > 0 or profile_limited),
        "cases_hash": asset_runtime.cases_hash({"cases": cases}),
    }
    # 显式 --only 是用户声明范围：分母收窄到所选维度，其余维度必须留痕而不是当作已覆盖。
    coverage_plan = list(doc.get("coverage_plan") or [])
    coverage_excluded = []
    if args.only and coverage_plan:
        wanted_dims = {d.strip() for d in args.only.split(",") if d.strip()}
        coverage_excluded = [d for d in coverage_plan if d not in wanted_dims]
        coverage_plan = [d for d in coverage_plan if d in wanted_dims]
        selection["coverage_excluded"] = coverage_excluded
    snapshot_doc = dict(doc, cases=cases)
    snapshot_doc.pop("approval", None)
    snapshot_doc["source_approval"] = doc.get("approval")
    snapshot_doc["source_cases_hash"] = asset_runtime.cases_hash(doc)
    asset_runtime.write_json(Path(args.out_dir) / "executed_cases.json", snapshot_doc)
    current = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    inputs = dict(current.get("inputs") or {})
    inputs.update({"cases_hash": asset_runtime.cases_hash(doc),
                   "selection_cases_hash": selection["cases_hash"],
                   "profile": profile, "only": args.only,
                   "coverage_excluded": coverage_excluded})
    asset_runtime.update_manifest(manifest_path, "running", selection=selection, inputs=inputs)
    if not cases:
        asset_runtime.update_manifest(manifest_path, "failed", failure_reason="筛选后没有用例",
                                      selection=selection)
        return 2
    R.EVIDENCE_DIR = str(Path(args.out_dir) / "evidence")
    R.RUN_ID = Path(args.out_dir).name
    R.CLEANUP_MANIFEST_PATH = str(Path(args.out_dir) / "cleanup_manifest.jsonl")
    R.COVERAGE_PLAN = coverage_plan
    R.CHECK_KEY_MISSING = doc.get("check_key") or {}
    # 本轮排除的参数（--exclude-params）：随报告透出，避免"用例比契约字段少"看着像漏测
    R.EXCLUDED_PARAMS = doc.get("excluded_params") or []
    # 信封注入键集 + 动态签名钩子（全链路联动）：doc 缺 envelope_keys（旧文件）→
    # None → build_payload 走改造前行为。
    R.ENVELOPE_KEYS = doc.get("envelope_keys")
    # 接口地址来自契约（doc 的 endpoint）：base_url 只配域名/根，路径由契约给。
    R.ENDPOINT = doc.get("endpoint") or ""
    # 请求形态（method / 请求体格式 / 参数位置）：缺键即回落改造前行为。
    R.apply_request_profile(doc)
    R.CONFIG_HEADERS, R._HEADER_SOURCES = R.load_request_headers()
    print("[地址] %s | %s %s" % (R.explain_request_url(), R.REQUEST_METHOD,
                                 R.REQUEST_BODY_KIND), file=sys.stderr)
    if R.PARAM_LOCATIONS:
        print("[位置] %s" % "、".join("%s=%s" % (k, v) for k, v in R.PARAM_LOCATIONS.items()),
              file=sys.stderr)
    if R.CONFIG_HEADERS:
        print("[请求头] %s（值不入产物）" % "、".join(sorted(R.CONFIG_HEADERS)),
              file=sys.stderr)
    # 出声：判定结果必须能被人看见。为空时最需要点名——「本轮不注入公共参数」与
    # 「契约示例没解析出来所以判不到」在产物上一样，后者会让整轮请求缺参被拒。
    if R.ENVELOPE_KEYS is not None:
        print("[信封] 注入键：%s" % ("、".join(R.ENVELOPE_KEYS) or
                                     "（无——本轮不注入公共参数，若接口需要 encode/appid/sign 请核对契约示例）"),
              file=sys.stderr)
    if args.payload_hook:
        try:
            R.PAYLOAD_HOOK = common_params.load_payload_hook(args.payload_hook)
        except ValueError as _exc:
            ap.error(str(_exc))
        print("[flow] 动态签名钩子已装载：%s" % args.payload_hook, file=sys.stderr)
    # 【前置判定】注入已人工确认的运行实证依赖。文件不存在或全为候选时列表为空，
    # 此时不做任何前置判定，行为与接入前完全一致（增量接入）。
    # 【前置判定】两条依赖通道在此合并：运行实证（须 status=confirmed）+ 契约声明
    # （kind=depends_on，契约白纸黑字写出、无需人工确认）。同一对主体/目标运行实证
    # 优先。此前只接了运行实证一条：契约写明「请先调用 1000102」的接口，执行期失败
    # 仍判 FAIL 进缺陷台账——「合并时以运行实证优先」这句话在代码里没有落点。
    _runtime_deps = load_confirmed_runtime_deps(args.runtime_deps)
    _contract_deps = contract_deps_from_dependency_file(
        (intelligence or {}).get("dependency_path"))
    _merged_deps, _overridden = merge_dependency_channels(_runtime_deps, _contract_deps)
    R.CONFIRMED_PRECONDITION_DEPS = _merged_deps
    if _merged_deps:
        _n_runtime = len(_runtime_deps)
        _n_contract = len(_merged_deps) - _n_runtime + _overridden
        print("前置判定依赖：运行实证 %d 条 + 契约声明 %d 条（运行实证优先，重叠 %d 条）"
              % (_n_runtime, _n_contract, _overridden))
        # 依赖契约就位检查：只对**运行实证**通道取回目标契约（契约声明通道的
        # 匹配只需要 target_action 号，不需要目标契约文件）。
        ensure_runtime_dep_contracts(_runtime_deps,
                                     args.out_dir, kb=args.kb, near=contract_path)
    interval = policy.get("request_interval_ms") or 0
    print("\n适配器=%s 档位=%s 开始执行 %d 条用例%s..." % (
        adapter_name, profile, len(cases),
        "（间隔 %dms）" % interval if interval else ""))
    try:
        for idx, c in enumerate(cases):
            if idx and interval:
                time.sleep(interval / 1000.0)  # 尊重服务端限流：用例间留出间隔，避免把限流误判成缺陷
            try:
                case_runner.exec_case(c, dry=args.dry_run, suite_op_type=doc.get("op_type"),
                                      suite_action=doc.get("action"))
            except Exception as exc:  # noqa: BLE001
                # 【审查修复·单条容错】旧实现不隔离单条用例：任何一条抛异常就跳出
                # 循环冒泡到最外层 except，write_reports / 门禁 / 回读 / 快照全部
                # 被跳过——已执行的 150 条结果一条都拿不到，产物目录里连 CSV 都没
                # 有，卡点无从定位。这里把异常转成一条 ERROR 结果后继续跑完。
                cid = str(c.get("id") or "?")
                sys.stderr.write(
                    "⚠️ 用例 %s 执行异常，已记为 ERROR 并继续：%s: %s\n"
                    % (cid, type(exc).__name__, exc))
                R.results.append({"case_no": cid, "name": str(c.get("name") or ""),
                                  "category": str(c.get("category") or ""),
                                  "dimension": str(c.get("dimension") or ""),
                                  "status": "ERROR",
                                  "reason": "执行器异常（%s: %s）" % (type(exc).__name__, exc)})
                # 记为 ERROR 结果即可让完成判定失败（clean 要求全 PASS），不必再
                # 往缺陷台账塞一条——执行器崩溃不是接口缺陷，按缺陷落账会污染
                # 缺陷计数，正是本 Skill 反复要防的「非缺陷进台账」。
                R.add_observation(
                    "[%s] 用例执行器异常（非接口返回错误）：%s: %s"
                    % (cid, type(exc).__name__, exc),
                    c.get("params") or {}, fid=cid, expected="用例正常执行完毕",
                    actual="执行器抛出 %s" % type(exc).__name__,
                    severity="中", dimension=str(c.get("dimension") or ""))
    finally:
        # 形态是模块级单例：不复位会让后续批次沿用本批的 method/位置。快照先取再复位。
        R.REQUEST_PROFILE = R.request_profile_snapshot()
        R.reset_request_profile()
    if args.dry_run:
        print("\n[dry-run] 未发起任何请求。")
        asset_runtime.update_manifest(manifest_path, "planned", dry_run=True)
        return 0

    print("\n" + "#" * 70)
    counts = {}
    for r in R.results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("结果统计：%s" % counts)
    R.coverage_gate()
    R.check_key_gate()
    case_runner.write_reports(os.path.join(args.out_dir, args.out_prefix))

    # 【运行实证依赖发现】执行报告是唯一能看到"真实失败文案"的地方，就地筛一遍：
    # 命中白名单话术的失败会被整理成提示词，供大模型抽事实、机械校验后成为**候选**依赖。
    # 这里只产候选：不写知识库、不改变任何判定、不影响本轮结论——确认是显式动作。
    report_path = os.path.join(args.out_dir, "%s_report_full.json" % args.out_prefix)
    if os.path.isfile(report_path):
        try:
            with open(report_path, "r", encoding="utf-8") as f:
                report = json.load(f)
        except Exception:
            report = {}
        cands = precondition_scan.harvest(report or {}, subject_name=doc.get("name") or "")
        if cands:
            prompt_path = os.path.join(args.out_dir, "precondition_prompt.md")
            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(precondition_scan.build_prompt(cands, artifact=report_path))
            print("\n疑似「前置条件未满足」的失败 %d 条（**不是**缺陷，待确认后才会生效）:" % len(cands))
            for c in cands:
                print("  [%s] %s | 命中「%s」| %s" % (
                    c["case_no"], c["case_name"][:34], c["hint"], c["response_excerpt"][:60]))
            print("抽取提示词 -> %s" % prompt_path)
            print("确认流程：大模型按提示词产事实 → precondition_scan --merge <facts.json> "
                  "→ 核对后 --confirm 转正")

    csv_path = os.path.join(args.out_dir, "%s_issues.csv" % args.out_prefix)
    if os.path.isfile(csv_path):
        report_enhance.enhance(csv_path, os.path.join(args.out_dir, "%s_issues_enhanced" % args.out_prefix))

    # 收尾清理也是真实请求：请求形态在上面 finally 里已复位，这里必须按同一份 doc
    # 重新装载，否则删除请求会退回 POST+JSON、并且丢掉认证头——删除静默失败，
    # 残留清单看着"已处理"，实际一条都没删掉（写接口跑多轮就是一堆垃圾数据）。
    R.apply_request_profile(doc)
    R.CONFIG_HEADERS, R._HEADER_SOURCES = R.load_request_headers()
    try:
        cleanup_step(exec_cfg, args.out_dir)
    finally:
        R.reset_request_profile()
    write_regression_artifacts(args.out_dir, doc)

    # 产物回读校验（交付前自检）。放在 report_enhance **之后**：增强会重写 CSV，
    # 放在它前面校验的是即将被替换的文件，等于校验了一个没人会拿到的产物。
    readback_errors = 0
    if not args.dry_run:
        if os.path.isfile(csv_path):
            readback_errors = verify_artifacts(args.out_dir, csv_path)
        else:
            # 台账没写出来 ≠ 台账完美。旧实现只在文件存在时才校验，缺文件时
            # readback_errors 保持 0，clean 判定里这一项直接通过——「报告没生成」
            # 与「报告无瑕疵」同分，最该拦的情况反而放行。
            readback_errors = 1
            print("[回读] ⚠️ 未找到台账 %s：报告未生成，交付前检查不通过" % csv_path,
                  file=sys.stderr)

    if args.retest:
        rows = []
        for r in R.results:
            _ok = r.get("status") == "PASS"
            # 【审查修复·P1-4】error 此前读 response.note（业务响应体没有该键，
            # 恒空串）——BLOCKED_ENV 永不命中、REGRESSED 的 err 比较恒空。
            # 复测错误语义在 reason 字段（网络异常文案、断言失败原因都在这），
            # 且仅失败用例才算错误（PASS 的 reason 是通过说明，非错误）。
            rows.append({"case_id": r.get("case_no"), "passed": _ok,
                         "error": "" if _ok else str(r.get("reason") or "")})
        import regression as REG
        verdicts = REG.retest_verdicts(baseline_cache, rows, current_contract_hash=_contract_hash_of(doc),
                                      current_cases=cases)
        vc = {}
        for v in verdicts:
            vc[v["verdict"]] = vc.get(v["verdict"], 0) + 1
        print("复测判定：%s" % vc)
        with open(os.path.join(args.out_dir, "retest_verdicts.json"), "w", encoding="utf-8") as f:
            json.dump(verdicts, f, ensure_ascii=False, indent=2)

    planned_ids = [c["id"] for c in cases]
    result_ids = [r.get("case_no") for r in R.results]
    complete_results = (len(result_ids) == len(planned_ids) and set(result_ids) == set(planned_ids))
    retest_ok = not args.retest or all(v.get("verdict") == "FIXED" for v in verdicts)
    # clean 只看**缺陷**：add_observation 也写 findings（kind=待确认），旧判据
    # `not R.findings` 让「有浅用例/一次无法验证」的正常轮次恒 exit 2——
    # 门禁常红等于没有门禁，与「待确认不算失败」的既有口径也矛盾。
    has_defect = any(str(f.get("type") or f.get("kind") or "") == "缺陷" for f in R.findings)
    clean = (bool(R.results) and complete_results and retest_ok
             and all(r.get("status") == "PASS" for r in R.results)
             and not has_defect and not getattr(R, "coverage_blocked", False)
             and not readback_errors)
    success = clean and not selection["truncated"]
    asset_runtime.finish_manifest(
        manifest_path, R.results,
        [str(p) for p in Path(args.out_dir).rglob("*") if p.is_file()],
        status="completed" if success else ("partial" if clean else "failed"),
        selection=dict(selection, readback_errors=readback_errors))
    asset_runtime.update_manifest(manifest_path, "failed" if not clean else ("partial" if not success else "completed"),
                                  readback_errors=readback_errors)
    return 0 if clean else 2


def main(argv=None):
    """统一生成/已存用例入口；异常留痕并恢复进程级运行参数。"""
    global _ACTIVE_MANIFEST
    _ACTIVE_MANIFEST = None
    runtime_names = ("EVIDENCE_DIR", "RUN_ID", "CLEANUP_MANIFEST_PATH", "RETRY_COUNT")
    saved = {name: getattr(R, name, None) for name in runtime_names}
    verified = case_runner._CONFIG_VERIFIED
    minimize = case_runner.MINIMIZE_ENABLED
    active_profile = discovery_profile._ACTIVE
    baseline_env = os.environ.get("WJX_UI_BASELINE")
    try:
        case_runner._CONFIG_VERIFIED = False
        return _main(argv)
    except BaseException as exc:
        if _ACTIVE_MANIFEST:
            asset_runtime.update_manifest(_ACTIVE_MANIFEST, "failed", failure_reason=type(exc).__name__)
        raise
    finally:
        for name, value in saved.items():
            setattr(R, name, value)
        case_runner._CONFIG_VERIFIED = verified
        case_runner.MINIMIZE_ENABLED = minimize
        discovery_profile._ACTIVE = active_profile
        if baseline_env is None:
            os.environ.pop("WJX_UI_BASELINE", None)
        else:
            os.environ["WJX_UI_BASELINE"] = baseline_env


if __name__ == "__main__":
    sys.exit(main() or 0)
