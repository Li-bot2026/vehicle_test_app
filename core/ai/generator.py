"""
用例生成与执行规划
===================
- generate_cases: 自然语言 → 可执行测试用例（LLM，离线时用模板兜底）
- plan_execution: 为一批用例给出执行顺序（LLM，离线时按优先级排序）
"""

import re
from typing import List, Optional, Tuple

from loguru import logger

from ..testcase.model import TestCase, TestStep, StepType, Priority
from .client import AIClient, AIError
from .prompts import GENERATE_SYSTEM, PLAN_SYSTEM, generate_prompt, plan_prompt

STEP_TYPE_LOOKUP = {st.value: st for st in StepType}
PRIORITY_LOOKUP = {p.value: p for p in Priority}

# 模型可能用别名/中文描述步骤类型，统一归一化
STEP_TYPE_ALIASES = {
    "tap": "ui_tap", "click": "ui_tap", "touch": "ui_tap", "点击": "ui_tap",
    "input": "ui_input", "type": "ui_input", "set_text": "ui_input",
    "输入": "ui_input", "text_input": "ui_input",
    "wait_text": "ui_wait_text", "assert_text": "ui_wait_text",
    "wait_for_text": "ui_wait_text", "等待文字": "ui_wait_text",
    "launch": "app_start", "start_app": "app_start", "open_app": "app_start",
    "launch_app": "app_start", "启动应用": "app_start",
    "swipe": "ui_swipe", "scroll": "ui_swipe", "滑动": "ui_swipe",
    "滚动": "ui_swipe", "上滑": "ui_swipe", "下滑": "ui_swipe",
    "agent": "agent_task", "autonomous": "agent_task",
    "自主任务": "agent_task", "自主": "agent_task",
}

_PACKAGE_RE = re.compile(r"\b([a-zA-Z][\w]*(?:\.[\w]+){2,})\b")
_PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

# ---------- 口语化界面流程解析 ----------

_UI_VERB_RE = re.compile(
    r"(向上滑|往上滑|向下滑|往下滑|向左滑|往左滑|向右滑|往右滑|"
    r"上滑|下滑|左滑|右滑|滑动|滚动|翻页|翻到|拖动|"
    r"长按|双击|点一下|轻触|点击|点开|点选|选择|"
    r"输入|填写|搜索|打开|启动|进入|打卡|播放|暂停|"
    r"返回|截图|截屏|清空|切换|关闭|退出|找到|查看|"
    r"等待|等到|检查|验证|确保)"
)
_CLAUSE_SPLIT = re.compile(
    r"[，,。；;！!？?\n]|然后再|再|然后|接着|之后|最后|同时|并且"
)
_LAUNCH_VERBS = {"打开", "启动", "进入", "点开", "打卡"}
_TAP_VERBS = {"点击", "轻触", "点一下", "选择", "长按", "双击", "点选",
              "切换", "查看"}
_INPUT_VERBS = {"输入", "填写", "搜索"}
# 等待/检查类动词："等待登录成功""验证出现提示"
# 注意："查看"是导航类动词（查看存储空间 = 点开看），不要放进等待动词，
# 只有"查看是否…"这种带判断语义的才由 _match_wait_clause 先行识别为等待。
_CHECK_VERBS = {"等待", "等到", "等", "检查", "验证", "确保", "确认"}
_PAGE_HINTS = ("详情页", "播放页", "播放详情", "页面", "界面")
_TARGET_TAILS = ("并播放", "进行播放", "按钮", "按键", "图标", "页签", "标签",
                 "提示", "弹窗", "这个", "它", "并")

# 时序后缀："进入界面后""点开设置之后"→ 表示动作顺序，不是界面上的文字
_SEQ_SUFFIX_RE = re.compile(r"(之后|以后|完后|完成后|完了|后|完)$")
# 泛指页面/视图的词：不能当成要等待的界面文字
_GENERIC_PAGE_WORDS = {
    "界面", "页面", "主界面", "主页面", "首页", "主页", "画面", "屏幕",
    "这里", "那里", "界面里", "页面中", "游戏", "应用", "软件", "程序",
    "详情", "详情页", "窗口", "界面中",
}
# 泛指等待结果的词：只能转成固定等待时长，不能匹配界面文字
_GENERIC_WAIT_WORDS = {
    "加载", "加载中", "加载完成", "响应", "完成", "成功", "一下", "一会儿",
    "一会", "片刻", "稳定", "几秒", "页面", "界面", "结果", "变化",
}

# 引号里的文字按字面作为目标（点击"设置"）
_QUOTED_RE = re.compile(r'[\u300c\u300e\u201c\u0022\u0027\u2018\u300a]([^\u300d\u300f\u201d\u0022\u0027\u2019\u300b]{1,30})[\u300d\u300f\u201d\u0022\u0027\u2019\u300b]')
# 代词目标："点击它"沿用上一个明确目标（扩展更多代词形式）
_PRONOUNS = {"它", "这个", "那个", "该项", "这一项", "此", "这里", "该项功能",
             "该歌曲", "这首歌", "这首", "该曲", "该按钮", "该选项", "该", "其"}
# 点击类动作里没有意义的目标（泛指视图）
_TAP_BLOCK_WORDS = {"界面", "页面", "画面", "屏幕", "这里", "那里"}
# "等待3秒 / 等5s"
_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(毫秒|ms|分钟|分|秒|s|S)")
# "直到出现X""以找到X"：挂到前置滑动上
_UNTIL_PREFIX_RE = re.compile(r"^(直到|直至|以|能够|可)?(出现|看到|找到|显示|滑动到|滚到|翻到)")
# 等待类子句前缀（避免把按钮"确认"当成等待动词）
_WAIT_PREFIX_RE = re.compile(
    r"^(?:请|先|然后)?(?:等待|等到|等一会儿|检查|验证|确保|确认|查看)"
    r"(?:是否|一下)?(?:出现|显示|弹出|看到|提示|有)?[：:，,]?"
)


