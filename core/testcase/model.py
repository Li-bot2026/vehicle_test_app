"""
测试用例数据模型
================
定义测试用例、测试步骤、测试套件的数据结构。

设计说明：
- TestCase: 单个测试用例，包含前置/后置条件、步骤列表、断言规则
- TestStep: 单个测试步骤，描述一个原子操作（发送/等待/断言/延时等）
- TestSuite: 测试套件，组织多个用例的集合
- 支持参数化与数据驱动
"""

import uuid
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime


# ============================================================
# 枚举定义
# ============================================================

class Priority(Enum):
    """用例优先级"""
    P0 = "P0"   # 最高优先级 - 核心功能
    P1 = "P1"   # 高优先级 - 重要功能
    P2 = "P2"   # 中优先级 - 一般功能
    P3 = "P3"   # 低优先级 - 边缘场景


class StepType(Enum):
    """步骤类型"""
    SEND_SERIAL = "send_serial"         # 发送串口数据
    SEND_NETWORK = "send_network"       # 发送网络数据
    SEND_ADB = "send_adb"               # 执行 ADB 命令
    SEND_CAN = "send_can"               # 发送 CAN 消息
    WAIT = "wait"                       # 等待指定时间
    WAIT_SIGNAL = "wait_signal"         # 等待特定信号/报文
    WAIT_CAN = "wait_can"               # 等待 CAN 消息
    ASSERT_VALUE = "assert_value"       # 数值断言
    ASSERT_RANGE = "assert_range"       # 范围断言
    ASSERT_STATE = "assert_state"       # 状态断言
    ASSERT_TIMING = "assert_timing"     # 时序断言
    ASSERT_CUSTOM = "assert_custom"     # 自定义脚本断言
    SCREENSHOT = "screenshot"           # 截图
    LOG_MESSAGE = "log_message"         # 记录日志
    APP_START = "app_start"             # 启动应用（自动解析启动页）
    UI_TAP = "ui_tap"                   # 按界面文字点击
    UI_INPUT = "ui_input"               # 向输入框输入文字
    UI_WAIT_TEXT = "ui_wait_text"       # 等待界面出现指定文字
    UI_SWIPE = "ui_swipe"               # 滑动/滚动（可指定区域与方向）
    AGENT_TASK = "agent_task"           # 自主任务：读屏→AI决策→执行，循环直到目标达成


class TestStatus(Enum):
    """测试状态"""
    PENDING = "pending"         # 待执行
    RUNNING = "running"         # 执行中
    PASSED = "passed"           # 通过
    FAILED = "failed"           # 失败
    ERROR = "error"             # 错误
    SKIPPED = "skipped"         # 跳过
    ABORTED = "aborted"         # 中止


# ============================================================
# 数据模型
# ============================================================

@dataclass
class TestStep:
    """
    测试步骤
    ---------
    描述一个原子操作，是测试用例的最小执行单元。
    """
    __test__ = False  # 避免 pytest 误收集数据类

    step_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    step_type: StepType = StepType.WAIT
    description: str = ""               # 步骤描述

    # 通用参数
    device_id: str = ""                 # 目标设备 ID
    params: Dict[str, Any] = field(default_factory=dict)

    # 预期结果（用于断言步骤）
    expected: Any = None
    actual: Any = None
    tolerance: float = 0.0              # 容差

    # 超时设置
    timeout: float = 30.0               # 步骤超时（秒）

    # 执行结果
    status: TestStatus = TestStatus.PENDING
    error_message: str = ""
    duration: float = 0.0               # 执行耗时（秒）
    timestamp: Optional[datetime] = None
    attachments: List[str] = field(default_factory=list)  # 步骤附件（截图、日志等）

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "step_id": self.step_id,
            "step_type": self.step_type.value,
            "description": self.description,
            "device_id": self.device_id,
            "params": self.params,
            "expected": self.expected,
            "actual": self.actual,
            "tolerance": self.tolerance,
            "timeout": self.timeout,
            "status": self.status.value,
            "error_message": self.error_message,
            "duration": self.duration,
            "attachments": self.attachments,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TestStep":
        """从字典反序列化"""
        raw_type = data.get("step_type", "wait")
        try:
            step_type = StepType(raw_type)
        except ValueError:
            step_type = StepType.WAIT
        return cls(
            step_id=data.get("step_id", str(uuid.uuid4())[:8]),
            step_type=step_type,
            description=data.get("description", ""),
            device_id=data.get("device_id", ""),
            params=data.get("params", {}),
            expected=data.get("expected"),
            actual=data.get("actual"),
            tolerance=data.get("tolerance", 0.0),
            timeout=data.get("timeout", 30.0),
            status=TestStatus(data.get("status", "pending")),
            error_message=data.get("error_message", ""),
            duration=data.get("duration", 0.0),
            attachments=data.get("attachments", []),
        )


