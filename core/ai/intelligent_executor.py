"""
智能执行器 v2
============
像 Qoder AI 一样理解和执行用户指令。

核心能力：
1. 意图识别：理解用户真正想要做什么
2. 语义解析：处理各种口语化表达
3. 上下文感知：根据当前状态调整执行策略
4. 智能容错：失败时自动尝试替代方案
"""

import re
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum

from loguru import logger

from ..testcase.model import TestCase, TestStep, StepType, Priority


class Intent(Enum):
    """用户意图类型"""
    OPEN_APP = "open_app"
    SEARCH = "search"
    PLAY_MEDIA = "play_media"
    NAVIGATE = "navigate"
    INPUT_TEXT = "input_text"
    SCROLL_FIND = "scroll_find"
    CLICK_ELEMENT = "click_element"
    WAIT = "wait"
    COMPLEX_TASK = "complex_task"


@dataclass
class IntentResult:
    """意图识别结果"""
    intent: Intent
    confidence: float
    entities: Dict[str, str]
    raw_text: str
    normalized_text: str


class SemanticUnderstanding:
    """语义理解器"""

    # 同义词组
    SYNONYMS = {
        "open": ["打开", "启动", "进入", "点开", "打卡", "运行"],
        "search": ["搜索", "查找", "搜一下", "找一下", "查询"],
        "play": ["播放", "放", "播放一下", "听", "看"],
        "click": ["点击", "点", "点一下", "轻触", "按"],
        "input": ["输入", "填写", "写入", "键入"],
        "scroll": ["滑动", "滑", "滚", "翻页", "下拉", "上拉"],
        "wait": ["等待", "等", "等一下", "等一会儿"],
        "find": ["找到", "查找", "发现", "看到", "出现"],
    }

    # 意图模式（所有动词前缀用非捕获组，target 统一在 group(1)）
    INTENT_PATTERNS = [
        # 滚动查找（必须在最前面，group(1)=方向动词, group(2)=目标）
        (r"(下滑|往下滑|向下滚|上滑|往上滑).*(?:找到|看到|出现)(.+)", Intent.SCROLL_FIND, 0.9),
        # 打开应用
        (r"(?:打开|启动|进入|点开)(.+)", Intent.OPEN_APP, 0.9),
        # 点击（必须在搜索/播放前面，避免"点击搜索"被误判为搜索意图）
        (r"(?:点击|点一下|轻触|按)(.+)", Intent.CLICK_ELEMENT, 0.85),
        # 搜索
        (r"(?:搜索|查找|搜一下|找一下|搜)(.+)", Intent.SEARCH, 0.9),
        # 播放（排除"一下"）
        (r"(?:播放|放|听|看)(?:一下)?(.+)", Intent.PLAY_MEDIA, 0.9),
        # 输入
        (r"(?:输入|填写|写入)(.+)", Intent.INPUT_TEXT, 0.85),
        # 等待
        (r"(?:等待|等)\s*(\d+)", Intent.WAIT, 0.8),
    ]

    def __init__(self):
        self._compiled = [(re.compile(p), i, c) for p, i, c in self.INTENT_PATTERNS]

    def understand(self, text: str, context: Dict = None) -> IntentResult:
        text = text.strip()
        if not text:
            return IntentResult(Intent.COMPLEX_TASK, 0.0, {}, text, "")

        # 模式匹配
        for pattern, intent, conf in self._compiled:
            m = pattern.search(text)
            if m:
                # SCROLL_FIND: group(1)=方向, group(2)=目标
                if intent == Intent.SCROLL_FIND:
                    target = m.group(2).strip() if m.lastindex >= 2 else ""
                    # "上滑"=手指上划=direction="up"
                    # "下滑"=手指下划=direction="down"
                    direction = "up" if "上" in m.group(1) else "down"
                else:
                    # 所有其他模式：group(1)=目标
                    target = m.group(1).strip() if m.lastindex else ""
                    direction = "down" if "下" in text else "up"

                # 清理目标
                target = re.sub(r"(吧|啊|呀|嘛|呢|哦)$", "", target).strip()

                entities = {"target": target}
                if intent == Intent.SEARCH:
                    entities["keyword"] = target
                elif intent == Intent.SCROLL_FIND:
                    entities["direction"] = direction
                elif intent == Intent.WAIT:
                    entities["duration"] = target

                return IntentResult(intent, conf, entities, text, self._normalize(text))

        # 关键词降级
        intent, entities = self._keyword_match(text)
        if intent:
            return IntentResult(intent, 0.7, entities, text, self._normalize(text))

        return IntentResult(Intent.COMPLEX_TASK, 0.5, {"goal": text}, text, text)

    def _keyword_match(self, text: str) -> Tuple[Optional[Intent], Dict]:
        for synonym in self.SYNONYMS["open"]:
            if synonym in text:
                m = re.search(rf"{synonym}(.+)", text)
                t = m.group(1).strip() if m else ""
                # 去掉尾部分隔符后的内容
                t = re.split(r"[，,。；;！!？?]", t)[0].strip()
                if t and t not in ["界面", "页面"]:
                    return Intent.OPEN_APP, {"target": t}

        # 点击优先于搜索/播放（避免"点击搜索"被误判）
        for synonym in self.SYNONYMS["click"]:
            if synonym in text:
                m = re.search(rf"{synonym}(.+)", text)
                t = m.group(1).strip() if m else ""
                t = re.split(r"[，,。；;！!？?]", t)[0].strip()
                if t:
                    return Intent.CLICK_ELEMENT, {"target": t}

        for synonym in self.SYNONYMS["search"]:
            if synonym in text:
                m = re.search(rf"{synonym}(.+)", text)
                t = m.group(1).strip() if m else ""
                t = re.split(r"[，,。；;！!？?]", t)[0].strip()
                if t:
                    return Intent.SEARCH, {"target": t, "keyword": t}

        # 播放：先检查滑动查找，避免"下滑找到播放按钮"被误判为播放
        if any(k in text for k in ["下滑", "往下滑", "向下滚", "上滑", "往上滑"]):
            m = re.search(r"(?:找到|看到|出现)(.+)", text)
            t = m.group(1).strip() if m else ""
            t = re.split(r"[，,。；;！!？?]", t)[0].strip()
            if t:
                # "上滑"=手指上划=direction="up"
                # "下滑"=手指下划=direction="down"
                direction = "up" if any(k in text for k in ["上滑", "往上滑"]) else "down"
                return Intent.SCROLL_FIND, {"target": t, "direction": direction}

        for synonym in self.SYNONYMS["play"]:
            if synonym in text:
                m = re.search(rf"{synonym}(?:一下)?(.+)", text)
                t = m.group(1).strip() if m else ""
                t = re.split(r"[，,。；;！!？?]", t)[0].strip()
                if t:
                    return Intent.PLAY_MEDIA, {"target": t}

        for synonym in self.SYNONYMS["input"]:
            if synonym in text:
                m = re.search(rf"{synonym}(.+)", text)
                t = m.group(1).strip() if m else ""
                t = re.split(r"[，,。；;！!？?]", t)[0].strip()
                if t:
                    return Intent.INPUT_TEXT, {"target": t}

        return None, {}

    def _normalize(self, text: str) -> str:
        text = re.sub(r"^(帮我|请|我想|需要|可以)", "", text)
        text = re.sub(r"(吧|啊|呀|嘛|呢|哦)$", "", text)
        return text.strip()


