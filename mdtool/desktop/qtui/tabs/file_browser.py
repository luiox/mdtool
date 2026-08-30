"""散装 KB 纯逻辑层 — 供统一「笔记库」页（qtui/tabs/library.py）调用。

本模块不含 UI。历史上它是文件浏览器 Tab，UI 合并后只保留可独立测试的
部分（tests/test_file_browser.py、test_link_resolver.py 直接导入这里）：

- :func:`plan_zip_bundle` / :func:`plan_zip_bundle_many` — zip 打包计划
  （媒体 URL 经 resolver 改写为 zip 内相对路径，知识库规范 §5）
- :func:`_search_md_job` — 散装正则搜索 worker（直接扫文件，不依赖 db）
- :func:`_export_db_job` — 散装 → SQLite 容器导出 job
"""

import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Optional

from mdtool.core.link_resolver import DEFAULT_HOST, DEFAULT_PORT, is_external_url, iter_link_destinations, parse
from mdtool.core.server.notes_db import NotesDB, extract_title
from mdtool.core.utils import resolve_image_path

_HIDDEN_ENTRIES = {"images", "assets", "meta.db"}  # KB 根的媒体面，不属于笔记树


def is_hidden_entry(name: str) -> bool:
    return name.lower() in _HIDDEN_ENTRIES


def list_md_tree(root: Path) -> list[tuple[str, Path]]:
    """Walk ``root`` and return sorted ``(posix_rel_path, abs_path)`` for .md
    files. 任意层级命中 ``_HIDDEN_ENTRIES`` 的目录都跳过（与旧树/搜索行为一致）。"""
    out: list[tuple[str, Path]] = []

    def _walk(dir_path: Path, prefix: str):
        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except (PermissionError, OSError):
            return
        for entry in entries:
            if is_hidden_entry(entry.name):
                continue
            if entry.is_dir():
                _walk(entry, f"{prefix}{entry.name}/")
            elif entry.suffix.lower() == ".md":
                out.append((f"{prefix}{entry.name}", entry))

    _walk(root, "")
    return out


def open_external(path: Path):
    """系统默认程序打开（Windows os.startfile，其他平台 open/xdg-open）。"""
    try:
        os.startfile(str(path))  # Windows
    except AttributeError:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        import subprocess
        subprocess.Popen([opener, str(path)])


# ── zip 打包计划（纯函数，可测试）──

def plan_zip_bundle(
    content: str,
    md_file: Path,
    *,
    depth: int = 0,
    media_root: Optional[Path] = None,
    images_subdir: str = "images",
    assets_subdir: str = "assets",
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    used: Optional[dict] = None,
) -> tuple[str, list[tuple[str, Path]], list[str]]:
    """解析 md 内容中的媒体链接，产出 zip 打包计划（单篇）。

    返回 ``(改写后的内容, [(zip成员名, 源文件路径), ...], [跳过的链接])``。

    - ``depth``：笔记在 zip 内的目录层数（0 = zip 根），链接改写为
      ``"../" * depth + "images/<name>"``（媒体平铺在 zip 根）。
    - 本知识库媒体 URL → 从媒体根 ``<media_root>/<subdir>`` 找文件；
      相对路径旧链接 → 按 ``md_file`` 所在目录解析，改写为 ``assets/<basename>``。
    - 外部链接 / 找不到文件 → 原样保留（找不到的记入 skipped 供日志）。
    - ``used``：跨笔记共享的成员去重表（多篇导出时传入）。
    """
    img_dir = Path(media_root) / images_subdir if media_root else None
    ast_dir = Path(media_root) / assets_subdir if media_root else None
    if used is None:
        used = {}
    prefix = "../" * depth
    out: list[str] = []
    members: list[tuple[str, Path]] = []
    skipped: list[str] = []
    pos = 0
    for start, end, dest, _is_image in iter_link_destinations(content):
        out.append(content[pos:start])
        member: Optional[str] = None
        src: Optional[Path] = None
        ref = parse(dest, host=host, port=port)
        if ref is not None:
            base = img_dir if ref.category == "images" else ast_dir
            cand = base / ref.name if base else None
            if cand is not None and cand.is_file():
                member, src = f"{ref.category}/{ref.name}", cand
        elif not is_external_url(dest):
            cand = resolve_image_path(md_file, dest)
            if cand is not None and cand.is_file():
                member, src = f"assets/{Path(dest).name}", cand
        if member is None:
            out.append(content[start:end])
            if ref is not None or not is_external_url(dest):
                skipped.append(dest)
            pos = end
            continue
        if member in used:
            used[member] += 1
            stem, ext = Path(member).stem, Path(member).suffix
            member = f"{Path(member).parent}/{stem}_{used[member]}{ext}"
        else:
            used[member] = 1
        members.append((member, src))
        out.append(content[start:end].replace(dest, prefix + member, 1))
        pos = end
    out.append(content[pos:])
    return "".join(out), members, skipped


