#pragma once

#include <filesystem>
#include <string>
#include <vector>

namespace projectagentcpp {

struct DirectoryInfo {
    bool has_src = false;
    bool has_include = false;
    bool has_tests = false;
    bool has_docs = false;
    bool has_config = false;
    bool has_scripts = false;
};

struct FileStats {
    int source_count = 0;
    int header_count = 0;
    int test_count = 0;
    int cmake_count = 0;
    int markdown_count = 0;
};

struct CMakeInfo {
    bool found = false;
    std::string project_name;
    std::string cpp_standard;
    bool enable_testing = false;
    std::vector<std::string> executables;
    std::vector<std::string> libraries;
    std::vector<std::string> tests;
    std::vector<std::string> packages;
    std::vector<std::string> linked_libraries;
};

struct ModuleFinding {
    std::string name;
    double confidence = 0.0;
    std::vector<std::string> files;
    std::vector<std::string> evidence;
};

struct RiskFinding {
    std::string type;
    std::string message;
};

struct CompileCommandEntry {
    std::string file;
    std::string directory;
    std::string command;
};

struct SymbolInfo {
    std::string name;
    std::string kind;
    std::string file;
    int line = 0;
};

struct ModuleDependency {
    std::string from;
    std::string to;
    std::string type;
    std::string evidence;
    int weight = 0;
};

struct CallInfo {
    std::string caller_file;
    std::string callee;
    int line = 0;
};

struct ClangAnalysis {
    bool compile_commands_found = false;
    std::string compile_commands_path;
    int command_count = 0;
    std::vector<CompileCommandEntry> sample_commands;
    std::vector<SymbolInfo> classes;
    std::vector<SymbolInfo> functions;
    std::vector<CallInfo> calls;
    std::vector<ModuleDependency> module_dependencies;
};

struct ProjectAnalysis {
    std::string project_name;
    std::string root;
    bool has_readme = false;
    bool has_cmake = false;
    DirectoryInfo directories;
    FileStats files;
    CMakeInfo cmake;
    std::vector<std::string> entry_points;
    std::vector<ModuleFinding> modules;
    ClangAnalysis clang;
    std::vector<std::string> strengths;
    std::vector<RiskFinding> risks;
};

ProjectAnalysis analyzeProject(const std::filesystem::path& root);
ProjectAnalysis scanProjectStructure(const std::filesystem::path& root);
void evaluateProjectFindings(ProjectAnalysis& analysis);
std::string toJson(const ProjectAnalysis& analysis);

}  // namespace projectagentcpp
