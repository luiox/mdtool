from __future__ import annotations
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from libmarkdown.document import Document


class Token:
    """Base class for all AST nodes.

    Every token tracks its original position in the source text.
    When *dirty* is False, serialization emits the original source
    substring verbatim via ``source[start:end]``.
    """

    document: Optional[Document]
    parent: Optional[Token]
    children: List[Token]

    source_start: int   # character offset in source text
    source_end: int     # exclusive
    dirty: bool         # True if the node or any descendant was modified

    def __init__(self) -> None:
        self.document = None
        self.parent = None
        self.children = []
        self.source_start = -1
        self.source_end = -1
        self.dirty = False

    # ── tree navigation ──

    def add_child(self, child: Token, *, dirty: bool = True) -> None:
        child.parent = self
        child.document = self.document
        self.children.append(child)
        if dirty:
            child.dirty = True
            self._mark_dirty()

    def _mark_dirty(self) -> None:
        self.dirty = True
        if self.parent is not None:
            self.parent._mark_dirty()

    # ── serialisation ──

    def serialize(self, source: str) -> str:
        """Return the Markdown source for this node.

        If the node (and all descendants) are clean, the original
        source substring is returned verbatim.
        """
        if not self.dirty and self.source_start >= 0 and self.source_end >= 0:
            return source[self.source_start:self.source_end]
        return self._render()

    def _render(self) -> str:
        """Re-render this node from its data. Subclasses must override."""
        return ""

    def __repr__(self) -> str:
        cls = type(self).__name__
        extra = self._repr_extra()
        dirty_flag = " [dirty]" if self.dirty else ""
        return f"<{cls}{extra}{dirty_flag}>"

    def _repr_extra(self) -> str:
        return ""
