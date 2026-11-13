"""
CAN 监控面板
=============
实时显示 CAN 总线消息，支持：
- 消息列表（ID、数据、时间戳）
- 信号值显示（DBC 解析）
- 过滤规则
- 消息统计
- 录制/回放
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QComboBox, QCheckBox, QGroupBox, QFileDialog,
    QHeaderView, QSplitter, QTextEdit, QLineEdit, QFormLayout, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QColor
from typing import List, Dict, Optional
import time
import json
from pathlib import Path

from core.device.virtual_can import VirtualCANBus, VirtualCANChannel, CANMessage, CANProtocol
from core.device.dbc_parser import DBCParser


class CANMonitorPanel(QWidget):
    """CAN 监控面板"""

    # 信号
    message_received = pyqtSignal(object)  # CANMessage

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bus = VirtualCANBus()
        self._channel: Optional[VirtualCANChannel] = None
        self._dbc = DBCParser()
        self._messages: List[CANMessage] = []
        self._running = False
        self._record_data: List[Dict] = []

        self._init_ui()
        self._init_timer()

    def _init_ui(self):
        """初始化界面"""
        layout = QVBoxLayout(self)

        # 顶部控制栏
        control_layout = QHBoxLayout()

        # 通道选择
        control_layout.addWidget(QLabel("通道："))
        self._channel_combo = QComboBox()
        self._channel_combo.addItems(["通道 0 (500k, CAN FD)", "通道 1 (250k, CAN 2.0)"])
        control_layout.addWidget(self._channel_combo)

        # 连接/断开按钮
        self._connect_btn = QPushButton("连接")
        self._connect_btn.clicked.connect(self._on_connect)
        control_layout.addWidget(self._connect_btn)

        self._disconnect_btn = QPushButton("断开")
        self._disconnect_btn.clicked.connect(self._on_disconnect)
        self._disconnect_btn.setEnabled(False)
        control_layout.addWidget(self._disconnect_btn)

        control_layout.addStretch()
        layout.addLayout(control_layout)

        # 中间分割器
        splitter = QSplitter(Qt.Orientation.Vertical)

        # 消息表格
        self._msg_table = QTableWidget()
        self._msg_table.setColumnCount(6)
        self._msg_table.setHorizontalHeaderLabels(["时间", "ID", "类型", "DLC", "数据", "信号值"])
        self._msg_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._msg_table.setAlternatingRowColors(True)
        self._msg_table.setFont(QFont("Consolas", 9))
        splitter.addWidget(self._msg_table)

        # 底部信息区
        bottom_widget = QWidget()
        bottom_layout = QHBoxLayout(bottom_widget)

        # 统计信息
        stats_group = QGroupBox("总线统计")
        stats_layout = QFormLayout()
        self._msg_count_label = QLabel("0")
        self._tx_count_label = QLabel("0")
        self._rx_count_label = QLabel("0")
        stats_layout.addRow("消息总数:", self._msg_count_label)
        stats_layout.addRow("发送:", self._tx_count_label)
        stats_layout.addRow("接收:", self._rx_count_label)
        stats_group.setLayout(stats_layout)
        bottom_layout.addWidget(stats_group)

        # 发送区域
        send_group = QGroupBox("发送消息")
        send_layout = QFormLayout()
        self._send_id_edit = QLineEdit("0x123")
        self._send_data_edit = QLineEdit("01 02 03 04 05 06 07 08")
        self._send_fd_checkbox = QCheckBox("CAN FD")
        send_layout.addRow("ID:", self._send_id_edit)
        send_layout.addRow("数据:", self._send_data_edit)
        send_layout.addRow("", self._send_fd_checkbox)
        send_group.setLayout(send_layout)
        bottom_layout.addWidget(send_group)

        self._send_btn = QPushButton("发送")
        self._send_btn.clicked.connect(self._on_send)
        self._send_btn.setEnabled(False)
        bottom_layout.addWidget(self._send_btn)

        self._inject_btn = QPushButton("注入")
        self._inject_btn.clicked.connect(self._on_inject)
        self._inject_btn.setEnabled(False)
        bottom_layout.addWidget(self._inject_btn)

        splitter.addWidget(bottom_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

        # 底部工具栏
        tool_layout = QHBoxLayout()

        # DBC 加载
        self._load_dbc_btn = QPushButton("加载 DBC...")
        self._load_dbc_btn.clicked.connect(self._on_load_dbc)
        tool_layout.addWidget(self._load_dbc_btn)

        self._dbc_label = QLabel("未加载 DBC")
        tool_layout.addWidget(self._dbc_label)

        tool_layout.addStretch()

        # 录制控制
        self._record_btn = QPushButton("开始录制")
        self._record_btn.clicked.connect(self._on_toggle_record)
        self._record_btn.setEnabled(False)
        tool_layout.addWidget(self._record_btn)

        # 清空
        self._clear_btn = QPushButton("清空")
        self._clear_btn.clicked.connect(self._on_clear)
        tool_layout.addWidget(self._clear_btn)

        layout.addLayout(tool_layout)

        # 过滤栏
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("过滤 ID:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("例如：0x100, 0x200-0x300")
        filter_layout.addWidget(self._filter_edit)
        self._apply_filter_btn = QPushButton("应用")
        self._apply_filter_btn.clicked.connect(self._on_apply_filter)
        filter_layout.addWidget(self._apply_filter_btn)
        self._clear_filter_btn = QPushButton("清除")
        self._clear_filter_btn.clicked.connect(self._on_clear_filter)
        filter_layout.addWidget(self._clear_filter_btn)
        filter_layout.addStretch()
        layout.addLayout(filter_layout)

    def _init_timer(self):
        """初始化定时器"""
        self._timer = QTimer()
        self._timer.timeout.connect(self._on_timer)
        self._timer.setInterval(100)  # 100ms 刷新

    def _on_connect(self):
        """连接设备"""
        try:
            channel_idx = self._channel_combo.currentIndex()
            if channel_idx == 0:
                baudrate = 500000
                is_fd = True
            else:
                baudrate = 250000
                is_fd = False

            # 创建通道
            self._channel = self._bus.add_channel(
                channel_id=channel_idx,
                baudrate=baudrate,
                is_fd=is_fd
            )
            self._channel.connect()

            self._running = True
            self._timer.start()

            self._connect_btn.setEnabled(False)
            self._disconnect_btn.setEnabled(True)
            self._record_btn.setEnabled(True)
            self._send_btn.setEnabled(True)
            self._inject_btn.setEnabled(True)

            QMessageBox.information(self, "连接成功", 
                                   f"已连接虚拟 CAN 通道 {channel_idx}\n"
                                   f"波特率：{baudrate // 1000} kbps\n"
                                   f"协议：{'CAN FD' if is_fd else 'CAN 2.0'}")

        except Exception as e:
            QMessageBox.critical(self, "连接失败", str(e))

    def _on_disconnect(self):
        """断开设备"""
        self._running = False
        self._timer.stop()
        
        if self._channel:
            self._channel.disconnect()
            self._bus.remove_channel(self._channel.channel_id)
            self._channel = None

        self._connect_btn.setEnabled(True)
        self._disconnect_btn.setEnabled(False)
        self._record_btn.setEnabled(False)
        self._send_btn.setEnabled(False)
        self._inject_btn.setEnabled(False)

    def _on_timer(self):
        """定时器回调"""
        if not self._running or not self._channel:
            return

        try:
            messages = self._channel.receive(timeout=0.1, max_count=50)
            for msg in messages:
                self._add_message(msg)
                self.message_received.emit(msg)
            
            # 更新统计
            if self._channel:
                self._tx_count_label.setText(str(self._channel.stats.tx_count))
                self._rx_count_label.setText(str(self._channel.stats.rx_count))
        except Exception as e:
            print(f"接收错误：{e}")

    def _add_message(self, msg: CANMessage):
        """添加消息到表格"""
        self._messages.append(msg)
        self._msg_count_label.setText(str(len(self._messages)))

        # 检查过滤
        if not self._check_filter(msg.id):
            return

        # 添加到表格
        row = self._msg_table.rowCount()
        self._msg_table.insertRow(row)

        # 时间
        time_str = time.strftime("%H:%M:%S", time.localtime(msg.timestamp))
        time_item = QTableWidgetItem(time_str)
        self._msg_table.setItem(row, 0, time_item)

        # ID
        id_item = QTableWidgetItem(f"0x{msg.id:03X}")
        self._msg_table.setItem(row, 1, id_item)

        # 类型
        type_str = "CAN FD" if msg.protocol == CANProtocol.CAN_FD else "CAN 2.0"
        if msg.is_extended:
            type_str += " (扩展)"
        type_item = QTableWidgetItem(type_str)
        self._msg_table.setItem(row, 2, type_item)

        # DLC
        dlc_item = QTableWidgetItem(str(msg.dlc))
        self._msg_table.setItem(row, 3, dlc_item)

        # 数据
        data_str = " ".join(f"{b:02X}" for b in msg.data)
        data_item = QTableWidgetItem(data_str)
        self._msg_table.setItem(row, 4, data_item)

        # 信号值（DBC 解析）
        signal_str = self._decode_signals(msg)
        signal_item = QTableWidgetItem(signal_str)
        self._msg_table.setItem(row, 5, signal_item)

        # 自动滚动
        self._msg_table.scrollToBottom()

    def _decode_signals(self, msg: CANMessage) -> str:
        """解码信号值"""
        msg_def = self._dbc.get_message(msg.id)
        if not msg_def:
            return ""

        signals = msg_def.decode(msg.data)
        parts = []
        for name, value in signals.items():
            if value is not None:
                sig_def = msg_def.signals[name]
                parts.append(f"{name}={value:.2f}{sig_def.unit}")
        
        return ", ".join(parts)

    def _check_filter(self, msg_id: int) -> bool:
        """检查过滤"""
        filter_text = self._filter_edit.text().strip()
        if not filter_text:
            return True

        try:
            for part in filter_text.split(","):
                part = part.strip()
                if "-" in part:
                    start, end = part.split("-")
                    if int(start, 16) <= msg_id <= int(end, 16):
                        return True
                else:
                    if msg_id == int(part, 16):
                        return True
            return False
        except:
            return True

    def _on_send(self):
        """发送消息"""
        if not self._channel:
            return

        try:
            msg_id = int(self._send_id_edit.text(), 16)
            data_hex = self._send_data_edit.text().replace(" ", "")
            data = bytes.fromhex(data_hex)
            is_fd = self._send_fd_checkbox.isChecked()

            protocol = CANProtocol.CAN_FD if is_fd else CANProtocol.CAN_20B
            msg = CANMessage(
                id=msg_id,
                data=data,
                protocol=protocol,
                is_extended=msg_id > 0x7FF
            )

            self._channel.send(msg)
            print(f"发送：{msg}")

        except Exception as e:
            QMessageBox.warning(self, "发送失败", str(e))

    def _on_inject(self):
        """注入消息（模拟外部 ECU）"""
        if not self._channel:
            return

        try:
            msg_id = int(self._send_id_edit.text(), 16)
            data_hex = self._send_data_edit.text().replace(" ", "")
            data = bytes.fromhex(data_hex)

            msg = CANMessage(
                id=msg_id,
                data=data,
                timestamp=time.time()
            )

            self._channel.inject_message(msg)
            print(f"注入：{msg}")

        except Exception as e:
            QMessageBox.warning(self, "注入失败", str(e))

    def _on_load_dbc(self):
        """加载 DBC 文件"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 DBC 文件", "", "DBC Files (*.dbc);;All Files (*)"
        )
        
        if path:
            try:
                if self._dbc.parse_file(path):
                    msg_count = len(self._dbc.messages)
                    self._dbc_label.setText(f"已加载：{Path(path).name} ({msg_count} 消息)")
                    QMessageBox.information(self, "加载成功", f"加载了 {msg_count} 个消息定义")
                else:
                    QMessageBox.warning(self, "加载失败", "DBC 文件解析失败")
            except Exception as e:
                QMessageBox.critical(self, "加载错误", str(e))

    def _on_apply_filter(self):
        """应用过滤"""
        # 过滤器已在 _check_filter 中实现
        pass

    def _on_clear_filter(self):
        """清除过滤"""
        self._filter_edit.clear()

    def _on_toggle_record(self):
        """切换录制"""
        if self._record_btn.text() == "开始录制":
            self._record_btn.setText("停止录制")
            self._record_data.clear()
            print("开始录制...")
        else:
            self._record_btn.setText("开始录制")
            print(f"录制完成，共 {len(self._record_data)} 帧")

    def _on_clear(self):
        """清空"""
        self._msg_table.setRowCount(0)
        self._messages.clear()
        self._msg_count_label.setText("0")
        
        if self._channel:
            self._channel.clear_buffer()
