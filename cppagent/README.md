# ProjectAgentCpp

ProjectAgentCpp 是一个由 C++ 和 Python 共同实现的 Coding Agent，用于分析和修改 C++ 服务端项目。

项目采用清晰的职责划分：

- C++ Analyzer 负责提取稳定、可验证的项目事实。
- Python Agent 负责任务编排、LLM 接入、候选补丁管理和 Markdown 报告生成。

目标分析对象包括 MiniRedis、CorpCron 等 C++ 服务端项目。

## 功能

- 扫描常见 C++ 项目目录：`src`、`include`、`tests`、`docs`、`config`、`scripts`
- 读取并分析根目录的 `CMakeLists.txt`
- 识别 CMake targets、C++ standard、测试入口、依赖包和 linked libraries
- 统计 source、header、CMake、Markdown 和测试相关文件
- 根据规则识别常见服务端模块：
  - network
  - protocol
  - storage
  - commands
  - concurrency
  - persistence
  - cluster
  - metrics
  - config
  - testing
- 生成结构化 JSON 和中文 Markdown 报告
- 分析项目架构、技术亮点、潜在风险和改进方向
- 生成面试讲法、简历表达和面试问答
- 执行构建、测试、服务启动、benchmark 和 stats 诊断
- 读取 `compile_commands.json`，提取类、函数、调用线索和模块依赖
- 根据开发任务检索相关源码并生成候选 unified diff
- 在应用补丁前执行路径、大小、文件类型和 `git apply --check` 校验
- 保存修改前备份，支持受保护的代码回滚
- 提供本地 Web UI，支持完整功能演示

## 目录结构

```text
projectagentcpp/
├── CMakeLists.txt
├── cpp_analyzer/
│   ├── include/projectagentcpp/
│   └── src/
├── agent/
│   ├── project_agent/
│   └── prompts/
├── webui/
│   ├── server.py
│   └── static/
├── scripts/
├── tests/
├── examples/
├── reports/
└── README.md
```

## 构建 C++ Analyzer

```bash
cmake -S . -B build
cmake --build build
```

直接运行 Analyzer：

```bash
./build/cpp_analyzer analyze /home/wcl/project/miniredis
```

将 JSON 写入文件：

```bash
./build/cpp_analyzer analyze /home/wcl/project/miniredis --json reports/miniredis.json
```

## 运行 C++ Agent Runtime

C++ Agent Runtime 提供 `Tool`、`ToolRegistry`、`Planner` 和 `Executor`。Planner 会根据任务内容选择需要执行的 Tool，Executor 负责依次执行并记录 Trace。

```bash
./build/cpp_analyzer agent /home/wcl/project/miniredis \
  --task "分析项目架构、核心模块和代码符号" \
  --json reports/miniredis_agent.json \
  --trace reports/miniredis_trace.json
```

例如，只分析 CMake 时会跳过 source 和 symbol 分析；执行完整项目分析或面试准备任务时，会运行完整 Tool 链路。

完整分析通常包含以下步骤：

```text
scan_project -> analyze_cmake -> analyze_sources -> analyze_symbols -> evaluate_project -> generate_json
```

Trace 会记录：

- Tool 名称
- success 或 failure 状态
- 执行耗时，单位为毫秒
- observation 文本

## 运行 Python Agent

Python 层可以构建 C++ Analyzer，并生成 Markdown 和 JSON：

```bash
python3 -m agent.project_agent.cli analyze /home/wcl/project/miniredis --build
```

默认输出：

```text
reports/<project_name>_analysis.md
reports/<project_name>_analysis.json
```

生成离线面试回答：

```bash
python3 -m agent.project_agent.cli ask /home/wcl/project/miniredis \
  --question "这个项目最能体现你 C++ 能力的地方是什么？"
```

## 本地 Web UI

Web UI 只依赖 Python standard library，不需要额外安装 Web framework。

启动方式：

```bash
python3 webui/server.py --host 127.0.0.1 --port 8765
```

也可以使用启动脚本：

