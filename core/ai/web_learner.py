"""
网络学习器
=========

当 AI 遇到不熟悉的应用时，自动从网络搜索规则、玩法，并通过 LLM 总结为结构化知识。

流程：
1. 根据应用名/包名构造搜索查询（如 "扫雷 玩法规则"）
2. 通过搜索引擎（百度/Bing/搜狗）获取 top N 结果
3. 抓取前几个页面的正文
4. 调用 LLM 将内容总结为知识库 schema
5. 保存到知识库供后续使用

特性：
- 多搜索引擎支持（百度、Bing、搜狗）
- 搜索结果缓存（避免重复请求）
- 自动重试机制（指数退避）
- 代理支持
"""

import html
import re
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple
from functools import wraps

from loguru import logger

from core.ai.client import AIClient, AIError
from core.ai.knowledge import KnowledgeBase, get_knowledge_base


# ---------- 缓存和重试机制 ----------

_search_cache: Dict[str, Tuple[float, List[Dict[str, str]]]] = {}
CACHE_TTL = 3600  # 缓存有效期：1小时

def _cached_search(func):
    """搜索结果缓存装饰器"""
    @wraps(func)
    def wrapper(query: str, num_results: int = 5, **kwargs):
        cache_key = f"{func.__name__}:{query}:{num_results}"
        
        # 检查缓存
        if cache_key in _search_cache:
            timestamp, results = _search_cache[cache_key]
            if time.time() - timestamp < CACHE_TTL:
                logger.debug(f"使用缓存的搜索结果: {query}")
                return results
        
        # 执行搜索
        results = func(query, num_results, **kwargs)
        
        # 保存缓存
        if results:
            _search_cache[cache_key] = (time.time(), results)
        
        return results
    return wrapper


