#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_load_contract.py — 从 L5 知识库(user_knowledge) 按 action ID / 关键词载入接口契约。

【适用范围】适用于含契约文件的知识库，扫描 <KB_ROOT>/project 下（不存在时退回
<KB_ROOT>）的 .md/.json/.yaml/.yml：
  <KB_ROOT>/project/showdoc_api_docs/.../*.md          （ShowDoc markdown，团队主流）
  <KB_ROOT>/project/contracts/.../*.{json,yaml,yml}    （OpenAPI/Swagger/极简 JSON 契约）
  <KB_ROOT>/project/_vector_index/meta.json             （向量索引，可选兜底）
不再绑定 showdoc_api_docs 单一目录：契约放哪个子目录都能按文件名命中。
Confluence 等需登录的来源仍请手动粘贴契约文档。
示例知识库：qa-req2testcase-generator 的 user_knowledge 目录（源设计位于与本 skill 同级目录；剥离适配：本包内无该同级目录，请显式传 `--kb` 或设 `KB_PATH`，未配置时报错）。

检索策略（均不依赖 numpy，纯标准库）：
  1) 文件系统匹配：在 project 下按文件名匹配 token，支持 .md/.json/.yaml/.yml
  2) 兜底：读取 project/_vector_index/meta.json（JSON 数组），模糊匹配 title/rel_path/text 含关键词的条目

用法：
  python kb_load_contract.py --kb <KB_ROOT> --action 1000107
  python kb_load_contract.py --kb <KB_ROOT> --keyword "AI主页" --out contract.md
  python kb_load_contract.py --kb <KB_ROOT> --action 1000108 --no-print   # 只列路径
  python kb_load_contract.py --kb <KB_ROOT> --action 1000107 --endpoint    # 抽取文档里的『接口地址』
  python kb_load_contract.py --action 1000107   # 省略 --kb：按 KB_PATH 回退（剥离适配：包内无同级知识库，未配置时报错）
"""
import argparse
import json
import os
import re
import sys


_CONTRACT_EXTS = (".md", ".json", ".yaml", ".yml")
_EXCLUDE_DIRS = {"_vector_index", "__pycache__", ".git", "node_modules",
                 ".venv", "venv", "dist", "build"}


def _iter_contract_files(kb_root):
    """遍历知识库下的契约文件（.md/.json/.yaml/.yml），不绑定 showdoc_api_docs。

    优先扫 <KB_ROOT>/project；该目录不存在时退回扫 <KB_ROOT> 本身，兼容
    「根目录直接放契约」的知识库。索引目录、缓存与依赖目录一律跳过，
    避免把 meta.json / 构建产物当成契约命中。
    """
    seen = set()
    for base in (os.path.join(kb_root, "project"), kb_root):
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS and not d.startswith(".")]
            for f in files:
                if not f.lower().endswith(_CONTRACT_EXTS):
                    continue
                p = os.path.normpath(os.path.join(root, f))
                if p in seen:
                    continue
                seen.add(p)
                yield p


def find_by_filesystem(kb_root, token):
    """在知识库契约文件（.md/.json/.yaml/.yml）里按文件名匹配 token。"""
    token_l = token.lower()
    hits = [p for p in _iter_contract_files(kb_root)
            if token_l in os.path.basename(p).lower()]
    return sorted(set(hits))


def find_by_vector_index(kb_root, token):
    """兜底：读 meta.json，按 title/rel_path/text 模糊匹配。返回 (rel_path, title) 列表。"""
    hits = []
    meta_path = os.path.join(kb_root, "project", "_vector_index", "meta.json")
    if not os.path.isfile(meta_path):
        return hits
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return hits
    if not isinstance(data, list):
        return hits
    token_l = token.lower()
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", ""))
        rel_path = str(item.get("rel_path", ""))
        text = str(item.get("text", ""))
        hay = (title + " " + rel_path + " " + text[:2000]).lower()
        # 仅关注契约文件来源：按扩展名识别，不再绑定 showdoc_api_docs 目录；
        # 索引/缓存目录里的条目（如 meta.json 自身）一律排除。
        low = rel_path.lower()
        if not low.endswith(_CONTRACT_EXTS):
            continue
        if any(t in rel_path for t in _EXCLUDE_DIRS):
            continue
        if token_l in hay:
            key = rel_path or title
            if key in seen:
                continue
            seen.add(key)
            hits.append((rel_path, title))
    return hits


def extract_endpoints(md_text):
    """从契约 markdown 提取『接口地址』实际 URL。

    策略：
      1) 命中形如 `# 接口地址` / `## 请求地址` 的标题行，取其行 + 后续 3 行内的 URL；
      2) 命中「生产环境：https://...」这类「环境名：URL」写法。
    不抓取文档来源(showdoc.com.cn)与示例载荷(example.wjx.cn)等无关 URL。
    """
    eps = []
    heading_re = re.compile(
        r'^#{1,6}\s*(接口地址|请求地址|请求路径|API地址|接口URL|请求URL|调用地址)', re.I)
    url_re = re.compile(r'https?://[^\s`\'"<>）)】]+')
    env_url_re = re.compile(
        r'(生产环境|测试环境|预发布环境|沙箱环境|联调环境|开发环境|正式环境)[：:]\s*`?'
        r'(https?://[^\s`\'"<>]+)', re.I)
    lines = md_text.splitlines()
    for i, line in enumerate(lines):
        if heading_re.match(line.strip()):
            window = lines[i:i + 4]
            for w in window:
                eps.extend(url_re.findall(w))
        for m in env_url_re.findall(line):
            eps.append(m[1])
    # 去重并保持顺序
    seen = set()
    out = []
    for e in eps:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def default_kb_candidates():
    """未传 --kb 时的默认回退候选列表（剥离适配：本包内该候选不存在，实际仅 KB_PATH 生效；未配置时报错）：源设计为与本 skill 同级目录下的 qa-req2testcase-generator/user_knowledge。

    脚本路径：<skill>/scripts/kb_load_contract.py
      parents: scripts(0) -> <skill>(1) -> <skill父目录>(2)
    由于 skill 可能经「真实目录」或「目录 junction」两种路径被调用，分别基于
    abspath 与 realpath 计算候选，取第一个实际存在的目录，保证两种入口都能命中。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    skill_parent = os.path.dirname(os.path.dirname(here))
    cand1 = os.path.join(skill_parent, "qa-req2testcase-generator", "user_knowledge")
    real_skill_parent = os.path.dirname(os.path.dirname(os.path.realpath(here)))
    cand2 = os.path.join(real_skill_parent, "qa-req2testcase-generator", "user_knowledge")
    cands = [cand1]
    if os.path.normpath(cand2) != os.path.normpath(cand1):
        cands.append(cand2)
    return cands


MERGE_BANNER = ("<!-- 本文档由 %d 页契约合并而成，仅供人读通览；下游解析器按「单契约」"
                "处理时，各页的请求/响应字段会揉进同一份字段集（字段串味），"
                "请勿直接作为单契约输入。 -->\n\n")


def guard_multi_write(paths, out_path, allow_merge=False):
    """--out 命中多页默认拒绝写出。

    合并写出产出的文件在下游看来就是「一个接口的契约」：多页的请求表、响应表会被
    揉进同一份字段集，第二个接口的字段从此成为第一个接口的断言前提（实测把
    「答卷查询 1001002」与「默认报告查询 1001101」两页合并后，1001002 的响应字段
    里混进了 answer_report / item_count）。这类错误全程不报错，只在执行期表现为
    「字段缺失」假缺陷，所以必须在**文件产生之前**拦住，而不是事后打印一句警告。
    """
    if not out_path:
        return
    if not paths:
        sys.stderr.write("错误：--out 指定了输出文件，但没有任何契约命中——"
                         "不写出空文件（空契约会让下游生成 0 字段用例）\n")
        sys.exit(2)
    if len(paths) < 2 or allow_merge:
        return
    sys.stderr.write("错误：--out 命中 %d 页契约，合并写出会被下游当成单契约解析（字段串味）\n"
                     % len(paths))
    for p in paths:
        sys.stderr.write("  - %s\n" % p)
    sys.stderr.write("处理方式：用 --action <ID> 精确到一页；确需多页通读时显式加 --allow-merge"
                     "（该文件仅供人读，不可作为单契约输入）。\n")
    sys.exit(2)


def write_bundle(paths, out_path):
    """把命中页写入 out_path：多页时加自述横幅与页间分隔，单页保持原样。"""
    with open(out_path, "w", encoding="utf-8") as fh:
        if len(paths) > 1:
            fh.write(MERGE_BANNER % len(paths))
        for i, p in enumerate(paths):
            if i:
                fh.write("\n---\n\n")
            fh.write("# %s\n\n" % os.path.basename(p))
            try:
                with open(p, "r", encoding="utf-8") as sf:
                    fh.write(sf.read())
            except Exception as e:
                fh.write("[读取失败: %s]" % e)
            fh.write("\n")


def main():
    ap = argparse.ArgumentParser(description="从 L5 知识库载入接口契约文档")
    ap.add_argument("--kb", help="user_knowledge 根目录绝对路径（可省略：省略时回退到 KB_PATH 环境变量（剥离适配：本包内无同级知识库，后续回退候选必不存在，未配置时报错））")
    ap.add_argument("--action", help="动作 ID，如 1000107")
    ap.add_argument("--keyword", help="关键词，如 'AI主页' 或 '创建AI主页'")
    ap.add_argument("--out", help="将命中文档内容写入此文件（命中多页时默认拒绝，需 --allow-merge）")
    ap.add_argument("--allow-merge", action="store_true",
                    help="允许把多页命中合并写入 --out（仅供人读；合并文件不可直接作为单契约输入）")
    ap.add_argument("--no-print", action="store_true", help="只打印命中路径，不打印正文")
    ap.add_argument("--endpoint", action="store_true",
                    help="额外提取契约里的『接口地址』实际 URL 并打印（每行 ENDPOINT: <url>）")
    args = ap.parse_args()

    token = (args.action or args.keyword or "").strip()
    if not token:
        sys.stderr.write("错误：必须提供 --action 或 --keyword\n")
        sys.exit(2)

    # 解析 KB 根目录：显式 --kb > KB_PATH 环境变量 > 与 skill 同级目录的默认回退
    kb_root = args.kb
    src = "--kb 参数"
    if not kb_root:
        env_kb = os.environ.get("KB_PATH")
        if env_kb:
            kb_root = env_kb
            src = "KB_PATH 环境变量"
    if not kb_root:
        src = "默认回退（剥离适配：本包内无同级知识库，候选不存在）"
        for c in default_kb_candidates():
            if os.path.isdir(c):
                kb_root = c
                break
        else:
            kb_root = default_kb_candidates()[0]  # 仅用于报错展示

    if not os.path.isdir(kb_root):
        sys.stderr.write("错误：KB 根目录不存在: %s\n" % kb_root)
        sys.stderr.write("提示：可用 --kb <路径> 显式指定，或设置 KB_PATH 环境变量。\n")
        sys.exit(2)
    print("== 使用 KB 根目录（来源：%s）: %s ==" % (src, kb_root))

    # 1) 优先文件系统精确匹配
    fs_hits = find_by_filesystem(kb_root, token)
    fs_norm = {os.path.normpath(p) for p in fs_hits}

    # 2) 向量索引兜底（补充文件系统未命中项）
    vi_hits = find_by_vector_index(kb_root, token)
    vi_paths = []
    for rel_path, _title in vi_hits:
        # rel_path 可能是相对 KB 根或相对 project 的路径，两种都试
        cand = os.path.join(kb_root, rel_path)
        if not os.path.isfile(cand):
            cand = os.path.join(kb_root, "project", rel_path)
        if os.path.isfile(cand) and os.path.normpath(cand) not in fs_norm:
            vi_paths.append(cand)

    all_hits = sorted(set(os.path.normpath(p) for p in (fs_hits + vi_paths)))

    print("== 命中契约文档 (%d) ==" % len(all_hits))
    for p in all_hits:
        print("  " + p)

    if args.endpoint:
        print("\n== 契约内『接口地址』抽取 ==")
        for p in all_hits:
            try:
                with open(p, "r", encoding="utf-8") as sf:
                    eps = extract_endpoints(sf.read())
            except Exception:
                eps = []
            if eps:
                for e in eps:
                    print("  ENDPOINT: %s   (来自 %s)" % (e, os.path.basename(p)))
            else:
                print("  （%s 未检出接口地址字段）" % os.path.basename(p))

    guard_multi_write(all_hits, args.out, args.allow_merge)

    if args.no_print:
        if args.out:
            write_bundle(all_hits, args.out)
            print("== 已写出: %s ==" % args.out)
        return

    # 打印正文
    chunks = []
    for p in all_hits:
        try:
            with open(p, "r", encoding="utf-8") as sf:
                body = sf.read()
        except Exception as e:
            body = "[读取失败: %s]" % e
        chunks.append("# %s\n\n%s" % (os.path.basename(p), body))

    full = "\n\n---\n\n".join(chunks)
    if args.out:
        write_bundle(all_hits, args.out)
        print("\n== 已写出: %s ==" % args.out)
    else:
        print("\n========== 契约正文 ==========\n")
        print(full)


if __name__ == "__main__":
    main()