def _is_generic_text(text: str) -> bool:
    """是否是泛指词（"界面""页面""加载成功""界面后再"等，不能当界面文字匹配）"""
    text = (text or "").strip()
    if not text:
        return True
    if text in _GENERIC_PAGE_WORDS or text in _GENERIC_WAIT_WORDS:
        return True
    # 带时序/连接后缀的残句："界面后再""开始游戏后"
    if text.endswith(("后", "时", "再", "然后", "接着", "并")):
        return True
    core = re.sub(r"(之后|以后)$", "", text).strip()
    return core in _GENERIC_PAGE_WORDS or core in _GENERIC_WAIT_WORDS


def _is_meaningful_text(text: str) -> bool:
    """是否是可以用来匹配界面文字的目标"""
    text = (text or "").strip()
    return len(text) >= 2 and not _is_generic_text(text)

# 滑动动词 → 方向
_SWIPE_VERBS = {
    "上滑": "up", "向上滑": "up", "往上滑": "up",
    "下滑": "down", "向下滑": "down", "往下滑": "down",
    "左滑": "left", "向左滑": "left", "往左滑": "left",
    "右滑": "right", "向右滑": "right", "往右滑": "right",
    "滑动": "up", "滚动": "up", "翻页": "down", "翻": "down",
}
# 区域词 → 区域（在滑动动词前出现）
_REGION_RE = re.compile(
    r"(左边|左侧|左面|左|右边|右侧|右面|右|顶部|上部|上面|顶|底部|下部|下面|底|中间|中部|屏幕|整个)"
)
_REGION_MAP = {
    "左边": "left", "左侧": "left", "左面": "left", "左": "left",
    "右边": "right", "右侧": "right", "右面": "right", "右": "right",
    "顶部": "top", "上部": "top", "上面": "top", "顶": "top",
    "底部": "bottom", "下部": "bottom", "下面": "bottom", "底": "bottom",
    "中间": "center", "中部": "center", "屏幕": "full", "整个": "full",
}
_REGION_LABEL = {
    "left": "左侧区域", "right": "右侧区域", "top": "顶部区域",
    "bottom": "底部区域", "center": "中间区域", "full": "整个屏幕",
}
_DIRECTION_LABEL = {"up": "上滑", "down": "下滑", "left": "左滑", "right": "右滑"}

# "搜索结果""播放详情页"里的动词是名词的一部分，先归一化避免误判
_NOUN_FIXES = (
    ("搜索结果", "结果"),
    ("搜索历史", "历史"),
    ("搜索记录", "记录"),
    ("搜索页面", "页面"),
    ("播放详情页", "详情页"),
    ("播放详情", "详情"),
    ("播放列表", "列表"),
    ("播放队列", "队列"),
    ("播放页", "详情页"),
    # 口语"点击按存储空间"里的"按"是语气助词，不是目标文字
    ("点击按", "点击"),
)
# 只作为点击目标的动词（"点击搜索""点击播放""点击关闭"）
_TAP_TARGET_VERBS = ("搜索", "播放", "返回", "关闭", "退出",
                     "确认", "确定", "取消", "清空")

# 开放式目标（需要执行时自主决策，而非固定点击序列）
_OPEN_GOAL_RE = re.compile(
    r"(获得胜利|获取胜利|获胜|赢得|下赢|赢下|完成一局|赢下这局|"
    r"你自己|自己下|自己玩|自动完成|自主完成|做出最佳|自行决策|自行操作)"
)
# 进入游戏玩法的分界（此后的画面通常是游戏/渲染界面，需交给自主任务）
_INGAME_START_RE = re.compile(
    r"(开始游戏|开始对局|开始对弈|开始下棋|开始比赛|开始战斗|开局)"
)
# 场景切换子句："进入界面后""点开设置之后""点击开始游戏后"
_TRANSITION_RE = re.compile(
    r"(进入|打开|点开|切换到|回到|点击|选择)[^，。；;]{0,8}(之后|以后|后)$"
)
# 只描述切换、没有具体目标的子句（可直接从目标描述中省略）
_TRANSITION_ONLY_RE = re.compile(
    r"(进入|打开|点开|切换到|回到)[^，。；;]{0,4}"
    r"(界面|页面|画面|屏幕|游戏|应用)?(之后|以后|后)$"
)


def _fold_open_goal_clauses(clauses: List[str]) -> List[str]:
    """
    若句子含开放式目标（赢一局/自己下棋…），则把"进入画面后"的所有动作
    合并进同一个自主任务，避免生成无法在渲染画面上执行的固定点击。

    例如：…点击开始游戏，进入界面后再点击开始游戏，进入游戏后获得胜利
      → …点击开始游戏，[点击开始游戏，进入游戏后获得胜利]
    前半段原生界面步骤保留，后半段整体作为 agent_task 目标。
    """
    goal_idx = next(
        (i for i, c in enumerate(clauses) if _OPEN_GOAL_RE.search(c)), -1
    )
    if goal_idx < 0:
        return clauses

    # 优先按"进入界面后"这类场景切换切分；否则按最后一个"开始玩法"切分
    boundary = -1
    for i in range(goal_idx):
        if _TRANSITION_RE.search(clauses[i]):
            boundary = i
    if boundary < 0:
        for i in range(goal_idx):
            if _INGAME_START_RE.search(clauses[i]):
                boundary = i
    # 没有明确分界时保持原样，仅把开放式目标本身作为自主任务
    if boundary < 0:
        return clauses

    # "进入界面后"只是时序说明，本身没有动作，不写进目标描述
    pure_transition = bool(_TRANSITION_ONLY_RE.fullmatch(clauses[boundary].strip()))
    start = boundary + 1 if pure_transition else boundary
    merged = "，".join(clauses[start: goal_idx + 1])
    return clauses[: boundary] + [merged] + clauses[goal_idx + 1:]


# ============================================================
# 用例生成
# ============================================================

