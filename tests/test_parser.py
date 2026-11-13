"""
测试用例解析器 - 单元测试
"""

import pytest
import tempfile
import json
import yaml
from pathlib import Path

from core.testcase.parser import TestCaseParser, ParseResult
from core.testcase.model import TestCase, TestStep, StepType, Priority


class TestCaseParserYAML:
    """YAML 解析测试"""

    def test_parse_simple_yaml(self, tmp_path):
        """测试解析简单 YAML 用例"""
        yaml_content = """
test_cases:
  - name: "测试用例1"
    description: "描述"
    category: "分类A"
    priority: "P0"
    steps:
      - step_type: wait
        description: "等待1秒"
        params:
          duration: 1.0
"""
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        result = TestCaseParser.parse_file(str(yaml_file))
        assert result.success
        assert len(result.test_cases) == 1
        assert result.test_cases[0].name == "测试用例1"
        assert result.test_cases[0].priority == Priority.P0

    def test_parse_yaml_with_parameters(self, tmp_path):
        """测试参数化用例解析"""
        yaml_content = """
- name: "参数化测试"
  parameter_sets:
    - speed: 0
    - speed: 50
    - speed: 100
  steps:
    - step_type: wait
      params:
        duration: 0.1
"""
        yaml_file = tmp_path / "param_test.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        result = TestCaseParser.parse_file(str(yaml_file))
        assert result.success
        # 参数化用例应展开为 3 个
        assert len(result.test_cases) == 3


class TestCaseParserJSON:
    """JSON 解析测试"""

    def test_parse_json(self, tmp_path):
        """测试 JSON 格式解析"""
        data = [
            {
                "name": "JSON测试",
                "category": "网络",
                "priority": "P1",
                "steps": [
                    {"step_type": "send_network", "params": {"data": "hello"}}
                ]
            }
        ]
        json_file = tmp_path / "test.json"
        json_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        result = TestCaseParser.parse_file(str(json_file))
        assert result.success
        assert len(result.test_cases) == 1
        assert result.test_cases[0].name == "JSON测试"


class TestCaseParserCSV:
    """CSV 解析测试"""

    def test_parse_csv(self, tmp_path):
        """测试 CSV 格式解析"""
        csv_content = """用例名称,描述,分类,优先级,步骤类型,步骤描述,步骤参数,预期结果,超时
CSV测试1,描述1,音频,P1,wait,等待,{},OK,10
CSV测试1,描述1,音频,P1,send_serial,发送,data=hello,,10
CSV测试2,描述2,导航,P0,wait,等待,{},OK,5
"""
        csv_file = tmp_path / "test.csv"
        csv_file.write_text(csv_content, encoding="utf-8-sig")

        result = TestCaseParser.parse_file(str(csv_file))
        assert len(result.test_cases) == 2
        # CSV测试1 应有 2 个步骤
        case1 = [c for c in result.test_cases if c.name == "CSV测试1"][0]
        assert len(case1.steps) == 2


class TestCaseExport:
    """导出测试"""

    def test_export_yaml(self, tmp_path):
        """测试 YAML 导出"""
        cases = [
            TestCase(name="导出测试1", category="测试"),
            TestCase(name="导出测试2", category="测试"),
        ]
        output = tmp_path / "export.yaml"
        success = TestCaseParser.export_yaml(cases, str(output))
        assert success
        assert output.exists()

        # 验证导出内容可重新解析
        result = TestCaseParser.parse_file(str(output))
        assert result.success
        assert len(result.test_cases) == 2

    def test_export_json(self, tmp_path):
        """测试 JSON 导出"""
        cases = [TestCase(name="JSON导出测试")]
        output = tmp_path / "export.json"
        success = TestCaseParser.export_json(cases, str(output))
        assert success
        assert output.exists()


class TestParseErrors:
    """错误处理测试"""

    def test_file_not_found(self):
        """测试文件不存在"""
        result = TestCaseParser.parse_file("/nonexistent/file.yaml")
        assert not result.success
        assert len(result.errors) > 0

    def test_unsupported_format(self, tmp_path):
        """测试不支持的格式"""
        file = tmp_path / "test.xyz"
        file.write_text("data")
        result = TestCaseParser.parse_file(str(file))
        assert not result.success

    def test_invalid_yaml(self, tmp_path):
        """测试无效 YAML"""
        file = tmp_path / "bad.yaml"
        file.write_text("{{invalid yaml: [", encoding="utf-8")
        result = TestCaseParser.parse_file(str(file))
        assert not result.success
