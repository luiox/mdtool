# libmarkdown

An idempotent Markdown read-write library with AST manipulation.

**Core principle**: reading a Markdown file into AST and writing it back without modifications produces a **byte-identical** output. Only explicitly modified nodes are re-rendered.

## Usage

```python
from libmarkdown import Document

doc = Document.read("input.md")
# ... manipulate AST nodes ...
doc.write("output.md")
```

## Architecture

```
Document
├── BlockToken (base)
│   ├── Heading     (# heading)
│   ├── Paragraph   (text paragraph)
│   ├── ThematicBreak (---, ***, ___)
│   ├── CodeBlock   (fenced ```/~~~)
│   ├── BlockQuote  (> quote)
│   ├── UnorderedList
│   ├── OrderedList
│   ├── HTMLBlock
│   └── Table
└── SpanToken (base)
    ├── Text
    ├── Bold / Italic / Strikethrough
    ├── InlineCode
    ├── Link / Image
    ├── HTMLSpan
    └── LineBreak
```

Each token stores `source_start` / `source_end` (character positions in the original text) and a `dirty` flag. Unmodified tokens are serialized by copying the original substring verbatim.