def generate_cases(client: AIClient, description: str, devices: list,
                   app_context: Optional[dict] = None,
                   image_b64: str = "",
                   param_context: str = "",
                   param_names: Optional[List[str]] = None
                   ) -> Tuple[List[TestCase], str, str]:
    """
    生成测试用例。

    参数:
        app_context: 真实采集的应用/界面上下文（包名、启动页、可见文字等）
        image_b64: 当前界面截图（画布界面且视觉模型可用时自动附带）
        param_context: 参数化测试上下文（仅传给 LLM，离线生成器不使用）
        param_names: 参数名列表（离线生成时用于注入 {param} 占位符）

    返回: (用例列表, 来源 "llm"/"rule", 说明文字；正常为空串)
    """
    description = (description or "").strip()
    if not description:
        raise ValueError("请输入测试需求描述")
    app_context = app_context or {}

    note = ""
    if client.configured:
        last_error: Optional[Exception] = None
        # LLM 提示词包含参数化上下文
        llm_description = description + param_context if param_context else description
        # 模型偶尔返回不完整 JSON / 空步骤，自动重试一次
        for attempt in range(2):
            try:
                data = client.chat_json(
                    GENERATE_SYSTEM,
                    generate_prompt(llm_description, devices,
                                    app_context=app_context,
                                    vision=client.supports_vision(),
                                    has_screenshot=bool(image_b64)),
                    temperature=0.2 if attempt == 0 else 0.35,
                    timeout=min(float(getattr(client.config, "timeout", 60.0)
                                      or 60.0), 45.0),
                    image_b64=image_b64 or None,
                )
                raw_cases = data.get("test_cases")
                if not isinstance(raw_cases, list):
                    raise AIError("AI 未返回 test_cases 列表")
                cases = [
                    c for c in (_sanitize_case(r, app_context)
                                for r in raw_cases) if c
                ]
                # 忠实度清洗：删掉模型自行添加、用户没要求的截图/断言/空等待
                cases = [_prune_unrequested(c, description) for c in cases]
                cases = [c for c in cases if c.steps]
                if cases:
                    return cases, "llm", ""
                raise AIError("AI 生成结果中没有有效的测试步骤")
            except Exception as e:
                last_error = e
                logger.warning(
                    f"AI 生成用例失败（第 {attempt + 1}/2 次）: {e}")
        note = f"AI 调用失败，已回退内置规则：{last_error}"
    else:
        note = "未配置 API Key，使用内置规则生成（设置 → 偏好设置 → AI）"

    # 离线生成器只使用用户原始描述，但传入参数名以注入占位符
    return _offline_generate(description, devices, app_context,
                             param_names=param_names), "rule", note


def _prune_unrequested(case: TestCase, description: str) -> TestCase:
    """去掉模型自行"脑补"的步骤：未要求就不加截图/断言/前台查询/空等待"""
    wants_shot = bool(re.search(
        r"(截图|截屏|留证|留存|保存.*(画面|屏幕|图片))", description))
    wants_check = bool(re.search(
        r"(校验|检查|验证|确认|断言|是否|前台|正确|异常|正常|成功|失败)",
        description))
    wants_wait = bool(re.search(r"(等待|等到|延迟|等一会|停留)", description))

    assert_types = {
        StepType.ASSERT_VALUE, StepType.ASSERT_RANGE, StepType.ASSERT_STATE,
        StepType.ASSERT_TIMING, StepType.ASSERT_CUSTOM,
    }
    noisy_waits = ("页面加载", "界面响应", "加载完成", "界面切换", "等待响应")

    kept = []
    prev_type = None
    for step in case.steps:
        if step.step_type is StepType.SCREENSHOT and not wants_shot:
            continue
        if step.step_type in assert_types and not wants_check:
            continue
        if (step.step_type is StepType.SEND_ADB
                and "前台" in (step.description or "") and not wants_check):
            continue
        if step.step_type is StepType.WAIT:
            desc = step.description or ""
            if (not wants_wait
                    and prev_type in (StepType.UI_TAP, StepType.UI_SWIPE,
                                      StepType.UI_INPUT)
                    and any(word in desc for word in noisy_waits)):
                continue
            # 连续等待只保留一个
            if prev_type is StepType.WAIT:
                continue
        kept.append(step)
        prev_type = step.step_type

    case.steps = kept
    return case