def _retry_with_backoff(max_retries: int = 3, base_delay: float = 1.0):
    """重试装饰器（指数退避）"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.debug(f"{func.__name__} 失败，{delay:.1f}秒后重试 ({attempt+1}/{max_retries}): {e}")
                        time.sleep(delay)
            logger.warning(f"{func.__name__} 在 {max_retries} 次重试后仍然失败: {last_exception}")
            return [] if 'search' in func.__name__ else None
        return wrapper
    return decorator


def clear_search_cache():
    """清除搜索缓存"""
    global _search_cache
    _search_cache.clear()
    logger.info("搜索缓存已清除")


# ---------- HTML 文本提取 ----------

def _strip_html_tags(text: str) -> str:
    """去除 HTML 标签，保留文本"""
    # 去掉 script/style 块
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # 去掉所有标签
    text = re.sub(r'<[^>]+>', ' ', text)
    # 解码 HTML 实体
    text = html.unescape(text)
    # 压缩空白
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _extract_visible_text(html_content: str) -> str:
    """从 HTML 中提取可见文本（简化版，不依赖 bs4）"""
    return _strip_html_tags(html_content)


# ---------- 网络搜索 ----------

@_cached_search
@_retry_with_backoff(max_retries=2, base_delay=1.0)
def search_baidu(query: str, num_results: int = 5) -> List[Dict[str, str]]:
    """
    百度搜索，返回 [{title, url, snippet}, ...]
    尝试多个百度接口以提高成功率。
    """
    # 尝试多个百度搜索接口
    search_urls = [
        # 桌面版
        f"https://www.baidu.com/s?wd={urllib.parse.quote(query)}&rn={num_results}",
        # 移动版（限制可能更松）
        f"https://m.baidu.com/s?word={urllib.parse.quote(query)}&rn={num_results}",
    ]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }
    
    for search_url in search_urls:
        try:
            req = urllib.request.Request(search_url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                # 处理 gzip 编码
                raw_bytes = resp.read()
                if resp.headers.get('Content-Encoding') == 'gzip':
                    import gzip
                    raw_bytes = gzip.decompress(raw_bytes)
                raw = raw_bytes.decode("utf-8", errors="replace")
                
                # 检查是否被拦截（安全验证页面）
                if "百度安全验证" in raw or "网络不给力" in raw:
                    logger.debug(f"百度接口被拦截: {search_url[:50]}")
                    continue
                
                # 解析搜索结果
                results = _parse_baidu_results(raw, num_results)
                if results:
                    logger.info(f"百度搜索 '{query}' 成功，返回 {len(results)} 条结果")
                    return results
        except Exception as e:
            logger.debug(f"百度搜索失败 {search_url[:50]}: {e}")
            continue
    
    # 所有百度接口都失败，尝试 Bing 作为后备
    logger.warning("百度搜索全部失败，尝试 Bing 后备")
    bing_results = search_bing(query, num_results)
    if bing_results:
        return bing_results
    
    # Bing 也失败，尝试搜狗
    logger.warning("Bing 搜索失败，尝试搜狗后备")
    return search_sogou(query, num_results)


def _parse_baidu_results(html: str, num_results: int) -> List[Dict[str, str]]:
    """解析百度搜索结果页面"""
    results = []
    
    # 百度搜索结果通常在 <div class="result c-container ..."> 中
    # 标题在 <h3 class="t"> 或 <h3 class="c-title"> 中
    # 尝试多种模式匹配
    
    # 模式1: 提取 <h3> 标签中的标题和链接
    h3_pattern = re.compile(
        r'<h3[^>]*class="[^"]*(?:t|c-title)[^"]*"[^>]*>\s*'
        r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    
    for m in h3_pattern.finditer(html):
        link_url = m.group(1)
        title_html = m.group(2)
        title = _strip_html_tags(title_html).strip()
        
        if not title or len(title) < 4:
            continue
        
        # 提取摘要（通常在标题后的 <div class="c-abstract"> 或类似标签中）
        start = m.end()
        snippet_html = html[start:start + 1500]
        
        # 尝试提取摘要
        abstract_match = re.search(
            r'<(?:div|span)[^>]*class="[^"]*(?:c-abstract|content-right)[^"]*"[^>]*>(.*?)</(?:div|span)>',
            snippet_html,
            re.DOTALL | re.IGNORECASE,
        )
        if abstract_match:
            snippet = _strip_html_tags(abstract_match.group(1)).strip()[:200]
        else:
            # 退化：取后面 200 字符的文本
            snippet = _strip_html_tags(snippet_html).strip()[:200]
        
        results.append({
            "title": title,
            "url": link_url,
            "snippet": snippet,
        })
        
        if len(results) >= num_results:
            break
    
    # 模式2: 如果模式1没结果，尝试更宽松的链接提取
    if not results:
        link_pattern = re.compile(
            r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )
        for m in link_pattern.finditer(html):
            link_url = m.group(1)
            title_html = m.group(2)
            title = _strip_html_tags(title_html).strip()
            
            # 过滤条件
            if not title or len(title) < 6:
                continue
            if "baidu.com" in link_url:
                continue
            if any(skip in title for skip in ["百度", "搜索", "登录", "注册"]):
                continue
            
            # 提取摘要
            start = m.end()
            snippet_html = html[start:start + 800]
            snippet = _strip_html_tags(snippet_html).strip()[:200]
            
            results.append({
                "title": title,
                "url": link_url,
                "snippet": snippet,
            })
            
            if len(results) >= num_results:
                break
    
    return results


@_cached_search
@_retry_with_backoff(max_retries=2, base_delay=1.0)
def search_bing(query: str, num_results: int = 5) -> List[Dict[str, str]]:
    """
    Bing 搜索作为后备方案，返回 [{title, url, snippet}, ...]
    """
    url = f"https://cn.bing.com/search?q={urllib.parse.quote(query)}&count={num_results}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning(f"Bing 搜索失败: {e}")
        return []
    
    results = []
    # Bing 结果通常在 <li class="b_algo"> 中
    # 标题在 <h2><a href="...">标题</a></h2>
    pattern = re.compile(
        r'<li[^>]*class="b_algo"[^>]*>.*?'
        r'<h2><a[^>]+href="([^"]+)"[^>]*>(.*?)</a></h2>.*?'
        r'<p[^>]*>(.*?)</p>',
        re.DOTALL | re.IGNORECASE,
    )
    
    for m in pattern.finditer(raw):
        link_url = m.group(1)
        title = _strip_html_tags(m.group(2)).strip()
        snippet = _strip_html_tags(m.group(3)).strip()[:200]
        
        if title and len(title) >= 4:
            results.append({
                "title": title,
                "url": link_url,
                "snippet": snippet,
            })
        
        if len(results) >= num_results:
            break
    
    logger.debug(f"Bing 搜索 '{query}' 返回 {len(results)} 条结果")
    return results


@_cached_search
@_retry_with_backoff(max_retries=2, base_delay=1.0)
def search_sogou(query: str, num_results: int = 5) -> List[Dict[str, str]]:
    """
    搜狗搜索作为第三后备方案，返回 [{title, url, snippet}, ...]
    """
    url = f"https://www.sogou.com/web?query={urllib.parse.quote(query)}&num={num_results}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning(f"搜狗搜索失败: {e}")
        return []
    
    results = []
    # 搜狗结果通常在 <div class="vrwrap"> 或 <div class="rb"> 中
    # 标题在 <h3><a href="...">标题</a></h3>
    pattern = re.compile(
        r'<div[^>]*class="[^"]*(?:vrwrap|rb)[^"]*"[^>]*>.*?'
        r'<h3[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?</h3>.*?'
        r'<(?:div|p)[^>]*class="[^"]*(?:str_info|star-wiki|fz)[^"]*"[^>]*>(.*?)</(?:div|p)>',
        re.DOTALL | re.IGNORECASE,
    )
    
    for m in pattern.finditer(raw):
        link_url = m.group(1)
        title = _strip_html_tags(m.group(2)).strip()
        snippet = _strip_html_tags(m.group(3)).strip()[:200]
        
        if title and len(title) >= 4:
            results.append({
                "title": title,
                "url": link_url,
                "snippet": snippet,
            })
        
        if len(results) >= num_results:
            break
    
    # 如果正则匹配失败，尝试更宽松的匹配
    if not results:
        link_pattern = re.compile(
            r'<h3[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )
        for m in link_pattern.finditer(raw):
            link_url = m.group(1)
            title = _strip_html_tags(m.group(2)).strip()
            
            if title and len(title) >= 4 and "sogou.com" not in link_url:
                # 提取摘要
                start = m.end()
                snippet_html = raw[start:start + 800]
                snippet = _strip_html_tags(snippet_html).strip()[:200]
                
                results.append({
                    "title": title,
                    "url": link_url,
                    "snippet": snippet,
                })
            
            if len(results) >= num_results:
                break
    
    logger.debug(f"搜狗搜索 '{query}' 返回 {len(results)} 条结果")
    return results


def fetch_page_text(url: str, max_chars: int = 8000) -> Optional[str]:
    """
    抓取页面并提取纯文本，最多 max_chars 字符。
    失败返回 None。
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read()
        # 尝试多种编码
        content = None
        for enc in ("utf-8", "gbk", "gb2312", "latin-1"):
            try:
                content = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if content is None:
            content = raw.decode("utf-8", errors="replace")
        text = _extract_visible_text(content)
        # 截断
        if len(text) > max_chars:
            text = text[:max_chars] + "..."
        return text
    except Exception as e:
        logger.debug(f"抓取页面失败 {url}: {e}")
        return None


