#!/usr/bin/env python3
"""运行时能力探测（D1）：纯探测，零副作用。

只回答「当次环境有哪些可用通道」：不安装依赖、不改配置、不发起业务请求、
不启动浏览器、不代判结论（exploration-runtime.md 164 行）。输出
`runtime-capabilities.json`（schema: runtime-capabilities/1），五通道三态
available / missing / unprobed，各附 reason 与 recovery。

降级链：接口/契约探针 → http_contract；页面交互探针 → browser_selfhost
→ browser_mcp → 均不可用则 BLOCKED 带 recovery。探测结果只进「能力边界」
呈现与批次编排（exploration_runner.py 队列过滤），不进页面证据——
不得以 curl/HTTP 状态码冒充页面渲染与交互验证（browser-connection.md 52 行）。

消费者（output-templates.md §0.4）：exploration_runner.py 队列过滤；
探索清单条目 blocked_reason / acknowledged_unexplored.next_action 的依据
（不得借此免压档）；报告「能力边界」段；交接包 runtime_diagnostics。

用法：
    python scripts/runtime_probe.py --runtime-manifest <manifest.json> --out <capabilities.json>
    python scripts/runtime_probe.py                # 缺省 manifest：只读环境变量与已知路径，各通道按 unprobed 处理
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "runtime-capabilities/1"
STATES = ("available", "missing", "unprobed")

# 五通道清单：探测入口名 → 输出键。顺序即呈现顺序。
CHANNELS = ("http_contract", "browser_selfhost", "browser_mcp",
            "auth_state", "design_data")

MANIFEST_REQUIRED_STRING_FIELDS = ("target_origin", "profile_dir", "chrome_path",
                                   "playwright_core_path")
MANIFEST_ALL_FIELDS = MANIFEST_REQUIRED_STRING_FIELDS + (
    "authorized_roles", "read_only_budget", "figma_export_path", "declared_at")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _entry(state, reason, recovery):
    return {"state": state, "reason": reason, "recovery": recovery}


def validate_manifest(payload):
    """manifest 结构校验（schema: runtime-manifest/1）。

    manifest 是环境声明，不是放行凭据，不写进 approval（v1.49.0 方案 D1）。
    只声明**已存在**资源；命中副作用或超预算仍当场必问（agents 必问矩阵）。
    """
    if not isinstance(payload, dict):
        raise ValueError("manifest 必须为 JSON 对象")
    errors = []
    declared = payload.get("declared_at")
    if not (isinstance(declared, str) and declared.strip()):
        errors.append("declared_at 必须为非空字符串（声明时间，可追溯）")
    for field in MANIFEST_REQUIRED_STRING_FIELDS:
        value = payload.get(field)
        if value is not None and not (isinstance(value, str) and value.strip()):
            errors.append(f"{field} 声明时必须为非空字符串（不声明可缺省，声明空串非法）")
    roles = payload.get("authorized_roles")
    if roles is not None and not (isinstance(roles, list)
                                  and all(isinstance(r, str) and r.strip() for r in roles)):
        errors.append("authorized_roles 必须为非空字符串数组")
    budget = payload.get("read_only_budget")
    if budget is not None and not isinstance(budget, dict):
        errors.append("read_only_budget 必须为对象（探针数/时长/写入次数三口径）")
    if errors:
        raise ValueError("manifest 校验失败：" + "；".join(errors))
    return payload


def _http_contract(manifest):
    """http_contract：运行时依赖恒具备（python + stdlib urllib）；
    available 取决于 manifest 是否登记 target_origin。结果只进可达性旁证，
    不进页面证据（X-07 修订：缺目标/契约时不能 available）。"""
    if manifest and str(manifest.get("target_origin") or "").strip():
        return _entry(
            "available",
            "运行时依赖恒具备（python + stdlib urllib）；manifest 已登记 target_origin="
            + str(manifest["target_origin"]).strip(),
            "")
    return _entry(
        "unprobed",
        "运行时依赖恒具备，但未登记目标地址/契约文件——无法声明接口通道可用",
        "在 runtime-manifest 登记 target_origin 与契约路径；结果只进「可达性旁证」，"
        "不进页面证据")


def _find_chrome(manifest):
    """chrome 可执行文件探测：manifest 声明优先，其次已知 ms-playwright 路径。"""
    declared = str((manifest or {}).get("chrome_path") or "").strip()
    if declared and Path(declared).is_file():
        return declared
    roots = [Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"] if os.environ.get("LOCALAPPDATA") else []
    for root in roots:
        if not root.is_dir():
            continue
        for candidate in sorted(root.glob("**/chrome.exe")):
            return str(candidate)
    return None


def _playwright_require_ok(playwright_core_path):
    """node -e 冒烟：require('playwright-core') 只解析模块，不启动浏览器。
    node 缺失或模块不可 require → (False, 失败原因)。"""
    script = ("let m;try{m=require(process.env.PW_PROBE_TARGET||'playwright-core');}"
              "catch(e){console.log('fail:'+String(e.message||e).slice(0,120));process.exit(0)}"
              "console.log('ok:'+(m&&m.chromium?'chromium':'no-chromium'))")
    env = dict(os.environ)
    if playwright_core_path:
        env["PW_PROBE_TARGET"] = playwright_core_path
    try:
        done = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                              timeout=20, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"node 冒烟不可用：{type(exc).__name__}"
    out = (done.stdout or "").strip()
    if out.startswith("ok:"):
        return True, out[3:]
    return False, (out[5:] if out.startswith("fail:") else
                   f"node 冒烟输出异常：{out[:80] or done.stderr[:80]}")


def _dir_writable(path):
    """目录存在且可写（用临时文件实测，不落任何业务内容）。"""
    base = Path(path)
    if not base.is_dir():
        return False, "目录不存在"
    try:
        with tempfile.TemporaryFile(dir=str(base)):
            return True, ""
    except OSError as exc:
        return False, f"不可写：{type(exc).__name__}"


def _browser_selfhost(manifest):
    """browser_selfhost：playwright-core 可 require + Chrome 命中 + profile 可写。
    三条件齐 → available；任一缺 → missing，recovery 指向宿主提供的运行时。"""
    pw_path = str((manifest or {}).get("playwright_core_path") or "").strip() or None
    ok, detail = _playwright_require_ok(pw_path)
    if not ok:
        return _entry(
            "missing",
            f"playwright-core 不可 require（{detail}）",
            "宿主提供 playwright-core 运行时，或声明 PLAYWRIGHT_CORE_PATH / "
            "manifest.playwright_core_path；不自动安装（exploration-runtime.md 164 行）")
    chrome = _find_chrome(manifest)
    if not chrome:
        return _entry(
            "missing",
            "未找到 Chrome 可执行文件（manifest.chrome_path 与已知 ms-playwright 路径均未命中）",
            "宿主提供 Chrome，或 manifest 声明 chrome_path")
    profile = str((manifest or {}).get("profile_dir") or "").strip()
    if not profile:
        return _entry(
            "missing",
            f"Chrome 已命中（{chrome}）但未声明固定 profile 目录——登录态无处落盘",
            "manifest 声明 profile_dir，或由用户完成有头登录后声明固定 profile")
    ok, detail = _dir_writable(profile)
    if not ok:
        return _entry("missing", f"profile 目录不可用（{detail}）：{profile}",
                      "修正 profile 目录权限，或重新声明可写的固定 profile")
    return _entry("available",
                  f"playwright-core 可 require；Chrome={chrome}；profile 可写={profile}",
                  "")


def _browser_mcp(manifest):
    """browser_mcp：MCP 工具存在于会话内，脚本探测不到（不在本进程工具清单）。
    恒 unprobed，由 Agent 按 browser-connection.md §一的工具名前缀在会话内判定；
    切换须留痕并重取快照。"""
    return _entry(
        "unprobed",
        "MCP 工具存在于 Agent 会话内，脚本层不可探测——按 browser-connection.md §一"
        "的工具名前缀在会话内判定（默认 Chrome DevTools MCP）",
        "会话内确认 MCP 工具可用性；切换通道须留痕并重取快照")


def _auth_state(manifest):
    """auth_state：只检查 profile 目录/文件存在性与可写性 → available 仅表示
    「可尝试登录」；登录态有效性保持 unprobed，由批次执行时按
    browser-connection.md §二处理（X-07 修订：不做任何读回）。"""
    profile = str((manifest or {}).get("profile_dir") or "").strip()
    if not profile:
        return _entry(
            "unprobed",
            "未声明固定 profile 目录——登录态检查无从谈起（本通道不做任何登录态读回）",
            "manifest 声明 profile_dir；或由用户在有头窗口手动登录（不自动填密码、不读 Cookie）")
    ok, detail = _dir_writable(profile)
    if not ok:
        return _entry("missing", f"profile 目录不可用（{detail}）：{profile}",
                      "用户重新登录固定 profile；失效期间相关项记 BLOCKED")
    return _entry(
        "available",
        f"profile 目录存在且可写（{profile}）——仅表示「可尝试登录」；"
        "登录态有效性未探测（unprobed），由批次执行时按 browser-connection.md §二处理",
        "")


def _design_data(manifest):
    """design_data：Figma 三来源任一（MCP / REST / 导出物）。REST 看
    FIGMA_TOKEN 环境变量；导出物看 manifest.figma_export_path；MCP 同
    browser_mcp 由会话内判定。均缺 → missing + dataMissing 口径（不得写还原度）。"""
    has_token = bool(os.environ.get("FIGMA_TOKEN"))
    export = str((manifest or {}).get("figma_export_path") or "").strip()
    if has_token or (export and Path(export).is_file()):
        source = "FIGMA_TOKEN 环境变量（REST 来源）" if has_token else f"导出物（{export}）"
        return _entry("available", f"Figma 设计数据来源命中：{source}", "")
    return _entry(
        "missing",
        "Figma 三来源均未命中（FIGMA_TOKEN 环境变量缺失、manifest 无导出物路径、MCP 会话内未判定）",
        "提供 Figma MCP 会话、声明 FIGMA_TOKEN，或 manifest 登记 figma_export_path；"
        "取数失败一律记 dataMissing，不得写「还原度」（design-data.md）")


_PROBES = {"http_contract": _http_contract, "browser_selfhost": _browser_selfhost,
           "browser_mcp": _browser_mcp, "auth_state": _auth_state,
           "design_data": _design_data}


def probe(manifest=None):
    """执行五通道探测，返回 runtime-capabilities/1 结构。不抛通道级异常：
    单通道内部错误记 missing 并把异常摘要写进 reason（纯探测不因环境怪异而崩）。"""
    if manifest is not None:
        manifest = validate_manifest(manifest)
    channels = {}
    for name in CHANNELS:
        try:
            entry = _PROBES[name](manifest)
        except Exception as exc:  # 探测本身失败 = 该通道 missing，不是脚本崩溃
            entry = _entry("missing", f"探测异常：{type(exc).__name__}: {str(exc)[:120]}",
                           "复查探测环境；通道不可用时按降级链 BLOCKED")
        if entry["state"] not in STATES:
            raise ValueError(f"通道 {name} 的 state 非法：{entry['state']}")
        channels[name] = entry
    return {
        "schema": SCHEMA,
        "probed_at": _now(),
        "manifest_present": manifest is not None,
        "channels": channels,
        "degradation_chain": {
            "api_or_contract": "http_contract",
            "page_interaction": "browser_selfhost -> browser_mcp -> BLOCKED(带 recovery)",
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="运行时能力探测（纯探测，零副作用）：五通道三态 + reason + recovery")
    parser.add_argument("--runtime-manifest", dest="runtime_manifest",
                        help="运行时 manifest JSON（schema: runtime-manifest/1）；"
                             "缺省时只读环境变量与已知路径，各通道按 unprobed 处理")
    parser.add_argument("--out", help="capabilities JSON 输出路径；缺省打印 stdout")
    args = parser.parse_args(argv)

    manifest = None
    if args.runtime_manifest:
        manifest = json.loads(Path(args.runtime_manifest).read_text(encoding="utf-8-sig"))
    result = probe(manifest)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"capabilities written: {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())