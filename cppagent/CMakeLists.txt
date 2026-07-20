cmake_minimum_required(VERSION 3.16)
project(projectagentcpp VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_executable(cpp_analyzer
    cpp_analyzer/src/main.cpp
    cpp_analyzer/src/project_scanner.cpp
    cpp_analyzer/src/cmake_analyzer.cpp
    cpp_analyzer/src/source_analyzer.cpp
    cpp_analyzer/src/clang_enhancer.cpp
    cpp_analyzer/src/report_json.cpp
    cpp_analyzer/src/agent_runtime.cpp
)

target_include_directories(cpp_analyzer PRIVATE cpp_analyzer/include)
