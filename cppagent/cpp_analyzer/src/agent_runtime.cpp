#include "projectagentcpp/agent_runtime.hpp"

#include "projectagentcpp/clang_enhancer.hpp"
#include "projectagentcpp/cmake_analyzer.hpp"
#include "projectagentcpp/source_analyzer.hpp"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <iomanip>
#include <sstream>

namespace projectagentcpp {
namespace {

std::string escapeJson(const std::string& value) {
    std::ostringstream out;
    for (const char ch : value) {
        switch (ch) {
            case '\\':
                out << "\\\\";
                break;
            case '"':
                out << "\\\"";
                break;
            case '\n':
                out << "\\n";
                break;
            case '\r':
                out << "\\r";
                break;
            case '\t':
                out << "\\t";
                break;
            default:
                if (static_cast<unsigned char>(ch) < 0x20) {
                    out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                        << static_cast<int>(static_cast<unsigned char>(ch));
                } else {
                    out << ch;
                }
        }
    }
    return out.str();
}

void indent(std::ostringstream& out, int level) {
    out << std::string(level * 2, ' ');
}

void writeString(std::ostringstream& out, const std::string& value) {
    out << '"' << escapeJson(value) << '"';
}

std::string boolWord(bool value) {
    return value ? "yes" : "no";
}

std::string lowerAscii(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return value;
}

bool containsAny(const std::string& value, const std::vector<std::string>& keywords) {
    return std::any_of(keywords.begin(), keywords.end(), [&](const std::string& keyword) {
        return value.find(keyword) != std::string::npos;
    });
}

ToolResult makeResult(const std::string& tool_name, bool success, const std::string& observation) {
    ToolResult result;
    result.tool_name = tool_name;
    result.success = success;
    result.observation = observation;
    return result;
}

class ScanProjectTool final : public Tool {
public:
    std::string name() const override {
        return "scan_project";
    }

    ToolResult run(AgentContext& context) override {
        context.analysis = scanProjectStructure(context.root);
        std::ostringstream observation;
        observation
            << "Scanned project structure. README=" << boolWord(context.analysis.has_readme)
            << ", CMake=" << boolWord(context.analysis.has_cmake)
            << ", sources=" << context.analysis.files.source_count
            << ", headers=" << context.analysis.files.header_count
            << ", tests=" << context.analysis.files.test_count << ".";
        return makeResult(name(), true, observation.str());
    }
};

class AnalyzeCMakeTool final : public Tool {
public:
    std::string name() const override {
        return "analyze_cmake";
    }

    ToolResult run(AgentContext& context) override {
        const auto root = context.analysis.root.empty() ? context.root : std::filesystem::path(context.analysis.root);
        context.analysis.cmake = analyzeCMake(root);
        if (context.analysis.cmake.found && !context.analysis.cmake.project_name.empty()) {
            context.analysis.project_name = context.analysis.cmake.project_name;
        }
        std::ostringstream observation;
        observation
            << "Analyzed CMake. found=" << boolWord(context.analysis.cmake.found)
            << ", cpp_standard=" << (context.analysis.cmake.cpp_standard.empty() ? "unknown" : context.analysis.cmake.cpp_standard)
            << ", executables=" << context.analysis.cmake.executables.size()
            << ", tests=" << context.analysis.cmake.tests.size() << ".";
        return makeResult(name(), true, observation.str());
    }
};

class AnalyzeSourcesTool final : public Tool {
public:
    std::string name() const override {
        return "analyze_sources";
    }

    ToolResult run(AgentContext& context) override {
        const auto root = context.analysis.root.empty() ? context.root : std::filesystem::path(context.analysis.root);
        context.analysis.entry_points = findEntryPoints(root);
        context.analysis.modules = analyzeSources(root);
        std::ostringstream observation;
        observation
            << "Analyzed source keywords. entry_points=" << context.analysis.entry_points.size()
            << ", modules=" << context.analysis.modules.size() << ".";
        if (!context.analysis.modules.empty()) {
            observation << " top_module=" << context.analysis.modules.front().name << ".";
        }
        return makeResult(name(), true, observation.str());
    }
};

class EvaluateProjectTool final : public Tool {
public:
    std::string name() const override {
        return "evaluate_project";
    }

    ToolResult run(AgentContext& context) override {
        evaluateProjectFindings(context.analysis);
        std::ostringstream observation;
        observation
            << "Evaluated project findings. strengths=" << context.analysis.strengths.size()
            << ", risks=" << context.analysis.risks.size() << ".";
        return makeResult(name(), true, observation.str());
    }
};

class AnalyzeSymbolsTool final : public Tool {
public:
    std::string name() const override {
        return "analyze_symbols";
    }

    ToolResult run(AgentContext& context) override {
        const auto root = context.analysis.root.empty() ? context.root : std::filesystem::path(context.analysis.root);
        context.analysis.clang = analyzeWithCompileCommands(root);
        std::ostringstream observation;
        observation
            << "Analyzed compile database and symbols. compile_commands="
            << boolWord(context.analysis.clang.compile_commands_found)
            << ", commands=" << context.analysis.clang.command_count
            << ", classes=" << context.analysis.clang.classes.size()
            << ", functions=" << context.analysis.clang.functions.size()
            << ", calls=" << context.analysis.clang.calls.size()
            << ", deps=" << context.analysis.clang.module_dependencies.size() << ".";
        return makeResult(name(), true, observation.str());
    }
};

class GenerateJsonTool final : public Tool {
public:
    std::string name() const override {
        return "generate_json";
    }

