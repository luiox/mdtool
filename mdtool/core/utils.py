import re
import hashlib
from pathlib import Path
from typing import List, Optional, Tuple

STANDARD_IMG = re.compile(r'!\[.*?\]\(([^)]+)\)')
OBSIDIAN_IMG = re.compile(r'!\[\[(.*?)\]\]')
FENCED_CODE = re.compile(r'(?:^|\n)[ \t]*(`{3,}|~{3,}).*?(?:\1)[ \t]*(?=\n|$)', re.DOTALL)
INLINE_CODE = re.compile(r'`[^`\n]+`')

DEFAULT_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "bmp", "webp", "svg"}

try:
    from libmarkdown import Document as AstDocument
    from libmarkdown.nodes import Image as AstImage
    HAVE_LIBMARKDOWN = True
except ImportError:
    HAVE_LIBMARKDOWN = False


def _code_regions(text: str) -> List[Tuple[int, int]]:
    regions = []
    for m in FENCED_CODE.finditer(text):
        regions.append((m.start(), m.end()))
    for m in INLINE_CODE.finditer(text):
        regions.append((m.start(), m.end()))
    return regions


def _in_code(pos: int, regions: List[Tuple[int, int]]) -> bool:
    for s, e in regions:
        if s <= pos < e:
            return True
    return False


def _match_extension(path: str, extensions: set) -> bool:
    """Check if path has a matching extension."""
    if not extensions:
        return True
    dot = path.rfind(".")
    if dot == -1:
        return False
    ext = path[dot + 1:].split("?")[0].split("#")[0].lower()
    return ext in extensions


def extract_image_links(
    text: str,
    *,
    use_standard: bool = True,
    use_obsidian: bool = True,
    extensions: set = DEFAULT_EXTENSIONS,
) -> List[dict]:
    """Extract image links using regex with code-block filtering."""
    regions = _code_regions(text)
    results = []

    if use_standard:
        for match in STANDARD_IMG.finditer(text):
            if _in_code(match.start(), regions):
                continue
            src = match.group(1).split("?")[0].split("#")[0]
            if not _match_extension(src, extensions):
                continue
            results.append({
                "type": "standard",
                "rel_path": src,
                "full_match": match.group(0),
                "start": match.start(),
                "end": match.end(),
                "line": text[:match.start()].count("\n") + 1,
            })

    if use_obsidian:
        for match in OBSIDIAN_IMG.finditer(text):
            if _in_code(match.start(), regions):
                continue
            src = match.group(1)
            if not _match_extension(src, extensions):
                continue
            results.append({
                "type": "obsidian",
                "rel_path": src,
                "full_match": match.group(0),
                "start": match.start(),
                "end": match.end(),
                "line": text[:match.start()].count("\n") + 1,
            })

    return results


def extract_image_links_ast(
    text: str,
    *,
    use_standard: bool = True,
    use_obsidian: bool = True,
    extensions: set = DEFAULT_EXTENSIONS,
) -> List[dict]:
    """Extract image links using libmarkdown AST parser."""
    if not HAVE_LIBMARKDOWN:
        return extract_image_links(text, use_standard=use_standard, use_obsidian=use_obsidian, extensions=extensions)

    results = []

    try:
        doc = AstDocument.from_string(text)
    except Exception:
        return extract_image_links(text, use_standard=use_standard, use_obsidian=use_obsidian, extensions=extensions)

    def walk(token):
        for child in getattr(token, "children", []):
            if isinstance(child, AstImage):
                if use_standard:
                    src = child.url.split("?")[0].split("#")[0]
                    if _match_extension(src, extensions):
                        # Find full_match via regex for content replacement
                        fm, sp, ep = "", -1, -1
                        for m in STANDARD_IMG.finditer(text):
                            if m.group(1) == child.url or m.group(1) == src:
                                fm, sp, ep = m.group(0), m.start(), m.end()
                                break
                        results.append({
                            "type": "standard",
                            "rel_path": src,
                            "full_match": fm,
                            "start": sp,
                            "end": ep,
                            "line": text[:sp].count("\n") + 1 if sp >= 0 else 1,
                        })
            walk(child)

    walk(doc)

    # Obsidian-style images: fallback to regex (libmarkdown doesn't parse ![[ ]])
    if use_obsidian:
        for match in OBSIDIAN_IMG.finditer(text):
            src = match.group(1)
            if _match_extension(src, extensions):
                results.append({
                    "type": "obsidian",
                    "rel_path": src,
                    "full_match": match.group(0),
                    "start": match.start(),
                    "end": match.end(),
                    "line": text[:match.start()].count("\n") + 1,
                })

    return results


def resolve_image_path(md_file: Path, rel_path: str) -> Optional[Path]:
    md_dir = md_file.parent
    candidate = md_dir / rel_path
    try:
        candidate = candidate.resolve()
    except (ValueError, OSError):
        pass
    try:
        return candidate if candidate.exists() else None
    except (ValueError, OSError):
        return None


def get_file_md5(filepath: Path) -> str:
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def get_file_signature(filepath: Path) -> Tuple[int, str]:
    try:
        size = filepath.stat().st_size
    except (ValueError, OSError):
        size = 0
    with open(filepath, "rb") as f:
        first_kb = f.read(1024)
    sig_hash = hashlib.md5(first_kb).hexdigest()
    return (size, sig_hash)


def find_md_files(root_dir: Path) -> List[Path]:
    return sorted(root_dir.rglob("*.md"))


def format_size(size_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


_seq = 0

def make_image_filename(base_dir: Path, original_filename: str = "image.png",
                        prefix: str = "image-") -> str:
    """Generate {prefix}YYYYMMDDHHMMSSnnn.EXT — uses current time."""
    global _seq
    from datetime import datetime
    original_filename = original_filename.split("#")[0].split("?")[0]
    ext = Path(original_filename).suffix or ".png"
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    _seq += 1
    name = f"{prefix}{ts}{_seq % 1000:03d}{ext}"
    while Path(base_dir, name).exists():
        _seq += 1
        name = f"{prefix}{ts}{_seq % 1000:03d}{ext}"
    return name


def make_image_filename_from_mtime(base_dir: Path, src_file: Path,
                                   prefix: str = "image-") -> str:
    """Generate {prefix}YYYYMMDDHHMMSSnnn.EXT — uses src_file's mtime."""
    global _seq
    from datetime import datetime
    ext = src_file.suffix or ".png"
    mtime = src_file.stat().st_mtime
    ts = datetime.fromtimestamp(mtime).strftime("%Y%m%d%H%M%S")
    _seq += 1
    name = f"{prefix}{ts}{_seq % 1000:03d}{ext}"
    while Path(base_dir, name).exists():
        _seq += 1
        name = f"{prefix}{ts}{_seq % 1000:03d}{ext}"
    return name


def is_image_hosting_name(filename: str) -> bool:
    """Check if filename matches the image-YYYYMMDDHHMMSSnnn.EXT pattern."""
    return bool(re.match(r"^image-\d{17}\.", filename))
