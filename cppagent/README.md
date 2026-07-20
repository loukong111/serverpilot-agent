# ProjectAgentCpp

ProjectAgentCpp is a hybrid C++ and Python coding agent for analyzing C++ server-side projects.

The first version follows a simple boundary:

- C++ Analyzer extracts deterministic project facts.
- Python Agent orchestrates the analyzer and generates a Markdown report.

The target projects are C++ service projects such as MiniRedis and CorpCron.

## Features

- Scan common C++ project directories: `src`, `include`, `tests`, `docs`, `config`, `scripts`
- Read and summarize root `CMakeLists.txt`
- Detect CMake targets, C++ standard, test entries, packages, and linked libraries
- Count source, header, CMake, Markdown, and test-related files
- Detect common service-side modules by rules:
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
- Generate JSON facts and a Chinese Markdown report
- Produce architecture, strengths, risks, and interview talking points

## Layout

```text
projectagentcpp/
├── CMakeLists.txt
├── cpp_analyzer/
│   ├── include/projectagentcpp/
│   └── src/
├── agent/
│   └── project_agent/
├── reports/
└── README.md
```

## Build The C++ Analyzer

```bash
cmake -S . -B build
cmake --build build
```

Run the analyzer directly:

```bash
./build/cpp_analyzer analyze /home/wcl/project/miniredis
```

Write JSON to a file:

```bash
./build/cpp_analyzer analyze /home/wcl/project/miniredis --json reports/miniredis.json
```

## Run The C++ Agent Runtime

Phase 2 adds a lightweight C++ Agent Runtime. It plans a fixed project-analysis workflow, executes registered tools, and writes a step trace.

```bash
./build/cpp_analyzer agent /home/wcl/project/miniredis \
  --json reports/miniredis_agent.json \
  --trace reports/miniredis_trace.json
```

The runtime currently executes these tools:

```text
scan_project -> analyze_cmake -> analyze_sources -> evaluate_project -> generate_json
```

The trace records:

- tool name
- success/failure
- duration in milliseconds
- observation text

## Run The Python Agent

The Python layer can build the C++ analyzer and generate both Markdown and JSON:

```bash
python3 -m agent.project_agent.cli analyze /home/wcl/project/miniredis --build
```

Default outputs:

```text
reports/<project_name>_analysis.md
reports/<project_name>_analysis.json
```

Generate an offline interview answer:

```bash
python3 -m agent.project_agent.cli ask /home/wcl/project/miniredis \
  --question "这个项目最能体现你 C++ 能力的地方是什么？"
```

## Local Web UI

The Web UI runs locally with Python standard library only:

```bash
python3 webui/server.py --host 127.0.0.1 --port 8765
```

Or use the helper script:

```bash
./scripts/start_webui.sh
```

Open:

```text
http://127.0.0.1:8765
```

The UI supports:

- project analysis
- LLM-generated reports with style selection
- Agent trace
- offline or LLM interview Q&A
- dry or build-test diagnosis with custom CMake/build/ctest args
- optional service start command, benchmark command, and stats URL
- optional Clang AST analysis or existing AST JSON parsing
- Markdown, JSON, and trace views

Generated Web UI reports are written under:

```text
reports/web/
```

For LLM reports and LLM interview Q&A, either set environment variables before starting:

```bash
export OPENAI_API_KEY=...
export PROJECTAGENTCPP_MODEL=your-model-name
python3 webui/server.py --host 127.0.0.1 --port 8765
```

Or enter the API key, model, and base URL directly in the Web UI. The API key is sent only to the local server for the current request and is not written to project files.

The Web UI keeps analyzer facts stable and asks the LLM to vary only the wording, emphasis, and report style.

## LLM Integration

Phase 3 adds an OpenAI-compatible LLM client and prompt templates. The analyzer still produces deterministic JSON first; the LLM only rewrites that JSON into a richer report or interview answer.

Set a model and API key:

```bash
export OPENAI_API_KEY=...
export PROJECTAGENTCPP_MODEL=your-model-name
```

Generate an LLM report:

```bash
python3 -m agent.project_agent.cli analyze /home/wcl/project/miniredis \
  --llm \
  --output reports/miniredis_llm_report.md \
  --save-prompt reports/miniredis_report_prompt.txt
```

Ask an LLM-powered interview question:

```bash
python3 -m agent.project_agent.cli ask /home/wcl/project/miniredis \
  --llm \
  --question "这个项目的架构亮点是什么？" \
  --output reports/miniredis_llm_answer.md
```

If you use another OpenAI-compatible endpoint:

```bash
export PROJECTAGENTCPP_BASE_URL=https://your-endpoint.example/v1
```

Prompt templates live in:

```text
agent/prompts/system.md
agent/prompts/report.md
agent/prompts/interview_qa.md
```

## Service Diagnosis