def _sanitize_case(raw: dict,
                   app_context: Optional[dict] = None) -> Optional[TestCase]:
    """把模型返回的字典清洗为 TestCase"""
    if not isinstance(raw, dict):
        return None
    app_context = app_context or {}

    name = str(raw.get("name") or "").strip()
    if not name:
        name = "AI 生成用例"

    steps = []
    for s in raw.get("steps") or []:
        if not isinstance(s, dict):
            continue
        raw_type = str(s.get("step_type") or "").strip().lower()
        step_type = STEP_TYPE_LOOKUP.get(STEP_TYPE_ALIASES.get(raw_type, raw_type))
        if step_type is None:
            continue
        params = s.get("params")
        if not isinstance(params, dict):
            params = {}
        # 模型有时把参数写在步骤顶层，这里兼容一下
        if step_type in (StepType.UI_TAP, StepType.UI_INPUT,
                         StepType.UI_WAIT_TEXT):
            if not params.get("text") and str(s.get("text") or "").strip():
                params["text"] = str(s["text"]).strip()
        if step_type is StepType.APP_START:
            if not params.get("package") and str(s.get("package") or "").strip():
                params["package"] = str(s["package"]).strip()
            if not (params.get("package") or params.get("pkg")):
                params["package"] = app_context.get("package", "")
            if not params.get("activity") and app_context.get("launch_activity"):
                params["activity"] = app_context["launch_activity"]
        if step_type is StepType.UI_SWIPE:
            direction = str(params.get("direction")
                            or s.get("direction") or "").strip().lower()
            if direction not in ("up", "down", "left", "right"):
                direction = _SWIPE_VERBS.get(
                    str(params.get("direction") or s.get("direction") or ""),
                    "up")
            params["direction"] = direction
            region = str(params.get("region") or s.get("region")
                         or "full").strip().lower()
            params["region"] = _REGION_MAP.get(region, region)
            until = str(params.get("until_text") or "").strip()
            if until:
                params["until_text"] = until
        if step_type is StepType.AGENT_TASK:
            goal = str(params.get("goal") or s.get("goal")
                       or s.get("description") or "").strip()
            if goal:
                params["goal"] = goal
            try:
                params["max_steps"] = int(params.get("max_steps", 40) or 40)
            except (TypeError, ValueError):
                params["max_steps"] = 40

        # 模型偶尔会拿泛指词当界面文字（"等待进入界面"），这类步骤必然失败：
        # 等待改为固定等待，点击改为丢弃
        if step_type is StepType.UI_WAIT_TEXT:
            if _is_generic_text(str(params.get("text") or "")):
                step_type = StepType.WAIT
                params = {"duration": 2.0}
                s = dict(s, description="等待界面响应完成")
        elif step_type is StepType.UI_TAP:
            if params.get("x") is None and _is_generic_text(
                    str(params.get("text") or "")):
                continue

        try:
            timeout = float(s.get("timeout", 30) or 30)
        except (TypeError, ValueError):
            timeout = 30.0
        steps.append(TestStep(
            step_type=step_type,
            description=str(s.get("description") or ""),
            device_id=str(s.get("device_id") or ""),
            params=params,
            expected=s.get("expected"),
            timeout=timeout,
        ))

    if not steps:
        return None

    priority = PRIORITY_LOOKUP.get(str(raw.get("priority") or "P1").upper(), Priority.P1)
    tags = raw.get("tags")
    if not isinstance(tags, list):
        tags = []
    tags = [str(t) for t in tags if str(t).strip()]
    if "AI生成" not in tags:
        tags.append("AI生成")

    # 参数化支持
    parameters = raw.get("parameters", {})
    if not isinstance(parameters, dict):
        parameters = {}
    parameter_sets = raw.get("parameter_sets", [])
    if not isinstance(parameter_sets, list):
        parameter_sets = []

    return TestCase(
        name=name[:60],
        description=str(raw.get("description") or ""),
        category=str(raw.get("category") or "AI 生成"),
        tags=tags,
        priority=priority,
        steps=steps,
        parameters=parameters,
        parameter_sets=parameter_sets,
        timeout=float(raw.get("timeout", 60) or 60),
    )


def _inject_placeholders_into_steps(steps: list, param_names: List[str]):
    """将步骤中的输入文本替换为 {param} 占位符，用于参数化测试。

    策略：
    - ui_input 步骤：text 替换为第一个参数名的占位符（通常是 keyword/search_text）
    - ui_tap/ui_wait_text 步骤：如果描述中包含输入相关文字，也替换
    - 优先选择看起来像搜索关键词的参数名（keyword, search, text, query 等）
    """
    if not param_names:
        return
    # 选择最合适的参数名作为输入占位符
    input_param = param_names[0]
    for name in param_names:
        lower = name.lower()
        if any(k in lower for k in ("keyword", "search", "query", "text", "input", "name")):
            input_param = name
            break

    placeholder = f"{{{input_param}}}"
    for step in steps:
        stype = step.step_type.value if hasattr(step.step_type, 'value') else str(step.step_type)
        if stype == "ui_input":
            # 输入步骤：直接替换 text 为占位符
            step.description = f"输入 {placeholder}"
            if step.params:
                step.params["text"] = placeholder
        elif stype in ("ui_tap", "ui_wait_text"):
            # 点击/等待步骤：如果描述中有「搜索」「输入」相关文字，替换目标为占位符
            desc = step.description or ""
            if any(k in desc for k in ("搜索", "输入", "查找", "查询")):
                # 替换描述中的具体目标文字
                if step.params and "text" in step.params:
                    old_text = step.params["text"]
                    if old_text and old_text not in ("搜索", "输入", "查找", "查询"):
                        step.params["text"] = placeholder
                        step.description = desc.replace(old_text, placeholder)


