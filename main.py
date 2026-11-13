#!/usr/bin/env python3
"""
车机测试应用程序 - 主入口
==========================
启动 PyQt6 GUI 应用。

用法:
    python main.py              # 启动 GUI
    python main.py --cli        # 命令行模式（无 GUI）
    python main.py --help       # 帮助信息
"""

import sys
import json
import argparse
from pathlib import Path

# 确保项目根目录在 Python 路径中
ROOT_DIR = Path(__file__).parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from loguru import logger


def setup_logging():
    """配置日志"""
    log_dir = Path.home() / ".vehicle_test_app" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # 移除默认处理器
    logger.remove()

    # 控制台输出
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        level="INFO",
        colorize=True,
    )

    # 文件输出
    logger.add(
        str(log_dir / "app_{time:YYYY-MM-DD}.log"),
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {module}:{function}:{line} | {message}",
        level="DEBUG",
        rotation="50 MB",
        retention="30 days",
        encoding="utf-8",
    )


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="车机测试应用程序")
    parser.add_argument("--cli", action="store_true", help="命令行模式（无 GUI）")
    parser.add_argument("--config", type=str, help="配置文件路径")
    parser.add_argument("--import", type=str, dest="import_file", help="导入测试用例文件")
    parser.add_argument("--run", type=str, help="执行指定套件")
    parser.add_argument("--report", type=str, help="导出报告路径")
    return parser.parse_args()


def run_gui():
    """启动 GUI 模式"""
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import Qt

    from ui.main_window import MainWindow
    from ui.styles.theme import apply_theme

    # 高 DPI 支持
    app = QApplication(sys.argv)
    app.setApplicationName("车机测试应用程序")
    app.setApplicationVersion("1.0.0")

    # 应用全局主题
    apply_theme(app)

    # 创建并显示主窗口
    window = MainWindow()
    window.show()

    logger.info("应用程序已启动")
    return app.exec()


def run_cli(args):
    """命令行模式"""
    from config.settings import ConfigManager
    from core.testcase.parser import TestCaseParser
    from core.testcase.database import TestDatabase
    from core.device.manager import DeviceManager
    from core.execution.engine import ExecutionEngine
    from core.execution.runner import TestRunner
    from core.reporting.generator import ReportGenerator

    config = ConfigManager()
    if args.config:
        config.load(args.config)
    else:
        config.load()

    db = TestDatabase(str(Path.home() / ".vehicle_test_app" / "test_results.db"))

    # 导入用例
    if args.import_file:
        logger.info(f"导入用例: {args.import_file}")
        result = TestCaseParser.parse_file(args.import_file)
        for case in result.test_cases:
            db.save_case(case)
        logger.info(f"导入完成: {len(result.test_cases)} 个用例")
        if result.errors:
            for err in result.errors:
                logger.warning(f"  警告: {err}")

    # 执行测试
    if args.run:
        logger.info(f"执行套件: {args.run}")

        # CLI 模式也需要 QApplication（Qt 信号依赖）
        from PyQt6.QtWidgets import QApplication
        _cli_app = QApplication(sys.argv)

        dm = DeviceManager()
        # 扫描并自动连接 ADB 设备
        dm.scan_devices()
        from core.device.manager import DeviceType
        for dev_info in dm.last_scan_results:
            if dev_info.device_type == DeviceType.ADB:
                device = dm.create_device_from_info(dev_info)
                if device:
                    dm.connect_device(device)
                    logger.info(f"CLI 自动连接: {dev_info.name}")

        engine = ExecutionEngine(dm)
        runner = TestRunner(engine, dm)

        cases = db.list_cases(limit=10000)
        if not cases:
            logger.error("没有可执行的用例")
            return 1

        # 按名称/分类过滤（--run 支持用例名或分类关键字）
        keyword = args.run.strip()
        matched = [c for c in cases
                   if keyword in c.name or keyword in (c.category or "")]
        if matched:
            cases = matched
        elif len(cases) > 1:
            logger.error(f"未找到名称或分类包含 '{keyword}' 的用例")
            return 1

        from core.testcase.model import TestSuite
        suite = TestSuite(name=args.run)
        for c in cases:
            suite.add_case(c)

        import time
        start = time.time()
        engine.execute_batch(suite)

        # 等待执行完成
        while engine.is_running:
            time.sleep(0.5)

        duration = time.time() - start
        logger.info(f"执行完成: {suite.pass_rate:.1f}% 通过率 ({duration:.1f}s)")

        # 保存结果
        db.save_execution(suite)

    # 导出报告
    if args.report:
        logger.info(f"导出报告: {args.report}")
        gen = ReportGenerator()
        from core.testcase.model import TestSuite

        # 优先使用最近一次执行记录生成报告，否则使用全部用例
        suite = None
        history = db.get_execution_history(limit=1)
        if history:
            rec = history[0]
            try:
                data = json.loads(rec.get("record_data") or "{}")
                if data.get("test_cases"):
                    suite = TestSuite.from_dict(data)
                    suite.calculate_stats()
                    logger.info(
                        f"使用最近一次执行记录生成报告 (记录ID={rec.get('record_id')})"
                    )
            except Exception as e:
                logger.warning(f"解析执行记录失败，回退到全部用例: {e}")
        if suite is None:
            suite = TestSuite(name="CLI 报告")
            for c in db.list_cases(limit=10000):
                suite.add_case(c)

        ext = Path(args.report).suffix.lower()
        if ext == ".allure" or Path(args.report).name == "allure-results":
            results = gen.generate_allure(suite, args.report)
            logger.info(f'Allure 结果已写入: {results}')
            logger.info(f'查看报告: allure serve "{results}"')
        elif ext == ".html":
            gen.generate_html(suite, args.report)
        elif ext == ".pdf":
            gen.generate_pdf(suite, args.report)
        elif ext == ".xlsx":
            gen.generate_excel(suite, args.report)
        elif ext == ".xml":
            gen.generate_junit_xml(suite, args.report)

    db.close()
    return 0


def main():
    """主入口"""
    setup_logging()
    args = parse_args()

    logger.info("=" * 50)
    logger.info("  车机测试应用程序 v1.0")
    logger.info("=" * 50)

    if args.cli or args.import_file or args.run or args.report:
        sys.exit(run_cli(args))
    else:
        sys.exit(run_gui())


if __name__ == "__main__":
    main()
