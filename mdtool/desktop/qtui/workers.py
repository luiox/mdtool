"""Thread-pool worker utilities.

The tkinter version ran every batch operation (``migrate_all``,
``image_check.start_check``, ``import_folder``) on the main thread and kept the
UI alive with ``self.frame.update()`` calls — which stutters on large folders.
Here each long job is a :class:`GenericWorker` :class:`QRunnable`; progress and
log lines flow back as Qt signals (auto-marshalled to the GUI thread), and the
GUI thread stays responsive.

The worker function receives a ``report`` callback it calls during the work
to emit progress/log without knowing about Qt.
"""

from typing import Callable, Optional

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    progress = Signal(int, int)   # current, total
    log = Signal(str, str)        # message, level
    finished = Signal(object)     # arbitrary result payload
    error = Signal(str)


def _null_report(*args, **kwargs):
    pass


class GenericWorker(QRunnable):
    """Run ``fn(*args, **kwargs)`` in the thread pool.

    ``fn`` may optionally accept a ``report`` keyword; if so it is given a
    callable with signature ``report(stage, **payload)`` where ``stage`` is one
    of ``"progress"`` (current:int, total:int) or ``"log"`` (msg:str,
    level:str). Exceptions are caught and emitted via :attr:`signals.error`.
    """

    signals = WorkerSignals()

    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        # Each instance needs its own signals (QRunnable can't own signals
        # directly), so override the class attribute with a per-instance one.
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self):
        def report(stage: str, **payload):
            if stage == "progress":
                self.signals.progress.emit(int(payload.get("current", 0)),
                                           int(payload.get("total", 0)))
            elif stage == "log":
                self.signals.log.emit(str(payload.get("msg", "")),
                                      str(payload.get("level", "INFO")))

        try:
            # Inject report only if the callable declares it.
            try:
                import inspect
                params = inspect.signature(self._fn).parameters
                kwargs = dict(self._kwargs)
                if "report" in params and "report" not in kwargs:
                    kwargs["report"] = report
            except (TypeError, ValueError):
                kwargs = self._kwargs

            result = self._fn(*self._args, **kwargs)
            self.signals.finished.emit(result)
        except Exception as e:  # noqa: BLE001 - worker boundary
            self.signals.error.emit(str(e))


def start_worker(fn: Callable, *,
                 on_progress: Optional[Callable] = None,
                 on_log: Optional[Callable] = None,
                 on_finished: Optional[Callable] = None,
                 on_error: Optional[Callable] = None,
                 args=(), kwargs=None, **fn_kwargs) -> GenericWorker:
    """Convenience: build a worker, wire signals, and start it on the pool.

    ``fn`` 的参数既可以用 ``kwargs=`` 字典传，也可以直接作为关键字参数
    散传（后者与所有既有调用点的书写习惯一致）。Returns the worker
    (keep a reference if you need to manage its lifetime).
    """
    from PySide6.QtCore import QThreadPool
    merged = dict(kwargs or {})
    merged.update(fn_kwargs)
    worker = GenericWorker(fn, *args, **merged)
    if on_progress:
        worker.signals.progress.connect(on_progress)
    if on_log:
        worker.signals.log.connect(on_log)
    if on_finished:
        worker.signals.finished.connect(on_finished)
    if on_error:
        worker.signals.error.connect(on_error)
    QThreadPool.globalInstance().start(worker)
    return worker
