# markdown_util

markdown图片链接相关的工具

1. markdown视图打包导出独立zip
基础功能，按照我的这个规范的可以直接导出zip
拓展功能，智能分析markdown图片链接，自动修整链接后导出zip

2. 图片链接验证器
验证图片链接是否有效

3. 图片搜索整理器
在特定目录内找出链接的图片，并且按照所需链接组织为文件夹格式

4. 链接规整器

主要是我需要一个规整器，就是把这个规整链接的功能直接放进我的这个markdown图片工具，这样子我就可以肆无忌惮随便从其他地方复制粘贴进typora而且图片是UUID这种也能自动规则回来

智能图片链接管理，这样子我仅需偶尔gc一下，自动搜索失效的图片，链接失去图片，这样子很容易就能解决问题，彻底避免丢图片，图片垃圾的问题

typora-uploader是对应给typora上传给本地图床用的工具


未来功能规划

导出器增加对本地图床服务器链接的支持，支持单个markdown导出zip，也支持批量多个导出zip包。


打包构建单exe

有两个入口：

- `main.py` — 旧 tkinter 版（高 DPI 下模糊，保留作为回退）
- `main_qt.py` — PySide6 版（推荐，高 DPI 清晰）

打包推荐用 Qt 版入口：

```shell
uv run pyinstaller MarkdownUtilQt.spec
```

或者直接用命令行（效果等同）：

```shell
uv run pyinstaller --onefile --noconsole --name MarkdownUtilQt --hidden-import server.meta_db --hidden-import server.media_server --hidden-import server.notes_db --hidden-import qtui.main_window --hidden-import qtui.widgets --hidden-import qtui.icons --hidden-import qtui.workers --hidden-import qtui.tabs.notes_browser --hidden-import qtui.tabs.file_browser --hidden-import qtui.tabs.media_server --hidden-import qtui.tabs.space_fix --hidden-import qtui.tabs.image_check --hidden-import qtui.tabs.migrate --collect-all libmarkdown --collect-all mistletoe --collect-all PIL --collect-all watchdog --collect-all PySide6 main_qt.py
```

如需打包旧 tkinter 版，把最后的 `main_qt.py` 换成 `main.py` 并按需调整 `--collect-all`。