def plan_zip_bundle_many(
    notes: list[tuple[str, Path, str]],
    **kwargs,
) -> tuple[list[tuple[str, str, list[tuple[str, Path]]]], list[str]]:
    """多篇打包计划。

    ``notes``：``[(zip内相对路径如 folder/a.md, 磁盘md文件, 内容)]``。
    返回 ``([(zip内路径, 改写后内容, 成员)], 汇总跳过列表)``；成员去重在
    所有笔记间共享，媒体平铺 zip 根，链接按各自深度加 ``../`` 前缀。
    """
    used: dict = {}
    results: list[tuple[str, str, list[tuple[str, Path]]]] = []
    skipped_all: list[str] = []
    for zip_rel, md_file, content in notes:
        depth = zip_rel.count("/")
        new_content, members, skipped = plan_zip_bundle(
            content, md_file, depth=depth, used=used, **kwargs)
        results.append((zip_rel, new_content, members))
        skipped_all.extend(skipped)
    return results, skipped_all


def write_zip(zip_path: str, results, notes_count: int) -> tuple[int, int]:
    """把打包计划写成 zip 文件，返回 ``(篇数, 媒体数)``。"""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for zip_rel, new_content, members in results:
            zf.writestr(zip_rel, new_content)
            for member, src in members:
                zf.write(src, member)
    n_media = sum(len(m) for _, _, m in results)
    return notes_count, n_media


def collect_media_config(mc_load) -> dict:
    """从加载器读媒体配置并归一化为打包参数（host/port/subdirs/media_root）。

    ``mc_load`` 注入而非直接导入，保持本模块对 qtui 无依赖。
    """
    mc = mc_load()
    try:
        port = int(mc.get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return {
        "media_root": Path(mc["media_root"]) if mc.get("media_root") else None,
        "images_subdir": mc.get("images_subdir") or "images",
        "assets_subdir": mc.get("assets_subdir") or "assets",
        "host": mc.get("host") or DEFAULT_HOST,
        "port": port,
    }


# ── worker jobs ──

def _search_md_job(root: Path, pattern: str, scope: str, report) -> list[dict]:
    """散装正则搜索（worker 线程）：扫 ``<root>`` 下 .md，跳过媒体面目录。"""
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise ValueError(f"正则无效: {e}") from e

    md_files = sorted(root.rglob("*.md"))
    md_files = [p for p in md_files
                if not any(part.lower() in _HIDDEN_ENTRIES for part in p.parts)]
    results: list[dict] = []
    total = len(md_files)
    for i, md in enumerate(md_files):
        rel = md.relative_to(root).as_posix()
        hits = 0
        snippet = ""
        if scope in ("name", "both") and rx.search(md.name):
            hits += 1
            snippet = md.name
        body = ""
        if scope in ("body", "both"):
            try:
                body = md.read_text(encoding="utf-8")
            except Exception:
                body = ""
            for line in body.splitlines():
                if rx.search(line):
                    hits += 1
                    if not snippet:
                        snippet = line.strip()[:160]
                    if hits >= 3:
                        break
        if hits:
            results.append({
                "rel": rel,
                "title": extract_title(body) if body else "",
                "hits": hits,
                "snippet": snippet,
            })
        report("progress", current=i + 1, total=total)
    return results


def _export_db_job(db_path: Path, src_root: Path, report) -> dict:
    """散装 → db 包（worker 线程）：新建库并把 src_root 下 .md 导入。

    导入逻辑复用 notes_browser 的 :func:`_import_folder_job`（延迟导入，
    避免模块级循环依赖）。
    """
    from mdtool.desktop.qtui.tabs.notes_browser import _import_folder_job
    db = NotesDB(db_path)
    try:
        return _import_folder_job(db, src_root, report)
    finally:
        db.close()