Phase 4 adds a service diagnosis workflow. It can configure, build, test, optionally start a service, run a benchmark command, fetch a stats endpoint, and write a Markdown/JSON diagnostic report.

Basic CMake build and test:

```bash
python3 -m agent.project_agent.cli diagnose /home/wcl/project/miniredis \
  --cmake-arg=-DMINIREDIS_ENABLE_INTEGRATION_TESTS=OFF \
  --timeout 180
```

Lightweight dry run that only creates a report skeleton:

```bash
python3 -m agent.project_agent.cli diagnose /home/wcl/project/miniredis \
  --skip-configure \
  --skip-build \
  --skip-test
```

Run a service workflow:

```bash
python3 -m agent.project_agent.cli diagnose /home/wcl/project/miniredis \
  --start-command "./build/miniredis --config config/miniredis.conf" \
  --stats-url "http://127.0.0.1:8080/stats" \
  --benchmark-command "redis-cli -p 6379 ping" \
  --skip-configure \
  --skip-build \
  --skip-test
```

Default outputs:

```text
reports/diagnostics/<project_name>_diagnostic.md
reports/diagnostics/<project_name>_diagnostic.json
```

## Clang Enhanced Analysis

Phase 5 reads `compile_commands.json` from common CMake build directories and adds code-level analysis data to the analyzer JSON.

```bash
./build/cpp_analyzer analyze /home/wcl/project/miniredis \
  --json reports/miniredis_phase5.json
```

The JSON contains a `clang` section:

```text
clang.compile_commands_found
clang.command_count
clang.classes
clang.functions
clang.calls
clang.module_dependencies
```

The C++ Agent Runtime also includes this step:

```text
scan_project -> analyze_cmake -> analyze_sources -> analyze_symbols -> evaluate_project -> generate_json
```

The current implementation is intentionally lightweight: it uses `compile_commands.json` plus source rules to extract class/function/call hints and include-based module dependencies. A future version can replace the rule extractor with libclang or Clang tooling while keeping the same JSON contract.

## Optional Clang AST Backend

An optional Python AST command is available for environments with `clang++`.

```bash
python3 -m agent.project_agent.cli ast /home/wcl/project/miniredis \
  --compile-db /home/wcl/project/miniredis/build/compile_commands.json \
  --max-files 3
```

Default outputs:

```text
reports/ast/<project_name>_ast.md
reports/ast/<project_name>_ast.json
reports/ast/dumps/<project_name>/*.ast.json
```

If `clang++` is not installed, the command still writes a diagnostic report explaining that the AST backend is unavailable. You can also parse an existing AST dump:

```bash
python3 -m agent.project_agent.cli ast /home/wcl/project/miniredis \
  --ast-json /path/to/file.ast.json
```

A tiny offline sample is included:

```bash
python3 -m agent.project_agent.cli ast /home/wcl/project/miniredis \
  --ast-json examples/sample_ast.json
```

This keeps the project usable on machines without Clang while making the true AST path ready for later.

## Design

The project is intentionally split into two parts:

- C++ is responsible for fast, deterministic, local analysis.
- Python is responsible for agent orchestration, report generation, and future LLM integration.

This keeps facts and language generation separate. The analyzer can work offline, while the Python layer can later call an LLM using the JSON result as structured context.

## Roadmap

1. Pure C++ offline analysis
   - CLI
   - project scanning
   - CMake parsing
   - source keyword analysis
   - JSON output
   - Markdown report through Python

2. Agent runtime
   - Tool abstraction
   - Tool registry
   - planner/executor loop
   - tool observations

Current status: implemented as a C++ runtime inside `cpp_analyzer`, with `Tool`, `ToolRegistry`, `Planner`, `Executor`, `AgentRun`, and JSON trace output.

3. LLM integration
   - LLM client
   - prompt templates
   - report generation from JSON facts
   - interview Q&A

Current status: implemented in Python with `LLMClient`, Markdown prompt templates, `analyze --llm`, and `ask --llm`. Offline report and Q&A remain available without an API key.

4. C++ service diagnosis
   - build project
   - run tests
   - start service
   - benchmark
   - analyze logs and stats

Current status: implemented in Python as `diagnose`, with CMake configure/build, ctest, optional service startup, optional benchmark command, optional stats fetch, Markdown report, JSON result, and heuristic suggestions.

5. Clang enhancement
   - read `compile_commands.json`
   - extract classes and functions
   - build module dependency graph
   - improve module confidence

Current status: implemented as a lightweight C++ enhancement that reads common `compile_commands.json` locations, extracts class/function/call hints by source rules, and emits module dependency edges from include relationships. This is the bridge toward a future libclang/Clang AST implementation.

## Example Positioning

> ProjectAgentCpp is a C++ static analysis engine plus Python agent orchestrator. It analyzes C++ server projects, extracts structured facts from CMake and source files, then generates architecture summaries, highlights, risks, and interview talking points.