def _offline_generate(description: str, devices: list,
                      app_context: Optional[dict] = None,
                      param_names: Optional[List[str]] = None) -> List[TestCase]:
    """离线模板生成：先尝试智能执行器，再退回规则解析"""
    app_context = app_context or {}
    adb_id = _pick_adb_device_id(devices)
    package = (str(app_context.get("package") or "").strip()
               or _extract_package(description))
    activity = str(app_context.get("launch_activity") or "").strip()

    # 尝试从应用名称映射中解析包名（支持中文应用名如"媒体中心"）
    if not package:
        app_name_map = app_context.get("app_name_map", {})
        if app_name_map:
            # 从描述中提取第一个应用名称
            app_name = _first_app_name(description)
            if app_name:
                # 精确匹配
                if app_name in app_name_map:
                    package = app_name_map[app_name]
                else:
                    # 模糊匹配
                    app_name_lower = app_name.lower()
                    for name, pkg in app_name_map.items():
                        if (app_name_lower in name.lower() or
                                name.lower() in app_name_lower):
                            package = pkg
                            break
                if package:
                    logger.info(f"解析应用名称 '{app_name}' -> 包名 '{package}'")
                else:
                    logger.debug(f"应用名称 '{app_name}' 在 app_name_map 中未找到，共 {len(app_name_map)} 个映射")

    # 优先使用成熟的规则解析器（_offline_ui_steps），它能处理复杂的口语化描述
    # 包括：代词解析、滑动方向、复合子句、引号文字等
    ui_steps = _offline_ui_steps(description, adb_id, package, activity)
    if ui_steps:
        # 参数化：将输入步骤的文本替换为 {param} 占位符
        if param_names:
            _inject_placeholders_into_steps(ui_steps, param_names)
        app_name = _first_app_name(description)
        title = f"{app_name} 界面流程" if app_name else "界面操作流程"
        detail = description[:200] + "（内置规则生成，语义解析）"
        if package:
            detail += f"；目标应用：{package}"
        logger.info(f"规则解析器生成 {len(ui_steps)} 个步骤")
        return [TestCase(
            name=title[:60],
            description=detail,
            category="AI 生成",
            tags=["AI生成", "离线模板", "界面流程"],
            priority=Priority.P1,
            steps=ui_steps,
        )]

    # 回退到智能执行器（语义理解，适合简单直接的指令）
    try:
        from .intelligent_executor import get_intelligent_executor
        executor = get_intelligent_executor()
        smart_steps = executor.generate(
            description,
            device_id=adb_id,
            package=package,
            activity=activity,
            context=app_context
        )
        if smart_steps:
            if param_names:
                _inject_placeholders_into_steps(smart_steps, param_names)
            app_name = _first_app_name(description)
            title = f"{app_name} 智能流程" if app_name else "智能操作流程"
            detail = description[:200] + "（智能执行器生成，语义理解）"
            if package:
                detail += f"；目标应用：{package}"
            logger.info(f"智能执行器生成 {len(smart_steps)} 个步骤")
            return [TestCase(
                name=title[:60],
                description=detail,
                category="AI 智能生成",
                tags=["AI 生成", "智能执行", "语义理解"],
                priority=Priority.P1,
                steps=smart_steps,
            )]
    except Exception as e:
        logger.warning(f"智能执行器也失败，回退基础模板：{e}")

    is_launch = any(k in description for k in ("启动", "打开", "进入", "launch", "open"))
    is_shot = any(k in description for k in ("截图", "画面", "screenshot"))

    steps: List[TestStep] = []
    name = "AI 冒烟测试"

    if package and is_launch:
        name = f"启动应用 {package}"
        steps.append(TestStep(
            step_type=StepType.SEND_ADB,
            description=f"通过 LAUNCHER 启动 {package}",
            device_id=adb_id,
            params={"command": f"monkey -p {package} -c android.intent.category.LAUNCHER 1"},
            timeout=20,
        ))
        steps.append(TestStep(
            step_type=StepType.WAIT,
            description="等待应用完成启动",
            params={"duration": 3.0},
        ))
        steps.append(TestStep(
            step_type=StepType.SEND_ADB,
            description="查询当前前台 Activity",
            device_id=adb_id,
            params={"command": "dumpsys activity activities | grep mResumedActivity"},
            timeout=15,
        ))
        steps.append(TestStep(
            step_type=StepType.ASSERT_STATE,
            description="校验前台应用为被测应用",
            expected=f"r:.*{re.escape(package)}.*",
        ))
    elif is_shot:
        name = "ADB 截图检查"
        steps.append(TestStep(
            step_type=StepType.SEND_ADB,
            description="查询设备型号，确认连接正常",
            device_id=adb_id,
            params={"command": "getprop ro.product.model"},
            timeout=10,
        ))
        steps.append(TestStep(
            step_type=StepType.WAIT,
            description="等待界面稳定",
            params={"duration": 1.0},
        ))
    else:
        name = "ADB 连接与状态检查"
        steps.append(TestStep(
            step_type=StepType.SEND_ADB,
            description="读取设备型号",
            device_id=adb_id,
            params={"command": "getprop ro.product.model"},
            timeout=10,
        ))
        steps.append(TestStep(
            step_type=StepType.SEND_ADB,
            description="读取系统版本",
            device_id=adb_id,
            params={"command": "getprop ro.build.version.release"},
            timeout=10,
        ))
        steps.append(TestStep(
            step_type=StepType.LOG_MESSAGE,
            description="记录离线模板执行结果",
            params={"message": "离线模板用例执行完成", "level": "INFO"},
        ))

    if is_shot:
        steps.append(TestStep(
            step_type=StepType.SCREENSHOT,
            description="截取当前屏幕",
            device_id=adb_id,
            params={"save_path": "screenshots/ai_smoke.png"},
        ))

    return [TestCase(
        name=name,
        description=description[:200] + "（内置模板生成，未配置 AI 模型）",
        category="AI 生成",
        tags=["AI生成", "离线模板"],
        priority=Priority.P1,
        steps=steps,
    )]


_FILLER_PREFIX_RE = re.compile(
    r"^(请|麻烦|首先|先|然后|接着|接着再|之后|最后|同时|另外|我|我们|帮我|需要|可以|尝试)+"
)


def _split_clauses(text: str) -> List[str]:
    """按标点与连接词切分子句，并去掉无意义的开头词"""
    clauses = []
    for c in _CLAUSE_SPLIT.split(text or ""):
        c = _FILLER_PREFIX_RE.sub("", (c or "").strip()).strip()
        if c:
            clauses.append(c)
    return clauses


def _clean_target(text: str) -> str:
    """去掉目标词里的口语后缀与修饰；引号里的内容按字面保留"""
    text = (text or "").strip(" 　:：,，。；;")
    quoted = _QUOTED_RE.search(text)
    if quoted:
        return quoted.group(1).strip()
    text = re.sub(r"^(一下|这个|那个|这里|那里|切换到|切换|到|至|往)", "", text)
    text = re.sub(r"(上的|中的|里的)", "", text)
    # 只在剩余部分仍然够长时裁掉修饰词，避免把"网络列表"错裁成"网络"
    changed = True
    while changed and len(text) > 2:
        changed = False
        for tail in _TARGET_TAILS:
            if text.endswith(tail) and len(text) - len(tail) >= 2:
                text = text[: -len(tail)].strip()
                changed = True
    # 引号被截断时清理残留的引号字符
    return text.strip("「」""\"'''《》")


def _clean_until(text: str) -> str:
    """"直到出现X" → X；否则返回空"""
    text = (text or "").strip(" 　:：,，。；;")
    match = _UNTIL_PREFIX_RE.match(text)
    if not match:
        return ""
    rest = text[match.end():].strip("「」""\"' 　")
    rest = re.sub(r"(为止|后停止|即可)$", "", rest).strip()
    return rest if _is_meaningful_text(rest) else ""


def _normalize_clause(clause: str) -> str:
    for source, replacement in _NOUN_FIXES:
        clause = clause.replace(source, replacement)
    return clause


