#!/usr/bin/env python3
"""extract_docx_media.py — 从 .docx 解包内嵌图片，产出可逐张识读的清单。

用途：需求文档里的原型图/流程图/截图往往是规则的真正载体，而 Word 正文只写"见下图"。
本脚本只做**解包与登记**（标准库 zipfile + hashlib，零第三方依赖），不调用任何云端 OCR：
图片内容由宿主多模态能力逐张读取，结果按 `references/review-result.schema.json` 的
`visual_evidence[]` 回填（file / sha256 / type / extracted_rules / confidence / boundary / image_understood）。

边界：
- 只处理 `.docx`（OOXML 压缩包）；`.doc`/`.pdf` 需先转换，脚本直接报错退出。
- 不判断图片内容与价值，不生成规则文本——那是识读阶段的事，避免"脚本假装看懂了"。
- 同名图片按序号去重；无法解码的条目仍登记，交识读阶段标 `image_understood=false`。

用法：
    python scripts/extract_docx_media.py --docx <需求.docx> --out <工作目录> [--json]
输出：`<out>/media/` 下的图片文件 + `<out>/media_manifest.json`

退出码：0=成功（可能 0 张图）；2=用法/输入错误（非 docx / 无 zip 结构）。
"""

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".emf", ".wmf", ".tif", ".tiff"}


def sha256_of(data):
    return hashlib.sha256(data).hexdigest()


def extract(docx_path, outdir):
    docx_path = Path(docx_path)
    if docx_path.suffix.lower() != ".docx":
        raise ValueError(f"仅支持 .docx（收到 {docx_path.suffix or '无扩展名'}）；.doc/.pdf 请先转换")
    if not docx_path.is_file():
        raise ValueError(f"文件不存在：{docx_path}")
    outdir = Path(outdir)
    media_dir = outdir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    try:
        archive = zipfile.ZipFile(docx_path)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"不是有效的 docx（zip 结构校验失败）：{exc}") from exc
    with archive:
        names = [n for n in archive.namelist() if n.startswith("word/media/") and not n.endswith("/")]
        used = set()
        for index, name in enumerate(sorted(names), 1):
            data = archive.read(name)
            ext = Path(name).suffix.lower()
            if ext not in IMAGE_EXTS:
                continue
            target = media_dir / f"img{index:02d}{ext}"
            while target.name in used:
                target = media_dir / f"img{index:02d}_{len(used)}{ext}"
            used.add(target.name)
            target.write_bytes(data)
            entries.append({
                "file": str(target),
                "source_entry": name,
                "sha256": sha256_of(data),
                "bytes": len(data),
                "ext": ext,
                "decodable": ext not in {".emf", ".wmf"},
            })
    manifest = {
        "docx": str(docx_path),
        "docx_sha256": sha256_of(docx_path.read_bytes()),
        "count": len(entries),
        "images": entries,
        "note": "仅解包登记；识读结果回填 review-result.json 的 visual_evidence[]，读不到写 image_understood=false",
    }
    (outdir / "media_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                               encoding="utf-8")
    return manifest


def main():
    ap = argparse.ArgumentParser(description="解包 docx 内嵌图片并登记（不调用云端 OCR）")
    ap.add_argument("--docx", required=True)
    ap.add_argument("--out", required=True, help="工作目录（输出 media/ 与 media_manifest.json）")
    ap.add_argument("--json", action="store_true", help="打印完整清单")
    args = ap.parse_args()
    try:
        manifest = extract(args.docx, args.out)
    except (ValueError, OSError) as exc:
        print(f"解包失败：{exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"count": manifest["count"], "manifest": str(Path(args.out) / 'media_manifest.json')},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