```bash
./scripts/start_webui.sh
```

浏览器访问：

```text
http://127.0.0.1:8765
```

Web UI 支持：

- 项目分析、代码修改、面试准备和项目诊断四个独立工作区
- Coding Agent 任务输入、源码上下文检索和修改计划
- 代码 Diff 审核、人工确认应用、构建测试和回滚
- 构建失败诊断和最多 5 轮候选修复循环
- 离线报告和 LLM 报告
- 报告风格选择
- Task-aware Agent Trace
- 离线或 LLM 面试问答
- dry 或 build-test 诊断模式
- 自定义 CMake、build 和 ctest 参数
- 可选的服务启动命令、benchmark 命令和 stats URL
- Clang AST 分析或已有 AST JSON 解析
- Markdown、Trace 和 JSON 视图
- 后台任务进度、运行日志和取消操作
- 带时间戳的报告历史和结果恢复

Web UI 生成的报告位于：

```text
reports/web/
reports/web/history/
reports/web/coding/proposals/
reports/web/coding/backups/
```

`reports/web/` 保存最新结果，`reports/web/history/` 保存每次成功运行的历史结果。`coding/proposals/` 保存候选补丁及状态，`coding/backups/` 保存应用补丁前的文件备份。

使用 LLM 报告和 LLM 面试问答时，可以在启动前设置环境变量：

```bash
export OPENAI_API_KEY=...
export PROJECTAGENTCPP_MODEL=your-model-name
python3 webui/server.py --host 127.0.0.1 --port 8765
```

Web UI 提供三种模型来源：

- `本地免费`：默认使用 Ollama 和 `http://127.0.0.1:11434/v1`，不需要 API Key。分析与问答使用较快的 `qwen2.5-coder:1.5b`，代码修改使用质量更稳的 `qwen2.5-coder:3b`，两者都可以在设置中调整。
- `免费云端`：使用 OpenRouter 的 `openrouter/free` 路由，需要用户自己的免费 API Key。
- `自定义`：填写任意 OpenAI-compatible model、API Key 和 Base URL。

使用默认本地模型前，需要安装并启动 Ollama，再下载默认模型：

```bash
ollama pull qwen2.5-coder:1.5b
ollama pull qwen2.5-coder:3b
```

模型设置会检测本地 Ollama 服务和已安装模型。API Key 只会发送到本地 Server，用于当前请求，不会写入项目文件或历史记录。

Analyzer 负责提供稳定事实，LLM 只调整报告的表达方式、重点和风格，不负责修改分析事实。

如果缺少 LLM 配置，或者 OpenAI-compatible endpoint 暂时不可用，项目分析和面试问答会自动回退到离线结果，不会丢弃已经完成的 Analyzer 数据。回退原因会显示在任务日志和报告中。

## Coding Agent 工作流

代码修改工作区执行以下闭环：

```text
分析项目 -> 检索源码 -> 生成候选补丁 -> 人工确认 -> 应用修改 -> 构建测试
                                                        |
                                      失败诊断 -> 候选修复 -> 再次审核与测试
```

输入开发任务并选择可用模型来源后，Coding Agent 会生成修改计划、风险、建议测试和标准 unified diff。候选补丁不会自动写入项目，只有点击“应用补丁”后才会修改文件。

应用补丁前会检查：

- 所有路径都位于目标项目中
- 不修改 `.git`、`build`、`reports` 等内部或生成目录
- 文件类型、补丁大小和变更文件数量符合限制
- 补丁可以通过 `git apply --check`

应用时会保存原文件和 SHA-256。点击“回滚”时，只有文件仍与补丁应用后的版本一致才会恢复，避免覆盖用户后续手工修改。第一版支持修改和新增文本源码，暂不支持删除文件、文件重命名和大型跨模块重构。

开启“自动推进构建与修复”后，应用补丁会自动运行 build-test。失败步骤的 command、exit code、stdout 和 stderr 会用于生成下一轮 repair proposal。Agent 不会自动应用修复，每一轮仍需人工审核 Diff；修复链最多执行 5 轮，并支持从后向前逐轮回滚。

