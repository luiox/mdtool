"""Public API — the ``Document`` class."""

from __future__ import annotations
from pathlib import Path
from typing import Optional, Union

from libmarkdown.token import Token
from libmarkdown.parser import Parser
from libmarkdown.serializer import Serializer


class Document(Token):
    """Represents an entire Markdown document.

    Usage::

        doc = Document.read("path/to/file.md")
        # … inspect / modify AST nodes …
        doc.write("path/to/output.md")

    For a no-op read/write round-trip the output will be **byte-identical**
    to the input.
    """

    #: Original source text. Kept so clean tokens can emit verbatim slices.
    source: str

    #: Absolute path of the source file (or None if created from string).
    source_path: Optional[Path]

    def __init__(self) -> None:
        super().__init__()
        self.source = ""
        self.source_path = None
        self.document = self  # Document is its own document

    # ── I/O ──────────────────────────────────────────────────────────

    @classmethod
    def read(cls, path: Union[str, Path]) -> Document:
        """Read a Markdown file and parse it into an AST."""
        path = Path(path)
        raw = path.read_text(encoding="utf-8")
        doc = cls()
        doc.source = raw
        doc.source_path = path.resolve()
        doc.source_start = 0
        doc.source_end = len(raw)

        parser = Parser()
        parser.feed(raw, doc)

        return doc

    @classmethod
    def from_string(cls, text: str) -> Document:
        """Parse Markdown text into an AST (no file involved)."""
        doc = cls()
        doc.source = text
        doc.source_start = 0
        doc.source_end = len(text)

        parser = Parser()
        parser.feed(text, doc)

        return doc

    def write(self, path: Union[str, Path]) -> None:
        """Write the (possibly modified) AST back to a file."""
        Path(path).write_text(self._render(), encoding="utf-8")

    # ── serialisation ────────────────────────────────────────────────

    def _render(self) -> str:
        ser = Serializer(self)
        return ser.serialize()

    def __str__(self) -> str:
        return self._render()

    # ── helpers ──────────────────────────────────────────────────────

    @property
    def body(self) -> list:
        """Shortcut for ``doc.children`` (the block-level children)."""
        return self.children

    def pretty(self) -> str:
        """Return a human-readable tree dump."""
        lines: list[str] = []

        def walk(tok: Token, indent: int = 0) -> None:
            lines.append("  " * indent + str(tok))
            for c in tok.children:
                walk(c, indent + 1)

        walk(self)
        return "\n".join(lines)