@dataclass
class TestCase:
    """
    测试用例
    ---------
    包含完整的测试定义：前置条件、步骤列表、后置条件、断言规则。
    支持参数化与数据驱动。
    """
    __test__ = False  # 避免 pytest 误收集数据类

    case_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    name: str = ""                          # 用例名称
    description: str = ""                   # 用例描述
    category: str = ""                      # 分类（如：音频、导航、空调）
    group: str = ""                         # 所属套件/分组（如：搜索测试、导入批次）
    tags: List[str] = field(default_factory=list)  # 标签
    priority: Priority = Priority.P1        # 优先级

    # 前置/后置条件
    setup_steps: List[TestStep] = field(default_factory=list)
    teardown_steps: List[TestStep] = field(default_factory=list)

    # 主测试步骤
    steps: List[TestStep] = field(default_factory=list)

    # 参数化支持
    parameters: Dict[str, Any] = field(default_factory=dict)
    parameter_sets: List[Dict[str, Any]] = field(default_factory=list)

    # 执行配置
    retry_count: Optional[int] = None       # 失败重试次数（None=沿用全局）
    retry_delay: float = 1.0                # 重试间隔
    timeout: float = 60.0                   # 用例总超时

    # 执行结果
    status: TestStatus = TestStatus.PENDING
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration: float = 0.0
    error_message: str = ""
    log_entries: List[str] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)
    attachments: List[str] = field(default_factory=list)  # 通用附件（截图、日志等文件路径）

    # 元数据
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    author: str = ""
    version: str = "1.0"
    is_template: bool = False             # 参数化模板用例（默认不在主列表显示）
    parent_id: Optional[str] = None       # 父用例 ID（参数化展开时设置）

    @property
    def total_steps(self) -> int:
        """总步骤数"""
        return len(self.setup_steps) + len(self.steps) + len(self.teardown_steps)

    @property
    def passed_steps(self) -> int:
        """已通过步骤数"""
        return sum(1 for s in self.all_steps if s.status == TestStatus.PASSED)

    @property
    def all_steps(self) -> List[TestStep]:
        """按执行顺序返回全部步骤（前置 → 主体 → 后置）"""
        return list(self.setup_steps) + list(self.steps) + list(self.teardown_steps)

    @property
    def is_passed(self) -> bool:
        return self.status == TestStatus.PASSED

    def reset(self):
        """重置执行状态"""
        self.status = TestStatus.PENDING
        self.start_time = None
        self.end_time = None
        self.duration = 0.0
        self.error_message = ""
        self.log_entries.clear()
        self.screenshots.clear()
        for step in self.all_steps:
            step.status = TestStatus.PENDING
            step.actual = None
            step.error_message = ""
            step.duration = 0.0

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "case_id": self.case_id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "group": self.group,
            "tags": self.tags,
            "priority": self.priority.value,
            "setup_steps": [s.to_dict() for s in self.setup_steps],
            "teardown_steps": [s.to_dict() for s in self.teardown_steps],
            "steps": [s.to_dict() for s in self.steps],
            "parameters": self.parameters,
            "parameter_sets": self.parameter_sets,
            "retry_count": self.retry_count,
            "retry_delay": self.retry_delay,
            "timeout": self.timeout,
            "status": self.status.value,
            "duration": self.duration,
            "error_message": self.error_message,
            "screenshots": self.screenshots,
            "attachments": self.attachments,
            "author": self.author,
            "version": self.version,
            "is_template": self.is_template,
            "parent_id": self.parent_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TestCase":
        """从字典反序列化"""
        return cls(
            case_id=data.get("case_id", str(uuid.uuid4())[:12]),
            name=data.get("name", ""),
            description=data.get("description", ""),
            category=data.get("category", ""),
            group=data.get("group", ""),
            tags=data.get("tags", []),
            priority=Priority(data.get("priority", "P1")),
            setup_steps=[TestStep.from_dict(s) for s in data.get("setup_steps", [])],
            teardown_steps=[TestStep.from_dict(s) for s in data.get("teardown_steps", [])],
            steps=[TestStep.from_dict(s) for s in data.get("steps", [])],
            parameters=data.get("parameters", {}),
            parameter_sets=data.get("parameter_sets", []),
            retry_count=data.get("retry_count"),
            retry_delay=data.get("retry_delay", 1.0),
            timeout=data.get("timeout", 60.0),
            status=TestStatus(data.get("status", "pending")),
            duration=data.get("duration", 0.0),
            error_message=data.get("error_message", ""),
            screenshots=data.get("screenshots", []),
            attachments=data.get("attachments", []),
            author=data.get("author", ""),
            version=data.get("version", "1.0"),
            is_template=data.get("is_template", False),
            parent_id=data.get("parent_id"),
        )