class IntelligentStepGenerator:
    """智能步骤生成器"""

    # 启动应用的动词
    _LAUNCH_VERBS = ["打开", "启动", "进入", "点开", "打卡", "运行"]

    def __init__(self, semantic: SemanticUnderstanding):
        self.semantic = semantic

    def generate_steps(self, description: str, device_id: str = "",
                       package: str = "", activity: str = "",
                       context: Dict = None) -> List[TestStep]:
        context = context or {}

        # 如果 package 为空，尝试从 context 的 app_name_map 解析
        if not package:
            app_name = self._extract_app_name(description)
            app_name_map = context.get("app_name_map", {})
            if app_name and app_name_map:
                # 精确匹配
                if app_name in app_name_map:
                    package = app_name_map[app_name]
                else:
                    # 模糊匹配
                    app_name_lower = app_name.lower()
                    for name, pkg in app_name_map.items():
                        if app_name_lower in name.lower() or name.lower() in app_name_lower:
                            package = pkg
                            break
                if package:
                    logger.info(f"从 app_name_map 解析: '{app_name}' -> '{package}'")

        clauses = self._split_clauses(description)

        steps = []
        # 上下文跟踪：记录前面提到的所有目标（用于代词解析）
        context_entities = {
            "media_target": "",      # 最近提到的媒体/歌曲名
            "last_target": "",       # 最近提到的任何目标
            "last_action_target": "",  # 上一个动作的具体目标
        }
        
        # 代词列表（扩展）
        pronouns = {
            "该歌曲", "这首歌", "这首", "该曲", "它", "这个", "那个",
            "该项", "这一项", "此", "该", "其", "该按钮", "该选项",
        }
        
        for clause in clauses:
            intent = self.semantic.understand(clause, context)
            
            # 处理指代词：用上下文中记录的真实目标替换
            target = intent.entities.get("target", "")
            if target in pronouns or (len(target) <= 3 and target in ("它", "这", "那", "该")):
                # 优先使用媒体目标，否则使用上一个动作目标
                resolved = (context_entities["media_target"] 
                           or context_entities["last_action_target"]
                           or context_entities["last_target"])
                if resolved:
                    logger.debug(f"代词「{target}」解析为「{resolved}」")
                    intent.entities["target"] = resolved
                    target = resolved
            
            # 更新上下文跟踪
            if target and target not in pronouns:
                context_entities["last_target"] = target
                context_entities["last_action_target"] = target
                # 媒体相关动作（播放/搜索/滑动查找）记录为媒体目标
                if intent.intent in (Intent.PLAY_MEDIA, Intent.SEARCH, Intent.SCROLL_FIND):
                    context_entities["media_target"] = target
            
            clause_steps = self._intent_to_steps(intent, device_id, package, activity)
            steps.extend(clause_steps)

        return steps

    def _extract_app_name(self, description: str) -> str:
        """从描述中提取被测应用名称"""
        for verb in self._LAUNCH_VERBS:
            if verb in description:
                # 提取动词后面的应用名称
                idx = description.index(verb) + len(verb)
                rest = description[idx:].strip()
                # 取到第一个分隔符之前的内容
                for sep in ["，", ",", "。", " ", "然后", "再", "接着"]:
                    if sep in rest:
                        rest = rest[:rest.index(sep)]
                        break
                # 清理常见后缀
                rest = re.sub(r"(的|应用|软件|app)$", "", rest, flags=re.IGNORECASE)
                if rest and len(rest) <= 20:
                    return rest.strip()
        return ""

    def _split_clauses(self, text: str) -> List[str]:
        separators = r"[，,。；;！!？?\n]|然后再|然后|接着|之后|最后"
        clauses = re.split(separators, text)
        result = []
        for c in clauses:
            c = re.sub(r"^(帮我|请|我想|需要)", "", c).strip()
            if c:
                result.append(c)
        return result

    def _intent_to_steps(self, intent: IntentResult, device_id: str,
                         package: str, activity: str) -> List[TestStep]:
        steps = []
        target = intent.entities.get("target", "")

        if intent.intent == Intent.OPEN_APP:
            if package:
                steps.append(TestStep(
                    step_type=StepType.APP_START,
                    description=f"启动应用 {package}",
                    device_id=device_id,
                    params={"package": package, "activity": activity} if activity else {"package": package},
                    timeout=20,
                ))
                steps.append(TestStep(
                    step_type=StepType.WAIT,
                    description="等待应用启动",
                    params={"duration": 1.5},
                ))
            elif target:
                steps.append(TestStep(
                    step_type=StepType.UI_TAP,
                    description=f"点击「{target}」",
                    device_id=device_id,
                    params={"text": target},
                    timeout=15,
                ))

        elif intent.intent == Intent.SEARCH:
            keyword = intent.entities.get("keyword", target)
            # 搜索意图：只输入关键词，不自动点击搜索按钮
            # 如果用户说"点击搜索"，那会被 CLICK_ELEMENT 处理
            if keyword:
                steps.append(TestStep(
                    step_type=StepType.UI_INPUT,
                    description=f"搜索输入「{keyword}」",
                    device_id=device_id,
                    params={"text": keyword, "clear": True, "submit": True},
                    timeout=10,
                ))

        elif intent.intent == Intent.PLAY_MEDIA:
            if target:
                steps.append(TestStep(
                    step_type=StepType.UI_TAP,
                    description=f"播放「{target}」",
                    device_id=device_id,
                    params={"text": target},
                    timeout=15,
                ))
            else:
                steps.append(TestStep(
                    step_type=StepType.UI_TAP,
                    description="点击播放",
                    device_id=device_id,
                    params={"text": "播放"},
                    timeout=10,
                ))

        elif intent.intent == Intent.SCROLL_FIND:
            direction = intent.entities.get("direction", "down")
            steps.append(TestStep(
                step_type=StepType.UI_SWIPE,
                description=f"向{direction}滑动直到出现「{target}」",
                device_id=device_id,
                params={
                    "direction": direction,
                    "region": "full",
                    "until_text": target,
                    "max_swipes": 10,
                    "duration": 400,
                },
                timeout=25,
            ))

        elif intent.intent == Intent.CLICK_ELEMENT:
            if target:
                steps.append(TestStep(
                    step_type=StepType.UI_TAP,
                    description=f"点击「{target}」",
                    device_id=device_id,
                    params={"text": target},
                    timeout=15,
                ))

        elif intent.intent == Intent.INPUT_TEXT:
            if target:
                steps.append(TestStep(
                    step_type=StepType.UI_INPUT,
                    description=f"输入「{target}」",
                    device_id=device_id,
                    params={"text": target, "clear": True},
                    timeout=10,
                ))

        elif intent.intent == Intent.WAIT:
            duration = float(intent.entities.get("duration", 2.0))
            steps.append(TestStep(
                step_type=StepType.WAIT,
                description=f"等待{duration}秒",
                params={"duration": duration},
            ))

        elif intent.intent == Intent.COMPLEX_TASK:
            goal = intent.entities.get("goal", intent.raw_text)
            steps.append(TestStep(
                step_type=StepType.AGENT_TASK,
                description=f"自主完成：{goal[:50]}",
                device_id=device_id,
                params={"goal": goal, "max_steps": 40},
                timeout=300,
            ))

        return steps


