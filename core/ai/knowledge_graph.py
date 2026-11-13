"""
知识图谱构建器
==============
构建应用界面和功能的知识图谱，实现知识迁移和推理。

能力：
1. 界面元素关系 - 理解按钮、文本、图标之间的空间和功能关系
2. 应用流程建模 - 建模应用的典型使用流程
3. 知识迁移 - 从一个应用学到的知识迁移到类似应用
4. 推理能力 - 基于图谱推理未知关系
"""

import json
from typing import Any, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from pathlib import Path
from collections import defaultdict
from loguru import logger


@dataclass
class UIElement:
    """UI 元素节点"""
    element_id: str
    element_type: str  # button, text, icon, input, etc.
    text: str = ""
    resource_id: str = ""
    location: Tuple[int, int] = (0, 0)
    package: str = ""
    activity: str = ""
    
    # 关系
    children: List[str] = field(default_factory=list)  # 子元素 ID
    parent: Optional[str] = None  # 父元素 ID
    next_elements: List[str] = field(default_factory=list)  # 流程上的下一个元素
    related_elements: List[str] = field(default_factory=list)  # 相关元素


@dataclass
class AppFlow:
    """应用流程"""
    flow_id: str
    name: str
    description: str
    steps: List[Dict[str, Any]] = field(default_factory=list)
    success_rate: float = 0.0
    common_issues: List[str] = field(default_factory=list)
    
    # 流程元素
    start_element: str = ""  # 起始元素 ID
    end_element: str = ""  # 结束元素 ID
    elements: List[str] = field(default_factory=list)  # 涉及的所有元素 ID


@dataclass
class KnowledgeGraph:
    """知识图谱"""
    package: str
    elements: Dict[str, UIElement] = field(default_factory=dict)
    flows: Dict[str, AppFlow] = field(default_factory=dict)
    
    # 统计信息
    total_executions: int = 0
    successful_executions: int = 0
    
    # 学习到的模式
    element_patterns: Dict[str, List[str]] = field(default_factory=lambda: defaultdict(list))
    flow_patterns: Dict[str, List[str]] = field(default_factory=lambda: defaultdict(list))


