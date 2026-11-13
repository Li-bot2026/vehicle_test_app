"""
报告生成器
===========
基于 Jinja2 模板生成测试报告，支持 HTML / PDF / Excel / JUnit XML 格式。

设计说明：
- 使用 Jinja2 渲染 HTML 报告模板
- HTML 报告包含：通过率饼图、耗时柱状图、失败详情、日志
- PDF 通过 WeasyPrint 从 HTML 转换
- Excel 通过 openpyxl 生成
- JUnit XML 兼容 CI/CD 集成
"""

import os
import re
import json
import shutil
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict

from loguru import logger

from ..testcase.model import TestSuite, TestCase, TestStatus


class ReportGenerator:
    """
    测试报告生成器
    ---------------
    支持多种格式的报告导出。
    """

    def __init__(self, template_dir: str = "templates"):
        self._template_dir = Path(template_dir)
        self._ensure_template()

    def _ensure_template(self):
        """确保模板目录和默认模板存在（旧默认模板会自动升级）"""
        self._template_dir.mkdir(parents=True, exist_ok=True)
        default_template = self._template_dir / "report_template.html"
        if not default_template.exists():
            default_template.write_text(
                self._get_default_template(), encoding="utf-8"
            )
            return
        try:
            content = default_template.read_text(encoding="utf-8")
        except Exception:
            return
        # 旧版默认模板（没有版本标记）才覆盖；用户自定义模板保持不变
        if "report-template-version: 2" not in content and "report-template-version: 3" not in content and (
                "车机测试报告" in content):
            default_template.write_text(
                self._get_default_template(), encoding="utf-8"
            )
            logger.info(f"报告模板已升级为 Allure 风格: {default_template}")

    # ---------- HTML 报告 ----------

    def generate_html(self, suite: TestSuite,
                      output_path: str = "reports/report.html") -> str:
        """
        生成 HTML 测试报告。

        参数:
            suite: 测试套件（包含执行结果）
            output_path: 输出文件路径

        返回:
            生成的文件路径
        """
        try:
            from jinja2 import Environment, FileSystemLoader
        except ImportError:
            logger.error("Jinja2 未安装，无法生成 HTML 报告")
            return ""

        # 准备模板数据
        context = self._build_context(suite)

        # 渲染模板
        env = Environment(
            loader=FileSystemLoader(str(self._template_dir)),
            autoescape=True
        )
        template = env.get_template("report_template.html")
        html_content = template.render(**context)

        # 写入文件
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(html_content, encoding="utf-8")

        logger.info(f"HTML 报告已生成: {output}")
        return str(output)

    # ---------- PDF 报告 ----------

    # 可用的无头浏览器（Windows 常见安装位置）
    _BROWSER_CANDIDATES = (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"/usr/bin/google-chrome",
        r"/usr/bin/chromium",
        r"/usr/bin/chromium-browser",
    )

    def generate_pdf(self, suite: TestSuite,
                     output_path: str = "reports/report.pdf") -> str:
        """
        生成 PDF 测试报告（先渲染 HTML，再转换为 PDF）。

        转换顺序：WeasyPrint → 系统 Edge/Chrome 无头打印。
        两者都不可用时返回空字符串，日志里会给出具体原因。
        """
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        html_path = output.with_name(output.stem + "_temp.html")

        try:
            if not self.generate_html(suite, str(html_path)):
                return ""
            # PDF 里展开全部用例明细，避免通过/跳过的步骤被折叠
            try:
                content = html_path.read_text(encoding="utf-8")
                content = re.sub(
                    r'<details class="case"(?![^>]*\bopen\b)',
                    '<details class="case" open', content)
                html_path.write_text(content, encoding="utf-8")
            except Exception:
                pass

            errors: List[str] = []
            if self._pdf_with_weasyprint(html_path, output, errors):
                logger.info(f"PDF 报告已生成: {output}")
                return str(output)
            if self._pdf_with_browser(html_path, output, errors):
                logger.info(f"PDF 报告已生成（浏览器打印）: {output}")
                return str(output)
            logger.error("PDF 生成失败：" + "；".join(errors))
            return ""
        finally:
            try:
                html_path.unlink(missing_ok=True)
            except Exception:
                pass

    @staticmethod
    def _pdf_with_weasyprint(html_path: Path, pdf_path: Path,
                             errors: List[str]) -> bool:
        try:
            from weasyprint import HTML
        except Exception:
            errors.append("WeasyPrint 未安装")
            return False
        try:
            HTML(filename=str(html_path)).write_pdf(str(pdf_path))
            return pdf_path.exists() and pdf_path.stat().st_size > 0
        except Exception as e:
            errors.append(f"WeasyPrint 转换失败: {e}")
            return False

    @classmethod
    def _find_pdf_browser(cls) -> str:
        """查找可用于无头打印 PDF 的浏览器"""
        env = os.environ.get("VEHICLE_TEST_PDF_BROWSER", "").strip()
        if env and Path(env).exists():
            return env
        for name in ("msedge", "microsoft-edge", "chrome",
                     "google-chrome", "chromium", "chromium-browser"):
            found = shutil.which(name)
            if found:
                return found
        for candidate in cls._BROWSER_CANDIDATES:
            if Path(candidate).exists():
                return candidate
        return ""

    @classmethod
    def _pdf_with_browser(cls, html_path: Path, pdf_path: Path,
                          errors: List[str]) -> bool:
        exe = cls._find_pdf_browser()
        if not exe:
            errors.append("未找到 Edge/Chrome 浏览器")
            return False

        url = html_path.resolve().as_uri()
        pdf_abs = pdf_path.resolve()
        profile = tempfile.mkdtemp(prefix="vt_pdf_profile_")
        flags = {}
        if os.name == "nt":
            flags["creationflags"] = getattr(
                subprocess, "CREATE_NO_WINDOW", 0x08000000)

        try:
            # 新版浏览器用 --headless=new；老版本回退 --headless
            for headless in ("--headless=new", "--headless"):
                try:
                    if pdf_abs.exists():
                        pdf_abs.unlink()
                    subprocess.run(
                        [exe, headless, "--disable-gpu", "--hide-scrollbars",
                         "--no-pdf-header-footer", "--print-to-pdf-no-header",
                         f"--user-data-dir={profile}",
                         f"--print-to-pdf={pdf_abs}", url],
                        timeout=90,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        **flags,
                    )
                except subprocess.TimeoutExpired:
                    errors.append(f"浏览器打印超时（{headless}）")
                    continue
                except Exception as e:
                    errors.append(f"浏览器调用失败: {e}")
                    continue
                if pdf_abs.exists() and pdf_abs.stat().st_size > 1024:
                    return True
                errors.append(f"浏览器未产出 PDF（{headless}）")
            return False
        finally:
            shutil.rmtree(profile, ignore_errors=True)

    # ---------- Excel 报告 ----------

    def generate_excel(self, suite: TestSuite,
                       output_path: str = "reports/report.xlsx") -> str:
        """生成 Excel 测试报告"""
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        except ImportError:
            logger.error("openpyxl 未安装，无法生成 Excel 报告")
            return ""

        wb = Workbook()

        # --- 概览页 ---
        ws_summary = wb.active
        ws_summary.title = "测试概览"

        header_font = Font(bold=True, size=14)
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_font_white = Font(bold=True, size=11, color="FFFFFF")

        ws_summary["A1"] = "测试报告"
        ws_summary["A1"].font = header_font

        summary_data = [
            ("套件名称", suite.name),
            ("执行时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            ("总用例数", suite.total_count),
            ("通过", suite.passed_count),
            ("失败", suite.failed_count),
            ("错误", suite.error_count),
            ("跳过", suite.skipped_count),
            ("通过率", f"{suite.pass_rate:.1f}%"),
            ("总耗时", f"{suite.total_duration:.2f}s"),
        ]

        for row_idx, (label, value) in enumerate(summary_data, start=3):
            ws_summary.cell(row=row_idx, column=1, value=label).font = Font(bold=True)
            ws_summary.cell(row=row_idx, column=2, value=str(value))

        # --- 详细结果页 ---
        ws_detail = wb.create_sheet("详细结果")

        headers = ["序号", "用例名称", "分类", "优先级", "状态", "耗时(s)",
                   "失败步骤", "错误信息", "截图/附件"]
        for col_idx, header in enumerate(headers, start=1):
            cell = ws_detail.cell(row=1, column=col_idx, value=header)
            cell.font = header_font_white
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        # 状态颜色映射
        status_colors = {
            TestStatus.PASSED: "C6EFCE",
            TestStatus.FAILED: "FFC7CE",
            TestStatus.ERROR: "FFC7CE",
            TestStatus.SKIPPED: "FFEB9C",
        }

        for row_idx, case in enumerate(suite.test_cases, start=2):
            ws_detail.cell(row=row_idx, column=1, value=row_idx - 1)
            ws_detail.cell(row=row_idx, column=2, value=case.name)
            ws_detail.cell(row=row_idx, column=3, value=case.category)
            ws_detail.cell(row=row_idx, column=4, value=case.priority.value)

            status_cell = ws_detail.cell(row=row_idx, column=5, value=case.status.value)
            color = status_colors.get(case.status, "FFFFFF")
            status_cell.fill = PatternFill(start_color=color, end_color=color, fill_type="solid")

            ws_detail.cell(row=row_idx, column=6, value=round(case.duration, 2))

            # 错误点：最后一个失败/错误步骤（与 HTML 报告一致）
            failed_step, failed_label = None, ""
            for prefix, attr in (("前置", "setup_steps"), ("", "steps"),
                                 ("后置", "teardown_steps")):
                for i, step in enumerate(getattr(case, attr), start=1):
                    if step.status in (TestStatus.FAILED, TestStatus.ERROR):
                        failed_step = step
                        failed_label = (f"{prefix}第 {i} 步" if prefix
                                        else f"第 {i} 步")
            if failed_step is not None:
                ws_detail.cell(
                    row=row_idx, column=7,
                    value=(f"{failed_label} · {failed_step.step_type.value} · "
                           f"{failed_step.description}")
                )
            ws_detail.cell(row=row_idx, column=8, value=case.error_message)
            all_atts = list(case.screenshots) + list(case.attachments)
            if all_atts:
                abs_paths = [os.path.abspath(p) for p in all_atts if os.path.exists(p)]
                ws_detail.cell(
                    row=row_idx, column=9,
                    value="; ".join(abs_paths) if abs_paths else ""
                )

        # 调整列宽
        for col in ws_detail.columns:
            max_length = max(len(str(cell.value or "")) for cell in col)
            ws_detail.column_dimensions[col[0].column_letter].width = min(max_length + 4, 50)

        # --- 步骤详情页 ---
        ws_steps = wb.create_sheet("步骤详情")
        step_headers = ["用例名称", "#", "类型", "描述", "参数", "预期值",
                        "结果", "耗时(s)", "附件"]
        for col_idx, header in enumerate(step_headers, start=1):
            cell = ws_steps.cell(row=1, column=col_idx, value=header)
            cell.font = header_font_white
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        step_row = 2
        for case in suite.test_cases:
            sections = (("", "setup_steps", "前置"), ("", "steps", ""),
                        ("", "teardown_steps", "后置"))
            step_num = 0
            for _, attr, prefix in sections:
                for s in getattr(case, attr):
                    step_num += 1
                    label = f"{prefix}{step_num}" if prefix else str(step_num)
                    params_str = json.dumps(s.params, ensure_ascii=False) if s.params else ""
                    att_str = "; ".join(s.attachments) if s.attachments else ""
                    ws_steps.cell(row=step_row, column=1, value=case.name)
                    ws_steps.cell(row=step_row, column=2, value=label)
                    ws_steps.cell(row=step_row, column=3, value=s.step_type.value)
                    ws_steps.cell(row=step_row, column=4, value=s.description)
                    ws_steps.cell(row=step_row, column=5, value=params_str)
                    ws_steps.cell(row=step_row, column=6,
                                  value=str(s.expected) if s.expected is not None else "")
                    ws_steps.cell(row=step_row, column=7, value=s.status.value)
                    ws_steps.cell(row=step_row, column=8, value=round(s.duration, 2))
                    ws_steps.cell(row=step_row, column=9, value=att_str)
                    step_row += 1

        # 调整步骤详情页列宽
        for col in ws_steps.columns:
            max_length = max(len(str(cell.value or "")) for cell in col)
            ws_steps.column_dimensions[col[0].column_letter].width = min(max_length + 4, 60)

        # 保存
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        wb.save(str(output))
        logger.info(f"Excel 报告已生成: {output}")
        return str(output)

    # ---------- JUnit XML ----------

    def generate_junit_xml(self, suite: TestSuite,
                           output_path: str = "reports/report.xml") -> str:
        """
        生成 JUnit XML 格式报告（兼容 Jenkins / GitLab CI 等）。
        """
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        suite_name = self._xml_escape(suite.name)
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<testsuites name="{suite_name}" tests="{suite.total_count}" '
            f'failures="{suite.failed_count}" errors="{suite.error_count}" '
            f'time="{suite.total_duration:.2f}">',
            f'  <testsuite name="{suite_name}" tests="{suite.total_count}" '
            f'failures="{suite.failed_count}" errors="{suite.error_count}" '
            f'skipped="{suite.skipped_count + suite.aborted_count}" '
            f'time="{suite.total_duration:.2f}">',
        ]

        for case in suite.test_cases:
            line = f'    <testcase name="{self._xml_escape(case.name)}" '
            line += f'classname="{self._xml_escape(case.category)}" '
            line += f'time="{case.duration:.2f}"'

            if case.status == TestStatus.PASSED:
                line += " />"
            elif case.status in (TestStatus.SKIPPED, TestStatus.ABORTED):
                line += ">"
                message = case.error_message or case.status.value
                line += f'\n      <skipped message="{self._xml_escape(message)}" />'
                line += "\n    </testcase>"
            else:
                line += ">"
                line += f'\n      <failure message="{self._xml_escape(case.error_message)}">'
                line += f"\n        {self._xml_escape(case.error_message)}"
                line += "\n      </failure>"
                if case.log_entries:
                    line += f'\n      <system-out>{self._xml_escape(chr(10).join(case.log_entries))}</system-out>'
                line += "\n    </testcase>"

            lines.append(line)

        lines.append("  </testsuite>")
        lines.append("</testsuites>")

        output.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"JUnit XML 报告已生成: {output}")
        return str(output)

    # ---------- Allure 结果 ----------

    _ALLURE_STATUS = {
        TestStatus.PASSED: "passed",
        TestStatus.FAILED: "failed",
        TestStatus.ERROR: "broken",
        TestStatus.SKIPPED: "skipped",
        TestStatus.ABORTED: "skipped",
        TestStatus.PENDING: "unknown",
        TestStatus.RUNNING: "unknown",
    }

    def generate_allure(self, suite: TestSuite,
                        results_dir: str = "reports/allure-results") -> str:
        """
        生成 Allure 原始结果目录（allure-results）。

        之后可以用 `allure serve <dir>` 或 `allure generate` 打开标准 Allure 报告；
        结果中包含步骤、预期/实际、失败原因与截图附件。
        """
        import shutil
        import uuid as _uuid

        out = Path(results_dir)
        if out.exists():
            shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True, exist_ok=True)

        for case in suite.test_cases:
            self._write_allure_result(out, suite, case, _uuid.uuid4().hex)

        # 环境信息（Allure 报告的 Environment 面板）
        # Java properties 按 ISO-8859-1 解析，非 ASCII 必须转成 \uXXXX
        def esc(text: str) -> str:
            return "".join(
                ch if ord(ch) < 128 else f"\\u{ord(ch):04x}"
                for ch in str(text).replace("\\", "\\\\")
            )

        env_lines = [
            f"Project={esc('车机测试应用程序')}",
            f"Suite={esc(suite.name)}",
            f"GeneratedAt={datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"TestCases={len(suite.test_cases)}",
            f"PassRate={suite.pass_rate:.1f}%",
        ]
        (out / "environment.properties").write_text(
            "\n".join(env_lines), encoding="utf-8")

        logger.info(f"Allure 结果已生成: {out}（{len(suite.test_cases)} 条用例）")
        return str(out)

    def _write_allure_result(self, out: Path, suite: TestSuite,
                             case: TestCase, result_uuid: str) -> None:
        """写入单个用例的 Allure result JSON（含步骤与附件）"""
        import shutil
        from datetime import timedelta

        start_dt = case.start_time or datetime.now()
        stop_dt = case.end_time or (start_dt + timedelta(
            seconds=max(case.duration, 0.0)))
        start_ms = int(start_dt.timestamp() * 1000)
        stop_ms = max(start_ms, int(stop_dt.timestamp() * 1000))

        severity = {
            "P0": "critical", "P1": "normal",
            "P2": "minor", "P3": "trivial",
        }.get(case.priority.value, "normal")

        # 步骤（含前置/后置），按耗时顺排时间轴
        steps = []
        cursor = start_ms
        failed_entry = None
        for prefix, attr in (("前置", "setup_steps"), ("", "steps"),
                             ("后置", "teardown_steps")):
            for idx, step in enumerate(getattr(case, attr), start=1):
                dur_ms = max(0, int((step.duration or 0.0) * 1000))
                entry = {
                    "name": (f"{prefix}第 {idx} 步：{step.description}"
                             if prefix else f"第 {idx} 步：{step.description}"),
                    "status": self._ALLURE_STATUS.get(step.status, "unknown"),
                    "stage": "finished",
                    "start": cursor,
                    "stop": cursor + dur_ms,
                    "parameters": [],
                }
                msg = step.error_message or ""
                if step.expected not in (None, ""):
                    entry["parameters"].append(
                        {"name": "预期", "value": str(step.expected)})
                if step.actual not in (None, ""):
                    entry["parameters"].append(
                        {"name": "实际", "value": str(step.actual)})
                if msg or step.expected not in (None, ""):
                    entry["statusDetails"] = {"message": msg or (
                        f"预期 {step.expected}，实际 {step.actual}")}
                steps.append(entry)
                cursor += dur_ms
                if entry["status"] in ("failed", "broken") and failed_entry is None:
                    failed_entry = entry

        # 失败截图作为附件，优先挂在失败步骤上
        attachments = []
        for i, path in enumerate(case.screenshots):
            abs_path = os.path.abspath(path)
            if not os.path.isfile(abs_path):
                continue
            suffix = os.path.splitext(abs_path)[1].lower() or ".png"
            source = f"{result_uuid}-attachment-{i}{suffix}"
            try:
                shutil.copyfile(abs_path, out / source)
            except Exception as e:
                logger.debug(f"复制附件失败: {abs_path} - {e}")
                continue
            att = {
                "name": os.path.basename(abs_path),
                "source": source,
                "type": "image/jpeg" if suffix in (".jpg", ".jpeg")
                        else "image/png",
            }
            if failed_entry is not None:
                failed_entry.setdefault("attachments", []).append(att)
            else:
                attachments.append(att)

        result = {
            "uuid": result_uuid,
            "historyId": case.case_id,
            "name": case.name,
            "fullName": f"{case.category or suite.name}.{case.name}",
            "status": self._ALLURE_STATUS.get(case.status, "unknown"),
            "stage": "finished",
            "start": start_ms,
            "stop": stop_ms,
            "labels": [
                {"name": "suite", "value": suite.name},
                {"name": "package", "value": case.category or "未分类"},
                {"name": "severity", "value": severity},
                {"name": "framework", "value": "车机测试应用程序"},
            ] + [{"name": "tag", "value": t} for t in case.tags],
            "parameters": [{"name": str(k), "value": str(v)}
                           for k, v in (case.parameters or {}).items()],
            "steps": steps,
            "attachments": attachments,
        }
        if case.error_message or case.status in (TestStatus.FAILED,
                                                 TestStatus.ERROR):
            result["statusDetails"] = {
                "message": case.error_message or "用例执行失败",
                "trace": "",
            }

        (out / f"{result_uuid}-result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ---------- 辅助方法 ----------

    def _build_context(self, suite: TestSuite) -> dict:
        """构建模板渲染上下文（Allure 风格：步骤明细 + 失败错误点 + 时间线）"""
        # 防止调用方忘记统计，导致报告顶部数字与实际用例不一致
        if suite.total_count != len(suite.test_cases):
            suite.calculate_stats()
        failed_cases = suite.get_failed_cases()

        # 按分类统计
        category_stats = {}
        for case in suite.test_cases:
            cat = case.category or "未分类"
            if cat not in category_stats:
                category_stats[cat] = {"total": 0, "passed": 0, "failed": 0}
            category_stats[cat]["total"] += 1
            if case.status == TestStatus.PASSED:
                category_stats[cat]["passed"] += 1
            elif case.status in (TestStatus.FAILED, TestStatus.ERROR):
                category_stats[cat]["failed"] += 1

        status_text = {
            "passed": "通过", "failed": "失败", "error": "错误",
            "skipped": "跳过", "aborted": "中止", "running": "执行中",
            "pending": "待执行",
        }
        status_icon = {
            "passed": "✓", "failed": "✗", "error": "!",
            "skipped": "−", "aborted": "×", "running": "…", "pending": "·",
        }

        sections = (
            ("前置", "setup_steps"),
            ("", "steps"),
            ("后置", "teardown_steps"),
        )

        def build_steps(case) -> list:
            """按前置/主步骤/后置分段编号，便于精确指出错误点"""
            views = []
            for prefix, attr in sections:
                for i, step in enumerate(getattr(case, attr)):
                    # 步骤参数（JSON 格式，便于展示）
                    params_str = ""
                    if step.params:
                        try:
                            params_str = json.dumps(step.params, ensure_ascii=False)
                        except Exception:
                            params_str = str(step.params)
                    # 步骤附件
                    step_attachments = []
                    for att_path in (step.attachments or []):
                        abs_path = os.path.abspath(att_path)
                        if os.path.exists(abs_path):
                            is_image = abs_path.lower().endswith(
                                (".png", ".jpg", ".jpeg", ".bmp", ".gif")
                            )
                            step_attachments.append({
                                "name": os.path.basename(abs_path),
                                "url": "file:///" + abs_path.replace("\\", "/"),
                                "is_image": is_image,
                            })
                    views.append({
                        "no": i + 1,
                        "seq": (f"{prefix}{i + 1}" if prefix else str(i + 1)),
                        "label": (f"{prefix}第 {i + 1} 步" if prefix
                                  else f"第 {i + 1} 步"),
                        "section": attr,
                        "type": step.step_type.value,
                        "description": step.description,
                        "params": params_str,
                        "status": step.status.value,
                        "status_text": status_text.get(step.status.value,
                                                       step.status.value),
                        "icon": status_icon.get(step.status.value, "·"),
                        "duration": round(step.duration, 2),
                        "error": step.error_message or "",
                        "expected": step.expected,
                        "actual": step.actual,
                        "attachments": step_attachments,
                    })
            return views

        case_views = []
        failed_details = []
        total_steps = 0
        passed_steps = 0
        for case in suite.test_cases:
            steps_view = build_steps(case)
            for step_view in steps_view:
                total_steps += 1
                if step_view["status"] == "passed":
                    passed_steps += 1

            # 失败点：最后执行失败的那个步骤
            failed_step = None
            for view in reversed(steps_view):
                if view["status"] in ("failed", "error"):
                    failed_step = view
                    break

            shots = []
            # 收集截图附件（screenshots + attachments 中的图片）
            all_attachment_paths = list(case.screenshots) + list(case.attachments)
            for path in all_attachment_paths:
                abs_path = os.path.abspath(path)
                if os.path.exists(abs_path) and abs_path not in [s.get("url", "").replace("file:///", "") for s in shots]:
                    is_image = abs_path.lower().endswith(
                        (".png", ".jpg", ".jpeg", ".bmp", ".gif")
                    )
                    shots.append({
                        "name": os.path.basename(abs_path),
                        "url": "file:///" + abs_path.replace("\\", "/"),
                        "is_image": is_image,
                    })

            # 收集非图片附件（日志等）
            other_attachments = []
            for path in case.attachments:
                abs_path = os.path.abspath(path)
                if os.path.exists(abs_path):
                    is_image = abs_path.lower().endswith(
                        (".png", ".jpg", ".jpeg", ".bmp", ".gif")
                    )
                    if not is_image:
                        other_attachments.append({
                            "name": os.path.basename(abs_path),
                            "url": "file:///" + abs_path.replace("\\", "/"),
                        })

            view = {
                "case": case,
                "status_text": status_text.get(case.status.value,
                                               case.status.value),
                "icon": status_icon.get(case.status.value, "·"),
                "steps": steps_view,
                "failed_step": failed_step,
                "shots": shots,
                "other_attachments": other_attachments,
                "step_count": len(steps_view),
            }
            case_views.append(view)
            if case.status in (TestStatus.FAILED, TestStatus.ERROR):
                failed_details.append(view)

        timeline = self._build_timeline(suite)

        return {
            "suite": suite,
            "suite_name": suite.name,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_count": suite.total_count,
            "passed_count": suite.passed_count,
            "failed_count": suite.failed_count,
            "error_count": suite.error_count,
            "skipped_count": suite.skipped_count,
            "aborted_count": suite.aborted_count,
            "pass_rate": round(suite.pass_rate, 1),
            "total_duration": round(suite.total_duration, 2),
            "total_steps": total_steps,
            "passed_steps": passed_steps,
            "failed_cases": failed_cases,
            "all_cases": suite.test_cases,
            "category_stats": category_stats,
            "case_views": case_views,
            "failed_details": failed_details,
            "timeline": timeline,
        }

    @staticmethod
    def _build_timeline(suite: TestSuite) -> list:
        """按执行起止时间生成时间线（类似 Allure 的 Timeline）"""
        timed = [c for c in suite.test_cases
                 if c.start_time and c.end_time]
        if not timed:
            return []
        start = min(c.start_time for c in timed)
        end = max(c.end_time for c in timed)
        span = max(0.01, (end - start).total_seconds())
        rows = []
        for case in timed:
            offset = (case.start_time - start).total_seconds()
            width = max(0.5, (case.end_time - case.start_time).total_seconds())
            rows.append({
                "name": case.name,
                "status": case.status.value,
                "left": round(offset / span * 100, 2),
                "width": round(min(width / span * 100, 100), 2),
                "duration": round(case.duration, 2),
                "start": case.start_time.strftime("%H:%M:%S"),
            })
        return rows

    @staticmethod
    def _xml_escape(text: str) -> str:
        """XML 转义"""
        return (text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&apos;"))

    @staticmethod
    def _get_default_template() -> str:
        """返回默认 HTML 报告模板（Allure 风格）"""
        return '''<!DOCTYPE html>
<!-- report-template-version: 3 -->
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ suite_name }} · 测试报告</title>
<style>
:root { --pass:#3fae5a; --fail:#e5484d; --warn:#d99a00; --skip:#8b8f94;
        --ink:#1f2933; --muted:#7b8794; --line:#e6e9ed; --bg:#f5f6f8; }
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:"Microsoft YaHei","PingFang SC",Segoe UI,sans-serif; background:var(--bg); color:var(--ink); font-size:14px; }
a { color:#2563eb; text-decoration:none; }
.topbar { background:#242f3d; color:#fff; padding:22px 32px; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px; }
.topbar h1 { font-size:20px; font-weight:600; letter-spacing:.3px; }
.topbar .meta { color:#9fb0c0; font-size:12px; margin-top:6px; }
.rate-pill { background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.15); border-radius:8px; padding:8px 20px; text-align:center; }
.rate-pill .num { font-size:26px; font-weight:700; }
.rate-pill .cap { font-size:11px; color:#9fb0c0; }
.wrap { max-width:1240px; margin:0 auto; padding:24px 20px 60px; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:14px; margin-bottom:22px; }
.card { background:#fff; border:1px solid var(--line); border-radius:10px; padding:16px 18px; }
.card .label { color:var(--muted); font-size:12px; }
.card .value { font-size:28px; font-weight:700; margin-top:6px; }
.card .sub { color:var(--muted); font-size:11px; margin-top:4px; }
.c-pass .value { color:var(--pass); } .c-fail .value { color:var(--fail); } .c-warn .value { color:var(--warn); }
.panel { background:#fff; border:1px solid var(--line); border-radius:10px; margin-bottom:22px; overflow:hidden; }
.panel > h2 { font-size:15px; font-weight:600; padding:14px 20px; border-bottom:1px solid var(--line); background:#fbfcfd; }
.panel .body { padding:18px 20px; }
.cat { display:grid; grid-template-columns:170px 1fr 96px; gap:14px; align-items:center; padding:7px 0; font-size:13px; }
.cat .bar { height:9px; background:#edf0f3; border-radius:6px; overflow:hidden; }
.cat .bar i { display:block; height:100%; background:var(--pass); }
.cat .bar i.has-fail { background:linear-gradient(90deg,var(--pass) 0 62%,var(--fail) 62% 100%); }
.tl-row { display:grid; grid-template-columns:220px 1fr 66px 66px; gap:12px; align-items:center; padding:3px 0; font-size:12px; }
.tl-name { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.tl-track { position:relative; height:13px; background:#f0f2f5; border-radius:7px; }
.tl-bar { position:absolute; top:0; height:13px; border-radius:7px; min-width:3px; }
.tl-bar.passed { background:var(--pass); } .tl-bar.failed,.tl-bar.error { background:var(--fail); }
.tl-bar.skipped,.tl-bar.aborted { background:var(--skip); } .tl-bar.pending,.tl-bar.running { background:#c9d1d9; }
.fail-card { border:1px solid #f3c9c9; border-left:4px solid var(--fail); border-radius:8px; padding:16px 18px; margin-bottom:14px; background:#fffafa; }
.fail-card:last-child { margin-bottom:0; }
.fail-head { display:flex; justify-content:space-between; gap:12px; align-items:baseline; flex-wrap:wrap; }
.fail-head .name { font-weight:600; font-size:15px; }
.fail-head .cat-tag { color:var(--muted); font-size:12px; font-weight:400; margin-left:8px; }
.errpoint { margin-top:12px; background:#fff; border:1px solid #f0d6d6; border-radius:6px; padding:12px 14px; }
.errpoint .where { color:var(--fail); font-weight:600; font-size:13px; }
.errpoint .row { display:grid; grid-template-columns:56px 1fr; gap:10px; margin-top:8px; }
.errpoint .k { color:var(--muted); font-size:12px; }
.errpoint .v { font-size:13px; word-break:break-all; white-space:pre-wrap; }
.errpoint .v.code { background:#f6f8fa; border:1px solid var(--line); border-radius:5px; padding:6px 8px; font-family:Consolas,monospace; font-size:12px; color:#b42318; }
.shots { display:flex; gap:10px; flex-wrap:wrap; margin-top:12px; }
.shots img { width:160px; border:1px solid var(--line); border-radius:6px; display:block; }
.shots .cap { font-size:11px; color:var(--muted); margin-top:4px; max-width:160px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
details.case { border:1px solid var(--line); border-radius:8px; margin-bottom:10px; background:#fff; }
details.case > summary { list-style:none; cursor:pointer; padding:12px 16px; display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
details.case > summary::-webkit-details-marker { display:none; }
details.case[open] > summary { border-bottom:1px solid var(--line); background:#fbfcfd; }
details.case .name { font-weight:600; }
details.case .grow { flex:1; }
.st { display:inline-flex; align-items:center; gap:5px; padding:2px 9px; border-radius:11px; font-size:12px; font-weight:600; white-space:nowrap; }
.st-passed { background:#e8f6ec; color:var(--pass); }
.st-failed { background:#fdecec; color:var(--fail); }
.st-error { background:#fdecec; color:#c4320a; }
.st-skipped { background:#fdf6e3; color:var(--warn); }
.st-aborted,.st-pending,.st-running { background:#eef1f4; color:#5b6770; }
.prio { font-size:11px; border-radius:9px; padding:1px 8px; border:1px solid var(--line); color:var(--muted); }
.prio-p0 { color:#b42318; border-color:#f0c2c0; background:#fff5f5; }
.prio-p1 { color:#b25e09; border-color:#f3dcbb; background:#fffaf0; }
.prio-p2 { color:#1d4ed8; border-color:#cad9f7; background:#f2f6ff; }
.prio-p3 { color:#5b6770; border-color:#e1e5e9; background:#f7f8fa; }
.steps { padding:0 16px 12px; }
table.steps { width:100%; border-collapse:collapse; font-size:12.5px; }
table.steps th { text-align:left; color:var(--muted); font-weight:600; padding:8px; border-bottom:1px solid var(--line); font-size:11.5px; }
table.steps td { padding:8px; border-bottom:1px solid #f0f2f5; vertical-align:top; }
table.steps tr:last-child td { border-bottom:none; }
.step-desc .err { margin-top:5px; color:#b42318; font-size:12px; background:#fff5f5; border-left:3px solid var(--fail); padding:5px 8px; border-radius:4px; white-space:pre-wrap; word-break:break-all; }
.step-desc .step-params { margin-top:4px; color:var(--muted); font-size:11px; font-family:Consolas,monospace; background:#f6f8fa; border:1px solid var(--line); padding:3px 6px; border-radius:4px; max-width:400px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.step-desc .step-expected { margin-top:3px; color:#1d4ed8; font-size:11.5px; }
.step-attachments { margin-top:5px; display:flex; gap:6px; flex-wrap:wrap; align-items:center; }
.step-attachments img { border:1px solid var(--line); border-radius:4px; }
.case-attachments { padding:8px 16px 12px; display:flex; gap:8px; flex-wrap:wrap; align-items:center; font-size:12px; }
.att-link { display:inline-flex; align-items:center; gap:3px; padding:3px 8px; background:#f0f4ff; border:1px solid #cad9f7; border-radius:5px; font-size:12px; color:#1d4ed8; }
.att-link:hover { background:#e0eaff; }
.dot { display:inline-block; width:16px; text-align:center; font-weight:700; }
.dot.passed { color:var(--pass); } .dot.failed,.dot.error { color:var(--fail); }
.dot.skipped { color:var(--warn); } .dot.aborted,.dot.pending,.dot.running { color:var(--skip); }
.muted { color:var(--muted); }
.empty { color:var(--muted); font-size:13px; padding:6px 0; }
.footer { text-align:center; color:#a3adb6; font-size:12px; margin-top:30px; }
@media print { .topbar { -webkit-print-color-adjust:exact; print-color-adjust:exact; } .panel,details.case { break-inside:avoid; } }
</style>
</head>
<body>
<div class="topbar">
  <div>
    <h1>{{ suite_name }} · 测试报告</h1>
    <div class="meta">生成时间 {{ generated_at }} · 共 {{ total_count }} 条用例 / {{ total_steps }} 个步骤 · 总耗时 {{ total_duration }}s</div>
  </div>
  <div class="rate-pill">
    <div class="num">{{ pass_rate }}%</div>
    <div class="cap">用例通过率</div>
  </div>
</div>

<div class="wrap">
  <div class="cards">
    <div class="card"><div class="label">用例总数</div><div class="value">{{ total_count }}</div><div class="sub">步骤 {{ total_steps }} 个</div></div>
    <div class="card c-pass"><div class="label">通过</div><div class="value">{{ passed_count }}</div><div class="sub">步骤通过 {{ passed_steps }}</div></div>
    <div class="card c-fail"><div class="label">失败 / 错误</div><div class="value">{{ failed_count + error_count }}</div><div class="sub">失败 {{ failed_count }} · 错误 {{ error_count }}</div></div>
    <div class="card c-warn"><div class="label">跳过 / 中止</div><div class="value">{{ skipped_count + aborted_count }}</div><div class="sub">跳过 {{ skipped_count }} · 中止 {{ aborted_count }}</div></div>
    <div class="card"><div class="label">步骤通过率</div><div class="value">{% if total_steps %}{{ "%.0f"|format(passed_steps / total_steps * 100) }}{% else %}0{% endif %}%</div><div class="sub">{{ passed_steps }} / {{ total_steps }}</div></div>
    <div class="card"><div class="label">总耗时</div><div class="value">{{ total_duration }}s</div><div class="sub">平均 {{ "%.2f"|format(total_duration / total_count if total_count else 0) }}s / 用例</div></div>
  </div>

  {% if category_stats %}
  <div class="panel">
    <h2>分类概览</h2>
    <div class="body">
      {% for cat, stats in category_stats.items() %}
      {% set rate = (stats.passed / stats.total * 100) if stats.total > 0 else 0 %}
      <div class="cat">
        <div>{{ cat }}</div>
        <div class="bar"><i class="{% if stats.failed %}has-fail{% endif %}" style="width:{{ "%.1f"|format(rate) }}%"></i></div>
        <div class="muted">{{ stats.passed }}/{{ stats.total }} · {{ "%.0f"|format(rate) }}%</div>
      </div>
      {% endfor %}
    </div>
  </div>
  {% endif %}

  {% if timeline %}
  <div class="panel">
    <h2>执行时间线</h2>
    <div class="body">
      {% for row in timeline %}
      <div class="tl-row">
        <div class="tl-name" title="{{ row.name }}">{{ row.name }}</div>
        <div class="tl-track"><div class="tl-bar {{ row.status }}" style="left:{{ row.left }}%;width:{{ row.width }}%"></div></div>
        <div class="muted">{{ row.start }}</div>
        <div class="muted">{{ row.duration }}s</div>
      </div>
      {% endfor %}
    </div>
  </div>
  {% endif %}

  {% if failed_details %}
  <div class="panel">
    <h2>失败详情（{{ failed_details|length }}）</h2>
    <div class="body">
      {% for v in failed_details %}
      <div class="fail-card">
        <div class="fail-head">
          <div><span class="name">{{ v.case.name }}</span><span class="cat-tag">{{ v.case.category or '未分类' }}</span></div>
          <div>
            <span class="prio prio-{{ v.case.priority.value|lower }}">{{ v.case.priority.value }}</span>
            <span class="st st-{{ v.case.status.value }}">{{ v.status_text }}</span>
          </div>
        </div>
        {% if v.failed_step %}
        <div class="errpoint">
          <div class="where">错误点 · {{ v.failed_step.label }} · {{ v.failed_step.type }}</div>
          <div class="row"><div class="k">操作</div><div class="v">{{ v.failed_step.description }}</div></div>
          {% if v.failed_step.expected %}<div class="row"><div class="k">预期</div><div class="v">{{ v.failed_step.expected }}</div></div>{% endif %}
          {% if v.failed_step.actual %}<div class="row"><div class="k">实际</div><div class="v">{{ v.failed_step.actual }}</div></div>{% endif %}
          <div class="row"><div class="k">原因</div><div class="v code">{{ v.failed_step.error or v.case.error_message or '步骤执行失败' }}</div></div>
        </div>
        {% else %}
        <div class="errpoint">
          <div class="where">错误点 · 未定位到具体步骤</div>
          <div class="row"><div class="k">原因</div><div class="v code">{{ v.case.error_message or '用例执行失败' }}</div></div>
        </div>
        {% endif %}
        {% if v.shots %}
        <div class="shots">
          {% for s in v.shots %}<a href="{{ s.url }}" target="_blank"><img src="{{ s.url }}" alt="{{ s.name }}" loading="lazy"><div class="cap">{{ s.name }}</div></a>{% endfor %}
        </div>
        {% endif %}
      </div>
      {% endfor %}
    </div>
  </div>
  {% endif %}

  <div class="panel">
    <h2>用例明细</h2>
    <div class="body">
      {% for v in case_views %}
      <details class="case" {% if v.case.status.value in ('failed', 'error') %}open{% endif %}>
        <summary>
          <span class="dot {{ v.case.status.value }}">{{ v.icon }}</span>
          <span class="name">{{ v.case.name }}</span>
          <span class="prio prio-{{ v.case.priority.value|lower }}">{{ v.case.priority.value }}</span>
          <span class="muted">{{ v.case.category or '未分类' }}</span>
          <span class="grow"></span>
          <span class="st st-{{ v.case.status.value }}">{{ v.status_text }}</span>
          <span class="muted">{{ "%.2f"|format(v.case.duration) }}s · {{ v.step_count }} 步</span>
        </summary>
        <div class="steps">
          {% if v.steps %}
          <table class="steps">
            <thead>
              <tr><th style="width:56px">#</th><th style="width:92px">类型</th><th>操作 / 断言</th><th style="width:86px">结果</th><th style="width:62px">耗时</th></tr>
            </thead>
            <tbody>
              {% for s in v.steps %}
              <tr>
                <td class="muted">{{ s.seq }}</td>
                <td class="muted">{{ s.type }}</td>
                <td class="step-desc">{{ s.description }}
                  {% if s.params %}<div class="step-params" title="{{ s.params }}">参数: {{ s.params|truncate(80) }}</div>{% endif %}
                  {% if s.expected %}<div class="step-expected">预期: {{ s.expected }}</div>{% endif %}
                  {% if s.error %}<div class="err">{{ s.error }}{% if s.actual %}<br>实际：{{ s.actual }}{% endif %}</div>{% endif %}
                  {% if s.attachments %}
                  <div class="step-attachments">
                    {% for att in s.attachments %}
                      {% if att.is_image %}
                        <a href="{{ att.url }}" target="_blank"><img src="{{ att.url }}" alt="{{ att.name }}" loading="lazy" style="max-width:120px;max-height:80px;margin:2px;"></a>
                      {% else %}
                        <a href="{{ att.url }}" target="_blank" class="att-link">📎 {{ att.name }}</a>
                      {% endif %}
                    {% endfor %}
                  </div>
                  {% endif %}
                </td>
                <td><span class="dot {{ s.status }}">{{ s.icon }}</span> <span class="muted">{{ s.status_text }}</span></td>
                <td class="muted">{{ "%.2f"|format(s.duration) }}s</td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
          {% else %}
          <div class="empty">该用例没有步骤记录</div>
          {% endif %}
          {% if v.shots %}
          <div class="shots">
            {% for s in v.shots %}{% if s.is_image %}<a href="{{ s.url }}" target="_blank"><img src="{{ s.url }}" alt="{{ s.name }}" loading="lazy"><div class="cap">{{ s.name }}</div></a>{% endif %}{% endfor %}
          </div>
          {% endif %}
          {% if v.other_attachments %}
          <div class="case-attachments">
            <strong>附件:</strong>
            {% for att in v.other_attachments %}
              <a href="{{ att.url }}" target="_blank" class="att-link">📎 {{ att.name }}</a>
            {% endfor %}
          </div>
          {% endif %}
        </div>
      </details>
      {% endfor %}
    </div>
  </div>

  <div class="footer">车机测试应用程序 · 报告自动生成于 {{ generated_at }}</div>
</div>
</body>
</html>'''
