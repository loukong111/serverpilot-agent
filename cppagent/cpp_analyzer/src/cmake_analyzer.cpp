#include "projectagentcpp/cmake_analyzer.hpp"

#include "projectagentcpp/text_utils.hpp"

#include <regex>
#include <set>

namespace projectagentcpp {
namespace {

std::string firstArgument(const std::string& line, const std::string& command) {
    const std::regex pattern(command + R"(\s*\(\s*([A-Za-z0-9_\-${}\.:/\-]+))", std::regex::icase);
    std::smatch match;
    if (std::regex_search(line, match, pattern) && match.size() > 1) {
        return match[1].str();
    }
    return {};
}

void addUnique(std::vector<std::string>& values, const std::string& value) {
    if (!value.empty() && std::find(values.begin(), values.end(), value) == values.end()) {
        values.push_back(value);
    }
}

}  // namespace

CMakeInfo analyzeCMake(const std::filesystem::path& root) {
    CMakeInfo info;
    const auto cmake_path = root / "CMakeLists.txt";
    const auto content = readTextFile(cmake_path);
    if (content.empty()) {
        return info;
    }

    info.found = true;
    std::istringstream lines(content);
    std::string line;
    while (std::getline(lines, line)) {
        const auto stripped = trim(line);
        if (stripped.empty() || stripped.front() == '#') {
            continue;
        }

        const auto lower = toLower(stripped);
        if (contains(lower, "enable_testing(")) {
            info.enable_testing = true;
        }

        if (auto value = firstArgument(stripped, "project"); !value.empty()) {
            info.project_name = value;
        }
        if (auto value = firstArgument(stripped, "add_executable"); !value.empty()) {
            addUnique(info.executables, value);
        }
        if (auto value = firstArgument(stripped, "add_library"); !value.empty()) {
            addUnique(info.libraries, value);
        }
        if (auto value = firstArgument(stripped, "add_test"); !value.empty()) {
            if (toLower(value) == "name") {
                const std::regex named_test(R"(NAME\s+([A-Za-z0-9_\-${}\.:/\-]+))", std::regex::icase);
                std::smatch match;
                if (std::regex_search(stripped, match, named_test) && match.size() > 1) {
                    value = match[1].str();
                }
            }
            addUnique(info.tests, value);
        }
        if (auto value = firstArgument(stripped, "find_package"); !value.empty()) {
            addUnique(info.packages, value);
        }

        const std::regex standard_pattern(R"(CMAKE_CXX_STANDARD\s+([0-9]+))", std::regex::icase);
        std::smatch standard_match;
        if (std::regex_search(stripped, standard_match, standard_pattern) && standard_match.size() > 1) {
            info.cpp_standard = standard_match[1].str();
        }

        const std::regex link_pattern(R"(target_link_libraries\s*\((.*)\))", std::regex::icase);
        std::smatch link_match;
        if (std::regex_search(stripped, link_match, link_pattern) && link_match.size() > 1) {
            std::istringstream tokens(link_match[1].str());
            std::string token;
            bool first = true;
            while (tokens >> token) {
                if (first || token == "PRIVATE" || token == "PUBLIC" || token == "INTERFACE") {
                    first = false;
                    continue;
                }
                addUnique(info.linked_libraries, token);
            }
        }
    }

    return info;
}

}  // namespace projectagentcpp
