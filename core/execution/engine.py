"""
测试执行引擎
=============
负责测试用例的调度与执行，支持单条执行、批量执行、定时执行。

设计说明：
- ExecutionEngine 是执行的核心调度器
- 支持暂停/继续/中止/跳过控制
- 支持失败自动重试
- 支持参数化测试（{param} 占位符替换 + parameter_sets 数据驱动）
- 通过 Qt 信号实时广播执行进度
- 线程安全，执行在独立工作线程中进行
"""

import os
import re
import time
import copy
import threading
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Callable, Any
from datetime import datetime
from dataclasses import dataclass, field

from loguru import logger
from PyQt6.QtCore import QObject, pyqtSignal

from ..testcase.model import (
    TestCase, TestStep, TestSuite, TestStatus, StepType
)
from ..device.manager import DeviceManager


class ExecutionState(Enum):
    """执行状态"""
    IDLE = "idle"               # 空闲
    RUNNING = "running"         # 执行中
    PAUSED = "paused"           # 已暂停
    STOPPING = "stopping"       # 正在停止
    FINISHED = "finished"       # 已完成


@dataclass
class ExecutionProgress:
    """执行进度信息"""
    total: int = 0              # 总用例数
    current: int = 0            # 当前执行到第几个
    passed: int = 0             # 已通过
    failed: int = 0             # 已失败
    error: int = 0              # 错误
    skipped: int = 0            # 跳过
    aborted: int = 0            # 中止
    current_case_name: str = "" # 当前用例名称
    elapsed_time: float = 0.0   # 已用时间
    estimated_remaining: float = 0.0  # 预计剩余时间


class ExecutionSignals(QObject):
    """执行引擎的 Qt 信号"""
    # 执行开始
    execution_started = pyqtSignal(str)          # suite_name
    # 执行结束
    execution_finished = pyqtSignal(str, float)  # suite_name, total_duration
    # 进度更新
    progress_updated = pyqtSignal(object)         # ExecutionProgress
    # 用例开始
    case_started = pyqtSignal(str, str)           # case_id, case_name
    # 用例结束
    case_finished = pyqtSignal(str, str, str, float)  # case_id, case_name, status, duration
    # 用例失败（供 AI 诊断）
    case_failed = pyqtSignal(str, str, object)    # case_id, case_name, failure_info
    # 步骤开始
    step_started = pyqtSignal(str, str, int)      # case_id, step_desc, step_index
    # 步骤结束
    step_finished = pyqtSignal(str, str, str, float)  # case_id, step_desc, status, duration
    # 状态变化
    state_changed = pyqtSignal(str)               # state
    # 日志输出
    log_output = pyqtSignal(str, str)             # level, message


