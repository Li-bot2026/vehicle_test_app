"""
测试用例解析器
===============
支持从 Excel / CSV / JSON / YAML 格式导入测试用例。

设计说明：
- 使用统一接口 parse_file() 自动识别文件格式
- 每种格式有独立的解析方法
- 支持参数化用例的展开（数据驱动）
- 解析错误会收集并在最后统一报告
"""

import csv
import json
from pathlib import Path
from typing import List, Optional, Union
from dataclasses import dataclass

import yaml
from loguru import logger

from .model import TestCase, TestStep, TestSuite, StepType, Priority


@dataclass
class ParseResult:
    """解析结果"""
    success: bool
    test_cases: List[TestCase] = None
    errors: List[str] = None
    warnings: List[str] = None

    def __post_init__(self):
        if self.test_cases is None:
            self.test_cases = []
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []


class TestCaseParser:
    """
    测试用例解析器
    ---------------
    支持 Excel (.xlsx/.xls)、CSV、JSON、YAML 格式。
    """

    # 支持的步骤类型映射
    STEP_TYPE_MAP = {
        "send_serial": StepType.SEND_SERIAL,
        "send_network": StepType.SEND_NETWORK,
        "send_adb": StepType.SEND_ADB,
        "wait": StepType.WAIT,
        "wait_signal": StepType.WAIT_SIGNAL,
        "assert_value": StepType.ASSERT_VALUE,
        "assert_range": StepType.ASSERT_RANGE,
        "assert_state": StepType.ASSERT_STATE,
        "assert_timing": StepType.ASSERT_TIMING,
        "assert_custom": StepType.ASSERT_CUSTOM,
        "screenshot": StepType.SCREENSHOT,
        "log_message": StepType.LOG_MESSAGE,
    }

    PRIORITY_MAP = {
        "P0": Priority.P0, "P1": Priority.P1,
        "P2": Priority.P2, "P3": Priority.P3,
        "p0": Priority.P0, "p1": Priority.P1,
        "p2": Priority.P2, "p3": Priority.P3,
    }

    # ---------- 统一入口 ----------

    @staticmethod
    def parse_file(file_path: str) -> ParseResult:
        """
        自动识别文件格式并解析。

        参数:
            file_path: 文件路径

        返回:
            ParseResult 包含解析结果和错误信息
        """
        path = Path(file_path)
        if not path.exists():
            return ParseResult(success=False, errors=[f"文件不存在: {file_path}"])

        suffix = path.suffix.lower()
        try:
            if suffix in (".xlsx", ".xls"):
                return TestCaseParser._parse_excel(path)
            elif suffix == ".csv":
                return TestCaseParser._parse_csv(path)
            elif suffix == ".json":
                return TestCaseParser._parse_json(path)
            elif suffix in (".yaml", ".yml"):
                return TestCaseParser._parse_yaml(path)
            else:
                return ParseResult(
                    success=False,
                    errors=[f"不支持的文件格式: {suffix}"]
                )
        except Exception as e:
            logger.error(f"解析文件失败: {file_path} - {e}")
            return ParseResult(success=False, errors=[str(e)])

    # ---------- YAML 解析 ----------

    @staticmethod
    def _parse_yaml(path: Path) -> ParseResult:
        """解析 YAML 格式的测试用例"""
        errors = []
        warnings = []
        cases = []

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if not data:
                return ParseResult(success=False, errors=["YAML 文件为空"])

            # 支持两种格式：
            # 1. 直接是用例列表
            # 2. 包含 suite 信息的套件格式
            if "test_cases" in data:
                raw_cases = data["test_cases"]
            elif isinstance(data, list):
                raw_cases = data
            else:
                raw_cases = [data]

            for i, raw in enumerate(raw_cases):
                try:
                    case = TestCaseParser._dict_to_case(raw)
                    if case:
                        # 展开参数化用例
                        if case.parameter_sets:
                            expanded = TestCaseParser._expand_parameters(case)
                            cases.extend(expanded)
                            warnings.append(
                                f"用例 '{case.name}' 展开为 {len(expanded)} 个参数化实例"
                            )
                        else:
                            cases.append(case)
                except Exception as e:
                    errors.append(f"第 {i + 1} 个用例解析失败: {e}")

            return ParseResult(
                success=len(errors) == 0,
                test_cases=cases,
                errors=errors,
                warnings=warnings
            )

        except yaml.YAMLError as e:
            return ParseResult(success=False, errors=[f"YAML 语法错误: {e}"])

    # ---------- JSON 解析 ----------

    @staticmethod
    def _parse_json(path: Path) -> ParseResult:
        """解析 JSON 格式的测试用例"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            errors = []
            cases = []

            if isinstance(data, list):
                raw_cases = data
            elif isinstance(data, dict):
                raw_cases = data.get("test_cases", [data])
            else:
                return ParseResult(success=False, errors=["JSON 格式不正确"])

            for i, raw in enumerate(raw_cases):
                try:
                    case = TestCaseParser._dict_to_case(raw)
                    if case:
                        cases.append(case)
                except Exception as e:
                    errors.append(f"第 {i + 1} 个用例解析失败: {e}")

            return ParseResult(
                success=len(errors) == 0,
                test_cases=cases,
                errors=errors
            )

        except json.JSONDecodeError as e:
            return ParseResult(success=False, errors=[f"JSON 语法错误: {e}"])

    # ---------- CSV 解析 ----------

    @staticmethod
    def _parse_csv(path: Path) -> ParseResult:
        """
        解析 CSV 格式的测试用例。

        CSV 列定义：
        用例名称 | 描述 | 分类 | 优先级 | 步骤类型 | 步骤参数 | 预期结果 | 超时
        """
        errors = []
        cases = []

        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)

                # 按用例名称分组合并步骤
                case_map = {}
                for row_num, row in enumerate(reader, start=2):
                    try:
                        name = row.get("用例名称", "").strip()
                        if not name:
                            errors.append(f"第 {row_num} 行: 用例名称为空")
                            continue

                        if name not in case_map:
                            case = TestCase(
                                name=name,
                                description=row.get("描述", ""),
                                category=row.get("分类", ""),
                                priority=TestCaseParser.PRIORITY_MAP.get(
                                    row.get("优先级", "P1"), Priority.P1
                                ),
                                tags=[t.strip() for t in row.get("标签", "").split(",") if t.strip()],
                            )
                            case_map[name] = case

                        # 添加步骤
                        step_type_str = row.get("步骤类型", "wait").strip().lower()
                        step_type = TestCaseParser.STEP_TYPE_MAP.get(
                            step_type_str, StepType.WAIT
                        )

                        step = TestStep(
                            step_type=step_type,
                            description=row.get("步骤描述", ""),
                            params=TestCaseParser._parse_params_str(
                                row.get("步骤参数", "")
                            ),
                            expected=row.get("预期结果", ""),
                            timeout=float(row.get("超时", 30)),
                        )
                        case_map[name].steps.append(step)

                    except Exception as e:
                        errors.append(f"第 {row_num} 行解析失败: {e}")

                cases = list(case_map.values())

            return ParseResult(
                success=len(errors) == 0,
                test_cases=cases,
                errors=errors
            )

        except Exception as e:
            return ParseResult(success=False, errors=[f"CSV 解析失败: {e}"])

    # ---------- Excel 解析 ----------

    @staticmethod
    def _parse_excel(path: Path) -> ParseResult:
        """解析 Excel 格式的测试用例"""
        try:
            from openpyxl import load_workbook
        except ImportError:
            return ParseResult(
                success=False,
                errors=["openpyxl 未安装，无法解析 Excel 文件"]
            )

        errors = []
        cases = []

        try:
            wb = load_workbook(str(path), read_only=True, data_only=True)

            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                rows = list(ws.iter_rows(values_only=True))
                if len(rows) < 2:
                    continue

                # 第一行为表头
                headers = [str(h).strip() if h else "" for h in rows[0]]

                # 查找关键列索引
                col_map = {}
                key_names = ["用例名称", "描述", "分类", "优先级", "标签",
                             "步骤类型", "步骤描述", "步骤参数", "预期结果", "超时"]
                for key in key_names:
                    if key in headers:
                        col_map[key] = headers.index(key)

                if "用例名称" not in col_map:
                    # 报告导出的概览页/失败详情页不含用例列，跳过即可，不当作错误
                    if sheet_name in ("测试概览", "失败详情", "时间线"):
                        continue
                    errors.append(f"工作表 '{sheet_name}' 缺少 '用例名称' 列")
                    continue

                # 按用例名称分组合并
                case_map = {}
                for row_num, row in enumerate(rows[1:], start=2):
                    try:
                        name = str(row[col_map["用例名称"]] or "").strip()
                        if not name:
                            continue

                        if name not in case_map:
                            case = TestCase(
                                name=name,
                                description=str(row[col_map.get("描述", -1)] or "") if "描述" in col_map else "",
                                category=str(row[col_map.get("分类", -1)] or "") if "分类" in col_map else "",
                                priority=TestCaseParser.PRIORITY_MAP.get(
                                    str(row[col_map.get("优先级", -1)] or "P1"), Priority.P1
                                ) if "优先级" in col_map else Priority.P1,
                            )
                            case_map[name] = case

                        # 添加步骤
                        if "步骤类型" in col_map:
                            step_type_str = str(row[col_map["步骤类型"]] or "wait").strip().lower()
                            step = TestStep(
                                step_type=TestCaseParser.STEP_TYPE_MAP.get(step_type_str, StepType.WAIT),
                                description=str(row[col_map.get("步骤描述", -1)] or "") if "步骤描述" in col_map else "",
                                params=TestCaseParser._parse_params_str(
                                    str(row[col_map.get("步骤参数", -1)] or "")
                                ) if "步骤参数" in col_map else {},
                                expected=str(row[col_map.get("预期结果", -1)] or "") if "预期结果" in col_map else None,
                                timeout=float(row[col_map["超时"]] or 30) if "超时" in col_map else 30.0,
                            )
                            case_map[name].steps.append(step)

                    except Exception as e:
                        errors.append(f"工作表 '{sheet_name}' 第 {row_num} 行: {e}")

                cases.extend(case_map.values())

            wb.close()
            return ParseResult(
                success=len(errors) == 0,
                test_cases=cases,
                errors=errors
            )

        except Exception as e:
            return ParseResult(success=False, errors=[f"Excel 解析失败: {e}"])

    # ---------- 辅助方法 ----------

    @staticmethod
    def _dict_to_case(data: dict) -> Optional[TestCase]:
        """将字典转换为 TestCase 对象"""
        if not data:
            return None

        case = TestCase(
            case_id=data.get("case_id", data.get("id", "")),
            name=data.get("name", ""),
            description=data.get("description", ""),
            category=data.get("category", ""),
            tags=data.get("tags", []),
            priority=TestCaseParser.PRIORITY_MAP.get(
                data.get("priority", "P1"), Priority.P1
            ),
            retry_count=data.get("retry_count"),
            retry_delay=data.get("retry_delay", 1.0),
            timeout=data.get("timeout", 60.0),
            author=data.get("author", ""),
            version=data.get("version", "1.0"),
            parameters=data.get("parameters", {}),
            parameter_sets=data.get("parameter_sets", []),
        )

        # 解析步骤
        for step_data in data.get("setup_steps", []):
            case.setup_steps.append(TestCaseParser._dict_to_step(step_data))

        for step_data in data.get("steps", []):
            case.steps.append(TestCaseParser._dict_to_step(step_data))

        for step_data in data.get("teardown_steps", []):
            case.teardown_steps.append(TestCaseParser._dict_to_step(step_data))

        return case

    @staticmethod
    def _dict_to_step(data: dict) -> TestStep:
        """将字典转换为 TestStep 对象"""
        step_type_str = data.get("step_type", data.get("type", "wait"))
        return TestStep(
            step_id=data.get("step_id", ""),
            step_type=TestCaseParser.STEP_TYPE_MAP.get(step_type_str, StepType.WAIT),
            description=data.get("description", ""),
            device_id=data.get("device_id", ""),
            params=data.get("params", {}),
            expected=data.get("expected"),
            tolerance=data.get("tolerance", 0.0),
            timeout=data.get("timeout", 30.0),
        )

    @staticmethod
    def _parse_params_str(params_str: str) -> dict:
        """
        解析参数字符串。
        支持格式: "key1=value1;key2=value2" 或 JSON 格式
        """
        if not params_str or not params_str.strip():
            return {}

        params_str = params_str.strip()

        # 尝试 JSON 格式
        if params_str.startswith("{"):
            try:
                return json.loads(params_str)
            except json.JSONDecodeError:
                pass

        # 键值对格式
        result = {}
        for pair in params_str.split(";"):
            pair = pair.strip()
            if "=" in pair:
                key, value = pair.split("=", 1)
                result[key.strip()] = value.strip()
        return result

    @staticmethod
    def _expand_parameters(case: TestCase) -> List[TestCase]:
        """
        展开参数化用例为多个实例（UI 显示用）。
        命名规则与执行引擎保持一致，使用稳定 ID 以保留勾选状态。
        """
        expanded = []
        for i, param_set in enumerate(case.parameter_sets):
            # 优先用 case_id 或 keyword 命名，否则用序号
            case_id = param_set.get("case_id", "")
            keyword = param_set.get("keyword", "")
            description = param_set.get("description", "")
            if case_id and description:
                child_name = f"[{case_id}] {description}"
            elif case_id:
                child_name = f"[{case_id}] {keyword or case_id}"
            elif keyword:
                child_name = f"{case.name} - {keyword}"
            else:
                child_name = f"{case.name} [{i + 1}]"

            # 描述中包含参数值，便于追溯
            child_desc = case.description
            if keyword:
                child_desc = f"{child_desc} | 关键词: {keyword}" if child_desc else f"关键词: {keyword}"

            # 合并默认参数和当前参数集
            merged_params = {**(case.parameters or {}), **param_set}

            # 深拷贝步骤并替换占位符（UI 显示实际值）
            import copy
            child_steps = copy.deepcopy(case.steps)
            child_setup = copy.deepcopy(case.setup_steps)
            child_teardown = copy.deepcopy(case.teardown_steps)
            for step in child_steps + child_setup + child_teardown:
                step.description = TestCaseParser._substitute(step.description, merged_params)
                step.params = TestCaseParser._substitute(step.params, merged_params)
                if step.expected is not None:
                    step.expected = TestCaseParser._substitute(step.expected, merged_params)

            new_case = TestCase(
                name=child_name,
                description=child_desc,
                category=case.category,
                tags=case.tags + [f"参数集{i + 1}"],
                priority=case.priority,
                setup_steps=child_setup,
                teardown_steps=child_teardown,
                steps=child_steps,
                parameters=merged_params,
                retry_count=case.retry_count,
                timeout=case.timeout,
                parent_id=case.case_id,
            )
            # 稳定 ID：基于父 ID + 序号，刷新后不变
            new_case.case_id = f"{case.case_id}_p{i+1}"
            expanded.append(new_case)
        return expanded

    @staticmethod
    def _substitute(obj, params):
        """递归替换 {param} 占位符"""
        if not params:
            return obj
        if isinstance(obj, str):
            result = obj
            for key, value in params.items():
                result = result.replace(f"{{{key}}}", str(value))
            return result
        elif isinstance(obj, dict):
            return {k: TestCaseParser._substitute(v, params) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return type(obj)(TestCaseParser._substitute(item, params) for item in obj)
        return obj

    # ---------- 导出功能 ----------

    @staticmethod
    def export_yaml(cases: List[TestCase], file_path: str) -> bool:
        """导出测试用例为 YAML 格式"""
        try:
            data = {
                "version": "1.0",
                "test_cases": [c.to_dict() for c in cases]
            }
            with open(file_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False,
                          allow_unicode=True, sort_keys=False)
            logger.info(f"用例已导出: {file_path}")
            return True
        except Exception as e:
            logger.error(f"导出失败: {e}")
            return False

    @staticmethod
    def export_json(cases: List[TestCase], file_path: str) -> bool:
        """导出测试用例为 JSON 格式"""
        try:
            data = [c.to_dict() for c in cases]
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"用例已导出: {file_path}")
            return True
        except Exception as e:
            logger.error(f"导出失败: {e}")
            return False