# ---------- LLM 总结 ----------

_SUMMARIZE_SYSTEM = """你是一个知识提取助手。你的任务是从网页内容中提取关于某个应用/游戏的关键知识，并以 JSON 格式输出。

输出格式：
{
  "name": "应用/游戏的中文名称",
  "rules": ["核心规则1", "核心规则2", ...],
  "controls": ["操作方式1", "操作方式2", ...],
  "win_condition": "胜利条件（如有）",
  "lose_condition": "失败条件（如有）",
  "ui_elements": {
    "元素名称": "元素描述/位置"
  },
  "notes": "其他重要信息"
}

要求：
- 只提取与目标应用直接相关的内容
- rules 和 controls 用简洁的中文短句
- 如果没有明确信息，对应字段留空或省略
- 不要编造内容，只从提供的文本中提取"""


def _summarize_with_llm(
    client: AIClient,
    app_name: str,
    content_pieces: List[str],
) -> Optional[Dict[str, Any]]:
    """调用 LLM 将抓取的内容总结为结构化知识"""
    combined = "\n\n---\n\n".join(content_pieces)
    # 截断避免超长
    if len(combined) > 12000:
        combined = combined[:12000] + "\n...(内容已截断)"

    user_prompt = f"""以下是关于应用「{app_name}」的网页内容，请提取关键知识并按 JSON 格式输出。

内容：
{combined}

请输出 JSON："""

    try:
        result = client.chat_json(_SUMMARIZE_SYSTEM, user_prompt, temperature=0.1, timeout=60)
        return result
    except AIError as e:
        logger.warning(f"LLM 总结失败: {e}")
        return None