自动代码修复只处理 CMake configure、build 和 CTest 失败。服务启动、stats 和 benchmark 失败会保留在诊断报告中，不会触发源码修改。构建成功但 CTest 没有发现测试用例时，验证状态会标记为 `incomplete`，避免把“没有测试”误报为“测试通过”。

## 测试

运行 Python regression tests：

```bash
python3 -m unittest discover -s tests -v
```

Web UI 启动后，可以运行完整 API smoke tests：

```bash
python3 tests/smoke_webui.py --base-url http://127.0.0.1:8765
```

smoke tests 会验证项目分析、LLM 回退、面试问答、Agent Trace、诊断、AST、历史记录和错误处理。

## LLM 接入

Python 层提供 OpenAI-compatible `LLMClient` 和 Prompt templates。Analyzer 会先生成稳定 JSON，LLM 再根据 JSON 生成更完整的项目报告或面试回答。

设置 model 和 API Key：

```bash
export OPENAI_API_KEY=...
export PROJECTAGENTCPP_MODEL=your-model-name
```

生成 LLM 报告：

```bash
python3 -m agent.project_agent.cli analyze /home/wcl/project/miniredis \
  --llm \
  --output reports/miniredis_llm_report.md \
  --save-prompt reports/miniredis_report_prompt.txt
```

使用 LLM 回答面试问题：

```bash
python3 -m agent.project_agent.cli ask /home/wcl/project/miniredis \
  --llm \
  --question "这个项目的架构亮点是什么？" \
  --output reports/miniredis_llm_answer.md
```

使用其他 OpenAI-compatible endpoint：

```bash
export PROJECTAGENTCPP_BASE_URL=https://your-endpoint.example/v1
```

Prompt templates 位于：

```text
agent/prompts/system.md
agent/prompts/report.md
agent/prompts/interview_qa.md
```

## 服务诊断

服务诊断流程可以执行 CMake configure、build 和 ctest，还可以选择启动服务、运行 benchmark、读取 stats endpoint，并生成 Markdown 和 JSON 诊断报告。

执行基础 CMake build 和 test：

```bash
python3 -m agent.project_agent.cli diagnose /home/wcl/project/miniredis \
  --cmake-arg=-DMINIREDIS_ENABLE_INTEGRATION_TESTS=OFF \
  --timeout 180
```

只生成诊断框架，不执行 configure、build 和 test：

```bash
python3 -m agent.project_agent.cli diagnose /home/wcl/project/miniredis \
  --skip-configure \
  --skip-build \
  --skip-test
```

执行服务诊断流程：

```bash
python3 -m agent.project_agent.cli diagnose /home/wcl/project/miniredis \
  --start-command "./build/miniredis --config config/miniredis.conf" \
  --stats-url "http://127.0.0.1:8080/stats" \
  --benchmark-command "redis-cli -p 6379 ping" \
  --skip-configure \
  --skip-build \
  --skip-test
```

默认输出：

```text
reports/diagnostics/<project_name>_diagnostic.md
reports/diagnostics/<project_name>_diagnostic.json
```

如果 configure 失败，后续 build 和 test 会自动跳过，避免产生重复错误。Web UI 中的长任务支持取消。

## Clang Enhanced Analysis

C++ Analyzer 会从常见 CMake build 目录中读取 `compile_commands.json`，并将代码级分析结果写入 JSON。

```bash
./build/cpp_analyzer analyze /home/wcl/project/miniredis \
  --json reports/miniredis_phase5.json
```

JSON 中的 `clang` 字段包含：

```text
clang.compile_commands_found
clang.command_count
clang.classes
clang.functions
clang.calls
clang.module_dependencies
```

C++ Agent Runtime 中对应的完整步骤为：

```text
scan_project -> analyze_cmake -> analyze_sources -> analyze_symbols -> evaluate_project -> generate_json
```

