# libmarkdown Feature Plan

Based on analysis of 4 test files covering 1,231 lines of Markdown.
Priority: **P0** = must-roundtrip  **P1** = important  **P2** = nice-to-have

---

## Phase 1 — Core Block Syntax (P0)

| Feature | Test evidence | Status |
|---------|--------------|--------|
| **Paragraph** | `【Markdown】常用语法.md` entire doc | ✅ done |
| **Heading (ATX)** `#`…`######` | 常用语法.md — heading section | ✅ done |
| **Thematic Break** `---` `***` `___` | 常用语法.md — 分隔线 section | ✅ done |
| **Fenced Code Block** `` ``` `` / `~~~` + info string | 常用语法.md + 拓展语法.md | ✅ done |
| **Inline Code** `` `code` `` | 常用语法.md — 代码 section | ✅ partial |
| **Escape Sequences** `\*` `\` ` ` `\!` etc. | 常用语法.md — 转义字符 section | ❌ |
| **RawBlock** (catch-all for unrecognised syntax) | All files — ensures idempotency | ❌ |

## Phase 2 — Core Inline Syntax (P0)

| Feature | test evidence | Status |
|---------|--------------|--------|
| **Text** (plain text runs) | everywhere | ✅ done |
| **Bold** `**text**` / `__text__` | 常用语法.md — 粗体 | ❌ inline parser |
| **Italic** `*text*` / `_text_` | 常用语法.md — 斜体 | ❌ inline parser |
| **Bold+Italic** `***` `___` `__*` `**__` | 常用语法.md — 粗斜体 | ❌ |
| **Link** `[text](url "title")` | 常用语法.md — 链接 | ❌ |
| **Image** `![alt](url "title")` | 常用语法.md — 图片 | ❌ |
| **Hard Line Break** `  \n` / `\n` / `<br>` | 常用语法.md — 换行 | ❌ |

## Phase 3 — Lists & Quotes (P1)

| Feature | Test evidence | Status |
|---------|--------------|--------|
| **Unordered List** `-` / `*` / `+` | 常用语法.md — 无序列表 | ❌ |
| **Ordered List** `1.` `2.` … | 常用语法.md — 有序列表 | ❌ |
| **Nested Lists** (indent sub-lists) | 常用语法.md — 嵌套列表 | ❌ |
| **List + Paragraph** (continue list after blank line) | 常用语法.md — 列表中嵌套段落 | ❌ |
| **List + Code Block** (8-space indent inside list) | 常用语法.md — 列表中代码块 | ❌ |
| **BlockQuote** `>` / `>>` / multi-paragraph | 常用语法.md — 引用 | ❌ |
| **BlockQuote mixed** (heading, list, code inside `>`) | 常用语法.md — 带元素的引用 | ❌ |

## Phase 4 — GFM / Extended Syntax (P1)

| Feature | Test evidence | Status |
|---------|--------------|--------|
| **Table** (w/ alignment `:---` `:---:` `---:`) | 拓展语法.md — 表格 | ❌ |
| **Strikethrough** `~~text~~` | 拓展语法.md — 删除线 | ❌ |
| **Task List** `- [ ]` / `- [x]` | 拓展语法.md — 任务列表 | ❌ |
| **Fenced Code w/ Mermaid** `` ```mermaid `` etc. | 拓展语法.md — Mermaid | ✅ (generic CodeBlock) |
| **Footnote** `[^1]` / `[^1]: text` | 拓展语法.md — 脚注 | ❌ |
| **Heading ID** `### title {#id}` | 拓展语法.md — 标题编号 | ❌ |
| **Definition List** `Term\n: Definition` | 拓展语法.md — 定义列表 | ❌ |

## Phase 5 — HTML & Special (P1)

| Feature | Test evidence | Status |
|---------|--------------|--------|
| **Inline HTML** `<font>` `<br>` `<div>` etc. | 内嵌HTML.md + 常用语法.md | ❌ |
| **HTML Block** `<div>`…`</div>` standalone | 内嵌HTML.md | ❌ |
| **Auto URL** `<https://…>` bare URLs | 拓展语法.md — 自动链接 | ❌ |
| **Escaping pipe in table** `&#124;` | 拓展语法.md — 表中转义 | ❌ (covered by RawBlock) |
| **Emoji shortcode** `:tent:` `:joy:` | 拓展语法.md — Emoji | ❌ |

## Phase 6 — Meta / Math (P2)

| Feature | Test evidence | Status |
|---------|--------------|--------|
| **YAML Front Matter** `---\nkey: val\n---` | All 3 docs | ❌ |
| **LaTeX inline** `$...$` | LaTeX公式.md | ❌ |
| **LaTeX display** `$$...$$` | LaTeX公式.md | ❌ |
| **TOC** `[TOC]` | 常用语法.md | ❌ |
| **Reference Link** `[text][label]` / `[label]: url` | 常用语法.md — 引用链接 | ❌ |

---

## Architecture Rule: Unknown Syntax

Any construct the parser does not recognise at the block level should be
captured as a **RawBlock** node that records `source_start` / `source_end`
and has `dirty = False` — this guarantees the original text passes through
untouched.

Similarly, unrecognised inline spans should be captured as plain **Text**.

This rule alone makes the library safe to use as a read-write pass-through
even before all syntax features are implemented.
