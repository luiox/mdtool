"""Inline parsing tests with AST structure verification."""

from libmarkdown import Document
from libmarkdown.nodes import (
    Text, Bold, Italic, Strikethrough, InlineCode,
    Link, Image, EscapeSequence, AutoLink,
    HardBreak, SoftBreak, HTMLSpan, MathInline, MathDisplay, FootnoteRef,
)


def assert_text(tok, content):
    assert isinstance(tok, Text), f"Expected Text, got {type(tok).__name__}"
    assert tok.content == content, f"Text content: {tok.content!r} != {content!r}"


# ── Bold ────────────────────────────────────────────────────────────

def test_bold_asterisk():
    doc = Document.from_string("**bold**\n")
    c = doc.children[0].children
    assert len(c) == 1
    assert isinstance(c[0], Bold)
    assert len(c[0].children) == 1
    assert_text(c[0].children[0], "bold")

def test_bold_underscore():
    doc = Document.from_string("__bold__\n")
    assert isinstance(doc.children[0].children[0], Bold)

def test_bold_mid_word():
    doc = Document.from_string("un**believe**able\n")
    c = doc.children[0].children
    assert_text(c[0], "un")
    assert isinstance(c[1], Bold)
    assert_text(c[2], "able")


# ── Italic ──────────────────────────────────────────────────────────

def test_italic_asterisk():
    doc = Document.from_string("*italic*\n")
    assert isinstance(doc.children[0].children[0], Italic)

def test_italic_underscore():
    doc = Document.from_string("_italic_\n")
    assert isinstance(doc.children[0].children[0], Italic)

def test_italic_mid_word():
    doc = Document.from_string("mid*word*\n")
    c = doc.children[0].children
    assert_text(c[0], "mid")
    assert isinstance(c[1], Italic)


# ── Bold + Italic ───────────────────────────────────────────────────

def test_bold_italic_overlap():
    doc = Document.from_string("***bold and italic***\n")
    # May parse as Bold containing Italic, or vice versa
    top = doc.children[0].children[0]
    assert isinstance(top, (Bold, Italic))


# ── Strikethrough ───────────────────────────────────────────────────

def test_strikethrough():
    doc = Document.from_string("~~strike~~\n")
    assert isinstance(doc.children[0].children[0], Strikethrough)
    assert_text(doc.children[0].children[0].children[0], "strike")


# ── Inline Code ─────────────────────────────────────────────────────

def test_inline_code():
    doc = Document.from_string("`code`\n")
    assert isinstance(doc.children[0].children[0], InlineCode)
    assert doc.children[0].children[0].content == "code"

def test_inline_code_with_backtick():
    doc = Document.from_string("`` ` ``\n")
    c = doc.children[0].children[0]
    assert isinstance(c, InlineCode)
    # Content between delimiters is kept verbatim (CommonMark trims spaces,
    # but we preserve them for idempotency)
    assert "`" in c.content


# ── Links ───────────────────────────────────────────────────────────

def test_link_basic():
    doc = Document.from_string("[text](http://example.com)\n")
    c = doc.children[0].children[0]
    assert isinstance(c, Link)
    assert c.url == "http://example.com"

def test_link_with_title():
    doc = Document.from_string('[text](http://example.com "Title")\n')
    c = doc.children[0].children[0]
    assert isinstance(c, Link)
    assert c.url == "http://example.com"
    assert c.title == "Title"

def test_link_with_inner_bold():
    doc = Document.from_string("[**bold**](url)\n")
    c = doc.children[0].children[0]
    assert isinstance(c, Link)
    assert len(c.children) == 1
    assert isinstance(c.children[0], Bold)


# ── Images ──────────────────────────────────────────────────────────

def test_image_basic():
    doc = Document.from_string("![alt](img.png)\n")
    c = doc.children[0].children[0]
    assert isinstance(c, Image)
    assert c.alt == "alt"
    assert c.url == "img.png"

def test_image_with_title():
    doc = Document.from_string('![alt](img.png "Title")\n')
    c = doc.children[0].children[0]
    assert isinstance(c, Image)
    assert c.title == "Title"


# ── Escape Sequences ────────────────────────────────────────────────

def test_escape_star():
    doc = Document.from_string(r"\*not italic*\n")
    c = doc.children[0].children
    assert isinstance(c[0], EscapeSequence)
    assert c[0].char == "*"

def test_escape_backtick():
    doc = Document.from_string(r"\`not code\`\n")
    assert isinstance(doc.children[0].children[0], EscapeSequence)

def test_escape_bracket():
    doc = Document.from_string(r"\[not link\]\n")
    assert isinstance(doc.children[0].children[0], EscapeSequence)


# ── Auto Links ──────────────────────────────────────────────────────

def test_autolink_url():
    doc = Document.from_string("<http://example.com>\n")
    assert isinstance(doc.children[0].children[0], AutoLink)

def test_autolink_email():
    doc = Document.from_string("<user@example.com>\n")
    assert isinstance(doc.children[0].children[0], AutoLink)


# ── Line Breaks ─────────────────────────────────────────────────────

def test_hard_break():
    doc = Document.from_string("line1  \nline2\n")
    c = doc.children[0].children
    assert isinstance(c[0], Text)
    assert isinstance(c[1], HardBreak)
    assert isinstance(c[2], Text)


# ── HTML Inline ─────────────────────────────────────────────────────

def test_html_inline_br():
    doc = Document.from_string("text<br>more\n")
    found_html = any(isinstance(t, HTMLSpan) for t in doc.children[0].children)
    assert found_html, "Expected HTMLSpan for <br>"


# ── LaTeX Math ──────────────────────────────────────────────────────

def test_math_inline():
    doc = Document.from_string("$a^2+b^2=c^2$\n")
    assert isinstance(doc.children[0].children[0], MathInline)

def test_math_display():
    doc = Document.from_string("$$\na^2+b^2=c^2\n$$\n")
    # Display math on its own line is handled as a block-level construct
    # (it's a RawBlock that passes through verbatim)
    assert str(doc) == "$$\na^2+b^2=c^2\n$$\n"


# ── Footnote References ─────────────────────────────────────────────

def test_footnote_ref():
    doc = Document.from_string("text[^1]more\n")
    c = doc.children[0].children
    assert isinstance(c[0], Text)
    assert isinstance(c[1], FootnoteRef)
    assert c[1].label == "1"
