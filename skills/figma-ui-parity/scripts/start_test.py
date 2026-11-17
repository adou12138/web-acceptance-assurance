#!/usr/bin/env python3
"""Start gate for UI parity runs.

This is a deterministic gate, not an interactive question UI. The orchestrator
must obtain the user's safety choice before invoking it and pass that choice in.
Non-test consultations should not call this script.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
TRIGGERS = re.compile(
    # ① 触发词在前、目标词在后（原口径）
    r"(开始|启动|重新执行|继续|跑|执行|检查|查看|看看|查查|验证|测|审)"
    r".*(ui\s*parity|figma|视觉|还原|走查|对照|响应式|自适应|断点|适配)"
    # ② 目标词在前、检查/疑问语气在后：实测高频措辞「不同断点下的响应式有没有问题」
    #    旧版只认 ①，把这类合法检查诉求判成咨询（P2 实测 started=false），
    #    与 test-prompts.json 第 2 条的契约背离。语气词表刻意不含裸「吗/呢」，
    #    避免「响应式这个说法对吗」这类纯咨询被误判为启动。
    r"|(ui\s*parity|figma|视觉|还原|走查|对照|响应式|自适应|断点|适配)"
    r".*(有没有|是否|能不能|可不可以|怎么样|如何|正不正常|不正常|有问题|问题|对不对|好不好"
    r"|检查|查看|看看|查查|验证|测|审)"
    # ③ 裸词：无上下文的领域词直接视为启动意图（保持原行为）
    r"|ui\s*parity|走查",
    re.I,
)
FIGMA_HOSTS = {"figma.com", "www.figma.com"}


_REDACT_KEY = re.compile(
    r"(?i)(password|passwd|pwd|token|secret|apikey|api[_-]?key|access[_-]?key|"
    r"credential|authorization|session|sig|signature|ticket|cookie)")


def _redact(text: str | None) -> str:
    """凭据类 query / 头值打码；与 runtime_observability 的采集端脱敏同口径。

    run-manifest 是长期留存的产物，SKILL 红线要求「输入 URL 脱敏」——
    带 ?token=/sessionid= 的地址不能原样落盘。
    """
    s = str(text or "")
    s = re.sub(r"([?&;])([^=&;\s]+)=([^&;\s]*)",
               lambda m: m.group(1) + m.group(2) + "="
               + ("[REDACTED]" if _REDACT_KEY.search(m.group(2)) else m.group(3)), s)
    s = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._\-]+", r"\1[REDACTED]", s)
    s = re.sub(r"(?i)(Basic\s+)[A-Za-z0-9+/=]+", r"\1[REDACTED]", s)
    return s


def is_url(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except ValueError:
        return False


def is_figma_url(value: str | None) -> bool:
    if not is_url(value):
        return False
    host = urlparse(value).netloc.lower().split(":")[0]
    return host in FIGMA_HOSTS or host.endswith(".figma.com")


# 【F43a】否定前缀：否定词后的「取消/不要/无需/先不」等意图反转词——
# 「取消走查」「不要跑 parity」不应触发 started=true（旧版裸词或运算全命中）。
#
# 【误伤修复】否定只应否定**紧随其后的那个短语**。旧实现对整句做 NEGATION.search，
# 只要句中任意位置出现「否定词 + 8 字内目标词」就无条件 return False：
#   - 「停止响应式适配检查，现在开始 UI Parity」被拒（真实意图是启动，否定在前一分句）；
#   - 「这个页面不需要响应式适配，走查走查」反而漏网（词表缺「不需要」）。
# 修法：① 按句读边界（。！？；\n）切句，判定范围限定在否定词所在的**那个分句**内；
#       ② 补齐「不需要/不想/不必/别」等词条。
# 句读边界：。！？；\n 为强边界；逗号同样是分句边界且是实测必需——
# 「停止响应式适配检查，现在开始 UI Parity」「这个页面不需要响应式适配，走查走查」
# 两例都只靠逗号断成「否定分句 + 启动分句」，只切强边界会落回整句单分句而误拒。
SENTENCE_SPLIT = re.compile(r"[。！？!?；;，,\n]")
NEGATION_WORDS = ("取消", "撤销", "不要", "无需", "先不", "暂停", "终止", "停止",
                  "放弃", "跳过", "不需要", "不想", "不必", "别", "不用", "无需再")
NEGATION_TARGETS = re.compile(
    r"(ui\s*parity|figma|视觉|还原|走查|对照|响应式|自适应|断点|适配|测试)", re.I)
NEGATION = re.compile(
    "(?:%s)\\s*[^。；！？!?\\n]{0,8}?(?:%s)"
    % ("|".join(NEGATION_WORDS), NEGATION_TARGETS.pattern), re.I)


def is_negated(instruction: str | None) -> bool:
    """判定整句是否含否定意图：只在否定词所在的**分句**内做匹配。

    分句之外的匹配一律不算否定——「停止响应式适配检查，现在开始 UI Parity」的第一个
    分句含否定（该分句内确实在否定适配检查），但第二个分句是明确的启动意图；
    因此只要**任一分句**不含否定、且整句含触发词，就应放行。
    这里返回「是否存在一个分句，其内含否定+目标词」——调用方据此与 TRIGGERS 联合判定。
    """
    if not instruction:
        return False
    for clause in SENTENCE_SPLIT.split(instruction):
        if clause.strip() and NEGATION.search(clause):
            return True
    return False


# 【审查修复·P2-17】历史陈述分句不得充当启动触发——
# 「上个版本走查过了，这次不用再走查」第一分句不含否定词，旧版 positive_clause
# 原样保留它，TRIGGERS 裸词「走查」命中 ⇒ started=true（已实证：明明在拒绝
# 重测，门禁却放行）。判定：分句含过去时间词 + 目标词、且不含「继续/重新」
# 等重启动词 ⇒ 视为历史陈述，从 positive_clause 剔除。
#   - 「上个版本走查过了，这次不用再走查」→ 两个分句都被剔除 → 拒绝 ✓
#   - 「继续上次的走查」→ RESUME_WORDS 豁免 → 放行 ✓
#   - 「已经走查过一半了，继续走查」→ 第一分句剔除、第二分句放行 ✓
#   - 「上个版本的走查报告在哪」→ 历史咨询，非启动指令 → 不触发 ✓
#   - 「上个月用 Figma 比过这个页面」→ 月度回顾同属历史陈述 → 不触发 ✓
# 【F52】词表补齐月度口径：缺「上个月」时该分句不被判历史，URL 配对会把它
# 当成启动信号（详见 should_start 的 rule 3 守卫）。
PAST_WORDS = re.compile(r"(上个版本|上个迭代|上一次|上次|之前|此前|刚刚|刚才|昨天|前天|上周|上上周|上个月|上月|已经|早已|早就)")
RESUME_WORDS = re.compile(r"(继续|重新|再来|接着|恢复|重启)")


def is_historical(clause: str) -> bool:
    """分句是否为对既往事实的陈述（而非当下的启动指令）。"""
    return bool(PAST_WORDS.search(clause) and NEGATION_TARGETS.search(clause)
                and not RESUME_WORDS.search(clause))


def positive_clause(instruction: str | None) -> str:
    """返回含触发词、不含否定且非历史陈述的那部分文本（多个分句以空格连接）。

    触发判定同样必须限定在分句内：否定只否定紧随的短语，不能让否定分句里的
    目标词满足 TRIGGERS；历史陈述（P2-17）同理，不能让「上个版本走查过了」
    这类既往事实充当当下的启动证据。
    """
    if not instruction:
        return ""
    ok = [c.strip() for c in SENTENCE_SPLIT.split(instruction)
          if c.strip() and not NEGATION.search(c) and not is_historical(c)]
    return " ".join(ok)


def should_start(instruction: str | None, figma_url: str | None, page_urls: list[str]) -> tuple[bool, str]:
    # 否定判定收窄到分句后，只有当**全部分句**都被否定时才拒绝启动：
    # 「停止响应式适配检查，现在开始 UI Parity」——第一分句否定、第二分句启动 → 放行。
    if instruction and is_negated(instruction) and not positive_clause(instruction).strip():
        return False, "negated_test_instruction"
    if instruction and TRIGGERS.search(positive_clause(instruction)):
        return True, "explicit_test_instruction"
    if is_figma_url(figma_url) and any(is_url(item) for item in page_urls):
        # 【F52】P2-17 原则的 URL 通道补齐：指令存在但 positive_clause 为空
        # （全部分句都是历史陈述或被否定）时，URL 配对不充当启动信号——
        # 「上个月用 Figma 比过这个页面」+ 双 URL 旧版直接 started=true，
        # 一次纯历史回顾就启动了完整走查。无指令文本时 URL 配对照常触发。
        if instruction and not positive_clause(instruction).strip():
            return False, "not_a_test_start"
        return True, "figma_and_page_urls_present"
    return False, "not_a_test_start"


def run_json(command: list[str], cwd: Path, extra_env: dict | None = None) -> dict:
    env = {**os.environ, **(extra_env or {})}
    proc = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "command failed").strip())
    return json.loads(proc.stdout)


def _auth_freshness_snapshot(root: Path) -> dict:
    """扫描 root 下全部 .auth 目录的保存时间，返回清单与新鲜度。

    多份凭据并存是本轮假缺陷的直接成因（旧凭据目录被静默选中），这里
    只读 savedAt 时间字段做清单，不解析凭据内容。
    """
    entries = []
    for p in sorted(root.rglob(".auth")):
        info = p / "auth_info.json"
        if not info.is_file():
            continue
        try:
            data = json.loads(info.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            entries.append({"dir": str(p), "savedAt": None, "ageHours": None, "error": "unreadable"})
            continue
        saved = data.get("savedAt")
        age = None
        if saved:
            try:
                age = round((datetime.now(timezone.utc) - datetime.fromisoformat(saved)).total_seconds() / 3600, 1)
            except ValueError:
                age = None
        entries.append({"dir": str(p), "savedAt": saved, "ageHours": age})
    stale = [e for e in entries if e.get("ageHours") is None or (e.get("ageHours") or 0) > 24]
    return {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "dirs": entries,
        "staleThresholdHours": 24,
        "staleDirs": [e["dir"] for e in stale],
        "warning": ("多个登录态目录并存且存在超 24h 未刷新项，运行前请显式指定 PARITY_AUTH_DIR"
                    if len(entries) > 1 and stale else None),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="UI parity 测试启动门禁")
    parser.add_argument("--instruction", default="")
    parser.add_argument("--figma-url", default="")
    parser.add_argument("--page-url", action="append", default=[])
    parser.add_argument("--safety-policy", choices=["all", "safe", "mockWrites", "staticOnly"], required=False)
    parser.add_argument("--test-environment-confirmed", action="store_true")
    parser.add_argument("--user-safety-selection", default="")
    parser.add_argument("--workdir", type=Path, default=Path("./parity-test"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    started, reason = should_start(args.instruction, args.figma_url, args.page_url)
    result = {"started": started, "trigger": reason}
    if not started:
        result["message"] = "未识别为 UI parity 测试启动，不执行环境检查或页面交互。"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if not args.safety_policy:
        result.update({"status": "blocked", "reason": "safety_policy_required", "message": "正式测试前必须先取得本轮安全策略选择。"})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    if args.safety_policy == "all" and not args.test_environment_confirmed:
        result.update({"status": "blocked", "reason": "test_environment_confirmation_required", "message": "全部操作必须确认当前为隔离测试环境。"})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    root = args.root.resolve()
    workdir = args.workdir if args.workdir.is_absolute() else root / args.workdir
    try:
        subprocess.run(
            [sys.executable, str(HERE / "init_workdir.py"),
             "--root", str(root), "--workdir", str(args.workdir)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        environment = run_json([sys.executable, str(HERE / "check_environment.py")], root,
                               {"PARITY_WORKDIR": str(workdir)})
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        print("[x] 工作目录初始化或环境检查失败：{}".format(
            (getattr(exc, "stderr", None) or str(exc)).strip()[:400]), file=sys.stderr)
        return 2
    # 【F43b】环境门禁必须真判：state=unavailable/needs_setup 时不得输出 status:ready——
    # 旧版拿到 environment 只透传不判断，「环境门禁」形同虚设。
    env_state = str((environment or {}).get("state", ""))
    write_interception = {"all": "none", "safe": "abort", "mockWrites": "mock", "staticOnly": "abort"}[args.safety_policy]
    # 【F43c】userSafetySelection 必须是用户原话：缺失时置 null（留痕"未提供"），
    # 不得用策略值伪造「用户选择」——违反 SKILL.md 留痕要求。
    user_selection = (args.user_safety_selection or "").strip()
    manifest = {
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "trigger": reason,
        "figmaUrl": _redact(args.figma_url),
        "pageUrls": [_redact(u) for u in (args.page_url or [])],
        "safetyPolicy": args.safety_policy,
        "userSafetySelection": user_selection or None,
        "userSafetySelectionProvided": bool(user_selection),
        # writeInterception 新语义：mockWrites = 交互前放行初始化写（留痕）、
        # 交互后中断业务写；不再是「全部非只读请求一律拦截」。
        "writeInterception": write_interception,
        "writeInterceptionNote": ("交互前放行页面初始化写请求并留痕；交互后业务写中断并计数"
                                  if write_interception == "mock" else None),
        "testEnvironmentConfirmed": bool(args.test_environment_confirmed),
        "environment": environment,
        "notRun": [] if args.safety_policy == "all" else ["caution", "danger"] if args.safety_policy == "safe" else ["danger"] if args.safety_policy == "mockWrites" else ["all interactive actions"],
    }
    # 登录态新鲜度入清单：本轮用的是哪个目录、多老，一眼可查。
    # 曾因两个 .auth 目录并存（新旧凭据各一份）静默跑到登录页产出假缺陷，
    # 这里把「用了哪份凭据」固化成 manifest 字段，多目录并存时显式告警。
    manifest["authFreshness"] = _auth_freshness_snapshot(root)
    # 【F49】run-manifest 必须落盘，即使环境降级/阻断也不例外——它记录的是本轮的
    # 安全策略与用户选择（SKILL.md 工作流第 1 步），旧版在 needs_setup/unavailable
    # 分支直接 return 且 manifest=None，安全留痕随环境缺失一起蒸发。
    workdir.mkdir(parents=True, exist_ok=True)
    manifest_path = workdir / "run-manifest.json"
    if env_state in ("unavailable", "needs_setup"):
        manifest["environmentGate"] = {
            "state": env_state,
            "verdict": "blocked" if env_state == "unavailable" else "degraded",
            "note": "环境检查未通过（%s）——依赖缺失时不得以 ready 状态放行正式测试" % env_state,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        result.update({
            "status": "blocked" if env_state == "unavailable" else "degraded",
            "reason": "environment_%s" % env_state,
            "message": "环境检查状态为 %s：%s" % (
                env_state,
                "缺不可用依赖，正式测试被阻断；请先补齐依赖" if env_state == "unavailable"
                else "部分依赖未就绪，可降级执行但产物需标注环境降级"),
            "manifest": str(manifest_path),
            "environment": environment,
            "safetyPolicy": args.safety_policy,
        })
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 3 if env_state == "unavailable" else 0
    manifest["environmentGate"] = {"state": env_state, "verdict": "pass"}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    result.update({"status": "ready", "manifest": str(manifest_path), "environment": environment, "safetyPolicy": args.safety_policy})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
