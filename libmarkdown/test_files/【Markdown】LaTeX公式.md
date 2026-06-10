---
aliases: 
date: 2023-12-23
tags:
  - Markdown
---
# 简介

对于大部分的Markdown编辑器都是支持LaTex公式的，因此可以大量使用。在Markdown中嵌入LaTeX公式，有两种类型，分别是行内公式和居中公式。

行内公式的格式如下。

```latex
$数学公式$
例子：$\log_{77}a$
```

显示效果为：$\log_{77}a$



居中公式的格式如下。

```latex
$$
数学公式
$$
例子：
$$
\log_{77}a
$$
```

显示效果为：

$$
\log_{77}a
$$



如果不会写LaTex公式，也不要紧，可以使用在线的LaTex公式编辑器。链接是[https://www.latexlive.com/](https://www.latexlive.com/)。利用在线的LaTex公式编辑器，可视化编辑LaTex公式，然后复制对应的LaTeX公式代码到Markdown编辑器。

LaTex公式符号识别网站：[https://detexify.kirelabs.org/classify.html](https://detexify.kirelabs.org/classify.html)

在LaTex中，`%`为单行注释。



# 注意事项

1. 使用`$`，即行中公式时，`数学公式`与`$`连接处不要有空格，否则公式不会显示。
2. 使用`$$`，即居中公式时，`数学公式`与`$$`连接处可以有空格。
3. 使用`$$`时，上方要空一行。
4. `=`不要单独打一行，否则可能会出错。
5. `+ - * / = ( ) | , . '`等符号直接在`$`或`$$`之间输入即可识别。



# 参考资料

1. 官方文档：[https://math.meta.stackexchange.com/questions/5020/mathjax-basic-tutorial-and-quick-reference](https://math.meta.stackexchange.com/questions/5020/mathjax-basic-tutorial-and-quick-reference)
2. 中文教程： [https://www.jianshu.com/p/25f0139637b7](https://www.jianshu.com/p/25f0139637b7) 



