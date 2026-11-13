"""
知识库管理器
===========

存储和管理 AI 学习到的应用规则、玩法、UI 元素等知识。
每个应用（按 package name）对应一个 YAML 文件，存储在 knowledge/ 目录下。

知识 schema:
{
  "name": "应用名称",
  "package": "com.example.app",
  "version": 1,
  "learned_at": "2026-10-30T15:00:00",
  "rules": ["规则1", "规则2"],
  "controls": ["操作方式1", "操作方式2"],
  "win_condition": "胜利条件描述",
  "lose_condition": "失败条件描述",
  "ui_elements": {
    "element_name": {"description": "...", "location": "..."}
  },
  "sources": ["https://..."],
  "notes": "备注"
}
"""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from loguru import logger


class KnowledgeBase:
    """应用知识库"""

    def __init__(self, base_dir: Optional[str] = None):
        if base_dir is None:
            # 默认在项目根目录下的 knowledge/
            base_dir = str(Path(__file__).parent.parent.parent / "knowledge")
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, Dict[str, Any]] = {}

    def _path_for(self, package: str) -> Path:
        """按 package name 返回文件路径"""
        # 将 com.wt.sweepmine 转为 com.wt.sweepmine.yaml
        safe = re.sub(r'[^\w\-.]', '_', package)
        return self._base_dir / f"{safe}.yaml"

    def load(self, package: str) -> Optional[Dict[str, Any]]:
        """加载指定应用的知识，不存在返回 None"""
        if package in self._cache:
            return self._cache[package]
        path = self._path_for(package)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self._cache[package] = data
            logger.debug(f"加载知识库: {package} (v{data.get('version', 0)})")
            return data
        except Exception as e:
            logger.error(f"读取知识库失败 {package}: {e}")
            return None

    def save(self, package: str, data: Dict[str, Any]) -> bool:
        """保存知识到文件"""
        # 自动填充元数据
        if "package" not in data:
            data["package"] = package
        if "version" not in data:
            data["version"] = 1
        if "learned_at" not in data:
            data["learned_at"] = datetime.now().isoformat(timespec="seconds")
        path = self._path_for(package)
        try:
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            self._cache[package] = data
            logger.info(f"保存知识库: {package} → {path}")
            return True
        except Exception as e:
            logger.error(f"写入知识库失败 {package}: {e}")
            return False

    def merge(self, package: str, new_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        合并新知识到现有知识。
        - 列表字段（rules, controls, sources）：追加不重复项
        - 字典字段（ui_elements）：合并键值
        - 标量字段（win_condition, notes）：新值覆盖旧值（除非旧值为空）
        - version 自增
        """
        existing = self.load(package) or {}
        merged = dict(existing)

        # 列表字段追加
        for key in ("rules", "controls", "sources"):
            old_list = merged.get(key, []) or []
            new_list = new_data.get(key, []) or []
            combined = list(old_list)
            for item in new_list:
                if item not in combined:
                    combined.append(item)
            merged[key] = combined

        # 字典字段合并
        for key in ("ui_elements",):
            old_dict = merged.get(key, {}) or {}
            new_dict = new_data.get(key, {}) or {}
            old_dict.update(new_dict)
            merged[key] = old_dict

        # 标量字段覆盖（新值非空时）
        for key in ("name", "win_condition", "lose_condition", "notes"):
            new_val = new_data.get(key)
            if new_val:
                merged[key] = new_val

        # 版本自增
        merged["version"] = merged.get("version", 0) + 1
        merged["learned_at"] = datetime.now().isoformat(timespec="seconds")

        self.save(package, merged)
        return merged

    def has(self, package: str) -> bool:
        """是否已有该应用的知识"""
        return self.load(package) is not None

    def get(self, package: str) -> Dict[str, Any]:
        """获取知识，不存在返回空字典"""
        return self.load(package) or {}

    def list_apps(self) -> List[Dict[str, Any]]:
        """列出所有已学习的应用"""
        apps = []
        for path in self._base_dir.glob("*.yaml"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                apps.append({
                    "package": data.get("package", path.stem),
                    "name": data.get("name", "未知"),
                    "version": data.get("version", 0),
                    "learned_at": data.get("learned_at", ""),
                    "rules_count": len(data.get("rules", [])),
                })
            except Exception as e:
                logger.warning(f"跳过损坏的知识文件 {path}: {e}")
        return sorted(apps, key=lambda x: x["package"])

    def delete(self, package: str) -> bool:
        """删除指定应用的知识"""
        path = self._path_for(package)
        if not path.exists():
            return False
        try:
            path.unlink()
            self._cache.pop(package, None)
            logger.info(f"删除知识库: {package}")
            return True
        except Exception as e:
            logger.error(f"删除知识库失败 {package}: {e}")
            return False

    def search(self, query: str) -> List[Dict[str, Any]]:
        """简单关键字搜索（在 name, rules, notes 中查找）"""
        results = []
        query_lower = query.lower()
        for app in self.list_apps():
            package = app["package"]
            data = self.get(package)
            text = " ".join([
                data.get("name", ""),
                " ".join(data.get("rules", [])),
                data.get("notes", ""),
            ]).lower()
            if query_lower in text:
                results.append(data)
        return results


# 全局单例
_kb_instance: Optional[KnowledgeBase] = None


def get_knowledge_base() -> KnowledgeBase:
    """获取全局知识库实例"""
    global _kb_instance
    if _kb_instance is None:
        _kb_instance = KnowledgeBase()
    return _kb_instance
