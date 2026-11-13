"""
参数数据导入器
===============
从外部文件（Excel / CSV / JSON / YAML / Word）导入参数化测试的参数定义与参数组。

设计说明：
- 统一入口 import_parameters(file_path) -> (parameters_dict, parameter_sets_list)
- 每种格式有独立的解析方法
- Excel/CSV: 首行 = 参数名，数据行 = 参数值
- JSON/YAML: 支持 [{...}, ...] 或 {"parameters": {...}, "parameter_sets": [...]}
- Word: 读取文档中的第一个表格，首行 = 参数名
"""

import csv
import json
import re
from pathlib import Path
from typing import Dict, List, Tuple, Any

import yaml
from loguru import logger


def _preprocess_python_expressions(text: str) -> str:
    """
    预处理文件内容，将 Python 风格的表达式替换为实际值。

    处理的模式：
    - "string" * N  →  重复 N 次的字符串
    - 'string' * N  →  重复 N 次的字符串
    - N * "string"  →  重复 N 次的字符串（反向）
    """
    # 匹配 "xxx" * N 或 'xxx' * N
    def replace_repeat(match):
        quote = match.group(1)
        content = match.group(2)
        count = int(match.group(3))
        return quote + content * count + quote

    # "string" * N 或 'string' * N
    text = re.sub(
        r"""(["'])([^"']*?)\1\s*\*\s*(\d+)""",
        replace_repeat, text
    )
    # N * "string" 或 N * 'string'（反向）
    text = re.sub(
        r"""(\d+)\s*\*\s*(["'])([^"']*?)\2""",
        lambda m: m.group(2) + m.group(3) * int(m.group(1)) + m.group(2),
        text
    )
    return text


