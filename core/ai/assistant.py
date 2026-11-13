"""
AI 测试助手（门面）
====================
统一封装诊断、用例生成、执行规划与修复动作应用。
"""

from typing import List, Optional, Tuple

from loguru import logger

from .analyzer import Diagnosis, analyze_failure
from .client import AIClient, AIError
from .device_context import collect_app_context
from .generator import generate_cases, plan_execution
from .repair import RepairResult, apply_actions


class AIAssistant:
    """AI 测试助手"""

    def __init__(self, config):
        self._config = config
        self._client = AIClient(config)
        # 首次启动时生成界面方法/同义词示例文件，方便用户不改程序就适配新应用
        try:
            from .ui_memory import ensure_templates
            ensure_templates()
        except Exception:
            pass
        
        # 初始化自适应生成器
        try:
            from .adaptive_generator import get_adaptive_generator
            self._adaptive_generator = get_adaptive_generator()
        except Exception as e:
            logger.debug(f"自适应生成器初始化失败: {e}")
            self._adaptive_generator = None

    # ---------- 配置 ----------

    def refresh_config(self, config) -> None:
        self._config = config
        self._client = AIClient(config)

    @property
    def client(self) -> AIClient:
        return self._client

    @property
    def llm_available(self) -> bool:
        return self._client.configured

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._config, "enabled", True))

    # ---------- 诊断与修复 ----------

    def diagnose(self, case, step, step_index, error: str,
                 device_manager=None) -> Diagnosis:
        return analyze_failure(
            self._client, case, step, step_index, error,
            self.devices_summary(device_manager),
            offline_fallback=bool(getattr(self._config, "offline_fallback", True)),
        )

    def repair(self, case, actions: List[dict],
               device_manager=None, default_step_index: Optional[int] = None,
               default_step_section: str = "steps"
               ) -> RepairResult:
        return apply_actions(
            case, actions, device_manager,
            default_step_index=default_step_index,
            default_step_section=default_step_section,
        )

    # ---------- 生成与规划 ----------

    def generate(self, description: str, device_manager=None,
                 app_package: str = "",
                 app_activity: str = "",
                 param_context: str = "",
                 param_names: Optional[List[str]] = None) -> Tuple[List, str, str]:
        """生成用例；app_package 为界面上选定的目标应用（可空）

        返回 (用例列表, 来源 "llm"/"rule", 说明文字)
        """
        context = collect_app_context(
            device_manager, app_package, app_activity
        )
        # 画布界面且视觉模型可用时，自动附带当前截图，让模型看清界面
        image_b64 = ""
        try:
            from .device_context import is_canvas_context, capture_screenshot_b64
            if is_canvas_context(context) and self._client.supports_vision():
                image_b64 = capture_screenshot_b64(device_manager)
        except Exception as e:
            logger.debug(f"生成时自动截图失败: {e}")
        return generate_cases(
            self._client, description,
            self.devices_summary(device_manager),
            app_context=context,
            image_b64=image_b64,
            param_context=param_context,
            param_names=param_names,
        )

    def plan(self, cases: List, instruction: str = "") -> Tuple[List, str, str]:
        return plan_execution(self._client, cases, instruction)

    # ---------- 上下文 ----------

    @staticmethod
    def devices_summary(device_manager) -> List[dict]:
        """生成设备摘要，供提示词与会话使用"""
        if device_manager is None:
            return []

        summary = []
        seen = set()
        try:
            for device in device_manager.get_all_devices().values():
                seen.add(device.device_id)
                summary.append({
                    "device_id": device.device_id,
                    "type": type(device).__name__.replace("Device", "").upper(),
                    "name": getattr(device, "name", device.device_id),
                    "status": device.status.value,
                    "connected": True,
                })
            for info in getattr(device_manager, "last_scan_results", [])[:20]:
                if info.device_id in seen:
                    continue
                seen.add(info.device_id)
                summary.append({
                    "device_id": info.device_id,
                    "type": info.device_type.value.upper(),
                    "name": info.name,
                    "status": "available",
                    "connected": False,
                })
        except Exception:
            return summary
        return summary

    # ---------- 学习能力 ----------

    def optimize_case(self, case, context: Optional[dict] = None):
        """
        使用学习到的经验优化测试用例
        
        参数:
            case: 待优化的 TestCase
            context: 执行上下文（设备信息、应用信息等）
        
        返回:
            优化后的 TestCase
        """
        if self._adaptive_generator is None:
            logger.debug("自适应生成器不可用，跳过优化")
            return case
        
        try:
            return self._adaptive_generator.generate_with_learning(case, context)
        except Exception as e:
            logger.warning(f"用例优化失败: {e}")
            return case
    
    def suggest_improvements(self, case) -> List[str]:
        """
        基于学习数据建议用例改进
        
        返回:
            改进建议列表
        """
        if self._adaptive_generator is None:
            return []
        
        try:
            return self._adaptive_generator.suggest_improvements(case)
        except Exception as e:
            logger.debug(f"获取改进建议失败: {e}")
            return []
    
    def get_learning_report(self) -> str:
        """
        获取学习报告
        
        返回:
            格式化的学习统计报告
        """
        if self._adaptive_generator is None:
            return "学习系统未初始化"
        
        try:
            return self._adaptive_generator.generate_learning_report()
        except Exception as e:
            return f"生成学习报告失败: {e}"
    
    def export_learned_knowledge(self) -> dict:
        """
        导出学习到的知识
        
        返回:
            包含经验和模式的知识字典
        """
        if self._adaptive_generator is None:
            return {}
        
        try:
            return self._adaptive_generator.export_knowledge()
        except Exception as e:
            logger.warning(f"导出学习知识失败: {e}")
            return {}
    
    def get_success_rate(self, step_type: Optional[str] = None) -> float:
        """
        获取步骤类型的成功率
        
        参数:
            step_type: 步骤类型（如 "ui_tap"），None 表示总体成功率
        
        返回:
            成功率 (0.0 ~ 1.0)
        """
        try:
            from .experience_learner import get_experience_learner
            learner = get_experience_learner()
            return learner.get_success_rate(step_type)
        except Exception as e:
            logger.debug(f"获取成功率失败: {e}")
            return 0.0
    
    def get_common_failures(self, top_n: int = 5) -> List[Tuple[str, int, str]]:
        """
        获取最常见的失败模式
        
        返回:
            [(错误类型, 出现次数, 影响的步骤类型), ...]
        """
        try:
            from .experience_learner import get_experience_learner
            learner = get_experience_learner()
            return learner.get_common_failures(top_n)
        except Exception as e:
            logger.debug(f"获取常见失败失败: {e}")
            return []
