#pragma once

#include "projectagentcpp/analysis.hpp"

#include <filesystem>

namespace projectagentcpp {

std::vector<ModuleFinding> analyzeSources(const std::filesystem::path& root);
std::vector<std::string> findEntryPoints(const std::filesystem::path& root);

}  // namespace projectagentcpp
