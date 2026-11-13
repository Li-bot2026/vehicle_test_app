"""
AI 助手对话框
==============
两大能力：
1. 用自然语言描述测试需求，自动生成可执行测试用例
2. 让 AI 识别已保存的用例，规划自动执行顺序并一键执行
"""

import json
import re
import threading

from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QWidget, QTextEdit, QLineEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QTextBrowser,
    QMessageBox, QSplitter, QComboBox, QFileDialog
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

from loguru import logger

from core.device.adb_device import ADBDevice

from .ai_worker import AIWorker
from .dialog_utils import allow_resize


# 各步骤类型对设备类型的依赖，用于给生成用例补全 device_id
DEVICE_TYPE_BY_STEP = {
    "send_adb": "ADB",
    "screenshot": "ADB",
    "send_serial": "SERIAL",
    "send_network": "NETWORK",
    "app_start": "ADB",
    "ui_tap": "ADB",
    "ui_input": "ADB",
    "ui_wait_text": "ADB",
    "ui_swipe": "ADB",
    "agent_task": "ADB",
}

_AUTO_APP_LABEL = "（自动：当前前台应用）"
_PACKAGE_RE = re.compile(r"^[A-Za-z][\w]*(\.[\w]+)+$")


class AIAssistantDialog(QDialog):
    """AI 测试助手"""

    cases_saved = pyqtSignal(int)
    run_cases_requested = pyqtSignal(object)

    app_list_ready = pyqtSignal(object, object, object)   # device, packages, foreground
    app_resolved = pyqtSignal(object, object, object)     # device, package, activity

    def __init__(self, assistant, db, device_manager, parent=None):
        super().__init__(parent)
        self._assistant = assistant
        self._db = db
        self._dm = device_manager

        self._generated = []
        self._ordered = []
        self._gen_worker = None
        self._plan_worker = None
        self._imported_params = None       # 导入的参数定义 {name: default}
        self._imported_param_sets = []     # 导入的参数组 [{name: value}, ...]
        self._last_import_path = ""

        self._app_activity = ""
        self._foreground_package = ""
        self._foreground_activity = ""
        self._app_loading = False

        self.setWindowTitle("AI 测试助手")
        self.resize(960, 700)
        allow_resize(self)
        self._init_ui()

        self.app_list_ready.connect(self._apply_app_list)
        self.app_resolved.connect(self._apply_app_resolved)
        self._load_app_list()

    # ---------- 界面 ----------

    def _init_ui(self):
        layout = QVBoxLayout(self)

        self._mode_label = QLabel()
        self._mode_label.setStyleSheet("color: #555;")
        layout.addWidget(self._mode_label)
        self._refresh_mode_label()

        tabs = QTabWidget()
        tabs.addTab(self._build_generate_tab(), "🧠 生成用例并自动执行")
        tabs.addTab(self._build_plan_tab(), "📋 识别用例并规划执行")
        layout.addWidget(tabs)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    def _refresh_mode_label(self):
        if self._assistant.llm_available:
            self._mode_label.setText(
                "🤖 当前使用 AI 模型生成与规划；失败时由模型给出原因与解决方法。"
            )
        else:
            self._mode_label.setText(
                "🔧 未配置 AI 模型，当前使用内置规则/模板（离线可用）。"
                "可在 设置 → 偏好设置 → AI 中配置模型以获得更强能力。"
            )

    # ---------- 生成用例页 ----------

    def _build_generate_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        layout.addWidget(QLabel("用自然语言描述测试需求："))
        self._gen_input = QTextEdit()
        self._gen_input.setMaximumHeight(88)
        self._gen_input.setPlaceholderText(
            "例如：打开设置，进入WLAN页面后点击网络列表，输入密码并确认；\n"
            "或者：打开游戏中心，选择扫雷，点击开始游戏，进入游戏后获得胜利"
            "（游戏/视频等画布界面会自动用 OCR 或视觉模型定位文字）"
        )
        layout.addWidget(self._gen_input)

        app_row = QHBoxLayout()
        app_row.addWidget(QLabel("目标应用:"))
        self._app_combo = QComboBox()
        self._app_combo.setEditable(True)
        self._app_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._app_combo.setMinimumWidth(260)
        self._app_combo.addItem(_AUTO_APP_LABEL, "")
        self._app_combo.currentIndexChanged.connect(self._on_app_changed)
        self._app_combo.lineEdit().editingFinished.connect(self._on_app_changed)
        app_row.addWidget(self._app_combo, 1)

        self._btn_app_refresh = QPushButton("🔄 读取应用列表")
        self._btn_app_refresh.clicked.connect(self._load_app_list)
        app_row.addWidget(self._btn_app_refresh)
        layout.addLayout(app_row)

        info_row = QHBoxLayout()
        self._app_info = QLabel("包名/启动页：连接 ADB 设备后自动读取")
        self._app_info.setStyleSheet("color: #666; font-size: 11px;")
        self._app_info.setWordWrap(True)
        self._app_info.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        info_row.addWidget(self._app_info, 1)
        self._btn_copy_app = QPushButton("📋 复制包名")
        self._btn_copy_app.setEnabled(False)
        self._btn_copy_app.clicked.connect(self._copy_package)
        info_row.addWidget(self._btn_copy_app)
        layout.addLayout(info_row)

        gen_row = QHBoxLayout()
        self._btn_import_params = QPushButton("📥 导入参数文件")
        self._btn_import_params.setToolTip("从 Excel / CSV / JSON / YAML 文件导入参数化数据")
        self._btn_import_params.clicked.connect(self._on_import_params)
        gen_row.addWidget(self._btn_import_params)

        self._btn_generate = QPushButton("🧠 AI 生成并识别用例")
        self._btn_generate.clicked.connect(self._on_generate)
        gen_row.addWidget(self._btn_generate)

        self._gen_status = QLabel("")
        self._gen_status.setStyleSheet("color: #8a6d3b;")
        gen_row.addWidget(self._gen_status, 1)
        layout.addLayout(gen_row)

        self._param_hint = QLabel("")
        self._param_hint.setStyleSheet("color: #2e7d32; font-size: 12px;")
        self._param_hint.setWordWrap(True)
        layout.addWidget(self._param_hint)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(6)

        self._gen_table = QTableWidget()
        self._gen_table.setColumnCount(6)
        self._gen_table.setHorizontalHeaderLabels(
            ["选择", "用例名称", "分类", "优先级", "步骤数", "描述"]
        )
        gen_header = self._gen_table.horizontalHeader()
        for col in range(6):
            gen_header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        gen_header.setStretchLastSection(True)
        for col, width in enumerate((56, 220, 90, 70, 60, 260)):
            self._gen_table.setColumnWidth(col, width)
        self._gen_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._gen_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._gen_table.itemSelectionChanged.connect(self._on_generated_selected)
        splitter.addWidget(self._gen_table)

        self._gen_detail = QTextBrowser()
        self._gen_detail.setFont(QFont("Consolas", 9))
        self._gen_detail.setMinimumHeight(140)
        splitter.addWidget(self._gen_detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        save_row = QHBoxLayout()
        self._btn_save = QPushButton("💾 保存选中用例")
        self._btn_save.setEnabled(False)
        self._btn_save.clicked.connect(lambda: self._save_generated(run=False))
        save_row.addWidget(self._btn_save)

        self._btn_save_run = QPushButton("▶ 保存并立即执行")
        self._btn_save_run.setEnabled(False)
        self._btn_save_run.clicked.connect(lambda: self._save_generated(run=True))
        save_row.addWidget(self._btn_save_run)

        save_row.addStretch()
        layout.addLayout(save_row)

        return page

    # ---------- 目标应用 ----------

    def _adb_device(self):
        try:
            for device in self._dm.get_all_devices().values():
                if isinstance(device, ADBDevice):
                    return device
        except Exception:
            pass
        return None

    def _selected_package(self) -> str:
        data = self._app_combo.currentData()
        if data:
            return str(data)
        text = self._app_combo.currentText().strip()
        if text == _AUTO_APP_LABEL:
            return ""
        return text if _PACKAGE_RE.match(text) else ""

    def _load_app_list(self):
        """后台读取已安装应用与当前前台应用"""
        device = self._adb_device()
        if device is None:
            self._app_info.setText(
                "包名/启动页：未连接 ADB 设备，生成时将不带应用上下文"
            )
            return
        if self._app_loading:
            return
        self._app_loading = True
        self._btn_app_refresh.setEnabled(False)
        self._btn_app_refresh.setText("读取中...")

        def worker():
            try:
                packages = device.list_packages()
            except Exception as e:
                logger.debug(f"读取应用列表失败: {e}")
                packages = []
            try:
                foreground = device.get_foreground_app()
            except Exception:
                foreground = (None, None)
            self.app_list_ready.emit(device, packages, foreground)

        threading.Thread(target=worker, daemon=True, name="AIAppList").start()

    def _apply_app_list(self, device, packages, foreground):
        self._app_loading = False
        self._btn_app_refresh.setEnabled(True)
        self._btn_app_refresh.setText("🔄 读取应用列表")

        current = self._selected_package()
        self._app_combo.blockSignals(True)
        self._app_combo.clear()
        self._app_combo.addItem(_AUTO_APP_LABEL, "")
        for package in packages or []:
            self._app_combo.addItem(package, package)
        if current:
            idx = self._app_combo.findData(current)
            if idx >= 0:
                self._app_combo.setCurrentIndex(idx)
            else:
                self._app_combo.setCurrentText(current)
        self._app_combo.blockSignals(False)

        package, activity = foreground or (None, None)
        if package:
            self._foreground_package = package
            self._foreground_activity = activity or ""
            self._app_info.setText(
                f"包名: {package}\n启动页: {activity or '未知'}"
                "（自动使用当前前台应用）"
            )
            self._btn_copy_app.setEnabled(True)
        else:
            self._app_info.setText("包名/启动页：未解析到前台应用")

        self._on_app_changed()

    def _effective_package(self) -> str:
        """当前实际生效的包名：用户显式选择 > 自动前台"""
        return self._selected_package() or self._foreground_package

    def _on_app_changed(self, _idx=0):
        package = self._effective_package()
        if not package:
            self._app_activity = ""
            self._app_info.setText("包名/启动页：未解析到前台应用")
            return
        self._resolve_activity(package)

    def _resolve_activity(self, package: str):
        device = self._adb_device()
        self._app_activity = ""
        self._btn_copy_app.setEnabled(True)
        if device is None:
            self._app_info.setText(
                f"包名: {package}\n启动页: 未连接设备，执行时自动解析"
            )
            return

        self._app_info.setText(f"包名: {package}\n启动页: 解析中...")

        def worker():
            try:
                activity = device.resolve_launch_activity(package)
            except Exception:
                activity = ""
            self.app_resolved.emit(device, package, activity)

        threading.Thread(target=worker, daemon=True, name="AIAppActivity").start()

    def _apply_app_resolved(self, device, package, activity):
        if package != self._effective_package():
            return
        self._app_activity = activity or ""
        auto_hint = ("" if self._selected_package()
                     else "\n（自动使用当前前台应用）")
        if activity:
            self._app_info.setText(
                f"包名: {package}\n启动页: {activity}{auto_hint}"
            )
        else:
            self._app_info.setText(
                f"包名: {package}\n启动页: 未解析到（执行时按包名自动拉起）{auto_hint}"
            )

    def _copy_package(self):
        package = self._selected_package() or self._foreground_package
        if not package:
            return
        QApplication.clipboard().setText(package)
        self._gen_status.setText(f"已复制包名: {package}")
        self._gen_status.setStyleSheet("color: #2e7d32;")

    # ---------- 参数文件导入 ----------

    def _on_import_params(self):
        """打开文件对话框，导入参数化数据"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "导入参数文件", self._last_import_path,
            "参数文件 (*.xlsx *.xls *.csv *.json *.yaml *.yml);;所有文件 (*)"
        )
        if not file_path:
            return

        from core.testcase.param_importer import import_parameters

        try:
            params, param_sets = import_parameters(file_path)
        except Exception as e:
            QMessageBox.warning(self, "导入失败", str(e))
            return

        self._imported_params = params
        self._imported_param_sets = param_sets
        self._last_import_path = file_path

        param_names = list(params.keys()) if params else []
        self._param_hint.setText(
            f"✅ 已导入 {len(param_sets)} 组参数数据，"
            f"参数项：{', '.join(param_names) if param_names else '（无）'}"
        )
        logger.info(
            f"AI 助手导入参数文件: {file_path}，"
            f"{len(param_sets)} 组参数，参数项: {param_names}"
        )

    # ---------- 生成 ----------

    def _on_generate(self):
        if self._gen_worker is not None:
            return
        description = self._gen_input.toPlainText().strip()
        if not description:
            QMessageBox.information(self, "提示", "请先描述测试需求")
            return

        # 构建参数化上下文（仅传给 LLM，离线生成器用 param_names 注入占位符）
        param_context = ""
        param_names = list(self._imported_params.keys()) if self._imported_params else []
        if not param_names and self._imported_param_sets:
            param_names = list(self._imported_param_sets[0].keys())
        if param_names:
            param_context = "\n\n【参数化测试要求】\n"
            if self._imported_param_sets:
                param_context += f"已导入 {len(self._imported_param_sets)} 组参数数据，参数项：{', '.join(param_names)}\n"
                # 展示前 3 组参数示例
                for i, ps in enumerate(self._imported_param_sets[:3], 1):
                    param_context += f"示例{i}: {json.dumps(ps, ensure_ascii=False)}\n"
                if len(self._imported_param_sets) > 3:
                    param_context += f"... 共 {len(self._imported_param_sets)} 组\n"
            else:
                param_context += f"参数项：{', '.join(param_names)}（仅默认值，无多组数据）\n"
            param_context += (
                "\n重要：生成步骤时必须使用 {参数名} 占位符格式！\n"
                "例如参数项有 keyword，则步骤描述写「输入 {keyword}」，params.text 写 \"{keyword}\"。\n"
                "执行时引擎会自动将 {keyword} 替换为每组参数的实际值。\n"
                "不要写死具体值，不要用 [参数化文件的测试数据] 这种文字，必须用 {参数名} 格式。\n"
                "用例名称和描述中也可以用 {参数名} 占位符。\n"
            )

        package = self._effective_package()
        activity = self._app_activity

        self._btn_generate.setEnabled(False)
        self._gen_status.setText(" 正在读取应用界面并生成用例...")
        self._gen_status.setStyleSheet("color: #8a6d3b;")

        worker = AIWorker(lambda: self._assistant.generate(
            description, self._dm, app_package=package, app_activity=activity,
            param_context=param_context,
            param_names=param_names or None
        ))
        worker.succeeded.connect(self._on_generated)
        worker.failed.connect(self._on_generate_failed)
        self._gen_worker = worker
        worker.start()

    def _on_generated(self, result):
        self._gen_worker = None
        self._btn_generate.setEnabled(True)

        cases, source, note = result
        if not cases:
            self._gen_status.setText("⚠ 未生成有效用例，请补充需求细节后重试")
            return

        missing = self._fill_device_ids(cases)

        self._generated = cases

        source_text = "AI 模型" if source == "llm" else "内置规则"
        self._gen_status.setText(f"✅ 已生成 {len(cases)} 个用例（来源：{source_text}）")
        self._gen_status.setStyleSheet("color: #2e7d32;")
        if note:
            self._gen_status.setText(f"{self._gen_status.text()}｜{note}")
            self._gen_status.setStyleSheet("color: #8a6d3b;")
        if missing:
            self._gen_status.setText(
                f"{self._gen_status.text()}；⚠ 缺少设备类型："
                f"{'、'.join(sorted(missing))}，相关步骤执行前请先连接设备"
            )
            self._gen_status.setStyleSheet("color: #8a6d3b;")

        self._gen_table.setRowCount(len(cases))
        for row, case in enumerate(cases):
            check_item = QTableWidgetItem()
            check_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable |
                Qt.ItemFlag.ItemIsEnabled |
                Qt.ItemFlag.ItemIsSelectable
            )
            check_item.setCheckState(Qt.CheckState.Checked)
            self._gen_table.setItem(row, 0, check_item)
            self._gen_table.setItem(row, 1, QTableWidgetItem(case.name))
            self._gen_table.setItem(row, 2, QTableWidgetItem(case.category or "-"))
            self._gen_table.setItem(row, 3, QTableWidgetItem(case.priority.value))
            self._gen_table.setItem(row, 4, QTableWidgetItem(str(case.total_steps)))
            self._gen_table.setItem(
                row, 5, QTableWidgetItem((case.description or "")[:60])
            )

        self._gen_table.selectRow(0)
        self._btn_save.setEnabled(True)
        self._btn_save_run.setEnabled(True)

    def _on_generate_failed(self, message: str):
        self._gen_worker = None
        self._btn_generate.setEnabled(True)
        self._gen_status.setText(f"❌ 生成失败：{message}")
        self._gen_status.setStyleSheet("color: #c0392b;")

    def _fill_device_ids(self, cases):
        """给生成的步骤补全可用设备 ID（按设备类型匹配）"""
        by_type = {}
        available_ids = set()
        for dev in self._assistant.devices_summary(self._dm):
            dev_id = dev.get("device_id", "")
            if dev_id:
                available_ids.add(dev_id)
            dev_type = str(dev.get("type", "")).upper()
            if dev_type and dev_type not in by_type:
                by_type[dev_type] = dev_id

        missing_types = set()
        for case in cases:
            for steps in (case.setup_steps, case.steps, case.teardown_steps):
                for step in steps:
                    need = DEVICE_TYPE_BY_STEP.get(step.step_type.value)
                    if not need:
                        continue
                    real_id = by_type.get(need)
                    # 模型有时会编造 adb_auto/auto/不存在 的 ID，统一换成真实设备
                    if step.device_id in available_ids:
                        continue
                    if real_id:
                        step.device_id = real_id
                    else:
                        step.device_id = ""
                        missing_types.add(need)
        return missing_types

    def _on_generated_selected(self):
        rows = self._gen_table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        if row >= len(self._generated):
            return
        case = self._generated[row]

        lines = [f"用例：{case.name}", f"分类：{case.category} | 优先级：{case.priority.value}", ""]
        for i, step in enumerate(case.steps, 1):
            lines.append(
                f"{i}. [{step.step_type.value}] {step.description}"
                f" | 设备: {step.device_id or '-'} | 超时: {step.timeout:g}s"
            )
            if step.params:
                lines.append(
                    "   参数: " + json.dumps(step.params, ensure_ascii=False)
                )
            if step.expected is not None:
                lines.append(f"   预期: {step.expected}")
        self._gen_detail.setPlainText("\n".join(lines))

    def _checked_generated(self):
        cases = []
        for row in range(self._gen_table.rowCount()):
            item = self._gen_table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                if row < len(self._generated):
                    cases.append(self._generated[row])
        return cases

    def _save_generated(self, run: bool):
        cases = self._checked_generated()
        if not cases:
            QMessageBox.information(self, "提示", "请先勾选要保存的用例")
            return

        # 如果导入了参数文件，将参数组附加到每个用例上，使其成为参数化父节点
        if self._imported_param_sets or self._imported_params:
            for case in cases:
                if self._imported_param_sets:
                    case.parameter_sets = [dict(ps) for ps in self._imported_param_sets]
                if self._imported_params:
                    case.parameters = dict(self._imported_params)

        saved_cases = [case for case in cases if self._db.save_case(case)]
        saved = len(saved_cases)
        if saved == 0:
            QMessageBox.warning(self, "失败", "用例保存失败，请查看日志")
            return
        if saved < len(cases):
            self._gen_status.setText(
                f"⚠ 仅保存成功 {saved}/{len(cases)} 个用例，失败的用例未执行"
            )
            self._gen_status.setStyleSheet("color: #8a6d3b;")

        param_note = ""
        if self._imported_param_sets or self._imported_params:
            param_note = f"（含 {len(self._imported_param_sets)} 组参数）"
        logger.info(f"AI 生成用例已保存: {saved} 个{param_note}")
        self.cases_saved.emit(saved)

        if run:
            self.run_cases_requested.emit(saved_cases)
            self.accept()
        else:
            self._gen_status.setText(
                f"✅ 已保存 {saved} 个用例到用例库{param_note}"
            )
            self._gen_status.setStyleSheet("color: #2e7d32;")

    # ---------- 规划执行页 ----------

    def _build_plan_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        tip = QLabel(
            "AI 会识别用例库中的全部用例，结合优先级、分类与步骤依赖给出执行顺序，"
            "随后自动按该顺序执行。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #555;")
        layout.addWidget(tip)

        row = QHBoxLayout()
        self._plan_instruction = QLineEdit()
        self._plan_instruction.setPlaceholderText(
            "可选：补充执行要求，例如「先跑冒烟用例，再跑耗时长的用例」"
        )
        row.addWidget(self._plan_instruction, 1)

        self._btn_plan = QPushButton("🧠 识别并规划")
        self._btn_plan.clicked.connect(self._on_plan)
        row.addWidget(self._btn_plan)
        layout.addLayout(row)

        self._plan_status = QLabel("")
        self._plan_status.setStyleSheet("color: #8a6d3b;")
        layout.addWidget(self._plan_status)

        self._plan_table = QTableWidget()
        self._plan_table.setColumnCount(5)
        self._plan_table.setHorizontalHeaderLabels(
            ["执行顺序", "用例名称", "分类", "优先级", "步骤数"]
        )
        plan_header = self._plan_table.horizontalHeader()
        for col in range(5):
            plan_header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        plan_header.setStretchLastSection(True)
        for col, width in enumerate((80, 240, 100, 70, 70)):
            self._plan_table.setColumnWidth(col, width)
        self._plan_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._plan_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        layout.addWidget(self._plan_table, 1)

        self._plan_reason = QLabel("")
        self._plan_reason.setWordWrap(True)
        self._plan_reason.setStyleSheet("color: #555;")
        layout.addWidget(self._plan_reason)

        run_row = QHBoxLayout()
        self._btn_run_plan = QPushButton("▶ 按此顺序自动执行")
        self._btn_run_plan.setEnabled(False)
        self._btn_run_plan.clicked.connect(self._on_run_plan)
        run_row.addWidget(self._btn_run_plan)
        run_row.addStretch()
        layout.addLayout(run_row)

        return page

    def _on_plan(self):
        if self._plan_worker is not None:
            return

        cases = self._db.list_cases(limit=300)
        if not cases:
            QMessageBox.information(self, "提示", "用例库为空，请先生成或导入用例")
            return

        self._btn_plan.setEnabled(False)
        self._btn_run_plan.setEnabled(False)
        self._plan_status.setText(f"🧠 正在识别 {len(cases)} 个用例并规划执行顺序...")
        self._plan_status.setStyleSheet("color: #8a6d3b;")

        instruction = self._plan_instruction.text().strip()
        worker = AIWorker(lambda: self._assistant.plan(cases, instruction))
        worker.succeeded.connect(self._on_planned)
        worker.failed.connect(self._on_plan_failed)
        self._plan_worker = worker
        worker.start()

    def _on_planned(self, result):
        self._plan_worker = None
        self._btn_plan.setEnabled(True)

        ordered, reason, source = result
        if not ordered:
            self._plan_status.setText("⚠ 没有可执行的用例")
            return

        self._ordered = ordered
        source_text = "AI 模型" if source == "llm" else "内置排序规则"
        self._plan_status.setText(f"✅ 已规划 {len(ordered)} 个用例（来源：{source_text}）")
        self._plan_status.setStyleSheet("color: #2e7d32;")
        self._plan_reason.setText(f"排序理由：{reason}")

        self._plan_table.setRowCount(len(ordered))
        for row, case in enumerate(ordered):
            idx_item = QTableWidgetItem(str(row + 1))
            idx_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._plan_table.setItem(row, 0, idx_item)
            self._plan_table.setItem(row, 1, QTableWidgetItem(case.name))
            self._plan_table.setItem(row, 2, QTableWidgetItem(case.category or "-"))
            self._plan_table.setItem(row, 3, QTableWidgetItem(case.priority.value))
            self._plan_table.setItem(row, 4, QTableWidgetItem(str(case.total_steps)))

        self._btn_run_plan.setEnabled(True)

    def _on_plan_failed(self, message: str):
        self._plan_worker = None
        self._btn_plan.setEnabled(True)
        self._plan_status.setText(f"❌ 规划失败：{message}")
        self._plan_status.setStyleSheet("color: #c0392b;")

    def _on_run_plan(self):
        if not self._ordered:
            return
        self.run_cases_requested.emit(list(self._ordered))
        self.accept()
