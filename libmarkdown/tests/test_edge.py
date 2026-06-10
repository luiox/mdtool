"""Edge-case tests — unclosed delimiters, nesting, empty content, etc."""

from libmarkdown import Document
from libmarkdown.nodes import (
    Text, Bold, Italic, InlineCode, Link, Image,
)


def assert_text(tok, content):
    assert isinstance(tok, Text), f"Expected Text, got {type(tok).__name__}"
    assert tok.content == content


# ── Unclosed delimiters (should degrade gracefully to text) ─────────

def test_unclosed_bold():
    """**text without closing should be treated as literal text."""
    doc = Document.from_string("**unclosed\n")
    c = doc.children[0].children
    # Should be text, not Bold
    assert not any(isinstance(t, Bold) for t in c)

def test_unclosed_italic():
    doc = Document.from_string("*unclosed\n")
    assert not any(isinstance(t, Italic) for t in doc.children[0].children)

def test_unclosed_code():
    doc = Document.from_string("`unclosed\n")
    assert not any(isinstance(t, InlineCode) for t in doc.children[0].children)

def test_unclosed_link():
    doc = Document.from_string("[unclosed\n")
    assert not any(isinstance(t, Link) for t in doc.children[0].children)

def test_unclosed_image():
    doc = Document.from_string("![unclosed\n")
    assert not any(isinstance(t, Image) for t in doc.children[0].children)


# ── Empty content ───────────────────────────────────────────────────

def test_empty_bold():
    doc = Document.from_string("a **** b\n")
    c = doc.children[0].children
    assert any(isinstance(t, Bold) for t in c)



def test_empty_heading():
    doc = Document.from_string("# \n")
    # Should be a heading with empty content, or a RawBlock
    assert len(doc.children) >= 1


# ── Nested / Mixed ──────────────────────────────────────────────────

def test_bold_inside_italic():
    doc = Document.from_string("*italic **bold** inside*\n")
    top = doc.children[0].children[0]
    assert isinstance(top, Italic)
    has_bold = any(isinstance(t, Bold) for t in top.children)
    assert has_bold

def test_italic_inside_bold():
    doc = Document.from_string("**bold *italic* inside**\n")
    top = doc.children[0].children[0]
    assert isinstance(top, Bold)
    has_italic = any(isinstance(t, Italic) for t in top.children)
    assert has_italic

def test_link_inside_bold():
    doc = Document.from_string("**[link](url)**\n")
    top = doc.children[0].children[0]
    assert isinstance(top, Bold)
    link_found = any(isinstance(t, Link) for t in top.children)
    assert link_found

def test_code_inside_paragraph():
    doc = Document.from_string("text `code` more `code2` end\n")
    c = doc.children[0].children
    inline_count = sum(1 for t in c if isinstance(t, InlineCode))
    assert inline_count == 2


# ── Fenced code special cases ───────────────────────────────────────

def test_fenced_code_empty_info():
    doc = Document.from_string("```\ncontent\n```\n")
    assert str(doc) == "```\ncontent\n```\n"

def test_fenced_code_no_closing():
    """Missing closing fence: treated as RawBlock or equivalent."""
    md = "```\nopen fence\n"
    doc = Document.from_string(md)
    # Should not crash
    _ = str(doc)

def test_fenced_code_with_backticks_inside():
    md = "```\n`inline` inside\n```\n"
    doc = Document.from_string(md)
    # CodeBlock should contain the literal backticks
    c = doc.children[0]
    assert str(doc) == md


# ── Lists special cases ─────────────────────────────────────────────

def test_list_with_empty_items():
    md = "- one\n-\n- three\n"
    doc = Document.from_string(md)
    assert str(doc) == md

def test_nested_lists():
    md = "- outer\n  - inner\n- outer2\n"
    doc = Document.from_string(md)
    assert str(doc) == md


# ── Blockquote special cases ────────────────────────────────────────

def test_blockquote_empty():
    md = ">\n"
    doc = Document.from_string(md)
    assert str(doc) == md

def test_blockquote_nested_markers():
    md = "> > nested\n"
    doc = Document.from_string(md)
    assert str(doc) == md


# ── Escape sequences in context ─────────────────────────────────────

def test_escape_in_list():
    """Escaped dash inside list item should not break parsing."""
    md = "- \\* not a list\n"
    doc = Document.from_string(md)
    assert str(doc) == md


# ── RawBlock passthrough ────────────────────────────────────────────

def test_rawblock_preserves_unknown():
    """Completely unknown syntax should pass through unchanged."""
    md = ":::something\ncontent\n:::\n"
    doc = Document.from_string(md)
    assert str(doc) == md


# ── Multiple blank lines ────────────────────────────────────────────

def test_preserve_double_blank():
    md = "para1\n\n\npara2\n"
    doc = Document.from_string(md)
    assert str(doc) == md

def test_preserve_triple_blank():
    md = "a\n\n\n\nb\n"
    doc = Document.from_string(md)
    assert str(doc) == md


# ── Leading/trailing whitespace in code ─────────────────────────────

def test_code_preserves_indent():
    md = "```\n    indented\n```\n"
    doc = Document.from_string(md)
    assert str(doc) == md
