"""
DBC 文件解析器
===============
解析 DBC（CAN Database）文件，实现信号级的 CAN 消息读写。

DBC 文件包含：
- 消息定义（ID、长度、发送节点）
- 信号定义（名称、位置、长度、字节序、值类型）
- 值描述（枚举值）
- 单位、注释等
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union


class ByteOrder(Enum):
    """字节序"""
    MOTOROLA = 0  # 大端
    INTEL = 1     # 小端


class ValueType(Enum):
    """值类型"""
    UNSIGNED = 0
    SIGNED = 1


@dataclass
class Signal:
    """CAN 信号"""
    name: str
    start_bit: int
    length: int
    byte_order: ByteOrder = ByteOrder.INTEL
    value_type: ValueType = ValueType.UNSIGNED
    factor: float = 1.0
    offset: float = 0.0
    min_value: float = 0.0
    max_value: float = 0.0
    unit: str = ""
    receiver: List[str] = field(default_factory=list)
    value_descriptions: Dict[int, str] = field(default_factory=dict)
    comment: str = ""

    def decode(self, data: bytes) -> float:
        """
        从 CAN 数据解码信号值
        
        参数:
            data: CAN 消息数据（8 字节）
        """
        # 提取原始位
        raw = self._extract_bits(data)
        
        # 应用缩放和偏移
        value = raw * self.factor + self.offset
        return value

    def encode(self, value: float) -> bytes:
        """
        编码信号值到 CAN 数据
        
        参数:
            value: 物理值
        返回:
            8 字节 CAN 数据
        """
        # 反向计算原始值
        raw = int((value - self.offset) / self.factor)
        
        # 限制范围
        raw = max(0, min(raw, (1 << self.length) - 1))
        
        # 编码到位
        return self._encode_bits(raw)

    def _extract_bits(self, data: bytes) -> int:
        """从数据中提取原始位"""
        if self.byte_order == ByteOrder.INTEL:
            return self._extract_intel(data)
        else:
            return self._extract_motorola(data)

    def _extract_intel(self, data: bytes) -> int:
        """Intel 字节序（小端）提取"""
        byte_start = self.start_bit // 8
        bit_start = self.start_bit % 8
        
        value = 0
        for i in range(self.length):
            byte_idx = byte_start + (bit_start + i) // 8
            bit_idx = (bit_start + i) % 8
            if byte_idx < len(data):
                if data[byte_idx] & (1 << bit_idx):
                    value |= (1 << i)
        
        # 处理有符号
        if self.value_type == ValueType.SIGNED and (value & (1 << (self.length - 1))):
            value -= (1 << self.length)
        
        return value

    def _extract_motorola(self, data: bytes) -> int:
        """Motorola 字节序（大端）提取"""
        byte_start = self.start_bit // 8
        bit_start = self.start_bit % 8
        
        value = 0
        for i in range(self.length):
            byte_idx = byte_start - (bit_start + i) // 8
            bit_idx = 7 - (bit_start + i) % 8
            if 0 <= byte_idx < len(data):
                if data[byte_idx] & (1 << bit_idx):
                    value |= (1 << i)
        
        if self.value_type == ValueType.SIGNED and (value & (1 << (self.length - 1))):
            value -= (1 << self.length)
        
        return value

    def _encode_bits(self, value: int) -> bytes:
        """编码值到位"""
        data = bytearray(8)
        
        if self.byte_order == ByteOrder.INTEL:
            byte_start = self.start_bit // 8
            bit_start = self.start_bit % 8
            
            for i in range(self.length):
                byte_idx = byte_start + (bit_start + i) // 8
                bit_idx = (bit_start + i) % 8
                if byte_idx < 8:
                    if value & (1 << i):
                        data[byte_idx] |= (1 << bit_idx)
        else:
            byte_start = self.start_bit // 8
            bit_start = self.start_bit % 8
            
            for i in range(self.length):
                byte_idx = byte_start - (bit_start + i) // 8
                bit_idx = 7 - (bit_start + i) % 8
                if 0 <= byte_idx < 8:
                    if value & (1 << i):
                        data[byte_idx] |= (1 << bit_idx)
        
        return bytes(data)


@dataclass
class Message:
    """CAN 消息"""
    id: int
    name: str
    length: int = 8
    sender: str = ""
    signals: Dict[str, Signal] = field(default_factory=dict)
    comment: str = ""

    def decode(self, data: bytes) -> Dict[str, float]:
        """解码消息中的所有信号"""
        result = {}
        for name, signal in self.signals.items():
            try:
                result[name] = signal.decode(data)
            except Exception:
                result[name] = None
        return result

    def encode(self, values: Dict[str, float]) -> bytes:
        """编码信号值到消息数据"""
        data = bytearray(8)
        for name, value in values.items():
            if name in self.signals:
                signal_data = self.signals[name].encode(value)
                for i in range(8):
                    data[i] |= signal_data[i]
        return bytes(data)


class DBCParser:
    """DBC 文件解析器"""

    def __init__(self):
        self.messages: Dict[int, Message] = {}
        self.nodes: List[str] = []
        self.version: str = ""

    def parse_file(self, path: Union[str, Path]) -> bool:
        """解析 DBC 文件"""
        path = Path(path)
        if not path.exists():
            return False

        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        return self.parse_content(content)

    def parse_content(self, content: str) -> bool:
        """解析 DBC 内容"""
        try:
            # 解析版本
            version_match = re.search(r'VERSION\s+"([^"]*)"', content)
            if version_match:
                self.version = version_match.group(1)

            # 解析节点
            nodes_match = re.search(r'BU_:\s*(.*?)(?:\n|$)', content)
            if nodes_match:
                self.nodes = [n.strip() for n in nodes_match.group(1).split() if n.strip()]

            # 解析消息
            msg_pattern = r'BO_\s+(\d+)\s+(\w+):\s+(\d+)\s+(\w+)'
            for match in re.finditer(msg_pattern, content):
                msg_id = int(match.group(1))
                msg_name = match.group(2)
                msg_length = int(match.group(3))
                msg_sender = match.group(4)

                msg = Message(
                    id=msg_id,
                    name=msg_name,
                    length=msg_length,
                    sender=msg_sender
                )

                # 解析该消息的信号
                msg_block = self._extract_message_block(content, msg_id)
                self._parse_signals(msg, msg_block)

                self.messages[msg_id] = msg

            return True
        except Exception as e:
            print(f"DBC 解析错误：{e}")
            return False

    def _extract_message_block(self, content: str, msg_id: int) -> str:
        """提取消息块内容"""
        pattern = rf'BO_\s+{msg_id}\s+\w+:.*?(?=BO_\s|\n\n|$)'
        match = re.search(pattern, content, re.DOTALL)
        return match.group(0) if match else ""

    def _parse_signals(self, msg: Message, block: str):
        """解析信号"""
        sig_pattern = r'SG_\s+(\w+)\s*:\s*(\d+)\|(\d+)@(\d+)([+-])\s*\(([0-9.eE+-]+),([0-9.eE+-]+)\)\s*\[([0-9.eE+-]+)\|([0-9.eE+-]+)\]\s*"([^"]*)"\s*(.*)'
        
        for match in re.finditer(sig_pattern, block):
            name = match.group(1)
            start_bit = int(match.group(2))
            length = int(match.group(3))
            byte_order = ByteOrder.INTEL if match.group(4) == '1' else ByteOrder.MOTOROLA
            value_type = ValueType.SIGNED if match.group(5) == '-' else ValueType.UNSIGNED
            factor = float(match.group(6))
            offset = float(match.group(7))
            min_val = float(match.group(8))
            max_val = float(match.group(9))
            unit = match.group(10)
            receivers = [r.strip() for r in match.group(11).split(',') if r.strip()]

            signal = Signal(
                name=name,
                start_bit=start_bit,
                length=length,
                byte_order=byte_order,
                value_type=value_type,
                factor=factor,
                offset=offset,
                min_value=min_val,
                max_value=max_val,
                unit=unit,
                receiver=receivers
            )

            msg.signals[name] = signal

    def get_message(self, msg_id: int) -> Optional[Message]:
        """根据 ID 获取消息"""
        return self.messages.get(msg_id)

    def get_signal(self, msg_id: int, signal_name: str) -> Optional[Signal]:
        """获取信号"""
        msg = self.get_message(msg_id)
        if msg:
            return msg.signals.get(signal_name)
        return None


# 测试代码
if __name__ == "__main__":
    print("DBC 解析器")
    print("=" * 50)
    
    # 示例：创建一个简单的 DBC 内容
    dbc_content = """
