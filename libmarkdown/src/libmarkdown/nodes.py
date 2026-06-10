"""Concrete Markdown node types.

Each class corresponds to a Markdown syntax construct and knows how to
re-render itself via ``_render()`` when marked dirty.
"""

from __future__ import annotations
from typing import ClassVar, List, Optional
from libmarkdown.token import Token


# ── Block-level nodes ────────────────────────────────────────────────

class BlockToken(Token):
    """Base class for all block-level tokens."""


class RawBlock(BlockToken):
    """Catch-all for unrecognised lines. Outputs original source verbatim."""

    def _render(self) -> str:
        return ""


class YAMLFrontMatter(BlockToken):
    def __init__(self, content: str = "") -> None:
        super().__init__()
        self.content = content

    def _render(self) -> str:
        return f"---\n{self.content}---\n"


class Document(BlockToken):
    """Root node."""

    def _render(self) -> str:
        parts = [c.serialize("") for c in self.children]
        return "".join(parts)


class Heading(BlockToken):
    def __init__(self, level: int) -> None:
        super().__init__()
        self.level = level

    def _render(self) -> str:
        prefix = "#" * self.level + " "
        inner = "".join(c._render() for c in self.children)
        return prefix + inner + "\n"

    def _repr_extra(self) -> str:
        return f" h{self.level}"


class Paragraph(BlockToken):
    def _render(self) -> str:
        inner = "".join(c._render() for c in self.children)
        return inner + "\n"


class ThematicBreak(BlockToken):
    def __init__(self, pattern: str = "---") -> None:
        super().__init__()
        self.pattern = pattern

    def _render(self) -> str:
        return self.pattern + "\n"


class CodeBlock(BlockToken):
    def __init__(self, fence: str = "```", info: str = "", content: str = "") -> None:
        super().__init__()
        self.fence = fence
        self.info = info
        self.content = content

    def _render(self) -> str:
        result = self.fence + self.info + "\n"
        result += self.content
        if not self.content.endswith("\n"):
            result += "\n"
        result += self.fence + "\n"
        return result

    def _repr_extra(self) -> str:
        return f" {self.info or 'code'}"


class BlockQuote(BlockToken):
    def _render(self) -> str:
        inner = "".join(c._render() for c in self.children)
        lines = inner.rstrip("\n").split("\n")
        return "\n".join(f"> {l}" if l else ">" for l in lines) + "\n"


class ListItem(BlockToken):
    def __init__(self, marker: str = "- ") -> None:
        super().__init__()
        self.marker = marker

    def _render(self) -> str:
        inner = "".join(c._render() for c in self.children)
        return self.marker + inner


class UnorderedList(BlockToken):
    def _render(self) -> str:
        return "".join(c._render() for c in self.children)


class OrderedList(BlockToken):
    def __init__(self, start: int = 1) -> None:
        super().__init__()
        self.start = start

    def _render(self) -> str:
        return "".join(c._render() for c in self.children)


class HTMLBlock(BlockToken):
    def __init__(self, content: str = "") -> None:
        super().__init__()
        self.content = content

    def _render(self) -> str:
        return self.content


class Table(BlockToken):
    def __init__(self) -> None:
        super().__init__()
        self.header: List[List[Token]] = []
        self.align: List[str] = []
        self.rows: List[List[List[Token]]] = []

    def _render(self) -> str:
        return "".join(c._render() for c in self.children)


class Footnote(BlockToken):
    def __init__(self, label: str = "", content: str = "") -> None:
        super().__init__()
        self.label = label
        self.content = content

    def _render(self) -> str:
        return f"[^{self.label}]: {self.content}\n"


# ── Span-level nodes ─────────────────────────────────────────────────

class SpanToken(Token):
    """Base class for inline tokens."""


class Text(SpanToken):
    def __init__(self, content: str = "") -> None:
        super().__init__()
        self.content = content

    def _render(self) -> str:
        return self.content

    def _repr_extra(self) -> str:
        return f" {self.content!r}"


class EscapeSequence(SpanToken):
    """An escaped character like \* → renders as the literal character."""

    def __init__(self, char: str = "") -> None:
        super().__init__()
        self.char = char

    def _render(self) -> str:
        return self.char


class Bold(SpanToken):
    def __init__(self, delimiter: str = "**") -> None:
        super().__init__()
        self.delimiter = delimiter

    def _render(self) -> str:
        inner = "".join(c._render() for c in self.children)
        return f"{self.delimiter}{inner}{self.delimiter}"


class Italic(SpanToken):
    def __init__(self, delimiter: str = "*") -> None:
        super().__init__()
        self.delimiter = delimiter

    def _render(self) -> str:
        inner = "".join(c._render() for c in self.children)
        return f"{self.delimiter}{inner}{self.delimiter}"


class Strikethrough(SpanToken):
    def _render(self) -> str:
        inner = "".join(c._render() for c in self.children)
        return f"~~{inner}~~"


class InlineCode(SpanToken):
    def __init__(self, content: str = "", delimiter: str = "`") -> None:
        super().__init__()
        self.content = content
        self.delimiter = delimiter

    def _render(self) -> str:
        if "`" in self.content:
            return f"`` {self.content} ``"
        return f"`{self.content}`"

    def _repr_extra(self) -> str:
        return f" {self.content!r}"


class Link(SpanToken):
    def __init__(self, url: str = "", title: str = "") -> None:
        super().__init__()
        self.url = url
        self.title = title

    def _render(self) -> str:
        alt = "".join(c._render() for c in self.children)
        title_attr = f' "{self.title}"' if self.title else ""
        return f"[{alt}]({self.url}{title_attr})"

    def _repr_extra(self) -> str:
        return f" -> {self.url}"


class Image(SpanToken):
    def __init__(self, url: str = "", title: str = "", alt: str = "") -> None:
        super().__init__()
        self.url = url
        self.title = title
        self.alt = alt

    def _render(self) -> str:
        title_attr = f' "{self.title}"' if self.title else ""
        return f"![{self.alt}]({self.url}{title_attr})"

    def _repr_extra(self) -> str:
        return f" ![{self.alt}]({self.url})"


class AutoLink(SpanToken):
    def __init__(self, url: str = "") -> None:
        super().__init__()
        self.url = url

    def _render(self) -> str:
        return f"<{self.url}>"


class HardBreak(SpanToken):
    def _render(self) -> str:
        return "  \n"


class SoftBreak(SpanToken):
    def _render(self) -> str:
        return "\n"


class HTMLSpan(SpanToken):
    def __init__(self, content: str = "") -> None:
        super().__init__()
        self.content = content

    def _render(self) -> str:
        return self.content


class MathInline(SpanToken):
    def __init__(self, content: str = "") -> None:
        super().__init__()
        self.content = content

    def _render(self) -> str:
        return f"${self.content}$"


class MathDisplay(SpanToken):
    def __init__(self, content: str = "") -> None:
        super().__init__()
        self.content = content

    def _render(self) -> str:
        return f"$$\n{self.content}\n$$"


class FootnoteRef(SpanToken):
    def __init__(self, label: str = "") -> None:
        super().__init__()
        self.label = label

    def _render(self) -> str:
        return f"[^{self.label}]"


# Alias for backward compatibility
LineBreak = HardBreak
