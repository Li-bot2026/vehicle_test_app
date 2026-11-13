"""
AI 失败诊断对话框
==================
用例执行失败时弹出：先自动分析失败原因，再给出解决方法，
最后询问用户是否让 AI 直接修复并重试。
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QListWidget, QListWidgetItem, QTextBrowser,
    QMessageBox, QApplication, QWidget
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor

from loguru import logger

from .ai_worker import AIWorker
from .dialog_utils import allow_resize


class AIErrorDialog(QDialog):
    """
    失败诊断对话框
    ---------------
    打开后立即开始分析，分析完成前禁用修复按钮。
    """

    def __init__(self, assistant, failure_info: dict, device_manager,
                 auto_start: bool = True, parent=None):
        super().__init__(parent)
        self._assistant = assistant
        self._info = failure_info or {}
        self._dm = device_manager
        self._worker = None
        self._diagnosis = None
        self._cancelled = False

        # 用户勾选的修复动作（对话框被接受后由主窗口读取）
        self.repair_actions = []

        self._init_ui()
        if auto_start:
            QTimer.singleShot(50, self._start_analysis)

    # ---------- 界面 ----------

    def _init_ui(self):
        self.setWindowTitle("AI 失败诊断")
        self.resize(780, 660)
        allow_resize(self)

        layout = QVBoxLayout(self)

        case = self._info.get("case")
        case_name = case.name if case is not None else "未知用例"
        title = QLabel(f"❌ 用例执行失败：{case_name}")
        title.setFont(QFont("Microsoft YaHei", 12, QFont.Weight.Bold))
        title.setStyleSheet("color: #c0392b;")
        layout.addWidget(title)

        step_desc = self._info.get("step_description") or "未定位到具体步骤"
        step_type = self._info.get("step_type") or "-"
        step_idx = self._info.get("step_index", -1)
        prefix = f"步骤 {step_idx + 1}" if isinstance(step_idx, int) and step_idx >= 0 else "步骤"
        meta = QLabel(f"{prefix}：{step_desc}（类型：{step_type}）")
        meta.setStyleSheet("color: #555;")
        layout.addWidget(meta)

        # 错误信息
        err_group = QGroupBox("错误信息")
        err_layout = QVBoxLayout(err_group)
        self._error_browser = QTextBrowser()
        self._error_browser.setMaximumHeight(80)
        self._error_browser.setStyleSheet("background: #fff5f5; color: #a61b1b;")
        self._error_browser.setPlainText(self._info.get("error") or "无详细错误信息")
        err_layout.addWidget(self._error_browser)
        layout.addWidget(err_group)

        # 分析状态
        self._status_label = QLabel("准备诊断...")
        self._status_label.setStyleSheet("color: #8a6d3b;")
        layout.addWidget(self._status_label)

        self._source_label = QLabel("")
        self._source_label.setStyleSheet("color: #888;")
        layout.addWidget(self._source_label)

        # 失败原因
        reason_group = QGroupBox("失败原因")
        reason_layout = QVBoxLayout(reason_group)
        self._reason_browser = QTextBrowser()
        self._reason_browser.setMinimumHeight(80)
        reason_layout.addWidget(self._reason_browser)
        layout.addWidget(reason_group)

        # 解决方法
        solution_group = QGroupBox("解决方法")
        solution_layout = QVBoxLayout(solution_group)
        self._solution_browser = QTextBrowser()
        self._solution_browser.setMinimumHeight(110)
        solution_layout.addWidget(self._solution_browser)
        layout.addWidget(solution_group)

        # 可自动修复的动作
        self._action_group = QGroupBox("AI 可自动修复的动作（勾选后执行）")
        action_layout = QVBoxLayout(self._action_group)
        self._action_list = QListWidget()
        self._action_list.setMaximumHeight(96)
        action_layout.addWidget(self._action_list)
        self._action_hint = QLabel("")
        self._action_hint.setWordWrap(True)
        self._action_hint.setStyleSheet("color: #666;")
        action_layout.addWidget(self._action_hint)
        layout.addWidget(self._action_group)
        self._action_group.setVisible(False)

        # 按钮
        btn_row = QHBoxLayout()
        self._btn_fix = QPushButton("🤖 让 AI 自动修复并重试")
        self._btn_fix.setEnabled(False)
        self._btn_fix.setDefault(True)
        self._btn_fix.clicked.connect(self._on_fix_clicked)
        btn_row.addWidget(self._btn_fix)

        self._btn_copy = QPushButton("📋 复制诊断")
        self._btn_copy.setEnabled(False)
        self._btn_copy.clicked.connect(self._on_copy)
        btn_row.addWidget(self._btn_copy)

        btn_row.addStretch()

        self._btn_close = QPushButton("关闭")
        self._btn_close.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_close)

        layout.addLayout(btn_row)

    # ---------- 诊断流程 ----------

    def _start_analysis(self):
        if self._worker is not None:
            return

        case = self._info.get("case")
        if case is None:
            self._on_failed("缺少用例上下文，无法诊断")
            return

        if self._assistant.llm_available:
            self._status_label.setText("🤖 AI 正在分析失败原因...")
        else:
            self._status_label.setText(
                "🔍 未配置 AI 模型，正在使用内置规则引擎分析..."
            )

        step = self._info.get("step")
        step_index = self._info.get("step_index", -1)
        error = self._info.get("error", "")

        worker = AIWorker(
            lambda: self._assistant.diagnose(
                case, step, step_index, error, self._dm
            )
        )
        worker.succeeded.connect(self._on_diagnosis)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        worker.start()

    def _on_diagnosis(self, diagnosis):
        self._worker = None
        if self._cancelled:
            return
        self._diagnosis = diagnosis

        self._status_label.setText("✅ 分析完成")
        self._status_label.setStyleSheet("color: #2e7d32;")
        conf = int(round(getattr(diagnosis, "confidence", 0) * 100))
        self._source_label.setText(
            f"诊断来源：{diagnosis.source_label} · 置信度 {conf}%"
        )

        self._reason_browser.setPlainText(diagnosis.reason or "未给出原因")
        self._solution_browser.setPlainText(diagnosis.solution or "未给出解决建议")

        actions = list(getattr(diagnosis, "actions", []) or [])
        if actions:
            self._action_group.setVisible(True)
            self._action_list.clear()
            for i, action in enumerate(actions):
                item = QListWidgetItem(
                    f"{i + 1}. {action.get('description') or action.get('kind')}"
                )
                item.setFlags(
                    Qt.ItemFlag.ItemIsUserCheckable |
                    Qt.ItemFlag.ItemIsEnabled |
                    Qt.ItemFlag.ItemIsSelectable
                )
                item.setCheckState(Qt.CheckState.Checked)
                item.setData(Qt.ItemDataRole.UserRole, action)
                self._action_list.addItem(item)

            if getattr(diagnosis, "auto_fixable", False):
                self._action_hint.setText(
                    "修复会直接修改并保存该用例，然后在本轮执行结束后自动重跑。"
                )
                self._btn_fix.setEnabled(True)
            else:
                self._action_hint.setText(
                    "⚠ AI 不建议自动修复该问题（可能掩盖真实缺陷），请参考解决方法人工处理。"
                )
                self._btn_fix.setEnabled(False)
        else:
            self._action_group.setVisible(True)
            self._action_list.clear()
            self._action_hint.setText(
                "⚠ 未找到可自动执行的修复动作，请参考解决方法人工处理。"
            )
            self._btn_fix.setEnabled(False)

        self._btn_copy.setEnabled(True)
        logger.info(f"AI 诊断完成: {diagnosis.reason[:60]}")

    def _on_failed(self, message: str):
        self._worker = None
        if self._cancelled:
            return
        self._status_label.setText(f"❌ 诊断失败：{message}")
        self._status_label.setStyleSheet("color: #c0392b;")
        self._reason_browser.setPlainText(
            "诊断服务暂时不可用。请检查网络/API 配置，或参考错误信息与日志人工排查。"
        )
        self._btn_fix.setEnabled(False)

    # ---------- 交互 ----------

    def reject(self):
        """关闭即视为放弃诊断，忽略后台线程的结果"""
        self._cancelled = True
        self._worker = None
        super().reject()

    def _on_fix_clicked(self):
        selected = []
        for row in range(self._action_list.count()):
            item = self._action_list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                action = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(action, dict):
                    selected.append(action)

        if not selected:
            QMessageBox.information(self, "提示", "请至少勾选一项修复动作")
            return

        self.repair_actions = selected
        self.accept()

    def _on_copy(self):
        if self._diagnosis is None:
            return
        text = (
            f"用例：{self._info.get('case').name if self._info.get('case') else '-'}\n"
            f"步骤：{self._info.get('step_description') or '-'}\n"
            f"错误：{self._info.get('error') or '-'}\n"
            f"原因：{self._diagnosis.reason}\n"
            f"解决方法：{self._diagnosis.solution}\n"
            f"来源：{self._diagnosis.source_label}"
        )
        QApplication.clipboard().setText(text)
        self._status_label.setText("✅ 已复制诊断内容到剪贴板")
