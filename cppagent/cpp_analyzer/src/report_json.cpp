#include "projectagentcpp/analysis.hpp"

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

void writeStringArray(std::ostringstream& out, const std::vector<std::string>& values, int level) {
    out << "[";
    if (!values.empty()) {
        out << "\n";
        for (std::size_t i = 0; i < values.size(); ++i) {
            indent(out, level + 1);
            writeString(out, values[i]);
            out << (i + 1 == values.size() ? "\n" : ",\n");
        }
        indent(out, level);
    }
    out << "]";
}

}  // namespace

std::string toJson(const ProjectAnalysis& analysis) {
    std::ostringstream out;
    out << "{\n";
    indent(out, 1); out << "\"project_name\": "; writeString(out, analysis.project_name); out << ",\n";
    indent(out, 1); out << "\"root\": "; writeString(out, analysis.root); out << ",\n";
    indent(out, 1); out << "\"has_readme\": " << (analysis.has_readme ? "true" : "false") << ",\n";
    indent(out, 1); out << "\"has_cmake\": " << (analysis.has_cmake ? "true" : "false") << ",\n";

    indent(out, 1); out << "\"directories\": {\n";
    indent(out, 2); out << "\"src\": " << (analysis.directories.has_src ? "true" : "false") << ",\n";
    indent(out, 2); out << "\"include\": " << (analysis.directories.has_include ? "true" : "false") << ",\n";
    indent(out, 2); out << "\"tests\": " << (analysis.directories.has_tests ? "true" : "false") << ",\n";
    indent(out, 2); out << "\"docs\": " << (analysis.directories.has_docs ? "true" : "false") << ",\n";
    indent(out, 2); out << "\"config\": " << (analysis.directories.has_config ? "true" : "false") << ",\n";
    indent(out, 2); out << "\"scripts\": " << (analysis.directories.has_scripts ? "true" : "false") << "\n";
    indent(out, 1); out << "},\n";

    indent(out, 1); out << "\"files\": {\n";
    indent(out, 2); out << "\"source_count\": " << analysis.files.source_count << ",\n";
    indent(out, 2); out << "\"header_count\": " << analysis.files.header_count << ",\n";
    indent(out, 2); out << "\"test_count\": " << analysis.files.test_count << ",\n";
    indent(out, 2); out << "\"cmake_count\": " << analysis.files.cmake_count << ",\n";
    indent(out, 2); out << "\"markdown_count\": " << analysis.files.markdown_count << "\n";
    indent(out, 1); out << "},\n";

    indent(out, 1); out << "\"cmake\": {\n";
    indent(out, 2); out << "\"found\": " << (analysis.cmake.found ? "true" : "false") << ",\n";
    indent(out, 2); out << "\"project_name\": "; writeString(out, analysis.cmake.project_name); out << ",\n";
    indent(out, 2); out << "\"cpp_standard\": "; writeString(out, analysis.cmake.cpp_standard); out << ",\n";
    indent(out, 2); out << "\"enable_testing\": " << (analysis.cmake.enable_testing ? "true" : "false") << ",\n";
    indent(out, 2); out << "\"executables\": "; writeStringArray(out, analysis.cmake.executables, 2); out << ",\n";
    indent(out, 2); out << "\"libraries\": "; writeStringArray(out, analysis.cmake.libraries, 2); out << ",\n";
    indent(out, 2); out << "\"tests\": "; writeStringArray(out, analysis.cmake.tests, 2); out << ",\n";
    indent(out, 2); out << "\"packages\": "; writeStringArray(out, analysis.cmake.packages, 2); out << ",\n";
    indent(out, 2); out << "\"linked_libraries\": "; writeStringArray(out, analysis.cmake.linked_libraries, 2); out << "\n";
    indent(out, 1); out << "},\n";

    indent(out, 1); out << "\"entry_points\": "; writeStringArray(out, analysis.entry_points, 1); out << ",\n";

    indent(out, 1); out << "\"modules\": [";
    if (!analysis.modules.empty()) {
        out << "\n";
        for (std::size_t i = 0; i < analysis.modules.size(); ++i) {
            const auto& module = analysis.modules[i];
            indent(out, 2); out << "{\n";
            indent(out, 3); out << "\"name\": "; writeString(out, module.name); out << ",\n";
            indent(out, 3); out << "\"confidence\": " << std::fixed << std::setprecision(2) << module.confidence << ",\n";
            indent(out, 3); out << "\"files\": "; writeStringArray(out, module.files, 3); out << ",\n";
            indent(out, 3); out << "\"evidence\": "; writeStringArray(out, module.evidence, 3); out << "\n";
            indent(out, 2); out << "}" << (i + 1 == analysis.modules.size() ? "\n" : ",\n");
        }
        indent(out, 1);
    }
    out << "],\n";

    indent(out, 1); out << "\"clang\": {\n";
    indent(out, 2); out << "\"compile_commands_found\": "
                         << (analysis.clang.compile_commands_found ? "true" : "false") << ",\n";
    indent(out, 2); out << "\"compile_commands_path\": "; writeString(out, analysis.clang.compile_commands_path); out << ",\n";
    indent(out, 2); out << "\"command_count\": " << analysis.clang.command_count << ",\n";
    indent(out, 2); out << "\"sample_commands\": [";
    if (!analysis.clang.sample_commands.empty()) {
        out << "\n";
        for (std::size_t i = 0; i < analysis.clang.sample_commands.size(); ++i) {
            const auto& command = analysis.clang.sample_commands[i];
            indent(out, 3); out << "{\n";
            indent(out, 4); out << "\"file\": "; writeString(out, command.file); out << ",\n";
            indent(out, 4); out << "\"directory\": "; writeString(out, command.directory); out << ",\n";
            indent(out, 4); out << "\"command\": "; writeString(out, command.command); out << "\n";
            indent(out, 3); out << "}" << (i + 1 == analysis.clang.sample_commands.size() ? "\n" : ",\n");
        }
        indent(out, 2);
    }
    out << "],\n";

    indent(out, 2); out << "\"classes\": [";
    if (!analysis.clang.classes.empty()) {
        out << "\n";
        for (std::size_t i = 0; i < analysis.clang.classes.size(); ++i) {
            const auto& symbol = analysis.clang.classes[i];
            indent(out, 3); out << "{\n";
            indent(out, 4); out << "\"name\": "; writeString(out, symbol.name); out << ",\n";
            indent(out, 4); out << "\"kind\": "; writeString(out, symbol.kind); out << ",\n";
            indent(out, 4); out << "\"file\": "; writeString(out, symbol.file); out << ",\n";
            indent(out, 4); out << "\"line\": " << symbol.line << "\n";
            indent(out, 3); out << "}" << (i + 1 == analysis.clang.classes.size() ? "\n" : ",\n");
        }
        indent(out, 2);
    }
    out << "],\n";

    indent(out, 2); out << "\"functions\": [";
    if (!analysis.clang.functions.empty()) {
        out << "\n";
        for (std::size_t i = 0; i < analysis.clang.functions.size(); ++i) {
            const auto& symbol = analysis.clang.functions[i];
            indent(out, 3); out << "{\n";
            indent(out, 4); out << "\"name\": "; writeString(out, symbol.name); out << ",\n";
            indent(out, 4); out << "\"kind\": "; writeString(out, symbol.kind); out << ",\n";
            indent(out, 4); out << "\"file\": "; writeString(out, symbol.file); out << ",\n";
            indent(out, 4); out << "\"line\": " << symbol.line << "\n";
            indent(out, 3); out << "}" << (i + 1 == analysis.clang.functions.size() ? "\n" : ",\n");
        }
        indent(out, 2);
    }
    out << "],\n";

    indent(out, 2); out << "\"calls\": [";
    if (!analysis.clang.calls.empty()) {
        out << "\n";
        for (std::size_t i = 0; i < analysis.clang.calls.size(); ++i) {
            const auto& call = analysis.clang.calls[i];
            indent(out, 3); out << "{\n";
            indent(out, 4); out << "\"caller_file\": "; writeString(out, call.caller_file); out << ",\n";
            indent(out, 4); out << "\"callee\": "; writeString(out, call.callee); out << ",\n";
            indent(out, 4); out << "\"line\": " << call.line << "\n";
            indent(out, 3); out << "}" << (i + 1 == analysis.clang.calls.size() ? "\n" : ",\n");
        }
        indent(out, 2);
    }
    out << "],\n";

    indent(out, 2); out << "\"module_dependencies\": [";
    if (!analysis.clang.module_dependencies.empty()) {
        out << "\n";
        for (std::size_t i = 0; i < analysis.clang.module_dependencies.size(); ++i) {
            const auto& dep = analysis.clang.module_dependencies[i];
            indent(out, 3); out << "{\n";
            indent(out, 4); out << "\"from\": "; writeString(out, dep.from); out << ",\n";
            indent(out, 4); out << "\"to\": "; writeString(out, dep.to); out << ",\n";
            indent(out, 4); out << "\"type\": "; writeString(out, dep.type); out << ",\n";
            indent(out, 4); out << "\"evidence\": "; writeString(out, dep.evidence); out << ",\n";
            indent(out, 4); out << "\"weight\": " << dep.weight << "\n";
            indent(out, 3); out << "}" << (i + 1 == analysis.clang.module_dependencies.size() ? "\n" : ",\n");
        }
        indent(out, 2);
    }
    out << "]\n";
    indent(out, 1); out << "},\n";

    indent(out, 1); out << "\"strengths\": "; writeStringArray(out, analysis.strengths, 1); out << ",\n";
    indent(out, 1); out << "\"risks\": [";
    if (!analysis.risks.empty()) {
        out << "\n";
        for (std::size_t i = 0; i < analysis.risks.size(); ++i) {
            indent(out, 2); out << "{\n";
            indent(out, 3); out << "\"type\": "; writeString(out, analysis.risks[i].type); out << ",\n";
            indent(out, 3); out << "\"message\": "; writeString(out, analysis.risks[i].message); out << "\n";
            indent(out, 2); out << "}" << (i + 1 == analysis.risks.size() ? "\n" : ",\n");
        }
        indent(out, 1);
    }
    out << "]\n";
    out << "}\n";
    return out.str();
}

}  // namespace projectagentcpp