def _detect_region(clause: str, verb_start: int) -> str:
    """从滑动动词之前的文字里识别区域"""
    prefix = clause[:verb_start]
    match = None
    for match in _REGION_RE.finditer(prefix):
        pass
    if match:
        return _REGION_MAP.get(match.group(1), "full")
    return "full"


def _match_wait_clause(clause: str) -> Optional[dict]:
    """
    识别整句的"等待/检查"语义，返回
    {"text": 等待文字, "timed": 是否只能定时等待, "duration": 秒}；不是等待句返回 None。
    """
    match = _WAIT_PREFIX_RE.match(clause)
    if not match:
        return None
    rest = clause[match.end():].strip("「」""\"' 　:：,，。；;")
    if not rest:
        return None

    # "查看存储空间""查看内存使用"是导航（点开看），不是等待：
    # 只有带判断语义（是否/正确/异常…）的"查看"才按等待处理。
    if re.match(r"^(请|先)?查看", clause) and not re.search(
            r"(是否|正确|异常|正常|成功|失败|错误|有没有|是否出现)", rest):
        return None

    # "等待出现X后点击Y"：等待只取 X，后半段动作单独解析
    tail = ""
    masked = _QUOTED_RE.sub(lambda m: "　" * len(m.group(0)), rest)
    next_verb = _UI_VERB_RE.search(masked)
    if next_verb:
        tail = rest[next_verb.start():].strip()
        rest = rest[:next_verb.start()]
        rest = _SEQ_SUFFIX_RE.sub("", rest).strip(" 　:：,，。；;")
        if not rest:
            return None

    # "等待X出现"里的"出现"是语气词
    rest = re.sub(r"^(出现|显示|弹出|看到)", "", rest).strip()
    rest = re.sub(r"(出现|显示|弹出|看到)$", "", rest).strip()
    if not rest:
        return None

    def result(text: str, timed: bool, duration: float) -> dict:
        return {"text": text, "timed": timed, "duration": duration, "rest": tail}

    # "等待3秒""等5s" → 固定等待时长
    dur = _DURATION_RE.match(rest)
    if dur:
        value = float(dur.group(1))
        unit = dur.group(2)
        if unit in ("分钟", "分"):
            value *= 60
        elif unit in ("毫秒", "ms"):
            value /= 1000.0
        return result("", True, max(0.5, min(value, 120.0)))

    # "检查版本号是否正确"：能取到具体名词时，先等待该文字出现（存在性校验）
    verdict = re.match(r"(.+?)(?:是否|有没有)", rest)
    if verdict:
        subject = _clean_target(verdict.group(1))
        if _is_meaningful_text(subject):
            return result(subject, False, 0)
        return result("", True, 2.0)
    # "页面切换成功""账号正确"这类语义确认无法用文字匹配，只能定时等待
    if re.search(
            r"(正确|一致|满足|成功|完成|结束|正常|稳定|就绪)$", rest):
        return result("", True, 2.0)
    text = _clean_target(rest)
    if not _is_meaningful_text(text):
        return result("", True, 2.0)
    return result(text, False, 0)


def _last_target(actions: List[Tuple[str, str, dict]]) -> str:
    """从已解析动作里回溯最近的明确目标（用于"点击它"）"""
    for _verb, tgt, extra in reversed(actions):
        candidate = extra.get("until_text") or tgt
        if candidate and candidate not in _PRONOUNS and _is_meaningful_text(candidate):
            return candidate
    return ""


def _parse_ui_actions(description: str) -> List[Tuple[str, str, dict]]:
    """把口语化描述拆成 [(动词, 目标, 附加信息)]"""
    clauses = [_normalize_clause(c) for c in _split_clauses(description)]
    clauses = _fold_open_goal_clauses(clauses)

    actions: List[Tuple[str, str, dict]] = []
    for clause in clauses:

        # 开放式目标（自主对弈/取胜等）：整句交给一个自主任务，不再拆固定点击
        if _OPEN_GOAL_RE.search(clause):
            actions.append(("自主", clause[:80], {"goal": clause}))
            continue

        # "把温度调到26度""设置为25.5℃"：滑杆/数值调节无法用固定点击表达，
        # 交给自主任务（执行时按界面判断拖动/加减），不臆造点击目标
        if re.search(r"(调到|调至|调为|调节到|设置为|设为|设定为|改成)"
                     r"[^\d，。；;]{0,4}\d", clause):
            actions.append(("自主", clause[:80], {"goal": clause}))
            continue

        # "等待登录成功""检查是否出现提示"：整句作为等待/检查
        wait = _match_wait_clause(clause)
        if wait is not None:
            actions.append(("等待", wait["text"], {
                "timed": wait["timed"], "duration": wait["duration"],
            }))
            # "等待X后点击Y"剩下的动作继续解析
            if wait.get("rest"):
                actions.extend(_parse_ui_actions(wait["rest"]))
            continue

        # 引号内的文字是目标而不是指令，扫描动词时先屏蔽
        masked = _QUOTED_RE.sub(lambda m: "　" * len(m.group(0)), clause)
        matches = list(_UI_VERB_RE.finditer(masked))
        i = 0
        while i < len(matches):
            verb = matches[i].group(1)
            end = (matches[i + 1].start()
                   if i + 1 < len(matches) else len(clause))
            target = _clean_target(clause[matches[i].end():end])
            extra: dict = {}

            # "进入界面后""点开设置之后"：后缀表达动作顺序，不是界面文字
            stripped = _SEQ_SUFFIX_RE.sub("", target).strip()
            if stripped != target:
                extra["sequenced"] = True
                target = stripped

            # "点击它/这个/该项"：沿用上一个明确目标
            if target in _PRONOUNS:
                target = _last_target(actions)

            # "点击搜索"里"搜索"被识别成动词，这里把它还原成点击目标
            if not target and verb in _TAP_VERBS and i + 1 < len(matches):
                next_verb = matches[i + 1].group(1)
                if next_verb in _TAP_TARGET_VERBS:
                    target = next_verb
                    i += 1

            if verb in _SWIPE_VERBS:
                extra["direction"] = _SWIPE_VERBS[verb]
                extra["region"] = _detect_region(clause, matches[i].start())
                until = _clean_until(target)
                if until:
                    extra["until_text"] = until
                elif target.startswith(("直到", "直至")):
                    target = ""
            elif verb == "长按":
                extra["long_press"] = True
            elif verb == "双击":
                extra["tap_times"] = 2
            elif verb == "找到" and actions and actions[-1][0] in _SWIPE_VERBS:
                # "上滑，找到系统选项"：目标挂到上一个滑动动作上
                # "找到系统选项并点击"：先滑到它，再点击它
                found = re.sub(r"(并|然后|再|接着)$", "", target).strip(
                    " 　:：,，。；;")
                quoted = _QUOTED_RE.search(found)
                if quoted:
                    found = quoted.group(1).strip()
                next_is_tap = (i + 1 < len(matches)
                               and matches[i + 1].group(1) in _TAP_VERBS)
                if found:
                    actions[-1][2]["until_text"] = found
                if next_is_tap:
                    if found:
                        actions.append(("点击", found, {}))
                    i += 2
                else:
                    i += 1
                continue

            # 截图/返回这类无目标动作
            if verb in ("截图", "截屏"):
                actions.append(("截图", "", {}))
                i += 1
                continue

            # 等待/检查动词："点击开始后等待加载"
            if verb in _CHECK_VERBS:
                text = _clean_target(target)
                if _is_meaningful_text(text):
                    actions.append(("等待", text, {"timed": False}))
                else:
                    actions.append(("等待", "", {"timed": True}))
                i += 1
                continue

            actions.append((verb, target, extra))
            i += 1
    return actions