# ---------- 主流程 ----------

def learn_about_app(
    app_name: str,
    package_name: str,
    client: AIClient,
    kb: Optional[KnowledgeBase] = None,
    extra_query: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    从网络学习某个应用的知识并保存到知识库。

    参数：
      app_name: 应用显示名称（如 "扫雷"）
      package_name: 包名（如 "com.wt.sweepmine"）
      client: AI 客户端
      kb: 知识库实例（默认全局）
      extra_query: 额外搜索词（如 "高级玩法"）

    返回：学到的知识字典，失败返回 None
    """
    if kb is None:
        kb = get_knowledge_base()

    logger.info(f"开始从网络学习「{app_name}」({package_name}) 的知识...")

    # 构造搜索查询
    queries = [
        f"{app_name} 玩法规则",
        f"{app_name} 操作说明",
    ]
    if extra_query:
        queries.append(f"{app_name} {extra_query}")

    # 搜索并抓取
    all_sources: List[str] = []
    content_pieces: List[str] = []

    for q in queries:
        results = search_baidu(q, num_results=3)
        for r in results:
            url = r["url"]
            if url in all_sources:
                continue
            all_sources.append(url)
            # 抓取正文
            text = fetch_page_text(url, max_chars=4000)
            if text and len(text) > 100:
                content_pieces.append(f"[来源: {r['title']}]\n{text}")
            # 控制抓取数量，避免过慢
            if len(content_pieces) >= 5:
                break
        if len(content_pieces) >= 5:
            break

    if not content_pieces:
        logger.warning(f"未找到关于「{app_name}」的有效内容")
        return None

    logger.info(f"抓取到 {len(content_pieces)} 篇内容，正在用 LLM 总结...")

    # LLM 总结
    structured = _summarize_with_llm(client, app_name, content_pieces)
    if not structured:
        return None

    # 补充元数据
    structured["package"] = package_name
    structured["sources"] = all_sources[:10]

    # 保存到知识库（合并模式，不覆盖已有）
    merged = kb.merge(package_name, structured)
    logger.info(f"✅ 学习完成「{app_name}」：{len(merged.get('rules', []))} 条规则，"
                f"{len(merged.get('controls', []))} 种操作，来源 {len(all_sources)} 个")

    return merged


def get_or_learn(
    app_name: str,
    package_name: str,
    client: AIClient,
    kb: Optional[KnowledgeBase] = None,
    force_refresh: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    获取某应用的知识：已有则直接返回，否则触发网络学习。

    参数：
      force_refresh: 强制重新学习（即使已有知识）
    """
    if kb is None:
        kb = get_knowledge_base()

    existing = kb.load(package_name)
    if existing and not force_refresh:
        logger.debug(f"使用已有知识: {package_name} (v{existing.get('version', 0)})")
        return existing

    return learn_about_app(app_name, package_name, client, kb)
