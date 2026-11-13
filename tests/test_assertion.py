"""
断言引擎 - 单元测试
"""

import pytest
from core.assertion.engine import AssertionEngine


class TestValueAssertion:
    """数值断言测试"""

    def setup_method(self):
        self.engine = AssertionEngine()

    def test_exact_match(self):
        """精确匹配"""
        assert self.engine.assert_value(100, 100)
        assert not self.engine.assert_value(100, 200)

    def test_with_tolerance(self):
        """带容差比较"""
        assert self.engine.assert_value(100, 102, tolerance=5)
        assert not self.engine.assert_value(100, 110, tolerance=5)

    def test_string_match(self):
        """字符串匹配"""
        assert self.engine.assert_value("ON", "ON")
        assert not self.engine.assert_value("ON", "OFF")

    def test_float_values(self):
        """浮点数比较"""
        assert self.engine.assert_value(3.14, 3.14)
        assert self.engine.assert_value(3.14, 3.15, tolerance=0.02)


class TestRangeAssertion:
    """范围断言测试"""

    def setup_method(self):
        self.engine = AssertionEngine()

    def test_within_range(self):
        """在范围内"""
        assert self.engine.assert_range(50, min_val=0, max_val=100)

    def test_below_min(self):
        """低于最小值"""
        assert not self.engine.assert_range(-5, min_val=0, max_val=100)

    def test_above_max(self):
        """超过最大值"""
        assert not self.engine.assert_range(150, min_val=0, max_val=100)

    def test_only_min(self):
        """仅检查下限"""
        assert self.engine.assert_range(50, min_val=0)
        assert not self.engine.assert_range(-1, min_val=0)

    def test_only_max(self):
        """仅检查上限"""
        assert self.engine.assert_range(50, max_val=100)
        assert not self.engine.assert_range(150, max_val=100)


class TestStateAssertion:
    """状态断言测试"""

    def setup_method(self):
        self.engine = AssertionEngine()

    def test_exact_state(self):
        """精确状态匹配"""
        assert self.engine.assert_state("RUNNING", "RUNNING")
        assert not self.engine.assert_state("RUNNING", "STOPPED")

    def test_case_insensitive(self):
        """不区分大小写"""
        assert self.engine.assert_state("running", "RUNNING")

    def test_list_match(self):
        """列表匹配"""
        assert self.engine.assert_state("ON", "ON,OFF,STANDBY")
        assert not self.engine.assert_state("ERROR", "ON,OFF,STANDBY")

    def test_regex_match(self):
        """正则匹配"""
        assert self.engine.assert_state("P0300", "r:P0\\d{3}")
        assert not self.engine.assert_state("P030A", "r:P0\\d{3}$")


class TestTimingAssertion:
    """时序断言测试"""

    def setup_method(self):
        self.engine = AssertionEngine()

    def test_within_max(self):
        """在最大时间以内"""
        assert self.engine.assert_timing(0.05, max_time=0.1)

    def test_exceeds_max(self):
        """超过最大时间"""
        assert not self.engine.assert_timing(0.2, max_time=0.1)

    def test_within_range(self):
        """在时间范围内"""
        assert self.engine.assert_timing(0.05, min_time=0.01, max_time=0.1)

    def test_below_min(self):
        """低于最小时间"""
        assert not self.engine.assert_timing(0.001, min_time=0.01, max_time=0.1)


class TestCompositeAssertions:
    """复合断言测试"""

    def setup_method(self):
        self.engine = AssertionEngine()

    def test_assert_all(self):
        """全部满足"""
        assert self.engine.assert_all(True, True, True)
        assert not self.engine.assert_all(True, False, True)

    def test_assert_any(self):
        """至少一个满足"""
        assert self.engine.assert_any(False, True, False)
        assert not self.engine.assert_any(False, False, False)

    def test_assert_contains(self):
        """包含断言"""
        assert self.engine.assert_contains("hello world", "world")
        assert not self.engine.assert_contains("hello", "world")

    def test_assert_regex(self):
        """正则断言"""
        assert self.engine.assert_regex("VIN: 1HGBH41JXMN109186", r"VIN:\s+\w{17}")