def _first_app_name(description: str) -> str:
    """从描述中提取被测应用名称（用于用例标题）"""
    for verb, target, _extra in _parse_ui_actions(description):
        if verb in _LAUNCH_VERBS and target \
                and not any(hint in target for hint in _PAGE_HINTS):
            return target
    return ""


def _offline_ui_steps(description: str, adb_id: str, package: str,
                      activity: str) -> List[TestStep]:
    """按界面文字生成可执行的界面流程步骤（离线可用）"""
    actions = _parse_ui_actions(description)
    if not actions:
        return []

    steps: List[TestStep] = []
    started = False

    for verb, target, extra in actions:
        if verb == "自主":
            goal = extra.get("goal") or target or "完成目标"
            steps.append(TestStep(
                step_type=StepType.AGENT_TASK,
                description=f"自主完成任务：{goal[:40]}",
                device_id=adb_id,
                params={"goal": goal, "max_steps": 40},
                timeout=300,
            ))
            continue

        # "等待提示出现""检查是否登录成功"
        if verb == "等待":
            if target and not extra.get("timed"):
                steps.append(TestStep(
                    step_type=StepType.UI_WAIT_TEXT,
                    description=f"等待界面出现「{target}」",
                    device_id=adb_id,
                    params={"text": target},
                    timeout=10,
                ))
            else:
                duration = float(extra.get("duration") or 2.0)
                steps.append(TestStep(
                    step_type=StepType.WAIT,
                    description=f"等待 {duration:g} 秒（界面响应/状态确认）",
                    params={"duration": duration},
                ))
            continue

        # "截图""截图留证"
        if verb == "截图":
            steps.append(TestStep(
                step_type=StepType.SCREENSHOT,
                description="截取当前屏幕",
                device_id=adb_id,
                params={"save_path": "screenshots/ai_ui_flow.png"},
            ))
            continue

        if verb in _SWIPE_VERBS or verb == "找到":
            if verb == "找到":
                # 没有前置滑动的"找到X"当作等待文字
                if _is_meaningful_text(target):
                    steps.append(TestStep(
                        step_type=StepType.UI_WAIT_TEXT,
                        description=f"等待界面出现「{target}」",
                        device_id=adb_id,
                        params={"text": target},
                        timeout=8,
                    ))
                continue
            direction = extra.get("direction", "up")
            region = extra.get("region", "full")
            until_text = extra.get("until_text", "")
            params = {
                "direction": direction,
                "region": region,
                "duration": 400,
            }
            desc = (f"在{_REGION_LABEL.get(region, '屏幕')}"
                    f"{_DIRECTION_LABEL.get(direction, '滑动')}")
            if until_text:
                params["until_text"] = until_text
                params["max_swipes"] = 10
                desc += f"，直到出现「{until_text}」"
            steps.append(TestStep(
                step_type=StepType.UI_SWIPE,
                description=desc,
                device_id=adb_id,
                params=params,
                timeout=25 if until_text else 10,
            ))
            continue

        if verb in _LAUNCH_VERBS:
            if not target:
                continue
            # "进入界面/页面"是泛指，不能拿它去匹配界面文字
            if extra.get("sequenced") or any(h in target for h in _PAGE_HINTS):
                steps.append(TestStep(
                    step_type=StepType.WAIT,
                    description=("等待页面切换"
                                 if not _is_meaningful_text(target)
                                 else f"等待进入「{target}」"),
                    params={"duration": 1.5},
                ))
                continue

            if package and not started:
                params = {"package": package}
                if activity:
                    params["activity"] = activity
                steps.insert(0, TestStep(
                    step_type=StepType.APP_START,
                    description=f"启动应用 {package}",
                    device_id=adb_id,
                    params=params,
                    timeout=20,
                ))
                steps.append(TestStep(
                    step_type=StepType.WAIT,
                    description="等待应用启动完成",
                    params={"duration": 1.5},
                ))
                started = True
            else:
                steps.append(TestStep(
                    step_type=StepType.UI_TAP,
                    description=f"点击「{target}」",
                    device_id=adb_id,
                    params={"text": target},
                    timeout=15,
                ))
            continue

        # "关闭/退出"：带具体目标按目标点击，否则按返回键退出当前页
        if verb in ("关闭", "退出"):
            if _is_meaningful_text(target) and target not in _GENERIC_PAGE_WORDS:
                steps.append(TestStep(
                    step_type=StepType.UI_TAP,
                    description=f"点击「{target}」",
                    device_id=adb_id,
                    params={"text": target},
                    timeout=15,
                ))
            else:
                steps.append(TestStep(
                    step_type=StepType.SEND_ADB,
                    description="返回上一级",
                    device_id=adb_id,
                    params={"command": "input keyevent 4"},
                    timeout=10,
                ))
            continue

        if verb in _TAP_VERBS:
            # 点击类只排除纯泛指词，像"首页""设置"这类页面名本身就是可点目标
            if not target or target in _TAP_BLOCK_WORDS:
                continue
            times = int(extra.get("tap_times", 1) or 1)
            for _ in range(times):
                params = {"text": target}
                if extra.get("long_press"):
                    params["long_press"] = True
                steps.append(TestStep(
                    step_type=StepType.UI_TAP,
                    description=(f"长按「{target}」" if extra.get("long_press")
                                 else f"点击「{target}」"),
                    device_id=adb_id,
                    params=params,
                    timeout=15,
                ))
            continue

        if verb in _INPUT_VERBS:
            if not target:
                continue
            steps.append(TestStep(
                step_type=StepType.UI_INPUT,
                description=f"{verb}「{target}」",
                device_id=adb_id,
                params={
                    "text": target,
                    "clear": True,
                    "submit": verb == "搜索",
                },
                timeout=15,
            ))
            continue

        if verb == "播放":
            if target:
                steps.append(TestStep(
                    step_type=StepType.UI_TAP,
                    description=f"点击播放「{target}」",
                    device_id=adb_id,
                    params={"text": target},
                    timeout=15,
                ))
                steps.append(TestStep(
                    step_type=StepType.WAIT,
                    description="等待开始播放",
                    params={"duration": 1.5},
                ))
            elif not steps or steps[-1].step_type is not StepType.WAIT:
                steps.append(TestStep(
                    step_type=StepType.WAIT,
                    description="等待播放开始",
                    params={"duration": 2.0},
                ))
            continue

        if verb == "返回":
            steps.append(TestStep(
                step_type=StepType.SEND_ADB,
                description="返回上一级",
                device_id=adb_id,
                params={"command": "input keyevent 4"},
                timeout=10,
            ))

    if not steps:
        return []

    # 只做用户明确要求的事：未提到"校验前台/截图"就不追加，
    # 避免自动塞入的断言和截图造成"过度理解"。
    wants_foreground = bool(re.search(
        r"(前台|仍在|依旧在|还在.*应用|保持.*前台|是否.*应用)", description))
    wants_shot = bool(re.search(
        r"(截图|截屏|留证|留存|保存.*(画面|屏幕|图片))", description))

    if package and wants_foreground:
        steps.append(TestStep(
            step_type=StepType.SEND_ADB,
            description="查询当前前台应用",
            device_id=adb_id,
            params={"command": "dumpsys activity activities | grep mResumedActivity"},
            timeout=15,
        ))
        # 流程里若会切到别的应用（例如从游戏中心点「开始游戏」进入游戏本体），
        # 再断言「前台仍为被测应用」必然失败，因此只在应用内流程里断言。
        cross_app = any(s.step_type is StepType.AGENT_TASK for s in steps) or bool(
            re.search(
                r"(开始游戏|启动游戏|进入游戏|打开游戏|跳转到|拨号|打电话|视频通话)",
                description,
            )
        )
        if not cross_app:
            steps.append(TestStep(
                step_type=StepType.ASSERT_STATE,
                description="校验前台仍为被测应用",
                expected=f"r:.*{re.escape(package)}.*",
            ))

    has_shot = any(s.step_type is StepType.SCREENSHOT for s in steps)
    if wants_shot and not has_shot:
        steps.append(TestStep(
            step_type=StepType.SCREENSHOT,
            description="截取当前屏幕",
            device_id=adb_id,
            params={"save_path": "screenshots/ai_ui_flow.png"},
        ))
    return steps


