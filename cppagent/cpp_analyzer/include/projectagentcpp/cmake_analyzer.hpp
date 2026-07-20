#pragma once

#include "projectagentcpp/analysis.hpp"

#include <filesystem>

namespace projectagentcpp {

CMakeInfo analyzeCMake(const std::filesystem::path& root);

}  // namespace projectagentcpp
