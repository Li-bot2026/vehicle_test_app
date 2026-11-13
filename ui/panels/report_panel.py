"""
报告面板
=========
显示测试报告、历史记录、导出功能。

支持：
- 勾选框多选 + 全选（三态）
- 批量删除执行记录 / 删除全部
- 导出选中（或当前选中行）记录，而非仅最近一次
- 双击行查看明细
"""

import json
from typing import List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QGroupBox, QTextEdit, QFileDialog,
    QSplitter, QComboBox, QProgressBar, QAbstractItemView,
    QMessageBox, QCheckBox
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QFont, QColor, QDesktopServices

from loguru import logger

from core.reporting.generator import ReportGenerator
from core.reporting.allure_report import (
    allure_available, allure_version, generate_allure_report, allure_server
)
from ..styles.theme import Tokens as T
from core.testcase.database import TestDatabase
from core.testcase.model import TestSuite


class ReportPanel(QWidget):
    """
    报告面板
    ---------
    查看历史报告、批量管理、导出多种格式。
    """

    def __init__(self, report_gen: ReportGenerator, db: TestDatabase,
                 parent=None):
        super().__init__(parent)
        self._gen = report_gen
        self._db = db
        self._records: List[dict] = []
        self._updating_checks = False
        self._init_ui()
        self.refresh()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 统计概览
        stats_group = QGroupBox("测试统计")
        stats_layout = QHBoxLayout(stats_group)

        self._stat_total = QLabel("总计: 0")
        self._stat_passed = QLabel("通过: 0")
        self._stat_failed = QLabel("失败: 0")
        self._stat_rate = QLabel("通过率: 0%")

        for lbl in [self._stat_total, self._stat_passed,
                    self._stat_failed, self._stat_rate]:
            lbl.setFont(QFont("Microsoft YaHei", 11))
            stats_layout.addWidget(lbl)

        stats_layout.addStretch()
        layout.addWidget(stats_group)

        # 进度条
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # 选择 / 操作栏
        sel_bar = QHBoxLayout()
        self._chk_all = QCheckBox("全选")
        self._chk_all.setTristate(True)
        self._chk_all.stateChanged.connect(self._on_select_all)
        sel_bar.addWidget(self._chk_all)

        self._lbl_count = QLabel("已选 0 项")
        self._lbl_count.setStyleSheet(f"color: {T.TEXT_SECONDARY};")
        sel_bar.addWidget(self._lbl_count)

        self._btn_clear = QPushButton("清空选择")
        self._btn_clear.clicked.connect(self._on_clear_checks)
        sel_bar.addWidget(self._btn_clear)

        sel_bar.addSpacing(10)

        self._btn_delete = QPushButton("🗑 删除选中")
        self._btn_delete.clicked.connect(self._on_delete)
        sel_bar.addWidget(self._btn_delete)

        self._btn_delete_all = QPushButton("🧹 删除全部")
        self._btn_delete_all.setToolTip("清空所有执行记录")
        self._btn_delete_all.clicked.connect(self._on_delete_all)
        sel_bar.addWidget(self._btn_delete_all)

        sel_bar.addStretch()

        self._btn_refresh = QPushButton("🔄 刷新")
        self._btn_refresh.clicked.connect(self.refresh)
        sel_bar.addWidget(self._btn_refresh)

        layout.addLayout(sel_bar)

        # 分割器
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(6)

        # 历史报告表
        self._history_table = QTableWidget()
        self._history_table.setColumnCount(7)
        self._history_table.setHorizontalHeaderLabels([
            "☑", "时间", "套件名称", "总数", "通过", "失败", "通过率"
        ])
        header = self._history_table.horizontalHeader()
        # 列宽允许拖动调整，最后一列自动填满剩余宽度
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        for col in range(1, 7):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for col, width in enumerate((36, 170, 260, 60, 60, 60)):
            self._history_table.setColumnWidth(col, width)
        self._history_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._history_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._history_table.setAlternatingRowColors(True)
        self._history_table.verticalHeader().setVisible(False)
        self._history_table.itemSelectionChanged.connect(self._on_selection_changed)
        self._history_table.itemChanged.connect(self._on_item_changed)
        self._history_table.itemDoubleClicked.connect(
            lambda item: self._show_detail(item.row())
        )
        splitter.addWidget(self._history_table)

        # 详情区
        self._detail_text = QTextEdit()
        self._detail_text.setReadOnly(True)
        self._detail_text.setFont(QFont("Consolas", 9))
        splitter.addWidget(self._detail_text)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

        # 导出按钮
        export_layout = QHBoxLayout()
        self._lbl_export_hint = QLabel("导出：优先选中记录，未选中则用最近一次")
        self._lbl_export_hint.setStyleSheet(f"color: {T.TEXT_TERTIARY};")
        export_layout.addWidget(self._lbl_export_hint)
        export_layout.addStretch()

        self._btn_allure = QPushButton("🧪 Allure 报告")
        self._btn_allure.setToolTip(
            "生成标准 Allure 报告，并在浏览器打开本机链接\n"
            "（字段包含步骤、预期/实际、失败原因与截图）"
        )
        self._btn_allure.clicked.connect(self.open_allure_report)
        export_layout.addWidget(self._btn_allure)

        for fmt, label in [("html", "📄 导出 HTML"), ("pdf", "📕 导出 PDF"),
                           ("xlsx", "📊 导出 Excel"), ("xml", "📋 导出 JUnit XML")]:
            btn = QPushButton(label)
            btn.clicked.connect(lambda checked, f=fmt: self._on_export(f))
            export_layout.addWidget(btn)

        layout.addLayout(export_layout)

        # Allure 链接（生成后可直接点击进入）
        self._lbl_allure_link = QLabel("")
        self._lbl_allure_link.setOpenExternalLinks(True)
        self._lbl_allure_link.setWordWrap(True)
        self._lbl_allure_link.setStyleSheet(f"color: {T.PRIMARY};")
        layout.addWidget(self._lbl_allure_link)

    # ---------- 数据刷新 ----------

    def refresh(self):
        """刷新报告数据（保留勾选）"""
        checked = self._checked_ids()
        stats = self._db.get_statistics()
        self._stat_total.setText(f"总计: {stats.get('total_cases', 0)}")
        self._stat_passed.setText(f"通过: {stats.get('total_passed', 0)}")
        self._stat_failed.setText(f"失败: {stats.get('total_failed', 0)}")
        self._stat_rate.setText(f"通过率: {stats.get('pass_rate', 0)}%")

        history = self._db.get_execution_history(limit=200)
        self._records = history

        self._updating_checks = True
        try:
            self._history_table.setRowCount(len(history))
            for row, record in enumerate(history):
                rid = record.get("record_id", 0)

                chk = QTableWidgetItem()
                chk.setFlags(
                    Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                )
                chk.setCheckState(
                    Qt.CheckState.Checked if rid in checked
                    else Qt.CheckState.Unchecked
                )
                chk.setData(Qt.ItemDataRole.UserRole, rid)
                self._history_table.setItem(row, 0, chk)

                self._history_table.setItem(row, 1, QTableWidgetItem(
                    record.get("start_time", "")[:19]
                ))

                suite_name = record.get("suite_id", "")
                try:
                    rd = json.loads(record.get("record_data", "{}"))
                    suite_name = rd.get("name", suite_name)
                except Exception:
                    pass
                self._history_table.setItem(row, 2, QTableWidgetItem(suite_name))

                total = record.get("total_count", 0)
                passed = record.get("passed_count", 0)
                failed = record.get("failed_count", 0)

                self._history_table.setItem(row, 3, QTableWidgetItem(str(total)))

                pass_item = QTableWidgetItem(str(passed))
                pass_item.setForeground(QColor("#52c41a"))
                self._history_table.setItem(row, 4, pass_item)

                fail_item = QTableWidgetItem(str(failed))
                if failed:
                    fail_item.setForeground(QColor("#ff4d4f"))
                self._history_table.setItem(row, 5, fail_item)

                rate = f"{passed / total * 100:.1f}%" if total > 0 else "N/A"
                self._history_table.setItem(row, 6, QTableWidgetItem(rate))
        finally:
            self._updating_checks = False
        self._update_selection_ui()

    # ---------- 选择逻辑 ----------

    def _checked_ids(self) -> set:
        ids = set()
        for row in range(self._history_table.rowCount()):
            item = self._history_table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                rid = item.data(Qt.ItemDataRole.UserRole)
                if rid:
                    ids.add(int(rid))
        return ids

    def _effective_record_id(self) -> Optional[int]:
        """导出/查看用：勾选第一个 > 行选中第一个 > 最近一次"""
        checked = sorted(self._checked_ids())
        if checked:
            return checked[0]
        rows = self._history_table.selectionModel().selectedRows()
        if rows:
            item = self._history_table.item(rows[0].row(), 0)
            rid = item.data(Qt.ItemDataRole.UserRole) if item else None
            if rid:
                return int(rid)
        if self._records:
            return self._records[0].get("record_id")
        return None

    def _update_selection_ui(self):
        total = self._history_table.rowCount()
        n = len(self._checked_ids())
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

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._updating_checks or item.column() != 0:
            return
        self._update_selection_ui()

    def _on_select_all(self, state: int):
        if self._updating_checks:
            return
        # 三态复选框点击会经历 未选→半选→全选 的循环，
        # 因此按“当前是否已全选”决定本次动作，避免首次点击反而取消选择。
        total = self._history_table.rowCount()
        checked = len(self._checked_ids())
        check = checked < total
        self._updating_checks = True
        for row in range(self._history_table.rowCount()):
            it = self._history_table.item(row, 0)
            if it is not None:
                it.setCheckState(
                    Qt.CheckState.Checked if check else Qt.CheckState.Unchecked
                )
        self._updating_checks = False
        self._update_selection_ui()

    def _on_clear_checks(self):
        self._updating_checks = True
        for row in range(self._history_table.rowCount()):
            it = self._history_table.item(row, 0)
            if it is not None:
                it.setCheckState(Qt.CheckState.Unchecked)
        self._updating_checks = False
        self._update_selection_ui()

    def _on_selection_changed(self):
        rows = self._history_table.selectionModel().selectedRows()
        if rows:
            self._show_detail(rows[0].row())

    def _show_detail(self, row: int):
        if not (0 <= row < len(self._records)):
            return
        record = self._records[row]
        details = self._db.get_execution_details(record.get("record_id", 0))
        text = f"执行记录 #{record.get('record_id', '')}\n"
        text += f"环境: {record.get('environment', '')}\n"
        text += f"操作人: {record.get('operator', '')}\n"
        text += f"耗时: {record.get('total_duration', 0):.1f}s\n\n"
        text += "--- 用例详情 ---\n"
        for d in details:
            text += f"[{d.get('status', '')}] {d.get('case_id', '')} "
            text += f"({d.get('duration', 0):.2f}s)\n"
            if d.get("error_message"):
                text += f"  错误: {d['error_message']}\n"
        self._detail_text.setPlainText(text)

    # ---------- 删除 ----------

    def _on_delete(self):
        """删除勾选的记录（未勾选时删除当前选中行）"""
        ids = sorted(self._checked_ids())
        if not ids:
            rows = self._history_table.selectionModel().selectedRows()
            if rows:
                item = self._history_table.item(rows[0].row(), 0)
                rid = item.data(Qt.ItemDataRole.UserRole) if item else None
                if rid:
                    ids = [int(rid)]
        if not ids:
            QMessageBox.information(self, "提示", "请先勾选或选中要删除的记录")
            return
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定删除 {len(ids)} 条执行记录吗？\n该操作不可恢复。"
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        deleted = sum(1 for rid in ids if self._db.delete_execution_record(rid))
        self._detail_text.clear()
        self.refresh()
        logger.info(f"删除执行记录 {deleted} 条")

    def _on_delete_all(self):
        """删除全部执行记录"""
        if not self._records:
            QMessageBox.information(self, "提示", "没有可删除的记录")
            return
        reply = QMessageBox.warning(
            self, "确认清空",
            f"将删除全部 {len(self._records)} 条执行记录，不可恢复！\n确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for record in list(self._records):
            rid = record.get("record_id")
            if rid:
                self._db.delete_execution_record(int(rid))
        self._detail_text.clear()
        self.refresh()

    # ---------- 导出 ----------

    def _on_export(self, fmt: str):
        """导出报告（优先选中/勾选记录，否则最近一次）"""
        filters = {
            "html": ("HTML 报告", "*.html"),
            "pdf": ("PDF 报告", "*.pdf"),
            "xlsx": ("Excel 报告", "*.xlsx"),
            "xml": ("JUnit XML", "*.xml"),
        }
        name, ext = filters.get(fmt, ("报告", "*.html"))
        path, _ = QFileDialog.getSaveFileName(
            self, f"导出{name}", f"report.{fmt}", ext
        )
        if not path:
            return
        # 用户手输文件名时可能没带扩展名，补齐
        if not path.lower().endswith(f".{fmt}"):
            path = f"{path}.{fmt}"

        record_id = self._effective_record_id()
        suite = self._load_suite(record_id)
        if suite is None or not suite.test_cases:
            QMessageBox.information(
                self, "提示",
                "没有可导出的执行结果。\n请先执行测试。"
            )
            return

        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        result = ""
        error = ""
        try:
            if fmt == "html":
                result = self._gen.generate_html(suite, path)
            elif fmt == "pdf":
                result = self._gen.generate_pdf(suite, path)
            elif fmt == "xlsx":
                result = self._gen.generate_excel(suite, path)
            elif fmt == "xml":
                result = self._gen.generate_junit_xml(suite, path)
        except Exception as e:
            error = str(e)
            logger.exception(f"导出{name}失败: {e}")
        finally:
            self._progress.setVisible(False)

        if result:
            logger.info(f"报告已导出: {result}")
            self._lbl_export_hint.setText(f"✅ 已导出：{result}")
            self._lbl_export_hint.setStyleSheet(f"color: {T.SUCCESS};")
            QMessageBox.information(self, "导出成功", f"{name} 已保存到：\n{result}")
            return

        if fmt == "pdf":
            detail = (
                "未能转换为 PDF。\n\n"
                "本程序优先使用 WeasyPrint，其次调用系统 Edge/Chrome 无头打印。\n"
                "请确认已安装 Microsoft Edge 或 Chrome（或执行 "
                "pip install weasyprint），也可先导出 HTML 再用浏览器打印为 PDF。"
            )
        else:
            detail = f"{name} 导出失败。"
        if error:
            detail += f"\n\n错误信息：{error}"
        self._lbl_export_hint.setText(f"⚠ {name}导出失败")
        self._lbl_export_hint.setStyleSheet(f"color: {T.DANGER};")
        QMessageBox.warning(self, "导出失败", detail)

    def open_allure_report(self):
        """生成标准 Allure 报告，并在浏览器打开本机链接"""
        record_id = self._effective_record_id()
        suite = self._load_suite(record_id)
        if suite is None or not suite.test_cases:
            QMessageBox.information(
                self, "提示", "没有可导出的执行结果。\n请先执行测试。"
            )
            return

        if not allure_available():
            QMessageBox.warning(
                self, "未安装 Allure",
                "未检测到 allure 命令行。\n\n"
                "安装方式（需 Java 8+）：\n"
                "  • Windows: scoop install allure 或 choco install allure\n"
                "  • 或下载 https://github.com/allure-framework/allure2/releases\n"
                "    解压后将 bin 目录加入 PATH\n\n"
                "安装后即可在这里直接打开 Allure 报告；"
                "应用内的 HTML 报告同样包含失败错误点与截图。"
            )
            return

        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        try:
            results = self._gen.generate_allure(
                suite, "reports/allure-results")
            report_dir = generate_allure_report(
                results, "reports/allure-report")
            url = allure_server.serve(report_dir)
            QDesktopServices.openUrl(QUrl(url))
            self._lbl_allure_link.setText(
                f'Allure 报告已就绪：<a href="{url}">{url}</a> '
                f'（{allure_version() or "Allure"}，关闭应用后链接失效）'
            )
            logger.info(f"已打开 Allure 报告: {url}")
        except Exception as e:
            logger.error(f"Allure 报告生成失败: {e}")
            QMessageBox.warning(self, "Allure 报告生成失败", str(e))
        finally:
            self._progress.setVisible(False)

    def _load_suite(self, record_id: Optional[int]) -> Optional[TestSuite]:
        """按 record_id 加载套件结果；record_id 为空则取最近一次"""
        if record_id:
            history = [r for r in
                       self._db.get_execution_history(limit=500)
                       if r.get("record_id") == record_id]
        else:
            history = self._db.get_execution_history(limit=1)
        if not history:
            return None
        record = history[0]
        try:
            record_data = json.loads(record.get("record_data", "{}"))
            suite = TestSuite.from_dict(record_data)
            suite.total_count = record.get("total_count", 0)
            suite.passed_count = record.get("passed_count", 0)
            suite.failed_count = record.get("failed_count", 0)
            suite.error_count = record.get("error_count", 0)
            suite.skipped_count = record.get("skipped_count", 0)
            suite.total_duration = record.get("total_duration", 0.0)
            return suite
        except Exception as e:
            logger.error(f"加载执行记录失败: {e}")
            return None
