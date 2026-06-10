---
aliases: 
date: 2023-12-21
tags:
  - Markdown
---
# 概述

对于大部分的Markdown编辑器都是支持内嵌HTML的，因此可以大量使用。从根本上来讲，Markdown设计之处就是要方便在浏览器内显示的，因此，Markdown的编辑器一般都是把Markdown转换为HTML以后再由浏览器渲染出来。因此使用HTML是一个美化Markdown的一个好方式。如需使用 HTML，不需要额外标注这是 HTML 或是 Markdown，只需 HTML 标签添加到 Markdown 文本中即可。



# 语法

## 分页

用Markdown撰写各种文档是很方便的事情，但是有时候需要强制开启新的一页，这个时候就需要使用HTML了。

```html
<div STYLE="page-break-after: always;"></div>
```



## 文本字体和颜色

利用HTML的`<font>`标签对部分文字进行自定义字体和颜色。

```markdown
包括新泽西州、康涅狄格州以及纽约市在内许多地区的法律都<font color="red">明确禁止</font>居民把电子废品当成一般垃圾来丢弃。
```



# 注意事项

对于 HTML 的块级元素 `<div>`、`<table>`、`<pre>` 和 `<p>`，请在其前后使用空行（blank lines）与其它内容进行分隔。尽量不要使用制表符（tabs）或空格（spaces）对 HTML 标签做缩进，否则将影响格式。

在 HTML 块级标签内不能使用 Markdown 语法。例如 `<p>italic and **bold**</p>` 将不起作用。