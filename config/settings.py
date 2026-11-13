"""
全局配置管理
============
管理应用的所有配置项，包括：
- 设备连接参数（串口 / 网络 / ADB）
- 测试执行参数（重试次数、超时时间等）
- 日志级别与输出路径
- 报告模板与导出格式
- 用户偏好设置

配置以 YAML 文件持久化存储，支持多环境切换（开发/测试/预生产）。
"""

import os
import yaml
import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional
from loguru import logger


# ============================================================
# 默认配置路径
# ============================================================
APP_DIR = Path.home() / ".vehicle_test_app"
CONFIG_FILE = APP_DIR / "config.yaml"
DB_FILE = APP_DIR / "test_results.db"
LOG_DIR = APP_DIR / "logs"


# ============================================================
# 数据类定义
# ============================================================

@dataclass
class SerialConfig:
    """串口连接配置"""
    port: str = "COM3"                  # 串口号
    baudrate: int = 115200              # 波特率
    data_bits: int = 8
    stop_bits: int = 1
    parity: str = "N"                   # N / E / O
    timeout: float = 1.0                # 读取超时（秒）


@dataclass
class NetworkConfig:
    """网络连接配置（TCP/UDP）"""
    protocol: str = "tcp"               # tcp / udp
    host: str = "192.168.1.100"         # 目标 IP
    port: int = 8080                    # 目标端口
    local_port: int = 0                 # 本地端口（UDP 用）
    timeout: float = 5.0                # 连接超时


@dataclass
class ADBConfig:
    """ADB 连接配置"""
    device_serial: str = ""             # 设备序列号（留空自动选择）
    host: str = "127.0.0.1"
    port: int = 5037
    auto_connect: bool = True


@dataclass
class ExecutionConfig:
    """测试执行配置"""
    retry_count: int = 2                # 失败自动重试次数
    retry_delay: float = 1.0            # 重试间隔（秒）
    step_timeout: float = 30.0          # 单步超时（秒）
    batch_interval: float = 0.5         # 批量执行间隔（秒）
    parallel_devices: bool = False       # 是否多设备并行
    stop_on_failure: bool = False        # 失败后是否停止


@dataclass
class ReportConfig:
    """报告生成配置"""
    template_dir: str = "templates"      # 模板目录
    output_dir: str = "reports"          # 报告输出目录
    export_formats: list = field(
        default_factory=lambda: ["html", "pdf"]
    )
    include_screenshots: bool = True     # 报告中包含截图
    include_logs: bool = True            # 报告中包含日志


@dataclass
class LogConfig:
    """日志配置"""
    level: str = "INFO"                  # DEBUG / INFO / WARN / ERROR
    log_to_file: bool = True
    log_dir: str = str(LOG_DIR)
    max_file_size: str = "50 MB"         # 单文件最大
    retention: str = "30 days"           # 保留天数
    audit_log: bool = True               # 是否记录审计日志


@dataclass
class EnvironmentConfig:
    """测试环境配置"""
    name: str = "开发环境"               # 环境名称
    description: str = ""
    vehicle_type: str = ""               # 车型
    ecu_version: str = ""                # ECU 软件版本


@dataclass
class AIConfig:
    """AI 测试助手配置（OpenAI 兼容接口）"""
    enabled: bool = True                 # 总开关
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""                    # 留空时使用内置规则引擎
    model: str = "deepseek-chat"
    timeout: float = 60.0                # 请求超时（秒）
    auto_diagnose: bool = True           # 用例失败时自动分析原因
    auto_repair: bool = False            # 不询问直接自动修复并重试
    max_fix_rounds: int = 2              # 同一用例自动修复最大轮次
    offline_fallback: bool = True        # 无 API Key 时使用内置规则引擎
    vision: bool = False                 # 强制启用图像输入（留空自动探测）
    vision_model: str = ""               # 视觉模型名称，留空则自动推导
    vision_base_url: str = ""            # 视觉模型独立接口，留空沿用 base_url
    vision_api_key: str = ""             # 视觉模型独立 Key，留空沿用 api_key


