# 🚗 车机测试应用程序 (Vehicle Head-Unit Test App)

> 车载信息娱乐系统（车机 / IVI）自动化测试工具。支持通过 **串口 / 网络（TCP·UDP）/ ADB** 与车机通信，执行测试用例并生成多格式报告；内置 **AI 助手**（失败诊断、自动修复、自然语言生成用例、智能执行）、**Monkey 随机压力测试**、**CAN 总线监控** 与 **自学习知识库**。

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![GUI](https://img.shields.io/badge/GUI-PyQt6-green)](https://www.riverbankcomputing.com/software/pyqt/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

仓库地址：<https://github.com/Li-bot2026/vehicle_test_app>

---

## 目录

- [功能特性](#功能特性)
- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [安装](#安装)
- [运行](#运行)
- [使用指南](#使用指南)
- [扩展开发](#扩展开发)
- [打包发布](#打包发布)
- [测试](#测试)
- [配置](#配置)
- [常见问题](#常见问题)
- [许可证](#许可证)

---

## 功能特性

### 1. 设备连接管理
- **自动扫描**：串口 / 网络 / ADB 设备自动发现
- **手动连接**：输入 IP、端口、串口号等参数手动连接
- **多设备并行**：可同时连接多个设备
- **断线重连**：自动检测连接状态并尝试重连
- **实时状态**：界面实时显示设备连接状态

### 2. 测试用例管理
- **多格式导入**：Excel / CSV / JSON / YAML
- **可视化编辑**：内置用例编辑器，支持增删改查、多选批量操作
- **分类标签**：按分类、标签、优先级（P0 / P1 / P2）组织
- **参数化测试**：数据驱动，一套模板多组参数；子用例从 `parameter_sets` **动态展开**（树形结构，不重复入库）
- **参数表导入**：从 Excel / CSV / **Word 表格** 导入参数集，自动注入生成上下文
- **前置 / 后置条件**：`setup` / `teardown` 步骤

### 3. 测试执行引擎
- **多种执行模式**：单条 / 批量 / 定时执行
- **执行控制**：暂停、继续、中止、跳过
- **失败重试**：可配置重试次数与间隔
- **实时进度**：执行进度、当前用例、通过 / 失败统计
- **丰富步骤类型**：ADB 点击 / 滑动 / 按键 / 文本、`ui_tap`（控件定位）、`agent_task`（AI 智能执行）、等待、断言等
- **视觉兜底 + 坐标记忆**：控件定位失败时回退视觉 / OCR；成功坐标可记忆复用

### 4. 结果判定与断言
- **数值断言**：精确值比较，支持容差
- **范围断言**：最小值 / 最大值范围检查
- **状态断言**：字符串 / 枚举状态匹配
- **时序断言**：响应时间范围检查
- **自定义脚本断言**：Python 表达式

### 5. ADB 投屏与数据记录
- **实时投屏**：H.264 流（延迟约 0.1~0.3s），支持点击 / 滑动 / 按键
- **回退截图模式**：缺少解码依赖时自动降级，不影响用例执行
- **不自动弹窗**：连接 ADB 设备后**不会**自动打开投屏窗口，需手动通过菜单打开
- **实时日志**：按级别记录报文与事件
- **截图 / 录屏**：通过 ADB 截取车机屏幕
- **数据导出**：CSV / TXT

### 6. Monkey 随机压力测试
- **双停止上限**：`时长（秒）` 与 `总事件数` **先到先停**；事件数 `0 = 不限`（仅按时长停止），对应官方 Monkey 的 `COUNT` 语义
- **事件权重**：可配置点击 / 滑动 / 按键 / 文本等事件比例
- **可复现**：随机种子（seed）固定后结果可复现
- **节流**：事件间隔（throttle）可调
- **目标拉回**：被测应用退到后台时自动拉回前台
- **Crash / ANR 检测**：实时监测崩溃与无响应
- **执行控制**：暂停 / 继续 / 停止
- **实时统计**：六卡片统计 + 进度条（`n/N 次事件 | %`）+ 可过滤日志
- **模拟模式**：无真实设备时可在模拟环境验证流程

### 7. CAN 总线监控
- **DBC 解析**：加载 DBC 文件，按信号定义解析报文
- **报文过滤**：按 ID / 信号筛选
- **录制 / 回放**：记录总线数据并回放
- **虚拟 CAN**：无硬件时使用虚拟 CAN 总线模拟

### 8. 报告生成
- **多格式导出**：HTML（Allure 风格）/ PDF / Excel / JUnit XML
- **Allure 结果**：生成 `allure-results`，可用 `allure serve` 查看
- **统计图表**：通过率饼图、耗时柱状图
- **失败详情**：期望值 vs 实际值 + 完整日志
- **CI/CD 集成**：JUnit XML 兼容 Jenkins / GitLab CI

### 9. AI 智能助手
- **失败诊断**：用例失败时给出原因与解决方法，可选「让 AI 自动修复并重试」
- **自动修复**：调整超时、补等待、重连设备、替换步骤等（**断言失败不自动改**，避免掩盖缺陷）
- **用例生成**：自然语言描述需求，自动生成可执行用例并入库
- **执行规划**：识别用例库并按优先级 / 依赖规划执行顺序，一键自动执行
- **智能执行（agent_task）**：视觉自动探测 + 降级 OCR，理解界面并自主操作
- **离线可用**：未配置模型时回退内置规则引擎；兼容 OpenAI 接口（DeepSeek / 通义 / Moonshot / Ollama 等）

### 10. 知识库系统
- **自动学习**：AI 执行自主任务时自动从网络搜索应用规则并学习
- **知识存储**：每个应用的知识以 YAML 存储在 `knowledge/` 目录
- **知识管理**：UI 查看、编辑、导入、导出
- **多搜索引擎**：百度 → Bing → 搜狗（自动回退）
- **搜索缓存**：避免重复请求，提高学习效率
- **内置游戏引擎**：
  - **五子棋**：AI 对弈，VCF 强杀搜索 + α-β 剪枝
  - **扫雷**：视觉识别棋盘、规则推理、自动插旗 / 翻格

### 11. 配置与日志
- **多环境切换**：开发 / 测试 / 预生产
- **用户偏好**：设置持久化保存
- **分级日志**：DEBUG / INFO / WARN / ERROR，按级别 / 关键字过滤
- **审计日志**：关键操作留痕

---

## 技术栈

| 组件 | 技术选型 |
|------|---------|
| 语言 | Python 3.10+ |
| GUI | PyQt6 |
| 串口 | pyserial |
| 网络 | socket / asyncio |
| ADB | adb 命令行 + H.264 实时投屏 |
| 数据库 | SQLite（内置） |
| 报告 | Jinja2 + WeasyPrint + openpyxl |
| AI | OpenAI 兼容接口（urllib，零额外依赖） |
| OCR | rapidocr_onnxruntime + Pillow（可选） |
| 打包 | PyInstaller |
| 测试 | pytest |

---

## 项目结构

```
vehicle_test_app/
├── main.py                          # 应用入口（GUI / CLI）
├── requirements.txt                 # 依赖清单
├── config/
│   └── settings.py                  # 全局配置管理
├── core/
│   ├── device/
│   │   ├── manager.py               # 设备管理器（扫描/连接/重连）
│   │   ├── serial_device.py         # 串口设备
│   │   ├── network_device.py        # TCP/UDP 网络设备
│   │   ├── adb_device.py            # ADB 设备
│   │   ├── ui_automation.py         # UI 自动化（点击/滑动/文本/控件树）
│   │   ├── dbc_parser.py            # CAN DBC 文件解析
│   │   └── virtual_can.py           # 虚拟 CAN 总线（无硬件时模拟）
│   ├── testcase/
│   │   ├── model.py                 # 数据模型（TestCase/TestSuite/TestStep）
│   │   ├── parser.py                # 用例解析器（YAML/JSON/CSV/Excel）
│   │   ├── database.py              # SQLite 数据库
│   │   ├── editor.py                # 用例编辑逻辑
│   │   └── param_importer.py        # 参数表导入（Excel/CSV/Word）
│   ├── execution/
│   │   ├── engine.py                # 执行引擎（调度/控制/重试）
│   │   └── runner.py                # 测试运行器（步骤处理器）
│   ├── assertion/
│   │   └── engine.py                # 断言引擎
│   ├── reporting/
│   │   ├── generator.py             # 报告生成器（HTML/PDF/Excel/JUnit）
│   │   └── allure_report.py         # Allure 风格报告
│   ├── testing/
│   │   └── monkey.py                # Monkey 随机压力测试引擎
│   └── ai/
│       ├── assistant.py             # AI 助手门面
│       ├── client.py                # OpenAI 兼容客户端
│       ├── analyzer.py              # 失败诊断（LLM + 内置规则）
│       ├── repair.py                # 修复动作应用（ADB 白名单）
│       ├── generator.py             # 用例生成与执行规划
│       ├── adaptive_generator.py    # 自适应生成
│       ├── intelligent_executor.py  # 智能执行器（agent_task）
│       ├── prompts.py               # 提示词模板
│       ├── knowledge.py             # 知识库管理（YAML 存储）
│       ├── knowledge_graph.py       # 知识图谱
│       ├── web_learner.py           # 网络学习器（搜索+抓取+LLM总结）
│       ├── semantic_learner.py      # 语义学习
│       ├── experience_learner.py    # 经验学习
│       ├── pattern_recognizer.py    # 模式识别
│       ├── device_context.py        # 设备上下文
│       ├── ui_memory.py             # UI 坐标记忆
│       ├── offline_ocr.py           # 离线 OCR
│       ├── board_vision.py          # 棋盘视觉识别
│       ├── gomoku.py                # 五子棋决策引擎（离线）
│       └── minesweeper.py           # 扫雷视觉/决策引擎（离线）
├── ui/
│   ├── main_window.py               # 主窗口
│   ├── panels/
│   │   ├── connection_panel.py      # 设备连接面板
│   │   ├── test_panel.py            # 测试用例面板
│   │   ├── report_panel.py          # 报告面板
│   │   ├── knowledge_panel.py       # 知识库管理面板
│   │   ├── can_monitor_panel.py     # CAN 监控面板
│   │   └── monkey_panel.py          # Monkey 测试面板
│   ├── widgets/
│   │   ├── adb_screen.py            # ADB 投屏
│   │   ├── live_stream.py           # H.264 实时画面流（回退截图模式）
│   │   ├── execution_view.py        # 执行画面（车机画面 + 步骤进度 + 日志）
│   │   ├── ai_assistant_dialog.py   # AI 助手对话框
│   │   ├── ai_error_dialog.py       # AI 失败诊断对话框
│   │   ├── ai_worker.py             # AI 后台任务
│   │   ├── test_case_editor.py      # 用例编辑器
│   │   ├── preferences_dialog.py    # 偏好设置
│   │   ├── environment_dialog.py    # 环境切换
│   │   ├── schedule_dialog.py       # 定时执行
│   │   └── dialog_utils.py          # 对话框工具
│   └── styles/
│       └── theme.py                 # 全局主题（QSS）
├── knowledge/                       # 知识库目录（YAML 格式）
│   └── com.wt.sweepmine.yaml        # 扫雷知识示例
├── templates/
│   └── report_template.html         # HTML 报告模板
├── examples/
│   └── sample_test_cases.yaml       # 示例测试用例
├── tests/                           # 单元测试
└── plugins/                         # 插件扩展
```

---

## 安装

### 1. 环境要求

- **Python 3.10 或更高版本**
- pip 包管理器
- （可选）ADB platform-tools，用于连接真实车机

### 2. 克隆与安装依赖

```bash
# 克隆仓库
git clone https://github.com/Li-bot2026/vehicle_test_app.git
cd vehicle_test_app

# 创建虚拟环境（推荐）
python -m venv venv
source venv/bin/activate      # Linux / macOS
venv\Scripts\activate         # Windows

# 安装依赖
pip install -r requirements.txt
```

### 3. 可选依赖与自动降级

部分依赖为**可选**，缺失时应用自动降级，不影响核心功能：

| 依赖 | 用途 | 缺失时的行为 |
|------|------|-------------|
| `av` + `numpy` | H.264 实时投屏解码 | 回退为截图模式（帧率下降） |
| `rapidocr_onnxruntime` + `Pillow` | 离线 OCR 文字定位 | 降级为控件 / 坐标定位 |
| `weasyprint` | HTML 转 PDF 报告 | PDF 导出不可用（需 GTK3 / Pango 系统依赖） |
| `python-docx` | Word 参数表导入 | 无法解析 Word 文件（Excel / CSV 仍可用） |

### 4. ADB 调试（可选）

使用 ADB 连接车机前，请确保：

- 车机已开启 USB 调试并在设备上允许授权
- 电脑已安装 platform-tools，`adb devices` 能识别设备
- **先启动 ADB 守护进程**：`adb start-server`（否则应用扫描可能超时、检测不到设备）

---

## 运行

### GUI 模式

```bash
python main.py
```

启动后界面分为多个区域：
- **左侧**：设备连接面板（扫描、连接、管理设备）
- **中间标签页**：测试用例 / 执行画面 / 测试报告 / 知识库 / CAN 监控 / Monkey 测试

### CLI 模式

```bash
# 导入测试用例
python main.py --cli --import examples/sample_test_cases.yaml

# 执行测试（--run 支持用例名或分类关键字）
python main.py --cli --run "全量测试"

# 导出报告（按扩展名自动选择格式）
python main.py --cli --report reports/result.html
python main.py --cli --report reports/result.pdf
python main.py --cli --report reports/result.xlsx
python main.py --cli --report reports/result.xml

# 生成 Allure 结果
python main.py --cli --report reports/allure-results
allure serve reports/allure-results
```

### 组合使用

```bash
# 导入 + 执行 + 导出
python main.py --cli \
    --import examples/sample_test_cases.yaml \
    --run "冒烟测试" \
    --report reports/smoke_test.html
```

> **Windows 控制台中文乱码**：设置环境变量 `PYTHONIOENCODING=utf-8` 后再运行。

---

## 使用指南

### 1. 连接设备
1. 点击左侧「扫描设备」按钮
2. 在「可用设备」列表中双击要连接的设备
3. 或手动输入地址连接（如 `192.168.1.100:8080`）

### 2. 导入用例
1. 菜单栏 → 文件 → 导入用例
2. 选择 YAML / JSON / CSV / Excel 文件
3. 用例自动加载到数据库

### 3. 执行测试
- **执行全部**：工具栏「▶ 执行」
- **执行选中**：表格中选中用例后点击「▶ 执行选中」
- **单条执行**：右键用例 → 执行此用例

### 4. 查看报告
1. 切换到「📄 测试报告」标签页
2. 查看历史执行记录
3. 点击「导出」生成 HTML / PDF / Excel / JUnit XML / Allure 报告

### 5. 执行画面
1. 执行用例时自动切换到「执行画面」标签页
2. 左侧显示车机实时画面（H.264 流），可直接点击 / 滑动操作
3. 状态栏显示投屏模式、目标帧率与实测帧率
4. 右侧同步显示步骤进度与实时日志

### 6. Monkey 测试
1. 切换到「🐒 Monkey 测试」标签页
2. 选择目标设备与被测应用包名
3. 配置参数：
   - **时长**：运行秒数上限
   - **总事件数**：事件总数上限，`不限` = 仅按时长停止；**两者先到先停**
   - **事件权重 / 节流 / 随机种子**：控制事件分布、间隔与可复现性
   - **Crash / ANR 检测**：勾选后实时监测
4. 点击「开始」，实时查看六卡片统计、进度条（`n/N 次事件 | %`）与可过滤日志
5. 无真实设备时可启用**模拟模式**验证流程

### 7. 使用 AI 助手
1. 工具栏「🤖 AI 助手」或菜单「AI → AI 测试助手」
2. **生成用例**：描述需求（如"启动应用 com.android.settings 并校验前台页面"），预览后保存或直接执行
3. **规划执行**：让 AI 识别用例库并给出执行顺序，一键自动执行
4. **失败诊断**：用例失败时自动弹出原因与解决方法，可勾选修复动作让 AI 修改用例并自动重跑
5. 在 设置 → 偏好设置 → AI 助手 中配置模型接口与 API Key；留空则使用内置规则引擎（离线可用）

### 8. 使用知识库
1. 切换到「📚 知识库」标签页
2. **查看 / 编辑**：左侧列表选择应用，查看结构化视图或原始 YAML，点击「✏️ 编辑」修改
3. **重新学习**：「🌐 重新学习」强制从网络重新搜索学习
4. **导入 / 导出**：支持单个或全部知识的 YAML / JSON 导入导出

**自动学习流程**：AI 执行自主任务时检测当前前台应用 → 知识库无该应用则自动网络搜索学习 → 保存到 `knowledge/` 目录，下次直接使用。

---

## 扩展开发

### 添加新的步骤类型

```python
from core.testcase.model import StepType
from core.execution.runner import TestRunner

# 1. 在 StepType 枚举中添加新类型
# StepType.MY_CUSTOM = "my_custom"

# 2. 实现处理函数
def handle_my_custom(step):
    result = do_something(step.params)
    return result == expected

# 3. 注册到运行器
runner.register_handler(StepType.MY_CUSTOM, handle_my_custom)
```

### 添加新的断言类型

```python
from core.assertion.engine import AssertionEngine

engine = AssertionEngine()

def assert_my_condition(actual, expected):
    return custom_check(actual, expected)
# 在用例中使用 assert_custom 步骤类型
```

### 添加新的设备类型

```python
from core.device.manager import BaseDevice, DeviceType

class MyDevice(BaseDevice):
    def connect(self) -> bool: ...
    def disconnect(self) -> None: ...
    def send(self, data: bytes) -> bool: ...
    def recv(self, timeout: float = 1.0): ...
    def is_connected(self) -> bool: ...
```

### 添加新的游戏引擎

```python
from core.ai.game_engine import BaseGameEngine, GameEngineRegistry

class MyGameEngine(BaseGameEngine):
    def __init__(self):
        super().__init__("我的游戏")
    def detect_board(self, screenshot: bytes): ...
    def choose_move(self, board, **kwargs):
        return {"action": "tap", "row": r, "col": c}
    def execute_move(self, device, move, board):
        x, y = self._calculate_coordinates(move, board)
        device.input_tap(x, y)
        return True
    def is_game_over(self, screenshot, board):
        return False, None

GameEngineRegistry.register("my_game", MyGameEngine())
```

游戏引擎按应用包名自动识别，可在 `GameEngineRegistry.detect_game()` 中添加包名映射。

### 插件开发

将自定义功能放入 `plugins/` 目录，应用启动时自动加载。

---

## 打包发布

```bash
pyinstaller --name="车机测试工具" \
    --windowed \
    --onefile \
    --add-data "templates;templates" \
    --add-data "examples;examples" \
    --icon=app.ico \
    main.py
```

生成的可执行文件在 `dist/` 目录。

> Linux / macOS 下 `--add-data` 分隔符为 `:`（如 `templates:templates`）。

---

## 测试

```bash
# 运行单元测试
pytest tests/ -v

# 运行并生成覆盖率报告
pytest tests/ --cov=core --cov-report=html
```

---

## 配置

配置文件位于 `~/.vehicle_test_app/config.yaml`，首次运行自动创建。日志位于 `~/.vehicle_test_app/logs/`，结果数据库为 `~/.vehicle_test_app/test_results.db`。

主要配置项：

```yaml
serial:
  port: COM3               # 串口号
  baudrate: 115200         # 波特率

network:
  protocol: tcp            # 协议
  host: 192.168.1.100      # 目标 IP
  port: 8080               # 目标端口

execution:
  retry_count: 2           # 失败重试次数
  retry_delay: 1.0         # 重试间隔
  step_timeout: 30.0       # 单步超时

log:
  level: INFO              # 日志级别

ai:
  enabled: true                          # 启用 AI 助手
  base_url: https://api.deepseek.com/v1  # OpenAI 兼容接口
  api_key: ""                            # 留空使用内置规则引擎
  model: deepseek-chat
  auto_diagnose: true                    # 失败时自动弹出诊断
  auto_repair: false                     # 无需确认直接修复
  max_fix_rounds: 2                      # 单用例最大自动修复次数
  offline_fallback: true                 # AI 不可用时回退规则引擎
```

> ⚠️ **请勿将真实 API Key 提交到版本库。** `config.yaml` 位于用户目录，不在仓库内。

---

## 常见问题

**Q: 扫描不到 ADB 设备？**
A: 先运行 `adb start-server` 启动守护进程，再确认 `adb devices` 能识别车机；检查 USB 调试授权与数据线。

**Q: Monkey 测试没有真实设备怎么办？**
A: 启用模拟模式即可验证事件生成、统计与停止逻辑；总事件数与时长上限先到先停。

**Q: AI 诊断不可用 / 生成用例走了内置模板？**
A: 在 设置 → 偏好设置 → AI 助手 中填写接口地址、API Key 与模型名称，点击「测试连接」确认；未配置时自动使用内置规则引擎（离线可用）。

**Q: 报告生成 PDF 失败？**
A: 确保安装了 WeasyPrint 及其系统依赖（GTK3、Pango 等）。Windows 用户可参考 [WeasyPrint 安装文档](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html)。

**Q: 投屏帧率很低？**
A: 缺少 `av` / `numpy` 时会回退截图模式，大分辨率车机截图较慢。安装 `pip install av numpy` 启用 H.264 实时流。

**Q: Windows 控制台中文乱码？**
A: 设置 `PYTHONIOENCODING=utf-8` 后运行。

**Q: 知识库为空 / 学习失败？**
A: 知识库需配置 AI 客户端后才能自动学习；百度搜索可能被反爬拦截，系统自动回退 Bing / 搜狗；也可在知识库面板手动编辑或在 `knowledge/` 目录创建 YAML。

**Q: 游戏引擎无法识别应用？**
A: 引擎按包名自动识别。可在 `GameEngineRegistry.detect_game()` 添加映射；用 `adb shell dumpsys window | grep mCurrentFocus` 查看当前包名。

**Q: 串口找不到设备？**
A: 检查串口号（Windows: `COMx`，Linux: `/dev/ttyUSBx`），用设备管理器确认可用端口。

**Q: 如何切换测试环境？**
A: 在配置文件中设置 `environment.name`，或调用 `ConfigManager.switch_environment("测试环境")`。

---

## 许可证

本项目基于 [MIT License](LICENSE) 开源。

## 联系方式

如有问题或建议，欢迎提交 [GitHub Issues](https://github.com/Li-bot2026/vehicle_test_app/issues)。