@dataclass
class TestSuite:
    """
    测试套件
    ---------
    组织多个测试用例的集合，支持批量执行。
    """
    suite_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    name: str = ""
    description: str = ""
    version: str = "1.0"
    author: str = ""

    # 用例列表
    test_cases: List[TestCase] = field(default_factory=list)

    # 全局配置
    setup_steps: List[TestStep] = field(default_factory=list)
    teardown_steps: List[TestStep] = field(default_factory=list)

    # 执行结果统计
    total_count: int = 0
    passed_count: int = 0
    failed_count: int = 0
    error_count: int = 0
    skipped_count: int = 0
    aborted_count: int = 0
    total_duration: float = 0.0

    # 元数据
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def pass_rate(self) -> float:
        """通过率（中止用例不计入分母）"""
        evaluated = self.total_count - self.skipped_count - self.aborted_count
        if evaluated <= 0:
            return 0.0
        return self.passed_count / evaluated * 100

    def add_case(self, case: TestCase) -> None:
        """添加测试用例"""
        self.test_cases.append(case)
        self.total_count = len(self.test_cases)

    def remove_case(self, case_id: str) -> bool:
        """移除测试用例"""
        before = len(self.test_cases)
        self.test_cases = [c for c in self.test_cases if c.case_id != case_id]
        self.total_count = len(self.test_cases)
        return len(self.test_cases) < before

    def get_cases_by_priority(self, priority: Priority) -> List[TestCase]:
        """按优先级筛选用例"""
        return [c for c in self.test_cases if c.priority == priority]

    def get_cases_by_category(self, category: str) -> List[TestCase]:
        """按分类筛选用例"""
        return [c for c in self.test_cases if c.category == category]

    def get_cases_by_tag(self, tag: str) -> List[TestCase]:
        """按标签筛选用例"""
        return [c for c in self.test_cases if tag in c.tags]

    def get_failed_cases(self) -> List[TestCase]:
        """获取失败用例"""
        return [c for c in self.test_cases
                if c.status in (TestStatus.FAILED, TestStatus.ERROR)]

    def calculate_stats(self):
        """计算统计信息"""
        self.total_count = len(self.test_cases)
        self.passed_count = sum(1 for c in self.test_cases if c.status == TestStatus.PASSED)
        self.failed_count = sum(1 for c in self.test_cases if c.status == TestStatus.FAILED)
        self.error_count = sum(1 for c in self.test_cases if c.status == TestStatus.ERROR)
        self.skipped_count = sum(1 for c in self.test_cases if c.status == TestStatus.SKIPPED)
        self.aborted_count = sum(1 for c in self.test_cases if c.status == TestStatus.ABORTED)
        self.total_duration = sum(c.duration for c in self.test_cases)

    def to_dict(self) -> dict:
        """序列化"""
        return {
            "suite_id": self.suite_id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "test_cases": [c.to_dict() for c in self.test_cases],
            "setup_steps": [s.to_dict() for s in self.setup_steps],
            "teardown_steps": [s.to_dict() for s in self.teardown_steps],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TestSuite":
        """反序列化"""
        suite = cls(
            suite_id=data.get("suite_id", str(uuid.uuid4())[:12]),
            name=data.get("name", ""),
            description=data.get("description", ""),
            version=data.get("version", "1.0"),
            author=data.get("author", ""),
            setup_steps=[TestStep.from_dict(s) for s in data.get("setup_steps", [])],
            teardown_steps=[TestStep.from_dict(s) for s in data.get("teardown_steps", [])],
        )
        for case_data in data.get("test_cases", []):
            suite.add_case(TestCase.from_dict(case_data))
        return suite