VERSION ""

NS_ :

BS_:

BU_: ECU1 ECU2

BO_ 100 EngineData: 8 ECU1
 SG_ EngineSpeed : 0|16@1+ (0.1,0) [0|6553.5] "rpm" ECU2
 SG_ EngineTemp : 16|8@1+ (1,-40) [-40|215] "°C" ECU2

BO_ 200 VehicleSpeed: 8 ECU1
 SG_ Speed : 0|16@1+ (0.01,0) [0|655.35] "km/h" ECU2
"""
    
    parser = DBCParser()
    if parser.parse_content(dbc_content):
        print(f"解析成功：{len(parser.messages)} 条消息")
        for msg_id, msg in parser.messages.items():
            print(f"  消息 0x{msg_id:03X}: {msg.name} ({len(msg.signals)} 信号)")
            for sig_name, sig in msg.signals.items():
                print(f"    - {sig_name}: 起始位{sig.start_bit}, 长度{sig.length}, 单位{sig.unit}")
        
        # 测试解码
        msg = parser.get_message(100)
        if msg:
            # 模拟数据：转速 3000rpm (30000 * 0.1 = 3000)
            data = bytes([0x30, 0x39, 0x55, 0x00, 0x00, 0x00, 0x00, 0x00])
            values = msg.decode(data)
            print(f"\n解码示例：{values}")
    else:
        print("解析失败")
