#include "projectagentcpp/clang_enhancer.hpp"

#include "projectagentcpp/text_utils.hpp"

#include <algorithm>
#include <map>
#include <regex>
#include <set>
#include <sstream>

namespace projectagentcpp {
namespace {

constexpr std::size_t kMaxSamples = 12;
constexpr std::size_t kMaxSymbols = 80;
constexpr std::size_t kMaxCalls = 120;
constexpr std::size_t kMaxDeps = 80;

std::filesystem::path findCompileCommands(const std::filesystem::path& root) {
    const std::vector<std::filesystem::path> candidates = {
        root / "compile_commands.json",
        root / "build" / "compile_commands.json",
        root / "build-debug" / "compile_commands.json",
        root / "build-release" / "compile_commands.json",
        root / "build-sanitize" / "compile_commands.json",
        root / "cmake-build-debug" / "compile_commands.json",
        root / "cmake-build-release" / "compile_commands.json",
    };
    for (const auto& candidate : candidates) {
        if (std::filesystem::exists(candidate)) {
            return candidate;
        }
    }
    return {};
}

std::string extractJsonStringField(const std::string& object, const std::string& field) {
    const std::regex pattern("\"" + field + "\"\\s*:\\s*\"((?:\\\\.|[^\"\\\\])*)\"");
    std::smatch match;
    if (std::regex_search(object, match, pattern) && match.size() > 1) {
        std::string value = match[1].str();
        std::string out;
        out.reserve(value.size());
        for (std::size_t i = 0; i < value.size(); ++i) {
            if (value[i] == '\\' && i + 1 < value.size()) {
                ++i;
                out.push_back(value[i]);
            } else {
                out.push_back(value[i]);
            }
        }
        return out;
    }
    return {};
}

std::vector<CompileCommandEntry> parseCompileCommands(const std::filesystem::path& path) {
    std::vector<CompileCommandEntry> entries;
    const auto content = readTextFile(path, 16 * 1024 * 1024);
    if (content.empty()) {
        return entries;
    }

    std::size_t pos = 0;
    while ((pos = content.find('{', pos)) != std::string::npos) {
        const auto end = content.find('}', pos);
        if (end == std::string::npos) {
            break;
        }
        const auto object = content.substr(pos, end - pos + 1);
        CompileCommandEntry entry;
        entry.file = extractJsonStringField(object, "file");
        entry.directory = extractJsonStringField(object, "directory");
        entry.command = extractJsonStringField(object, "command");
        if (!entry.file.empty()) {
            entries.push_back(std::move(entry));
        }
        pos = end + 1;
    }
    return entries;
}

bool isSourceLike(const std::filesystem::path& path) {
    return hasExtension(path, {".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh"});
}

std::string moduleFromRelativePath(const std::string& relative) {
    std::filesystem::path path(relative);
    auto it = path.begin();
    if (it == path.end()) {
        return "root";
    }
    const auto first = toLower(it->string());
    ++it;
    if ((first == "src" || first == "include") && it != path.end()) {
        const auto second = toLower(it->string());
        if (first == "include" && second == "miniredis") {
            ++it;
            return it == path.end() ? "include" : toLower(it->string());
        }
        return second;
    }
    if (first == "tests" || first == "test") {
        return "tests";
    }
    if (first == "tools") {
        return "tools";
    }
    return first.empty() ? "root" : first;
}

std::string moduleFromInclude(const std::string& include_name) {
    std::filesystem::path path(include_name);
    auto it = path.begin();
    if (it == path.end()) {
        return {};
    }
    auto first = toLower(it->string());
    if (first == "miniredis") {
        ++it;
        return it == path.end() ? "miniredis" : toLower(it->string());
    }
    if (first == "src" || first == "include") {
        ++it;
        return it == path.end() ? first : toLower(it->string());
    }
    return {};
}

void addSymbol(std::vector<SymbolInfo>& symbols, const SymbolInfo& symbol) {
    if (symbols.size() >= kMaxSymbols) {
        return;
    }
    const auto exists = std::any_of(symbols.begin(), symbols.end(), [&](const SymbolInfo& item) {
        return item.name == symbol.name && item.kind == symbol.kind && item.file == symbol.file;
    });
    if (!exists) {
        symbols.push_back(symbol);
    }
}

void addCall(std::vector<CallInfo>& calls, const CallInfo& call) {
    if (calls.size() >= kMaxCalls) {
        return;
    }
    static const std::set<std::string> ignored = {
        "if", "for", "while", "switch", "return", "sizeof", "catch", "static_cast",
        "reinterpret_cast", "const_cast", "dynamic_cast"
    };
    if (ignored.count(call.callee) > 0) {
        return;
    }
    calls.push_back(call);
}

void addDependency(std::map<std::string, ModuleDependency>& deps,
                   const std::string& from,
                   const std::string& to,
                   const std::string& type,
                   const std::string& evidence) {
    if (from.empty() || to.empty() || from == to) {
        return;
    }
    const auto key = from + "\t" + to + "\t" + type;
    auto& dep = deps[key];
    dep.from = from;
    dep.to = to;
    dep.type = type;
    dep.evidence = dep.evidence.empty() ? evidence : dep.evidence;
    dep.weight += 1;
}

std::vector<std::filesystem::path> collectFiles(const std::filesystem::path& root,
                                                const std::vector<CompileCommandEntry>& commands) {
    std::vector<std::filesystem::path> files;
    std::set<std::string> seen;
    for (const auto& command : commands) {
        std::filesystem::path file(command.file);
        if (!std::filesystem::exists(file) || !isSourceLike(file)) {
            continue;
        }
        if (seen.insert(file.string()).second) {
            files.push_back(file);
        }
    }

    std::error_code ec;
    std::filesystem::recursive_directory_iterator it(root, std::filesystem::directory_options::skip_permission_denied, ec);
    const std::filesystem::recursive_directory_iterator end;
    for (; it != end; it.increment(ec)) {
        if (ec) {
            ec.clear();
            continue;
        }
        const auto& entry = *it;
        if (entry.is_directory(ec) && isIgnoredDirectoryName(entry.path().filename().string())) {
            it.disable_recursion_pending();
            continue;
        }
        if (entry.is_regular_file(ec) && isSourceLike(entry.path())) {
            const auto path = entry.path().string();
            if (seen.insert(path).second) {
                files.push_back(entry.path());
            }
        }
    }
    std::sort(files.begin(), files.end());
    return files;
}

void analyzeFile(const std::filesystem::path& root,
                 const std::filesystem::path& path,
                 ClangAnalysis& result,
                 std::map<std::string, ModuleDependency>& deps) {
    const auto content = readTextFile(path, 512 * 1024);
    if (content.empty()) {
        return;
    }

    const auto relative = relativeString(root, path);
    const auto source_module = moduleFromRelativePath(relative);
    const std::regex class_pattern(R"(\b(class|struct)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:final\s*)?(?:[:{]))");
    const std::regex function_pattern(R"(^\s*(?:[A-Za-z_][A-Za-z0-9_:<>\*&,\s]+\s+)+([A-Za-z_][A-Za-z0-9_:]*)\s*\([^;{}]*\)\s*(?:const\s*)?(?:noexcept\s*)?(?:override\s*)?\{)");
    const std::regex include_pattern(R"(^\s*#\s*include\s*[<"]([^>"]+)[>"])");
    const std::regex qualified_call_pattern(R"(\b([A-Za-z_][A-Za-z0-9_:]*::[A-Za-z_][A-Za-z0-9_]*)\s*\()");
    const std::regex call_pattern(R"(\b([A-Za-z_][A-Za-z0-9_]*)\s*\()");

    std::istringstream lines(content);
    std::string line;
    int line_number = 0;
    while (std::getline(lines, line)) {
        ++line_number;
        std::smatch match;
        if (std::regex_search(line, match, include_pattern) && match.size() > 1) {
            const auto include_name = match[1].str();
            const auto target_module = moduleFromInclude(include_name);
            addDependency(deps, source_module, target_module, "include", include_name);
        }

        auto class_begin = std::sregex_iterator(line.begin(), line.end(), class_pattern);
        const auto class_end = std::sregex_iterator();
        for (auto it = class_begin; it != class_end; ++it) {
            addSymbol(result.classes, SymbolInfo{(*it)[2].str(), (*it)[1].str(), relative, line_number});
        }

        if (std::regex_search(line, match, function_pattern) && match.size() > 1) {
            const auto name = match[1].str();
            if (name != "if" && name != "for" && name != "while" && name != "switch") {
                addSymbol(result.functions, SymbolInfo{name, "function", relative, line_number});
            }
        }

        auto qualified_begin = std::sregex_iterator(line.begin(), line.end(), qualified_call_pattern);
        for (auto it = qualified_begin; it != class_end; ++it) {
            addCall(result.calls, CallInfo{relative, (*it)[1].str(), line_number});
        }

        auto call_begin = std::sregex_iterator(line.begin(), line.end(), call_pattern);
        for (auto it = call_begin; it != class_end; ++it) {
            addCall(result.calls, CallInfo{relative, (*it)[1].str(), line_number});
        }
    }
}

}  // namespace

ClangAnalysis analyzeWithCompileCommands(const std::filesystem::path& root_input) {
    std::error_code ec;
    const auto root = std::filesystem::absolute(root_input, ec);
    ClangAnalysis result;

    const auto compile_commands = findCompileCommands(root);
    if (!compile_commands.empty()) {
        result.compile_commands_found = true;
        result.compile_commands_path = compile_commands.string();
    }

    const auto commands = result.compile_commands_found ? parseCompileCommands(compile_commands) : std::vector<CompileCommandEntry>{};
    result.command_count = static_cast<int>(commands.size());
    for (const auto& command : commands) {
        if (result.sample_commands.size() >= kMaxSamples) {
            break;
        }
        result.sample_commands.push_back(CompileCommandEntry{
            relativeString(root, command.file),
            command.directory,
            command.command,
        });
    }

    std::map<std::string, ModuleDependency> deps;
    for (const auto& file : collectFiles(root, commands)) {
        analyzeFile(root, file, result, deps);
    }

    for (const auto& item : deps) {
        if (result.module_dependencies.size() >= kMaxDeps) {
            break;
        }
        result.module_dependencies.push_back(item.second);
    }
    std::sort(result.module_dependencies.begin(), result.module_dependencies.end(),
              [](const ModuleDependency& lhs, const ModuleDependency& rhs) {
                  return lhs.weight > rhs.weight;
              });
    return result;
}

}  // namespace projectagentcpp
