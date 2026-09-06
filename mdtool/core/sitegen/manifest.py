"""发布清单——笔记路径 → 文章 id 的持久映射（URL 身份的单一事实）。

语义沿承 Hexo 时代的 ``passage_index.json``（generate_passage_index.js）：
**id 只增不改、永不复用、永不重排**——permalink 是 ``article/<id>.html``，
id 一变老 URL 就 404。清单文件随笔记库提交，换机器不断链。

manifest 里 ``note`` 是不透明的键（约定为笔记库相对 posix 路径）；笔记被
移动/改名后键会失联，发布前校验（lookup miss → 报告重指）兜底，不为此
引入 UUID 复杂度——个人库移动笔记是低频事件。

JSON 形态::

    {"next_id": 29,
     "entries": [{"id": 1, "note": "blog/x.md", "published": "2025-06-12",
                  "selected": true}]}

``published`` 是首次发布日期（ISO 日期串，可空）；未出现在 entries 里的
笔记 = 草稿态。``selected`` 是勾选制发布开关：False 表示暂不发布但映射
保留——id 与 note 的绑定不可解除（URL 身份底线），重新勾选 id 不变。
写盘走 save_manifest，其余纯函数。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ManifestEntry:
    id: int
    note: str                 # 不透明键：约定笔记库相对 posix 路径
    published: str = ""       # 首次发布日期（ISO 日期串），草稿转正时填
    selected: bool = True     # 勾选制发布开关；False = 暂不发布，映射保留


@dataclass(frozen=True)
class Manifest:
    next_id: int
    entries: tuple[ManifestEntry, ...]

    def id_of(self, note: str) -> int | None:
        for e in self.entries:
            if e.note == note:
                return e.id
        return None

    def note_of(self, post_id: int) -> str | None:
        for e in self.entries:
            if e.id == post_id:
                return e.note
        return None


def load_manifest(path: Path) -> Manifest:
    """读清单文件；不存在/坏 JSON → 空清单（首建友好，绝不因清单损坏炸掉生成）。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return Manifest(1, ())
    entries = tuple(
        ManifestEntry(id=int(e.get("id", 0)), note=str(e.get("note", "")),
                      published=str(e.get("published", "")),
                      selected=bool(e.get("selected", True)))
        for e in data.get("entries", []))
    entries = tuple(sorted((e for e in entries if e.note and e.id > 0),
                           key=lambda e: e.id))
    next_id = max([int(data.get("next_id", 1)),
                   *(e.id + 1 for e in entries), 1])
    return Manifest(next_id, entries)


def save_manifest(path: Path, manifest: Manifest) -> None:
    """清单落盘（ensure_ascii=False，id 升序；目录不存在自动建）。"""
    data = {
        "next_id": manifest.next_id,
        "entries": [{"id": e.id, "note": e.note, "published": e.published,
                     "selected": e.selected}
                    for e in sorted(manifest.entries, key=lambda e: e.id)],
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def assign_ids(manifest: Manifest, notes: list[str],
               *, published: str = "") -> tuple[Manifest, dict[str, int]]:
    """给未入册的笔记分配 id（确定性：按 note 排序追加），返回 ``(新清单, 映射)``。

    已入册的沿用旧 id——这是"URL 身份 = 文件路径键"的底线；新分配只从
    ``next_id`` 前进，任何情况下不回收、不复用既有 id（含已从清单外失联的）。
    不落盘，调用方确认后 save_manifest。
    """
    known = {e.note: e.id for e in manifest.entries}
    mapping = dict(known)
    fresh = sorted(n for n in notes if n not in known)
    entries = list(manifest.entries)
    next_id = manifest.next_id
    for note in fresh:
        entries.append(ManifestEntry(id=next_id, note=note, published=published))
        mapping[note] = next_id
        next_id += 1
    return Manifest(next_id, tuple(entries)), mapping


def apply_selection(manifest: Manifest, checked_notes: list[str],
                    *, published: str = "") -> tuple[Manifest, dict[str, int]]:
    """勾选制定稿：把复选树的勾选集落成清单状态，返回 ``(新清单, 勾选映射)``。

    - 勾选且已入册 → 沿用旧 id（重新勾选 id 不变，URL 永不换址）；
    - 勾选但未入册 → 按 note 排序从 ``next_id`` 追加，``published`` 记首次
      勾选日期；
    - 未勾选的入册条目 → 仅置 ``selected=False``，id 与键原样保留——取消
      发布不回收 id（老 URL 语义归历史）。

    不落盘，调用方确认后 save_manifest。
    """
    checked = {e.note: e for e in manifest.entries}
    mapping: dict[str, int] = {}
    entries = list(manifest.entries)
    next_id = manifest.next_id
    fresh = sorted(n for n in checked_notes if n not in checked)
    for note in fresh:
        entry = ManifestEntry(id=next_id, note=note, published=published)
        entries.append(entry)
        checked[note] = entry
        next_id += 1
    checked_set = set(checked_notes)
    for i, e in enumerate(entries):
        if e.note in checked_set:
            entries[i] = e if e.selected else ManifestEntry(
                e.id, e.note, e.published, True)
            mapping[e.note] = e.id
        elif e.selected:
            entries[i] = ManifestEntry(e.id, e.note, e.published, False)
    return Manifest(next_id, tuple(entries)), mapping
