"""Global log bus — one message stream for every page.

设计动机：旧 UI 每个 tab 自带一个 LogPanel，日志分散且常驻占空间。
重构后页面只管 ``log()``（BaseTab 已路由到这里），主窗口的日志页
订阅本总线统一展示。总线自带环形缓冲，日志页晚于早期消息创建时
也能回放历史。

线程安全：worker 线程经 Qt 信号跨线程 publish，接收方在 GUI 线程，
AutoConnection 自动退化为队列投递；缓冲的读写用锁保护，不依赖连接类型。
"""

import threading
from collections import deque

from PySide6.QtCore import QObject, Signal

_MAX_LINES = 800


class LogBus(QObject):
    """Application-wide log stream. Use :func:`get_log_bus`, not instances."""

    message = Signal(str, str)  # msg, level
    cleared = Signal()

    def __init__(self):
        super().__init__()
        self._buffer = deque(maxlen=_MAX_LINES)
        self._lock = threading.Lock()

    def publish(self, msg: str, level: str = "INFO") -> None:
        with self._lock:
            self._buffer.append((msg, level))
        self.message.emit(msg, level)

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
        self.cleared.emit()

    def snapshot(self) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._buffer)


_bus: LogBus | None = None


def get_log_bus() -> LogBus:
    global _bus
    if _bus is None:
        _bus = LogBus()
    return _bus


def log(msg: str, level: str = "INFO") -> None:
    """Module-level convenience so any code (incl. non-Qt helpers) can log."""
    get_log_bus().publish(msg, level)
