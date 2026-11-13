"""
AI 客户端
==========
OpenAI 兼容的聊天补全接口封装（零第三方依赖，使用 urllib）。

支持任意兼容 /v1/chat/completions 的服务，例如 DeepSeek、通义千问、
Moonshot、OpenAI、本地 Ollama（/v1 兼容模式）等。
"""

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from loguru import logger


class AIError(Exception):
    """AI 调用异常"""


def parse_json_object(text: str) -> Dict[str, Any]:
    """从模型输出中提取 JSON 对象（容忍 Markdown 代码块与前后说明文字）"""
    if not text:
        raise AIError("AI 返回内容为空")

    cleaned = text.strip()
    if cleaned.startswith("```"):
        # 去掉 ```json ... ``` 包裹
        lines = cleaned.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # 退化路径：截取第一个完整的大括号块
    start = cleaned.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(cleaned[start:i + 1])
                        if isinstance(data, dict):
                            return data
                    except json.JSONDecodeError:
                        break
    raise AIError("无法解析 AI 返回的 JSON")


class AIClient:
    """OpenAI 兼容聊天客户端"""

    def __init__(self, config):
        self._config = config
        # 图像请求被判不支持后，本次运行不再尝试视觉，自动改用离线 OCR
        self._vision_broken = False

    @property
    def config(self):
        return self._config

    @property
    def configured(self) -> bool:
        return bool(self._config and str(getattr(self._config, "api_key", "")).strip())

    # 常见多模态模型名关键字（用于自动识别是否支持图像输入）
    _VISION_HINTS = (
        "vl", "vision", "4o", "omni", "gemini", "claude-3", "claude-4",
        "glm-4v", "glm-4.5v", "qwen-vl", "internvl", "llava", "minicpm-v",
        "pixtral", "step-1v", "yi-vl", "mistral-small-3", "gpt-4.1",
    )
    # 各服务商的默认视觉模型：用户没有单独填写视觉模型时自动选用
    _PROVIDER_VISION_MODELS = (
        ("dashscope.aliyuncs.com", "qwen-vl-max"),
        ("aliyuncs.com", "qwen-vl-max"),
        ("open.bigmodel.cn", "glm-4v-plus"),
        ("api.openai.com", "gpt-4o"),
        ("api.moonshot.cn", "moonshot-v1-8k-vision-preview"),
        ("generativelanguage.googleapis.com", "gemini-2.0-flash"),
        ("api.siliconflow.cn", "Qwen/Qwen2.5-VL-72B-Instruct"),
    )

    @classmethod
    def _looks_vision_model(cls, model: str) -> bool:
        model = (model or "").lower()
        return any(h in model for h in cls._VISION_HINTS)

    def _vision_api_key(self) -> str:
        """视觉接口的 Key（未单独配置时沿用主 Key）"""
        return (str(getattr(self._config, "vision_api_key", "") or "").strip()
                or str(getattr(self._config, "api_key", "") or "").strip())

    def _provider_default_vision_model(self) -> str:
        """按接口地址推断服务商默认视觉模型（自动配置用）"""
        if not self.configured:
            return ""
        # 优先按视觉接口地址推断，否则按主接口
        url = (str(getattr(self._config, "vision_base_url", "") or "").strip()
               or str(getattr(self._config, "base_url", "") or "")).lower()
        for host, model in self._PROVIDER_VISION_MODELS:
            if host in url:
                return model
        return ""

    def vision_model_name(self) -> str:
        """
        当前实际用于读屏的视觉模型（自动推导）：
        1. 单独配置的 vision_model
        2. 主模型本身是多模态模型
        3. 按服务商推断的默认视觉模型
        """
        explicit = str(getattr(self._config, "vision_model", "") or "").strip()
        if explicit:
            return explicit
        model = str(getattr(self._config, "model", "") or "").strip()
        if self._looks_vision_model(model):
            return model
        return self._provider_default_vision_model()

    def supports_vision(self) -> bool:
        """是否把截图喂给模型（自动探测；失败后自动降级）"""
        if self._vision_broken:
            return False
        if getattr(self._config, "vision", False):
            # 用户显式要求启用：即使模型名识别不出来也尝试，失败会自动降级
            return bool(self._vision_api_key())
        return bool(self.vision_model_name() and self._vision_api_key())

    def vision_endpoint(self) -> tuple:
        """视觉请求使用的 (url, api_key)，支持独立视觉服务"""
        base = (str(getattr(self._config, "vision_base_url", "") or "").strip()
                or str(getattr(self._config, "base_url", "") or ""))
        return base.rstrip("/") + "/chat/completions", self._vision_api_key()

    @staticmethod
    def _is_image_unsupported(error: Exception) -> bool:
        text = str(error).lower()
        return any(k in text for k in (
            "image", "vision", "multimodal", "content type",
            "not support", "unsupported", "invalid_request_error",
        ))

    def chat(self, system: str, user: str, temperature: float = 0.2,
             json_mode: bool = False,
             timeout: Optional[float] = None,
             image_b64: Optional[str] = None) -> str:
        """发起一次对话，返回助手文本；image_b64 为 data:image/png;base64 或纯 base64"""
        if not self.configured:
            raise AIError("未配置 AI 模型 API Key")

        url = str(self._config.base_url).rstrip("/") + "/chat/completions"
        api_key = str(self._config.api_key)
        user_content: Any = user
        model = self._config.model
        if image_b64 and self.supports_vision():
            if not image_b64.startswith("data:"):
                image_b64 = f"data:image/png;base64,{image_b64}"
            user_content = [
                {"type": "text", "text": user},
                {"type": "image_url", "image_url": {"url": image_b64}},
            ]
            # 自动选用视觉模型（可能是独立服务商的接口与 Key）
            model = self.vision_model_name() or model
            url, api_key = self.vision_endpoint()
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "temperature": temperature,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            data = self._post(url, payload, timeout or self._config.timeout,
                              api_key=api_key)
        except AIError as e:
            if image_b64 and self._is_image_unsupported(e):
                self._vision_broken = True
                logger.warning(
                    f"视觉模型 {model} 不支持图像输入，本次运行将自动改用离线 OCR：{e}"
                )
            raise
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise AIError("AI 接口返回结构异常，缺少 choices/message 字段")

    def chat_json(self, system: str, user: str, temperature: float = 0.1,
                  timeout: Optional[float] = None,
                  image_b64: Optional[str] = None) -> Dict[str, Any]:
        """对话并解析为 JSON 对象"""
        text = self.chat(
            system, user, temperature=temperature, json_mode=True,
            timeout=timeout, image_b64=image_b64,
        )
        return parse_json_object(text)

    # ---------- 内部实现 ----------

    def _post(self, url: str, payload: dict, timeout: float,
              api_key: Optional[str] = None) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        key = (api_key or "").strip() or str(self._config.api_key)
        req.add_header("Authorization", f"Bearer {key}")

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            # 部分兼容服务不支持 response_format，去掉后重试一次
            if e.code == 400 and "response_format" in payload:
                payload.pop("response_format", None)
                return self._post(url, payload, timeout, api_key=key)
            raise AIError(f"AI 接口返回错误 {e.code}: {detail or e.reason}")
        except urllib.error.URLError as e:
            raise AIError(f"无法连接 AI 接口: {e.reason}")
        except Exception as e:
            raise AIError(f"AI 请求失败: {e}")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug(f"AI 非 JSON 响应: {raw[:200]}")
            raise AIError("AI 接口返回非 JSON 数据")

        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise AIError(f"AI 接口错误: {msg}")
        return data
