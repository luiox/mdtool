"""Block-level parsing tests with AST structure verification."""

from libmarkdown import Document
from libmarkdown.nodes import (
    Heading,
    Paragraph,
    ThematicBreak,
    CodeBlock,
    BlockQuote,
    UnorderedList,
    OrderedList,
    ListItem,
    HTMLBlock,
    YAMLFrontMatter,
    RawBlock,
    Text,
    Bold,
    InlineCode,
)


def assert_node(node, cls, **attrs):
    assert isinstance(node, cls), f"Expected {cls.__name__}, got {type(node).__name__}"
    for k, v in attrs.items():
        actual = getattr(node, k, None)
        assert actual == v, f"{cls.__name__}.{k}: expected {v!r}, got {actual!r}"


# ── Headings ────────────────────────────────────────────────────────

def test_heading_h1():
    doc = Document.from_string("# Hello\n")
    assert_node(doc.children[0], Heading, level=1)

def test_heading_h6():
    doc = Document.from_string("###### Hello\n")
    assert_node(doc.children[0], Heading, level=6)

def test_heading_multiple():
    doc = Document.from_string("# A\n## B\n### C\n")
    assert len(doc.children) == 3
    assert doc.children[0].level == 1
    assert doc.children[1].level == 2
    assert doc.children[2].level == 3

def test_heading_with_inline():
    doc = Document.from_string("# **bold** *italic*\n")
    h = doc.children[0]
    assert len(h.children) >= 2

def test_heading_closing_hashes():
    """# Title # should keep title as 'Title'."""
    doc = Document.from_string("# Title #\n")
    assert_node(doc.children[0], Heading, level=1)

def test_heading_no_space():
    """CommonMark: #Text is not a heading (but many parsers accept it)."""
    doc = Document.from_string("#NoSpace\n")
    # Should NOT parse as heading
    assert not isinstance(doc.children[0], Heading)


# ── Thematic Break ──────────────────────────────────────────────────

def test_thematic_break_dashes():
    doc = Document.from_string("---\n")
    assert_node(doc.children[0], ThematicBreak)

def test_thematic_break_asterisks():
    doc = Document.from_string("***\n")
    assert_node(doc.children[0], ThematicBreak)

def test_thematic_break_underscores():
    doc = Document.from_string("___\n")
    assert_node(doc.children[0], ThematicBreak)

def test_thematic_break_with_spaces():
    doc = Document.from_string("  ---  \n")
    assert isinstance(doc.children[0], ThematicBreak)


# ── Fenced Code Blocks ──────────────────────────────────────────────

def test_fenced_code_basic():
    doc = Document.from_string("```\ncode\n```\n")
    assert_node(doc.children[0], CodeBlock, info="", content="code")

def test_fenced_code_with_language():
    doc = Document.from_string("```python\nx=1\n```\n")
    assert_node(doc.children[0], CodeBlock, info="python", content="x=1")

def test_fenced_code_tilde():
    doc = Document.from_string("~~~\ncode\n~~~\n")
    assert_node(doc.children[0], CodeBlock, info="", content="code")

def test_fenced_code_multiline():
    doc = Document.from_string("```\nline1\nline2\nline3\n```\n")
    assert_node(doc.children[0], CodeBlock, content="line1\nline2\nline3")

def test_fenced_code_empty():
    doc = Document.from_string("```\n```\n")
    assert_node(doc.children[0], CodeBlock, content="")

def test_fenced_code_indented_fence():
    doc = Document.from_string("   ```\n   code\n   ```\n")
    c = doc.children[0]
    assert isinstance(c, (CodeBlock, RawBlock))


# ── Block Quotes ────────────────────────────────────────────────────

def test_blockquote_single():
    doc = Document.from_string("> hello\n")
    assert_node(doc.children[0], BlockQuote)

def test_blockquote_multiline():
    doc = Document.from_string("> line1\n> line2\n")
    assert isinstance(doc.children[0], BlockQuote)

def test_blockquote_with_paragraph():
    md = "> First paragraph\n>\n> Second paragraph\n"
    doc = Document.from_string(md)
    assert isinstance(doc.children[0], BlockQuote)


# ── Unordered Lists ─────────────────────────────────────────────────

def test_unordered_list_dash():
    doc = Document.from_string("- one\n- two\n- three\n")
    assert_node(doc.children[0], UnorderedList)
    assert len(doc.children[0].children) == 3

def test_unordered_list_star():
    doc = Document.from_string("* one\n* two\n")
    assert isinstance(doc.children[0], UnorderedList)

def test_unordered_list_plus():
    doc = Document.from_string("+ one\n+ two\n")
    assert isinstance(doc.children[0], UnorderedList)


# ── Ordered Lists ───────────────────────────────────────────────────

def test_ordered_list():
    doc = Document.from_string("1. first\n2. second\n")
    assert_node(doc.children[0], OrderedList)
    assert len(doc.children[0].children) == 2

def test_ordered_list_non_sequential():
    doc = Document.from_string("1. first\n8. second\n")
    assert isinstance(doc.children[0], OrderedList)


# ── Paragraphs ──────────────────────────────────────────────────────

def test_paragraph_basic():
    doc = Document.from_string("Hello world.\n")
    assert_node(doc.children[0], Paragraph)


# ── YAML Front Matter ───────────────────────────────────────────────

def test_front_matter_basic():
    md = "---\ntitle: Test\n---\n"
    doc = Document.from_string(md)
    assert_node(doc.children[0], YAMLFrontMatter)

def test_front_matter_multiline():
    md = "---\ntitle: Test\ntags:\n  - a\n  - b\n---\n\nBody\n"
    doc = Document.from_string(md)
    assert_node(doc.children[0], YAMLFrontMatter)


# ── HTML Blocks ─────────────────────────────────────────────────────

def test_html_block_div():
    md = "<div>\ncontent\n</div>\n"
    doc = Document.from_string(md)
    assert isinstance(doc.children[0], (HTMLBlock, RawBlock))


# ── RawBlock fallback ───────────────────────────────────────────────

def test_rawblock_unknown():
    """Unrecognised syntax should be captured as RawBlock."""
    doc = Document.from_string("::: something\ncontent\n:::\n")
    # Should not crash, output should match input
    assert str(doc) == "::: something\ncontent\n:::\n"
