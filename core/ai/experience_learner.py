"""
经验学习器
===========
从测试执行结果中学习，持续改进 AI 的测试生成和执行能力。

学习维度：
1. 步骤成功率统计 - 识别容易失败的操作类型
2. 失败模式聚类 - 发现常见失败原因并总结规律
3. 参数优化建议 - 基于历史数据推荐最优参数
4. 知识库自动更新 - 将学习到的经验沉淀到知识库
"""

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from loguru import logger


@dataclass
class ExecutionRecord:
    """单次执行记录"""
    timestamp: str
    case_name: str
    case_category: str
    step_type: str
    step_description: str
    success: bool
    duration: float
    error_type: str = ""
    error_message: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StepStatistics:
    """步骤类型统计"""
    total_executions: int = 0
    success_count: int = 0
    failure_count: int = 0
    average_duration: float = 0.0
    common_errors: Dict[str, int] = field(default_factory=dict)
    optimal_params: Dict[str, Any] = field(default_factory=dict)
    last_updated: str = ""


@dataclass
class FailurePattern:
    """失败模式"""
    pattern_id: str
    error_type: str
    error_keywords: List[str]
    occurrence_count: int = 0
    suggested_fix: str = ""
    success_rate_after_fix: float = 0.0
    affected_step_types: List[str] = field(default_factory=list)
    first_seen: str = ""
    last_seen: str = ""


