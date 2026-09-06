"""mdtool 命令行工具：知识库批量维护动作的脚本入口。

动机：桌面 GUI 不适合批量/自动化场景（笔记迁移、脚本化导入媒体），
此前这类操作只能临时写一次性脚本。CLI 把"导入媒体入图床"这类高频
动作固化：拷贝 + meta.db 登记 + 打印规范 URL，与媒体服务器的上传
语义一致（命名规则、双目录冲突检查同 :mod:`mdtool.core.server.media_server`），
但无需服务器在线——直接文件系统 + sqlite 操作。

用法（仓库根目录）::

    mdtool import-media <路径...> [--kb-root DIR] [--asset] [--dry-run]

    # 或没有 console script 时
    python -m mdtool import-media ../some/*.png

默认 KB 根取 ``~/.monocodes_media.json`` 的 ``media_root``（与桌面端
一致），``--kb-root`` 可覆盖；host/port 同样读该配置（默认 127.0.0.1:8765）。
"""

import argparse
import json
import mimetypes
import shutil
import sys
from datetime import datetime
from pathlib import Path

from mdtool.core.server.meta_db import MetaDB

CONFIG_FILE = Path.home() / ".monocodes_media.json"
DEFAULT_CONFIG = {
    "media_root": "",
    "images_subdir": "images",
    "assets_subdir": "assets",
    "port": 8765,
    "host": "127.0.0.1",
}


def load_media_config() -> dict:
    """读 ~/.monocodes_media.json，坏文件/缺字段按默认兜底（与 media_server tab 一致）。"""
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def timestamped_names(originals: list[str], existing: set[str], *,
                      asset: bool, now: datetime) -> list[tuple[str, str]]:
    """批量生成媒体时间戳名 → [(原名, 新名)]。

    规则与 media_server._make_name 同语义：图片前缀 ``image-``、附件无前缀，
    ``YYYYMMDDHHMMSS`` + 3 位递增序号 + 原扩展名（缺省 .png），
    与 existing（images/ 与 assets/ 并集）冲突时序号跳进。
    同批之间也互不重名（生成即入 existing）。
    """
    ts = now.strftime("%Y%m%d%H%M%S")
    prefix = "" if asset else "image-"
    seq = 1
    out = []
    for orig in originals:
        ext = Path(orig.split("#")[0].split("?")[0]).suffix or ".png"
        name = f"{prefix}{ts}{seq:03d}{ext}"
        while name in existing:
            seq += 1
            name = f"{prefix}{ts}{seq:03d}{ext}"
        existing.add(name)
        out.append((orig, name))
        seq += 1
    return out


def media_url(name: str, *, asset: bool, host: str, port: int) -> str:
    """按知识库规范的存储形式拼完整 URL（宿主可配置，path 才是权威标识）。"""
    kind = "assets" if asset else "images"
    return f"http://{host}:{port}/{kind}/{name}"


def collect_files(args: list[str]) -> list[Path]:
    """展开路径参数：目录递归取文件（跳过隐藏项），文件原样，不存在的报错。"""
    out: list[Path] = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            out.extend(sorted(
                q for q in p.rglob("*")
                if q.is_file() and not any(part.startswith(".") for part in q.parts)))
        elif p.is_file():
            out.append(p)
        else:
            raise FileNotFoundError(f"路径不存在: {a}")
    return out


def cmd_import_media(paths: list[str], kb_root: str | None,
                     asset: bool, dry_run: bool) -> int:
    cfg = load_media_config()
    root = Path(kb_root or cfg["media_root"])
    if not str(root) or not root.is_dir():
        print(f"KB 根不存在: {root}（用 --kb-root 指定，或先在桌面端配置 media_root）",
              file=sys.stderr)
        return 2

    files = collect_files(paths)
    if not files:
        print("没有可导入的文件", file=sys.stderr)
        return 2

    subdir = cfg["assets_subdir"] if asset else cfg["images_subdir"]
    other = cfg["images_subdir"] if asset else cfg["assets_subdir"]
    target_dir = root / subdir
    existing = {q.name for q in target_dir.iterdir()} if target_dir.is_dir() else set()
    if (root / other).is_dir():
        existing |= {q.name for q in (root / other).iterdir()}

    plan = timestamped_names([f.name for f in files], existing, asset=asset,
                             now=datetime.now())

    if dry_run:
        for (orig, name), src in zip(plan, files):
            print(f"[dry-run] {src} -> {media_url(name, asset=asset, host=cfg['host'], port=int(cfg['port']))}")
        return 0

    target_dir.mkdir(parents=True, exist_ok=True)
    meta = MetaDB(root / "meta.db")
    try:
        for (orig, name), src in zip(plan, files):
            shutil.copy2(src, target_dir / name)
            mime, _ = mimetypes.guess_type(orig)
            mime = mime or "application/octet-stream"
            if asset:
                meta.add_asset(name, orig, src.stat().st_size, mime)
            else:
                meta.add_image(name, orig, src.stat().st_size, mime)
            print(media_url(name, asset=asset, host=cfg["host"], port=int(cfg["port"])))
    finally:
        meta.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mdtool", description="mdtool 命令行工具：知识库批量维护动作")
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser(
        "import-media", help="把图片/附件导入知识库图床（拷贝 + meta.db 登记），逐行打印 URL")
    p_import.add_argument("paths", nargs="+", help="文件/目录（目录递归导入）")
    p_import.add_argument("--kb-root", default=None,
                          help="KB 根目录（默认读 ~/.monocodes_media.json 的 media_root）")
    p_import.add_argument("--asset", action="store_true",
                          help="导入 assets/（附件）而非 images/（图片）")
    p_import.add_argument("--dry-run", action="store_true", help="只打印计划不落盘")

    p_migrate = sub.add_parser(
        "blog-migrate", help="hexo 博客仓一次性迁入笔记库博客目录（阶段 3；拒绝重跑）")
    p_migrate.add_argument("--src", required=True, help="hexo 源仓根目录")
    p_migrate.add_argument("--kb", required=True, help="笔记库根目录")
    p_migrate.add_argument("--dir", default="blog",
                           help="笔记树下博客目录名（默认 blog）")
    p_migrate.add_argument("--dry-run", action="store_true", help="只打印计划不落盘")

    args = parser.parse_args(argv)
    if args.command == "import-media":
        return cmd_import_media(args.paths, args.kb_root, args.asset, args.dry_run)
    if args.command == "blog-migrate":
        from mdtool.cli.blog_migrate import cmd_blog_migrate
        return cmd_blog_migrate(args.src, args.kb, args.dir, args.dry_run)
    parser.error(f"未知命令: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
