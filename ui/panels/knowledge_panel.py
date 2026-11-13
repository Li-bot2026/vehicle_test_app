"""
知识库查看与管理面板
==================

显示已学习的应用知识，支持查看、编辑、删除、强制重新学习、导入导出。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget,
    QTextEdit, QLabel, QMessageBox, QInputDialog, QFileDialog,
    QProgressDialog, QApplication, QTreeWidget, QTreeWidgetItem,
    QSplitter, QTabWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from core.ai.knowledge import get_knowledge_base
from loguru import logger


class KnowledgePanel(QWidget):
    """知识库管理面板"""

    def __init__(self, ai_client=None, parent=None):
        super().__init__(parent)
        self._kb = get_knowledge_base()
        self._ai_client = ai_client
        self._init_ui()
        self._refresh_list()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 标题
        title = QLabel("📚 应用知识库")
        title.setStyleSheet("font-size: 16px; font-weight: bold; padding: 8px;")
        layout.addWidget(title)

        # 列表 + 详情 水平布局
        h_layout = QHBoxLayout()

        # 左侧：应用列表
        self._app_list = QListWidget()
        self._app_list.currentItemChanged.connect(self._on_item_selected)
        self._app_list.setMaximumWidth(300)
        h_layout.addWidget(self._app_list)

        # 右侧：详情 + 操作按钮
        right_layout = QVBoxLayout()

        # 创建标签页：结构化视图 + 原始文本
        self._detail_tabs = QTabWidget()
        
        # 标签页1：结构化视图（树形）
        self._detail_tree = QTreeWidget()
        self._detail_tree.setHeaderLabels(["属性", "值"])
        self._detail_tree.setColumnWidth(0, 200)
        self._detail_tree.setStyleSheet("""
            QTreeWidget {
                font-family: 'Microsoft YaHei', 'SimSun', sans-serif;
                font-size: 13px;
            }
            QTreeWidget::item {
                padding: 4px;
            }
            QTreeWidget::item:selected {
                background-color: #0078d4;
                color: white;
            }
        """)
        self._detail_tabs.addTab(self._detail_tree, "📊 结构化视图")
        
        # 标签页2：原始文本
        self._detail_text = QTextEdit()
        self._detail_text.setReadOnly(True)
        self._detail_text.setStyleSheet("font-family: 'Consolas', 'Monaco', monospace; font-size: 12px;")
        self._detail_tabs.addTab(self._detail_text, "📝 原始文本")
        
        # 标签页3：学习统计
        self._learning_stats_widget = self._create_learning_stats_tab()
        self._detail_tabs.addTab(self._learning_stats_widget, "📈 学习统计")
        
        right_layout.addWidget(self._detail_tabs, stretch=1)

        # 按钮行
        btn_layout = QHBoxLayout()

        self._btn_refresh = QPushButton("🔄 刷新列表")
        self._btn_refresh.clicked.connect(self._refresh_list)
        btn_layout.addWidget(self._btn_refresh)

        self._btn_learn = QPushButton("🌐 重新学习")
        self._btn_learn.clicked.connect(self._on_relearn)
        self._btn_learn.setEnabled(False)
        btn_layout.addWidget(self._btn_learn)

        self._btn_edit = QPushButton("✏️ 编辑")
        self._btn_edit.clicked.connect(self._on_edit)
        self._btn_edit.setEnabled(False)
        btn_layout.addWidget(self._btn_edit)

        self._btn_delete = QPushButton("🗑️ 删除")
        self._btn_delete.clicked.connect(self._on_delete)
        self._btn_delete.setEnabled(False)
        btn_layout.addWidget(self._btn_delete)

        right_layout.addLayout(btn_layout)

        # 第二行按钮：导入导出
        btn_layout2 = QHBoxLayout()

        self._btn_export = QPushButton("📤 导出")
        self._btn_export.clicked.connect(self._on_export)
        self._btn_export.setEnabled(False)
        btn_layout2.addWidget(self._btn_export)

        self._btn_import = QPushButton("📥 导入")
        self._btn_import.clicked.connect(self._on_import)
        btn_layout2.addWidget(self._btn_import)

        self._btn_export_all = QPushButton("📦 导出全部")
        self._btn_export_all.clicked.connect(self._on_export_all)
        btn_layout2.addWidget(self._btn_export_all)

        btn_layout2.addStretch()
        right_layout.addLayout(btn_layout2)
        h_layout.addLayout(right_layout, stretch=1)

        layout.addLayout(h_layout)

    def _create_learning_stats_tab(self) -> QWidget:
        """创建学习统计标签页"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # 标题
        title = QLabel("📊 AI 学习统计")
        title.setStyleSheet("font-size: 14px; font-weight: bold; padding: 8px;")
        layout.addWidget(title)
        
        # 统计信息文本框
        self._learning_stats_text = QTextEdit()
        self._learning_stats_text.setReadOnly(True)
        self._learning_stats_text.setStyleSheet(
            "font-family: 'Consolas', 'Monaco', monospace; font-size: 12px; "
            "background-color: #f5f5f5; padding: 10px;"
        )
        layout.addWidget(self._learning_stats_text)
        
        # 按钮行
        btn_layout = QHBoxLayout()
        
        self._btn_refresh_stats = QPushButton("🔄 刷新统计")
        self._btn_refresh_stats.clicked.connect(self._refresh_learning_stats)
        btn_layout.addWidget(self._btn_refresh_stats)
        
        self._btn_export_stats = QPushButton("📤 导出学习数据")
        self._btn_export_stats.clicked.connect(self._on_export_learning_data)
        btn_layout.addWidget(self._btn_export_stats)
        
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        # 初始加载
        self._refresh_learning_stats()
        
        return widget

    def _refresh_learning_stats(self):
        """刷新学习统计信息"""
        try:
            from core.ai.experience_learner import get_experience_learner
            learner = get_experience_learner()
            report = learner.generate_learning_report()
            self._learning_stats_text.setPlainText(report)
        except Exception as e:
            self._learning_stats_text.setPlainText(f"加载学习统计失败: {e}")

    def _on_export_learning_data(self):
        """导出学习数据"""
        try:
            from core.ai.experience_learner import get_experience_learner
            import json
            
            learner = get_experience_learner()
            data = learner.export_knowledge()
            
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "导出学习数据",
                "learning_data.json",
                "JSON 文件 (*.json);;所有文件 (*)"
            )
            
            if file_path:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                QMessageBox.information(self, "导出成功", f"学习数据已导出到:\n{file_path}")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", f"导出学习数据时出错:\n{e}")

    def _refresh_list(self):
        """刷新应用列表"""
        self._app_list.clear()
        apps = self._kb.list_apps()
        for app in apps:
            label = f"{app['name']} ({app['package']})"
            if app.get('version'):
                label += f" v{app['version']}"
            item = self._app_list.addItem(label)
            # 存储 package 到 item data
            list_item = self._app_list.item(self._app_list.count() - 1)
            list_item.setData(Qt.ItemDataRole.UserRole, app['package'])

        if not apps:
            self._detail_text.setPlainText(
                "知识库为空。\n\n"
                "当 AI 执行自主任务时，会自动学习当前应用的知识并保存到这里。\n"
                "你也可以手动触发学习（未来版本）。"
            )

    def _on_item_selected(self, current, previous):
        """选中某个应用时显示详情"""
        if current is None:
            self._detail_tree.clear()
            self._detail_text.clear()
            self._btn_learn.setEnabled(False)
            self._btn_edit.setEnabled(False)
            self._btn_delete.setEnabled(False)
            self._btn_export.setEnabled(False)
            return

        package = current.data(Qt.ItemDataRole.UserRole)
        data = self._kb.get(package)
        if not data:
            self._detail_text.setPlainText(f"无法加载 {package} 的知识")
            return

        # 更新树形视图
        self._detail_tree.clear()
        
        # 基本信息
        basic_item = QTreeWidgetItem(["📋 基本信息", ""])
        basic_item.setExpanded(True)
        self._detail_tree.addTopLevelItem(basic_item)
        
        basic_item.addChild(QTreeWidgetItem(["应用名称", data.get('name', '未知')]))
        basic_item.addChild(QTreeWidgetItem(["包名", data.get('package', package)]))
        basic_item.addChild(QTreeWidgetItem(["版本", str(data.get('version', 0))]))
        basic_item.addChild(QTreeWidgetItem(["学习时间", data.get('learned_at', '未知')]))
        
        # 规则
        if data.get('rules'):
            rules_item = QTreeWidgetItem([f"📏 规则 ({len(data['rules'])}条)", ""])
            rules_item.setExpanded(True)
            self._detail_tree.addTopLevelItem(rules_item)
            for i, rule in enumerate(data['rules'], 1):
                rules_item.addChild(QTreeWidgetItem([str(i), rule]))
        
        # 操作方式
        if data.get('controls'):
            controls_item = QTreeWidgetItem([f"🎮 操作方式 ({len(data['controls'])}种)", ""])
            controls_item.setExpanded(True)
            self._detail_tree.addTopLevelItem(controls_item)
            for i, ctrl in enumerate(data['controls'], 1):
                controls_item.addChild(QTreeWidgetItem([str(i), ctrl]))
        
        # 胜利条件
        if data.get('win_condition'):
            win_item = QTreeWidgetItem(["🏆 胜利条件", data['win_condition']])
            self._detail_tree.addTopLevelItem(win_item)
        
        # 失败条件
        if data.get('lose_condition'):
            lose_item = QTreeWidgetItem(["❌ 失败条件", data['lose_condition']])
            self._detail_tree.addTopLevelItem(lose_item)
        
        # UI 元素
        if data.get('ui_elements'):
            ui_item = QTreeWidgetItem([f"🖼️ UI 元素 ({len(data['ui_elements'])}个)", ""])
            ui_item.setExpanded(True)
            self._detail_tree.addTopLevelItem(ui_item)
            for name, desc in data['ui_elements'].items():
                if isinstance(desc, dict):
                    elem_item = QTreeWidgetItem([name, desc.get('description', '')])
                    if desc.get('location'):
                        elem_item.addChild(QTreeWidgetItem(["位置", desc['location']]))
                else:
                    elem_item = QTreeWidgetItem([name, str(desc)])
                ui_item.addChild(elem_item)
        
        # 来源
        if data.get('sources'):
            sources_item = QTreeWidgetItem([f"🔗 来源 ({len(data['sources'])}个)", ""])
            sources_item.setExpanded(False)
            self._detail_tree.addTopLevelItem(sources_item)
            for i, src in enumerate(data['sources'], 1):
                sources_item.addChild(QTreeWidgetItem([str(i), src]))
        
        # 备注
        if data.get('notes'):
            notes_item = QTreeWidgetItem(["📝 备注", ""])
            notes_item.setExpanded(False)
            self._detail_tree.addTopLevelItem(notes_item)
            notes_item.addChild(QTreeWidgetItem(["", data['notes']]))
        
        # 更新原始文本视图
        lines = []
        lines.append(f"应用名称: {data.get('name', '未知')}")
        lines.append(f"包名: {data.get('package', package)}")
        lines.append(f"版本: {data.get('version', 0)}")
        lines.append(f"学习时间: {data.get('learned_at', '未知')}")
        lines.append("")

        if data.get('rules'):
            lines.append("【规则】")
            for i, rule in enumerate(data['rules'], 1):
                lines.append(f"  {i}. {rule}")
            lines.append("")

        if data.get('controls'):
            lines.append("【操作方式】")
            for i, ctrl in enumerate(data['controls'], 1):
                lines.append(f"  {i}. {ctrl}")
            lines.append("")

        if data.get('win_condition'):
            lines.append(f"【胜利条件】{data['win_condition']}")
            lines.append("")

        if data.get('lose_condition'):
            lines.append(f"【失败条件】{data['lose_condition']}")
            lines.append("")

        if data.get('ui_elements'):
            lines.append("【UI 元素】")
            for name, desc in data['ui_elements'].items():
                if isinstance(desc, dict):
                    lines.append(f"  - {name}: {desc.get('description', '')}")
                else:
                    lines.append(f"  - {name}: {desc}")
            lines.append("")

        if data.get('sources'):
            lines.append("【来源】")
            for src in data['sources'][:5]:
                lines.append(f"  - {src}")
            lines.append("")

        if data.get('notes'):
            lines.append("【备注】")
            lines.append(data['notes'])

        self._detail_text.setPlainText("\n".join(lines))
        
        # 启用按钮
        self._btn_learn.setEnabled(True)
        self._btn_edit.setEnabled(True)
        self._btn_delete.setEnabled(True)
        self._btn_export.setEnabled(True)

    def _on_relearn(self):
        """强制重新学习当前应用"""
        current = self._app_list.currentItem()
        if not current:
            return
        package = current.data(Qt.ItemDataRole.UserRole)
        data = self._kb.get(package)
        app_name = data.get('name', package) if data else package

        # 检查是否有 AI 客户端
        if not self._ai_client or not self._ai_client.configured:
            QMessageBox.warning(
                self, "无法学习",
                "未配置 AI 客户端，无法进行网络学习。\n"
                "请在设置中配置 AI 接口（API Key 和模型名称）。"
            )
            return

        reply = QMessageBox.question(
            self, "重新学习",
            f"确定要从网络重新学习「{app_name}」的知识吗？\n"
            f"（现有知识会被合并，不会完全覆盖）\n\n"
            f"注意：学习过程可能需要 10-30 秒，请耐心等待。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 显示进度对话框
        progress = QProgressDialog("正在从网络学习知识...", "取消", 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.setCancelButton(None)  # 不允许取消
        progress.show()
        QApplication.processEvents()

        try:
            # 调用 web_learner 重新学习
            from core.ai.web_learner import learn_about_app
            
            learned = learn_about_app(
                app_name=app_name,
                package_name=package,
                client=self._ai_client,
                kb=self._kb,
            )
            
            progress.close()
            
            if learned:
                rules_count = len(learned.get('rules', []))
                controls_count = len(learned.get('controls', []))
                sources_count = len(learned.get('sources', []))
                
                QMessageBox.information(
                    self, "学习成功",
                    f"✅ 已成功学习「{app_name}」的知识！\n\n"
                    f"• 规则：{rules_count} 条\n"
                    f"• 操作方式：{controls_count} 种\n"
                    f"• 来源：{sources_count} 个网页\n\n"
                    f"知识已保存并合并到知识库。"
                )
                # 刷新列表并重新选中
                self._refresh_list()
                for i in range(self._app_list.count()):
                    item = self._app_list.item(i)
                    if item.data(Qt.ItemDataRole.UserRole) == package:
                        self._app_list.setCurrentItem(item)
                        break
            else:
                QMessageBox.warning(
                    self, "学习失败",
                    f"❌ 未能从网络学习到「{app_name}」的知识。\n\n"
                    f"可能的原因：\n"
                    f"• 网络连接问题\n"
                    f"• 搜索引擎被拦截\n"
                    f"• 未找到相关内容\n\n"
                    f"您可以手动编辑知识。"
                )
        except Exception as e:
            progress.close()
            logger.error(f"重新学习失败: {e}")
            QMessageBox.critical(
                self, "错误",
                f"学习过程中发生错误：\n{e}"
            )

    def _on_edit(self):
        """编辑当前应用的知识（简单文本编辑）"""
        current = self._app_list.currentItem()
        if not current:
            return
        package = current.data(Qt.ItemDataRole.UserRole)
        data = self._kb.get(package)
        if not data:
            return

        # 弹出简单编辑框（未来可改为结构化编辑器）
        import yaml
        yaml_text = yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
        new_text, ok = QInputDialog.getMultiLineText(
            self, "编辑知识", f"编辑 {data.get('name', package)} 的知识（YAML 格式）：",
            yaml_text,
        )
        if not ok:
            return

        try:
            new_data = yaml.safe_load(new_text)
            if not isinstance(new_data, dict):
                raise ValueError("YAML 解析结果不是字典")
            self._kb.save(package, new_data)
            QMessageBox.information(self, "成功", "知识已更新")
            self._refresh_list()
            # 重新选中
            for i in range(self._app_list.count()):
                item = self._app_list.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == package:
                    self._app_list.setCurrentItem(item)
                    break
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存失败：{e}")

    def _on_delete(self):
        """删除当前应用的知识"""
        current = self._app_list.currentItem()
        if not current:
            return
        package = current.data(Qt.ItemDataRole.UserRole)
        data = self._kb.get(package)
        app_name = data.get('name', package) if data else package

        reply = QMessageBox.warning(
            self, "确认删除",
            f"确定要删除「{app_name}」的知识吗？\n此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if self._kb.delete(package):
            QMessageBox.information(self, "成功", f"已删除 {app_name} 的知识")
            self._refresh_list()
        else:
            QMessageBox.critical(self, "错误", "删除失败")

    def _on_export(self):
        """导出当前应用的知识"""
        current = self._app_list.currentItem()
        if not current:
            return
        package = current.data(Qt.ItemDataRole.UserRole)
        data = self._kb.get(package)
        if not data:
            return

        app_name = data.get('name', package)
        
        # 选择保存位置
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            f"导出 {app_name} 的知识",
            f"{package}.yaml",
            "YAML 文件 (*.yaml);;JSON 文件 (*.json)",
        )
        
        if not file_path:
            return

        try:
            import yaml
            import json
            
            if file_path.endswith('.json'):
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            else:
                with open(file_path, 'w', encoding='utf-8') as f:
                    yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            
            QMessageBox.information(
                self, "导出成功",
                f"✅ 已将「{app_name}」的知识导出到：\n{file_path}"
            )
        except Exception as e:
            QMessageBox.critical(
                self, "导出失败",
                f"导出过程中发生错误：\n{e}"
            )

    def _on_import(self):
        """导入知识到当前应用"""
        current = self._app_list.currentItem()
        if not current:
            QMessageBox.information(
                self, "提示",
                "请先在左侧列表中选择一个应用，然后再导入知识。"
            )
            return
        
        package = current.data(Qt.ItemDataRole.UserRole)
        data = self._kb.get(package)
        app_name = data.get('name', package) if data else package

        # 选择文件
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            f"导入知识到 {app_name}",
            "",
            "知识文件 (*.yaml *.json);;YAML 文件 (*.yaml);;JSON 文件 (*.json)",
        )
        
        if not file_path:
            return

        try:
            import yaml
            import json
            
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 根据文件扩展名解析
            if file_path.endswith('.json'):
                imported_data = json.loads(content)
            else:
                imported_data = yaml.safe_load(content)
            
            if not isinstance(imported_data, dict):
                raise ValueError("导入的文件格式不正确，应该是字典")
            
            # 确认导入
            reply = QMessageBox.question(
                self, "确认导入",
                f"确定要将文件中的知识导入到「{app_name}」吗？\n"
                f"（现有知识会被合并）\n\n"
                f"导入内容预览：\n"
                f"• 规则：{len(imported_data.get('rules', []))} 条\n"
                f"• 操作：{len(imported_data.get('controls', []))} 种\n"
                f"• 来源：{len(imported_data.get('sources', []))} 个",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            
            if reply != QMessageBox.StandardButton.Yes:
                return
            
            # 合并知识
            self._kb.merge(package, imported_data)
            
            QMessageBox.information(
                self, "导入成功",
                f"✅ 已成功将知识导入到「{app_name}」！"
            )
            
            # 刷新列表并重新选中
            self._refresh_list()
            for i in range(self._app_list.count()):
                item = self._app_list.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == package:
                    self._app_list.setCurrentItem(item)
                    break
                    
        except Exception as e:
            QMessageBox.critical(
                self, "导入失败",
                f"导入过程中发生错误：\n{e}"
            )

    def _on_export_all(self):
        """导出所有知识到一个压缩包"""
        apps = self._kb.list_apps()
        if not apps:
            QMessageBox.information(
                self, "提示",
                "知识库为空，没有可导出的内容。"
            )
            return

        # 选择保存目录
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "选择导出目录",
            "",
        )
        
        if not dir_path:
            return

        try:
            import yaml
            from pathlib import Path
            
            export_count = 0
            for app in apps:
                package = app['package']
                data = self._kb.get(package)
                if data:
                    # 生成文件名
                    safe_name = package.replace('.', '_')
                    file_path = Path(dir_path) / f"{safe_name}.yaml"
                    
                    with open(file_path, 'w', encoding='utf-8') as f:
                        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
                    
                    export_count += 1
            
            QMessageBox.information(
                self, "导出成功",
                f"✅ 已成功导出 {export_count} 个应用的知识到：\n{dir_path}"
            )
        except Exception as e:
            QMessageBox.critical(
                self, "导出失败",
                f"导出过程中发生错误：\n{e}"
            )