def import_parameters(file_path: str) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    """
    从外部文件导入参数定义和参数组。

    参数:
        file_path: 文件路径

    返回:
        (parameters, parameter_sets)
        parameters: {"param_name": "default_value", ...}
        parameter_sets: [{"param_name": "value", ...}, ...]

    异常:
        FileNotFoundError: 文件不存在
        ValueError: 文件格式不支持或解析失败
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    suffix = path.suffix.lower()
    try:
        if suffix in (".xlsx", ".xls"):
            return _import_excel(path)
        elif suffix == ".csv":
            return _import_csv(path)
        elif suffix == ".json":
            return _import_json(path)
        elif suffix in (".yaml", ".yml"):
            return _import_yaml(path)
        elif suffix == ".docx":
            return _import_word(path)
        else:
            raise ValueError(f"不支持的文件格式: {suffix}（支持 .xlsx/.csv/.json/.yaml/.docx）")
    except (FileNotFoundError, ValueError):
        raise
    except Exception as e:
        raise ValueError(f"文件解析失败: {e}")


def _import_excel(path: Path) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    """从 Excel 文件导入参数"""
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise ValueError("openpyxl 未安装，无法解析 Excel 文件。请运行: pip install openpyxl")

    wb = load_workbook(str(path), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if len(rows) < 2:
        raise ValueError("Excel 文件至少需要 2 行（首行=参数名，后续行=参数值）")

    # 首行：参数名
    headers = [str(h).strip() if h else "" for h in rows[0]]
    headers = [h for h in headers if h]  # 过滤空列
    if not headers:
        raise ValueError("Excel 首行未找到有效的参数名")

    # 数据行：参数值
    parameter_sets = []
    for row in rows[1:]:
        param_set = {}
        for i, header in enumerate(headers):
            if i < len(row):
                value = row[i]
                param_set[header] = str(value) if value is not None else ""
            else:
                param_set[header] = ""
        # 跳过全空行
        if any(v.strip() for v in param_set.values()):
            parameter_sets.append(param_set)

    # 生成默认参数（取每个参数名的第一个值或空字符串）
    parameters = {h: "" for h in headers}
    if parameter_sets:
        for h in headers:
            parameters[h] = parameter_sets[0].get(h, "")

    return parameters, parameter_sets


def _import_csv(path: Path) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    """从 CSV 文件导入参数"""
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV 文件首行未找到有效的参数名")

        headers = [h.strip() for h in reader.fieldnames if h.strip()]
        parameter_sets = []
        for row in reader:
            param_set = {h: str(row.get(h, "") or "").strip() for h in headers}
            # 跳过全空行
            if any(v for v in param_set.values()):
                parameter_sets.append(param_set)

    # 生成默认参数
    parameters = {h: "" for h in headers}
    if parameter_sets:
        for h in headers:
            parameters[h] = parameter_sets[0].get(h, "")

    return parameters, parameter_sets


def _import_json(path: Path) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    """从 JSON 文件导入参数"""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    # 预处理 Python 表达式
    raw = _preprocess_python_expressions(raw)
    data = json.loads(raw)

    return _parse_dict_or_list(data)


def _import_yaml(path: Path) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    """从 YAML 文件导入参数"""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    # 预处理 Python 表达式（如 "a" * 101）
    raw = _preprocess_python_expressions(raw)
    data = yaml.safe_load(raw)

    return _parse_dict_or_list(data)


def _parse_dict_or_list(data: Any) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    """解析 JSON/YAML 数据：支持列表格式、对象格式、以及嵌套场景格式"""
    if isinstance(data, list):
        # 列表格式：[{"param1": "val1", "param2": "val2"}, ...]
        if not data:
            raise ValueError("JSON/YAML 列表为空")
        parameter_sets = []
        all_keys = set()
        for item in data:
            if not isinstance(item, dict):
                raise ValueError("JSON/YAML 列表中的每一项必须是对象（字典）")
            param_set = {str(k): str(v) if v is not None else "" for k, v in item.items()}
            all_keys.update(param_set.keys())
            parameter_sets.append(param_set)
        # 默认参数：取第一个参数组的值
        parameters = {}
        for key in sorted(all_keys):
            parameters[key] = parameter_sets[0].get(key, "") if parameter_sets else ""
        return parameters, parameter_sets

    elif isinstance(data, dict):
        # 对象格式：{"parameters": {...}, "parameter_sets": [...]}
        parameters = data.get("parameters", {})
        if not isinstance(parameters, dict):
            parameters = {}
        parameters = {str(k): str(v) if v is not None else "" for k, v in parameters.items()}

        parameter_sets = data.get("parameter_sets", [])
        if not isinstance(parameter_sets, list):
            parameter_sets = []
        cleaned_sets = []
        for item in parameter_sets:
            if isinstance(item, dict):
                param_set = {str(k): str(v) if v is not None else "" for k, v in item.items()}
                cleaned_sets.append(param_set)
        parameter_sets = cleaned_sets

        if not parameters and parameter_sets:
            # 从参数组推断参数名
            all_keys = set()
            for ps in parameter_sets:
                all_keys.update(ps.keys())
            parameters = {k: "" for k in sorted(all_keys)}

        if parameters or parameter_sets:
            return parameters, parameter_sets

        # 嵌套场景格式：{scenario_name: [{case...}, ...], ...}
        # 例如：{basic_search: [{case_id: "TC1", keyword: "xxx"}, ...], ...}
        return _parse_nested_scenarios(data)

    else:
        raise ValueError("JSON/YAML 数据格式不正确，需要是列表或对象")


def _parse_nested_scenarios(data: dict) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    """
    解析嵌套场景格式：顶层 key 是场景名，value 是用例列表。
    提取所有用例的公共字段作为参数，每个用例作为一组参数。
    """
    # 收集所有用例
    all_cases = []
    scenario_names = []
    for scenario_name, items in data.items():
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    case = {"scenario": str(scenario_name)}
                    case.update({str(k): str(v) if v is not None else ""
                                 for k, v in item.items()
                                 if not isinstance(v, (dict, list))})
                    all_cases.append(case)
                    if scenario_name not in scenario_names:
                        scenario_names.append(str(scenario_name))

    if not all_cases:
        raise ValueError(
            "未找到有效的参数数据。支持格式：\n"
            "  - 列表: [{param1: val1, ...}, ...]\n"
            "  - 对象: {parameters: {...}, parameter_sets: [...]}\n"
            "  - 嵌套场景: {scenario: [{field: value, ...}, ...], ...}"
        )

    # 找出所有用例共有的字段作为参数（排除 case_id 等标识字段）
    field_counts = {}
    for case in all_cases:
        for key in case:
            field_counts[key] = field_counts.get(key, 0) + 1

    # 保留出现次数 >= 总用例数 50% 的字段
    threshold = max(1, len(all_cases) * 0.5)
    param_keys = sorted(k for k, c in field_counts.items() if c >= threshold)

    parameters = {k: "" for k in param_keys}
    parameter_sets = []
    for case in all_cases:
        param_set = {k: case.get(k, "") for k in param_keys}
        if any(v for v in param_set.values()):
            parameter_sets.append(param_set)

    if parameter_sets:
        for k in param_keys:
            parameters[k] = parameter_sets[0].get(k, "")

    return parameters, parameter_sets


def _import_word(path: Path) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    """从 Word 文档导入参数（读取第一个表格）"""
    try:
        from docx import Document
    except ImportError:
        raise ValueError("python-docx 未安装，无法解析 Word 文件。请运行: pip install python-docx")

    doc = Document(str(path))
    if not doc.tables:
        raise ValueError("Word 文档中未找到表格")

    # 使用第一个表格
    table = doc.tables[0]
    rows = []
    for row in table.rows:
        cells = [cell.text.strip() for cell in row.cells]
        rows.append(cells)

    if len(rows) < 2:
        raise ValueError("Word 表格至少需要 2 行（首行=参数名，后续行=参数值）")

    # 首行：参数名
    headers = [h for h in rows[0] if h]
    if not headers:
        raise ValueError("Word 表格首行未找到有效的参数名")

    # 数据行：参数值
    parameter_sets = []
    for row_cells in rows[1:]:
        param_set = {}
        for i, header in enumerate(headers):
            if i < len(row_cells):
                param_set[header] = row_cells[i]
            else:
                param_set[header] = ""
        # 跳过全空行
        if any(v.strip() for v in param_set.values()):
            parameter_sets.append(param_set)

    # 生成默认参数
    parameters = {h: "" for h in headers}
    if parameter_sets:
        for h in headers:
            parameters[h] = parameter_sets[0].get(h, "")

    return parameters, parameter_sets
