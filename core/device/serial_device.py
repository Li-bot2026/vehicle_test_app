"""
串口设备
=========
基于 pyserial 实现串口通信，用于与车机的 UART/RS232 接口交互。

核心功能：
- 串口连接与参数配置
- 数据收发（支持文本和二进制模式）
- 行模式读取（按换行符分割）
"""

import time
import threading
from typing import Optional, Callable, List

from loguru import logger

from .manager import BaseDevice, DeviceStatus

try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False
    logger.warning("pyserial 未安装，串口功能不可用")


class SerialDevice(BaseDevice):
    """
    串口通信设备
    -------------
    封装 pyserial 的 Serial 对象。
    """

    def __init__(self, device_id: str, config: dict):
        """
        参数:
            config:
                - port: 串口号 (如 "COM3" 或 "/dev/ttyUSB0")
                - baudrate: 波特率 (默认 115200)
                - data_bits: 数据位 (5/6/7/8)
                - stop_bits: 停止位 (1/1.5/2)
                - parity: 校验 (N/E/O)
                - timeout: 读取超时秒数
        """
        super().__init__(device_id, config)
        self._serial: Optional[object] = None
        self._recv_thread: Optional[threading.Thread] = None
        self._running = False
        self._on_data: Optional[Callable[[bytes], None]] = None
        self._rx_buffer = bytearray()

    # ---------- 设备接口 ----------

    def connect(self) -> bool:
        """打开串口连接"""
        if not HAS_SERIAL:
            logger.error("pyserial 未安装")
            return False

        try:
            port = self.config.get("port", "COM3")
            baudrate = self.config.get("baudrate", 115200)
            bytesize = self.config.get("data_bits", 8)
            stopbits = self.config.get("stop_bits", 1)
            parity = self.config.get("parity", "N")
            timeout = self.config.get("timeout", 1.0)

            # 映射参数到 pyserial 枚举
            parity_map = {"N": serial.PARITY_NONE,
                          "E": serial.PARITY_EVEN,
                          "O": serial.PARITY_ODD}
            stopbits_map = {1: serial.STOPBITS_ONE,
                            1.5: serial.STOPBITS_ONE_POINT_FIVE,
                            2: serial.STOPBITS_TWO}

            self._serial = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=bytesize,
                parity=parity_map.get(parity, serial.PARITY_NONE),
                stopbits=stopbits_map.get(stopbits, serial.STOPBITS_ONE),
                timeout=timeout
            )

            self._running = True
            self._recv_thread = threading.Thread(
                target=self._recv_loop, daemon=True,
                name=f"Serial_RX_{self.device_id}"
            )
            self._recv_thread.start()

            logger.info(f"串口已连接: {port} @ {baudrate}bps")
            return True

        except Exception as e:
            logger.error(f"串口连接失败: {e}")
            return False

    def disconnect(self) -> None:
        """关闭串口"""
        self._running = False
        if self._recv_thread and self._recv_thread.is_alive():
            self._recv_thread.join(timeout=2.0)
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception as e:
                logger.error(f"串口关闭异常: {e}")
            self._serial = None
        logger.info(f"串口已断开: {self.device_id}")

    def send(self, data: bytes) -> bool:
        """发送数据到串口"""
        if not self._serial or not self._serial.is_open:
            return False
        try:
            self._serial.write(data)
            self._serial.flush()
            return True
        except Exception as e:
            logger.error(f"串口发送失败: {e}")
            return False

    def send_text(self, text: str, encoding: str = "utf-8") -> bool:
        """发送文本（自动编码）"""
        return self.send(text.encode(encoding))

    def send_line(self, text: str, ending: str = "\r\n") -> bool:
        """发送一行文本（自动追加换行符）"""
        return self.send_text(text + ending)

    def recv(self, timeout: float = 1.0) -> Optional[bytes]:
        """读取串口数据（从后台线程的接收缓冲区消费，阻塞到超时）"""
        if not self.is_connected():
            return None
        deadline = time.time() + max(0.0, timeout)
        while True:
            if self._rx_buffer:
                data = bytes(self._rx_buffer)
                self._rx_buffer.clear()
                return data
            if time.time() >= deadline:
                return None
            time.sleep(0.01)

    def recv_line(self, timeout: float = 5.0) -> Optional[str]:
        """
        读取一行文本（以 \\n 结尾）。
        适用于 AT 命令、日志输出等行协议。
        """
        if not self._serial or not self._serial.is_open:
            return None

        deadline = time.time() + timeout
        while time.time() < deadline:
            idx = self._rx_buffer.find(b"\n")
            if idx >= 0:
                line = self._rx_buffer[:idx + 1].decode("utf-8", errors="replace")
                del self._rx_buffer[:idx + 1]
                return line.rstrip("\r\n")
            # 缓冲区由后台接收线程持续填充
            time.sleep(0.01)

        return None

    def is_connected(self) -> bool:
        """检查串口是否打开"""
        return self._serial is not None and self._serial.is_open and self._running

    # ---------- 扩展功能 ----------

    def set_data_callback(self, callback: Callable[[bytes], None]):
        """设置数据接收回调"""
        self._on_data = callback

    def get_port_info(self) -> dict:
        """获取当前串口信息"""
        if self._serial and self._serial.is_open:
            return {
                "port": self._serial.port,
                "baudrate": self._serial.baudrate,
                "bytesize": self._serial.bytesize,
                "parity": self._serial.parity,
                "stopbits": self._serial.stopbits,
                "in_waiting": self._serial.in_waiting,
                "out_waiting": self._serial.out_waiting,
            }
        return {}

    @staticmethod
    def list_ports() -> List[dict]:
        """列出系统所有可用串口"""
        if not HAS_SERIAL:
            return []
        ports = []
        for p in serial.tools.list_ports.comports():
            ports.append({
                "device": p.device,
                "description": p.description,
                "hwid": p.hwid,
                "vid": p.vid,
                "pid": p.pid,
            })
        return ports

    # ---------- 内部方法 ----------

    def _recv_loop(self):
        """后台接收循环"""
        while self._running and self._serial and self._serial.is_open:
            try:
                waiting = self._serial.in_waiting
                if waiting > 0:
                    data = self._serial.read(waiting)
                    if data and self._on_data:
                        self._on_data(data)
                    if data:
                        self._rx_buffer.extend(data)
                        # 长时间无人消费时限制缓冲增长
                        if len(self._rx_buffer) > 1_000_000:
                            del self._rx_buffer[:-100_000]
                else:
                    time.sleep(0.01)
            except Exception as e:
                if self._running:
                    logger.error(f"串口接收异常: {e}")
                    time.sleep(0.5)
