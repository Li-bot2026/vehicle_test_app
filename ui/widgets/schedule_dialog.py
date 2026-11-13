"""
定时执行对话框
===============
设置定时执行测试的时间。
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QDateTimeEdit, QDialogButtonBox,
    QMessageBox, QGroupBox, QComboBox
)
from PyQt6.QtCore import QDateTime, Qt

from loguru import logger

from .dialog_utils import allow_resize


class ScheduleDialog(QDialog):
    """
    定时执行对话框
    ---------------
    选择执行时间，触发定时执行。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("定时执行")
        self.setMinimumWidth(400)
        self.resize(440, 320)
        allow_resize(self)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        group = QGroupBox("执行时间设置")
        form = QVBoxLayout(group)

        form.addWidget(QLabel("选择执行时间:"))

        # 日期时间选择器
        self._datetime = QDateTimeEdit(QDateTime.currentDateTime().addSecs(60))
        self._datetime.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self._datetime.setCalendarPopup(True)
        form.addWidget(self._datetime)

        # 快捷选项
        quick_row = QHBoxLayout()
        for label, seconds in [
            ("1 分钟后", 60),
            ("5 分钟后", 300),
            ("30 分钟后", 1800),
            ("1 小时后", 3600),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(
                lambda _, s=seconds: self._set_quick(s)
            )
            quick_row.addWidget(btn)
        form.addLayout(quick_row)

        layout.addWidget(group)

        # 提示
        hint = QLabel(
            "提示: 定时执行期间请保持应用运行。\n"
            "可以在执行面板随时取消。"
        )
        hint.setStyleSheet("color: #888;")
        layout.addWidget(hint)

        # 按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定定时")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_quick(self, seconds: int):
        """快捷设置时间"""
        target = QDateTime.currentDateTime().addSecs(seconds)
        self._datetime.setDateTime(target)

    def _on_accept(self):
        """校验时间后接受"""
        target = self._datetime.dateTime()
        now = QDateTime.currentDateTime()
        if target <= now:
            QMessageBox.warning(self, "时间错误", "执行时间必须晚于当前时间")
            return
        self.accept()

    def get_datetime(self):
        """获取选择的执行时间"""
        return self._datetime.dateTime().toPyDateTime()
