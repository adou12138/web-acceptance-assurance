# -*- coding: utf-8 -*-
"""缺陷回流自进化：把新发现的缺陷抽象成"模式候选"，回写到规则库候选池待评审。

闭环的最后一环：defect_patterns.md 的模式清单来自历史缺陷统计归纳，
但要让它"越用越准"，就得把本轮新发现、且现有模式没覆盖的缺陷回流成新候选。

纪律（防止规则库被污染）：
  * 候选先进候选池（`references/pattern_candidates.log.md`），**不直接改 defect_patterns.md**；
  * 每条候选必须带「证据」（缺陷现象 + 触发用例来源），评审通过后才并入正式清单；
  * 同一批次内，同维度且归一化后现象相同的候选会聚合计数；跨批次已登记的候选
    直接跳过（不重复登记，次数也不累加）。

职责拆分：纪律与登记口径写在 `references/pattern_candidates.md`（稳定文档，入库）；
本脚本只往 `references/pattern_candidates.log.md` 追加运行时数据（已 gitignore，不入库）。
两者分开是为了让「约定」能版本化，而「每轮跑出来的候选行」不污染提交历史。

用法：
  # 从问题台账自动提炼候选
  python pattern_feedback.py --from-csv api_test_issues.csv
  # 手工登记一条
  python pattern_feedback.py --add --dim B8 --symptom "可选字段传空被拒" --evidence "用例 B-NULL-xx"
"""
import argparse
import csv
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
# 【审查修复】normpath 消除 ".." 组件：部分 Windows 环境对含 ".." 的路径
# 一律 PermissionError，open() 直接失败，导致 _existing_symptoms 吞异常返回
# 空集、去重失效（补充模式已覆盖的现象被重复登记）。规范化后的路径在所有
# 平台行为一致。
# CANDIDATES 指「运行时池」（log），去重与写入都以它为准；CANDIDATES_DOC 是
# 纪律文档，只在去重时兜底读取（历史上候选行曾直接写在文档里）。
CANDIDATES = os.path.normpath(os.path.join(HERE, "..", "references",
                                           "pattern_candidates.log.md"))
CANDIDATES_DOC = os.path.normpath(os.path.join(HERE, "..", "references",
                                               "pattern_candidates.md"))
PATTERNS = os.path.normpath(os.path.join(HERE, "..", "references", "defect_patterns.md"))

HEADER = """# 缺陷模式候选池（运行时数据 · 不入库）

> 由 `scripts/pattern_feedback.py` 自动创建并追加；纪律与登记口径见 `references/pattern_candidates.md`。
> 本文件已在 `.gitignore`：候选只作待评审素材，评审通过后并入 `references/defect_patterns.md`。

| 登记时间 | 维度 | 候选模式（现象） | 出现次数 | 证据/来源 | 评审状态 |
| --- | --- | --- | --- | --- | --- |
"""


