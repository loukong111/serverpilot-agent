#pragma once

#include "projectagentcpp/analysis.hpp"

#include <filesystem>
#include <map>
#include <memory>
#include <string>
#include <vector>

namespace projectagentcpp {

struct AgentContext {
    std::filesystem::path root;
    ProjectAnalysis analysis;
    std::string final_json;
};

struct ToolResult {
    std::string tool_name;
    bool success = false;
    std::string observation;
    long long duration_ms = 0;
};

class Tool {
public:
    virtual ~Tool() = default;
    virtual std::string name() const = 0;
    virtual ToolResult run(AgentContext& context) = 0;
};

class ToolRegistry {
public:
    void registerTool(std::unique_ptr<Tool> tool);
    Tool* get(const std::string& name) const;
    std::vector<std::string> listTools() const;

private:
    std::map<std::string, std::unique_ptr<Tool>> tools_;
};

struct Plan {
    std::vector<std::string> tool_names;
};

class Planner {
public:
    Plan plan(const std::string& task) const;
};

class Executor {
public:
    explicit Executor(const ToolRegistry& registry);
    std::vector<ToolResult> execute(const Plan& plan, AgentContext& context) const;

private:
    const ToolRegistry& registry_;
};

struct AgentRun {
    std::string task;
    std::filesystem::path root;
    ProjectAnalysis analysis;
    std::vector<ToolResult> trace;
    std::string final_json;
};

class AgentRuntime {
public:
    AgentRuntime();
    AgentRun run(const std::string& task, const std::filesystem::path& root) const;

private:
    ToolRegistry registry_;
    Planner planner_;
};

AgentRun runAgentAnalysis(const std::filesystem::path& root, const std::string& task = "Analyze C++ project");
std::string traceToJson(const AgentRun& run);

}  // namespace projectagentcpp
