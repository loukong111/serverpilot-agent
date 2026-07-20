#pragma once

#include "projectagentcpp/analysis.hpp"

#include <filesystem>

namespace projectagentcpp {

ClangAnalysis analyzeWithCompileCommands(const std::filesystem::path& root);

}  // namespace projectagentcpp