当前实现采用轻量方案，通过 `compile_commands.json` 和 source rules 提取 class、function、call 线索以及基于 include 的 module dependencies。后续可以替换为 libclang 或 Clang tooling，同时保持现有 JSON contract 不变。

## 可选 Clang AST Backend

在安装了 `clang++` 的环境中，可以使用 Python AST command：

```bash
python3 -m agent.project_agent.cli ast /home/wcl/project/miniredis \
  --compile-db /home/wcl/project/miniredis/build/compile_commands.json \
  --max-files 3
```

默认输出：

```text
reports/ast/<project_name>_ast.md
reports/ast/<project_name>_ast.json
reports/ast/dumps/<project_name>/*.ast.json
```

如果没有安装 `clang++`，command 仍会生成诊断报告并说明 AST Backend 不可用。也可以直接解析已有 AST JSON：

```bash
python3 -m agent.project_agent.cli ast /home/wcl/project/miniredis \
  --ast-json /path/to/file.ast.json
```

项目包含一个离线示例：

```bash
python3 -m agent.project_agent.cli ast /home/wcl/project/miniredis \
  --ast-json examples/sample_ast.json
```

这样可以保证没有安装 Clang 时项目仍然可用，同时为后续接入真实 AST 分析保留扩展路径。

## 设计思路

项目分为两个主要部分：

- C++ 负责快速、稳定、可离线运行的本地分析。
- Python 负责 Agent 编排、报告生成、服务诊断和 LLM 接入。

这种设计将事实提取和语言生成分开。Analyzer 可以完全离线运行，Python Agent 则可以将 JSON 作为结构化上下文交给 LLM。

## 开发路线

### 第一阶段：纯 C++ 离线分析

- CLI
- 项目扫描
- CMake 解析
- source keyword 分析
- JSON 输出
- 通过 Python 生成 Markdown 报告

当前状态：已完成。

### 第二阶段：Agent Runtime

- Tool abstraction
- ToolRegistry
- Planner 和 Executor
- 多步骤 Trace
- Tool observation

当前状态：已完成。C++ Runtime 已包含 `Tool`、`ToolRegistry`、`Planner`、`Executor` 和 `AgentRun`，Planner 会根据任务内容选择 Tool。

### 第三阶段：LLM 接入

- LLMClient
- Prompt templates
- 根据 JSON 生成报告
- 面试问答

当前状态：已完成。Python 提供 `LLMClient`、Markdown Prompt templates、`analyze --llm` 和 `ask --llm`，没有 API Key 时仍可使用离线报告和问答。

### 第四阶段：C++ 服务端项目诊断

- build project
- run tests
- start service
- benchmark
- 分析 logs 和 stats

当前状态：已完成。`diagnose` 支持 CMake configure、build、ctest、服务启动、benchmark、stats 获取、Markdown/JSON 报告和诊断建议。

### 第五阶段：Clang 增强

- 读取 `compile_commands.json`
- 提取 class 和 function
- 构建 module dependency graph
- 提高 module confidence

当前状态：已完成轻量版本。C++ Analyzer 会读取常见位置的 `compile_commands.json`，通过 source rules 提取 class、function、call 线索，并根据 include 关系生成 module dependency edges。Python 层另外提供可选 Clang AST Backend。

### 第六阶段：Coding Agent

- 源码上下文检索
- LLM 修改计划和 unified diff
- 补丁安全校验与人工审核
- 文件备份、应用和回滚
- 构建测试闭环

当前状态：已完成受控修改 MVP 和基于编译错误的多轮候选修复循环。下一步将加入按文件接受变更和 Git branch/commit 工作流。

## 项目介绍参考

> ProjectAgentCpp 是一个由 C++ static analysis engine 和 Python Agent orchestrator 组成的 C++ 服务端 Coding Agent。它可以从 CMake 和 source files 中提取结构化事实，生成架构报告和面试讲法，也可以根据开发任务检索源码、生成候选补丁，并在人工确认后修改、构建、测试和回滚代码。
