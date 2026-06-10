"""Round-trip idempotency tests — read → write → compare byte-for-byte."""

from pathlib import Path
import pytest
from libmarkdown import Document

TEST_DIR = Path(__file__).resolve().parent.parent / "test_files"


def test_roundtrip_all_files():
    """Every .md file in test_files survives a read/write round-trip."""
    for path in sorted(TEST_DIR.glob("**/*.md")):
        original = path.read_text(encoding="utf-8")
        doc = Document.read(path)
        output = str(doc)
        assert output == original, (
            f"{path.name}: round-trip differs ({len(original)}B vs {len(output)}B)"
        )


def test_roundtrip_empty():
    doc = Document.from_string("")
    assert str(doc) == ""


def test_roundtrip_only_newline():
    doc = Document.from_string("\n")
    assert str(doc) == "\n"


def test_roundtrip_only_whitespace():
    doc = Document.from_string("   \n  \n")
    assert str(doc) == "   \n  \n"


def test_roundtrip_front_matter():
    md = "---\ntitle: Test\n---\n\nHello"
    doc = Document.from_string(md)
    assert str(doc) == md


def test_roundtrip_front_matter_no_body():
    md = "---\nkey: val\n---\n"
    doc = Document.from_string(md)
    assert str(doc) == md


def test_roundtrip_multiple_blank_lines():
    md = "# A\n\n\n\n# B\n"
    doc = Document.from_string(md)
    assert str(doc) == md

def test_roundtrip_validate_all_notes():
    NOTE_ROOT = Path("D:/Canrad/notes")
    if not NOTE_ROOT.exists():
        pytest.skip(f"{NOTE_ROOT} not found")
    failed = []
    total = 0

    for md in sorted(NOTE_ROOT.rglob("*.md")):
        total += 1
        print(f"  [{total}] {md.relative_to(NOTE_ROOT)}", end="")
        try:
            original = md.read_text(encoding="utf-8")
            doc = Document.read(md)
            roundtrip = str(doc)
            if original != roundtrip:
                failed.append((md, original, roundtrip))
                print("  DIFF")
            else:
                print(f"  OK ({len(original)}B)")
        except Exception as e:
            failed.append((md, None, str(e)))
            print(f"  ERROR: {e}")

    print()
    if failed:
        print(f"Failed {len(failed)}/{total} files:")
        for f, orig, rt in failed[:10]:
            print(f"  {f.name}: {type(rt).__name__ if orig is None else 'content differs'}")
            if orig is not None:
                # Show first difference
                for i, (a, b) in enumerate(zip(orig, rt)):
                    if a != b:
                        print(f"    byte {i}: {repr(a)} vs {repr(b)}")
                        break
                if len(orig) != len(rt):
                    print(f"    length: {len(orig)} vs {len(rt)}")
        assert False, f"{len(failed)} files failed"
    else:
        print(f"All {total} files passed!")