"""
设备连接面板
=============
显示设备列表、连接状态、操作按钮。

支持：
- 自动扫描（真实设备 + 虚拟设备）
- 手动连接（串口、TCP、UDP、ADB）
- 双击可用设备快速连接
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QListWidget, QListWidgetItem,
    QGroupBox, QComboBox, QLineEdit, QFormLayout,
    QMessageBox, QSpinBox, QStackedWidget
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont

from loguru import logger

from core.device.manager import DeviceManager, DeviceType, DeviceStatus, DeviceInfo
from core.device.serial_device import SerialDevice
from core.device.network_device import NetworkDevice
from core.device.adb_device import ADBDevice


class ConnectionPanel(QWidget):
    """
    设备连接面板
    -------------
    左侧面板，显示可用设备和已连接设备。
    """

    device_connected = pyqtSignal(str)    # device_id
    device_disconnected = pyqtSignal(str) # device_id

    def __init__(self, device_manager: DeviceManager, parent=None):
        super().__init__(parent)
        self._dm = device_manager
        self._init_ui()
        self._connect_signals()
        # 启动时自动扫描一次
        self._dm.scan_devices_async()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 标题
        title = QLabel("📡 设备连接")
        title.setFont(QFont("Microsoft YaHei", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        # 扫描按钮
        btn_layout = QHBoxLayout()
        self._btn_scan = QPushButton("🔍 扫描设备")
        self._btn_scan.clicked.connect(self._on_scan)
        btn_layout.addWidget(self._btn_scan)

        self._btn_disconnect_all = QPushButton("断开全部")
        self._btn_disconnect_all.clicked.connect(self._on_disconnect_all)
        btn_layout.addWidget(self._btn_disconnect_all)
        layout.addLayout(btn_layout)

        # 可用设备列表
        avail_group = QGroupBox("可用设备（双击连接）")
        avail_layout = QVBoxLayout(avail_group)
        self._avail_list = QListWidget()
        self._avail_list.itemDoubleClicked.connect(self._on_connect_item)
        avail_layout.addWidget(self._avail_list)
        layout.addWidget(avail_group)

        # 手动连接（分类型表单）
        manual_group = QGroupBox("手动连接")
        manual_layout = QVBoxLayout(manual_group)

        self._type_combo = QComboBox()
        self._type_combo.addItems(["串口", "TCP", "UDP", "ADB"])
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        manual_layout.addWidget(self._type_combo)

        # 表单堆叠
        self._form_stack = QStackedWidget()
        manual_layout.addWidget(self._form_stack)

        self._build_serial_form()
        self._build_tcp_form()
        self._build_udp_form()
        self._build_adb_form()

        self._btn_manual_connect = QPushButton("连接")
        self._btn_manual_connect.clicked.connect(self._on_manual_connect)
        manual_layout.addWidget(self._btn_manual_connect)
        layout.addWidget(manual_group)

        # 已连接设备列表
        conn_group = QGroupBox("已连接设备")
        conn_layout = QVBoxLayout(conn_group)
        self._conn_list = QListWidget()
        conn_layout.addWidget(self._conn_list)

        btn_disconnect = QPushButton("断开选中")
        btn_disconnect.clicked.connect(self._on_disconnect_selected)
        conn_layout.addWidget(btn_disconnect)
        layout.addWidget(conn_group)

    # ---------- 手动连接表单 ----------

    def _build_serial_form(self):
        """串口连接表单"""
        page = QWidget()
        form = QFormLayout(page)

        self._serial_port = QLineEdit()
        self._serial_port.setPlaceholderText("如 COM3 / loop://")
        form.addRow("串口号:", self._serial_port)

        self._serial_baudrate = QComboBox()
        self._serial_baudrate.addItems([
            "115200", "57600", "38400", "19200", "9600"
        ])
        self._serial_baudrate.setEditable(True)
        form.addRow("波特率:", self._serial_baudrate)
        self._form_stack.addWidget(page)

    def _build_tcp_form(self):
        """TCP 连接表单"""
        page = QWidget()
        form = QFormLayout(page)

        self._tcp_host = QLineEdit("127.0.0.1")
        form.addRow("IP 地址:", self._tcp_host)

        self._tcp_port = QSpinBox()
        self._tcp_port.setRange(1, 65535)
        self._tcp_port.setValue(8080)
        form.addRow("端口:", self._tcp_port)
        self._form_stack.addWidget(page)

    def _build_udp_form(self):
        """UDP 连接表单"""
        page = QWidget()
        form = QFormLayout(page)

        self._udp_host = QLineEdit("127.0.0.1")
        form.addRow("目标 IP:", self._udp_host)

        self._udp_port = QSpinBox()
        self._udp_port.setRange(1, 65535)
        self._udp_port.setValue(5555)
        form.addRow("目标端口:", self._udp_port)

        self._udp_local_port = QSpinBox()
        self._udp_local_port.setRange(0, 65535)
        self._udp_local_port.setValue(0)
        form.addRow("本地端口(0=自动):", self._udp_local_port)
        self._form_stack.addWidget(page)

    def _build_adb_form(self):
        """ADB 连接表单"""
        page = QWidget()
        form = QFormLayout(page)

        self._adb_serial = QLineEdit()
        self._adb_serial.setPlaceholderText("留空自动选择，或输入序列号")
        form.addRow("设备序列号:", self._adb_serial)
        self._form_stack.addWidget(page)

    def _on_type_changed(self, idx: int):
        self._form_stack.setCurrentIndex(idx)

    # ---------- 信号连接 ----------

    def _connect_signals(self):
        self._dm.signals.scan_completed.connect(self._on_scan_completed)
        self._dm.signals.device_status_changed.connect(
            self._on_status_changed
        )

    # ---------- 操作 ----------

    def _on_scan(self):
        self._btn_scan.setEnabled(False)
        self._btn_scan.setText("扫描中...")
        self._avail_list.clear()
        self._avail_list.addItem("正在扫描...")
        self._dm.scan_devices_async()

    def _on_scan_completed(self, count: int, device_infos: list):
        """扫描完成，填充可用设备列表"""
        self._btn_scan.setEnabled(True)
        self._btn_scan.setText("🔍 扫描设备")

        self._avail_list.clear()
        for info in device_infos:
            item = QListWidgetItem(f"● {info.name}  [{info.device_type.value}]")
            # 虚拟设备加标记
            if info.extra.get("is_virtual"):
                item.setForeground(QColor("#8c8c8c"))
                item.setText(f"◌ {info.name}  [{info.device_type.value}](虚拟)")
            item.setData(Qt.ItemDataRole.UserRole, info)
            self._avail_list.addItem(item)

        if not device_infos:
            self._avail_list.addItem("未发现设备（可尝试手动连接）")

        logger.info(f"扫描完成，发现 {count} 个设备")

    def _on_connect_item(self, item: QListWidgetItem):
        """双击可用设备直接连接"""
        info = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(info, DeviceInfo):
            return
        self._connect_with_info(info)

    def _connect_with_info(self, info: DeviceInfo) -> bool:
        """根据设备信息创建设备并连接"""
        device = self._create_device_from_info(info)
        if device is None:
            QMessageBox.warning(self, "连接失败", f"无法创建该类型设备: {info.device_type}")
            return False

        success = self._dm.connect_device(device)
        if success:
            self.device_connected.emit(info.device_id)
            self.refresh()
        else:
            QMessageBox.warning(
                self, "连接失败",
                f"无法连接到设备: {info.name}\n请检查硬件连接和参数配置"
            )
        return success

    def _create_device_from_info(self, info: DeviceInfo):
        """从扫描信息创建设备实例"""
        extra = dict(info.extra)
        try:
            if info.device_type == DeviceType.SERIAL:
                config = {
                    "port": info.port or extra.get("port", "loop://"),
                    "baudrate": extra.get("baudrate", 115200),
                }
                return SerialDevice(info.device_id, config)
            elif info.device_type in (DeviceType.NETWORK_TCP, DeviceType.NETWORK_UDP):
                config = {
                    "protocol": "tcp" if info.device_type == DeviceType.NETWORK_TCP else "udp",
                    "host": extra.get("host", "127.0.0.1"),
                    "port": extra.get("port", 8080),
                    "local_port": extra.get("local_port", 0),
                }
                return NetworkDevice(info.device_id, config)
            elif info.device_type == DeviceType.ADB:
                config = {"device_serial": extra.get("serial", "")}
                return ADBDevice(info.device_id, config)
        except Exception as e:
            logger.error(f"创建设备失败: {e}")
        return None

    def _on_manual_connect(self):
        """手动连接"""
        idx = self._type_combo.currentIndex()
        try:
            if idx == 0:  # 串口
                port = self._serial_port.text().strip() or "loop://"
                baudrate = int(self._serial_baudrate.currentText())
                device_id = f"serial_{port}"
                device = SerialDevice(device_id, {"port": port, "baudrate": baudrate})
            elif idx == 1:  # TCP
                host = self._tcp_host.text().strip() or "127.0.0.1"
                port = self._tcp_port.value()
                device_id = f"tcp_{host}_{port}"
                device = NetworkDevice(device_id, {"protocol": "tcp", "host": host, "port": port})
            elif idx == 2:  # UDP
                host = self._udp_host.text().strip() or "127.0.0.1"
                port = self._udp_port.value()
                local_port = self._udp_local_port.value()
                device_id = f"udp_{host}_{port}"
                device = NetworkDevice(device_id, {
                    "protocol": "udp", "host": host, "port": port,
                    "local_port": local_port
                })
            elif idx == 3:  # ADB
                serial_no = self._adb_serial.text().strip()
                device_id = f"adb_{serial_no or 'auto'}"
                device = ADBDevice(device_id, {"device_serial": serial_no})
            else:
                return
        except ValueError as e:
            QMessageBox.warning(self, "参数错误", f"输入参数格式错误: {e}")
            return

        success = self._dm.connect_device(device)
        if success:
            self.device_connected.emit(device_id)
            self.refresh()
        else:
            QMessageBox.warning(self, "连接失败", f"无法连接到: {device_id}")

    def _on_disconnect_all(self):
        self._dm.disconnect_all()
        self.refresh()

    def _on_disconnect_selected(self):
        item = self._conn_list.currentItem()
        if item:
            device_id = item.data(Qt.ItemDataRole.UserRole)
            if device_id:
                self._dm.disconnect_device(device_id)
                self.device_disconnected.emit(device_id)
                self.refresh()

    def _on_status_changed(self, device_id: str, old: str, new: str):
        self.refresh()

    # ---------- 刷新 ----------

    def refresh(self):
        """刷新已连接设备列表"""
        self._conn_list.clear()
        for device_id, device in self._dm.get_all_devices().items():
            status_text = device.status.value
            item = QListWidgetItem(f"● {device_id} [{status_text}]")
            item.setData(Qt.ItemDataRole.UserRole, device_id)
            if device.status == DeviceStatus.CONNECTED:
                item.setForeground(QColor("#52c41a"))
            elif device.status == DeviceStatus.ERROR:
                item.setForeground(QColor("#ff4d4f"))
            elif device.status == DeviceStatus.CONNECTING:
                item.setForeground(QColor("#faad14"))
            self._conn_list.addItem(item)

    def update_device_status(self, device_id: str, status: str):
        """更新单个设备状态显示"""
        self.refresh()

    def _create_device(self, device_id: str, device_type, config: dict):
        """根据类型创建设备实例（兼容旧接口）"""
        try:
            if device_type == DeviceType.SERIAL:
                return SerialDevice(device_id, config)
            elif device_type in (DeviceType.NETWORK_TCP, DeviceType.NETWORK_UDP):
                return NetworkDevice(device_id, config)
            elif device_type == DeviceType.ADB:
                return ADBDevice(device_id, config)
        except Exception as e:
            logger.error(f"创建设备失败: {e}")
        return None
