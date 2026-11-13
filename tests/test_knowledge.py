"""
知识库系统单元测试
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.ai.knowledge import KnowledgeBase, get_knowledge_base


def test_knowledge_base_basic():
    """测试知识库基本功能"""
    print("=== 测试知识库基本功能 ===")
    
    # 创建临时目录
    with tempfile.TemporaryDirectory() as tmpdir:
        kb = KnowledgeBase(tmpdir)
        
        # 测试保存
        test_data = {
            "name": "测试应用",
            "package": "com.test.app",
            "rules": ["规则1", "规则2"],
            "controls": ["操作1"],
            "win_condition": "胜利条件",
        }
        
        assert kb.save("com.test.app", test_data), "保存失败"
        assert kb.has("com.test.app"), "保存后应该存在"
        
        # 测试加载
        loaded = kb.load("com.test.app")
        assert loaded is not None, "加载失败"
        assert loaded["name"] == "测试应用", "名称不匹配"
        assert len(loaded["rules"]) == 2, "规则数量不匹配"
        
        # 测试合并
        new_data = {
            "rules": ["规则3"],  # 新规则
            "notes": "备注信息",
        }
        merged = kb.merge("com.test.app", new_data)
        assert len(merged["rules"]) == 3, "合并后规则数量错误"
        assert "规则3" in merged["rules"], "新规则未添加"
        assert merged["notes"] == "备注信息", "备注未添加"
        assert merged["version"] == 2, "版本号未自增"
        
        # 测试列表
        apps = kb.list_apps()
        assert len(apps) == 1, "应用列表数量错误"
        assert apps[0]["package"] == "com.test.app", "包名不匹配"
        
        # 测试删除
        assert kb.delete("com.test.app"), "删除失败"
        assert not kb.has("com.test.app"), "删除后仍应不存在"
        
        print("✅ 基本功能测试通过")


def test_knowledge_base_search():
    """测试知识库搜索功能"""
    print("\n=== 测试知识库搜索功能 ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        kb = KnowledgeBase(tmpdir)
        
        # 添加多个应用
        kb.save("com.test.gomoku", {
            "name": "五子棋",
            "package": "com.test.gomoku",
            "rules": ["五子连珠获胜"],
            "notes": "棋类游戏",
        })
        
        kb.save("com.test.minesweeper", {
            "name": "扫雷",
            "package": "com.test.minesweeper",
            "rules": ["避开地雷"],
            "notes": "益智游戏",
        })
        
        # 测试搜索
        results = kb.search("五子棋")
        assert len(results) == 1, "搜索结果数量错误"
        assert results[0]["name"] == "五子棋", "搜索结果不匹配"
        
        results = kb.search("游戏")
        assert len(results) == 2, "应该搜索到两个游戏"
        
        results = kb.search("不存在")
        assert len(results) == 0, "不应该搜索到结果"
        
        print("✅ 搜索功能测试通过")


def test_knowledge_base_persistence():
    """测试知识库持久化"""
    print("\n=== 测试知识库持久化 ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建并保存
        kb1 = KnowledgeBase(tmpdir)
        kb1.save("com.test.app", {
            "name": "测试",
            "package": "com.test.app",
            "rules": ["规则1"],
        })
        
        # 重新加载
        kb2 = KnowledgeBase(tmpdir)
        loaded = kb2.load("com.test.app")
        assert loaded is not None, "重新加载失败"
        assert loaded["name"] == "测试", "数据不一致"
        
        print("✅ 持久化测试通过")


def test_global_knowledge_base():
    """测试全局知识库实例"""
    print("\n=== 测试全局知识库实例 ===")
    
    kb1 = get_knowledge_base()
    kb2 = get_knowledge_base()
    
    assert kb1 is kb2, "全局实例应该是同一个"
    
    print("✅ 全局实例测试通过")


if __name__ == "__main__":
    try:
        test_knowledge_base_basic()
        test_knowledge_base_search()
        test_knowledge_base_persistence()
        test_global_knowledge_base()
        print("\n✅ 所有知识库测试通过！")
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