class IntelligentExecutor:
    """智能执行器主入口"""

    def __init__(self):
        self.semantic = SemanticUnderstanding()
        self.generator = IntelligentStepGenerator(self.semantic)

    def generate(self, description: str, device_id: str = "",
                 package: str = "", activity: str = "",
                 context: Dict = None) -> List[TestStep]:
        context = context or {}
        app_name_map = context.get("app_name_map", {})
        logger.info(f"智能解析：{description}")
        logger.info(f"  package={package or '(空)'}, app_name_map={len(app_name_map)}个映射")
        steps = self.generator.generate_steps(description, device_id, package, activity, context)
        logger.info(f"生成{len(steps)}个步骤")
        for i, s in enumerate(steps, 1):
            logger.info(f"  {i}. {s.step_type.value}: {s.description}")
        return steps

    def generate_test_case(self, description: str, device_id: str = "",
                           package: str = "", activity: str = "",
                           context: Dict = None) -> TestCase:
        steps = self.generate(description, device_id, package, activity, context)
        return TestCase(
            name=f"智能执行：{description[:30]}",
            description=description,
            category="AI 智能生成",
            tags=["AI 生成", "智能执行"],
            priority=Priority.P1,
            steps=steps,
        )


_executor_instance: Optional[IntelligentExecutor] = None

def get_intelligent_executor() -> IntelligentExecutor:
    global _executor_instance
    if _executor_instance is None:
        _executor_instance = IntelligentExecutor()
    return _executor_instance
