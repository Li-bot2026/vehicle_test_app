"""
断言引擎
=========
提供各类断言的实现，用于测试结果的判定。

支持的断言类型：
- 数值断言：精确值、容差比较
- 范围断言：最小值/最大值范围
- 状态断言：字符串/枚举状态比较
- 时序断言：响应时间范围
- 自定义脚本断言：Python 表达式
"""

import re
from typing import Any, Optional, Union
from dataclasses import dataclass

from loguru import logger


@dataclass
class AssertionResult:
    """断言结果"""
    passed: bool
    expected: Any = None
    actual: Any = None
    message: str = ""


class AssertionEngine:
    """
    断言引擎
    ---------
    提供统一的断言接口，返回标准化的断言结果。
    """

    # ---------- 数值断言 ----------

    def assert_value(self, actual: Any, expected: Any,
                     tolerance: float = 0.0) -> bool:
        """
        数值断言：actual 是否等于 expected（允许容差）。

        参数:
            actual: 实际值
            expected: 期望值
            tolerance: 容差范围
        """
        if actual is None or expected is None:
            logger.debug(f"数值断言缺少实际值或期望值: {actual!r} vs {expected!r}")
            return False
        try:
            actual_f = float(actual)
            expected_f = float(expected)

            if tolerance > 0:
                passed = abs(actual_f - expected_f) <= tolerance
            else:
                passed = actual_f == expected_f

            if not passed:
                logger.debug(
                    f"数值断言失败: 实际={actual_f}, 期望={expected_f}, "
                    f"容差={tolerance}"
                )
            return passed

        except (ValueError, TypeError) as e:
            # 非数值类型，使用字符串比较
            passed = str(actual) == str(expected)
            if not passed:
                logger.debug(f"字符串断言失败: 实际='{actual}', 期望='{expected}'")
            return passed

    # ---------- 范围断言 ----------

    def assert_range(self, actual: Any,
                     min_val: Optional[float] = None,
                     max_val: Optional[float] = None) -> bool:
        """
        范围断言：actual 是否在 [min_val, max_val] 范围内。

        参数:
            actual: 实际值
            min_val: 最小值（含），None 表示不检查下限
            max_val: 最大值（含），None 表示不检查上限
        """
        try:
            actual_f = float(actual)
        except (ValueError, TypeError):
            logger.debug(f"范围断言失败: 无法转换为数值: {actual}")
            return False

        if min_val is not None and actual_f < float(min_val):
            logger.debug(f"范围断言失败: {actual_f} < {min_val}")
            return False

        if max_val is not None and actual_f > float(max_val):
            logger.debug(f"范围断言失败: {actual_f} > {max_val}")
            return False

        return True

    # ---------- 状态断言 ----------

    def assert_state(self, actual: Any, expected: Any) -> bool:
        """
        状态断言：actual 是否匹配 expected 状态。

        支持：
        - 精确匹配: "ON" == "ON"
        - 包含匹配: "ON" in ["ON", "STANDBY"]
        - 正则匹配: 以 "r:" 前缀
        """
        if actual is None or expected is None:
            logger.debug(f"状态断言缺少实际值或期望值: {actual!r} vs {expected!r}")
            return False
        actual_str = str(actual).strip()
        expected_str = str(expected).strip()

        # 正则匹配
        if expected_str.startswith("r:"):
            pattern = expected_str[2:]
            return bool(re.match(pattern, actual_str))

        # 列表匹配
        if "," in expected_str:
            options = [s.strip() for s in expected_str.split(",")]
            return actual_str in options

        # 精确匹配（不区分大小写）
        return actual_str.upper() == expected_str.upper()

    # ---------- 时序断言 ----------

    def assert_timing(self, actual_time: Any,
                      max_time: Optional[float] = None,
                      min_time: Optional[float] = None) -> bool:
        """
        时序断言：响应时间是否在允许范围内。

        参数:
            actual_time: 实际耗时（秒）
            max_time: 最大允许时间
            min_time: 最小允许时间（可选）
        """
        try:
            actual_f = float(actual_time)
        except (ValueError, TypeError):
            logger.debug(f"时序断言失败: 无法转换为数值: {actual_time}")
            return False

        if max_time is not None and actual_f > max_time:
            logger.debug(f"时序断言失败: {actual_f}s > {max_time}s")
            return False

        if min_time is not None and actual_f < min_time:
            logger.debug(f"时序断言失败: {actual_f}s < {min_time}s")
            return False

        return True

    # ---------- 复合断言 ----------

    def assert_all(self, *conditions: bool) -> bool:
        """所有条件都必须满足"""
        return all(conditions)

    def assert_any(self, *conditions: bool) -> bool:
        """至少一个条件满足"""
        return any(conditions)

    def assert_not_equal(self, actual: Any, unexpected: Any) -> bool:
        """不等于断言"""
        return str(actual) != str(unexpected)

    def assert_contains(self, text: Any, substring: str) -> bool:
        """包含断言"""
        return substring in str(text)

    def assert_not_contains(self, text: Any, substring: str) -> bool:
        """不包含断言"""
        return substring not in str(text)

    def assert_regex(self, text: Any, pattern: str) -> bool:
        """正则匹配断言"""
        return bool(re.search(pattern, str(text)))
