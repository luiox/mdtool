"""Line-oriented Markdown → AST parser with source-position tracking."""

from __future__ import annotations
import re
from typing import List, Optional

from libmarkdown.token import Token
from libmarkdown.nodes import (
    Document,
    RawBlock,
    YAMLFrontMatter,
    Heading,
    Paragraph,
    ThematicBreak,
    CodeBlock,
    BlockQuote,
    UnorderedList,
    OrderedList,
    ListItem,
    HTMLBlock,
    Footnote,
    Text,
    EscapeSequence,
    Bold,
    Italic,
    Strikethrough,
    InlineCode,
    Link,
    Image,
    AutoLink,
    HardBreak,
    SoftBreak,
    HTMLSpan,
    MathInline,
    MathDisplay,
    FootnoteRef,
)

# ── helpers ──────────────────────────────────────────────────────────

THEMATIC_BREAK = re.compile(r"^[ \t]*(-{3,}|\*{3,}|_{3,})[ \t]*$")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)(?:\s+#+\s*)?$")
FENCED_START = re.compile(r"^( {0,3})(`{3,}|~{3,})\s*(\S.*)?$")
BLOCKQUOTE_MARK = re.compile(r"^ {0,3}> ?(.*)$")
UNORDERED_MARKER = re.compile(r"^ {0,3}[-*+](?:\s|$)")
ORDERED_MARKER = re.compile(r"^ {0,3}\d{1,9}\.\s+")
BLANK_LINE = re.compile(r"^[ \t]*$")
HTML_BLOCK_OPEN = re.compile(
    r"^ {0,3}</?(?:pre|script|style|div|p|table|tr|td|th|ul|ol|li|"
    r"h[1-6]|blockquote|dl|dt|dd|figure|figcaption|form|fieldset|"
    r"textarea|details|summary)", re.IGNORECASE)
TABLE_SEP = re.compile(r"^ {0,3}\|[ \t]*:?-{3,}:?[ \t]*\|")
FRONT_MATTER_END = re.compile(r"^---\s*$")
HEADING_ID = re.compile(r"^(#{1,6})\s+(.*?)\s+\{#([^}]+)\}\s*$")
FOOTNOTE_DEF = re.compile(r"^ {0,3}\[\^([^\]]+)\]:\s*(.*)$")
MATH_DISPLAY = re.compile(r"^\$\$\s*$")
YAML_FRONT = re.compile(r"^---\s*$")


class Parser:
    def __init__(self) -> None:
        self._text = ""
        self._pos = 0
        self._line = 0
        self._line_start = 0

    def feed(self, text: str, document: Document) -> None:
        self._text = text
        self._pos = 0
        self._line = 1
        self._line_start = 0

        fm = self._parse_front_matter()
        if fm is not None:
            document.add_child(fm, dirty=False)

        _safe, _max_iters = 0, max(1000, len(text))
        while self._pos < len(text):
            _safe += 1
            if _safe > _max_iters:
                if self._pos < len(text):
                    tok = RawBlock()
                    tok.source_start = self._pos
                    tok.source_end = len(text)
                    document.add_child(tok, dirty=False)
                break
            line = self._current_line()
            if not line and self._pos >= len(text):
                break

            tok = (self._parse_heading() or self._parse_thematic_break()
                   or self._parse_fenced_code() or self._parse_blockquote()
                   or self._parse_unordered_list() or self._parse_ordered_list()
                   or self._parse_html_block() or self._parse_footnote_def()
                   or self._parse_paragraph())
            if tok is None:
                tok = self._parse_raw_block()
            if tok is None:
                self._advance_line()
            else:
                document.add_child(tok, dirty=False)

        document.source_start = 0
        document.source_end = len(text)

    # ── line helpers ─────────────────────────────────────────────────

    def _current_line(self) -> str:
        end = self._text.find("\n", self._pos)
        return self._text[self._pos:] if end == -1 else self._text[self._pos:end]

    def _advance_line(self) -> None:
        end = self._text.find("\n", self._pos)
        if end == -1:
            self._pos = len(self._text)
        else:
            self._pos = end + 1
        self._line += 1
        self._line_start = self._pos

    def _peek_line(self) -> str:
        """Return the next line after \n at current position (no split of entire buffer)."""
        end = self._text.find("\n", self._pos)
        if end == -1:
            return ""
        nxt = end + 1
        if nxt >= len(self._text):
            return ""
        nxt_end = self._text.find("\n", nxt)
        return self._text[nxt:nxt_end] if nxt_end != -1 else self._text[nxt:]

    def _skip_blank_lines(self) -> None:
        while self._pos < len(self._text):
            if BLANK_LINE.match(self._current_line()):
                self._advance_line()
            else:
                break

    # ── front matter ─────────────────────────────────────────────────

    def _parse_front_matter(self) -> Optional[YAMLFrontMatter]:
        if not YAML_FRONT.match(self._current_line()):
            return None
        start = self._pos
        self._advance_line()
        lines: List[str] = []
        while self._pos < len(self._text):
            if FRONT_MATTER_END.match(self._current_line()):
                self._advance_line()
                tok = YAMLFrontMatter(content="".join(lines))
                tok.source_start = start
                tok.source_end = self._pos
                return tok
            lines.append(self._current_line() + "\n")
            self._advance_line()
        self._pos = start
        return None

    # ── block parsers ────────────────────────────────────────────────

    def _parse_heading(self) -> Optional[Heading]:
        line = self._current_line()
        m = HEADING_ID.match(line) or HEADING.match(line)
        if not m:
            return None
        level = len(m.group(1))
        content = m.group(2)
        start = self._pos
        self._advance_line()
        tok = Heading(level)
        tok.source_start = start
        tok.source_end = start + len(line)
        for span in self._parse_inline(content):
            tok.add_child(span, dirty=False)
        return tok

    def _parse_thematic_break(self) -> Optional[ThematicBreak]:
        line = self._current_line()
        m = THEMATIC_BREAK.match(line)
        if not m:
            return None
        start = self._pos
        self._advance_line()
        tok = ThematicBreak(m.group(1))
        tok.source_start = start
        tok.source_end = start + len(line)
        return tok

    def _parse_fenced_code(self) -> Optional[CodeBlock]:
        line = self._current_line()
        m = FENCED_START.match(line)
        if not m:
            return None
        fence_char, fence_len = m.group(2)[0], len(m.group(2))
        info = m.group(3) or ""
        start = self._pos
        self._advance_line()
        code_lines, closing = [], fence_char * fence_len
        _max_code = 100000
        while self._pos < len(self._text) and len(code_lines) < _max_code:
            cl = self._current_line()
            if cl.strip() == closing or (cl.strip().startswith(closing) and cl.strip().strip(fence_char) == ""):
                break
            code_lines.append(cl)
            self._advance_line()
        if self._pos < len(self._text):
            self._advance_line()
        tok = CodeBlock(fence=fence_char * fence_len, info=info, content="\n".join(code_lines))
        tok.source_start = start
        tok.source_end = self._pos
        return tok

    def _parse_blockquote(self) -> Optional[BlockQuote]:
        if not BLOCKQUOTE_MARK.match(self._current_line()):
            return None
        start = self._pos
        lines: List[str] = []
        while self._pos < len(self._text):
            line = self._current_line()
            m = BLOCKQUOTE_MARK.match(line)
            if not m:
                if BLANK_LINE.match(line):
                    next_line = self._peek_line()
                    if BLOCKQUOTE_MARK.match(next_line):
                        lines.append("")
                        self._advance_line()
                        continue
                break
            lines.append(m.group(1))
            self._advance_line()
        if not lines:
            return None
        content = "\n".join(lines)
        tok = BlockQuote()
        tok.source_start = start
        tok.source_end = self._pos
        inner_doc = Document()
        sub = Parser()
        sub.feed(content, inner_doc)
        for c in inner_doc.children:
            tok.add_child(c, dirty=False)
        return tok

    def _parse_unordered_list(self) -> Optional[UnorderedList]:
        return self._parse_list(False) if UNORDERED_MARKER.match(self._current_line()) else None

    def _parse_ordered_list(self) -> Optional[OrderedList]:
        return self._parse_list(True) if ORDERED_MARKER.match(self._current_line()) else None

    def _parse_list(self, is_ordered: bool) -> Optional[UnorderedList | OrderedList]:
        start = self._pos
        items: List[tuple] = []
        while self._pos < len(self._text):
            line = self._current_line()
            m = ORDERED_MARKER.match(line) if is_ordered else UNORDERED_MARKER.match(line)
            if m is None and not items:
                return None
            if m is None:
                if items and (line.startswith("    ") or line.startswith("\t") or BLANK_LINE.match(line)):
                    items[-1] = (items[-1][0], items[-1][1], items[-1][2] + "\n" + line)
                    self._advance_line(); continue
                break
            marker, content_start = m.group(0), m.end()
            items.append((marker, content_start, line[content_start:]))
            self._advance_line()
            while self._pos < len(self._text):
                cl = self._current_line()
                if BLANK_LINE.match(cl):
                    next_line = self._peek_line()
                    if (ORDERED_MARKER if is_ordered else UNORDERED_MARKER).match(next_line):
                        break
                    items[-1] = (items[-1][0], items[-1][1], items[-1][2] + "\n")
                    self._advance_line(); continue
                if (ORDERED_MARKER if is_ordered else UNORDERED_MARKER).match(cl):
                    break
                items[-1] = (items[-1][0], items[-1][1], items[-1][2] + "\n" + cl)
                self._advance_line()
        if not items:
            return None
        lst = OrderedList() if is_ordered else UnorderedList()
        lst.source_start, lst.source_end = start, self._pos
        for marker, _, content in items:
            li = ListItem(marker=marker)
            for span in self._parse_inline(content.lstrip("\n").rstrip()):
                li.add_child(span, dirty=False)
            lst.add_child(li, dirty=False)
        return lst

    def _parse_html_block(self) -> Optional[HTMLBlock]:
        line = self._current_line()
        if not HTML_BLOCK_OPEN.match(line):
            return None
        start = self._pos
        lines: List[str] = [line]
        self._advance_line()
        tag = re.match(r"^ {0,3}</?(\w+)", line, re.I)
        close_tag = tag.group(1).lower() if tag else ""
        while self._pos < len(self._text):
            cl = self._current_line()
            lines.append(cl)
            self._advance_line()
            if close_tag and cl.strip().startswith("</" + close_tag):
                break
            if BLANK_LINE.match(cl):
                break
        tok = HTMLBlock(content="\n".join(lines))
        tok.source_start, tok.source_end = start, self._pos
        return tok

    def _parse_footnote_def(self) -> Optional[Footnote]:
        line = self._current_line()
        m = FOOTNOTE_DEF.match(line)
        if not m:
            return None
        label = m.group(1)
        content = m.group(2)
        start = self._pos
        self._advance_line()
        tok = Footnote(label=label, content=content)
        tok.source_start, tok.source_end = start, start + len(line)
        return tok

    def _parse_raw_block(self) -> Optional[RawBlock]:
        lines: List[str] = []
        start = self._pos
        _max_raw = 100000
        while self._pos < len(self._text) and len(lines) < _max_raw:
            line = self._current_line()
            if BLANK_LINE.match(line) or HEADING.match(line) or THEMATIC_BREAK.match(line) or FENCED_START.match(line):
                break
            lines.append(line)
            self._advance_line()
        if not lines:
            return None
        tok = RawBlock()
        tok.source_start, tok.source_end = start, self._pos
        return tok

    def _parse_paragraph(self) -> Optional[Paragraph]:
        lines: List[str] = []
        start = self._pos
        def is_block():
            if self._pos >= len(self._text): return True
            l = self._current_line()
            return bool(HEADING.match(l) or THEMATIC_BREAK.match(l) or FENCED_START.match(l)
                        or BLOCKQUOTE_MARK.match(l) or UNORDERED_MARKER.match(l)
                        or ORDERED_MARKER.match(l) or MATH_DISPLAY.match(l))
        while self._pos < len(self._text):
            if BLANK_LINE.match(self._current_line()) or is_block():
                break
            lines.append(self._current_line())
            self._advance_line()
        if not lines:
            return None
        tok = Paragraph()
        tok.source_start, tok.source_end = start, self._pos
        for span in self._parse_inline("\n".join(lines)):
            tok.add_child(span, dirty=False)
        return tok

    # ── inline parser ────────────────────────────────────────────────

    def _parse_inline(self, text: str) -> List[Token]:
        tokens: List[Token] = []
        i, buf = 0, []
        def flush():
            if buf:
                tokens.append(Text("".join(buf)))
                buf.clear()

        _safe, _max_inline = 0, max(10000, len(text) * 10)
        while i < len(text):
            _safe += 1
            if _safe > _max_inline:
                flush(); tokens.append(Text(text[i:])); break
            c = text[i]

            if c == "\n":
                if i >= 2 and text[i - 2:i] == "  ":
                    flush(); tokens.append(HardBreak())
                else:
                    flush(); tokens.append(SoftBreak())
                i += 1; continue

            if c == "\\" and i + 1 < len(text) and text[i + 1] in r"\`*_{}[]()#+-.!|<>":
                flush(); tokens.append(EscapeSequence(char=text[i + 1]))
                i += 2; continue

            if c == "`":
                bc = 0
                while i + bc < len(text) and text[i + bc] == "`": bc += 1
                close = text.find("`" * bc, i + bc)
                if close != -1 and bc < 3:
                    flush()
                    tokens.append(InlineCode(content=text[i + bc:close], delimiter="`" * bc))
                    i = close + bc; continue

            if text[i:i + 2] == "$$":
                j = text.find("$$", i + 2)
                if j != -1:
                    flush(); tokens.append(MathDisplay(content=text[i + 2:j].strip()))
                    i = j + 2; continue

            if c == "$":
                j = text.find("$", i + 1)
                if j != -1 and j > i + 1:
                    flush(); tokens.append(MathInline(content=text[i + 1:j]))
                    i = j + 1; continue

            if text[i:i + 2] == "![":
                cp = text.find(")", i + 2)
                if cp != -1:
                    ps = text.find("](", i + 2)
                    if ps != -1 and ps < cp:
                        alt, rest = text[i + 2:ps], text[ps + 2:cp]
                        url = rest.split('"')[0].strip() if '"' in rest else rest
                        title = rest.split('"', 1)[1].split('"')[0] if '"' in rest else ""
                        flush(); tokens.append(Image(url=url, title=title, alt=alt))
                        i = cp + 1; continue

            if c == "[":
                cp = text.find(")", i + 1)
                if cp != -1:
                    ps = text.find("](", i + 1)
                    if ps != -1 and ps < cp:
                        inner, rest = text[i + 1:ps], text[ps + 2:cp]
                        url = rest.split('"')[0].strip() if '"' in rest else rest
                        title = rest.split('"', 1)[1].split('"')[0] if '"' in rest else ""
                        flush()
                        tok = Link(url=url, title=title)
                        for s in self._parse_inline(inner): tok.add_child(s, dirty=False)
                        tokens.append(tok); i = cp + 1; continue
                if text[i:i + 2] == "[^":
                    cb = text.find("]", i + 2)
                    if cb != -1:
                        flush(); tokens.append(FootnoteRef(label=text[i + 2:cb]))
                        i = cb + 1; continue

            if c == "<":
                ca = text.find(">", i + 1)
                if ca != -1 and (ca - i) < 200:
                    inner = text[i + 1:ca]
                    if "://" in inner or "@" in inner:
                        flush(); tokens.append(AutoLink(url=inner))
                        i = ca + 1; continue

            if text[i:i + 2] == "~~":
                cs = text.find("~~", i + 2)
                if cs != -1:
                    flush()
                    tok = Strikethrough()
                    for s in self._parse_inline(text[i + 2:cs]): tok.add_child(s, dirty=False)
                    tokens.append(tok); i = cs + 2; continue

            if text[i:i + 2] == "**" and i + 2 < len(text) and text[i + 2] != " ":
                cb = text.find("**", i + 2)
                if cb != -1:
                    flush()
                    tok = Bold(delimiter="**")
                    for s in self._parse_inline(text[i + 2:cb]): tok.add_child(s, dirty=False)
                    tokens.append(tok); i = cb + 2; continue

            if text[i:i + 2] == "__" and i + 2 < len(text) and text[i + 2] != " ":
                cb = text.find("__", i + 2)
                if cb != -1:
                    flush()
                    tok = Bold(delimiter="__")
                    for s in self._parse_inline(text[i + 2:cb]): tok.add_child(s, dirty=False)
                    tokens.append(tok); i = cb + 2; continue

            if c == "*":
                if i + 1 < len(text) and text[i + 1] == "*":
                    buf.append(c); i += 1; continue
                if i + 1 < len(text) and text[i + 1] == " ":
                    buf.append(c); i += 1; continue
                ci = self._find_single(text, "*", i + 1)
                if ci != -1:
                    flush()
                    tok = Italic(delimiter="*")
                    for s in self._parse_inline(text[i + 1:ci]): tok.add_child(s, dirty=False)
                    tokens.append(tok); i = ci + 1; continue

            if c == "_":
                if i + 1 < len(text) and text[i + 1] == "_":
                    buf.append(c); i += 1; continue
                if i + 1 < len(text) and text[i + 1] == " ":
                    buf.append(c); i += 1; continue
                ci = self._find_single(text, "_", i + 1)
                if ci != -1:
                    flush()
                    tok = Italic(delimiter="_")
                    for s in self._parse_inline(text[i + 1:ci]): tok.add_child(s, dirty=False)
                    tokens.append(tok); i = ci + 1; continue

            if c == "<":
                j = text.find(">", i + 1)
                if j != -1 and (j - i) < 200 and (text[i + 1] == "/" or text[i + 1].isalpha()):
                    possible = text[i:j + 1]
                    if re.match(r"^</?[\w-]+[^>]*/?>$", possible):
                        flush(); tokens.append(HTMLSpan(content=possible))
                        i = j + 1; continue

            buf.append(c); i += 1

        flush()
        return tokens

    @staticmethod
    def _find_single(text: str, ch: str, start: int) -> int:
        i = start
        while i < len(text):
            if text[i] == ch:
                if i + 1 < len(text) and text[i + 1] == ch:
                    i += 2; continue
                return i
            i += 1
        return -1