    ToolResult run(AgentContext& context) override {
        context.final_json = toJson(context.analysis);
        std::ostringstream observation;
        observation << "Generated analysis JSON with " << context.final_json.size() << " bytes.";
        return makeResult(name(), true, observation.str());
    }
};

}  // namespace

void ToolRegistry::registerTool(std::unique_ptr<Tool> tool) {
    if (!tool) {
        return;
    }
    const auto tool_name = tool->name();
    tools_[tool_name] = std::move(tool);
}

Tool* ToolRegistry::get(const std::string& name) const {
    const auto found = tools_.find(name);
    if (found == tools_.end()) {
        return nullptr;
    }
    return found->second.get();
}

std::vector<std::string> ToolRegistry::listTools() const {
    std::vector<std::string> names;
    for (const auto& item : tools_) {
        names.push_back(item.first);
    }
    return names;
}

Plan Planner::plan(const std::string& task) const {
    const auto normalized = lowerAscii(task);
    const bool asks_cmake = containsAny(normalized, {"cmake", "build", "target", "architecture", "构建", "依赖", "架构"});
    const bool asks_sources = containsAny(normalized, {"architecture", "module", "source", "架构", "模块", "源码"});
    const bool asks_symbols = containsAny(normalized, {"clang", "symbol", "class", "function", "call", "符号", "类", "函数", "调用"});
    const bool asks_interview = containsAny(normalized, {"interview", "report", "resume", "面试", "报告", "简历"});
    const bool has_specific_intent = asks_cmake || asks_sources || asks_symbols || asks_interview;
    const bool broad_analysis = normalized.empty()
        || containsAny(normalized, {"overall", "complete", "full", "全面", "完整"})
        || (!has_specific_intent && containsAny(normalized, {"analyze", "analysis", "project", "分析", "项目"}));

    Plan plan;
    plan.tool_names.push_back("scan_project");
    if (broad_analysis || asks_cmake || asks_interview) {
        plan.tool_names.push_back("analyze_cmake");
    }
    if (broad_analysis || asks_sources || asks_interview) {
        plan.tool_names.push_back("analyze_sources");
    }
    if (broad_analysis || asks_symbols || asks_interview) {
        plan.tool_names.push_back("analyze_symbols");
    }
    plan.tool_names.push_back("evaluate_project");
    plan.tool_names.push_back("generate_json");
    return plan;
}

Executor::Executor(const ToolRegistry& registry)
    : registry_(registry) {}

std::vector<ToolResult> Executor::execute(const Plan& plan, AgentContext& context) const {
    std::vector<ToolResult> trace;
    for (const auto& tool_name : plan.tool_names) {
        auto* tool = registry_.get(tool_name);
        if (tool == nullptr) {
            trace.push_back(makeResult(tool_name, false, "Tool is not registered."));
            break;
        }

        const auto start = std::chrono::steady_clock::now();
        auto result = tool->run(context);
        const auto end = std::chrono::steady_clock::now();
        result.duration_ms = std::chrono::duration_cast<std::chrono::milliseconds>(end - start).count();
        trace.push_back(result);
        if (!result.success) {
            break;
        }
    }
    return trace;
}

AgentRuntime::AgentRuntime() {
    registry_.registerTool(std::make_unique<ScanProjectTool>());
    registry_.registerTool(std::make_unique<AnalyzeCMakeTool>());
    registry_.registerTool(std::make_unique<AnalyzeSourcesTool>());
    registry_.registerTool(std::make_unique<AnalyzeSymbolsTool>());
    registry_.registerTool(std::make_unique<EvaluateProjectTool>());
    registry_.registerTool(std::make_unique<GenerateJsonTool>());
}

AgentRun AgentRuntime::run(const std::string& task, const std::filesystem::path& root) const {
    AgentContext context;
    context.root = root;

    const auto plan = planner_.plan(task);
    const Executor executor(registry_);
    auto trace = executor.execute(plan, context);

    AgentRun run;
    run.task = task;
    run.root = root;
    run.analysis = context.analysis;
    run.trace = std::move(trace);
    run.final_json = std::move(context.final_json);
    return run;
}

AgentRun runAgentAnalysis(const std::filesystem::path& root, const std::string& task) {
    const AgentRuntime runtime;
    return runtime.run(task, root);
}

std::string traceToJson(const AgentRun& run) {
    std::ostringstream out;
    out << "{\n";
    indent(out, 1); out << "\"task\": "; writeString(out, run.task); out << ",\n";
    indent(out, 1); out << "\"root\": "; writeString(out, run.root.string()); out << ",\n";
    indent(out, 1); out << "\"steps\": [";
    if (!run.trace.empty()) {
        out << "\n";
        for (std::size_t i = 0; i < run.trace.size(); ++i) {
            const auto& step = run.trace[i];
            indent(out, 2); out << "{\n";
            indent(out, 3); out << "\"index\": " << (i + 1) << ",\n";
            indent(out, 3); out << "\"tool\": "; writeString(out, step.tool_name); out << ",\n";
            indent(out, 3); out << "\"success\": " << (step.success ? "true" : "false") << ",\n";
            indent(out, 3); out << "\"duration_ms\": " << step.duration_ms << ",\n";
            indent(out, 3); out << "\"observation\": "; writeString(out, step.observation); out << "\n";
            indent(out, 2); out << "}" << (i + 1 == run.trace.size() ? "\n" : ",\n");
        }
        indent(out, 1);
    }
    out << "]\n";
    out << "}\n";
    return out.str();
}

}  // namespace projectagentcpp
