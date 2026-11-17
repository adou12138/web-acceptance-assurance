#!/usr/bin/env python3
"""为设计还原类缺陷生成网页首屏与设计稿整图的左右对照。

只用网页整页图截取首屏，不把元素特写冒充整屏，不根据网页 bbox 猜设计稿位置。
shotBox 仅用于网页周边区域图。尺寸不同时保留原有比例并注明限制；两张图并排
只是视觉证据，不代表已确认页面、数据状态或位置可比。刷新时撤下旧对比引用，
不删除引用指向的原始文件。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover - 环境缺依赖时明确报错，不静默跳过
    print("ERROR: 需要 Pillow（pip install pillow）才能生成设计稿对比图", file=sys.stderr)
    raise SystemExit(3)

# 设计稿裁剪必须与页面 crop **同框**：页面 crop 是 `locator.screenshot()`
# 的元素精确尺寸，这里一旦加 padding，两边尺寸就不一致，并排时为了对齐高度
# 会把页面图放大几倍——看上去"页面文字比设计稿大得多"，而那是缩放造成的假差异
# （实测 415x47 的标题被放大 3.4 倍）。上下文信息由整页带框图负责，不靠裁图留白。
PAD_RATIO = 0.0
PAD_MIN, PAD_MAX = 0, 0
# 对比图单侧宽度上限。报告会把小于 400KB 的图内联成 base64，整页级别的对比图
# 若按原图存（每侧 1520），一张就几百 KB 到 1MB，只能退化成链接——而"对比图"
# 恰恰是最该直接看到的东西。收到 700/侧（总宽约 1400）既能看清布局与文字位置，
# 也能稳定落在内联阈值内。
MAX_SIDE = 700
BORDER = 3
GAP = 12


def _open(path: Path) -> Image.Image:
    with Image.open(path) as img:
        return img.convert("RGB") if img.mode not in ("RGB", "RGBA") else img.copy()


def _clamp_box(box: dict, width: int, height: int) -> tuple[int, int, int, int]:
    """元素框 → 设计稿裁剪框。

    设计稿与页面即使侧栏宽一致，主区内容也可能有几十像素平移（设计稿是「已选
    项目的工作台」，页面是「空态」，两者布局本就不同）。按元素精确同框裁，
    设计稿那侧会把文字切掉一半——看起来像"元素位置错了"，实际只是状态差异。

    所以水平方向**以元素为参照向右留富余**（左 -1 倍宽、右 +1.5 倍宽），
    保证即使有平移也能看到完整元素；垂直方向留行距上下文，用来观察换行差异。
    精确的宽高差由缺陷描述里的数值负责（如"设计 671x81，页面 561x58"），
    不靠这张图去量。
    """
    x, y = float(box.get("x", 0)), float(box.get("y", 0))
    w, h = float(box.get("w", 0)), float(box.get("h", 0))
    vpad = max(18, min(56, int(h * 0.6)))
    left = max(0, int(x - w))
    right = min(width, int(x + w * 1.5))
    top = max(0, int(y) - vpad)
    bottom = min(height, int(y + h) + vpad)
    if right - left < 8 or bottom - top < 8:
        return 0, 0, width, height
    return left, top, right, bottom


def _scale_box(box: dict, vp: dict, design: Image.Image) -> tuple[dict, float, bool]:
    """把视口坐标映射到设计稿坐标。

    返回 (映射后的 box, 缩放比, 是否 1:1)。视口与设计稿同尺寸时不缩放。
    """
    vw, vh = int(vp.get("width") or 0), int(vp.get("height") or 0)
    dw, dh = design.size
    if not vw or not vh:
        return box, 1.0, False
    sx, sy = dw / vw, dh / vh
    exact = abs(sx - 1) < 0.01 and abs(sy - 1) < 0.01
    if exact:
        return box, 1.0, True
    return {
        "x": box["x"] * sx, "y": box["y"] * sy,
        "w": box["w"] * sx, "h": box["h"] * sy,
    }, sx, False


def _fit(img: Image.Image, max_side: int = MAX_SIDE) -> Image.Image:
    w, h = img.size
    if w <= max_side and h <= max_side:
        return img
    factor = min(max_side / w, max_side / h)
    return img.resize((max(1, int(w * factor)), max(1, int(h * factor))), Image.LANCZOS)


def _side_by_side(page_img: Image.Image, design_img: Image.Image) -> Image.Image:
    """左页面 / 右设计稿，**原始像素**并排。

    不做"缩放到等高"：两张图来自同一视口的同一区域，尺寸本就该一致；
    硬拉齐高度会在尺寸不一致时放大其中一张，把"缩放"伪装成"页面元素更大"这种
    不存在的差异。尺寸真不一致时如实保留并留出灰底，让人一眼看出是映射问题。
    只在整体过宽时做**同比**缩放，两边比例关系始终不变。
    """
    page_w, page_h = page_img.size
    design_w, design_h = design_img.size
    height = max(page_h, design_h)
    canvas_w = page_w + GAP + design_w + BORDER * 2
    canvas = Image.new("RGB", (canvas_w, height + BORDER * 2), "#d0d5dd")
    # 垂直居中：设计稿侧常因多留了上下文而更高，顶部对齐会让"两行 vs 一行"
    # 看起来像整体错位，居中后同一行文字仍在水平线上。
    canvas.paste(page_img, (BORDER, BORDER + (height - page_h) // 2))
    canvas.paste(design_img, (BORDER + page_w + GAP, BORDER + (height - design_h) // 2))
    # 统一按目标宽度等比缩放：单侧超过上限时整体缩，保证两侧比例关系不变。
    target_w = MAX_SIDE * 2
    if canvas_w > target_w:
        factor = target_w / canvas_w
        canvas = canvas.resize(
            (max(1, int(canvas_w * factor)), max(1, int(canvas.size[1] * factor))),
            Image.LANCZOS)
    return canvas


def _page_viewport(page_img: Image.Image, viewport: dict, design: Image.Image) -> Image.Image:
    """从页面整页截图取首屏，作为与设计稿整屏的可比证据。

    ``capture_defect_shots.cjs`` 的 ``page`` 图是 fullPage 截图，不能直接与设计稿
    拼接：页面高度通常远大于设计稿，会把两侧缩成不同语义的长图。这里固定取
    (0, 0, viewport.width, viewport.height)，并在尺寸不足时用中性底补齐；不对内容
    做非等比拉伸，避免把缩放伪装成视觉差异。
    """
    vw = int((viewport or {}).get("width") or design.size[0])
    vh = int((viewport or {}).get("height") or design.size[1])
    if page_img.size == (vw, vh):
        return page_img
    if page_img.size[0] != vw:
        factor = vw / page_img.size[0]
        page_img = page_img.resize(
            (vw, max(1, int(page_img.size[1] * factor))), Image.LANCZOS)
    canvas = Image.new("RGB", (vw, vh), "#f5f7fa")
    canvas.paste(page_img.crop((0, 0, min(vw, page_img.size[0]), min(vh, page_img.size[1]))), (0, 0))
    return canvas


def _full_page_pair(page_path: Path, design: Image.Image, viewport: dict | None = None) -> Image.Image | None:
    if not page_path.exists():
        return None
    try:
        page = _page_viewport(_open(page_path), viewport or {}, design)
        return _side_by_side(page, design)
    except Exception:
        return None


def _attach_page_region(defect: dict) -> None:
    """从整页截图裁一块「元素 + 周边」的区域图，替换过小的元素特写。

    判据是尺寸而不是字节：文字级缺陷的元素常只有 126x16，裁出来就是一细条，
    读者无法定位。小元素改用区域图（含周边 60–160px），大控件仍用元素特写。
    """
    shots = defect.get("screenshots") or []
    page_shot = next((s for s in shots if s.get("kind") == "page"), None)
    crop_shot = next((s for s in shots if s.get("kind") == "crop"), None)
    box = defect.get("shotBox")
    if not page_shot or not page_shot.get("path") or not box:
        return
    try:
        page_img = _open(Path(page_shot["path"]))
    except Exception:
        return
    x, y = float(box.get("x", 0)), float(box.get("y", 0)) + float(box.get("scrollY") or 0)
    w, h = float(box.get("w", 0)), float(box.get("h", 0))
    if w <= 0 or h <= 0:
        return
    pad = max(60, min(160, int(max(w, h) * 0.6)))
    left = max(0, int(x) - pad)
    right = min(page_img.size[0], int(x + w) + pad)
    top = max(0, int(y) - pad)
    bottom = min(page_img.size[1], int(y + h) + pad)
    if right - left < 16 or bottom - top < 16:
        return
    source = Path(page_shot["path"])
    name = (source.name[:-len(".page.png")] if source.name.endswith(".page.png") else source.stem) + ".region.png"
    out = source.with_name(name)
    if out.resolve() == source.resolve():
        out = source.with_name(source.stem + ".context.png")
    try:
        page_img.crop((left, top, right, bottom)).save(out)
    except Exception:
        return
    # 元素特写"够不够看"以图本身为准：尺寸太小或字节数过低（纯色块/细线）
    # 都不足以让人定位，一律换成区域图。用尺寸以外的判据是因为同一尺寸下
    # 内容复杂度差很多，1px 分隔线裁出来可能是 0KB 的空图。
    crop_usable = False
    if crop_shot and crop_shot.get("path"):
        try:
            ci = _open(Path(crop_shot["path"]))
            crop_usable = (ci.size[0] >= 240 and ci.size[1] >= 50
                           and Path(crop_shot["path"]).stat().st_size >= 4000)
        except Exception:
            crop_usable = False
    shots = [s for s in shots if s.get("kind") != "region"]
    if not crop_usable:
        shots = [s for s in shots if s.get("kind") != "crop"]
        shots.insert(0, {"kind": "crop", "path": str(out),
                         "note": "元素周边区域（元素本身 {}x{}，特写不足以定位）".format(int(w), int(h))})
    else:
        shots.append({"kind": "region", "path": str(out), "note": "元素周边区域"})
    defect["screenshots"] = shots


def main() -> int:
    ap = argparse.ArgumentParser(description="为缺陷生成设计稿同区域截图与左右对比图")
    ap.add_argument("--report", required=True, type=Path)
    ap.add_argument("--design-png", required=True, type=Path, help="设计稿 1x 整图")
    ap.add_argument("--viewport", default="1520x1013",
                    help="截图环节使用的视口（与设计稿同尺寸时 1:1 对应）")
    ap.add_argument("--out-dir", default=None, type=Path, help="默认与页面 crop 同目录")
    ap.add_argument("--max", default=60, type=int)
    ap.add_argument("--only-dims", default="7.1,7.2,7.3,7.5,7.6,7.7,7.8,7.13,7.21,7.23",
                    help="只对这些维度出设计稿对比（设计还原类）。对比度、性能、"
                         "网络异常这类页面自身质量问题在设计稿上是空白，放并排图会"
                         "让人误以为「设计稿这里什么都没有」。传空串表示全部维度都出")
    args = ap.parse_args()

    only = {d.strip() for d in (args.only_dims or "").split(",") if d.strip()}

    payload = json.loads(args.report.read_text(encoding="utf-8"))
    defects = payload.get("defects") or []
    try:
        design = _open(args.design_png)
    except (OSError, ValueError) as exc:
        for defect in defects:
            defect["screenshots"] = [s for s in (defect.get("screenshots") or [])
                                     if s.get("kind") not in ("design", "compare")]
            defect["designNote"] = "设计稿不可读，未生成本轮对比：{}".format(str(exc)[:120])
        args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("ERROR: 设计稿不存在或不可读: {}".format(args.design_png), file=sys.stderr)
        return 3
    for defect in defects:
        for shot in defect.get("screenshots") or []:
            raw = shot.get("path")
            if raw and not Path(raw).is_absolute():
                shot["path"] = str((args.report.resolve().parent / raw).resolve())
    if not defects:
        print("[i] 没有缺陷，跳过设计稿对比")
        return 0

    m = args.viewport.lower().split("x")
    vp = {"width": int(m[0]), "height": int(m[1])} if len(m) == 2 and m[0].isdigit() else {}
    dw, dh = design.size

    stats = {"total": len(defects), "designCropped": 0, "fullPagePair": 0,
             "noBox": 0, "offScreen": 0, "failed": 0, "scaleNote": ""}
    if vp and (vp["width"] != dw or vp["height"] != dh):
        stats["scaleNote"] = "视口 {}x{} ≠ 设计稿 {}x{}；保留原始比例，仅作整图参考，不能作同视口位置结论".format(
            vp.get("width"), vp.get("height"), dw, dh)

    # 先撤掉全部旧对比引用；未选中、超限或本轮失败均不能沿用旧图。
    # 不删除引用文件：路径可能指向用户提供的原始素材。
    for index, defect in enumerate(defects):
        defect["screenshots"] = [s for s in (defect.get("screenshots") or [])
                                 if s.get("kind") not in ("compare", "design")]
        defect.pop("designNote", None)
        if index >= max(0, args.max):
            defect["designNote"] = "未生成对比图：超出本轮 --max 上限"

    for defect in defects[: max(0, args.max)]:
        dim = str(defect.get("dimension") or "")
        # 无论是否出设计稿对比，都先补一张「页面带上下文区域图」。
        # 元素特写对整块控件够用，但对「一段 126x16 的文字」只有 1KB，报告里
        # 就是一小条色块，看不出它在页面哪儿。区域图从整页图裁，扩出周边。
        _attach_page_region(defect)
        if only and dim not in only:
            # 非设计还原类维度不拼设计稿，旧引用已在本轮开始时撤下。
            stats_skipped = stats.get("skippedDims", 0) + 1
            stats["skippedDims"] = stats_skipped
            continue
        shots = defect.get("screenshots") or []
        crop_shot = next((s for s in shots if s.get("kind") == "crop"), None)
        page_shot = next((s for s in shots if s.get("kind") == "page"), None)
        box = defect.get("shotBox")

        # 输出目录跟页面图走，保证同一缺陷的几张图在一起，删起来也不会漏。
        base = None
        for s in (crop_shot, page_shot):
            if s and s.get("path"):
                base = Path(s["path"]).parent
                break
        if base is None:
            stats["noBox"] += 1
            continue
        out_dir = args.out_dir or base
        out_dir.mkdir(parents=True, exist_ok=True)

        stem = None
        for s in (crop_shot, page_shot):
            if s and s.get("path"):
                name = Path(s["path"]).name
                for suffix in (".crop.png", ".page.png"):
                    if name.endswith(suffix):
                        stem = name[: -len(suffix)]
                        break
            if stem:
                break
        if not stem:
            stats["failed"] += 1
            continue

        design_path = out_dir / (stem + ".design.png")
        compare_path = out_dir / (stem + ".compare.png")

        # 1) 设计稿证据固定为完整首屏，不再按元素 bbox 裁剪。
        # 页面和设计稿可能处于不同数据状态（例如页面空态、设计稿有项目），
        # 元素 bbox 只能用于页面红框定位，不能拿来推算设计稿坐标；否则会把
        # 主区平移、换行或空白状态误报成「对比区域错位」。
        # 设计稿整屏与页面同视口并排，元素级精确差异仍由 defect 描述给出。
        cropped = False
        try:
            design.save(design_path)
            shots = [s for s in shots if s.get("kind") != "design"]
            shots.append({
                "kind": "design",
                "path": str(design_path),
                "note": "设计稿完整首屏（与网页对比图同视口）",
            })
            defect["screenshots"] = shots
        except Exception as e:
            stats["failed"] += 1
            defect["designNote"] = "设计稿截图写入失败: {}".format(str(e)[:120])

        # 2) 拼左右完整首屏对比。page 图是 fullPage，先取首屏再与设计稿拼接，
        # 不使用元素 crop，保证两侧是同一视口、同一坐标原点、同一尺度。
        page_region = None
        if page_shot and page_shot.get("path"):
            try:
                page_region = _page_viewport(_open(Path(page_shot["path"])), vp, design)
            except Exception:
                page_region = None
        if page_region is None:
            defect["designNote"] = (defect.get("designNote") or "") + "；缺少整页截图或文件不可读，不生成整屏对比；元素特写仅作为页面局部证据"
            stats["failed"] += 1

        if page_region is not None:
            try:
                pair = _side_by_side(page_region, design)
                pair.save(compare_path)
                shots = [s for s in (defect.get("screenshots") or []) if s.get("kind") != "compare"]
                shots.append({
                    "kind": "compare",
                    "path": str(compare_path),
                    "note": "左：网页完整首屏（元素级有红框）　右：设计稿整图；" + (stats["scaleNote"] or "同视口并排；数据与交互状态仍需核对"),
                })
                defect["screenshots"] = shots
                stats["fullPagePair"] += 1
            except Exception as e:
                stats["failed"] += 1
                defect["designNote"] = (defect.get("designNote") or "") + "；对比图生成失败: " + str(e)[:100]

    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[i] 设计稿对比：裁剪 {}/{} 条，并排图 {} 条，无坐标 {} 条，首屏外 {} 条，失败 {} 条".format(
        stats["designCropped"], stats["total"], stats["fullPagePair"],
        stats["noBox"], stats["offScreen"], stats["failed"]))
    if stats["scaleNote"]:
        print("[i] " + stats["scaleNote"])
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
