#!/usr/bin/env python3
"""sitegen 阶段 1 对齐验证：sitegen 产物 ↔ Hexo public/ 产物逐项核对。

用法::

    uv run python scripts/sitegen_check.py <hexo源仓根> <hexo产物目录> [--out DIR]

默认 out=build/sitegen-check（fresh 生成，不碰 hexo 仓）。核对项：

1. 文章 id 集合双向 diff（URL 身份：两边应完全一致）
2. 每篇文章 <title> 首段一致（front-matter title 语义）
3. sitegen 文章页引用的 /assets/* 在产物 assets/ 全部存在
4. 站内链接（/article /tags /categories /archives /page）全部可达
5. 页内锚点 #x 在同页存在 id（hexo 百分号编码形态解码后比对）
6. assets 覆盖面：hexo 引用而 sitegen 缺失的媒体名清单

退出码非零 = 有核对项失败（CI/阶段 2 回归可用）。
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mdtool.core.sitegen.generate import build_site  # noqa: E402
from mdtool.core.sitegen.legacy import (  # noqa: E402
    legacy_assets_dir,
    legacy_post_inputs,
    load_legacy_manifest,
    load_legacy_spec,
)

_RE_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_RE_SRC_HREF = re.compile(r'(?:src|href)="([^"]+)"')
_RE_IDS = re.compile(r'id="([^"]+)"')


def _parse_ids_set(text: str) -> set[str]:
    return {unquote(m) for m in _RE_IDS.findall(text)}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("blog_root", type=Path)
    ap.add_argument("hexo_public", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv[1:])

    blog_root, hexo_public = args.blog_root, args.hexo_public
    out_dir = args.out or (ROOT / "build" / "sitegen-check")
    if out_dir.exists():
        shutil.rmtree(out_dir)

    spec = load_legacy_spec(blog_root)
    manifest = load_legacy_manifest(blog_root)
    inputs, missing = legacy_post_inputs(blog_root, manifest)
    report = build_site(inputs, spec, assets_src=legacy_assets_dir(blog_root),
                        out_dir=out_dir)

    failures: list[str] = []

    # 1. 文章 id 集合
    hexo_ids = {p.stem for p in (hexo_public / "article").glob("*.html")}
    sg_ids = {p.stem for p in (out_dir / "article").glob("*.html")}
    if hexo_ids != sg_ids:
        failures.append(f"文章 id 集合不一致：hexo 独有 {sorted(hexo_ids - sg_ids)}"
                        f"，sitegen 独有 {sorted(sg_ids - hexo_ids)}")

    # 逐篇核对
    # 逐篇核对（站内 dest 一律以"输出目录中存在"判定可达——页面与静态
    # 资源同属一棵输出树，无需区分）
    asset_names = {p.name for p in (out_dir / "assets").glob("*")} \
        if (out_dir / "assets").is_dir() else set()

    hexo_asset_refs: set[str] = set()
    for p in sorted((out_dir / "article").glob("*.html")):
        text = p.read_text(encoding="utf-8")
        sg_title_m = _RE_TITLE.search(text)
        sg_title = sg_title_m.group(1).split("|")[0].strip() if sg_title_m else ""
        ids = _parse_ids_set(text)
        for m in _RE_SRC_HREF.finditer(text):
            dest = unquote(m.group(1))
            if dest.startswith("/assets/"):
                name = dest[len("/assets/"):]
                if name not in asset_names:
                    failures.append(f"{p.name}: 媒体缺失 {dest}")
            elif dest.startswith("/"):
                if not (out_dir / dest.lstrip("/")).exists():
                    failures.append(f"{p.name}: 站内链接不可达 {dest}")
            elif dest.startswith("#"):
                if unquote(dest[1:]) not in ids:
                    failures.append(f"{p.name}: 锚点不可达 {dest}")
        # 对照 hexo 同 id 页：title 与 assets 引用覆盖
        hexo_page = hexo_public / "article" / p.name
        if hexo_page.is_file():
            htext = hexo_page.read_text(encoding="utf-8")
            hm = _RE_TITLE.search(htext)
            hexo_title = hm.group(1).split("|")[0].strip() if hm else ""
            if sg_title != hexo_title:
                failures.append(f"{p.name}: title 不一致 hexo={hexo_title!r} sitegen={sg_title!r}")
            for m in _RE_SRC_HREF.finditer(htext):
                d = unquote(m.group(1))
                if d.startswith("/assets/"):
                    hexo_asset_refs.add(d[len("/assets/"):])

    # 6. hexo 引用的媒体 sitegen 是否都有
    missing_assets = sorted(n for n in hexo_asset_refs if n not in asset_names)
    if missing_assets:
        failures.append(f"hexo 引用而 sitegen 缺失的媒体：{missing_assets}")

    print(f"spec: {spec.title} | {spec.url}")
    print(f"生成: 文章 {report.posts}，文件 {report.files}，"
          f"跳过 {len(report.skipped)}，清单失联 {len(missing)}")
    for s in report.skipped:
        print(f"  [skip] {s}")
    for s in missing:
        print(f"  [失联] {s}")
    print(f"文章 id 集合: hexo {len(hexo_ids)} vs sitegen {len(sg_ids)} -> "
          f"{'一致' if hexo_ids == sg_ids else '不一致'}")
    print(f"hexo 引用媒体 {len(hexo_asset_refs)} 个，sitegen assets {len(asset_names)} 个")
    if failures:
        print(f"\n核对失败 {len(failures)} 项：")
        for f in failures[:40]:
            print(f"  ✗ {f}")
        return 1
    print("\n全部核对通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
