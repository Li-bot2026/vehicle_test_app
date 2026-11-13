"""
语义学习器
===========
使用 LLM 理解测试执行的语义，实现更深层次的学习。

能力：
1. 理解步骤语义 - 不只是记录"ui_tap"，而是理解"点击开始按钮"的含义
2. 上下文推理 - 理解为什么某个步骤会失败
3. 知识提取 - 从成功/失败中提取可复用的知识
4. 智能建议 - 基于语义理解给出改进建议
"""

import json
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from loguru import logger

from .client import AIClient, AIError


@dataclass
class SemanticInsight:
    """语义洞察"""
    step_description: str
    step_type: str
    success: bool
    semantic_meaning: str  # 语义理解（如"启动应用并等待主界面加载"）
    failure_reason: str = ""  # 失败原因分析
    reusable_knowledge: str = ""  # 可复用知识
    confidence: float = 0.0  # 置信度


@dataclass
class ContextualPattern:
    """上下文模式"""
    scenario: str  # 场景描述（如"游戏应用启动流程"）
    step_sequence: List[str]  # 步骤序列
    success_rate: float = 0.0
    common_pitfalls: List[str] = field(default_factory=list)  # 常见陷阱
    optimization_suggestions: List[str] = field(default_factory=list)  # 优化建议


