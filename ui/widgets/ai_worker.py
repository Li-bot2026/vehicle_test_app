"""
AI 后台任务
============
把耗时的 AI 调用放到 QThread 中执行，避免界面卡顿。
"""

from PyQt6.QtCore import QThread, pyqtSignal


class AIWorker(QThread):
    """
    执行一个无参可调用对象，结果通过信号回传。

    线程对象存放在类级注册表中，即使调用方（对话框）提前关闭也不会被回收。
    """

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    _registry = set()

    def __init__(self, func, parent=None):
        super().__init__(parent)
        self._func = func
        AIWorker._registry.add(self)
        self.finished.connect(self._on_finished)

    def run(self):
        try:
            result = self._func()
        except Exception as e:
            self.failed.emit(str(e) or e.__class__.__name__)
        else:
            self.succeeded.emit(result)

    def _on_finished(self):
        AIWorker._registry.discard(self)
        self.deleteLater()
