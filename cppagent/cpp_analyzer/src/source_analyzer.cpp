#include "projectagentcpp/source_analyzer.hpp"

#include "projectagentcpp/text_utils.hpp"

#include <algorithm>
#include <map>
#include <set>

namespace projectagentcpp {
namespace {

struct ModuleRule {
    std::string name;
    std::vector<std::string> keywords;
};

const std::vector<ModuleRule>& rules() {
    static const std::vector<ModuleRule> value = {
        {"network", {"socket", "listen", "accept", "recv", "send", "epoll", "eventfd", "connection", "tcp", "server"}},
        {"protocol", {"protocol", "parser", "resp", "serialize", "deserialize", "request", "response"}},
        {"storage", {"storage", "store", "cache", "kv", "unordered_map", "map<", "database", "memory"}},
        {"commands", {"command", "handler", "dispatch", "execute", "set", "get", "del"}},
        {"concurrency", {"thread", "mutex", "atomic", "lock_guard", "condition_variable", "threadpool", "thread_pool"}},
        {"persistence", {"persistence", "snapshot", "append_only", "appendonly", "aof", "wal", "flush", "restore"}},
        {"cluster", {"cluster", "replica", "replication", "slot", "shard", "failover", "gossip"}},
        {"metrics", {"metrics", "stats", "prometheus", "latency", "counter", "http_stats"}},
        {"config", {"config", "option", "parse_config", ".conf", "yaml", "toml"}},
        {"testing", {"gtest", "assert", "integration", "unit_test", "add_test", "test_"}}
    };
    return value;
}

bool isSourceLike(const std::filesystem::path& path) {
    return hasExtension(path, {".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh"});
}

int pathPriority(const std::string& relative) {
    const auto lower = toLower(relative);
    if (lower == "main.cpp" || lower == "src/main.cpp") {
        return 0;
    }
    if (lower.rfind("src/", 0) == 0 || lower.rfind("include/", 0) == 0) {
        return 1;
    }
    if (lower.rfind("tests/", 0) == 0 || lower.rfind("test/", 0) == 0) {
        return 2;
    }
    if (lower.rfind("tools/", 0) == 0) {
        return 3;
    }
    return 4;
}

std::vector<std::filesystem::path> collectSourceFiles(const std::filesystem::path& root) {
    std::vector<std::filesystem::path> files;
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
            files.push_back(entry.path());
        }
    }
    std::sort(files.begin(), files.end(), [&](const auto& lhs, const auto& rhs) {
        const auto lhs_relative = relativeString(root, lhs);
        const auto rhs_relative = relativeString(root, rhs);
        const auto lhs_priority = pathPriority(lhs_relative);
        const auto rhs_priority = pathPriority(rhs_relative);
        if (lhs_priority != rhs_priority) {
            return lhs_priority < rhs_priority;
        }
        return lhs_relative < rhs_relative;
    });
    return files;
}

void addLimited(std::vector<std::string>& values, const std::string& value, std::size_t limit) {
    if (value.empty() || values.size() >= limit) {
        return;
    }
    if (std::find(values.begin(), values.end(), value) == values.end()) {
        values.push_back(value);
    }
}

}  // namespace

std::vector<ModuleFinding> analyzeSources(const std::filesystem::path& root) {
    std::map<std::string, int> scores;
    std::map<std::string, ModuleFinding> findings;
    for (const auto& rule : rules()) {
        findings[rule.name].name = rule.name;
    }

    for (const auto& path : collectSourceFiles(root)) {
        const auto relative = relativeString(root, path);
        const auto haystack = toLower(relative + "\n" + readTextFile(path, 256 * 1024));
        for (const auto& rule : rules()) {
            int local_score = 0;
            for (const auto& keyword : rule.keywords) {
                if (contains(haystack, toLower(keyword))) {
                    ++local_score;
                    addLimited(findings[rule.name].evidence, keyword, 8);
                }
            }
            if (local_score > 0) {
                scores[rule.name] += local_score;
                addLimited(findings[rule.name].files, relative, 10);
            }
        }
    }

    std::vector<ModuleFinding> result;
    for (auto& item : findings) {
        const auto score_it = scores.find(item.first);
        if (score_it == scores.end() || score_it->second == 0) {
            continue;
        }
        item.second.confidence = std::min(0.95, 0.25 + score_it->second * 0.08);
        result.push_back(item.second);
    }

    std::sort(result.begin(), result.end(), [](const ModuleFinding& lhs, const ModuleFinding& rhs) {
        return lhs.confidence > rhs.confidence;
    });
    return result;
}

std::vector<std::string> findEntryPoints(const std::filesystem::path& root) {
    std::vector<std::string> entries;
    for (const auto& path : collectSourceFiles(root)) {
        if (!hasExtension(path, {".cpp", ".cc", ".cxx"})) {
            continue;
        }
        const auto content = readTextFile(path, 128 * 1024);
        if (contains(content, "int main(") || contains(content, "int main (")) {
            entries.push_back(relativeString(root, path));
        }
    }
    return entries;
}

}  // namespace projectagentcpp