class SemanticLearner:
    """语义学习器"""

    def __init__(self, ai_client: AIClient):
        self._client = ai_client
        self._insights: List[SemanticInsight] = []
        self._patterns: Dict[str, ContextualPattern] = {}
        
        # 提示词模板
        self._analyze_prompt = """你是一个测试自动化专家。请分析以下测试步骤的执行结果，并提供深层次的语义理解。

测试步骤：
- 描述: {description}
- 类型: {step_type}
- 参数: {params}
- 执行结果: {success_text}
- 错误信息: {error}

上下文信息：
- 应用包名: {package}
- 设备类型: {device_type}
- 前一个步骤: {previous_step}

请分析并返回 JSON 格式：
{{
  "semantic_meaning": "这个步骤的实际含义（用自然语言描述）",
  "failure_analysis": "如果失败，分析根本原因（成功时留空）",
  "reusable_knowledge": "从这次执行中可以学到的通用知识",
  "context_insight": "对当前场景的理解（如这是什么类型的应用流程）",
  "confidence": 0.85
}}"""

        self._pattern_prompt = """你是一个测试优化专家。基于以下多个测试执行记录，识别出通用的模式和改进建议。

执行记录：
{records}

请分析并返回 JSON 格式：
{{
  "scenario": "识别出的测试场景（如'游戏应用启动和主界面操作'）",
  "common_pattern": "常见的步骤模式",
  "success_factors": "成功的关键因素",
  "failure_patterns": "常见的失败模式",
  "optimization_suggestions": ["优化建议1", "优化建议2"],
  "reusable_template": "可复用的测试模板描述"
}}"""

    def analyze_execution(
        self,
        step_description: str,
        step_type: str,
        params: Dict[str, Any],
        success: bool,
        error: str = "",
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[SemanticInsight]:
        """
        分析单次执行的语义
        
        使用 LLM 理解步骤的实际含义，而不仅仅是记录成功/失败
        """
        if not self._client.configured:
            logger.debug("AI 客户端未配置，跳过语义分析")
            return None
        
        context = context or {}
        
        # 构建提示词
        prompt = self._analyze_prompt.format(
            description=step_description,
            step_type=step_type,
            params=json.dumps(params, ensure_ascii=False, indent=2),
            success_text="成功" if success else "失败",
            error=error or "无",
            package=context.get("package", "未知"),
            device_type=context.get("device_type", "未知"),
            previous_step=context.get("previous_step", "无"),
        )
        
        try:
            # 调用 LLM 分析
            system_prompt = "你是一个测试自动化专家，擅长理解测试步骤的语义和分析失败原因。"
            result = self._client.chat_json(
                system_prompt,
                prompt,
                temperature=0.3,
                timeout=30.0,
            )
            
            # 创建语义洞察
            insight = SemanticInsight(
                step_description=step_description,
                step_type=step_type,
                success=success,
                semantic_meaning=result.get("semantic_meaning", ""),
                failure_reason=result.get("failure_analysis", ""),
                reusable_knowledge=result.get("reusable_knowledge", ""),
                confidence=float(result.get("confidence", 0.7)),
            )
            
            self._insights.append(insight)
            
            # 提取上下文模式
            context_insight = result.get("context_insight", "")
            if context_insight:
                self._extract_pattern(context_insight, step_type, success)
            
            logger.info(f"语义分析完成: {insight.semantic_meaning[:50]}...")
            return insight
            
        except Exception as e:
            logger.warning(f"语义分析失败: {e}")
            return None

    def _extract_pattern(self, scenario: str, step_type: str, success: bool) -> None:
        """从语义洞察中提取上下文模式"""
        if scenario not in self._patterns:
            self._patterns[scenario] = ContextualPattern(
                scenario=scenario,
                step_sequence=[],
            )
        
        pattern = self._patterns[scenario]
        pattern.step_sequence.append(step_type)
        
        # 更新成功率
        total = len(pattern.step_sequence)
        success_count = sum(1 for s in pattern.step_sequence if s == step_type and success)
        pattern.success_rate = success_count / total if total > 0 else 0.0

    def learn_from_batch(
        self,
        executions: List[Dict[str, Any]],
    ) -> Optional[ContextualPattern]:
        """
        从批量执行中学习模式
        
        使用 LLM 分析多个执行记录，识别通用模式
        """
        if not self._client.configured or len(executions) < 3:
            return None
        
        # 构建执行记录文本
        records_text = []
        for i, exec_data in enumerate(executions, 1):
            records_text.append(
                f"{i}. [{exec_data.get('step_type')}] {exec_data.get('description')}\n"
                f"   结果: {'成功' if exec_data.get('success') else '失败'}\n"
                f"   错误: {exec_data.get('error', '无')}"
            )
        
        prompt = self._pattern_prompt.format(records="\n".join(records_text))
        
        try:
            system_prompt = "你是一个测试优化专家，擅长从历史数据中识别模式并提出改进建议。"
            result = self._client.chat_json(
                system_prompt,
                prompt,
                temperature=0.3,
                timeout=45.0,
            )
            
            # 创建上下文模式
            pattern = ContextualPattern(
                scenario=result.get("scenario", "未知场景"),
                step_sequence=[e.get("step_type") for e in executions],
                success_rate=sum(1 for e in executions if e.get("success")) / len(executions),
                common_pitfalls=result.get("failure_patterns", []),
                optimization_suggestions=result.get("optimization_suggestions", []),
            )
            
            # 保存模式
            self._patterns[pattern.scenario] = pattern
            
            logger.info(f"批量学习完成: 识别场景 '{pattern.scenario}'")
            return pattern
            
        except Exception as e:
            logger.warning(f"批量学习失败: {e}")
            return None

    def generate_intelligent_suggestions(
        self,
        step_description: str,
        step_type: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """
        基于语义理解生成智能建议
        
        不只是统计推荐，而是理解意图后给出针对性建议
        """
        suggestions = []
        context = context or {}
        
        # 1. 查找相似的语义洞察
        similar_insights = [
            i for i in self._insights
            if i.step_type == step_type and i.semantic_meaning
        ]
        
        if similar_insights:
            # 从成功经验中学习
            success_insights = [i for i in similar_insights if i.success]
            if success_insights:
                suggestions.append(
                    f"基于历史经验，{success_insights[0].reusable_knowledge}"
                )
            
            # 从失败中吸取教训
            failure_insights = [i for i in similar_insights if not i.success and i.failure_reason]
            if failure_insights:
                suggestions.append(
                    f"注意避免: {failure_insights[0].failure_reason}"
                )
        
        # 2. 查找相关上下文模式
        for scenario, pattern in self._patterns.items():
            if step_type in pattern.step_sequence:
                if pattern.common_pitfalls:
                    suggestions.extend(pattern.common_pitfalls[:2])
                if pattern.optimization_suggestions:
                    suggestions.extend(pattern.optimization_suggestions[:2])
        
        # 3. 基于语义的通用建议
        if "等待" in step_description or "wait" in step_type.lower():
            suggestions.append("考虑增加超时时间以应对设备响应延迟")
        elif "点击" in step_description or "tap" in step_type.lower():
            suggestions.append("确保目标元素已加载完成且可见")
        elif "启动" in step_description or "start" in step_type.lower():
            suggestions.append("启动后建议等待 2-3 秒让应用完全加载")
        
        return suggestions[:5]  # 最多返回 5 条建议

    def get_semantic_understanding(self, step_description: str) -> str:
        """获取步骤的语义理解"""
        # 查找最相似的洞察
        for insight in self._insights:
            if insight.step_description == step_description:
                return insight.semantic_meaning
        
        # 如果没有历史记录，返回基本理解
        return f"执行 {step_description}"

    def export_semantic_knowledge(self) -> Dict[str, Any]:
        """导出语义知识"""
        return {
            "insights": [
                {
                    "description": i.step_description,
                    "type": i.step_type,
                    "meaning": i.semantic_meaning,
                    "success": i.success,
                    "failure_reason": i.failure_reason,
                    "knowledge": i.reusable_knowledge,
                    "confidence": i.confidence,
                }
                for i in self._insights[-100:]  # 最近 100 条
            ],
            "patterns": {
                scenario: {
                    "scenario": p.scenario,
                    "success_rate": p.success_rate,
                    "pitfalls": p.common_pitfalls,
                    "suggestions": p.optimization_suggestions,
                }
                for scenario, p in self._patterns.items()
            },
        }


# 全局单例
_semantic_learner: Optional[SemanticLearner] = None


def get_semantic_learner(ai_client: AIClient) -> SemanticLearner:
    """获取全局语义学习器实例"""
    global _semantic_learner
    if _semantic_learner is None:
        _semantic_learner = SemanticLearner(ai_client)
    return _semantic_learner
