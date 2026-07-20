#pragma once

#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

namespace projectagentcpp {

inline std::string trim(std::string value) {
    auto not_space = [](unsigned char ch) { return !std::isspace(ch); };
    value.erase(value.begin(), std::find_if(value.begin(), value.end(), not_space));
    value.erase(std::find_if(value.rbegin(), value.rend(), not_space).base(), value.end());
    return value;
}

inline std::string toLower(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return value;
}

inline bool contains(const std::string& text, const std::string& pattern) {
    return text.find(pattern) != std::string::npos;
}

inline std::string readTextFile(const std::filesystem::path& path, std::size_t max_bytes = 1024 * 1024) {
    std::ifstream input(path);
    if (!input) {
        return {};
    }
    std::ostringstream buffer;
    char ch = '\0';
    std::size_t count = 0;
    while (input.get(ch) && count < max_bytes) {
        buffer << ch;
        ++count;
    }
    return buffer.str();
}

inline std::string relativeString(const std::filesystem::path& root, const std::filesystem::path& path) {
    std::error_code ec;
    auto relative = std::filesystem::relative(path, root, ec);
    return ec ? path.string() : relative.generic_string();
}

inline bool hasExtension(const std::filesystem::path& path, const std::vector<std::string>& extensions) {
    const auto ext = toLower(path.extension().string());
    return std::find(extensions.begin(), extensions.end(), ext) != extensions.end();
}

inline bool isIgnoredDirectoryName(const std::string& name) {
    static const std::vector<std::string> ignored = {
        ".git", ".vscode", ".idea",
        "third_party", "vendor", "node_modules", "__pycache__"
    };
    if (std::find(ignored.begin(), ignored.end(), name) != ignored.end()) {
        return true;
    }
    return name == "build" || name.rfind("build-", 0) == 0 || name.rfind("cmake-build", 0) == 0;
}

}  // namespace projectagentcpp
