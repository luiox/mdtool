"""站点本地预览——stdlib 线程化静态服务器。

frozen 打包后没有独立的 python 子解释器，hexo 时代 ``npm run server`` 的
预览方案不可移植；这里用 http.server 在守护线程里服务输出目录，随桌面端
进程存活（tab shutdown 时停）。端口绑 0 由内核分配空闲口，``start()``
返回实际端口——零配置且绝不与 8765/4000 冲突。只绑 127.0.0.1：预览是
本机行为，与"手机端 8765 双端契约"无关。
"""

from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread


class _QuietHandler(SimpleHTTPRequestHandler):
    """访问日志静默——桌面日志页只需要"预览已启动"，不要每请求一行。"""

    def log_message(self, format, *args):  # noqa: A002 - stdlib 签名
        pass


class SitePreview:
    """一个目录的本地预览服务器。可 start/stop 多次，start 幂等。"""

    def __init__(self, root: Path):
        self._root = Path(root)
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def url(self) -> str:
        port = self.port
        return f"http://127.0.0.1:{port}/" if port else ""

    @property
    def port(self) -> int:
        if self._server is None:
            return 0
        return self._server.server_address[1]

    def start(self) -> int:
        """启动并返回实际端口；已在运行则直接返回现有端口。"""
        if self._server is not None:
            return self.port
        handler = partial(_QuietHandler, directory=str(self._root))
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = Thread(target=self._server.serve_forever,
                              name="sitegen-preview", daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        """停服并等待线程退出；未运行时调用是 no-op（shutdown 幂等兜底）。"""
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
