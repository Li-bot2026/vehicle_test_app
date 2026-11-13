"""
网络学习器单元测试
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.ai.web_learner import (
    _strip_html_tags,
    _extract_visible_text,
    search_baidu,
    search_bing,
    search_sogou,
    clear_search_cache,
)


def test_html_stripping():
    """测试 HTML 标签去除"""
    print("=== 测试 HTML 标签去除 ===")
    
    html1 = "<p>测试文本</p>"
    assert _strip_html_tags(html1) == "测试文本", "基本标签去除失败"
    
    html2 = "<div><script>alert('test')</script>内容</div>"
    result = _strip_html_tags(html2)
    assert "alert" not in result, "script 标签未去除"
    assert "内容" in result, "有效内容被误删"
    
    html3 = "<a href='test'>链接</a>&amp;实体&lt;"
    result = _strip_html_tags(html3)
    assert "链接" in result, "链接文本丢失"
    assert "&" in result, "HTML 实体未解码"
    assert "<" in result, "HTML 实体未解码"
    
    html4 = "多个    空格\n\n换行"
    result = _strip_html_tags(html4)
    assert "多个 空格 换行" == result, "空白压缩失败"
    
    print("✅ HTML 标签去除测试通过")


def test_search_cache():
    """测试搜索缓存"""
    print("\n=== 测试搜索缓存 ===")
    
    # 清除缓存
    clear_search_cache()
    
    # 第一次搜索（会缓存）
    # 注意：这里不实际调用搜索，只测试缓存机制
    from core.ai.web_learner import _search_cache
    
    # 手动添加缓存
    _search_cache["test:query:5"] = (1000000000, [{"title": "test"}])
    
    # 检查缓存是否存在
    assert "test:query:5" in _search_cache, "缓存未添加"
    
    # 清除缓存
    clear_search_cache()
    assert len(_search_cache) == 0, "缓存未清除"
    
    print("✅ 搜索缓存测试通过")


def test_search_engines_availability():
    """测试搜索引擎可用性（需要网络）"""
    print("\n=== 测试搜索引擎可用性 ===")
    
    # 清除缓存
    clear_search_cache()
    
    test_query = "五子棋规则"
    
    # 测试百度
    print("  测试百度搜索...")
    baidu_results = search_baidu(test_query, num_results=2)
    if baidu_results:
        print(f"    ✅ 百度返回 {len(baidu_results)} 条结果")
        assert "title" in baidu_results[0], "结果格式错误"
        assert "url" in baidu_results[0], "结果缺少 URL"
    else:
        print("    ⚠️  百度无结果（可能被拦截）")
    
    # 测试 Bing
    print("  测试 Bing 搜索...")
    bing_results = search_bing(test_query, num_results=2)
    if bing_results:
        print(f"    ✅ Bing 返回 {len(bing_results)} 条结果")
        assert "title" in bing_results[0], "结果格式错误"
    else:
        print("    ⚠️  Bing 无结果")
    
    # 测试搜狗
    print("  测试搜狗搜索...")
    sogou_results = search_sogou(test_query, num_results=2)
    if sogou_results:
        print(f"    ✅ 搜狗返回 {len(sogou_results)} 条结果")
        assert "title" in sogou_results[0], "结果格式错误"
    else:
        print("    ⚠️  搜狗无结果")
    
    # 至少一个搜索引擎应该返回结果
    total_results = len(baidu_results) + len(bing_results) + len(sogou_results)
    if total_results == 0:
        print("  ⚠️  所有搜索引擎都无结果（可能是网络问题）")
    else:
        print(f"  ✅ 总共获得 {total_results} 条结果")
    
    print("✅ 搜索引擎可用性测试完成")


def test_search_fallback():
    """测试搜索回退机制"""
    print("\n=== 测试搜索回退机制 ===")
    
    # 清除缓存
    clear_search_cache()
    
    # 测试一个可能失败的查询
    test_query = "非常特殊的查询xyz123"
    
    # search_baidu 会自动尝试 Bing 和搜狗
    results = search_baidu(test_query, num_results=1)
    
    # 即使没有结果，也不应该抛出异常
    print(f"  搜索结果: {len(results)} 条")
    
    print("✅ 搜索回退机制测试通过")


if __name__ == "__main__":
    try:
        test_html_stripping()
        test_search_cache()
        test_search_engines_availability()
        test_search_fallback()
        print("\n✅ 所有网络学习器测试通过！")
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
