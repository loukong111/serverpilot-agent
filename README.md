# ServerPilot Agent

ServerPilot Agent 是一个面向服务端项目分析与诊断的 Agent 项目集合。

## C++ 项目分析 Agent

当前实现位于 [`cppagent/`](cppagent/)，由 C++ Analyzer、Python Agent 和本地 Web UI 组成，主要用于分析 MiniRedis、CorpCron 等 C++ 服务端项目。

主要功能包括：

- 扫描 C++ 项目目录和源文件
- 分析 CMake、核心模块和代码符号
- 生成架构、亮点、风险和改进建议
- 生成面试讲法和面试问答
- 执行 build、test、benchmark 和 stats 诊断
- 提供 Agent Runtime、ToolRegistry、Planner、Executor 和 Trace
- 支持 LLM 报告及无配置时的离线回退
- 提供本地 Web UI、任务进度、运行日志和报告历史

## 快速开始

```bash
cd cppagent
cmake -S . -B build
cmake --build build
./scripts/start_webui.sh
```

浏览器访问：

```text
http://127.0.0.1:8765
```

完整功能、CLI 命令和设计说明请查看 [`cppagent/README.md`](cppagent/README.md)。