@dataclass
class AppConfig:
    """应用总配置"""
    serial: SerialConfig = field(default_factory=SerialConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    adb: ADBConfig = field(default_factory=ADBConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    log: LogConfig = field(default_factory=LogConfig)
    environment: EnvironmentConfig = field(default_factory=EnvironmentConfig)
    ai: AIConfig = field(default_factory=AIConfig)


# ============================================================
# 配置管理器
# ============================================================

class ConfigManager:
    """
    配置管理器（单例模式）
    ----------------------
    负责配置的加载、保存、获取与多环境切换。
    """

    _instance: Optional["ConfigManager"] = None
    _config: AppConfig

    def __new__(cls):
        """单例模式：确保全局只有一个配置管理器实例"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._config = AppConfig()
            cls._instance._config_path = CONFIG_FILE
        return cls._instance

    # ---------- 加载 / 保存 ----------

    def load(self, config_path: Optional[str] = None) -> AppConfig:
        """
        从 YAML 文件加载配置。
        如果文件不存在，则使用默认配置并自动创建。
        """
        path = Path(config_path) if config_path else self._config_path

        if not path.exists():
            logger.info(f"配置文件不存在，使用默认配置: {path}")
            self._ensure_dirs()
            self.save(str(path))
            return self._config

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self._config = self._dict_to_config(data)
            logger.info(f"配置加载成功: {path}")
        except Exception as e:
            logger.error(f"配置加载失败: {e}，使用默认配置")
            self._config = AppConfig()

        return self._config

    def save(self, config_path: Optional[str] = None) -> None:
        """将当前配置保存到 YAML 文件"""
        path = Path(config_path) if config_path else self._config_path
        self._ensure_dirs()

        try:
            data = asdict(self._config)
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False,
                          allow_unicode=True, sort_keys=False)
            logger.info(f"配置已保存: {path}")
        except Exception as e:
            logger.error(f"配置保存失败: {e}")

    # ---------- 获取 / 设置 ----------

    @property
    def config(self) -> AppConfig:
        """获取当前配置对象"""
        return self._config

    def get(self, section: str, key: str, default=None):
        """
        获取配置值。
        用法: get("serial", "baudrate") -> 115200
        """
        section_obj = getattr(self._config, section, None)
        if section_obj is None:
            return default
        return getattr(section_obj, key, default)

    def set(self, section: str, key: str, value) -> None:
        """
        设置配置值。
        用法: set("can", "bitrate", 250000)
        """
        section_obj = getattr(self._config, section, None)
        if section_obj is None:
            logger.warning(f"配置节不存在: {section}")
            return
        if hasattr(section_obj, key):
            setattr(section_obj, key, value)
        else:
            logger.warning(f"配置项不存在: {section}.{key}")

    # ---------- 环境切换 ----------

    def switch_environment(self, env_name: str) -> None:
        """
        切换测试环境（开发 / 测试 / 预生产）。
        每个环境有独立的配置文件。
        """
        env_file = APP_DIR / f"config_{env_name}.yaml"
        if env_file.exists():
            self.load(str(env_file))
            self._config.environment.name = env_name
            logger.info(f"已切换到环境: {env_name}")
        else:
            logger.warning(f"环境配置文件不存在: {env_file}")
            self._config.environment.name = env_name

    def export_presets(self) -> dict:
        """导出预置环境配置模板"""
        return {
            "开发环境": {
                "network": {"host": "192.168.1.100", "port": 8080},
                "execution": {"retry_count": 0, "step_timeout": 60.0},
                "log": {"level": "DEBUG"},
            },
            "测试环境": {
                "network": {"host": "10.0.0.100", "port": 8080},
                "execution": {"retry_count": 2, "step_timeout": 30.0},
                "log": {"level": "INFO"},
            },
            "预生产环境": {
                "network": {"host": "172.16.0.100", "port": 8080},
                "execution": {"retry_count": 3, "step_timeout": 20.0},
                "log": {"level": "WARN"},
            },
        }

    # ---------- 内部方法 ----------

    def _dict_to_config(self, data: dict) -> AppConfig:
        """将字典转换为 AppConfig 数据类"""
        config = AppConfig()
        section_map = {
            "serial": SerialConfig,
            "network": NetworkConfig,
            "adb": ADBConfig,
            "execution": ExecutionConfig,
            "report": ReportConfig,
            "log": LogConfig,
            "environment": EnvironmentConfig,
            "ai": AIConfig,
        }
        for section_name, section_cls in section_map.items():
            section_data = data.get(section_name, {})
            if section_data:
                # 只传入类中已定义的字段，忽略多余键
                valid_keys = {f.name for f in section_cls.__dataclass_fields__.values()}
                filtered = {k: v for k, v in section_data.items() if k in valid_keys}
                setattr(config, section_name, section_cls(**filtered))
        return config

    def _ensure_dirs(self) -> None:
        """确保必要的目录存在"""
        APP_DIR.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        Path(self._config.report.output_dir).mkdir(parents=True, exist_ok=True)
