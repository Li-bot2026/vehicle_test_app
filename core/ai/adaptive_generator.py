"""
自适应测试生成器
================
基于历史执行经验和学习到的模式，智能生成更可靠的测试用例。

特性：
1. 参数优化 - 使用历史最优参数
2. 超时调整 - 基于实际执行时间动态调整
3. 风险规避 - 避免高失败率的步骤组合
4. 执行序列优化 - 按最优顺序排列步骤
"""

from typing import Any, Dict, List, Optional, Tuple
from loguru import logger

from ..testcase.model import TestCase, TestStep, StepType, Priority
from .experience_learner import get_experience_learner
from .pattern_recognizer import get_pattern_recognizer


class AdaptiveTestGenerator:
    """自适应测试生成器"""

    def __init__(self):
        self._learner = get_experience_learner()
        self._recognizer = get_pattern_recognizer()

    def optimize_test_case(self, case: TestCase) -> TestCase:
        """
        优化测试用例
        - 调整超时时间
        - 优化参数
        - 重新排列步骤顺序
        """
        optimized_steps = []
        
        for step in case.steps:
            optimized_step = self._optimize_step(step)
            optimized_steps.append(optimized_step)
        
        # 优化步骤顺序
        step_types = [s.step_type.value for s in optimized_steps]
        optimal_order = self._recognizer.get_optimal_sequence(step_types)
        
        # 按最优顺序重排（如果与当前不同）
        if optimal_order != step_types:
            optimized_steps = self._reorder_steps(optimized_steps, optimal_order)
        
        case.steps = optimized_steps
        return case

    def _optimize_step(self, step: TestStep) -> TestStep:
        """优化单个步骤"""
        optimized_params = dict(step.params)
        
        # 1. 基于历史数据优化参数
        recommended_params = self._recognizer.recommend_parameters(
            step.step_type.value
        )
        
        for param_name, recommended_value in recommended_params.items():
            if param_name not in optimized_params:
                optimized_params[param_name] = recommended_value
                logger.debug(
                    f"步骤 {step.description} 参数 {param_name} "
                    f"使用推荐值: {recommended_value}"
                )
        
        # 2. 优化超时时间
        optimized_timeout = step.timeout
        if step.timeout == 30.0:  # 默认超时
            suggested_timeout = self._learner.suggest_timeout(
                step.step_type.value,
                default=30.0
            )
            if suggested_timeout != 30.0:
                optimized_timeout = suggested_timeout
                logger.debug(
                    f"步骤 {step.description} 超时调整为 {suggested_timeout:.1f}s"
                )
        
        # 3. 创建优化后的步骤
        optimized_step = TestStep(
            step_id=step.step_id,
            step_type=step.step_type,
            description=step.description,
            device_id=step.device_id,
            params=optimized_params,
            expected=step.expected,
            tolerance=step.tolerance,
            timeout=optimized_timeout,
        )
        
        return optimized_step

    def _reorder_steps(
        self,
        steps: List[TestStep],
        optimal_order: List[str]
    ) -> List[TestStep]:
        """按最优顺序重排步骤"""
        # 创建步骤类型到步骤的映射
        type_to_steps: Dict[str, List[TestStep]] = {}
        for step in steps:
            step_type = step.step_type.value
            if step_type not in type_to_steps:
                type_to_steps[step_type] = []
            type_to_steps[step_type].append(step)
        
        # 按最优顺序重组步骤
        reordered = []
        for step_type in optimal_order:
            if step_type in type_to_steps and type_to_steps[step_type]:
                reordered.append(type_to_steps[step_type].pop(0))
        
        # 添加剩余步骤（保持原顺序）
        for step_list in type_to_steps.values():
            reordered.extend(step_list)
        
        return reordered

    def generate_with_learning(
        self,
        base_case: TestCase,
        context: Optional[Dict[str, Any]] = None
    ) -> TestCase:
        """
        基于学习生成测试用例
        - 预测失败风险
        - 应用缓解措施
        - 优化参数和超时
        """
        context = context or {}
        
        # 复制基础用例
        case = TestCase(
            case_id=base_case.case_id,
            name=base_case.name,
            description=base_case.description,
            category=base_case.category,
            tags=list(base_case.tags),
            priority=base_case.priority,
            steps=[],
            timeout=base_case.timeout,
        )
        
        # 优化每个步骤
        for step in base_case.steps:
            # 预测失败风险
            prediction = self._recognizer.predict_failure(
                step.step_type.value,
                step.params,
                context
            )
            
            # 如果风险过高，应用缓解措施
            if prediction.predicted_failure_probability > 0.6:
                logger.info(
                    f"步骤 {step.description} 预测失败率 "
                    f"{prediction.predicted_failure_probability:.1%}，"
                    f"应用缓解措施"
                )
                step = self._apply_mitigations(step, prediction)
            
            # 检测异常
            anomalies = self._recognizer.detect_anomalies(
                step.step_type.value,
                step.timeout,
                context
            )
            
            if anomalies:
                logger.warning(f"步骤 {step.description} 检测到异常: {anomalies}")
            
            case.steps.append(step)
        
        # 优化整体顺序
        case = self.optimize_test_case(case)
        
        return case

    def _apply_mitigations(
        self,
        step: TestStep,
        prediction
    ) -> TestStep:
        """应用风险缓解措施"""
        mitigated_params = dict(step.params)
        mitigated_timeout = step.timeout
        
        # 基于建议调整参数
        for mitigation in prediction.suggested_mitigations:
            if "超时" in mitigation or "等待" in mitigation:
                # 增加超时时间
                mitigated_timeout = min(step.timeout * 1.5, 300.0)
            elif "参数" in mitigation:
                # 使用推荐参数
                recommended = self._recognizer.recommend_parameters(
                    step.step_type.value
                )
                mitigated_params.update(recommended)
        
        return TestStep(
            step_id=step.step_id,
            step_type=step.step_type,
            description=step.description + " [已优化]",
            device_id=step.device_id,
            params=mitigated_params,
            expected=step.expected,
            tolerance=step.tolerance,
            timeout=mitigated_timeout,
        )

    def suggest_improvements(self, case: TestCase) -> List[str]:
        """基于学习数据建议改进"""
        suggestions = []
        
        # 分析各步骤
        for step in case.steps:
            success_rate = self._learner.get_success_rate(step.step_type.value)
            
            if success_rate < 0.7:
                suggestions.append(
                    f"步骤 '{step.description}' 的历史成功率较低 "
                    f"({success_rate:.1%})，建议增加重试或调整参数"
                )
            
            # 检查超时是否合理
            suggested_timeout = self._learner.suggest_timeout(
                step.step_type.value,
                default=step.timeout
            )
            if abs(suggested_timeout - step.timeout) > 5.0:
                suggestions.append(
                    f"步骤 '{step.description}' 的超时时间建议调整为 "
                    f"{suggested_timeout:.1f}s（当前 {step.timeout:.1f}s）"
                )
        
        # 检查整体成功率
        category = case.category
        category_stats = self._learner.get_category_performance()
        if category in category_stats:
            stats = category_stats[category]
            if stats["success_rate"] < 0.6:
                suggestions.append(
                    f"测试类别 '{category}' 的整体成功率较低 "
                    f"({stats['success_rate']:.1%})，建议审查测试设计"
                )
        
        return suggestions

    def generate_learning_report(self) -> str:
        """生成学习报告"""
        return self._learner.generate_learning_report()

    def export_knowledge(self) -> Dict[str, Any]:
        """导出学习到的知识"""
        return {
            "experience": self._learner.export_knowledge(),
            "patterns": self._recognizer.export_patterns(),
        }


# 全局单例
_generator_instance: Optional[AdaptiveTestGenerator] = None


def get_adaptive_generator() -> AdaptiveTestGenerator:
    """获取全局自适应生成器实例"""
    global _generator_instance
    if _generator_instance is None:
        _generator_instance = AdaptiveTestGenerator()
    return _generator_instance
