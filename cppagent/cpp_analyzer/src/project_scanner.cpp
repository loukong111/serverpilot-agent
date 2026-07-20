#include "projectagentcpp/analysis.hpp"

#include "projectagentcpp/clang_enhancer.hpp"
#include "projectagentcpp/cmake_analyzer.hpp"
#include "projectagentcpp/source_analyzer.hpp"
#include "projectagentcpp/text_utils.hpp"

#include <algorithm>

namespace projectagentcpp {
namespace {

bool pathContainsSegment(const std::filesystem::path& path, const std::string& segment) {
    for (const auto& part : path) {
        if (toLower(part.string()) == segment) {
            return true;
        }
    }
    return false;
}

bool hasModule(const std::vector<ModuleFinding>& modules, const std::string& name) {
    return std::any_of(modules.begin(), modules.end(), [&](const ModuleFinding& module) {
        return module.name == name;
    });
}

void addStrength(std::vector<std::string>& strengths, const std::string& text) {
    strengths.push_back(text);
}

void addRisk(std::vector<RiskFinding>& risks, const std::string& type, const std::string& message) {
    risks.push_back(RiskFinding{type, message});
}

}  // namespace

ProjectAnalysis scanProjectStructure(const std::filesystem::path& root_input) {
    std::error_code ec;
    const auto root = std::filesystem::absolute(root_input, ec);

    ProjectAnalysis analysis;
    analysis.root = ec ? root_input.string() : root.string();
    analysis.project_name = root.filename().string();
    analysis.has_readme = std::filesystem::exists(root / "README.md") || std::filesystem::exists(root / "readme.md");
    analysis.has_cmake = std::filesystem::exists(root / "CMakeLists.txt");

    std::filesystem::recursive_directory_iterator it(root, std::filesystem::directory_options::skip_permission_denied, ec);
    const std::filesystem::recursive_directory_iterator end;
    for (; it != end; it.increment(ec)) {
        if (ec) {
            ec.clear();
            continue;
        }
        const auto& entry = *it;
        const auto path = entry.path();
        const auto name = toLower(path.filename().string());

        if (entry.is_directory(ec)) {
            if (isIgnoredDirectoryName(name)) {
                it.disable_recursion_pending();
                continue;
            }
            analysis.directories.has_src = analysis.directories.has_src || name == "src";
            analysis.directories.has_include = analysis.directories.has_include || name == "include";
            analysis.directories.has_tests = analysis.directories.has_tests || name == "tests" || name == "test";
            analysis.directories.has_docs = analysis.directories.has_docs || name == "docs" || name == "doc";
            analysis.directories.has_config = analysis.directories.has_config || name == "config" || name == "conf";
            analysis.directories.has_scripts = analysis.directories.has_scripts || name == "scripts" || name == "script";
            continue;
        }

        if (!entry.is_regular_file(ec)) {
            continue;
        }

        if (name == "cmakelists.txt" || path.extension() == ".cmake") {
            ++analysis.files.cmake_count;
        }
        if (hasExtension(path, {".md"})) {
            ++analysis.files.markdown_count;
        }
        if (hasExtension(path, {".cpp", ".cc", ".cxx", ".c"})) {
            ++analysis.files.source_count;
        }
        if (hasExtension(path, {".h", ".hpp", ".hh"})) {
            ++analysis.files.header_count;
        }
        if (pathContainsSegment(path, "tests") || pathContainsSegment(path, "test") || contains(name, "test")) {
            ++analysis.files.test_count;
        }
    }

    return analysis;
}

void evaluateProjectFindings(ProjectAnalysis& analysis) {
    analysis.strengths.clear();
    analysis.risks.clear();

    if (analysis.has_cmake) {
        addStrength(analysis.strengths, "使用 CMake 管理构建，适合跨平台构建、测试和后续 CI 集成。");
    }
    if (analysis.cmake.enable_testing || !analysis.cmake.tests.empty() || analysis.directories.has_tests) {
        addStrength(analysis.strengths, "项目包含测试入口，具备自动化验证和回归测试的基础。");
    }
    if (analysis.directories.has_src && analysis.directories.has_include) {
        addStrength(analysis.strengths, "源码与头文件目录分离，整体结构利于模块化维护。");
    }
    if (hasModule(analysis.modules, "network") && hasModule(analysis.modules, "protocol")) {
        addStrength(analysis.strengths, "识别到网络层和协议层，说明项目具备服务端请求处理链路。");
    }
    if (hasModule(analysis.modules, "concurrency")) {
        addStrength(analysis.strengths, "代码中存在并发相关实现，可作为 C++ 服务端能力的展示点。");
    }
    if (hasModule(analysis.modules, "persistence")) {
        addStrength(analysis.strengths, "包含持久化相关模块，项目不止停留在内存数据结构层面。");
    }
    if (analysis.clang.compile_commands_found) {
        addStrength(analysis.strengths, "识别到 compile_commands.json，可继续接入 Clang AST 做更精确的符号和依赖分析。");
    }
    if (!analysis.clang.classes.empty() || !analysis.clang.functions.empty()) {
        addStrength(analysis.strengths, "已提取类、函数和调用线索，项目分析开始具备代码级结构视角。");
    }

    if (!analysis.has_readme) {
        addRisk(analysis.risks, "documentation", "未发现 README.md，项目背景、构建方式和使用方法需要补充。");
    }
    if (!analysis.has_cmake) {
        addRisk(analysis.risks, "build", "未发现根目录 CMakeLists.txt，C++ 项目的构建入口不够明确。");
    }
    if (!analysis.directories.has_tests && analysis.files.test_count == 0) {
        addRisk(analysis.risks, "testing", "未识别到 tests/test 目录或测试文件，面试时容易被追问质量保障。");
    }
    if (analysis.files.source_count + analysis.files.header_count > 20 && !analysis.directories.has_docs) {
        addRisk(analysis.risks, "documentation", "源码规模已经不小，但未识别到 docs 目录，架构说明可能不足。");
    }
    if (hasModule(analysis.modules, "network") && !hasModule(analysis.modules, "metrics")) {
        addRisk(analysis.risks, "observability", "服务端项目未识别到 metrics/stats 相关模块，运行时可观测性可能不足。");
    }
    if (hasModule(analysis.modules, "concurrency") && analysis.files.test_count < 2) {
        addRisk(analysis.risks, "concurrency", "代码包含并发特征，但测试线索较少，需要重点补充并发边界和压力测试。");
    }
    if (!analysis.clang.compile_commands_found && analysis.files.source_count > 0) {
        addRisk(analysis.risks, "clang", "未发现 compile_commands.json，后续 Clang AST 分析需要先生成 compilation database。");
    }
    if (analysis.strengths.empty()) {
        addStrength(analysis.strengths, "项目具备可分析的 C++ 源码结构，可继续通过模块命名和构建脚本完善工程表达。");
    }

}

ProjectAnalysis analyzeProject(const std::filesystem::path& root_input) {
    auto analysis = scanProjectStructure(root_input);
    analysis.cmake = analyzeCMake(analysis.root);
    if (analysis.cmake.found && !analysis.cmake.project_name.empty()) {
        analysis.project_name = analysis.cmake.project_name;
    }
    analysis.entry_points = findEntryPoints(analysis.root);
    analysis.modules = analyzeSources(analysis.root);
    analysis.clang = analyzeWithCompileCommands(analysis.root);
    evaluateProjectFindings(analysis);
    return analysis;
}

}  // namespace projectagentcpp
