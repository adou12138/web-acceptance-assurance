#!/usr/bin/env python3
"""kb_freshness.py — 知识库新鲜度检查（只读、建议性，不阻断契约定位）。

判据口径（按审核意见修正，勿改回墙钟单一判据）：
- **主判据 = 源-索引漂移**：`project/` 内源文件最新 mtime 晚于索引
  `build_at`/`source_mtime` → 判 stale（语义通道结果不可信，退关键词通道）；
- 墙钟年龄（index_age_days）只是提示字段，**不参与 stale 判定**；
- 站点快照 `crawled_at`（wjx_site/manifest.json）是"数据截至"，只展示，不判 stale。

KB 解析顺序与 kb_load_contract 一致：`--kb` 参数 > `KB_PATH` 环境变量 > 同级候选。
退出码：0 = 正常出报告（含 stale），2 = KB 根不存在/不可读（此时按 doc_review
登记节降级为用户提供契约原文——本脚本失败不得阻断契约定位，见 fail-open 语义）。

用法：
  python kb_freshness.py [--kb <KB_ROOT>] [--json]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path


def _epoch(value: str) -> float:
    """ISO 时间戳（可带时区）转 epoch 秒；解析失败返回 0。"""
    try:
        ts = _dt.datetime.fromisoformat(value)
        if ts.tzinfo is None:
            ts = ts.astimezone()
        return ts.timestamp()
    except (ValueError, TypeError, OSError):
        return 0.0


def resolve_kb_root(explicit: str | None) -> tuple[str | None, str]:
    """解析顺序与 kb_load_contract.main 一致：--kb > KB_PATH > 同级候选。"""
    if explicit:
        return explicit, "--kb 参数"
    env_kb = os.environ.get("KB_PATH")
    if env_kb:
        return env_kb, "KB_PATH 环境变量"
    try:
        from kb_load_contract import default_kb_candidates
    except ImportError:
        default_kb_candidates = None  # 独立运行时允许缺失，仅报错展示
    if default_kb_candidates is not None:
        for cand in default_kb_candidates():
            if cand and os.path.isdir(cand):
                return cand, "同级候选"
    return None, "无可用来源（未传 --kb、未设 KB_PATH）"


def check_kb(kb_root: str) -> dict:
    project = Path(kb_root) / "project"
    result: dict = {"kb_root": str(kb_root), "verdict": "ok", "stale": False}

    info_path = project / "_vector_index" / "info.json"
    if not info_path.is_file():
        result["verdict"] = "missing_index"
        result["note"] = "语义索引缺失（info.json 不存在）：关键词通道仍可用，语义通道不可用"
        return result
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        result["verdict"] = "missing_index"
        result["note"] = f"语义索引不可读（{exc}）：关键词通道仍可用"
        return result

    build_at = str(info.get("build_at") or "")
    source_mtime = float(info.get("source_mtime") or 0)
    result["index_build_at"] = build_at
    result["index_n_chunks"] = info.get("n_chunks")
    if build_at:
        age_days = (_dt.datetime.now(_dt.timezone.utc).timestamp() - _epoch(build_at)) / 86400
        result["index_age_days"] = round(age_days, 1)  # 仅提示，不判 stale

    newest, newest_path = 0.0, ""
    if project.is_dir():
        for p in project.rglob("*"):
            if p.is_file() and "_vector_index" not in p.parts:
                m = p.stat().st_mtime
                if m > newest:
                    newest, newest_path = m, str(p)
    result["source_newest_mtime"] = (
        _dt.datetime.fromtimestamp(newest).astimezone().isoformat() if newest else None)
    result["source_newest_file"] = newest_path or None

    if source_mtime and newest > source_mtime:
        result["verdict"] = "stale_drift"
        result["stale"] = True
        result["note"] = ("源文件比语义索引新：索引落后于源，语义通道结果不可信，"
                          "退回关键词通道并在报告注明“语义通道未更新”；重建入口见 wjx-knowledge-base 文档")
    else:
        result["note"] = "索引未落后于源文件（漂移判据通过）"

    manifest = project / "wjx_site" / "manifest.json"
    if manifest.is_file():
        try:
            crawled = str(json.loads(manifest.read_text(encoding="utf-8")).get("crawled_at") or "")
            if crawled:
                result["site_data_as_of"] = crawled  # 数据截至，只展示，不判 stale
        except (OSError, ValueError):
            pass
    return result


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="知识库新鲜度检查（漂移主判据；只读、建议性）")
    ap.add_argument("--kb", help="知识库根目录；省略时回退 KB_PATH 环境变量或同级候选")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(list(argv) if argv is not None else None)

    kb_root, resolved_by = resolve_kb_root(args.kb)
    if not kb_root or not os.path.isdir(kb_root):
        print(json.dumps({"verdict": "kb_missing", "kb_root": kb_root, "resolved_by": resolved_by,
                          "note": "知识库不可用：按 doc_review 登记节降级为用户提供契约原文（B/C/D 模式），"
                                  "不得凭记忆补契约；本失败不阻断其他检查（fail open）"},
                         ensure_ascii=False))
        return 2

    result = check_kb(kb_root)
    result["resolved_by"] = resolved_by
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("== KB 新鲜度（判据：源-索引漂移；墙钟与快照时间仅展示）==")
        print(f"KB 根目录（{result['resolved_by']}）：{result['kb_root']}")
        print(f"结论：{result['verdict']}" + ("（stale）" if result.get("stale") else ""))
        for key in ("index_build_at", "index_age_days", "index_n_chunks",
                    "source_newest_mtime", "site_data_as_of"):
            if key in result:
                print(f"{key}: {result[key]}")
        print(f"note: {result.get('note')}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
