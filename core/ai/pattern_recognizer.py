"""
模式识别器
===========
从历史执行数据中识别模式，提供智能建议。

功能：
1. 失败预测 - 基于历史模式预测步骤失败概率
2. 参数推荐 - 根据上下文推荐最优参数
3. 执行序列优化 - 识别高效的步骤组合
4. 异常检测 - 发现偏离正常模式的行为
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple
from loguru import logger


@dataclass
class ExecutionSequence:
    """执行序列模式"""
    sequence_id: str
    step_types: List[str]
    success_rate: float = 0.0
    occurrence_count: int = 0
    average_duration: float = 0.0
    context_pattern: Dict[str, Any] = field(default_factory=dict)
    last_seen: str = ""


@dataclass
class ParameterPattern:
    """参数模式"""
    step_type: str
    param_name: str
    param_value: Any
    success_count: int = 0
    failure_count: int = 0
    context_conditions: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FailurePrediction:
    """失败预测结果"""
    step_type: str
    predicted_failure_probability: float
    risk_factors: List[str]
    suggested_mitigations: List[str]
    confidence: float


class PatternRecognizer:
    """模式识别器"""

    def __init__(self):
        # 执行序列模式
        self._sequences: Dict[str, ExecutionSequence] = {}
        
        # 参数模式
        self._param_patterns: Dict[str, List[ParameterPattern]] = defaultdict(list)
        
        # 上下文 → 结果映射
        self._context_outcomes: Dict[str, Dict[str, int]] = defaultdict(lambda: {"success": 0, "failure": 0})
        
        # 步骤类型转换概率
        self._transition_probs: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        
        # 时间相关模式
        self._hourly_patterns: Dict[int, Dict[str, int]] = defaultdict(lambda: {"success": 0, "failure": 0})
        
        # 错误传播链
        self._error_chains: List[List[str]] = []
        
    def learn_from_execution(
        self,
        step_type: str,
        params: Dict[str, Any],
        success: bool,
        duration: float,
        context: Optional[Dict[str, Any]] = None,
        previous_step_type: Optional[str] = None,
        error_type: str = "",
    ) -> None:
        """从单次执行中学习"""
        context = context or {}
        
        # 学习参数模式
        self._learn_parameter_pattern(step_type, params, success, context)
        
        # 学习上下文模式
        context_key = self._build_context_key(context)
        if success:
            self._context_outcomes[context_key]["success"] += 1
        else:
            self._context_outcomes[context_key]["failure"] += 1
        
        # 学习步骤转换
        if previous_step_type:
            self._transition_probs[previous_step_type][step_type] += 1
        
        # 学习时间模式
        hour = datetime.now().hour
        if success:
            self._hourly_patterns[hour]["success"] += 1
        else:
            self._hourly_patterns[hour]["failure"] += 1
        
        # 记录错误链
        if error_type and not success:
            self._record_error_chain(error_type, step_type)
    
    def _learn_parameter_pattern(
        self,
        step_type: str,
        params: Dict[str, Any],
        success: bool,
        context: Dict[str, Any],
    ) -> None:
        """学习参数模式"""
        for param_name, param_value in params.items():
            # 只学习有意义的参数值
            if param_value is None or param_value == "":
                continue
            
            # 创建参数模式
            pattern_key = f"{step_type}:{param_name}:{str(param_value)[:50]}"
            
            # 查找已有模式
            existing = None
            for pattern in self._param_patterns[pattern_key]:
                if (pattern.step_type == step_type and
                    pattern.param_name == param_name and
                    pattern.param_value == param_value):
                    existing = pattern
                    break
            
            if existing is None:
                existing = ParameterPattern(
                    step_type=step_type,
                    param_name=param_name,
                    param_value=param_value,
                    context_conditions=context,
                )
                self._param_patterns[pattern_key].append(existing)
            
            if success:
                existing.success_count += 1
            else:
                existing.failure_count += 1
    
    def _build_context_key(self, context: Dict[str, Any]) -> str:
        """构建上下文键"""
        # 提取关键上下文信息
        key_parts = []
        if "package" in context:
            key_parts.append(f"pkg:{context['package']}")
        if "activity" in context:
            key_parts.append(f"act:{context['activity']}")
        if "device_type" in context:
            key_parts.append(f"dev:{context['device_type']}")
        if "screen_size" in context:
            key_parts.append(f"screen:{context['screen_size']}")
        return "|".join(key_parts) if key_parts else "default"
    
    def _record_error_chain(self, error_type: str, step_type: str) -> None:
        """记录错误链"""
        chain_key = f"{error_type}:{step_type}"
        
        # 查找最近的链
        if self._error_chains:
            last_chain = self._error_chains[-1]
            if len(last_chain) < 5:  # 限制链长度
                last_chain.append(chain_key)
                return
        
        # 开始新链
        self._error_chains.append([chain_key])
        
        # 限制链数量
        if len(self._error_chains) > 100:
            self._error_chains = self._error_chains[-50:]
    
    def predict_failure(
        self,
        step_type: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> FailurePrediction:
        """预测步骤失败概率"""
        context = context or {}
        risk_factors = []
        mitigations = []
        
        # 基础失败率
        base_failure_rate = self._get_base_failure_rate(step_type)
        
        # 上下文调整
        context_key = self._build_context_key(context)
        context_adjustment = self._get_context_adjustment(context_key)
        
        # 参数风险评估
        param_risk, param_mitigations = self._assess_parameter_risk(step_type, params)
        
        # 时间因素
        time_adjustment = self._get_time_adjustment()
        
        # 综合计算
        predicted_probability = min(1.0, max(0.0,
            base_failure_rate + context_adjustment + param_risk + time_adjustment
        ))
        
        risk_factors.extend([
            f"基础失败率: {base_failure_rate:.1%}",
            f"上下文风险: {context_adjustment:+.1%}",
            f"参数风险: {param_risk:+.1%}",
            f"时间因素: {time_adjustment:+.1%}",
        ])
        
        mitigations.extend(param_mitigations)
        mitigations.extend(self._suggest_mitigations(step_type, context))
        
        return FailurePrediction(
            step_type=step_type,
            predicted_failure_probability=predicted_probability,
            risk_factors=risk_factors,
            suggested_mitigations=mitigations[:5],  # 最多5条建议
            confidence=self._calculate_confidence(step_type, context),
        )
    
    def _get_base_failure_rate(self, step_type: str) -> float:
        """获取基础失败率"""
        total = 0
        failures = 0
        
        for pattern_key, patterns in self._param_patterns.items():
            if pattern_key.startswith(f"{step_type}:"):
                for pattern in patterns:
                    total += pattern.success_count + pattern.failure_count
                    failures += pattern.failure_count
        
        return failures / total if total > 0 else 0.2  # 默认20%
    
    def _get_context_adjustment(self, context_key: str) -> float:
        """获取上下文调整值"""
        outcomes = self._context_outcomes.get(context_key)
        if not outcomes:
            return 0.0
        
        total = outcomes["success"] + outcomes["failure"]
        if total == 0:
            return 0.0
        
        failure_rate = outcomes["failure"] / total
        # 上下文失败率高时增加风险
        return (failure_rate - 0.5) * 0.3
    
    def _assess_parameter_risk(
        self,
        step_type: str,
        params: Dict[str, Any],
    ) -> Tuple[float, List[str]]:
        """评估参数风险"""
        risk = 0.0
        mitigations = []
        
        for param_name, param_value in params.items():
            pattern_key = f"{step_type}:{param_name}:{str(param_value)[:50]}"
            patterns = self._param_patterns.get(pattern_key, [])
            
            for pattern in patterns:
                if pattern.param_value == param_value:
                    total = pattern.success_count + pattern.failure_count
                    if total > 0:
                        failure_rate = pattern.failure_count / total
                        risk += failure_rate * 0.1
                        
                        if failure_rate > 0.5:
                            mitigations.append(
                                f"参数 {param_name}={param_value} 历史失败率较高，考虑调整"
                            )
        
        return min(risk, 0.3), mitigations
    
    def _get_time_adjustment(self) -> float:
        """获取时间调整值"""
        hour = datetime.now().hour
        outcomes = self._hourly_patterns.get(hour, {"success": 0, "failure": 0})
        total = outcomes["success"] + outcomes["failure"]
        
        if total < 10:  # 数据不足
            return 0.0
        
        failure_rate = outcomes["failure"] / total
        return (failure_rate - 0.3) * 0.1  # 轻微调整
    
    def _calculate_confidence(self, step_type: str, context: Dict[str, Any]) -> float:
        """计算预测置信度"""
        # 基于数据量计算置信度
        data_points = 0
        
        for pattern_key, patterns in self._param_patterns.items():
            if pattern_key.startswith(f"{step_type}:"):
                for pattern in patterns:
                    data_points += pattern.success_count + pattern.failure_count
        
        context_key = self._build_context_key(context)
        context_outcomes = self._context_outcomes.get(context_key, {"success": 0, "failure": 0})
        data_points += context_outcomes["success"] + context_outcomes["failure"]
        
        # 数据越多置信度越高，最高0.95
        if data_points == 0:
            return 0.3
        elif data_points < 10:
            return 0.5
        elif data_points < 50:
            return 0.7
        elif data_points < 200:
            return 0.85
        else:
            return 0.95
    
    def _suggest_mitigations(self, step_type: str, context: Dict[str, Any]) -> List[str]:
        """建议风险缓解措施"""
        mitigations = []
        
        # 基于步骤类型的通用建议
        if step_type == "ui_tap":
            mitigations.append("确保目标元素可见且可点击")
            mitigations.append("考虑增加等待时间以确保界面加载完成")
        elif step_type == "ui_wait_text":
            mitigations.append("检查文字是否正确，避免错别字")
            mitigations.append("考虑增加超时时间")
        elif step_type == "ui_swipe":
            mitigations.append("确保滑动方向正确")
            mitigations.append("考虑调整滑动距离")
        elif step_type == "app_start":
            mitigations.append("确认应用已安装且包名正确")
            mitigations.append("检查应用是否被禁用或限制")
        
        # 基于上下文的建议
        if context.get("device_type") == "ADB":
            mitigations.append("检查 ADB 连接稳定性")
        
        return mitigations
    
    def recommend_parameters(
        self,
        step_type: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """推荐最优参数"""
        context = context or {}
        recommendations = {}
        
        # 收集该步骤类型的所有参数模式
        param_success_rates: Dict[str, Dict[Any, Tuple[int, int]]] = defaultdict(dict)
        
        for pattern_key, patterns in self._param_patterns.items():
            if not pattern_key.startswith(f"{step_type}:"):
                continue
            
            parts = pattern_key.split(":", 2)
            if len(parts) != 3:
                continue
            
            _, param_name, param_value_str = parts
            
            for pattern in patterns:
                if pattern.param_name != param_name:
                    continue
                
                total = pattern.success_count + pattern.failure_count
                if total > 0:
                    success_rate = pattern.success_count / total
                    param_success_rates[param_name][pattern.param_value] = (
                        success_rate,
                        total,
                    )
        
        # 为每个参数选择最优值
        for param_name, value_rates in param_success_rates.items():
            if not value_rates:
                continue
            
            # 选择成功率最高且样本量足够的值
            best_value = None
            best_rate = 0.0
            best_samples = 0
            
            for value, (rate, samples) in value_rates.items():
                if samples >= 3 and (rate > best_rate or (rate == best_rate and samples > best_samples)):
                    best_value = value
                    best_rate = rate
                    best_samples = samples
            
            if best_value is not None:
                recommendations[param_name] = best_value
        
        return recommendations
    
    def get_optimal_sequence(self, step_types: List[str]) -> List[str]:
        """优化执行序列"""
        if len(step_types) <= 1:
            return step_types
        
        # 基于转换概率排序
        scored_sequences = []
        
        for i in range(len(step_types)):
            for j in range(i + 1, len(step_types)):
                # 计算交换后的得分
                swapped = step_types.copy()
                swapped[i], swapped[j] = swapped[j], swapped[i]
                
                score = self._calculate_sequence_score(swapped)
                scored_sequences.append((score, swapped))
        
        if scored_sequences:
            # 返回得分最高的序列
            scored_sequences.sort(key=lambda x: x[0], reverse=True)
            return scored_sequences[0][1]
        
        return step_types
    
    def _calculate_sequence_score(self, sequence: List[str]) -> float:
        """计算序列得分"""
        if len(sequence) <= 1:
            return 0.0
        
        score = 0.0
        for i in range(len(sequence) - 1):
            current = sequence[i]
            next_step = sequence[i + 1]
            
            # 转换概率
            transitions = self._transition_probs.get(current, {})
            total_transitions = sum(transitions.values())
            
            if total_transitions > 0:
                transition_prob = transitions.get(next_step, 0) / total_transitions
                score += transition_prob
        
        return score
    
    def detect_anomalies(
        self,
        step_type: str,
        duration: float,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """检测异常"""
        anomalies = []
        context = context or {}
        
        # 检测执行时间异常
        expected_duration = self._get_expected_duration(step_type, context)
        if expected_duration > 0:
            deviation = abs(duration - expected_duration) / expected_duration
            if deviation > 0.5:  # 偏差超过50%
                anomalies.append(
                    f"执行时间异常: 预期 {expected_duration:.1f}s, 实际 {duration:.1f}s"
                )
        
        # 检测上下文异常
        context_key = self._build_context_key(context)
        outcomes = self._context_outcomes.get(context_key, {"success": 0, "failure": 0})
        total = outcomes["success"] + outcomes["failure"]
        
        if total > 20:
            failure_rate = outcomes["failure"] / total
            if failure_rate > 0.7:
                anomalies.append(
                    f"当前上下文失败率过高: {failure_rate:.1%}"
                )
        
        return anomalies
    
    def _get_expected_duration(self, step_type: str, context: Dict[str, Any]) -> float:
        """获取预期执行时间"""
        # 从历史数据中学习预期时间
        total_duration = 0.0
        count = 0
        
        for pattern_key, patterns in self._param_patterns.items():
            if pattern_key.startswith(f"{step_type}:"):
                for pattern in patterns:
                    # 这里简化处理，实际应该记录执行时间
                    count += pattern.success_count + pattern.failure_count
        
        # 基于步骤类型的默认预期时间
        default_durations = {
            "ui_tap": 1.0,
            "ui_wait_text": 5.0,
            "ui_swipe": 2.0,
            "app_start": 3.0,
            "wait": 2.0,
            "send_adb": 1.5,
        }
        
        return default_durations.get(step_type, 2.0)
    
    def export_patterns(self) -> Dict[str, Any]:
        """导出识别到的模式"""
        return {
            "sequences": {
                seq_id: {
                    "step_types": seq.step_types,
                    "success_rate": seq.success_rate,
                    "occurrence_count": seq.occurrence_count,
                }
                for seq_id, seq in self._sequences.items()
            },
            "parameter_patterns": {
                key: [
                    {
                        "step_type": p.step_type,
                        "param_name": p.param_name,
                        "param_value": p.param_value,
                        "success_rate": p.success_count / (p.success_count + p.failure_count) if (p.success_count + p.failure_count) > 0 else 0,
                    }
                    for p in patterns
                ]
                for key, patterns in self._param_patterns.items()
            },
            "context_outcomes": dict(self._context_outcomes),
            "transition_probabilities": {
                k: dict(v) for k, v in self._transition_probs.items()
            },
        }


# 全局单例
_recognizer_instance: Optional[PatternRecognizer] = None


def get_pattern_recognizer() -> PatternRecognizer:
    """获取全局模式识别器实例"""
    global _recognizer_instance
    if _recognizer_instance is None:
        _recognizer_instance = PatternRecognizer()
    return _recognizer_instance
