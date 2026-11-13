"""
Allure 报告服务
===============
把 allure-results 生成为标准 Allure 报告，并在本机启动一个只读 HTTP 服务，
返回可直接打开的链接（如 http://127.0.0.1:xxxx）。

依赖 Allure 命令行（allure 2.x + Java）。未安装时返回明确提示，
应用内的 Allure 风格 HTML 报告仍可正常使用。
"""

import shutil
import socket
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from loguru import logger

# Windows 下隐藏 subprocess 控制台窗口
if sys.platform == "win32":
    _CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
else:
    _CREATE_NO_WINDOW = 0


class _QuietHandler(SimpleHTTPRequestHandler):
    """静默的静态文件处理器（避免刷控制台）"""

    def log_message(self, fmt, *args):  # noqa: A003
        logger.debug("allure http: " + fmt % args)

    def end_headers(self):
        # 允许本地任意端口页面读取报告数据
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def allure_available() -> bool:
    """Allure 命令行是否可用"""
    return shutil.which("allure") is not None


def allure_version() -> str:
    """返回 Allure 版本号，不可用时返回空串"""
    exe = shutil.which("allure")
    if not exe:
        return ""
    try:
        out = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=20,
            creationflags=_CREATE_NO_WINDOW,
        )
        return (out.stdout or out.stderr or "").strip().splitlines()[0]
    except Exception as e:
        logger.debug(f"读取 Allure 版本失败: {e}")
        return ""


def generate_allure_report(results_dir: str,
                           report_dir: str = "reports/allure-report"
                           ) -> str:
    """
    调用 allure generate 把结果渲染成静态报告。

    返回报告目录；失败抛 RuntimeError（带可读原因）。
    """
    exe = shutil.which("allure")
    if not exe:
        raise RuntimeError(
            "未检测到 Allure 命令行。请安装 Allure（需 Java 8+），"
            "例如：scoop install allure / choco install allure，"
            "或从 https://github.com/allure-framework/allure2/releases 下载后加入 PATH。"
        )
    out = Path(report_dir)
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        proc = subprocess.run(
            [exe, "generate", str(Path(results_dir)), "-o", str(out),
             "--clean"],
            capture_output=True, text=True, timeout=180,
            creationflags=_CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Allure 报告生成超时（>180s）")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise RuntimeError("Allure 生成失败: " +
                           (detail[-1] if detail else f"exit {proc.returncode}"))
    if not (out / "index.html").exists():
        raise RuntimeError("Allure 生成完成但未找到 index.html")
    logger.info(f"Allure 报告已生成: {out}")
    return str(out)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class AllureReportServer:
    """本地托管 Allure 静态报告，供浏览器打开链接"""

    def __init__(self):
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.urls = []          # 历史链接（端口可能变化）

    @property
    def url(self) -> str:
        return self.urls[-1] if self.urls else ""

    @property
    def running(self) -> bool:
        return self._server is not None

    def serve(self, report_dir: str) -> str:
        """启动本地 HTTP 服务，返回访问链接"""
        directory = Path(report_dir).resolve()
        if not (directory / "index.html").exists():
            raise RuntimeError(f"报告目录无效（缺少 index.html）: {directory}")

        self.stop()
        port = _free_port()
        handler = partial(_QuietHandler, directory=str(directory))
        self._server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True, name="AllureReportServer",
        )
        self._thread.start()
        url = f"http://127.0.0.1:{port}/index.html"
        self.urls.append(url)
        logger.info(f"Allure 报告服务已启动: {url}")
        return url

    def stop(self) -> None:
        if self._server is None:
            return
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception as e:
            logger.debug(f"停止 Allure 服务失败: {e}")
        finally:
            self._server = None
            self._thread = None


# 进程内共享一个服务实例（重复导出时切换端口即可）
allure_server = AllureReportServer()
