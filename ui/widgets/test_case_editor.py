"""
可视化用例编辑器
================
内置测试用例编辑对话框，支持：
- 用例的增删改查（列表 + 表单）
- 步骤的增删改（步骤表格 + 步骤编辑）
- 标签、优先级、分类编辑
- 保存到数据库
"""

import json
from typing import Optional, List

from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTreeWidget, QTreeWidgetItem,
    QLineEdit, QFormLayout, QComboBox, QTextEdit,
    QTableWidget, QTableWidgetItem, QMessageBox,
    QGroupBox, QSpinBox, QDoubleSpinBox, QSplitter,
    QHeaderView, QAbstractItemView, QDialogButtonBox,
    QPlainTextEdit, QSizePolicy, QScrollArea, QTabWidget
)
from PyQt6.QtCore import Qt, pyqtSignal, QSettings
from PyQt6.QtGui import QFont, QIcon

from loguru import logger

from core.testcase.model import (
    TestCase, TestStep, StepType, Priority, TestStatus
)
from core.testcase.database import TestDatabase
from .dialog_utils import allow_resize


class StepEditDialog(QDialog):
    """
    步骤编辑对话框
    ---------------
    编辑单个测试步骤的类型、参数、预期值等。
    """

    def __init__(self, step: Optional[TestStep] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑步骤")
        self.setMinimumWidth(500)
        self.resize(580, 460)
        allow_resize(self)
        self._step = step or TestStep(step_type=StepType.WAIT)
        self._init_ui()
        self._load_step()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        form = QVBoxLayout()
        form.setSpacing(6)

        # 步骤类型
        row = QHBoxLayout()
        row.addWidget(QLabel("步骤类型:"))
        self._type_combo = QComboBox()
        for st in StepType:
            self._type_combo.addItem(st.value, st.value)
        row.addWidget(self._type_combo, 1)
        form.addLayout(row)

        # 描述
        row = QHBoxLayout()
        row.addWidget(QLabel("描述:"))
        self._desc_input = QLineEdit()
        self._desc_input.setPlaceholderText("步骤描述（支持 {param} 占位符）")
        row.addWidget(self._desc_input, 1)
        form.addLayout(row)

        # 设备 ID
        row = QHBoxLayout()
        row.addWidget(QLabel("设备 ID:"))
        self._device_input = QLineEdit()
        self._device_input.setPlaceholderText("留空则自动匹配")
        row.addWidget(self._device_input, 1)
        form.addLayout(row)

        # 参数 JSON
        form.addWidget(QLabel("参数 (JSON):"))
        self._params_input = QTextEdit()
        self._params_input.setMaximumHeight(120)
        self._params_input.setFont(QFont("Consolas", 9))
        self._params_input.setPlaceholderText('{"text": "{keyword}", "clear": true}')
        form.addWidget(self._params_input)

        # 预期值
        row = QHBoxLayout()
        row.addWidget(QLabel("预期值:"))
        self._expected_input = QLineEdit()
        self._expected_input.setPlaceholderText("可选，JSON 或字符串")
        row.addWidget(self._expected_input, 1)
        form.addLayout(row)

        # 容差和超时
        row = QHBoxLayout()
        row.addWidget(QLabel("容差:"))
        self._tolerance_input = QDoubleSpinBox()
        self._tolerance_input.setRange(0, 100)
        self._tolerance_input.setDecimals(2)
        self._tolerance_input.setValue(0)
        row.addWidget(self._tolerance_input)
        row.addSpacing(20)
        row.addWidget(QLabel("超时(s):"))
        self._timeout_input = QDoubleSpinBox()
        self._timeout_input.setRange(1, 600)
        self._timeout_input.setDecimals(1)
        self._timeout_input.setValue(30)
        row.addWidget(self._timeout_input)
        row.addStretch()
        form.addLayout(row)

        layout.addLayout(form)
        layout.addStretch()

        # 按钮
        buttons = QHBoxLayout()
        buttons.addStretch()
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(btn_cancel)
        btn_save = QPushButton("保存")
        btn_save.setDefault(True)
        btn_save.clicked.connect(self._on_accept)
        buttons.addWidget(btn_save)
        layout.addLayout(buttons)

    def _load_step(self):
        """加载步骤数据到表单"""
        idx = self._type_combo.findData(self._step.step_type.value)
        if idx >= 0:
            self._type_combo.setCurrentIndex(idx)
        self._desc_input.setText(self._step.description)
        self._device_input.setText(self._step.device_id)
        self._params_input.setPlainText(
            json.dumps(self._step.params, ensure_ascii=False, indent=2)
            if self._step.params else ""
        )
        self._expected_input.setText(
            str(self._step.expected) if self._step.expected is not None else ""
        )
        self._tolerance_input.setValue(self._step.tolerance)
        self._timeout_input.setValue(self._step.timeout)

    def _on_accept(self):
        """校验并保存"""
        # 解析参数
        params_text = self._params_input.toPlainText().strip()
        params = {}
        if params_text:
            try:
                params = json.loads(params_text)
            except json.JSONDecodeError as e:
                QMessageBox.warning(self, "参数错误", f"参数 JSON 格式错误: {e}")
                return

        # 解析预期值
        expected_text = self._expected_input.text().strip()
        expected = None
        if expected_text:
            try:
                expected = json.loads(expected_text)
            except json.JSONDecodeError:
                expected = expected_text  # 保留原始字符串

        # 更新步骤
        self._step.step_type = StepType(self._type_combo.currentData())
        self._step.description = self._desc_input.text().strip()
        self._step.device_id = self._device_input.text().strip()
        self._step.params = params
        self._step.expected = expected
        self._step.tolerance = self._tolerance_input.value()
        self._step.timeout = self._timeout_input.value()

        self.accept()

    def get_step(self) -> TestStep:
        return self._step


class TestCaseEditorDialog(QDialog):
    """
    用例编辑器对话框
    -----------------
    左侧用例列表 + 右侧编辑表单 + 底部步骤表格。
    """

    data_changed = pyqtSignal()  # 数据变化信号（通知外部刷新）

    def __init__(self, db: TestDatabase, parent=None, device_mgr=None):
        super().__init__(parent)
        self._db = db
        self._device_mgr = device_mgr
        self._cases: List[TestCase] = []
        self._current_case: Optional[TestCase] = None
        self._regen_worker = None
        self.setWindowTitle("测试用例编辑器")
        allow_resize(self)
        self._init_ui()
        self._restore_geometry()
        self.reload()

    def _restore_geometry(self):
        """恢复窗口大小和分割器状态"""
        settings = QSettings("VehicleTestApp", "TestCaseEditor")
        geo = settings.value("geometry")
        if geo and isinstance(geo, bytes):
            self.restoreGeometry(geo)
        else:
            self.resize(1200, 750)
        splitter_state = settings.value("splitter_state")
        if splitter_state and isinstance(splitter_state, bytes):
            self._main_splitter.restoreState(splitter_state)

    def _save_geometry(self):
        """保存窗口大小和分割器状态"""
        settings = QSettings("VehicleTestApp", "TestCaseEditor")
        settings.setValue("geometry", self.saveGeometry())
        if hasattr(self, '_main_splitter'):
            settings.setValue("splitter_state", self._main_splitter.saveState())

    def closeEvent(self, event):
        self._save_geometry()
        super().closeEvent(event)

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 主分割器
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(6)

        # 左侧：用例列表 + 操作按钮
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)

        left_header = QHBoxLayout()
        self._btn_add = QPushButton("➕ 新增")
        self._btn_add.clicked.connect(self._on_add_case)
        left_header.addWidget(self._btn_add)

        self._btn_delete = QPushButton("🗑 删除")
        self._btn_delete.clicked.connect(self._on_delete_case)
        left_header.addWidget(self._btn_delete)

        self._btn_duplicate = QPushButton("📄 复制")
        self._btn_duplicate.clicked.connect(self._on_duplicate_case)
        left_header.addWidget(self._btn_duplicate)
        left_layout.addLayout(left_header)

        self._case_list = QTreeWidget()
        self._case_list.setColumnCount(1)
        self._case_list.setHeaderLabels(["用例列表"])
        self._case_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._case_list.currentItemChanged.connect(self._on_case_selected)
        self._case_list.itemSelectionChanged.connect(self._on_selection_changed)
        self._case_list.setStyleSheet("""
            QTreeWidget {
                border: 1px solid #d0d0d0;
                border-radius: 4px;
                padding: 4px;
                font-size: 13px;
            }
            QTreeWidget::item {
                padding: 6px 4px;
                border-bottom: 1px solid #f0f0f0;
            }
            QTreeWidget::item:selected {
                background: #e3f2fd;
                border-left: 3px solid #1976d2;
            }
            QTreeWidget::item:hover {
                background: #f5f5f5;
            }
        """)
        left_layout.addWidget(self._case_list)

        splitter.addWidget(left_widget)

        self._main_splitter = splitter  # 保存引用以便恢复状态

        # 右侧：编辑表单 + 步骤（放入滚动区域）
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        # 用例信息表单
        info_group = QGroupBox("用例信息")
        info_form = QFormLayout(info_group)

        self._name_input = QLineEdit()
        info_form.addRow("名称:", self._name_input)

        self._category_input = QLineEdit()
        info_form.addRow("分类:", self._category_input)

        self._priority_combo = QComboBox()
        self._priority_combo.addItems(["P0", "P1", "P2", "P3"])
        info_form.addRow("优先级:", self._priority_combo)

        self._tags_input = QLineEdit()
        self._tags_input.setPlaceholderText("逗号分隔，如: 空调,导航")
        info_form.addRow("标签:", self._tags_input)

        self._desc_input = QTextEdit()
        self._desc_input.setMaximumHeight(60)
        info_form.addRow("描述:", self._desc_input)

        retry_layout = QHBoxLayout()
        self._retry_spin = QSpinBox()
        self._retry_spin.setRange(-1, 10)
        self._retry_spin.setSpecialValueText("默认")
        self._retry_spin.setToolTip("重试次数；-1 表示沿用全局配置")
        retry_layout.addWidget(QLabel("重试次数:"))
        retry_layout.addWidget(self._retry_spin)

        self._timeout_spin = QDoubleSpinBox()
        self._timeout_spin.setRange(1, 3600)
        self._timeout_spin.setValue(60.0)
        retry_layout.addWidget(QLabel("超时(秒):"))
        retry_layout.addWidget(self._timeout_spin)
        retry_layout.addStretch()
        info_form.addRow(retry_layout)

        right_layout.addWidget(info_group)

        # 参数化测试（可折叠，默认收起）
        param_group = QGroupBox("参数化测试")
        param_group.setCheckable(True)
        param_group.setChecked(False)
        param_layout = QVBoxLayout(param_group)

        # 参数定义表
        param_def_header = QHBoxLayout()
        param_def_header.addWidget(QLabel("参数定义:"))
        self._btn_add_param = QPushButton("➕ 添加参数")
        self._btn_add_param.clicked.connect(self._on_add_param)
        param_def_header.addWidget(self._btn_add_param)
        self._btn_remove_param = QPushButton("🗑 删除")
        self._btn_remove_param.clicked.connect(self._on_remove_param)
        param_def_header.addWidget(self._btn_remove_param)
        self._btn_import_params = QPushButton("📥 导入参数文件")
        self._btn_import_params.setToolTip(
            "从 Excel / CSV / JSON / YAML / Word 文件导入参数组和参数定义"
        )
        self._btn_import_params.clicked.connect(self._on_import_params)
        param_def_header.addWidget(self._btn_import_params)
        param_def_header.addStretch()
        param_layout.addLayout(param_def_header)

        self._param_table = QTableWidget()
        self._param_table.setColumnCount(2)
        self._param_table.setHorizontalHeaderLabels(["参数名", "默认值"])
        self._param_table.setMaximumHeight(100)
        self._param_table.horizontalHeader().setStretchLastSection(True)
        self._param_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self._param_table.setColumnWidth(0, 150)
        self._param_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
        )
        self._param_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._param_table.cellChanged.connect(self._on_param_table_changed)
        param_layout.addWidget(self._param_table)

        # 参数组表
        param_set_header = QHBoxLayout()
        param_set_header.addWidget(QLabel("参数组 (每组生成一条独立用例):"))
        self._btn_add_param_set = QPushButton("➕ 添加参数组")
        self._btn_add_param_set.clicked.connect(self._on_add_param_set)
        param_set_header.addWidget(self._btn_add_param_set)
        self._btn_remove_param_set = QPushButton("🗑 删除")
        self._btn_remove_param_set.clicked.connect(self._on_remove_param_set)
        param_set_header.addWidget(self._btn_remove_param_set)
        param_set_header.addStretch()
        param_layout.addLayout(param_set_header)

        self._param_set_table = QTableWidget()
        self._param_set_table.setColumnCount(0)
        self._param_set_table.setMaximumHeight(120)
        self._param_set_table.horizontalHeader().setStretchLastSection(True)
        self._param_set_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self._param_set_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
        )
        self._param_set_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        param_layout.addWidget(self._param_set_table)

        param_hint = QLabel(
            "提示: 在步骤参数/描述中使用 {参数名} 占位符，执行时自动替换。"
            "例如: ui_tap 参数 text 填 {song_name}，参数组里填不同的歌名。"
        )
        param_hint.setFont(QFont("Consolas", 8))
        param_hint.setStyleSheet("color: #888;")
        param_hint.setWordWrap(True)
        param_layout.addWidget(param_hint)

        self._param_updating = False  # 防止刷新时触发 cellChanged
        right_layout.addWidget(param_group)

        # 步骤表格（可折叠）
        steps_group = QGroupBox("测试步骤")
        steps_group.setCheckable(True)
        steps_group.setChecked(True)
        steps_layout = QVBoxLayout(steps_group)

        step_buttons = QHBoxLayout()
        self._btn_add_step = QPushButton("➕ 添加步骤")
        self._btn_add_step.clicked.connect(self._on_add_step)
        step_buttons.addWidget(self._btn_add_step)

        self._btn_edit_step = QPushButton("✏ 编辑步骤")
        self._btn_edit_step.clicked.connect(self._on_edit_step)
        step_buttons.addWidget(self._btn_edit_step)

        self._btn_remove_step = QPushButton("🗑 删除步骤")
        self._btn_remove_step.clicked.connect(self._on_remove_step)
        step_buttons.addWidget(self._btn_remove_step)

        self._btn_move_up = QPushButton("⬆ 上移")
        self._btn_move_up.clicked.connect(lambda: self._on_move_step(-1))
        step_buttons.addWidget(self._btn_move_up)

        self._btn_move_down = QPushButton("⬇ 下移")
        self._btn_move_down.clicked.connect(lambda: self._on_move_step(1))
        step_buttons.addWidget(self._btn_move_down)

        self._btn_regen = QPushButton("🔄 按描述重新生成")
        self._btn_regen.setToolTip(
            "按上方「描述」重新生成步骤并替换当前步骤（改了描述不用新建用例）"
        )
        self._btn_regen.clicked.connect(self._on_regenerate)
        step_buttons.addWidget(self._btn_regen)
        step_buttons.addStretch()
        steps_layout.addLayout(step_buttons)

        # 状态提示单独一行并自动换行，避免长文本把对话框撑宽、导致无法缩放
        self._regen_status = QLabel("")
        self._regen_status.setStyleSheet("color: #555;")
        self._regen_status.setWordWrap(True)
        self._regen_status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._regen_status.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self._regen_status.setMinimumWidth(0)
        steps_layout.addWidget(self._regen_status)

        self._steps_table = QTableWidget()
        self._steps_table.setColumnCount(5)
        self._steps_table.setHorizontalHeaderLabels([
            "#", "类型", "描述", "设备", "参数"
        ])
        # 列宽可拖拽调整（上次末列拉伸填充剩余空间）
        header = self._steps_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        header.setSectionsMovable(False)
        self._steps_table.setColumnWidth(0, 40)
        self._steps_table.setColumnWidth(1, 110)
        self._steps_table.setColumnWidth(2, 300)
        self._steps_table.setColumnWidth(3, 130)
        self._steps_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._steps_table.setWordWrap(True)
        self._steps_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        steps_layout.addWidget(self._steps_table)

        right_layout.addWidget(steps_group)
        # 右侧放入滚动区域，内容超出时可上下滚动
        right_scroll = QScrollArea()
        right_scroll.setWidget(right_widget)
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        right_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        splitter.addWidget(right_scroll)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter)

        # 底部按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Close
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).clicked.connect(
            self._on_save
        )
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(
            self.close
        )
        layout.addWidget(buttons)

    # ---------- 数据加载 ----------

    def reload(self):
        """从数据库加载用例列表，并从父用例的 parameter_sets 动态展开子用例"""
        all_cases = self._db.list_cases(limit=10000)
        # 过滤掉有 parent_id 的旧记录（参数化子用例不应独立入库，由父用例动态展开）
        root_cases = [c for c in all_cases if not c.parent_id]
        # 隐藏模板用例（is_template 且无 parameter_sets 的才隐藏）
        root_cases = [c for c in root_cases if not (
            getattr(c, 'is_template', False) and not c.parameter_sets
        )]
        self._cases = root_cases
        self._expand_all_parameterized()
        self._refresh_list()

    def _expand_all_parameterized(self):
        """遍历所有用例，对有 parameter_sets 的父用例展开子用例（内存中，不入库）"""
        from core.testcase.parser import TestCaseParser
        expanded = []
        for case in self._cases:
            if case.parameter_sets:
                children = TestCaseParser._expand_parameters(case)
                expanded.extend(children)
        self._cases.extend(expanded)

    def select_case(self, case_id: str):
        """在列表中定位并选中指定用例（供外部双击编辑）"""
        # 遍历树的所有节点
        for i in range(self._case_list.topLevelItemCount()):
            parent = self._case_list.topLevelItem(i)
            # 检查父节点
            if parent.data(0, Qt.ItemDataRole.UserRole) == case_id:
                self._case_list.setCurrentItem(parent)
                return True
            # 检查子节点
            for j in range(parent.childCount()):
                child = parent.child(j)
                if child.data(0, Qt.ItemDataRole.UserRole) == case_id:
                    self._case_list.setCurrentItem(child)
                    return True
        return False

    def _refresh_list(self):
        """刷新左侧用例列表（树形结构）"""
        current_id = self._current_case.case_id if self._current_case else None
        self._case_list.clear()

        # 构建父子关系映射
        parent_map = {}  # parent_id -> [child_cases]
        root_cases = []  # 没有父节点的用例

        for case in self._cases:
            if case.parent_id:
                if case.parent_id not in parent_map:
                    parent_map[case.parent_id] = []
                parent_map[case.parent_id].append(case)
            else:
                root_cases.append(case)

        # 渲染树
        for case in root_cases:
            children = parent_map.get(case.case_id, [])

            if children:
                # 有子节点：创建父节点
                parent_item = QTreeWidgetItem(self._case_list)
                parent_item.setText(0, f"📁 [{case.priority.value}] {case.name} ({len(children)})")
                parent_item.setData(0, Qt.ItemDataRole.UserRole, case.case_id)
                parent_item.setExpanded(True)

                # 添加子节点
                for child in children:
                    child_item = QTreeWidgetItem(parent_item)
                    child_item.setText(0, f"[{child.priority.value}] {child.name}")
                    child_item.setData(0, Qt.ItemDataRole.UserRole, child.case_id)
                    if child.case_id == current_id:
                        self._case_list.setCurrentItem(child_item)
            else:
                # 无子节点：直接作为叶子节点
                leaf_item = QTreeWidgetItem(self._case_list)
                leaf_item.setText(0, f"[{case.priority.value}] {case.name}")
                leaf_item.setData(0, Qt.ItemDataRole.UserRole, case.case_id)
                if case.case_id == current_id:
                    self._case_list.setCurrentItem(leaf_item)

    def _on_case_selected(self, item: QTreeWidgetItem, prev):
        """选中用例时加载到表单"""
        if not item:
            return
        # 先保存当前表单到内存中的用例对象
        self._save_form_to_case()

        case_id = item.data(0, Qt.ItemDataRole.UserRole)
        for case in self._cases:
            if case.case_id == case_id:
                self._current_case = case
                self._load_case_form(case)
                break

    def _load_case_form(self, case: TestCase):
        """将用例数据加载到表单"""
        self._name_input.setText(case.name)
        self._category_input.setText(case.category)
        idx = self._priority_combo.findText(case.priority.value)
        if idx >= 0:
            self._priority_combo.setCurrentIndex(idx)
        self._tags_input.setText(",".join(case.tags))
        self._desc_input.setPlainText(case.description)
        self._retry_spin.setValue(
            case.retry_count if case.retry_count is not None else -1
        )
        self._timeout_spin.setValue(case.timeout)
        self._refresh_param_table(case)
        self._refresh_param_set_table(case)
        self._refresh_steps_table(case)

    def _refresh_steps_table(self, case: TestCase):
        """刷新步骤表格"""
        self._steps_table.setRowCount(len(case.steps))
        for row, step in enumerate(case.steps):
            self._steps_table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
            self._steps_table.setItem(row, 1, QTableWidgetItem(step.step_type.value))
            self._steps_table.setItem(row, 2, QTableWidgetItem(step.description))
            self._steps_table.setItem(row, 3, QTableWidgetItem(step.device_id))
            params_str = json.dumps(step.params, ensure_ascii=False)
            if len(params_str) > 60:
                params_str = params_str[:60] + "..."
            self._steps_table.setItem(row, 4, QTableWidgetItem(params_str))

    # ---------- 参数化测试 ----------

    def _get_param_names(self) -> List[str]:
        """从参数定义表读取所有参数名"""
        names = []
        for row in range(self._param_table.rowCount()):
            item = self._param_table.item(row, 0)
            name = item.text().strip() if item else ""
            if name:
                names.append(name)
        return names

    def _refresh_param_table(self, case: TestCase):
        """加载参数定义到表格"""
        self._param_updating = True
        params = case.parameters or {}
        self._param_table.setRowCount(len(params))
        for row, (name, default) in enumerate(params.items()):
            self._param_table.setItem(row, 0, QTableWidgetItem(str(name)))
            self._param_table.setItem(
                row, 1, QTableWidgetItem(str(default) if default is not None else "")
            )
        self._param_updating = False

    def _refresh_param_set_table(self, case: TestCase):
        """加载参数组到表格，列头根据参数定义动态生成"""
        self._param_updating = True
        param_names = self._get_param_names()
        sets = case.parameter_sets or []

        self._param_set_table.setColumnCount(len(param_names))
        self._param_set_table.setHorizontalHeaderLabels(param_names)
        self._param_set_table.setRowCount(len(sets))

        for row, param_set in enumerate(sets):
            for col, name in enumerate(param_names):
                value = param_set.get(name, "")
                self._param_set_table.setItem(
                    row, col, QTableWidgetItem(str(value) if value is not None else "")
                )
        self._param_updating = False

    def _on_param_table_changed(self, row: int, col: int):
        """参数名修改后，同步更新参数组表的列头"""
        if self._param_updating:
            return
        if col == 0 and self._current_case:
            # 参数名变了，需要刷新参数组表的列头和数据
            self._refresh_param_set_table(self._current_case)

    def _on_add_param(self):
        """添加参数定义行"""
        row = self._param_table.rowCount()
        self._param_table.insertRow(row)
        self._param_table.setItem(row, 0, QTableWidgetItem(f"param{row + 1}"))
        self._param_table.setItem(row, 1, QTableWidgetItem(""))

    def _on_remove_param(self):
        """删除选中的参数定义"""
        row = self._param_table.currentRow()
        if row >= 0:
            self._param_table.removeRow(row)
            if self._current_case:
                self._refresh_param_set_table(self._current_case)

    def _on_add_param_set(self):
        """添加一组参数"""
        if not self._get_param_names():
            QMessageBox.information(
                self, "提示", "请先在上方定义参数名和默认值"
            )
            return
        row = self._param_set_table.rowCount()
        self._param_set_table.insertRow(row)
        for col in range(self._param_set_table.columnCount()):
            self._param_set_table.setItem(row, col, QTableWidgetItem(""))

    def _on_remove_param_set(self):
        """删除选中的参数组"""
        row = self._param_set_table.currentRow()
        if row >= 0:
            self._param_set_table.removeRow(row)

    def _save_params_from_tables(self, case: TestCase):
        """从表格读取参数定义和参数组，保存到用例对象（表格为空时保留已有数据）"""
        # 读取参数定义（表格为空时不覆盖已有参数）
        if self._param_table.rowCount() > 0:
            params = {}
            for row in range(self._param_table.rowCount()):
                name_item = self._param_table.item(row, 0)
                default_item = self._param_table.item(row, 1)
                name = name_item.text().strip() if name_item else ""
                if name:
                    default_val = default_item.text().strip() if default_item else ""
                    params[name] = default_val
            case.parameters = params

        # 读取参数组（表格为空时不覆盖已有参数组）
        if self._param_set_table.rowCount() > 0:
            param_names = list(case.parameters.keys()) if case.parameters else []
            sets = []
            for row in range(self._param_set_table.rowCount()):
                param_set = {}
                for col, name in enumerate(param_names):
                    item = self._param_set_table.item(row, col)
                    param_set[name] = item.text().strip() if item else ""
                if any(v for v in param_set.values()):
                    sets.append(param_set)
            case.parameter_sets = sets

    def _on_import_params(self):
        """从外部文件导入参数组或测试用例定义"""
        if not self._current_case:
            QMessageBox.information(self, "提示", "请先选择一个用例")
            return

        from PyQt6.QtWidgets import QFileDialog
        file_path, selected_filter = QFileDialog.getOpenFileName(
            self,
            "导入文件",
            "",
            "支持的文件 (*.xlsx *.csv *.json *.yaml *.yml *.docx);;"
            "Excel 文件 (*.xlsx);;"
            "CSV 文件 (*.csv);;"
            "JSON 文件 (*.json);;"
            "YAML 文件 (*.yaml *.yml);;"
            "Word 文件 (*.docx)"
        )
        if not file_path:
            return
        self._last_import_path = file_path

        try:
            from core.testcase.param_importer import import_parameters
            parameters, parameter_sets = import_parameters(file_path)
        except Exception as e:
            QMessageBox.warning(self, "导入失败", f"无法导入文件:\n{e}")
            return

        if not parameter_sets:
            QMessageBox.information(self, "提示", "文件中未找到有效的数据")
            return

        # 自动检测：如果数据包含 case_id/keyword 等字段，当作用例定义导入
        first_set = parameter_sets[0]
        is_case_definition = any(k in first_set for k in ("case_id", "keyword", "description"))

        if is_case_definition:
            self._import_as_test_cases(parameter_sets)
        else:
            self._import_as_parameters(parameters, parameter_sets)

    def _import_as_parameters(self, parameters, parameter_sets):
        """将数据作为参数化测试的参数导入"""
        # 收集所有参数名
        all_param_names = set()
        for ps in parameter_sets:
            all_param_names.update(ps.keys())
        all_param_names = sorted(all_param_names)

        full_parameters = {}
        for name in all_param_names:
            if name in parameters:
                full_parameters[name] = parameters[name]
            elif parameter_sets:
                full_parameters[name] = parameter_sets[0].get(name, "")
            else:
                full_parameters[name] = ""

        self._param_updating = True
        self._param_table.setRowCount(len(full_parameters))
        for row, (name, default) in enumerate(full_parameters.items()):
            self._param_table.setItem(row, 0, QTableWidgetItem(str(name)))
            self._param_table.setItem(row, 1, QTableWidgetItem(str(default)))
        self._param_updating = False

        self._current_case.parameters = full_parameters
        self._current_case.parameter_sets = parameter_sets
        self._refresh_param_set_table(self._current_case)

        file_name = self._get_file_name(self._last_import_path if hasattr(self, '_last_import_path') else "")
        QMessageBox.information(
            self, "导入成功",
            f"已导入 {len(full_parameters)} 个参数、{len(parameter_sets)} 组参数值。\n"
            f"请在步骤中使用 {{参数名}} 占位符，然后点 Save 保存。"
        )

    def _import_as_test_cases(self, parameter_sets):
        """将用例定义数据转为父用例的 parameter_sets，由统一展开逻辑生成子节点"""
        if not parameter_sets:
            QMessageBox.information(self, "提示", "文件中未找到有效的数据")
            return

        # 收集所有字段名作为参数名
        all_param_names = set()
        for ps in parameter_sets:
            all_param_names.update(ps.keys())
        all_param_names = sorted(all_param_names)

        # 构建参数定义（默认值取第一组数据）
        parameters = {}
        for name in all_param_names:
            parameters[name] = str(parameter_sets[0].get(name, ""))

        # 更新参数定义表
        self._param_updating = True
        self._param_table.setRowCount(len(parameters))
        for row, (name, default) in enumerate(parameters.items()):
            self._param_table.setItem(row, 0, QTableWidgetItem(str(name)))
            self._param_table.setItem(row, 1, QTableWidgetItem(str(default)))
        self._param_updating = False

        # 将导入数据设为父用例的 parameter_sets
        self._current_case.parameters = parameters
        self._current_case.parameter_sets = parameter_sets

        # 刷新参数组表和树
        self._refresh_param_set_table(self._current_case)
        self._refresh_list()

        file_name = self._get_file_name(self._last_import_path if hasattr(self, '_last_import_path') else "")
        QMessageBox.information(
            self, "导入成功",
            f"已从文件导入 {len(parameter_sets)} 组参数，作为「{self._current_case.name}」的参数化子用例。\n"
            f"点 Save 保存到数据库。"
        )

    @staticmethod
    def _get_file_name(path: str) -> str:
        if not path:
            return ""
        return path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

    def _save_form_to_case(self):
        """将当前表单数据保存到内存中的用例对象"""
        if not self._current_case:
            return
        case = self._current_case
        case.name = self._name_input.text().strip() or "未命名用例"
        case.category = self._category_input.text().strip()
        case.priority = Priority(self._priority_combo.currentText())
        tags_text = self._tags_input.text().strip()
        case.tags = [t.strip() for t in tags_text.split(",") if t.strip()]
        case.description = self._desc_input.toPlainText()
        retry = self._retry_spin.value()
        case.retry_count = None if retry < 0 else retry
        case.timeout = self._timeout_spin.value()
        self._save_params_from_tables(case)

    # ---------- 用例操作 ----------

    def _on_add_case(self):
        """新增用例"""
        self._save_form_to_case()
        new_case = TestCase(name="新用例", priority=Priority.P1)
        self._cases.append(new_case)
        self._current_case = new_case
        self._refresh_list()
        self._load_case_form(new_case)
        self._name_input.setFocus()
        self._name_input.selectAll()

    def _on_delete_case(self):
        """删除用例（支持多选）"""
        selected = self._get_selected_cases()
        if not selected:
            QMessageBox.information(self, "提示", "请先选中要删除的用例")
            return
        names = [c.name for c in selected]
        if len(names) == 1:
            msg = f"确定删除用例 '{names[0]}' 吗？"
        else:
            msg = f"确定删除以下 {len(names)} 个用例吗？\n" + "\n".join(f"  • {n}" for n in names[:10])
            if len(names) > 10:
                msg += f"\n  ... 等 {len(names)} 个"
        reply = QMessageBox.question(self, "确认删除", msg)
        if reply == QMessageBox.StandardButton.Yes:
            deleted = 0
            failed = []
            # 按父节点分组，批量移除子用例对应的参数组
            parent_updates = {}  # parent_id -> [indices to remove]
            for case in selected:
                if case in self._cases:
                    self._cases.remove(case)
                if case.parent_id:
                    # 子用例：从 case_id 提取索引
                    suffix = case.case_id.split("_p")[-1]
                    try:
                        idx = int(suffix) - 1
                        if case.parent_id not in parent_updates:
                            parent_updates[case.parent_id] = []
                        parent_updates[case.parent_id].append(idx)
                    except ValueError:
                        pass
                    self._db.delete_case(case.case_id)
                    deleted += 1
                else:
                    # 父用例：直接删除
                    if self._db.delete_case(case.case_id):
                        deleted += 1
                        # 移除内存中该父节点的展开子节点
                        self._cases = [c for c in self._cases
                                       if c.parent_id != case.case_id]
                    else:
                        failed.append(case.name)

            # 批量更新父用例的参数组
            for parent_id, indices in parent_updates.items():
                parent = self._db.load_case(parent_id)
                if parent and parent.parameter_sets:
                    for idx in sorted(set(indices), reverse=True):
                        if 0 <= idx < len(parent.parameter_sets):
                            parent.parameter_sets.pop(idx)
                    if not parent.parameter_sets:
                        parent.parameters = {}
                    if self._db.save_case(parent):
                        # 更新内存中的父用例
                        for c in self._cases:
                            if c.case_id == parent_id:
                                c.parameter_sets = parent.parameter_sets
                                c.parameters = parent.parameters
                                break
                    else:
                        failed.append(f"{parent.name} 的参数组")

            if failed:
                QMessageBox.warning(self, "删除失败", f"以下用例删除失败:\n{', '.join(failed[:5])}")
            self._current_case = self._cases[0] if self._cases else None
            self._refresh_list()
            if self._current_case:
                self._load_case_form(self._current_case)
            logger.info(f"已删除 {deleted} 个用例，失败 {len(failed)} 个")

    def _on_duplicate_case(self):
        """复制用例（支持多选）"""
        selected = self._get_selected_cases()
        if not selected:
            return
        self._save_form_to_case()
        new_cases = []
        for case in selected:
            new_case = TestCase.from_dict(case.to_dict())
            new_case.name = f"{case.name} (副本)"
            new_case.case_id = ""
            new_case.parent_id = None  # 副本作为独立根节点
            self._cases.append(new_case)
            new_cases.append(new_case)
        self._current_case = new_cases[-1] if new_cases else None
        self._refresh_list()
        if self._current_case:
            self._load_case_form(self._current_case)

    def _get_selected_cases(self) -> List[TestCase]:
        """获取当前选中的用例（支持多选）"""
        items = self._case_list.selectedItems()
        if not items:
            return [self._current_case] if self._current_case else []
        case_map = {c.case_id: c for c in self._cases}
        result = []
        for item in items:
            case_id = item.data(0, Qt.ItemDataRole.UserRole)
            if case_id and case_id in case_map:
                result.append(case_map[case_id])
        return result

    def _on_selection_changed(self):
        """多选变化时更新当前用例（单选时）"""
        items = self._case_list.selectedItems()
        if len(items) == 1:
            item = items[0]
            case_id = item.data(0, Qt.ItemDataRole.UserRole)
            for case in self._cases:
                if case.case_id == case_id:
                    if self._current_case != case:
                        self._save_form_to_case()
                        self._current_case = case
                        self._load_case_form(case)
                    break

    # ---------- 步骤操作 ----------

    def _on_add_step(self):
        if not self._current_case:
            QMessageBox.information(self, "提示", "请先选择一个用例")
            return
        dlg = StepEditDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            step = dlg.get_step()
            self._current_case.steps.append(step)
            self._refresh_steps_table(self._current_case)

    def _on_edit_step(self):
        if not self._current_case:
            return
        row = self._steps_table.currentRow()
        if row < 0 or row >= len(self._current_case.steps):
            QMessageBox.information(self, "提示", "请先选择一个步骤")
            return
        step = self._current_case.steps[row]
        dlg = StepEditDialog(step, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._refresh_steps_table(self._current_case)

    def _on_remove_step(self):
        if not self._current_case:
            return
        row = self._steps_table.currentRow()
        if row < 0 or row >= len(self._current_case.steps):
            return
        self._current_case.steps.pop(row)
        self._refresh_steps_table(self._current_case)

    def _on_move_step(self, direction: int):
        """上移/下移步骤"""
        if not self._current_case:
            return
        row = self._steps_table.currentRow()
        if row < 0:
            return
        new_row = row + direction
        if 0 <= new_row < len(self._current_case.steps):
            steps = self._current_case.steps
            steps[row], steps[new_row] = steps[new_row], steps[row]
            self._refresh_steps_table(self._current_case)
            self._steps_table.selectRow(new_row)

    # ---------- 保存 ----------

    # ---------- 按描述重新生成 ----------

    @staticmethod
    def _case_app_target(case: TestCase):
        """从用例已有步骤里找出目标应用包名/启动页（供重新生成时复用）"""
        for step in case.steps:
            if step.step_type is StepType.APP_START:
                package = str(step.params.get("package") or "").strip()
                activity = str(step.params.get("activity") or "").strip()
                if package:
                    return package, activity
        return "", ""

    def _on_regenerate(self):
        """按当前「描述」重新生成步骤，并替换当前用例的步骤"""
        if not self._current_case:
            QMessageBox.information(self, "提示", "请先选择一个用例")
            return
        if self._regen_worker is not None:
            return
        description = self._desc_input.toPlainText().strip()
        if not description:
            QMessageBox.information(
                self, "提示", "请先在「描述」里填写测试需求，再重新生成"
            )
            return

        # 先保存表单，避免名称/描述丢失
        self._save_form_to_case()
        package, activity = self._case_app_target(self._current_case)

        # 构建参数上下文，让生成器保留 {param} 占位符
        param_context = ""
        param_names = None
        case = self._current_case
        if case.parameters or case.parameter_sets:
            param_names = list(case.parameters.keys()) if case.parameters else []
            if not param_names and case.parameter_sets:
                param_names = list(case.parameter_sets[0].keys())
            param_context = "\n\n【参数化测试要求】\n"
            if case.parameter_sets:
                param_context += f"当前用例含 {len(case.parameter_sets)} 组参数，参数项：{', '.join(param_names)}\n"
                for i, ps in enumerate(case.parameter_sets[:3], 1):
                    param_context += f"示例{i}: {json.dumps(ps, ensure_ascii=False)}\n"
            else:
                param_context += f"参数项：{', '.join(param_names)}（默认值）\n"
            param_context += (
                "\n重要：生成步骤时必须使用 {参数名} 占位符格式！\n"
                "例如参数项有 keyword，则步骤描述写「输入 {keyword}」，params.text 写 \"{keyword}\"。\n"
                "不要写死具体值，必须用 {参数名} 格式。\n"
            )

        self._btn_regen.setEnabled(False)
        self._regen_status.setText("🤖 正在按描述重新生成步骤...")
        self._regen_status.setStyleSheet("color: #8a6d3b;")

        from config.settings import ConfigManager
        from core.ai.assistant import AIAssistant
        from ui.widgets.ai_worker import AIWorker

        assistant = AIAssistant(ConfigManager().config.ai)
        if not assistant.llm_available:
            self._regen_status.setText(
                "🤖 未配置 AI 模型，正在用内置规则按描述重新生成..."
            )
        worker = AIWorker(lambda: assistant.generate(
            description, self._device_mgr,
            app_package=package, app_activity=activity,
            param_context=param_context,
            param_names=param_names,
        ))
        worker.succeeded.connect(self._on_regenerated)
        worker.failed.connect(self._on_regenerate_failed)
        self._regen_worker = worker
        worker.start()

    def _on_regenerated(self, result):
        self._regen_worker = None
        self._btn_regen.setEnabled(True)
        cases, source, note = result
        if not cases:
            self._regen_status.setText("⚠ 未生成有效步骤，请补充描述细节后重试")
            self._regen_status.setStyleSheet("color: #c0392b;")
            return

        generated = cases[0]
        case = self._current_case
        if case is None:
            return

        # 先把旧步骤的描述记下来，用于判断“重新生成后是否真的变了”
        old_steps = list(case.steps)
        case.steps = generated.steps
        # 描述/分类等留空时用生成结果补全，已有内容保持不变
        if not case.description.strip():
            case.description = generated.description
        if not case.category.strip():
            case.category = generated.category
        if generated.tags:
            existing = [t for t in case.tags if t]
            merged = existing + [t for t in generated.tags if t not in existing]
            case.tags = merged

        self._load_case_form(case)
        self._refresh_steps_table(case)

        source_text = "AI 模型" if source == "llm" else "内置规则"
        extra = f"（共生成 {len(cases)} 个用例，已用第 1 个替换当前步骤）" \
            if len(cases) > 1 else ""
        unchanged = self._steps_signature(old_steps) == self._steps_signature(
            generated.steps)
        if unchanged and source != "llm":
            self._regen_status.setText(
                f"⚠ 生成结果与当前步骤一致（来源：{source_text}）。"
                "若要得到不同步骤，请修改「描述」中的操作用词；"
                "配置 AI 模型后可按语义重新生成。"
            )
            self._regen_status.setStyleSheet("color: #8a6d3b;")
        else:
            self._regen_status.setText(
                f"✅ 已生成 {len(generated.steps)} 个步骤（来源：{source_text}），"
                f"记得点「Save」保存{extra}"
            )
            self._regen_status.setStyleSheet("color: #2e7d32;")

        if note:
            self._regen_status.setText(f"{self._regen_status.text()}\n{note}")

    @staticmethod
    def _steps_signature(steps) -> list:
        """步骤指纹：类型 + 参数 + 描述，用于比较生成前后是否发生变化"""
        return [
            (s.step_type.value,
             s.description or "",
             tuple(sorted((str(k), str(v)) for k, v in (s.params or {}).items())))
            for s in steps
        ]

    def _on_regenerate_failed(self, error: str):
        self._regen_worker = None
        self._btn_regen.setEnabled(True)
        self._regen_status.setText(f"❌ 生成失败：{error}")
        self._regen_status.setStyleSheet("color: #c0392b;")

    def _on_save(self):
        """保存所有用例到数据库（跳过参数化子用例，它们由父用例的 parameter_sets 动态生成）"""
        self._save_form_to_case()
        saved = 0
        for case in self._cases:
            # 跳过参数化展开的子用例（有 parent_id），它们不入库
            if case.parent_id:
                continue
            if case.name.strip() and case.name != "新用例":
                if self._db.save_case(case):
                    saved += 1
        logger.info(f"已保存 {saved} 个用例")
        self.data_changed.emit()
        self.reload()
        QMessageBox.information(self, "保存成功", f"已保存 {saved} 个用例到数据库")