class ExecutionEngine:
    """
    测试执行引擎
    -------------
    核心调度器，管理测试的执行流程。
    """

    def __init__(self, device_manager: DeviceManager):
        self.signals = ExecutionSignals()
        self._device_manager = device_manager

        # 执行状态
        self._state = ExecutionState.IDLE
        self._pause_event = threading.Event()
        self._pause_event.set()  # 初始为非暂停状态
        self._stop_event = threading.Event()
        self._skip_event = threading.Event()

        # 执行线程
        self._exec_thread: Optional[threading.Thread] = None
        # 定时任务线程（与执行线程分开，避免开始执行时 join 到自己）
        self._schedule_thread: Optional[threading.Thread] = None

        # 当前执行上下文
        self._current_suite: Optional[TestSuite] = None
        self._current_case: Optional[TestCase] = None
        self._progress = ExecutionProgress()

        # 执行配置
        self._retry_count: int = 2
        self._retry_delay: float = 1.0
        self._batch_interval: float = 0.5
        self._stop_on_failure: bool = False

        # 步骤执行器注册表
        self._step_handlers: Dict[StepType, Callable] = {}
        self._register_default_handlers()

    @property
    def state(self) -> ExecutionState:
        return self._state

    @property
    def progress(self) -> ExecutionProgress:
        return self._progress

    @property
    def is_running(self) -> bool:
        return self._state in (
            ExecutionState.RUNNING, ExecutionState.PAUSED,
            ExecutionState.STOPPING,
        )

    def checkpoint(self) -> bool:
        """
        供长耗时步骤（等待、滑动直到出现、自主任务等）调用：
        - 暂停时在此阻塞，直到继续；
        - 返回 True 表示已请求停止，步骤应尽快中止。
        """
        self._pause_event.wait()
        return self._stop_event.is_set()

    # ---------- 执行控制 ----------

    def execute_single(self, case: TestCase) -> None:
        """单条执行"""
        suite = TestSuite(name="单条执行")
        suite.add_case(case)
        self._start_execution(suite)

    def execute_batch(self, suite: TestSuite) -> None:
        """批量执行"""
        self._start_execution(suite)

    def execute_scheduled(self, suite: TestSuite,
                          run_time: datetime) -> None:
        """定时执行：
        - 时间到后自动开始执行
        - 执行前可用 stop() 取消（stop 会同时结束等待中的定时任务）
        """
        def delayed_start():
            try:
                while datetime.now() < run_time:
                    if self._stop_event.is_set():
                        logger.info("定时执行已取消")
                        return
                    time.sleep(0.5)
                self._start_execution(suite)
            except Exception as e:
                logger.error(f"定时执行失败: {e}")

        # 允许 stop() 在等待阶段取消定时任务
        self._stop_event.clear()
        self._schedule_thread = threading.Thread(
            target=delayed_start, daemon=True, name="ScheduledExec"
        )
        self._schedule_thread.start()
        logger.info(f"定时执行已设置: {run_time}")

    def pause(self) -> None:
        """暂停执行"""
        if self._state == ExecutionState.RUNNING:
            self._state = ExecutionState.PAUSED
            self._pause_event.clear()
            self.signals.state_changed.emit(ExecutionState.PAUSED.value)
            logger.info("执行已暂停")

    def resume(self) -> None:
        """继续执行"""
        if self._state == ExecutionState.PAUSED:
            self._state = ExecutionState.RUNNING
            self._pause_event.set()
            self.signals.state_changed.emit(ExecutionState.RUNNING.value)
            logger.info("执行已继续")

    def stop(self) -> None:
        """中止执行（仅在运行/暂停状态下生效）；有等待中的定时任务时一并取消"""
        pending_schedule = (
            self._schedule_thread is not None
            and self._schedule_thread.is_alive()
        )
        if self._state not in (ExecutionState.RUNNING,
                               ExecutionState.PAUSED):
            if pending_schedule:
                self._stop_event.set()
                logger.info("已取消等待中的定时执行")
                self.signals.log_output.emit("INFO", "已取消定时执行")
            return
        self._stop_event.set()
        self._pause_event.set()  # 解除暂停阻塞
        self._state = ExecutionState.STOPPING
        self.signals.state_changed.emit(ExecutionState.STOPPING.value)
        logger.info("正在停止执行...")

    def skip_current(self) -> None:
        """跳过当前用例"""
        if self._current_case:
            self._skip_event.set()
            logger.info(f"跳过用例: {self._current_case.name}")

    # ---------- 配置 ----------

    def set_config(self, retry_count: int = None, retry_delay: float = None,
                   batch_interval: float = None, stop_on_failure: bool = None):
        """设置执行参数"""
        if retry_count is not None:
            self._retry_count = retry_count
        if retry_delay is not None:
            self._retry_delay = retry_delay
        if batch_interval is not None:
            self._batch_interval = batch_interval
        if stop_on_failure is not None:
            self._stop_on_failure = stop_on_failure

    # ---------- 步骤处理器注册 ----------

    def register_step_handler(self, step_type: StepType,
                               handler: Callable) -> None:
        """注册自定义步骤处理器"""
        self._step_handlers[step_type] = handler

    def _register_default_handlers(self):
        """注册默认步骤处理器（由 runner 模块补充实现）"""
        # 这里注册空处理器，实际处理逻辑在 TestRunner 中
        pass

    # ---------- 内部执行逻辑 ----------

    def _start_execution(self, suite: TestSuite) -> None:
        """启动执行（在工作线程中）"""
        if self.is_running:
            logger.warning("已有执行任务在运行")
            return
        if self._exec_thread and self._exec_thread is not threading.current_thread() \
                and self._exec_thread.is_alive():
            self._exec_thread.join(timeout=2.0)
            if self._exec_thread.is_alive():
                logger.warning("上一次执行线程尚未退出，拒绝启动新任务")
                return

        self._current_suite = suite
        self._stop_event.clear()
        self._skip_event.clear()
        self._pause_event.set()
        self._state = ExecutionState.RUNNING

        self._exec_thread = threading.Thread(
            target=self._execution_loop,
            args=(suite,),
            daemon=True,
            name="TestExecution"
        )
        self._exec_thread.start()

    def _execution_loop(self, suite: TestSuite):
        """主执行循环"""
        start_time = time.time()
        suite_name = suite.name

        self.signals.execution_started.emit(suite_name)
        self.signals.state_changed.emit(ExecutionState.RUNNING.value)

        # 预展开参数化用例，使进度和统计正确计数
        expanded_suite_cases = []
        for case in suite.test_cases:
            expanded = self._expand_parameterized_case(case)
            if len(expanded) > 1:
                logger.info(f"参数化用例: {case.name} ({len(expanded)} 组参数)")
            expanded_suite_cases.extend(expanded)

        # 初始化进度（使用展开后的用例数）
        self._progress = ExecutionProgress(total=len(expanded_suite_cases))

        logger.info(f"开始执行测试套件: {suite_name} ({len(expanded_suite_cases)} 个用例)")

        # 执行套件级 setup（失败则跳过全部用例）
        setup_ok = True
        if suite.setup_steps:
            setup_ok = self._execute_steps(suite.setup_steps, "套件前置")
            if not setup_ok:
                self.signals.log_output.emit(
                    "ERROR", "套件前置条件失败，跳过全部用例"
                )

        # 逐个执行用例（已预展开）
        for i, case in enumerate(expanded_suite_cases):
            if not setup_ok:
                case.status = TestStatus.SKIPPED
                self._progress.skipped += 1
                continue

            # 检查停止信号
            if self._stop_event.is_set():
                case.status = TestStatus.ABORTED
                self._progress.aborted += 1
                continue

            # 检查暂停
            self._pause_event.wait()

            # 更新进度
            self._progress.current = i + 1
            self._progress.current_case_name = case.name
            self._progress.elapsed_time = time.time() - start_time
            if i > 0:
                avg_time = self._progress.elapsed_time / i
                self._progress.estimated_remaining = avg_time * (len(expanded_suite_cases) - i)
            self.signals.progress_updated.emit(self._progress)

            # 执行用例（已展开，直接执行单个）
            self._skip_event.clear()
            self._execute_single_case(case)

            # 更新统计
            if case.status == TestStatus.PASSED:
                self._progress.passed += 1
            elif case.status == TestStatus.FAILED:
                self._progress.failed += 1
                if self._stop_on_failure:
                    logger.info(f"失败停止: {case.name}")
                    # 标记后续用例为跳过
                    for remaining in expanded_suite_cases[i + 1:]:
                        remaining.status = TestStatus.SKIPPED
                        self._progress.skipped += 1
                    break
            elif case.status == TestStatus.ERROR:
                self._progress.error += 1
            elif case.status == TestStatus.SKIPPED:
                self._progress.skipped += 1
            elif case.status == TestStatus.ABORTED:
                self._progress.aborted += 1

            # 批量间隔
            if i < len(expanded_suite_cases) - 1:
                time.sleep(self._batch_interval)

        # 执行套件级 teardown
        if suite.teardown_steps:
            self._execute_steps(suite.teardown_steps, "套件后置")

        # 计算最终统计
        total_duration = time.time() - start_time
        suite.calculate_stats()

        # 发送完成信号
        self._state = ExecutionState.FINISHED
        self.signals.execution_finished.emit(suite_name, total_duration)
        self.signals.state_changed.emit(ExecutionState.FINISHED.value)

        logger.info(
            f"执行完成: {suite_name} | "
            f"通过: {self._progress.passed} | "
            f"失败: {self._progress.failed} | "
            f"错误: {self._progress.error} | "
            f"耗时: {total_duration:.1f}s"
        )

    def _execute_case(self, case: TestCase):
        """执行单个测试用例（支持参数化展开）"""
        # 检查是否有参数集，如果有则展开为多个用例执行
        expanded_cases = self._expand_parameterized_case(case)

        if len(expanded_cases) > 1:
            # 参数化用例：逐个执行展开后的用例
            logger.info(f"参数化用例: {case.name} ({len(expanded_cases)} 组参数)")
            for expanded_case in expanded_cases:
                self._execute_single_case(expanded_case)
        else:
            # 普通用例：直接执行
            self._execute_single_case(expanded_cases[0])

    def _execute_single_case(self, case: TestCase):
        """执行单个测试用例（内部方法）"""
        self._current_case = case
        case.reset()
        case.start_time = datetime.now()
        case.status = TestStatus.RUNNING

        # 预连接用例引用的所有设备，避免发送帧时接收端尚未上线
        self._preconnect_devices(case)

        self.signals.case_started.emit(case.case_id, case.name)
        self.signals.log_output.emit("INFO", f"开始执行: {case.name}")

        case_start = time.time()
        retry = 0
        # retry_count 为 None 时沿用全局配置；显式 0 表示不重试
        max_retries = (self._retry_count if case.retry_count is None
                       else max(0, int(case.retry_count)))

        while retry <= max_retries:
            if self._stop_event.is_set():
                case.status = TestStatus.ABORTED
                break

            if retry > 0:
                logger.info(f"重试 ({retry}/{max_retries}): {case.name}")
                case.reset()
                # 分片等待重试间隔，以便及时响应停止
                waited = 0.0
                while waited < self._retry_delay and not self._stop_event.is_set():
                    time.sleep(min(0.2, self._retry_delay - waited))
                    waited += 0.2
                if self._stop_event.is_set():
                    case.status = TestStatus.ABORTED
                    break

            if self._skip_event.is_set():
                case.status = TestStatus.SKIPPED
                break

            try:
                # 1. 执行 setup
                if case.setup_steps:
                    setup_ok = self._execute_steps(
                        case.setup_steps, f"{case.name} 前置", case=case)
                    if self._stop_event.is_set():
                        case.status = TestStatus.ABORTED
                        break
                    if not setup_ok:
                        case.status = TestStatus.ERROR
                        case.error_message = "前置条件执行失败"
                        break

                # 2. 执行主步骤
                all_passed = True
                for step_idx, step in enumerate(case.steps):
                    # 检查停止/暂停/跳过
                    if self._stop_event.is_set():
                        case.status = TestStatus.ABORTED
                        break
                    if self._skip_event.is_set():
                        case.status = TestStatus.SKIPPED
                        break
                    self._pause_event.wait()

                    step_ok = self._execute_step(
                        case.case_id, step, step_idx, case=case
                    )
                    if not step_ok:
                        all_passed = False
                        break

                # 中止/跳过时不再执行后置条件，直接结束用例
                if case.status in (TestStatus.ABORTED, TestStatus.SKIPPED):
                    break

                # 3. 执行 teardown（无论主步骤是否通过都执行）
                if case.teardown_steps:
                    self._execute_steps(
                        case.teardown_steps, f"{case.name} 后置", case=case)

                # 4. 判定结果
                if all_passed:
                    case.status = TestStatus.PASSED
                    break  # 通过则不重试
                else:
                    case.status = TestStatus.FAILED

            except Exception as e:
                case.status = TestStatus.ERROR
                case.error_message = str(e)
                logger.error(f"用例执行异常: {case.name} - {e}")
                break

            retry += 1

        case.end_time = datetime.now()
        case.duration = time.time() - case_start

        self.signals.case_finished.emit(
            case.case_id, case.name, case.status.value, case.duration
        )

        if case.status in (TestStatus.FAILED, TestStatus.ERROR):
            failed, section = self._find_failed_step(case)
            section_steps = getattr(case, section)
            step_index = -1
            if failed is not None:
                # 按对象身份定位，避免重复步骤命中第一个同值项
                step_index = next(
                    (i for i, s in enumerate(section_steps) if s is failed), -1
                )
            # 错误点：写到用例错误信息里，报告/列表都能直接看到是哪一步
            if failed is not None and not case.error_message:
                if section == "setup_steps":
                    label = f"前置第 {step_index + 1} 步"
                elif section == "teardown_steps":
                    label = f"后置第 {step_index + 1} 步"
                else:
                    label = f"第 {step_index + 1} 步"
                reason = failed.error_message or "步骤执行失败"
                case.error_message = (
                    f"{label}「{failed.description}」（{failed.step_type.value}）失败：{reason}"
                )
            self.signals.case_failed.emit(case.case_id, case.name, {
                "case": case,
                "step": failed,
                "step_section": section,
                "step_index": step_index,
                "step_description": failed.description if failed else "（未定位到具体步骤）",
                "step_type": failed.step_type.value if failed else "",
                "error": (failed.error_message if failed and failed.error_message
                          else case.error_message or "步骤执行失败"),
            })

        self.signals.log_output.emit(
            "INFO",
            f"{'✓' if case.is_passed else '✗'} {case.name} - "
            f"{case.status.value} ({case.duration:.2f}s)"
        )

        self._current_case = None

    @staticmethod
    def _find_failed_step(case: TestCase):
        """定位第一个失败/异常的步骤，返回 (步骤, 所属列表名)"""
        sections = (
            ("setup_steps", case.setup_steps),
            ("steps", case.steps),
            ("teardown_steps", case.teardown_steps),
        )
        for section_name, steps in sections:
            for step in steps:
                if step.status in (TestStatus.FAILED, TestStatus.ERROR):
                    return step, section_name
        return None, "steps"

    def _preconnect_devices(self, case: TestCase) -> None:
        """预连接用例引用的所有设备（发送/接收端需同时在线）"""
        device_ids = set()
        for steps in (case.setup_steps, case.steps, case.teardown_steps):
            for step in steps:
                if step.device_id:
                    device_ids.add(step.device_id)
        for device_id in device_ids:
            self._device_manager.auto_connect(device_id)

    @staticmethod
    def _substitute_params(obj: Any, params: Dict[str, str]) -> Any:
        """
        递归替换对象中的 {param} 占位符。
        支持 str、dict、list、tuple 类型。
        """
        if not params:
            return obj
        if isinstance(obj, str):
            result = obj
            for key, value in params.items():
                result = result.replace(f"{{{key}}}", str(value))
            return result
        elif isinstance(obj, dict):
            return {k: ExecutionEngine._substitute_params(v, params) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            replaced = [ExecutionEngine._substitute_params(item, params) for item in obj]
            return type(obj)(replaced)
        return obj

    def _expand_parameterized_case(self, case: TestCase) -> List[TestCase]:
        """
        展开参数化用例：如果有 parameter_sets，为每组参数生成一个用例副本。
        如果没有 parameter_sets 但有 parameters，用 parameters 作为默认值生成一个用例。
        命名规则与 UI 展开（parser._expand_parameters）保持一致。
        """
        if not case.parameter_sets and not case.parameters:
            return [case]

        cases = []
        sets = case.parameter_sets if case.parameter_sets else [case.parameters]

        for i, param_set in enumerate(sets):
            # 深拷贝用例
            case_copy = TestCase.from_dict(case.to_dict())
            # 每个展开副本使用唯一 ID
            case_copy.case_id = f"{case.case_id}_p{i+1}"
            case_copy.parent_id = case.case_id
            # 合并默认参数和当前参数集
            merged_params = {**case.parameters, **param_set}
            # 替换所有步骤中的占位符
            for step in case_copy.steps + case_copy.setup_steps + case_copy.teardown_steps:
                step.description = self._substitute_params(step.description, merged_params)
                step.params = self._substitute_params(step.params, merged_params)
                if step.expected is not None:
                    step.expected = self._substitute_params(step.expected, merged_params)
            # 替换用例名称和描述中的占位符
            case_copy.name = self._substitute_params(case_copy.name, merged_params)
            case_copy.description = self._substitute_params(case_copy.description, merged_params)
            # 多组参数时使用与 UI 一致的命名（优先 case_id + description）
            if len(sets) > 1:
                cid = param_set.get("case_id", "")
                desc = param_set.get("description", "")
                kw = param_set.get("keyword", "")
                if cid and desc:
                    case_copy.name = f"[{cid}] {desc}"
                elif cid:
                    case_copy.name = f"[{cid}] {kw or cid}"
                elif kw:
                    case_copy.name = f"{case.name} - {kw}"
                else:
                    case_copy.name = f"{case.name} [{i+1}]"
            # 清除 parameter_sets 避免二次展开
            case_copy.parameter_sets = []
            case_copy.parameters = merged_params
            cases.append(case_copy)

        return cases

    def _execute_step(self, case_id: str, step: TestStep,
                      step_idx: int, case: TestCase = None) -> bool:
        """执行单个步骤"""
        step.status = TestStatus.RUNNING
        step.timestamp = datetime.now()

        self.signals.step_started.emit(case_id, step.description, step_idx)

        step_start = time.time()
        try:
            # 查找步骤处理器
            handler = self._step_handlers.get(step.step_type)
            if handler:
                result = handler(step)
                step.status = TestStatus.PASSED if result else TestStatus.FAILED
            else:
                # 默认处理：等待类型直接通过
                if step.step_type == StepType.WAIT:
                    wait_time = step.params.get("duration", 1.0)
                    time.sleep(float(wait_time))
                    step.status = TestStatus.PASSED
                else:
                    step.status = TestStatus.ERROR
                    step.error_message = f"未注册的步骤类型: {step.step_type}"

        except Exception as e:
            step.status = TestStatus.ERROR
            step.error_message = str(e)
            logger.error(f"步骤执行异常: {step.description} - {e}")

        step.duration = time.time() - step_start

        # 截图步骤：把产物记到用例上，供报告引用
        if case is not None and step.step_type is StepType.SCREENSHOT:
            path = str(step.params.get("save_path") or "").strip()
            if path and os.path.exists(path):
                if path not in case.screenshots:
                    case.screenshots.append(path)
                if path not in step.attachments:
                    step.attachments.append(path)

        # 步骤失败：自动截一张现场图，报告里直接看到错误点
        if (case is not None and step.status in (TestStatus.FAILED,
                                                 TestStatus.ERROR)):
            shot = self._capture_failure_screenshot(case, step)
            if shot:
                if shot not in case.screenshots:
                    case.screenshots.append(shot)
                if shot not in step.attachments:
                    step.attachments.append(shot)

        # 记录执行结果到经验学习器
        try:
            from ..ai.experience_learner import get_experience_learner
            learner = get_experience_learner()
            learner.record_execution(
                case_name=case.name if case else "",
                case_category=case.category if case else "",
                step_type=step.step_type.value,
                step_description=step.description,
                success=(step.status == TestStatus.PASSED),
                duration=step.duration,
                error_type=step.status.value if step.status != TestStatus.PASSED else "",
                error_message=step.error_message or "",
                params=step.params,
                context={"device_id": step.device_id},
            )
        except Exception as e:
            logger.debug(f"记录执行经验失败: {e}")

        self.signals.step_finished.emit(
            case_id, step.description, step.status.value, step.duration
        )

        return step.status == TestStatus.PASSED

    def _capture_failure_screenshot(self, case: TestCase,
                                    step: TestStep) -> str:
        """失败时截取设备现场图，返回保存路径；失败返回空串"""
        try:
            device = None
            if step.device_id:
                device = self._device_manager.get_device(step.device_id)
            if device is None:
                for dev in self._device_manager.get_all_devices().values():
                    from core.device.adb_device import ADBDevice as _ADB
                    if isinstance(dev, _ADB):
                        device = dev
                        break
            if device is None or not hasattr(device, "take_screenshot"):
                return ""
            out_dir = Path("screenshots")
            out_dir.mkdir(parents=True, exist_ok=True)
            safe_name = "".join(
                ch if ch.isalnum() or ch in "-_" else "_"
                for ch in (case.name or "case")
            )[:20]
            path = out_dir / (
                f"fail_{safe_name}_{step.step_id}.png"
            )
            if device.take_screenshot(str(path)):
                logger.info(f"失败现场截图已保存: {path}")
                return str(path)
        except Exception as e:
            logger.debug(f"失败截图失败: {e}")
        return ""

    def _execute_steps(self, steps: List[TestStep], context: str,
                       case: TestCase = None) -> bool:
        """执行步骤列表，返回是否全部通过"""
        for idx, step in enumerate(steps):
            if self._stop_event.is_set():
                step.status = TestStatus.SKIPPED
                return False
            if not self._execute_step("__system__", step, idx, case=case):
                logger.error(f"{context} 步骤失败: {step.description}")
                return False
        return True
