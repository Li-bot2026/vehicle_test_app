"""
测试用例编辑器
===============
提供测试用例的可视化编辑功能（增删改查）。
本模块为纯逻辑层，UI 层在 ui/widgets/test_case_editor.py 中实现。
"""

from typing import List, Optional, Dict
from loguru import logger

from .model import TestCase, TestStep, TestSuite, StepType, Priority, TestStatus


class TestCaseEditor:
    """
    测试用例编辑器（逻辑层）
    -------------------------
    提供用例的增删改查、步骤编辑、批量操作等功能。
    """

    def __init__(self):
        self._current_suite: Optional[TestSuite] = None
        self._undo_stack: List[dict] = []
        self._redo_stack: List[dict] = []

    # ---------- 套件管理 ----------

    def new_suite(self, name: str, description: str = "") -> TestSuite:
        """创建新的测试套件"""
        self._current_suite = TestSuite(name=name, description=description)
        logger.info(f"新建测试套件: {name}")
        return self._current_suite

    def open_suite(self, suite: TestSuite) -> None:
        """打开已有套件"""
        self._current_suite = suite

    @property
    def current_suite(self) -> Optional[TestSuite]:
        return self._current_suite

    # ---------- 用例增删改 ----------

    def add_case(self, case: TestCase) -> bool:
        """添加测试用例到当前套件"""
        if not self._current_suite:
            logger.error("未打开测试套件")
            return False

        # 检查 ID 唯一性
        existing_ids = {c.case_id for c in self._current_suite.test_cases}
        if case.case_id in existing_ids:
            logger.warning(f"用例 ID 已存在: {case.case_id}")
            return False

        self._save_undo_state()
        self._current_suite.add_case(case)
        logger.info(f"添加用例: {case.name}")
        return True

    def create_case(self, name: str, category: str = "",
                    priority: Priority = Priority.P1,
                    description: str = "") -> Optional[TestCase]:
        """快速创建新用例"""
        case = TestCase(
            name=name,
            category=category,
            priority=priority,
            description=description,
        )
        if self.add_case(case):
            return case
        return None

    def remove_case(self, case_id: str) -> bool:
        """删除测试用例"""
        if not self._current_suite:
            return False
        self._save_undo_state()
        result = self._current_suite.remove_case(case_id)
        if result:
            logger.info(f"删除用例: {case_id}")
        return result

    def update_case(self, case_id: str, **kwargs) -> Optional[TestCase]:
        """
        更新用例属性。
        支持: name, description, category, tags, priority, retry_count, timeout 等。
        """
        if not self._current_suite:
            return None

        case = self.get_case(case_id)
        if not case:
            return None

        self._save_undo_state()
        for key, value in kwargs.items():
            if hasattr(case, key):
                setattr(case, key, value)

        from datetime import datetime
        case.updated_at = datetime.now()
        logger.info(f"更新用例: {case_id}")
        return case

    def get_case(self, case_id: str) -> Optional[TestCase]:
        """获取单个用例"""
        if not self._current_suite:
            return None
        for case in self._current_suite.test_cases:
            if case.case_id == case_id:
                return case
        return None

    def list_cases(self, category: str = None,
                   priority: Priority = None,
                   tag: str = None) -> List[TestCase]:
        """列出用例（支持过滤）"""
        if not self._current_suite:
            return []

        cases = self._current_suite.test_cases
        if category:
            cases = [c for c in cases if c.category == category]
        if priority:
            cases = [c for c in cases if c.priority == priority]
        if tag:
            cases = [c for c in cases if tag in c.tags]
        return cases

    def duplicate_case(self, case_id: str) -> Optional[TestCase]:
        """复制用例"""
        original = self.get_case(case_id)
        if not original:
            return None

        new_case = TestCase.from_dict(original.to_dict())
        new_case.name = f"{original.name} (副本)"
        if self.add_case(new_case):
            return new_case
        return None

    # ---------- 步骤编辑 ----------

    def add_step(self, case_id: str, step: TestStep,
                 position: int = -1) -> bool:
        """
        在用例中添加步骤。

        参数:
            position: 插入位置，-1 表示末尾
        """
        case = self.get_case(case_id)
        if not case:
            return False

        self._save_undo_state()
        if position < 0 or position >= len(case.steps):
            case.steps.append(step)
        else:
            case.steps.insert(position, step)
        return True

    def remove_step(self, case_id: str, step_index: int) -> bool:
        """删除步骤"""
        case = self.get_case(case_id)
        if not case or step_index >= len(case.steps):
            return False

        self._save_undo_state()
        case.steps.pop(step_index)
        return True

    def update_step(self, case_id: str, step_index: int,
                    **kwargs) -> Optional[TestStep]:
        """更新步骤属性"""
        case = self.get_case(case_id)
        if not case or step_index >= len(case.steps):
            return None

        step = case.steps[step_index]
        self._save_undo_state()
        for key, value in kwargs.items():
            if hasattr(step, key):
                setattr(step, key, value)
        return step

    def move_step(self, case_id: str, from_idx: int, to_idx: int) -> bool:
        """移动步骤位置"""
        case = self.get_case(case_id)
        if not case:
            return False
        if from_idx >= len(case.steps) or to_idx >= len(case.steps):
            return False

        self._save_undo_state()
        step = case.steps.pop(from_idx)
        case.steps.insert(to_idx, step)
        return True

    # ---------- 批量操作 ----------

    def batch_update_priority(self, case_ids: List[str],
                              priority: Priority) -> int:
        """批量更新优先级"""
        count = 0
        for cid in case_ids:
            case = self.get_case(cid)
            if case:
                case.priority = priority
                count += 1
        return count

    def batch_add_tag(self, case_ids: List[str], tag: str) -> int:
        """批量添加标签"""
        count = 0
        for cid in case_ids:
            case = self.get_case(cid)
            if case and tag not in case.tags:
                case.tags.append(tag)
                count += 1
        return count

    def sort_cases(self, key: str = "name", reverse: bool = False):
        """对用例排序"""
        if not self._current_suite:
            return
        self._current_suite.test_cases.sort(
            key=lambda c: getattr(c, key, ""),
            reverse=reverse
        )

    # ---------- 撤销/重做 ----------

    def _save_undo_state(self):
        """保存当前状态到撤销栈"""
        if self._current_suite:
            self._undo_stack.append(self._current_suite.to_dict())
            self._redo_stack.clear()
            # 限制撤销栈深度
            if len(self._undo_stack) > 50:
                self._undo_stack.pop(0)

    def undo(self) -> bool:
        """撤销上一步操作"""
        if not self._undo_stack:
            return False

        self._redo_stack.append(self._current_suite.to_dict())
        state = self._undo_stack.pop()
        self._current_suite = TestSuite.from_dict(state)
        return True

    def redo(self) -> bool:
        """重做"""
        if not self._redo_stack:
            return False

        self._undo_stack.append(self._current_suite.to_dict())
        state = self._redo_stack.pop()
        self._current_suite = TestSuite.from_dict(state)
        return True
