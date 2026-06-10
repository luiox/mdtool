"""AST → Markdown string serialisation.

The key idempotency guarantee comes from ``Token.serialize(source)``:
if the token (and all its descendants) are *clean*, the original source
substring is emitted verbatim.
"""

from __future__ import annotations
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from libmarkdown.document import Document

from libmarkdown.token import Token


class Serializer:
    """Walks the AST and produces final Markdown text."""

    def __init__(self, document: Document) -> None:
        self.document = document
        self.source = document.source
        self._parts: List[str] = []

    def serialize(self) -> str:
        self._parts.clear()
        self._serialize_node(self.document)
        return "".join(self._parts)

    def _serialize_node(self, token: Token) -> None:
        if not token.dirty and token.source_start >= 0 and token.source_end >= 0:
            # Emit verbatim original source
            self._parts.append(self.source[token.source_start:token.source_end])
            return

        # Dirty node: re-render
        self._parts.append(token._render())