class KnowledgeGraphBuilder:
    """知识图谱构建器"""

    def __init__(self, storage_dir: Optional[str] = None):
        if storage_dir is None:
            storage_dir = str(Path.home() / ".vehicle_test_app" / "knowledge_graphs")
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        
        # 图谱缓存
        self._graphs: Dict[str, KnowledgeGraph] = {}

    def _graph_path(self, package: str) -> Path:
        """图谱文件路径"""
        safe_name = package.replace(".", "_")
        return self._storage_dir / f"{safe_name}.json"

    def get_or_create_graph(self, package: str) -> KnowledgeGraph:
        """获取或创建应用图谱"""
        if package in self._graphs:
            return self._graphs[package]
        
        # 尝试加载
        path = self._graph_path(package)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                graph = self._deserialize_graph(data)
                self._graphs[package] = graph
                logger.debug(f"加载知识图谱: {package}")
                return graph
            except Exception as e:
                logger.warning(f"加载图谱失败: {e}")
        
        # 创建新图谱
        graph = KnowledgeGraph(package=package)
        self._graphs[package] = graph
        return graph

    def _deserialize_graph(self, data: Dict[str, Any]) -> KnowledgeGraph:
        """反序列化图谱"""
        graph = KnowledgeGraph(
            package=data.get("package", ""),
            total_executions=data.get("total_executions", 0),
            successful_executions=data.get("successful_executions", 0),
        )
        
        # 反序列化元素
        for elem_id, elem_data in data.get("elements", {}).items():
            graph.elements[elem_id] = UIElement(
                element_id=elem_id,
                element_type=elem_data.get("type", ""),
                text=elem_data.get("text", ""),
                resource_id=elem_data.get("resource_id", ""),
                location=tuple(elem_data.get("location", (0, 0))),
                package=elem_data.get("package", ""),
                activity=elem_data.get("activity", ""),
                children=elem_data.get("children", []),
                parent=elem_data.get("parent"),
                next_elements=elem_data.get("next_elements", []),
                related_elements=elem_data.get("related_elements", []),
            )
        
        # 反序列化流程
        for flow_id, flow_data in data.get("flows", {}).items():
            graph.flows[flow_id] = AppFlow(
                flow_id=flow_id,
                name=flow_data.get("name", ""),
                description=flow_data.get("description", ""),
                steps=flow_data.get("steps", []),
                success_rate=flow_data.get("success_rate", 0.0),
                common_issues=flow_data.get("common_issues", []),
                start_element=flow_data.get("start_element", ""),
                end_element=flow_data.get("end_element", ""),
                elements=flow_data.get("elements", []),
            )
        
        return graph

    def save_graph(self, package: str) -> bool:
        """保存图谱"""
        if package not in self._graphs:
            return False
        
        graph = self._graphs[package]
        data = self._serialize_graph(graph)
        
        try:
            path = self._graph_path(package)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"保存知识图谱: {package}")
            return True
        except Exception as e:
            logger.error(f"保存图谱失败: {e}")
            return False

    def _serialize_graph(self, graph: KnowledgeGraph) -> Dict[str, Any]:
        """序列化图谱"""
        data = {
            "package": graph.package,
            "total_executions": graph.total_executions,
            "successful_executions": graph.successful_executions,
            "elements": {},
            "flows": {},
        }
        
        # 序列化元素
        for elem_id, elem in graph.elements.items():
            data["elements"][elem_id] = {
                "type": elem.element_type,
                "text": elem.text,
                "resource_id": elem.resource_id,
                "location": list(elem.location),
                "package": elem.package,
                "activity": elem.activity,
                "children": elem.children,
                "parent": elem.parent,
                "next_elements": elem.next_elements,
                "related_elements": elem.related_elements,
            }
        
        # 序列化流程
        for flow_id, flow in graph.flows.items():
            data["flows"][flow_id] = {
                "name": flow.name,
                "description": flow.description,
                "steps": flow.steps,
                "success_rate": flow.success_rate,
                "common_issues": flow.common_issues,
                "start_element": flow.start_element,
                "end_element": flow.end_element,
                "elements": flow.elements,
            }
        
        return data

    def add_ui_element(
        self,
        package: str,
        element_type: str,
        text: str = "",
        resource_id: str = "",
        location: Tuple[int, int] = (0, 0),
        activity: str = "",
    ) -> str:
        """添加 UI 元素"""
        graph = self.get_or_create_graph(package)
        
        # 生成元素 ID
        elem_id = f"{element_type}_{text or resource_id}_{len(graph.elements)}"
        
        # 创建元素
        element = UIElement(
            element_id=elem_id,
            element_type=element_type,
            text=text,
            resource_id=resource_id,
            location=location,
            package=package,
            activity=activity,
        )
        
        graph.elements[elem_id] = element
        
        # 记录模式
        if text:
            graph.element_patterns[text].append(elem_id)
        
        return elem_id

    def add_element_relationship(
        self,
        package: str,
        from_elem: str,
        to_elem: str,
        relationship: str = "next",
    ) -> None:
        """添加元素关系"""
        graph = self.get_or_create_graph(package)
        
        if from_elem not in graph.elements or to_elem not in graph.elements:
            return
        
        from_element = graph.elements[from_elem]
        to_element = graph.elements[to_elem]
        
        if relationship == "next":
            if to_elem not in from_element.next_elements:
                from_element.next_elements.append(to_elem)
        elif relationship == "related":
            if to_elem not in from_element.related_elements:
                from_element.related_elements.append(to_elem)
        elif relationship == "parent":
            from_element.parent = to_elem
            if from_elem not in to_element.children:
                to_element.children.append(from_elem)

    def add_app_flow(
        self,
        package: str,
        name: str,
        description: str,
        steps: List[Dict[str, Any]],
        start_element: str = "",
        end_element: str = "",
    ) -> str:
        """添加应用流程"""
        graph = self.get_or_create_graph(package)
        
        # 生成流程 ID
        flow_id = f"flow_{name}_{len(graph.flows)}"
        
        # 创建流程
        flow = AppFlow(
            flow_id=flow_id,
            name=name,
            description=description,
            steps=steps,
            start_element=start_element,
            end_element=end_element,
            elements=[s.get("element_id", "") for s in steps if s.get("element_id")],
        )
        
        graph.flows[flow_id] = flow
        
        # 记录模式
        graph.flow_patterns[name].append(flow_id)
        
        return flow_id

    def update_flow_success_rate(
        self,
        package: str,
        flow_id: str,
        success: bool,
    ) -> None:
        """更新流程成功率"""
        graph = self.get_or_create_graph(package)
        
        if flow_id not in graph.flows:
            return
        
        flow = graph.flows[flow_id]
        graph.total_executions += 1
        
        if success:
            graph.successful_executions += 1
            # 更新流程成功率（指数移动平均）
            if flow.success_rate == 0:
                flow.success_rate = 1.0
            else:
                flow.success_rate = 0.7 * flow.success_rate + 0.3 * 1.0
        else:
            # 更新流程成功率
            if flow.success_rate == 0:
                flow.success_rate = 0.0
            else:
                flow.success_rate = 0.7 * flow.success_rate + 0.3 * 0.0

    def find_similar_elements(
        self,
        package: str,
        text: str,
        element_type: Optional[str] = None,
    ) -> List[UIElement]:
        """查找相似元素"""
        graph = self.get_or_create_graph(package)
        similar = []
        
        for elem in graph.elements.values():
            if element_type and elem.element_type != element_type:
                continue
            
            # 文本相似度
            if text in elem.text or elem.text in text:
                similar.append(elem)
            # resource_id 相似度
            elif elem.resource_id and any(
                token in elem.resource_id.lower()
                for token in text.lower().split()
            ):
                similar.append(elem)
        
        return similar

    def predict_next_element(
        self,
        package: str,
        current_element: str,
    ) -> List[Tuple[str, float]]:
        """预测下一个元素"""
        graph = self.get_or_create_graph(package)
        
        if current_element not in graph.elements:
            return []
        
        element = graph.elements[current_element]
        predictions = []
        
        # 基于流程的预测
        for next_elem_id in element.next_elements:
            if next_elem_id in graph.elements:
                # 计算置信度（基于历史成功率）
                confidence = 0.5  # 基础置信度
                
                # 查找包含这两个元素的流程
                for flow in graph.flows.values():
                    if current_element in flow.elements and next_elem_id in flow.elements:
                        confidence = max(confidence, flow.success_rate)
                
                predictions.append((next_elem_id, confidence))
        
        # 按置信度排序
        predictions.sort(key=lambda x: x[1], reverse=True)
        return predictions[:5]  # 返回前 5 个预测

    def get_app_understanding(self, package: str) -> Dict[str, Any]:
        """获取应用理解"""
        graph = self.get_or_create_graph(package)
        
        return {
            "package": package,
            "total_elements": len(graph.elements),
            "total_flows": len(graph.flows),
            "success_rate": (
                graph.successful_executions / graph.total_executions
                if graph.total_executions > 0 else 0.0
            ),
            "common_elements": [
                {"text": text, "count": len(ids)}
                for text, ids in sorted(
                    graph.element_patterns.items(),
                    key=lambda x: len(x[1]),
                    reverse=True,
                )[:10]
            ],
            "common_flows": [
                {"name": name, "count": len(ids)}
                for name, ids in sorted(
                    graph.flow_patterns.items(),
                    key=lambda x: len(x[1]),
                    reverse=True,
                )[:5]
            ],
        }

    def transfer_knowledge(
        self,
        source_package: str,
        target_package: str,
    ) -> int:
        """
        知识迁移：从源应用迁移知识到目标应用
        
        返回迁移的元素数量
        """
        source_graph = self.get_or_create_graph(source_package)
        target_graph = self.get_or_create_graph(target_package)
        
        migrated_count = 0
        
        # 迁移元素（基于文本匹配）
        for source_elem in source_graph.elements.values():
            if not source_elem.text:
                continue
            
            # 在目标应用中查找相似元素
            similar = self.find_similar_elements(
                target_package,
                source_elem.text,
                source_elem.element_type,
            )
            
            if not similar:
                # 没有相似元素，创建新元素
                new_elem_id = self.add_ui_element(
                    target_package,
                    source_elem.element_type,
                    source_elem.text,
                    activity=source_elem.activity,
                )
                migrated_count += 1
                
                # 复制关系
                if source_elem.parent:
                    parent_text = source_graph.elements[source_elem.parent].text
                    if parent_text:
                        parent_similar = self.find_similar_elements(
                            target_package, parent_text
                        )
                        if parent_similar:
                            self.add_element_relationship(
                                target_package,
                                new_elem_id,
                                parent_similar[0].element_id,
                                "parent",
                            )
        
        logger.info(f"知识迁移完成: {source_package} → {target_package}, 迁移 {migrated_count} 个元素")
        return migrated_count


# 全局单例
_graph_builder: Optional[KnowledgeGraphBuilder] = None


def get_knowledge_graph_builder() -> KnowledgeGraphBuilder:
    """获取全局图谱构建器实例"""
    global _graph_builder
    if _graph_builder is None:
        _graph_builder = KnowledgeGraphBuilder()
    return _graph_builder