def _pick_adb_device_id(devices: list) -> str:
    for dev in devices or []:
        if str(dev.get("type", "")).upper() == "ADB":
            return str(dev.get("device_id") or "")
    return ""


def _extract_package(text: str) -> Optional[str]:
    match = _PACKAGE_RE.search(text)
    return match.group(1) if match else None


# ============================================================
# 执行规划
# ============================================================

def plan_execution(client: AIClient, cases: List[TestCase],
                   instruction: str = "") -> Tuple[List[TestCase], str, str]:
    """
    生成执行顺序。

    返回: (排序后的用例列表, 排序理由, 来源 "llm"/"rule")
    """
    if not cases:
        return [], "没有可执行的用例", "rule"

    if client.configured:
        try:
            summary = [{
                "case_id": c.case_id,
                "name": c.name,
                "category": c.category,
                "priority": c.priority.value,
                "tags": c.tags,
                "steps": len(c.steps),
                "description": c.description[:80],
            } for c in cases]
            data = client.chat_json(
                PLAN_SYSTEM,
                plan_prompt(summary, instruction),
                temperature=0.1,
                timeout=min(float(getattr(client.config, "timeout", 60.0) or 60.0), 30.0),
            )
            order = data.get("order")
            reason = str(data.get("reason") or "").strip()
            if isinstance(order, list):
                by_id = {c.case_id: c for c in cases}
                ordered = [by_id[cid] for cid in order if cid in by_id]
                seen = {c.case_id for c in ordered}
                ordered += [c for c in _offline_order(cases) if c.case_id not in seen]
                if ordered:
                    return ordered, reason or "AI 已按依赖与优先级规划执行顺序", "llm"
        except Exception as e:
            logger.warning(f"AI 执行规划失败，使用优先级排序: {e}")

    return _offline_order(cases), "未配置 AI 模型，已按优先级（P0→P3）排序", "rule"


def _offline_order(cases: List[TestCase]) -> List[TestCase]:
    return sorted(
        cases,
        key=lambda c: (
            _PRIORITY_ORDER.get(c.priority.value, 9),
            c.category or "",
            c.name,
        ),
    )