class ExperienceLearner:
    """经验学习器"""

    def __init__(self, storage_dir: Optional[str] = None):
        if storage_dir is None:
            storage_dir = str(Path.home() / ".vehicle_test_app" / "experience")
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        
        # 统计数据
        self._step_stats: Dict[str, StepStatistics] = {}
        self._failure_patterns: Dict[str, FailurePattern] = {}
        self._execution_history: List[ExecutionRecord] = []
        self._category_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"success": 0, "failure": 0})
        
        # 加载历史数据
        self._load_history()
        self._load_statistics()
        
    def _history_path(self) -> Path:
        return self._storage_dir / "execution_history.json"
    
    def _stats_path(self) -> Path:
        return self._storage_dir / "step_statistics.json"
    
    def _patterns_path(self) -> Path:
        return self._storage_dir / "failure_patterns.json"
    
    def _load_history(self) -> None:
        """加载执行历史"""
        path = self._history_path()
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._execution_history = [
                ExecutionRecord(**record) for record in data.get("records", [])
            ]
            logger.debug(f"加载了 {len(self._execution_history)} 条执行历史")
        except Exception as e:
            logger.warning(f"加载执行历史失败: {e}")
    
    def _load_statistics(self) -> None:
        """加载统计数据"""
        path = self._stats_path()
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for step_type, stats_data in data.get("step_stats", {}).items():
                self._step_stats[step_type] = StepStatistics(**stats_data)
            for pattern_id, pattern_data in data.get("failure_patterns", {}).items():
                self._failure_patterns[pattern_id] = FailurePattern(**pattern_data)
            logger.debug(f"加载了 {len(self._step_stats)} 个步骤统计和 {len(self._failure_patterns)} 个失败模式")
        except Exception as e:
            logger.warning(f"加载统计数据失败: {e}")
    
    def _save_history(self) -> None:
        """保存执行历史（只保留最近 1000 条）"""
        recent = self._execution_history[-1000:]
        data = {"records": [asdict(r) for r in recent]}
        try:
            with open(self._history_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存执行历史失败: {e}")
    
    def _save_statistics(self) -> None:
        """保存统计数据"""
        data = {
            "step_stats": {k: asdict(v) for k, v in self._step_stats.items()},
            "failure_patterns": {k: asdict(v) for k, v in self._failure_patterns.items()},
        }
        try:
            with open(self._stats_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存统计数据失败: {e}")
    
    def record_execution(
        self,
        case_name: str,
        case_category: str,
        step_type: str,
        step_description: str,
        success: bool,
        duration: float,
        error_type: str = "",
        error_message: str = "",
        params: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """记录一次步骤执行"""
        record = ExecutionRecord(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            case_name=case_name,
            case_category=case_category,
            step_type=step_type,
            step_description=step_description,
            success=success,
            duration=duration,
            error_type=error_type,
            error_message=error_message,
            params=params or {},
            context=context or {},
        )
        self._execution_history.append(record)
        
        # 更新统计
        self._update_step_statistics(step_type, success, duration, error_type, params)
        
        # 更新分类统计
        if success:
            self._category_stats[case_category]["success"] += 1
        else:
            self._category_stats[case_category]["failure"] += 1
        
        # 识别失败模式
        if not success and error_type:
            self._identify_failure_pattern(step_type, error_type, error_message)
        
        # 定期保存
        if len(self._execution_history) % 10 == 0:
            self._save_history()
            self._save_statistics()
    
    def _update_step_statistics(
        self,
        step_type: str,
        success: bool,
        duration: float,
        error_type: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        """更新步骤统计"""
        if step_type not in self._step_stats:
            self._step_stats[step_type] = StepStatistics()
        
        stats = self._step_stats[step_type]
        stats.total_executions += 1
        
        if success:
            stats.success_count += 1
        else:
            stats.failure_count += 1
            if error_type:
                stats.common_errors[error_type] = stats.common_errors.get(error_type, 0) + 1
        
        # 更新平均执行时间（指数移动平均）
        if stats.average_duration == 0:
            stats.average_duration = duration
        else:
            alpha = 0.3  # 平滑系数
            stats.average_duration = alpha * duration + (1 - alpha) * stats.average_duration
        
        # 记录成功时的参数（用于优化建议）
        if success and params:
            stats.optimal_params = params
        
        stats.last_updated = datetime.now().isoformat(timespec="seconds")
    
    def _identify_failure_pattern(
        self,
        step_type: str,
        error_type: str,
        error_message: str,
    ) -> None:
        """识别失败模式"""
        # 生成模式 ID（基于错误类型和关键词）
        keywords = self._extract_error_keywords(error_message)
        pattern_id = f"{error_type}_{'_'.join(keywords[:3])}"
        
        if pattern_id not in self._failure_patterns:
            self._failure_patterns[pattern_id] = FailurePattern(
                pattern_id=pattern_id,
                error_type=error_type,
                error_keywords=keywords[:5],
                first_seen=datetime.now().isoformat(timespec="seconds"),
            )
        
        pattern = self._failure_patterns[pattern_id]
        pattern.occurrence_count += 1
        pattern.last_seen = datetime.now().isoformat(timespec="seconds")
        
        if step_type not in pattern.affected_step_types:
            pattern.affected_step_types.append(step_type)
    
    def _extract_error_keywords(self, error_message: str) -> List[str]:
        """从错误信息中提取关键词"""
        # 常见错误关键词
        keyword_patterns = [
            "超时", "timeout", "未连接", "断开", "失败", "错误",
            "不存在", "无法", "拒绝", "异常", "无效", "空",
            "连接", "设备", "应用", "界面", "元素", "文字",
        ]
        
        keywords = []
        message_lower = error_message.lower()
        for pattern in keyword_patterns:
            if pattern in message_lower:
                keywords.append(pattern)
        
        return keywords[:5] if keywords else ["未知错误"]
    
    def get_success_rate(self, step_type: Optional[str] = None) -> float:
        """获取成功率"""
        if step_type:
            stats = self._step_stats.get(step_type)
            if not stats or stats.total_executions == 0:
                return 0.0
            return stats.success_count / stats.total_executions
        
        # 总体成功率
        total = sum(s.total_executions for s in self._step_stats.values())
        success = sum(s.success_count for s in self._step_stats.values())
        return success / total if total > 0 else 0.0
    
    def get_common_failures(self, top_n: int = 5) -> List[Tuple[str, int, str]]:
        """获取最常见的失败模式"""
        patterns = sorted(
            self._failure_patterns.values(),
            key=lambda p: p.occurrence_count,
            reverse=True,
        )[:top_n]
        
        return [
            (p.error_type, p.occurrence_count, ", ".join(p.affected_step_types))
            for p in patterns
        ]
    
    def suggest_optimal_params(self, step_type: str) -> Optional[Dict[str, Any]]:
        """基于历史数据建议最优参数"""
        stats = self._step_stats.get(step_type)
        if not stats or not stats.optimal_params:
            return None
        return stats.optimal_params
    
    def suggest_timeout(self, step_type: str, default: float = 30.0) -> float:
        """建议超时时间（基于历史执行时间）"""
        stats = self._step_stats.get(step_type)
        if not stats or stats.average_duration == 0:
            return default
        
        # 建议超时 = 平均时间 * 2 + 5秒缓冲
        suggested = stats.average_duration * 2 + 5.0
        return max(default, min(suggested, 300.0))  # 限制在 default ~ 300秒
    
    def get_category_performance(self) -> Dict[str, Dict[str, Any]]:
        """获取各类别测试的性能统计"""
        result = {}
        for category, counts in self._category_stats.items():
            total = counts["success"] + counts["failure"]
            if total > 0:
                result[category] = {
                    "total": total,
                    "success": counts["success"],
                    "failure": counts["failure"],
                    "success_rate": counts["success"] / total,
                }
        return result
    
    def generate_learning_report(self) -> str:
        """生成学习报告"""
        lines = [
            "=" * 60,
            "AI 经验学习报告",
            "=" * 60,
            f"\n总执行记录数: {len(self._execution_history)}",
            f"总体成功率: {self.get_success_rate():.1%}",
            f"已学习步骤类型: {len(self._step_stats)}",
            f"已识别失败模式: {len(self._failure_patterns)}",
            "\n" + "-" * 60,
            "各步骤类型成功率:",
            "-" * 60,
        ]
        
        for step_type, stats in sorted(self._step_stats.items()):
            if stats.total_executions > 0:
                rate = stats.success_count / stats.total_executions
                lines.append(f"  {step_type:20s}: {rate:6.1%} ({stats.success_count}/{stats.total_executions})")
        
        if self._failure_patterns:
            lines.extend([
                "\n" + "-" * 60,
                "常见失败模式 (Top 5):",
                "-" * 60,
            ])
            for error_type, count, step_types in self.get_common_failures(5):
                lines.append(f"  [{count:3d}次] {error_type:30s} → {step_types}")
        
        lines.append("\n" + "=" * 60)
        return "\n".join(lines)
    
    def export_knowledge(self) -> Dict[str, Any]:
        """导出学习到的知识（可合并到知识库）"""
        knowledge = {
            "learned_at": datetime.now().isoformat(timespec="seconds"),
            "statistics": {
                step_type: {
                    "success_rate": stats.success_count / stats.total_executions if stats.total_executions > 0 else 0,
                    "average_duration": stats.average_duration,
                    "common_errors": stats.common_errors,
                    "optimal_params": stats.optimal_params,
                }
                for step_type, stats in self._step_stats.items()
            },
            "failure_patterns": {
                pattern_id: {
                    "error_type": p.error_type,
                    "occurrence_count": p.occurrence_count,
                    "affected_step_types": p.affected_step_types,
                    "keywords": p.error_keywords,
                }
                for pattern_id, p in self._failure_patterns.items()
            },
        }
        return knowledge


# 全局单例
_learner_instance: Optional[ExperienceLearner] = None


def get_experience_learner() -> ExperienceLearner:
    """获取全局经验学习器实例"""
    global _learner_instance
    if _learner_instance is None:
        _learner_instance = ExperienceLearner()
    return _learner_instance
