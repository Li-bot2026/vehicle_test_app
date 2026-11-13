"""
测试面板
=========
显示测试用例列表、执行控制、进度显示。

支持：
- 勾选框多选 + 全选（三态）
- 批量执行 / 删除 / 复制 / 导出 JSON
- 跨刷新保留勾选状态
- 双击行直接编辑
"""

import json
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QComboBox, QLineEdit, QGroupBox,
    QProgressBar, QSplitter, QTextEdit, QMenu,
    QMessageBox, QFileDialog, QAbstractItemView, QCheckBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont

from loguru import logger

from core.testcase.model import TestCase, TestSuite, TestStatus, Priority
from core.testcase.database import TestDatabase
from ..styles.theme import Tokens as T
from core.execution.engine import ExecutionEngine, ExecutionState
from core.device.manager import DeviceManager


class TestPanel(QWidget):
    """
    测试面板
    ---------
    显示和管理测试用例，控制执行。
    """

    ai_assistant_requested = pyqtSignal()

    def __init__(self, engine: ExecutionEngine, db: TestDatabase,
                 device_mgr: DeviceManager, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._db = db
        self._dm = device_mgr
        self._current_suite: Optional[TestSuite] = None
        self._visible_cases: List[TestCase] = []
        self._checked_ids: set = set()
        self._updating_checks = False
        self._init_ui()
        self.refresh()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # ---------- 工具栏：过滤 + 入口 ----------
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("分类:"))
        self._filter_category = QComboBox()
        self._filter_category.addItem("全部")
        self._filter_category.currentTextChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self._filter_category)

        toolbar.addWidget(QLabel("优先级:"))
        self._filter_priority = QComboBox()
        self._filter_priority.addItems(["全部", "P0", "P1", "P2", "P3"])
        self._filter_priority.currentTextChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self._filter_priority)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("搜索用例...")
        self._search_input.setMaximumWidth(160)
        self._search_input.textChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self._search_input)

        toolbar.addStretch()

        self._btn_editor = QPushButton("✏ 用例编辑器")
        self._btn_editor.clicked.connect(self._on_open_editor)
        toolbar.addWidget(self._btn_editor)

        self._btn_ai = QPushButton("🤖 AI 助手")
        self._btn_ai.setToolTip("AI 生成用例 / 识别用例并自动执行")
        self._btn_ai.clicked.connect(self.ai_assistant_requested.emit)
        toolbar.addWidget(self._btn_ai)

        layout.addLayout(toolbar)

        # ---------- 选择 / 批量操作栏 ----------
        sel_bar = QHBoxLayout()
        self._chk_all = QCheckBox("全选")
        self._chk_all.setTristate(True)
        self._chk_all.setToolTip("勾选/取消全部用例")
        self._chk_all.stateChanged.connect(self._on_select_all)
        sel_bar.addWidget(self._chk_all)

        self._lbl_count = QLabel("已选 0 项")
        self._lbl_count.setStyleSheet(f"color: {T.TEXT_SECONDARY};")
        sel_bar.addWidget(self._lbl_count)

        self._btn_clear = QPushButton("清空选择")
        self._btn_clear.clicked.connect(self._on_clear_checks)
        sel_bar.addWidget(self._btn_clear)

        sel_bar.addSpacing(10)

        self._btn_run_selected = QPushButton("▶ 执行选中")
        self._btn_run_selected.clicked.connect(self._on_run_selected)
        sel_bar.addWidget(self._btn_run_selected)

        self._btn_delete = QPushButton("🗑 删除选中")
        self._btn_delete.clicked.connect(self._on_delete_case)
        sel_bar.addWidget(self._btn_delete)

        self._btn_dup = QPushButton("📄 复制选中")
        self._btn_dup.clicked.connect(self._on_duplicate_case)
        sel_bar.addWidget(self._btn_dup)

        self._btn_export = QPushButton("💾 导出选中")
        self._btn_export.setToolTip("将选中用例导出为 JSON 文件")
        self._btn_export.clicked.connect(self._on_export_selected)
        sel_bar.addWidget(self._btn_export)

        sel_bar.addStretch()

        self._btn_run_all = QPushButton("▶ 执行全部")
        self._btn_run_all.clicked.connect(self._on_run_all)
        sel_bar.addWidget(self._btn_run_all)

        self._btn_schedule = QPushButton("⏰ 定时执行")
        self._btn_schedule.clicked.connect(self._on_schedule)
        sel_bar.addWidget(self._btn_schedule)

        layout.addLayout(sel_bar)

        # ---------- 用例树 + 进度 ----------
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(6)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(8)
        self._tree.setHeaderLabels([
            "用例名称", "分组", "分类", "优先级", "步骤数", "状态", "耗时", "描述"
        ])
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        for col in range(1, 8):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for col, width in enumerate((220, 120, 100, 70, 70, 80, 80, 200)):
            self._tree.setColumnWidth(col, width)
        self._tree.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._tree.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._tree.setAlternatingRowColors(True)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        self._tree.itemChanged.connect(self._on_item_changed)
        splitter.addWidget(self._tree)

        progress_widget = QWidget()
        progress_layout = QVBoxLayout(progress_widget)

        self._progress_bar = QProgressBar()
        self._progress_bar.setTextVisible(True)
        progress_layout.addWidget(self._progress_bar)

        self._log_output = QTextEdit()
        self._log_output.setReadOnly(True)
        self._log_output.setMinimumHeight(60)
        self._log_output.setFont(QFont("Consolas", 9))
        progress_layout.addWidget(self._log_output)

        splitter.addWidget(progress_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter)

        # 连接执行引擎信号
        self._engine.signals.progress_updated.connect(self._on_progress)
        self._engine.signals.case_finished.connect(self._on_case_finished)
        self._engine.signals.log_output.connect(self._on_log)
        self._engine.signals.execution_finished.connect(self._on_finished)

    # ---------- 数据操作 ----------

    def refresh(self):
        """刷新用例列表（保留勾选状态）"""
        if getattr(self, "_refreshing", False):
            return
        self._refreshing = True
        try:
            self._do_refresh()
        finally:
            self._refreshing = False

    def _do_refresh(self):
        # 记住当前勾选，重建后恢复
        self._checked_ids = {c.case_id for c in self._checked_cases()}
        self._updating_checks = True
        try:
            self._filter_category.blockSignals(True)
            categories = self._db.get_all_categories()
            current = self._filter_category.currentText()
            self._filter_category.clear()
            self._filter_category.addItem("全部")
            self._filter_category.addItems(categories)
            if current in categories:
                self._filter_category.setCurrentText(current)
            self._filter_category.blockSignals(False)

            keyword = self._search_input.text().strip() or None
            category = self._filter_category.currentText()
            if category in ("", "全部"):
                category = None
            priority = None
            prio_text = self._filter_priority.currentText()
            if prio_text != "全部":
                priority = Priority(prio_text)

            cases = self._db.list_cases(
                category=category, keyword=keyword, priority=priority
            )
            # 过滤掉有 parent_id 的旧记录（参数化子用例由父用例动态展开）
            cases = [c for c in cases if not c.parent_id]
            # 隐藏模板用例（有参数集但没有父节点的才是模板）
            cases = [c for c in cases if not (
                getattr(c, 'is_template', False) and not c.parameter_sets
            )]
            # 从父用例的 parameter_sets 动态展开子用例
            from core.testcase.parser import TestCaseParser
            expanded = []
            for case in cases:
                if case.parameter_sets:
                    children = TestCaseParser._expand_parameters(case)
                    expanded.extend(children)
            cases.extend(expanded)
            self._visible_cases = cases

            status_colors = {
                TestStatus.PASSED: "#52c41a",
                TestStatus.FAILED: "#ff4d4f",
                TestStatus.ERROR: "#ff7a45",
                TestStatus.SKIPPED: "#faad14",
            }

            self._tree.clear()

            # 构建父子关系映射
            parent_map = {}  # parent_id -> [child_cases]
            root_cases = []  # 没有父节点的用例

            for case in cases:
                if case.parent_id:
                    if case.parent_id not in parent_map:
                        parent_map[case.parent_id] = []
                    parent_map[case.parent_id].append(case)
                else:
                    root_cases.append(case)

            # 渲染树：先渲染根节点，再渲染子节点
            for case in root_cases:
                # 检查是否有子节点
                children = parent_map.get(case.case_id, [])

                if children:
                    # 有子节点：创建父节点
                    parent_item = QTreeWidgetItem(self._tree)
                    parent_item.setText(0, f"📁 {case.name} ({len(children)})")
                    parent_item.setText(1, case.group or "-")
                    parent_item.setText(2, case.category or "-")
                    parent_item.setText(3, case.priority.value)
                    parent_item.setText(4, str(case.total_steps))
                    parent_item.setText(5, case.status.value)
                    parent_item.setText(6, f"{case.duration:.2f}s")
                    parent_item.setText(7, case.description[:50] if case.description else "")
                    parent_item.setExpanded(True)
                    parent_item.setData(0, Qt.ItemDataRole.UserRole, case.case_id)
                    parent_item.setFlags(
                        parent_item.flags()
                        | Qt.ItemFlag.ItemIsUserCheckable
                        | Qt.ItemFlag.ItemIsSelectable
                    )
                    # 父节点勾选状态（默认未勾选）
                    parent_item.setCheckState(0, Qt.CheckState.Unchecked)

                    # 添加子节点
                    for child in children:
                        child_item = QTreeWidgetItem(parent_item)
                        child_item.setText(0, child.name)
                        child_item.setText(1, child.group or "-")
                        child_item.setText(2, child.category or "-")
                        child_item.setText(3, child.priority.value)
                        child_item.setText(4, str(child.total_steps))
                        child_item.setText(5, child.status.value)
                        child_item.setText(6, f"{child.duration:.2f}s")
                        child_item.setText(7, child.description[:50] if child.description else "")

                        # 设置状态颜色
                        status_col = QColor(status_colors.get(child.status, "#333"))
                        child_item.setForeground(5, status_col)

                        # 存储用例 ID
                        child_item.setData(0, Qt.ItemDataRole.UserRole, child.case_id)

                        # 勾选状态
                        if child.case_id in self._checked_ids:
                            child_item.setCheckState(0, Qt.CheckState.Checked)
                        else:
                            child_item.setCheckState(0, Qt.CheckState.Unchecked)
                        child_item.setFlags(
                            child_item.flags()
                            | Qt.ItemFlag.ItemIsUserCheckable
                            | Qt.ItemFlag.ItemIsSelectable
                        )
                else:
                    # 无子节点：直接作为叶子节点
                    leaf_item = QTreeWidgetItem(self._tree)
                    leaf_item.setText(0, case.name)
                    leaf_item.setText(1, case.group or "-")
                    leaf_item.setText(2, case.category or "-")
                    leaf_item.setText(3, case.priority.value)
                    leaf_item.setText(4, str(case.total_steps))
                    leaf_item.setText(5, case.status.value)
                    leaf_item.setText(6, f"{case.duration:.2f}s")
                    leaf_item.setText(7, case.description[:50] if case.description else "")

                    # 设置状态颜色
                    status_col = QColor(status_colors.get(case.status, "#333"))
                    leaf_item.setForeground(5, status_col)

                    # 存储用例 ID
                    leaf_item.setData(0, Qt.ItemDataRole.UserRole, case.case_id)

                    # 勾选状态
                    if case.case_id in self._checked_ids:
                        leaf_item.setCheckState(0, Qt.CheckState.Checked)
                    else:
                        leaf_item.setCheckState(0, Qt.CheckState.Unchecked)
                    leaf_item.setFlags(
                        leaf_item.flags()
                        | Qt.ItemFlag.ItemIsUserCheckable
                        | Qt.ItemFlag.ItemIsSelectable
                    )
            # 恢复子节点勾选后，同步父节点三态
            for i in range(self._tree.topLevelItemCount()):
                parent_item = self._tree.topLevelItem(i)
                if parent_item.childCount() > 0:
                    checked_children = sum(
                        1 for j in range(parent_item.childCount())
                        if parent_item.child(j).checkState(0) == Qt.CheckState.Checked
                    )
                    total_children = parent_item.childCount()
                    if checked_children == 0:
                        parent_item.setCheckState(0, Qt.CheckState.Unchecked)
                    elif checked_children == total_children:
                        parent_item.setCheckState(0, Qt.CheckState.Checked)
                    else:
                        parent_item.setCheckState(0, Qt.CheckState.PartiallyChecked)
        finally:
            self._updating_checks = False
        self._update_selection_ui()

    def get_current_suite(self) -> Optional[TestSuite]:
        """获取当前测试套件（与 UI 显示一致的过滤逻辑）"""
        cases = self._db.list_cases(limit=10000)
        if not cases:
            return None
        # 与 _do_refresh 一致的过滤
        cases = [c for c in cases if not c.parent_id]
        cases = [c for c in cases if not (
            getattr(c, 'is_template', False) and not c.parameter_sets
        )]
        suite = TestSuite(name="全部用例")
        for c in cases:
            suite.add_case(c)
        self._current_suite = suite
        return suite

    # ---------- 选择逻辑 ----------

    def _on_item_changed(self, item: QTreeWidgetItem, column: int):
        """勾选框变化时联动：父节点勾选 → 所有子节点勾选；子节点变化 → 更新父节点状态"""
        if self._updating_checks:
            return
        if column != 0:
            return

        self._updating_checks = True
        try:
            if item.parent() is None:
                # 父节点变化 → 同步所有子节点
                state = item.checkState(0)
                for j in range(item.childCount()):
                    child = item.child(j)
                    if child.checkState(0) != state:
                        child.setCheckState(0, state)
            else:
                # 子节点变化 → 更新父节点状态
                parent = item.parent()
                checked_count = sum(
                    1 for j in range(parent.childCount())
                    if parent.child(j).checkState(0) == Qt.CheckState.Checked
                )
                if checked_count == 0:
                    parent.setCheckState(0, Qt.CheckState.Unchecked)
                elif checked_count == parent.childCount():
                    parent.setCheckState(0, Qt.CheckState.Checked)
                else:
                    parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
        finally:
            self._updating_checks = False
        self._update_selection_ui()

    def _checked_cases(self) -> List[TestCase]:
        """勾选框选中的用例（父节点只作为容器，不计入可执行用例）"""
        cases = []
        seen_ids = set()
        case_map = {c.case_id: c for c in self._visible_cases}
        for i in range(self._tree.topLevelItemCount()):
            parent = self._tree.topLevelItem(i)
            parent_id = parent.data(0, Qt.ItemDataRole.UserRole)

            if parent.childCount() > 0:
                # 有子节点的父节点：只收集勾选的子节点
                for j in range(parent.childCount()):
                    child = parent.child(j)
                    if child.checkState(0) == Qt.CheckState.Checked:
                        child_id = child.data(0, Qt.ItemDataRole.UserRole)
                        if child_id and child_id in case_map and child_id not in seen_ids:
                            cases.append(case_map[child_id])
                            seen_ids.add(child_id)
            else:
                # 叶子节点：直接收集
                if parent.checkState(0) == Qt.CheckState.Checked:
                    if parent_id and parent_id in case_map and parent_id not in seen_ids:
                        cases.append(case_map[parent_id])
                        seen_ids.add(parent_id)
        return cases

    def get_selected_cases(self) -> List[TestCase]:
        """优先返回勾选用例；未勾选时回退到行选中（父节点返回其子节点，去重）"""
        checked = self._checked_cases()
        if checked:
            return checked
        items = self._tree.selectedItems()
        case_map = {c.case_id: c for c in self._visible_cases}
        result = []
        seen = set()
        for item in items:
            if item.childCount() > 0:
                # 父节点被选中，返回其所有子节点
                for j in range(item.childCount()):
                    child = item.child(j)
                    child_id = child.data(0, Qt.ItemDataRole.UserRole)
                    if child_id and child_id in case_map and child_id not in seen:
                        result.append(case_map[child_id])
                        seen.add(child_id)
            else:
                case_id = item.data(0, Qt.ItemDataRole.UserRole)
                if case_id and case_id in case_map and case_id not in seen:
                    result.append(case_map[case_id])
                    seen.add(case_id)
        return result

    def _update_selection_ui(self):
        # 总数排除父节点（有 parameter_sets 的是模板容器，不可执行）
        executable_cases = [c for c in self._visible_cases if not c.parameter_sets]
        total = len(executable_cases)
        n = len(self._checked_cases())
        self._lbl_count.setText(
            f"已选 {n} 项" + (f" / 共 {total}" if total else "")
        )
        self._chk_all.blockSignals(True)
        if n == 0 or total == 0:
            self._chk_all.setCheckState(Qt.CheckState.Unchecked)
        elif n >= total:
            self._chk_all.setCheckState(Qt.CheckState.Checked)
        else:
            self._chk_all.setCheckState(Qt.CheckState.PartiallyChecked)
        self._chk_all.blockSignals(False)

    def _on_select_all(self, state: int):
        if self._updating_checks:
            return
        # 三态复选框点击会经历 未选→半选→全选 的循环，
        # 因此按"当前是否已全选"决定本次动作，避免首次点击反而取消选择。
        executable_cases = [c for c in self._visible_cases if not c.parameter_sets]
        total = len(executable_cases)
        checked = len(self._checked_cases())
        check = checked < total
        self._updating_checks = True
        try:
            # 遍历树的所有节点（包括叶子节点和子节点）
            for i in range(self._tree.topLevelItemCount()):
                parent = self._tree.topLevelItem(i)
                if parent.childCount() > 0:
                    # 有子节点的父节点：设置所有子节点
                    for j in range(parent.childCount()):
                        child = parent.child(j)
                        child.setCheckState(
                            0, Qt.CheckState.Checked if check else Qt.CheckState.Unchecked
                        )
                    # 同步父节点状态
                    parent.setCheckState(
                        0, Qt.CheckState.Checked if check else Qt.CheckState.Unchecked
                    )
                else:
                    # 叶子节点：直接设置
                    parent.setCheckState(
                        0, Qt.CheckState.Checked if check else Qt.CheckState.Unchecked
                    )
        finally:
            self._updating_checks = False
        self._update_selection_ui()

    def _on_clear_checks(self):
        self._updating_checks = True
        try:
            for i in range(self._tree.topLevelItemCount()):
                parent = self._tree.topLevelItem(i)
                if parent.childCount() > 0:
                    for j in range(parent.childCount()):
                        child = parent.child(j)
                        child.setCheckState(0, Qt.CheckState.Unchecked)
                    parent.setCheckState(0, Qt.CheckState.Unchecked)
                else:
                    parent.setCheckState(0, Qt.CheckState.Unchecked)
        finally:
            self._updating_checks = False
        self._update_selection_ui()

    # ---------- 事件处理 ----------

    def _on_filter_changed(self):
        self.refresh()

    def _on_run_all(self):
        suite = self.get_current_suite()
        if suite:
            self._engine.execute_batch(suite)

    def _on_run_selected(self):
        cases = self.get_selected_cases()
        if not cases:
            QMessageBox.information(self, "提示", "请先勾选或选中要执行的用例")
            return
        suite = TestSuite(name="选中执行")
        for c in cases:
            suite.add_case(c)
        self._engine.execute_batch(suite)

    def _on_context_menu(self, pos):
        menu = QMenu(self)
        menu.addAction("执行选中/此行", self._on_run_selected)
        menu.addAction("编辑用例", self._on_edit_case)
        menu.addAction("复制选中", self._on_duplicate_case)
        menu.addAction("导出选中", self._on_export_selected)
        menu.addSeparator()
        menu.addAction("删除选中", self._on_delete_case)
        menu.exec(self._tree.mapToGlobal(pos))

    def _on_open_editor(self):
        """打开用例编辑器"""
        from ui.widgets.test_case_editor import TestCaseEditorDialog
        dlg = TestCaseEditorDialog(self._db, parent=self, device_mgr=self._dm)
        dlg.data_changed.connect(self.refresh)
        dlg.exec()

    def _on_schedule(self):
        """定时执行"""
        suite = self.get_current_suite()
        if not suite:
            QMessageBox.information(self, "提示", "没有可执行的用例")
            return

        from ui.widgets.schedule_dialog import ScheduleDialog
        dlg = ScheduleDialog(parent=self)
        if dlg.exec() == ScheduleDialog.DialogCode.Accepted:
            run_time = dlg.get_datetime()
            self._engine.execute_scheduled(suite, run_time)
            self._log_output.append(f"[INFO] 已设置定时执行: {run_time}")

    def _on_double_click(self, item, column):
        """双击行直接编辑该用例"""
        # 只处理子节点（用例节点）
        if item.parent() is None:
            return
        case_id = item.data(0, Qt.ItemDataRole.UserRole)
        for case in self._visible_cases:
            if case.case_id == case_id:
                self._edit_case(case)
                break

    def _on_edit_case(self):
        """编辑选中用例（打开编辑器）"""
        cases = self.get_selected_cases()
        if not cases:
            QMessageBox.information(self, "提示", "请先选择一个用例")
            return
        self._edit_case(cases[0])

    def _edit_case(self, case: TestCase):
        from ui.widgets.test_case_editor import TestCaseEditorDialog
        dlg = TestCaseEditorDialog(self._db, parent=self, device_mgr=self._dm)
        dlg.select_case(case.case_id)
        dlg.data_changed.connect(self.refresh)
        dlg.exec()

    def _on_delete_case(self):
        cases = self.get_selected_cases()
        if not cases:
            QMessageBox.information(self, "提示", "请先勾选或选中要删除的用例")
            return
        reply = QMessageBox.question(
            self, "确认删除", f"确定删除 {len(cases)} 个用例？\n该操作不可恢复。"
        )
        if reply == QMessageBox.StandardButton.Yes:
            deleted = 0
            failed = []
            # 按父节点分组，批量移除子用例对应的参数组
            parent_updates = {}  # parent_id -> [indices to remove]
            for c in cases:
                if c.parent_id:
                    # 子用例：从 case_id 提取索引 (格式: parent_id_p{i+1})
                    suffix = c.case_id.split("_p")[-1]
                    try:
                        idx = int(suffix) - 1
                        if c.parent_id not in parent_updates:
                            parent_updates[c.parent_id] = []
                        parent_updates[c.parent_id].append(idx)
                    except ValueError:
                        pass
                    # 也尝试直接删除（可能在 DB 中）
                    self._db.delete_case(c.case_id)
                else:
                    # 父用例：直接删除（子用例会因参数组移除而消失）
                    if self._db.delete_case(c.case_id):
                        deleted += 1
                    else:
                        failed.append(c.name)

            # 批量更新父用例的参数组
            for parent_id, indices in parent_updates.items():
                parent = self._db.load_case(parent_id)
                if parent and parent.parameter_sets:
                    # 从大到小删除，避免索引偏移
                    for idx in sorted(set(indices), reverse=True):
                        if 0 <= idx < len(parent.parameter_sets):
                            parent.parameter_sets.pop(idx)
                    # 如果没有参数组了，清空 parameters 默认值
                    if not parent.parameter_sets:
                        parent.parameters = {}
                    if self._db.save_case(parent):
                        deleted += len(indices)
                    else:
                        failed.append(f"{parent.name} 的参数组")

            self._checked_ids -= {c.case_id for c in cases}
            self.refresh()
            if failed:
                self._log_output.append(f"[ERROR] 删除失败: {', '.join(failed[:5])}")
            self._log_output.append(f"[INFO] 已删除 {deleted}/{len(cases)} 个用例")

    def _on_duplicate_case(self):
        """复制选中用例"""
        cases = self.get_selected_cases()
        if not cases:
            QMessageBox.information(self, "提示", "请先勾选或选中要复制的用例")
            return
        for c in cases:
            new_case = TestCase.from_dict(c.to_dict())
            new_case.name = f"{c.name} (副本)"
            new_case.case_id = ""
            new_case.parent_id = None  # 副本是独立用例，不继承父子关系
            self._db.save_case(new_case)
        self.refresh()
        self._log_output.append(f"[INFO] 已复制 {len(cases)} 个用例")

    def _on_export_selected(self):
        """导出选中用例为 JSON"""
        cases = self.get_selected_cases()
        if not cases:
            QMessageBox.information(self, "提示", "请先勾选或选中要导出的用例")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出用例", "test_cases.json", "JSON (*.json)"
        )
        if not path:
            return
        try:
            data = [c.to_dict() for c in cases]
            Path(path).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._log_output.append(f"[INFO] 已导出 {len(cases)} 个用例 → {path}")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    # ---------- 执行回调 ----------

    def _on_progress(self, progress):
        self._progress_bar.setMaximum(progress.total)
        self._progress_bar.setValue(progress.current)
        self._progress_bar.setFormat(
            f"{progress.current}/{progress.total} - {progress.current_case_name}"
        )

    def _on_case_finished(self, case_id, name, status, duration):
        self._log_output.append(f"[{status}] {name} ({duration:.2f}s)")

    def _on_log(self, level, message):
        self._log_output.append(f"[{level}] {message}")

    def _on_finished(self, name, duration):
        self._log_output.append(f"\n===== 执行完成: {name} ({duration:.1f}s) =====\n")
        self.refresh()
