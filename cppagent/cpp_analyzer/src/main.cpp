#include "projectagentcpp/analysis.hpp"
#include "projectagentcpp/agent_runtime.hpp"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>

namespace {

void printUsage() {
    std::cout
        << "Usage:\n"
        << "  cpp_analyzer analyze <project_path> [--json <output_file>]\n"
        << "  cpp_analyzer scan <project_path> [--json <output_file>]\n"
        << "  cpp_analyzer agent <project_path> [--task <task>] [--json <output_file>] [--trace <trace_file>]\n";
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 3) {
        printUsage();
        return 1;
    }

    const std::string command = argv[1];
    if (command != "analyze" && command != "scan" && command != "agent") {
        printUsage();
        return 1;
    }

    const std::filesystem::path project_path = argv[2];
    std::string output_path;
    std::string trace_path;
    std::string task = "Analyze C++ project";
    for (int i = 3; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--json" && i + 1 < argc) {
            output_path = argv[++i];
        } else if (arg == "--trace" && i + 1 < argc) {
            trace_path = argv[++i];
        } else if (arg == "--task" && i + 1 < argc) {
            task = argv[++i];
        } else {
            std::cerr << "Unknown argument: " << arg << "\n";
            printUsage();
            return 1;
        }
    }

    std::error_code ec;
    if (!std::filesystem::exists(project_path, ec) || !std::filesystem::is_directory(project_path, ec)) {
        std::cerr << "Project path is not a directory: " << project_path << "\n";
        return 2;
    }

    std::string json;
    if (command == "agent") {
        const auto run = projectagentcpp::runAgentAnalysis(project_path, task);
        json = run.final_json.empty() ? projectagentcpp::toJson(run.analysis) : run.final_json;
        if (!trace_path.empty()) {
            std::ofstream trace_output(trace_path);
            if (!trace_output) {
                std::cerr << "Failed to open trace file: " << trace_path << "\n";
                return 4;
            }
            trace_output << projectagentcpp::traceToJson(run);
        }
    } else {
        const auto analysis = projectagentcpp::analyzeProject(project_path);
        json = projectagentcpp::toJson(analysis);
    }

    if (output_path.empty()) {
        std::cout << json;
        return 0;
    }

    std::ofstream output(output_path);
    if (!output) {
        std::cerr << "Failed to open output file: " << output_path << "\n";
        return 3;
    }
    output << json;
    return 0;
}