def _existing_symptoms():
    """读正式清单里已有的模式名，用于判断候选是否已被覆盖（避免重复登记）。

    【W55】旧版只抓 `##` 标题行，两个方向都错：① 「使用方式 / 缺陷模式池 /
    回归复测专项」等非模式小节标题混进 known（sym[:8] 前缀匹配，可能误跳过
    真候选）；② 补充模式全部是**表格行**、无 `##` 标题，根本不在 known 里——
    已被补充模式覆盖的缺陷会重复登记进候选池，去重形同虚设。
    现在 known = Top 节标题 + 补充模式节表格首列模式名，两节之外的小节标题不计。
    """
    try:
        with open(PATTERNS, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        # 【审查修复】读取失败不得静默返回空集——那会让「已覆盖现象」去重整体
        # 失效且无任何感知（skill 自身的反静默失败纪律同样适用于这里）。
        # 仍返回 []（缺清单时放行候选比误杀候选安全），但必须向 stderr 出声。
        sys.stderr.write("[pattern_feedback] 正式清单读取失败（%s: %s），"
                         "已知模式去重本轮不生效：%s\n" % (type(e).__name__, e, PATTERNS))
        return []
    known = re.findall(r"^##\s+Top\s*\d+\s*(.+)$", text, re.M)
    m = re.search(r"^##\s*补充模式[^\n]*\n(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    if m:
        for line in m.group(1).splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 2 or not cells[0]:
                continue
            if cells[0] == "模式" or set(cells[0]) <= {"-", " ", ":"}:
                continue  # 表头行与分隔行
            known.append(cells[0])
    return known


def _load_rows(path):
    rows = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


def _norm(s):
    s = re.sub(r"\[[^\]]*\]", "", s or "")
    s = re.sub(r"[0-9]+", "N", s)
    return re.sub(r"[\s，,。；;：:]+", "", s)[:50]


def mine_from_csv(csv_path):
    """从台账提炼候选：只取「缺陷」类，按维度+归一化现象聚合。"""
    rows = [r for r in _load_rows(csv_path)
            if (r.get("类型") or "").strip() == "缺陷"]
    agg = {}
    for r in rows:
        dim = (r.get("维度") or "-").strip()
        key = (dim, _norm(r.get("问题描述", "")))
        c = agg.setdefault(key, {"dim": dim, "symptom": r.get("问题描述", ""),
                                 "count": 0, "ids": []})
        c["count"] += 1
        c["ids"].append(r.get("编号", ""))
    return sorted(agg.values(), key=lambda c: (-c["count"], c["dim"]))


STATUS_VALUES = ("待评审", "已并入", "已归并", "不通过")
_CASE_PREFIX = re.compile(r"^\[[^\]]*\]\s*")


def _symptom_text(sym):
    """现象正文：去掉挖掘时加的 `[用例ID]` 前缀，否则前 8 字永远不含模式名。"""
    return _CASE_PREFIX.sub("", str(sym or "")).strip()


def _status_code(text):
    """评审状态是自由文本，机械统计按枚举前缀归组。"""
    value = str(text or "").strip()
    for name in STATUS_VALUES:
        if value.startswith(name):
            return name
    return "待评审"


def _canonical_cell(sym):
    """持久现象口径：替换管道符、截前60字，再去首尾空白，与表格读取一致。"""
    return str(sym or "").strip().replace("|", "/")[:60].strip()


def _pool_symptoms(path):
    """只取候选表的现象列，避免证据列/评审列的文字被当成已登记现象。"""
    out = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        return out
    for line in lines:
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or set(cells[0]) <= {"-", " ", ":"} or cells[0] == "登记时间":
            continue
        out.append(_canonical_cell(cells[2]))
    return out


def pool_status_counts(path):
    counts = {name: 0 for name in STATUS_VALUES}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        return counts
    for line in lines:
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 6 or set(cells[0]) <= {"-", " ", ":"} or cells[0] == "登记时间":
            continue
        counts[_status_code(cells[5])] += 1
    return counts


def _covered_by_formal(text, known):
    """现象正文与正式模式名做归一化前缀比对，避免只比原样前 8 字。"""
    head = _norm(text)[:6]
    if not head:
        return False
    for name in known:
        key = _norm(name)[:6]
        if key and (head == key or head.startswith(key) or key.startswith(head)):
            return True
    return False


def append_candidates(items):
    path = os.path.abspath(CANDIDATES)
    if not os.path.isfile(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(HEADER)
    # 读取、写入共用现象单元格口径，截断后的尾空格也须消除。
    # 只在「现象列单元格」里比较，不能拿整文件子串——证据列/评审列同样含中文，
    # 命中一次就会把真候选静默丢掉。
    # 去重同时看运行时池与纪律文档：历史候选曾直接写在文档里，迁移或人工补写
    # 都可能留行，只读 log 会把它们当新候选重复登记。
    pool = set(_pool_symptoms(path)) | set(_pool_symptoms(os.path.abspath(CANDIDATES_DOC)))
    known = _existing_symptoms()
    today = datetime.now().strftime("%Y-%m-%d")
    added = 0
    lines = []
    for it in items:
        sym = (it.get("symptom") or "").strip()
        if not sym:
            continue
        text = _symptom_text(sym)
        # 已被正式清单覆盖的模式跳过；带 [用例ID] 前缀时必须先剥前缀再比。
        if _covered_by_formal(text, known):
            continue
        cell = _canonical_cell(sym)
        if cell in pool:
            continue
        evidence = it.get("evidence") or (",".join(x for x in it.get("ids", []) if x) or "—")
        lines.append("| %s | %s | %s | %d | %s | 待评审 |" % (
            today, it.get("dim", "-"), cell,
            it.get("count", 1), evidence.replace("|", "/")[:50]))
        pool.add(cell)
        added += 1
    if not lines:
        print("无新增候选（可能已被正式清单覆盖或已登记）。")
        return 0
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("已登记 %d 条候选 -> %s" % (added, path))
    return added


def main():
    ap = argparse.ArgumentParser(description="缺陷回流自进化：提炼模式候选")
    ap.add_argument("--from-csv", help="从问题台账 CSV 自动提炼")
    ap.add_argument("--add", action="store_true", help="手工登记一条")
    ap.add_argument("--dim", help="维度编号")
    ap.add_argument("--symptom", help="缺陷现象（现象口吻）")
    ap.add_argument("--evidence", help="证据/触发用例来源")
    a = ap.parse_args()

    if a.add:
        if not (a.dim and a.symptom):
            ap.error("--add 需同时提供 --dim 与 --symptom")
        append_candidates([{"dim": a.dim, "symptom": a.symptom, "count": 1,
                            "evidence": a.evidence or "人工登记"}])
        return
    if a.from_csv:
        if not os.path.isfile(a.from_csv):
            sys.stderr.write("台账不存在：%s\n" % a.from_csv)
            sys.exit(2)
        items = mine_from_csv(a.from_csv)
        print("台账中缺陷类问题 %d 条，聚合出 %d 个候选模式。" % (
            sum(i["count"] for i in items), len(items)))
        append_candidates(items)
        return
    ap.error("需指定 --from-csv 或 --add")


if __name__ == "__main__":
    main()
